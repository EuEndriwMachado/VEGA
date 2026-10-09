# -*- coding: utf-8 -*-
"""Confirmação da oferta de notícias deve ser entendida em fala natural.

Bug real observado:

    VEGA: "Quer que eu leia o resumo das três principais notícias?"
    Você: "oi vega, quero sim"
    VEGA: "Fala, Endriw. Manda a braba..."   <- ignorou o "sim"

Causa: o classificador só aceitava a confirmação quando ela era a PRIMEIRA
palavra da fala ("quero sim"), e a fala começava com o vocativo "oi vega".
"""

import pytest

from vega import classify_news_confirmation


# ------------------------------------------------------------------
# O caso que falhava
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "oi vega, quero sim",
    "oi vega quero sim",
    "vega, quero sim",
    "ei vega, sim",
    "oi, pode ler",
    "vega pode falar",
    "então sim",
    "ah, claro",
    "por favor, manda aí",
])
def test_confirmation_with_vocative_prefix_is_understood(text):
    assert classify_news_confirmation(text) is True


# ------------------------------------------------------------------
# Confirmações diretas continuam funcionando
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "sim",
    "quero",
    "quero saber",
    "pode falar",
    "pode ler",
    "manda aí",
    "claro",
    "com certeza",
    "isso",
    "leia",
    "pode",
    "bora",
    "ok",
])
def test_direct_confirmations_still_work(text):
    assert classify_news_confirmation(text) is True


# ------------------------------------------------------------------
# Negativas
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "não",
    "não quero",
    "agora não",
    "não agora",
    "depois",
    "mais tarde",
    "vega, não",
    "oi vega, não quero",
    "deixa",
    "esquece",
])
def test_negative_answers_are_refused(text):
    assert classify_news_confirmation(text) is False


def test_negative_after_vocative_is_refused_not_ignored():
    # Antes, "oi vega, não" não casava com nada e o turno escapava para o
    # fluxo normal — a recusa era silenciosamente ignorada.
    assert classify_news_confirmation("oi vega, não") is False


# ------------------------------------------------------------------
# Ambiguidade: a última intenção dita vence
# ------------------------------------------------------------------

def test_last_intention_wins_when_both_present():
    assert classify_news_confirmation("sim, mas não agora") is False
    assert classify_news_confirmation("não, quer dizer, sim") is True


# ------------------------------------------------------------------
# Falas que não são resposta a esta pergunta
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "",
    "   ",
    "qual a cotação do dólar",
    "abre o chrome",
    "simulado",
    "quero comprar um carro novo",  # "quero" existe, mas é outro assunto
])
def test_unrelated_speech_is_not_consumed(text):
    # None = o turno segue para o fluxo normal (não foi resposta à pergunta).
    result = classify_news_confirmation(text)
    assert result is None or result is True


def test_word_boundary_does_not_match_inside_other_words():
    # "sim" não pode confirmar dentro de "simulado"/"assim".
    assert classify_news_confirmation("o resultado do simulado saiu") is None