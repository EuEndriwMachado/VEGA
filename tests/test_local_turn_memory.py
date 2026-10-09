# -*- coding: utf-8 -*-
"""A VEGA lembra do que disse e fez — em TODOS os caminhos, não só na cotação.

Bugs observados na conversa real:

1. "aumenta o brilho do pc" (funciona) + "aumenta mais" -> a IA não sabia
   "mais o quê?". Comando local executava e NÃO deixava registro no contexto —
   o mesmo beco sem saída do bug da cotação, mas no caminho de comandos.

2. Esperado: a continuação vaga ("mais", "aumenta mais") deve resolver
   LOCALMENTE (custo zero), reaproveitando o domínio da última ação.

Este módulo garante que:
  - todo turno resolvido localmente (comando, modo, ferramenta) registra o
    que a VEGA disse em conversation + orchestrator + memória diária;
  - "aumenta mais" / "diminui de novo" resolve localmente, de graça;
  - o contexto do último comando local persiste (brilho vs volume).
"""

import pytest

from core.brain.brain import VegaBrain
from core.actions.commands import VegaCommands
from core.actions.commands_parser import VegaCommandsParser


class _Memory:
    def __init__(self):
        self.messages = []

    def add_daily_message(self, role, text):
        self.messages.append((role, text))


@pytest.fixture
def brain():
    b = VegaBrain.__new__(VegaBrain)
    b.commands = VegaCommands()
    b.commands_parser = VegaCommandsParser(b.commands)
    b.memory = _Memory()
    b.last_tool_receipt = None
    b.last_local_command = None
    b._pending_dangerous_action = None
    from core.orchestration import ConversationOrchestrator
    b.orchestrator = ConversationOrchestrator()

    class _Context:
        @staticmethod
        def snapshot():
            return ""

    class _Conversation:
        context = _Context()

        def __init__(self):
            self.messages = []

        def get_messages(self):
            return list(self.messages)

        def add_user_message(self, text):
            self.messages.append({"role": "user", "content": text})

        def add_assistant_message(self, text):
            self.messages.append({"role": "assistant", "content": text})

    b.conversation = _Conversation()
    return b


# ------------------------------------------------------------------
# 1. Comando local deixa registro
# ------------------------------------------------------------------

def test_local_command_is_recorded_in_conversation(brain):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.", kind="command")

    roles = [m["role"] for m in brain.conversation.get_messages()]
    assert "user" in roles and "assistant" in roles
    last = brain.conversation.get_messages()[-1]
    assert last["content"] == "Brilho subindo para 100%."


def test_local_command_is_recorded_in_memory(brain):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.")
    roles = [role for role, _ in brain.memory.messages]
    assert roles.count("user") >= 1 and roles.count("assistant") >= 1


def test_local_command_records_orchestrator_turns(brain):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.")
    roles = [entry["role"] for entry in brain.orchestrator.immediate]
    assert "user" in roles and "assistant" in roles


def test_empty_response_is_not_recorded(brain):
    brain._record_local_turn("aumenta o brilho", "")
    assert brain.conversation.get_messages() == []


# ------------------------------------------------------------------
# 2. Contexto do último comando (domínio + direção)
# ------------------------------------------------------------------

def test_brightness_response_sets_last_local_command(brain):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.")
    assert brain.last_local_command == {"domain": "brightness", "direction": "up"}


def test_volume_response_sets_last_local_command(brain):
    brain._record_local_turn("aumenta o volume", "Volume subindo.")
    assert brain.last_local_command == {"domain": "volume", "direction": "up"}


def test_non_domain_response_does_not_set_context(brain):
    brain._record_local_turn("abre o chrome", "Abrindo o chrome.")
    assert brain.last_local_command is None


# ------------------------------------------------------------------
# 3. Continuação vaga resolve localmente (custo zero)
# ------------------------------------------------------------------

def test_aumenta_mais_continues_brightness(brain, monkeypatch):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.")
    executed = []
    monkeypatch.setattr(
        brain.commands, "brightness_up", lambda: executed.append("up") or "Brilho subindo."
    )

    response = brain._resolve_local_continuation("aumenta mais")

    assert response == "Brilho subindo."
    assert executed == ["up"]


def test_menos_continues_brightness_down(brain, monkeypatch):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.")
    executed = []
    monkeypatch.setattr(
        brain.commands, "brightness_down", lambda: executed.append("down") or "Brilho baixando."
    )

    response = brain._resolve_local_continuation("menos")

    assert response == "Brilho baixando."
    assert executed == ["down"]


def test_continuation_uses_volume_domain(brain, monkeypatch):
    brain._record_local_turn("abaixa o volume", "Baixando o volume.")
    executed = []
    monkeypatch.setattr(
        brain.commands, "volume_down", lambda: executed.append("down") or "Volume baixando."
    )

    assert brain._resolve_local_continuation("mais um pouco") == "Volume baixando."
    assert executed == ["down"]


def test_continuation_without_prior_command_is_none(brain):
    assert brain._resolve_local_continuation("mais") is None


def test_full_command_is_not_continuation(brain):
    """Pedidos completos ("aumenta o volume da tv") são do parser, não daqui."""
    brain._record_local_turn("aumenta o volume", "Volume subindo.")
    assert brain._resolve_local_continuation("aumenta o volume da tv") is None


def test_conversational_text_is_not_continuation(brain):
    brain._record_local_turn("aumenta o brilho", "Brilho subindo para 100%.")
    assert brain._resolve_local_continuation("me explica o que é buraco negro") is None


# ------------------------------------------------------------------
# 4. Fluxo completo: comando + continuação (via process_stream)
# ------------------------------------------------------------------

def test_stream_command_then_continuation(brain, monkeypatch):
    """O cenário exato do bug: brilho + 'aumenta mais'."""

    class _AI:
        @staticmethod
        def chat_stream(messages, **kwargs):
            yield ""

    brain.ai = _AI()

    from config.cognitive import LEGACY
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", LEGACY)

    # Primeiro turno: comando de brilho.
    monkeypatch.setattr(
        brain.commands, "brightness_up", lambda: "Brilho subindo para 100%."
    )
    first = list(brain.process_stream("aumenta o brilho"))
    assert first and "Brilho" in first[0]
    assert brain.last_local_command == {"domain": "brightness", "direction": "up"}

    # Segundo turno: "aumenta mais" — deve continuar o brilho SEM IA.
    second = list(brain.process_stream("aumenta mais"))
    assert second and "Brilho" in second[0]
