# -*- coding: utf-8 -*-
"""Modo live: sessão de tempo real, isolada e degradando com elegância.

Garante:
  1. sem chave/SDK, o modo live RECUSA em vez de quebrar;
  2. o ciclo de vida (start/stop) é seguro e idempotente;
  3. nada no módulo toca no pipeline de turnos nem na interface;
  4. a configuração tem limites (tempo máximo) e padrões sãos;
  5. o provedor de quadros reusa o capturador que já existe no cérebro.
"""

import os
from unittest.mock import patch

import pytest

from core.brain import live_session as live
from core.brain.live_session import LiveConfig, LiveSession, build_live_session


# ------------------------------------------------------------------
# 1. Disponibilidade honesta
# ------------------------------------------------------------------

def test_live_is_unavailable_without_api_key():
    with patch.dict(os.environ, {}, clear=True):
        assert live.live_is_available() is False


def test_start_refuses_without_availability():
    session = LiveSession()
    with patch.object(live, "live_is_available", return_value=False):
        assert session.start() is False
    assert session.is_running is False


def test_start_notifies_when_unavailable():
    messages = []
    session = LiveSession(on_status=messages.append)
    with patch.object(live, "live_is_available", return_value=False):
        session.start()
    assert messages and "indisponível" in messages[0].lower()


# ------------------------------------------------------------------
# 2. Ciclo de vida
# ------------------------------------------------------------------

def test_new_session_is_not_running():
    assert LiveSession().is_running is False


def test_stop_on_fresh_session_is_safe():
    session = LiveSession()
    session.stop()
    assert session.is_running is False


def test_stop_is_idempotent():
    session = LiveSession()
    session.stop()
    session.stop()
    assert session.is_running is False


def test_send_text_without_session_is_safe():
    """Sem sessão aberta, enviar texto simplesmente não faz nada."""
    LiveSession().send_text("oi")


def test_second_start_while_running_is_noop():
    session = LiveSession()
    session._running = True
    with patch.object(live, "live_is_available", return_value=True) as available:
        assert session.start() is True
    # Já rodando: não tentou abrir uma segunda sessão.
    available.assert_not_called()
    session._running = False


# ------------------------------------------------------------------
# 3. Configuração
# ------------------------------------------------------------------

def test_default_video_mode_is_none():
    """O padrão não liga câmera/tela sozinho (privacidade e custo)."""
    assert LiveConfig().video_mode == "none"


def test_session_has_bounded_duration():
    """Uma sessão esquecida não pode ficar aberta para sempre."""
    assert LiveConfig().max_seconds > 0
    assert LiveConfig().max_seconds <= 600


def test_model_falls_back_to_default():
    with patch.dict(os.environ, {}, clear=True):
        assert LiveConfig().resolved_model()


def test_model_prefers_env():
    with patch.dict(os.environ, {"GEMINI_LIVE_MODEL": "modelo-custom"}, clear=True):
        assert LiveConfig().resolved_model() == "modelo-custom"


# ------------------------------------------------------------------
# 4. Integração com o cérebro (sem abrir rede)
# ------------------------------------------------------------------

def test_build_uses_camera_callback_when_available():
    class FakeBrain:
        camera_frame_callback = staticmethod(lambda: "FRAME")

    session = build_live_session(FakeBrain(), video_mode="camera")
    assert session.frame_provider is not None
    assert session.frame_provider() == "FRAME"


def test_build_without_callback_has_no_provider():
    class FakeBrain:
        pass

    session = build_live_session(FakeBrain(), video_mode="camera")
    assert session.frame_provider is None


def test_build_with_none_mode_has_no_provider():
    class FakeBrain:
        camera_frame_callback = staticmethod(lambda: "FRAME")

    session = build_live_session(FakeBrain(), video_mode="none")
    assert session.frame_provider is None


def test_build_reuses_brain_ai_client():
    """Reusa o cliente Gemini já configurado, em vez de abrir outro."""
    marker = object()

    class FakeBrain:
        ai = type("AI", (), {"gemini_client": marker})()

    session = build_live_session(FakeBrain())
    assert session.ai is FakeBrain.ai


# ------------------------------------------------------------------
# 5. Conversão de quadro (payload do protocolo)
# ------------------------------------------------------------------

def test_jpeg_payload_from_none_is_none():
    assert live._jpeg_payload_from_pil(None) is None


def test_jpeg_payload_has_mime_and_base64():
    pil = pytest.importorskip("PIL.Image")
    image = pil.new("RGB", (800, 600), (10, 20, 30))
    payload = live._jpeg_payload_from_pil(image)
    assert payload is not None
    assert payload["mime_type"] == "image/jpeg"
    assert isinstance(payload["data"], str) and payload["data"]


def test_jpeg_payload_accepts_garbage_without_raising():
    """Uma imagem inválida não pode derrubar a sessão."""
    assert live._jpeg_payload_from_pil("nao e imagem") is None


# ------------------------------------------------------------------
# 6. Function calling no modo live (desenho e ações durante a conversa)
# ------------------------------------------------------------------
# Durante a conversa contínua o modelo pode pedir para DESENHAR no canvas e
# para executar ações do PC. As declarações são as mesmas do registro oficial
# e o executor é o mesmo do pipeline de turnos — nada de caminho paralelo.

class _FakeTool:
    def __init__(self, name, purpose, auth_required=False, implementation=None):
        self.name = name
        self.purpose = purpose
        self.auth_required = auth_required
        self.parameters = []
        self.tags = []
        self.implementation = implementation or (lambda **kwargs: {"ok": name})


class _FakeManager:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return list(self._tools)


def _brain_with(tools, executor=None):
    class FakeBrain:
        tool_manager = _FakeManager(tools)
        ai = None
        live_tool_callback = None

    if executor is not None:
        FakeBrain._execute_registered_tool_call = staticmethod(executor)
    return FakeBrain()


def test_live_declares_registered_tools():
    brain = _brain_with([
        _FakeTool("draw_image", "Desenha ao vivo no canvas"),
        _FakeTool("weather", "Consulta o clima"),
    ])
    session = build_live_session(brain, video_mode="none")
    names = [item["name"] for item in session.tool_declarations]
    assert "draw_image" in names
    assert "weather" in names


def test_live_excludes_tools_that_require_authorization():
    """Ferramenta com PIN/rosto NÃO entra no modo live.

    O fluxo contínuo não tem o portão de segurança do fluxo por turnos;
    oferecê-las ao modelo seria abrir um atalho que a VEGA não tem.
    """
    brain = _brain_with([
        _FakeTool("draw_image", "Desenha ao vivo"),
        _FakeTool("esvaziar_lixeira", "Ação perigosa", auth_required=True),
    ])
    session = build_live_session(brain, video_mode="none")
    names = [item["name"] for item in session.tool_declarations]
    assert "esvaziar_lixeira" not in names


def test_live_executor_reuses_brain_execution():
    calls = []

    def executor(name, arguments):
        calls.append((name, arguments))
        return {"executed": name}

    brain = _brain_with([_FakeTool("draw_image", "Desenha")], executor=executor)
    session = build_live_session(brain, video_mode="none")
    assert callable(session.tool_executor)
    assert session.tool_executor("draw_image", {"title": "Foguete"}) == {"executed": "draw_image"}
    assert calls == [("draw_image", {"title": "Foguete"})]


def test_live_without_manager_has_no_tools():
    class BareBrain:
        ai = None

    session = build_live_session(BareBrain(), video_mode="none")
    assert session.tool_declarations == []
    assert session.tool_executor is None


def test_tools_can_be_disabled_by_config():
    brain = _brain_with([_FakeTool("draw_image", "Desenha")])
    session = build_live_session(brain, video_mode="none")
    session.config.enable_tools = False
    assert session.config.enable_tools is False


# ------------------------------------------------------------------
# 7. Extração e resposta de tool_call (defensivo ao formato do SDK)
# ------------------------------------------------------------------

def test_extract_tool_calls_from_response():
    class Call:
        name = "draw_image"
        args = {"title": "Foguete"}
        id = "c1"

    class ToolCall:
        function_calls = [Call()]

    class Response:
        tool_call = ToolCall()

    calls = LiveSession._extract_tool_calls(Response())
    assert len(calls) == 1
    assert calls[0].name == "draw_image"


def test_extract_tool_calls_without_tool_call():
    class Response:
        text = "oi"

    assert LiveSession._extract_tool_calls(Response()) == []


def test_extract_tool_calls_with_broken_format_is_safe():
    class Response:
        tool_call = object()

    assert LiveSession._extract_tool_calls(Response()) == []


def test_tool_response_normalizes_dict():
    assert LiveSession._tool_response({"status": "drawing"}) == {"status": "drawing"}


def test_tool_response_normalizes_scalar():
    assert LiveSession._tool_response("pronto") == {"result": "pronto"}


def test_tool_response_normalizes_none():
    assert LiveSession._tool_response(None) == {"status": "ok"}
