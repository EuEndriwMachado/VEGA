# -*- coding: utf-8 -*-
"""Respostas de sim/não funcionam em QUALQUER lugar, não só no loop de voz.

Bug real: a VEGA pergunta "quer que eu leia as notícias?" e o usuário responde
"oi vega, quero sim" — e ela responde "Quer o quê exatamente?". O classificador
local do loop de voz existia, mas vivia SÓ lá: por texto, ou em qualquer outro
timing, a resposta caía no cérebro que não sabia da própria pergunta.

Agora: o cérebro lembra das PRÓPRIAS perguntas (ask_pending_question) e
classifica a resposta em qualquer posição da fala (confirmation.py).
"""

import pytest

from core.brain.confirmation import classify_confirmation
from core.brain.brain import VegaBrain
from tools.api_tools import build_tool_registry


# ------------------------------------------------------------------
# 1. Classificador universal
# ------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # Vocativo no começo: era exatamente o caso que falhava.
    ("oi vega, quero sim", True),
    ("oi vega quero sim", True),
    ("vega, quero sim", True),
    ("ei vega, sim", True),
    # Formas diretas.
    ("sim", True),
    ("quero", True),
    ("pode ler", True),
    ("bora", True),
    # Negação.
    ("nao", False),
    ("nao quero", False),
    ("agora nao", False),
    ("oi vega, nao", False),
    # Ambiguidade: última intenção vence.
    ("sim, mas nao agora", False),
    ("nao, quer dizer, sim", True),
    # Não é resposta de sim/não.
    ("abre o chrome", None),
    ("qual a cotação do dólar", None),
    ("", None),
])
def test_classify_confirmation(text, expected):
    assert classify_confirmation(text) is expected


def test_positive_word_inside_other_word_does_not_confirm():
    assert classify_confirmation("o resultado do simulado saiu") is None


# ------------------------------------------------------------------
# 2. O cérebro lembra das próprias perguntas
# ------------------------------------------------------------------

@pytest.fixture
def brain():
    b = VegaBrain.__new__(VegaBrain)
    b._pending_question = None
    return b


def test_brain_remembers_and_answers_its_own_question(brain):
    handled = []
    brain.ask_pending_question("news", lambda yes: handled.append(yes) or "ok")

    assert brain.has_pending_question()

    assert brain._answer_pending_question("oi vega, quero sim") == "ok"
    assert handled == [True]
    # Consumida: a próxima fala não repete a resposta.
    assert not brain.has_pending_question()


def test_brain_consumes_pending_question_on_unrelated_speech(brain):
    handled = []
    brain.ask_pending_question("news", lambda yes: handled.append(yes))

    # "abre o chrome" não é sim/não: pergunta expira e o fluxo segue.
    assert brain._answer_pending_question("abre o chrome") is None
    assert handled == []
    assert not brain.has_pending_question()


def test_brain_handles_negative_answer(brain):
    handled = []
    brain.ask_pending_question("news", lambda yes: handled.append(yes) or ("neg" if not yes else "pos"))

    assert brain._answer_pending_question("nao quero") == "neg"
    assert handled == [False]


# ------------------------------------------------------------------
# 3. Fluxo completo: a resposta passa pelo process_stream
# ------------------------------------------------------------------

def test_stream_answers_pending_question():
    """O cenário exato do bug: pergunta feita, resposta 'oi vega, quero sim'."""
    from core.orchestration import ConversationOrchestrator

    b = VegaBrain.__new__(VegaBrain)
    b._pending_question = None
    b.orchestrator = ConversationOrchestrator()

    class _AI:
        @staticmethod
        def chat_stream(messages, **kwargs):
            yield ""

    b.ai = _AI()

    import config.cognitive as cognitive
    cognitive.get_mode = staticmethod(lambda: "legacy")

    handled = []
    b.ask_pending_question("news", lambda yes: handled.append(yes) or "Lendo as notícias.")

    responses = list(b.process_stream("oi vega, quero sim"))

    assert handled == [True]
    assert responses and "Lendo as notícias." in responses[0]


def test_stream_ignores_unrelated_speech_after_question():
    from core.orchestration import ConversationOrchestrator

    b = VegaBrain.__new__(VegaBrain)
    b._pending_question = None
    b._pending_dangerous_action = None
    b.orchestrator = ConversationOrchestrator()

    class _AI:
        @staticmethod
        def chat_stream(messages, **kwargs):
            yield ""

    b.ai = _AI()
    b.tool_manager = build_tool_registry()

    class _Memory:
        add_daily_message = staticmethod(lambda role, text: None)
        build_memory_context = staticmethod(lambda t: "")
        retrieve_relevant = staticmethod(lambda t, limit=4: [])
        contextual_recall = staticmethod(lambda t, limit=4: [])

    b.memory = _Memory()
    b.agent_state = type("A", (), {"observe": staticmethod(lambda t: None),
                                   "render_context_prompt": staticmethod(lambda: "")})()
    b._update_context_state = staticmethod(lambda t: None)
    b.conversation = type("C", (), {
        "get_messages": staticmethod(lambda: []),
        "add_user_message": staticmethod(lambda t: None),
        "add_assistant_message": staticmethod(lambda t: None),
        "context": type("X", (), {"snapshot": staticmethod(lambda: "")})(),
    })()

    import config.cognitive as cognitive
    cognitive.get_mode = staticmethod(lambda: "legacy")

    handled = []
    b.ask_pending_question("news", lambda yes: handled.append(yes))

    # Fala que não é resposta: pergunta expira e o fluxo normal segue
    # (aqui, sem provedor de IA, a resposta é vazia — mas nada trava).
    list(b.process_stream("abre o chrome"))
    assert handled == []
    assert not b.has_pending_question()
