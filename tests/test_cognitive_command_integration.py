# -*- coding: utf-8 -*-
"""Integração do modo cognitivo ao ponto de saída de comandos."""

import threading
from unittest.mock import Mock
import config.cognitive as cognitive
from core.brain.brain import VegaBrain

def _command_brain(monkeypatch, mode):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", mode)
    brain = VegaBrain.__new__(VegaBrain)
    brain.ai = Mock()
    brain.memory = Mock()
    brain.conversation = Mock()
    brain.agent_state = Mock()
    brain.orchestrator = Mock()
    brain.daily_support = Mock()
    brain.task_workflow = Mock()
    brain.productivity = Mock()
    brain.scheduler = Mock()
    brain.phone_guard = Mock()
    brain.commands = Mock()
    brain.commands_parser = Mock()
    brain.commands_parser.parse.return_value = "Abrindo Paint."
    brain._pending_dangerous_action = None
    brain._last_opened_app = None
    brain._pending_close_app = None
    brain.tool_manager = Mock()
    brain.tool_manager.resolve_for_text.return_value = None
    brain.tool_result_callback = None
    brain.app_opened_callback = None
    brain.database_view_callback = None
    brain.brain_view_callback = None
    brain.environment_callback = None
    brain.holographic_command_callback = None
    brain._response_cancelled = threading.Event()
    brain._process_stream_lock = threading.RLock()

    # Isola o transporte da resposta para não executar nenhuma ação do sistema.
    monkeypatch.setattr(brain, "_process_command", lambda text: "Abrindo Paint.")
    monkeypatch.setattr(brain, "_is_short_nonsense", lambda text: False)
    monkeypatch.setattr(brain, "_phone_security_gate", lambda text: None)
    monkeypatch.setattr(brain, "_process_security_request", lambda text: None)
    monkeypatch.setattr(brain, "_process_pending_security_response", lambda text: None)
    monkeypatch.setattr(brain, "_process_daily_support_request", lambda text: None)
    monkeypatch.setattr(brain, "_handle_task_workflow", lambda text: None)
    monkeypatch.setattr(brain, "_is_holographic_environment_request", lambda text: False)
    monkeypatch.setattr(brain, "_process_holographic_interaction_command", lambda text: None)
    monkeypatch.setattr(brain, "_is_visual_analysis_request", lambda text: False)
    monkeypatch.setattr(brain, "_is_database_view_request", lambda text: False)
    monkeypatch.setattr("core.brain.brain.is_brain_view_request", lambda text: False)
    monkeypatch.setattr(brain, "_answer_time_or_date_question", lambda text: None)
    monkeypatch.setattr(brain, "_process_scheduler_request", lambda text: None)
    monkeypatch.setattr(brain, "_is_recycle_bin_request", lambda text: False)
    monkeypatch.setattr(brain, "_is_file_delete_request", lambda text: False)
    monkeypatch.setattr(brain, "_process_mode", lambda text: None)
    monkeypatch.setattr(brain, "_process_tool_request", lambda text: None)
    monkeypatch.setattr(brain, "_looks_like_action_command", lambda text: False)
    monkeypatch.setattr(brain, "_register_user_turn", lambda *a, **k: None)
    monkeypatch.setattr(brain, "_prepare_messages", lambda text: [])
    return brain

def test_legacy_mode_returns_local_command_result_without_ai(monkeypatch):
    brain = _command_brain(monkeypatch, cognitive.LEGACY)
    assert "".join(brain.process_stream("abra o paint")) == "Abrindo Paint."
    brain.ai.chat_stream.assert_not_called()


def test_generated_mode_narrates_verified_command_result(monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", cognitive.GENERATED)
    brain = _command_brain(monkeypatch, cognitive.GENERATED)
    brain.ai.chat_stream.return_value = iter(["O Paint está aberto na tela."])
    assert "".join(brain.process_stream("abra o paint")) == "O Paint está aberto na tela."
    brain.ai.chat_stream.assert_called_once()
    messages = brain.ai.chat_stream.call_args.args[0]
    assert "abra o paint" in messages[0]["content"]
    assert "Abrindo Paint." in messages[0]["content"]


def test_generated_mode_keeps_result_if_narration_provider_fails(monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", cognitive.GENERATED)
    brain = _command_brain(monkeypatch, cognitive.GENERATED)
    brain.ai.chat_stream.return_value = iter([
        "Não consegui responder agora. Tente novamente em alguns segundos."
    ])
    assert "".join(brain.process_stream("abra o paint")) == "Abrindo Paint."
