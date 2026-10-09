# -*- coding: utf-8 -*-
"""Gatilhos de visão como dados: tela vence câmera, sempre.

Garante que a extração das três listas duplicadas do cérebro para um módulo
único não mudou o comportamento — e que a regra de ouro continua valendo:
uma fala que cita a tela NUNCA abre a câmera.
"""

import pytest

from core.brain.visual_triggers import (
    classify_visual_request,
    describe,
    fold,
    is_camera_request,
    is_mindmap_request,
    is_screen_request,
    mentions_screen,
)


# ------------------------------------------------------------------
# 1. Reconhecimento de tela
# ------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "olha a minha tela",
    "olha isso na tela",
    "analisa a tela",
    "descreve a tela",
    "o que tem na tela",
    "o que aparece na tela",
    "me ajuda com esse codigo",
    "revisa esse codigo",
    "o que eu faco aqui",
    "qual o proximo passo",
])
def test_screen_requests_are_recognized(phrase):
    assert is_screen_request(phrase) is True


# ------------------------------------------------------------------
# 2. Reconhecimento de câmera (explícito e implícito)
# ------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "o que voce esta vendo na camera",
    "analisa a camera",
    "olha pela camera",
    "o que aparece na webcam",
    "o que voce esta vendo",
    "o que voce esta vendo agora",
    "o que vc ve agora",
])
def test_camera_requests_are_recognized(phrase):
    assert is_camera_request(phrase) is True


# ------------------------------------------------------------------
# 3. A regra de ouro: tela vence câmera
# ------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "o que voce esta vendo na minha tela",
    "o que esta vendo na minha tela",
    "o que voce esta vendo na tela",
    "o que tem na minha tela",
])
def test_screen_wins_over_camera(phrase):
    """Uma fala que cita a tela não pode virar captura de câmera.

    Antes, o gatilho genérico de câmera ("o que esta vendo") capturava essas
    frases e a VEGA abria a webcam em vez de fotografar a tela.
    """
    assert mentions_screen(phrase) is True
    assert is_camera_request(phrase) is False
    assert classify_visual_request(phrase).kind == "screen"


# ------------------------------------------------------------------
# 4. Classificação unificada
# ------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    ("olha a minha tela", "screen"),
    ("me ajuda com esse trabalho", "screen"),
    ("o que voce esta vendo na camera", "camera"),
    ("o que voce esta vendo", "camera"),
])
def test_classify_returns_expected_kind(phrase, expected):
    assert classify_visual_request(phrase).kind == expected


@pytest.mark.parametrize("phrase", [
    "abre o chrome",
    "aumenta o volume",
    "qual a previsao do tempo",
    "",
])
def test_non_visual_speech_is_not_visual(phrase):
    request = classify_visual_request(phrase)
    assert request.kind is None
    assert request.is_visual is False


def test_visual_request_flags():
    camera = classify_visual_request("analisa a camera")
    assert camera.is_visual is True
    assert camera.wants_camera is True

    screen = classify_visual_request("analisa a tela")
    assert screen.is_visual is True
    assert screen.wants_camera is False


# ------------------------------------------------------------------
# 5. Mapa mental exige resumo anterior
# ------------------------------------------------------------------

def test_mindmap_requires_previous_summary():
    """Sem resumo visual anterior, "mapa mental" não é pedido de visão."""
    assert is_mindmap_request("faz um mapa mental", has_previous_summary=False) is False
    assert is_mindmap_request("faz um mapa mental", has_previous_summary=True) is True


def test_mindmap_with_summary_is_screen():
    request = classify_visual_request("faz um mapa mental disso", has_previous_summary=True)
    assert request.kind == "screen"


# ------------------------------------------------------------------
# 6. Normalização
# ------------------------------------------------------------------

def test_fold_removes_accents():
    """`fold` do módulo de visão só normaliza caixa e acentos.

    Ele NÃO remove pontuação de propósito: os gatilhos são comparados por
    substring, e limpar a pontuação mudaria como uma frase é casada.
    """
    assert fold("Está na TELA") == "esta na tela"


def test_triggers_are_accent_free():
    """Gatilhos sem acento valem para texto digitado e transcrito.

    `fold` normaliza acentos e expande abreviações, então o gatilho precisa
    estar na forma canônica: minúsculo, sem acento e sem abreviação.
    """
    from core.brain.visual_triggers import expand_abbreviations

    for entry in describe():
        trigger = entry["trigger"]
        assert fold(trigger) == trigger
        # E precisa ser estável: normalizar de novo não muda nada.
        assert expand_abbreviations(trigger) == trigger


def test_catalog_inventory_is_not_empty():
    kinds = {entry["kind"] for entry in describe()}
    assert kinds == {"screen", "camera"}
