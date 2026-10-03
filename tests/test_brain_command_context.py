from core.brain.brain import VegaBrain
from unittest.mock import Mock
import threading


class FakeCommands:
    def __init__(self):
        self.closed = []

    def close_app(self, app_name):
        self.closed.append(app_name)
        return f"Fechando {app_name}."


class FakeParser:
    @staticmethod
    def parse(text):
        return "Abrindo Spotify." if text == "abre o spotify" else None

    @staticmethod
    def _extract_app_name(text):
        return "spotify" if "spotify" in text else None

    @staticmethod
    def _normalize_app_name(app_name):
        return app_name or ""


def test_close_without_target_confirms_last_opened_app():
    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = FakeCommands()
    brain.commands_parser = FakeParser()
    brain._last_opened_app = None
    brain._pending_close_app = None

    assert brain._process_command("abre o spotify") == "Abrindo Spotify."
    assert brain._last_opened_app == "spotify"

    assert brain._process_command("fechar") == "Você quer que eu feche o spotify?"
    assert brain._process_command("confirmo") == "Fechando spotify."
    assert brain.commands.closed == ["spotify"]


def test_rejecting_contextual_close_keeps_the_app_open():
    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = FakeCommands()
    brain._last_opened_app = "spotify"
    brain._pending_close_app = None

    assert brain._process_contextual_close_request("fechar") == "Você quer que eu feche o spotify?"
    assert brain._process_contextual_close_request("não") == "Tudo bem, não vou fechar o aplicativo."
    assert brain.commands.closed == []


def test_database_view_request_invokes_mysql_callback():
    brain = VegaBrain.__new__(VegaBrain)
    calls = []
    brain._pending_dangerous_action = None
    brain.database_view_callback = lambda: calls.append(True)

    assert brain.process("abre o banco de dados da vega") == "Abrindo meu banco de dados no MySQL."
    assert calls == [True]


def test_database_view_request_accepts_requested_variations():
    assert VegaBrain._is_database_view_request("abrir banco de dados da vega")
    assert VegaBrain._is_database_view_request("me mostre seu banco de dados")
    assert VegaBrain._is_database_view_request("mostra o mysql da vega")
    assert not VegaBrain._is_database_view_request("o que é mysql")


def test_visual_assistance_recognizes_natural_code_and_school_requests():
    brain = VegaBrain.__new__(VegaBrain)
    brain.last_screen_summary = ""

    for request in (
        "o que tem de errado aqui",
        "oq tem de errado aqui",
        "me ajuda com esse código",
        "me ajuda com essa atividade",
        "qual o próximo passo",
        "o que você está vendo na câmera",
        "o que vc está vendo na camera",
    ):
        assert brain._is_visual_analysis_request(request), request

    assert not brain._is_visual_analysis_request("qual linguagem devo aprender?")
    assert brain._is_camera_analysis_request("o que vc está vendo na minha câmera?")
    assert not brain._is_camera_analysis_request("o que você está vendo na tela?")


def test_camera_visual_request_passes_camera_frame_not_screen_capture():
    brain = VegaBrain.__new__(VegaBrain)
    frame = object()
    captured = {}

    class FakeAI:
        @staticmethod
        def chat_stream(_messages, **kwargs):
            captured.update(kwargs)
            yield "Vejo uma pessoa diante da câmera."

    brain.ai = FakeAI()
    brain.camera_frame_callback = lambda: frame
    brain.last_screen_summary = None
    brain.conversation = Mock()
    brain.memory = Mock()

    response = list(brain._handle_visual_request_stream("vega, oq vc está vendo na câmera?"))

    assert response == ["Vejo uma pessoa diante da câmera."]
    assert captured["include_screen"] is False
    assert captured["include_image"] is frame


def test_camera_visual_request_reports_missing_camera_frame():
    brain = VegaBrain.__new__(VegaBrain)
    brain.camera_frame_callback = lambda: None
    brain.last_screen_summary = None

    response = list(brain._handle_visual_request_stream("o que você está vendo na câmera?"))

    assert len(response) == 1
    assert "Não consegui acessar a câmera" in response[0]


def test_pasted_image_takes_priority_over_short_greeting():
    brain = VegaBrain.__new__(VegaBrain)
    brain._process_stream_lock = threading.RLock()
    brain._response_cancelled = threading.Event()
    brain.last_screen_summary = None
    frame = object()
    received = {}

    class FakeAI:
        @staticmethod
        def chat_stream(_messages, **kwargs):
            received.update(kwargs)
            yield "Consigo analisar a imagem."

    brain.ai = FakeAI()
    brain.camera_frame_callback = None
    brain.conversation = Mock()
    brain.memory = Mock()

    response = list(brain.process_stream("oi", image=frame))

    assert response == ["Consigo analisar a imagem."]
    assert received["include_screen"] is False
    assert received["include_image"] is frame


def test_visual_prompt_asks_one_question_when_screen_context_is_unclear():
    brain = VegaBrain.__new__(VegaBrain)
    prompt = brain._build_visual_prompt("me ajuda com isso")

    assert "não se limite a descrever" in prompt
    assert "mensagem de erro" in prompt
    assert "atividade escolar" in prompt
    assert "uma única pergunta específica" in prompt
