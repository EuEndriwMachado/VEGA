# -*- coding: utf-8 -*-
"Testes de isolamento do modo cognitivo no turno."""

import threading
from unittest.mock import Mock
import pytest
import config.cognitive as cognitive
from core.brain.brain import VegaBrain
@pytest.fixture
def brain(monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", "legacy")
    instance = VegaBrain.__new__(VegaBrain)
    instance.ai = Mock()
    instance.ai._coerce_text.side_effect = lambda value: str(value)
    instance.memory = Mock()
    instance.conversation = Mock()
    instance.conversation.get_messages.return_value = []
    instance.orchestrator = Mock()
    instance.orchestrator.classify_turn_intent.return_value = "new"
    instance.orchestrator.resolve_references.return_value = ("", False)
    instance.orchestrator.task.state = "pending"
    instance.conversation.context.snapshot.return_value = {}
    instance.phone_guard = Mock()
    instance.tool_manager = Mock()
    instance._process_stream_lock = threading.RLock()
    instance._response_cancelled = threading.Event()
    instance._pending_dangerous_action = None
    instance._pending_close_app = None
    instance._last_opened_app = None
    instance.database_view_callback = None
    instance.brain_view_callback = None
    instance.environment_callback = None
    instance.holographic_command_callback = None
    instance.scheduler = Mock()
    instance.daily_support = Mock()
    instance.task_workflow = Mock()
    instance.task_workflow.handle.return_value = None
    instance.productivity = Mock()
    instance.tool_result_callback = None
    instance.app_opened_callback = None
    monkeypatch.setattr(instance, "_is_short_nonsense", lambda _text: False)
    monkeypatch.setattr(instance, "_phone_security_gate", lambda _text: None)
    monkeypatch.setattr(instance, "_process_security_request", lambda _text: None)
    monkeypatch.setattr(instance, "_process_pending_security_response", lambda _text: None)
    monkeypatch.setattr(instance, "_process_daily_support_request", lambda _text: None)
    monkeypatch.setattr(instance, "_handle_task_workflow", lambda _text: None)
    monkeypatch.setattr(instance, "_is_holographic_environment_request", lambda _text: False)
    monkeypatch.setattr(instance, "_process_holographic_interaction_command", lambda _text: None)
    monkeypatch.setattr(instance, "_is_visual_analysis_request", lambda _text: False)
    monkeypatch.setattr(instance, "_is_database_view_request", lambda _: False)
    monkeypatch.setattr("core.brain.brain.is_brain_view_request", lambda _text: False)
    monkeypatch.setattr(instance, "_answer_time_or_date_question", lambda _text: None)
    monkeypatch.setattr(instance, "_process_scheduler_request", lambda _text: None)
    monkeypatch.setattr(instance, "_is_recycle_bin_request", lambda _text: False)
    monkeypatch.setattr(instance, "_is_file_delete_request", lambda _text: False)
    monkeypatch.setattr(instance, "_process_mode", lambda _text: None)
    monkeypatch.setattr(instance, "_process_command", lambda _text: None)
    monkeypatch.setattr(instance, "_process_tool_request", lambda _text: None)
    monkeypatch.setattr(instance, "_looks_like_action_command", lambda _text: False)
    monkeypatch.setattr(instance, "_register_user_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(instance, "_prepare_messages", lambda _text: [])
    monkeypatch.setattr(instance, "_stream_model_response", lambda *_args, **_kwargs: iter(["resposta com contexto"]))
    return instance

def test_short_greeting_in_legacy_stays_local_without_token(brain):
    assert list(brain.process_stream("oi")) == ["Estou ouvindo."]
    brain.ai.chat_stream.assert_not_called()


def test_contextual_greeting_in_generated_mode_reaches_model(brain, monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", "generated")
    brain.conversation.get_messages.return_value = [{"role": "assistant", "content": "Perguntei algo."}]
    response = list(brain.process_stream("oi"))
    assert "".join(response) == "resposta com contexto"
    brain.conversation.get_messages.assert_called_once()


def test_isolated_greeting_in_generated_mode_remains_free(brain, monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", "generated")
    brain.conversation.get_messages.return_value = []
    assert list(brain.process_stream("oi")) == ["Estou ouvindo."]
    assert brain.conversation.get_messages.call_count == 1

def test_security_challenge_is_not_sent_to_narrator(brain, monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", "generated")
    monkeypatch.setattr(instance := brain, "_phone_security_gate", lambda _text: "Confirme e informe o PIN.")
    assert list(brain.process_stream("controle celular")) == ["Confirme e informe o PIN."]
    brain.ai.chat_stream.assert_not_called()


def test_time_answer_stays_local_in_generated_mode(brain, monkeypatch):
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", "generated")
    monkeypatch.setattr(brain, "_answer_time_or_date_question", lambda _text: "São 12:30.")
    assert list(brain.process_stream("que horas são")) == ["São 12:30."]
    brain.ai.chat_stream.assert_not_called()
