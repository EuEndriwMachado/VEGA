# -*- coding: utf-8 -*-
"""Confirmação pela INTENÇÃO, não por palavra-chave.

Cenário real: a VEGA pergunta algo do tipo "quer que eu...?", o usuário
responde com um imperativo ("abre aí") e ela responde "abrir o quê?" — porque
"abre aí" não era palavra de confirmação na lista.

Regras garantidas:
  1. Imperativo só conta como "sim" QUANDO há pergunta pendente da VEGA
     (accept_imperative=True). Fora disso, "abre aí" segue sendo comando.
  2. A pergunta de follow-up das notícias registra handler no cérebro:
     confirmada, ela ABRE as notícias que leu.
  3. Sem pergunta pendente, nada muda no fluxo normal.
"""

import pytest

from core.brain.confirmation import classify_confirmation
from core.brain.brain import VegaBrain


# ------------------------------------------------------------------
# 1. Imperativo como resposta: só com pergunta pendente
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "abre aí", "abre ai", "pode abrir", "vai", "manda",
    "toca", "leia", "abre",
])
def test_imperative_counts_as_yes_when_question_was_asked(text):
    assert classify_confirmation(text, accept_imperative=True) is True


@pytest.mark.parametrize("text", [
    "abre aí", "abre", "abre o chrome",
])
def test_imperative_without_question_is_not_confirmation(text):
    """Sem pergunta pendente, imperativo é comando — nunca um 'sim'."""
    assert classify_confirmation(text, accept_imperative=False) is None
    assert classify_confirmation(text) is None


def test_negative_still_wins_over_imperative():
    # "não abre" recusa, mesmo com accept_imperative.
    assert classify_confirmation("não abre", accept_imperative=True) is False
    assert classify_confirmation("nao precisa abrir", accept_imperative=True) is False


# ------------------------------------------------------------------
# 2. Cérebro: resposta por intenção executa o handler
# ------------------------------------------------------------------

def test_brain_accepts_imperative_answer_to_own_question():
    handled = []
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_question = None
    brain.ask_pending_question("news_more", lambda yes: handled.append(yes) or "Abri as notícias.")

    assert brain._answer_pending_question("abre aí") == "Abri as notícias."
    assert handled == [True]


def test_brain_still_treats_imperative_as_command_without_question():
    """Sem pergunta pendente, "abre aí" NÃO é consumido: segue como comando."""
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_question = None

    assert brain._answer_pending_question("abre aí") is None


def test_brain_negative_answer_still_works():
    handled = []
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_question = None
    brain.ask_pending_question("news_more", lambda yes: handled.append(yes) or "ok, ficamos.")

    assert brain._answer_pending_question("não") == "ok, ficamos."
    assert handled == [False]
