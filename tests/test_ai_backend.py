from config.ai import (
    GEMINI_MODEL_NAME,
    normalize_gemini_live_model_name,
    normalize_gemini_model_name,
)
from core.brain.ai import VegaAI, VEGA_RUNTIME_PROMPT, VegaLiveSession
from core.brain.brain import VegaBrain
import vega
from vega import format_vega_status_message


def test_default_backend_is_gemini(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "")

    ai = VegaAI()
    assert ai.backend == "gemini"
    assert isinstance(ai.gemini_model, str)


def test_live_model_candidates_use_environment_only(monkeypatch):
    monkeypatch.setattr("core.brain.ai.GEMINI_LIVE_MODEL", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_MODEL_NAME", "")
    # _live_model_candidates também lê os.getenv direto, então zerar o módulo
    # não basta: removemos as variáveis de ambiente para o teste ser hermético.
    monkeypatch.delenv("GEMINI_LIVE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL_NAME", raising=False)
    ai = VegaAI()
    assert ai._live_model_candidates() == []


def test_ollama_has_precedence_over_remote_backends_when_configured(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "sk-test-openrouter")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "llama3.2:3b")

    ai = VegaAI()

    assert ai.backend == "ollama"


def test_chat_stream_uses_ollama_when_local_model_is_selected(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setattr("core.brain.ai.OLLAMA_BASE_URL", "http://localhost:11434")

    ai = VegaAI()
    ai.backend = "ollama"

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
            self.text = "ok"

        def iter_lines(self):
            return iter([
                b'{"message":{"content":"res"}}',
                b'{"message":{"content":"posta via ollama"}}',
                b'{"done":true}',
            ])

        def json(self):
            return self._payload

    def fake_post(*args, **kwargs):
        return FakeResponse({"message": {"content": "resposta via ollama"}})

    monkeypatch.setattr("core.brain.ai.requests.post", fake_post)

    chunks = list(ai.chat_stream([{"role": "user", "content": "oi"}]))
    assert chunks == ["res", "posta via ollama"]


def test_chat_stream_falls_back_to_gemini_when_openai_is_quota_exhausted(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "")

    ai = VegaAI()
    ai.backend = "openai"
    ai.client = object()
    ai.gemini_model = "models/gemini-3.1-pro-preview"
    ai.gemini_fallback_model = "models/gemini-2.5-flash-lite"

    monkeypatch.setattr(ai, "_chat_stream_openai", lambda *args, **kwargs: iter(["Não consegui responder agora. Tente novamente em alguns segundos."]))

    class FakeChat:
        def send_message_stream(self, contents):
            return iter([type("Chunk", (), {"text": "resposta via gemini"})()])

    class FakeGeminiClient:
        def __init__(self):
            self.chats = type("Chats", (), {"create": staticmethod(lambda **kwargs: FakeChat())})()

    ai.gemini_client = FakeGeminiClient()

    chunks = list(ai.chat_stream([{"role": "user", "content": "oi"}]))
    assert chunks == ["resposta via gemini"]


def test_visual_request_is_detected_and_uses_summary_prompt():
    brain = VegaBrain()
    assert brain._is_visual_analysis_request("olha isso na minha tela")
    assert brain._is_visual_analysis_request("analisa a tela")

    prompt = brain._build_visual_prompt(
        "faça um mapa mental resumindo esse tema",
        previous_summary="o tema é inteligência artificial aplicada em automação",
    )
    assert "mapa mental" in prompt.lower()
    assert "inteligência artificial" in prompt.lower()


def test_vega_speaks_in_first_person_and_keeps_status_prefix():
    assert "primeira pessoa" in VEGA_RUNTIME_PROMPT.lower()
    assert "eu" in VEGA_RUNTIME_PROMPT.lower()
    assert format_vega_status_message("VEGA: Eu continuo funcionando normalmente") == "VEGA: Eu continuo funcionando normalmente"


def test_live_session_instructions_require_direct_responses():
    session = VegaLiveSession()
    instruction = session.config.system_instruction.lower()
    assert "responda diretamente" in instruction
    assert "narrar" in instruction or "descreva" in instruction
    assert "uma frase" in instruction or "curtas" in instruction


def test_deprecated_gemini_model_names_are_normalized():
    assert normalize_gemini_model_name("gemini-2.0-flash") == "models/gemini-2.0-flash"
    assert normalize_gemini_model_name("models/gemini-2.5-flash") == "models/gemini-2.5-flash"
    assert normalize_gemini_model_name("models/gemini-2.5-pro") == "models/gemini-2.5-pro"
    assert normalize_gemini_model_name("models/gemini-3.1-pro-preview") == "models/gemini-3.1-pro-preview"


def test_live_model_aliases_are_kept_separate_from_text_models():
    assert normalize_gemini_model_name("gemini-2.5-flash-live-preview") == "models/gemini-2.5-flash-live-preview"
    assert normalize_gemini_live_model_name("gemini-2.5-flash-live-preview") == "models/gemini-2.5-flash-live-preview"
    assert normalize_gemini_live_model_name("models/gemini-2.5-flash-preview-native-audio") == "models/gemini-2.5-flash-preview-native-audio"


def test_chat_stream_skips_screen_capture_by_default(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "")

    ai = VegaAI()
    ai.gemini_model = "models/gemini-3.1-pro-preview"
    ai.gemini_fallback_model = "models/gemini-2.5-flash-lite"
    calls = {"count": 0}

    def fake_capture_screen():
        calls["count"] += 1
        return "SCREEN"

    monkeypatch.setattr(ai, "_capture_screen", fake_capture_screen)

    def fake_send_message_stream(_contents):
        return iter([type("Chunk", (), {"text": "resposta pronta"})()])

    class FakeChat:
        def __init__(self):
            self.sent = None

        def send_message_stream(self, contents):
            self.sent = contents
            return fake_send_message_stream(contents)

    def fake_create(**kwargs):
        return FakeChat()

    ai.client = type("FakeClient", (), {"chats": type("Chats", (), {"create": staticmethod(fake_create)})})()

    chunks = list(ai.chat_stream([{"role": "user", "content": "oi"}]))
    assert chunks == ["resposta pronta"]
    assert calls["count"] == 0


def test_chat_stream_can_include_screen_for_visual_requests(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "")

    ai = VegaAI()
    ai.gemini_model = "models/gemini-3.1-pro-preview"
    ai.gemini_fallback_model = "models/gemini-2.5-flash-lite"
    calls = {"count": 0}

    def fake_capture_screen():
        calls["count"] += 1
        return "SCREEN"

    monkeypatch.setattr(ai, "_capture_screen", fake_capture_screen)

    class FakeChat:
        def send_message_stream(self, contents):
            assert contents[0] == "SCREEN"
            return iter([type("Chunk", (), {"text": "visão ok"})()])

    ai.client = type("FakeClient", (), {"chats": type("Chats", (), {"create": staticmethod(lambda **kwargs: FakeChat())})})()

    chunks = list(ai.chat_stream([{"role": "user", "content": "olha isso na tela"}], include_screen=True))
    assert chunks == ["visão ok"]
    assert calls["count"] == 1


def test_visual_image_request_uses_gemini_when_text_backend_is_ollama():
    import threading

    ai = VegaAI.__new__(VegaAI)
    ai.backend = "ollama"
    ai.gemini_client = object()
    ai._generation_cancelled = threading.Event()
    calls = {}

    def fake_gemini(_messages, **kwargs):
        calls.update(kwargs)
        return iter(["descrição visual"])

    ai._chat_stream_gemini = fake_gemini

    result = list(ai.chat_stream([{"role": "user", "content": "olhe"}], include_image="camera-frame"))

    assert result == ["descrição visual"]
    assert calls["include_image"] == "camera-frame"


def test_emit_vega_status_updates_global_ui(monkeypatch):
    class FakeUi:
        def __init__(self):
            self.messages = []

        def push_status_message(self, message):
            self.messages.append(message)

    fake_ui = FakeUi()
    vega.vega_ui = fake_ui

    calls = []

    def fake_invoke_method(obj, method_name, connection, *args):
        calls.append((obj, method_name, connection, args))
        if method_name == "push_status_message":
            obj.push_status_message(args[0])
        return True

    monkeypatch.setattr(vega.QMetaObject, "invokeMethod", fake_invoke_method)

    vega.emit_vega_status("VEGA: resposta em tempo real")

    assert calls
    assert calls[0][1] == "push_status_message"
    assert fake_ui.messages


def test_quota_exhausted_message_is_reported_clearer(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "")

    ai = VegaAI()
    ai.client = type("FakeClient", (), {})()

    class FakeError(Exception):
        def __init__(self):
            self.status = 429
            self.code = "RESOURCE_EXHAUSTED"
            self.message = "You exceeded your current quota."

    class FakeChat:
        def __init__(self, model):
            self.model = model

        def send_message_stream(self, contents):
            raise FakeError()

    class FakeChats:
        def create(self, model, config):
            return FakeChat(model)

    ai.client.chats = FakeChats()
    ai.gemini_model = "models/gemini-3.1-pro-preview"
    ai.gemini_fallback_model = "models/gemini-2.5-flash-lite"

    # O contrato deste teste é o tratamento da cota do fluxo Gemini: não
    # delegamos a outro provedor nem dependemos da seleção por credenciais.
    chunks = list(ai._chat_stream_gemini([{"role": "user", "content": "oi"}]))

    assert any("quota" in chunk.lower() or "cota" in chunk.lower() for chunk in chunks)
    assert all("não consegui responder agora" not in chunk.lower() for chunk in chunks)


def test_chat_handles_non_string_stream_chunks(monkeypatch):
    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OPENROUTER_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("core.brain.ai.OLLAMA_MODEL", "")

    ai = VegaAI()

    class FakeChunk:
        def __init__(self, text):
            self.text = text

    class FakeChat:
        def send_message_stream(self, contents):
            return iter([FakeChunk(123), FakeChunk("ok")])

    class FakeChats:
        def create(self, model, config):
            return FakeChat()

    ai.client = type("FakeClient", (), {"chats": FakeChats()})()
    ai.gemini_model = "models/gemini-3.1-pro-preview"
    ai.gemini_fallback_model = "models/gemini-2.5-flash-lite"

    answer = ai.chat([{"role": "user", "content": "oi"}])

    assert isinstance(answer, str)
    assert "ok" in answer.lower()


def test_chat_stream_falls_back_to_openai_when_openrouter_is_exhausted(monkeypatch):
    ai = VegaAI()
    ai.backend = "openrouter"
    ai.client = object()

    monkeypatch.setattr(ai, "_chat_stream_openrouter", lambda *args, **kwargs: iter(["Não consegui responder agora. Tente novamente em alguns segundos."]))

    def fake_openai(*args, **kwargs):
        yield "resposta via openai"

    monkeypatch.setattr(ai, "_chat_stream_openai", fake_openai)
    monkeypatch.setattr("config.ai.OPENAI_API_KEY", "sk-test")

    chunks = list(ai.chat_stream([{"role": "user", "content": "oi"}]))
    assert chunks == ["resposta via openai"]


def test_brain_process_stream_coerces_non_string_chunks():
    from core.brain.brain import VegaBrain

    class FakeAI:
        def chat_stream(self, messages, include_screen=False):
            yield 123
            yield "ok"

        def _coerce_text(self, value):
            return str(value)

    class FakeConversation:
        class context:
            @staticmethod
            def snapshot():
                return ""

        def add_user_message(self, *args, **kwargs):
            pass

        def add_assistant_message(self, *args, **kwargs):
            pass

    class FakeMemory:
        def add_daily_message(self, *args, **kwargs):
            pass

    brain = VegaBrain.__new__(VegaBrain)
    brain.ai = FakeAI()
    brain.conversation = FakeConversation()
    brain.memory = FakeMemory()
    brain.last_screen_summary = ""
    brain._build_visual_prompt = lambda *args, **kwargs: "prompt"

    chunks = list(brain._handle_visual_request_stream("oi"))

    assert chunks == ["123", "ok"]


def test_brain_cancellation_stops_current_model_generation():
    import threading

    from core.brain.brain import VegaBrain

    class FakeAI:
        def __init__(self):
            self.cancelled = False

        def cancel_generation(self):
            self.cancelled = True

    brain = VegaBrain.__new__(VegaBrain)
    brain.ai = FakeAI()
    brain._response_cancelled = threading.Event()

    brain.cancel_current_response()

    assert brain._response_cancelled.is_set()
    assert brain.ai.cancelled is True


def test_brain_offers_registered_tools_to_model_stream():
    from config.ai import MAX_TOOL_OUTPUT_TOKENS
    from core.brain.brain import VegaBrain

    tool_spec = object()
    captured = {}

    class FakeToolManager:
        def list_tools(self):
            return [tool_spec]

    class FakeAI:
        def chat_stream_with_tools(self, messages, tools, executor, **kwargs):
            captured["tools"] = tools
            captured["arguments"] = kwargs
            yield "resposta escolhida pelo modelo"

    brain = VegaBrain.__new__(VegaBrain)
    brain.ai = FakeAI()
    brain.tool_manager = FakeToolManager()
    brain._execute_registered_tool_call = lambda *_args: None

    chunks = list(brain._stream_model_response([{"role": "user", "content": "tempo"}]))

    assert chunks == ["resposta escolhida pelo modelo"]
    assert captured["tools"] == [tool_spec]
    assert captured["arguments"]["num_predict"] == MAX_TOOL_OUTPUT_TOKENS
    assert captured["arguments"]["include_screen"] is False
    assert captured["arguments"]["include_image"] is None


def test_openai_stream_is_closed_when_generation_is_cancelled(monkeypatch):
    ai = VegaAI()
    ai.backend = "openai"
    ai.client = object()
    ai.openai_model = "test-model"
    ai.openai_fallback_model = ""

    class FakeStream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield type("Chunk", (), {
                "choices": [type("Choice", (), {
                    "delta": type("Delta", (), {"content": "primeira frase."})()
                })()]
            })()
            yield type("Chunk", (), {
                "choices": [type("Choice", (), {
                    "delta": type("Delta", (), {"content": " texto antigo."})()
                })()]
            })()

        def close(self):
            self.closed = True

    stream = FakeStream()
    ai.client = type("Client", (), {
        "chat": type("Chat", (), {
            "completions": type("Completions", (), {
                "create": staticmethod(lambda **_kwargs: stream)
            })()
        })()
    })()

    chunks = ai.chat_stream([{"role": "user", "content": "interrompa"}])
    assert next(chunks) == "primeira frase."
    ai.cancel_generation()

    assert list(chunks) == []
    assert stream.closed is True


def test_model_selects_registered_tool_and_uses_its_result():
    from types import SimpleNamespace
    from config.ai import MAX_TOOL_OUTPUT_TOKENS

    ai = VegaAI()
    ai.backend = "openai"
    ai.gemini_client = None
    ai.openai_model = "test-model"
    ai.openai_fallback_model = ""
    tool_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call-weather",
                            function=SimpleNamespace(
                                name="weather",
                                arguments='{"city":"Recife"}',
                            ),
                        )
                    ],
                )
            )
        ]
    )
    final_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="Chuva provável em Recife.", tool_calls=[])
            )
        ]
    )

    class FakeStream:
        def __init__(self, chunks):
            self.chunks = chunks

        def __iter__(self):
            return iter(self.chunks)

        def close(self):
            pass

    class FakeCompletions:
        def __init__(self):
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            return FakeStream(
                [tool_response] if len(self.requests) == 1 else [final_response]
            )

    completions = FakeCompletions()
    ai.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    registered = SimpleNamespace(
        name="weather",
        purpose="Consulta previsão do tempo.",
        implementation=lambda city: None,
    )
    executed = []

    chunks = list(
        ai.chat_stream_with_tools(
            [{"role": "user", "content": "Vai chover em Recife?"}],
            [registered],
            lambda name, args: executed.append((name, args)) or {"condition": "chuva"},
        )
    )

    assert executed == [("weather", {"city": "Recife"})]
    assert chunks == ["Chuva provável em Recife."]
    assert completions.requests[0]["max_tokens"] == MAX_TOOL_OUTPUT_TOKENS
    declaration = completions.requests[0]["tools"][0]["function"]
    assert declaration["parameters"]["required"] == ["city"]
    assert completions.requests[1]["messages"][-1]["content"] == '{"condition": "chuva"}'


def test_gemini_uses_registered_function_declaration_and_tool_result():
    from types import SimpleNamespace
    from config.ai import MAX_TOOL_OUTPUT_TOKENS

    ai = VegaAI()
    ai.backend = "gemini"
    ai.gemini_model = "models/test-model"
    ai.gemini_fallback_model = ""
    ai.client = None
    tool_part = SimpleNamespace(
        function_call=SimpleNamespace(name="weather", args={"city": "Recife"}),
        text=None,
    )
    tool_response = SimpleNamespace(
        text="",
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[tool_part]))],
    )

    class FakeChat:
        def __init__(self):
            self.calls = []

        def send_message_stream(self, contents):
            self.calls.append(contents)
            if len(self.calls) == 1:
                return iter([tool_response])
            return iter([SimpleNamespace(text="Chuva provável em Recife.", candidates=[])])

    fake_chat = FakeChat()
    configurations = []

    def create_chat(**kwargs):
        configurations.append(kwargs["config"])
        return fake_chat

    fake_client = SimpleNamespace(chats=SimpleNamespace(create=create_chat))
    ai.gemini_client = fake_client
    registered = SimpleNamespace(
        name="weather",
        purpose="Consulta previsão do tempo.",
        implementation=lambda city: None,
    )
    executed = []

    chunks = list(
        ai.chat_stream_with_tools(
            [{"role": "user", "content": "Vai chover em Recife?"}],
            [registered],
            lambda name, args: executed.append((name, args)) or {"condition": "chuva"},
        )
    )

    assert executed == [("weather", {"city": "Recife"})]
    assert chunks == ["Chuva provável em Recife."]
    assert len(fake_chat.calls) == 2
    assert configurations[0].max_output_tokens == MAX_TOOL_OUTPUT_TOKENS


def test_openrouter_streams_registered_function_calls(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    ai = VegaAI()
    ai.backend = "openrouter"
    ai.openrouter_api_key = "test-key"
    ai.openrouter_model = "test-model"
    ai.openrouter_fallback_model = ""
    ai.gemini_client = None
    calls = []

    tool_event = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"id":"call-weather","function":{"name":"weather",'
        b'"arguments":"{\\"city\\":\\"Recife\\"}"}}]}}]}'
    )
    final_event = b'data: {"choices":[{"delta":{"content":"Chuva em Recife."}}]}'

    class FakeResponse:
        status_code = 200
        text = "ok"

        def __init__(self, lines):
            self.lines = lines

        def iter_lines(self):
            return iter(self.lines)

        def close(self):
            pass

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return FakeResponse(
            [tool_event, b"data: [DONE]"]
            if len(calls) == 1
            else [final_event, b"data: [DONE]"]
        )

    monkeypatch.setattr("core.brain.ai.requests.post", fake_post)
    registered = SimpleNamespace(
        name="weather",
        purpose="Consulta previsão do tempo.",
        implementation=lambda city: None,
    )
    executed = []
    chunks = list(
        ai.chat_stream_with_tools(
            [{"role": "user", "content": "Vai chover em Recife?"}],
            [registered],
            lambda name, args: executed.append((name, args)) or {"condition": "chuva"},
        )
    )

    assert executed == [("weather", {"city": "Recife"})]
    assert chunks == ["Chuva em Recife."]
    assert calls[0]["json"]["stream"] is True


def test_ollama_streams_registered_function_calls(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("core.brain.ai.OPENAI_API_KEY", "")
    ai = VegaAI()
    ai.backend = "ollama"
    ai.ollama_model = "local-test"
    ai.ollama_fallback_model = ""
    ai.gemini_client = None
    calls = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        def __init__(self, lines):
            self.lines = lines

        def iter_lines(self):
            return iter(self.lines)

        def close(self):
            pass

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            lines = [
                b'{"message":{"tool_calls":[{"function":{"name":"weather",'
                b'"arguments":{"city":"Recife"}}}]},"done":true}',
            ]
        else:
            lines = [b'{"message":{"content":"Chuva em Recife."},"done":true}']
        return FakeResponse(lines)

    monkeypatch.setattr("core.brain.ai.requests.post", fake_post)
    registered = SimpleNamespace(
        name="weather",
        purpose="Consulta previsão do tempo.",
        implementation=lambda city: None,
    )
    executed = []
    chunks = list(
        ai.chat_stream_with_tools(
            [{"role": "user", "content": "Vai chover em Recife?"}],
            [registered],
            lambda name, args: executed.append((name, args)) or {"condition": "chuva"},
        )
    )

    assert executed == [("weather", {"city": "Recife"})]
    assert chunks == ["Chuva em Recife."]
    assert calls[0]["json"]["tools"][0]["function"]["name"] == "weather"


def test_status_message_slot_is_callable_via_qt_metaobject():
    from PySide6.QtCore import QMetaObject, Q_ARG, Qt
    from PySide6.QtWidgets import QApplication

    from config.interface import InterfaceVEGA

    app = QApplication.instance() or QApplication([])
    ui = InterfaceVEGA()

    invoked = QMetaObject.invokeMethod(
        ui,
        "push_status_message",
        Qt.QueuedConnection,
        Q_ARG(str, "VEGA: resposta normal"),
    )

    app.processEvents()

    assert invoked is True
    assert "VEGA: resposta normal" in ui.status_label.text()
