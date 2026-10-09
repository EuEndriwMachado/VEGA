# -*- coding: utf-8 -*-
"""Fase 0.5 — o dado da ferramenta precisa sobreviver ao turno seguinte.

Caso real que originou a correção:

    usuário: "qual a cotação do dólar hoje"   -> ferramenta responde (0 token)
    usuário: "e por que isso?"                -> VEGA respondia "porque o quê?"

Causa: o caminho de ferramenta respondia e dava return sem registrar o turno
no orquestrador e sem guardar o dado concreto obtido. O turno seguinte entrava
em contexto perdido e a IA só via uma pergunta solta.
"""

import pytest

from core.brain.tool_receipt import build_receipt, ToolReceipt, _fmt_number
from core.brain.brain import VegaBrain
from core.orchestration import ConversationOrchestrator


CURRENCY_RESULT = {
    "source": "awesomeapi",
    "base": "USD",
    "target": "BRL",
    "amount": 1.0,
    "rate": 5.4321,
    "converted": 5.4321,
    "high": 5.4711,
    "low": 5.4102,
    "pct_change": -0.83,
}


# ------------------------------------------------------------------
# Recibo estruturado
# ------------------------------------------------------------------

def test_currency_receipt_keeps_the_real_numbers():
    receipt = build_receipt("currency", CURRENCY_RESULT, user_question="qual a cotação do dólar hoje")

    assert receipt is not None
    assert receipt.tool == "currency"
    assert receipt.facts["rate"] == pytest.approx(5.4321)
    assert "5,43" in receipt.summary
    assert "queda" in receipt.summary

    block = receipt.to_context_block()
    assert "DADO VERIFICADO" in block
    assert "5,43" in block
    assert "USD" in block and "BRL" in block
    assert "queda de 0,83% hoje" in block
    assert "5,47" in block and "5,41" in block


def test_weather_receipt_keeps_temperature_and_place():
    receipt = build_receipt(
        "weather",
        {
            "location": "São Paulo",
            "temperature_c": 27.4,
            "feels_like_c": 29.1,
            "condition": "parcialmente nublado",
            "temp_max": 31.0,
            "temp_min": 19.0,
            "rain_prob": 60,
        },
    )

    assert receipt is not None
    block = receipt.to_context_block()
    assert "São Paulo" in block
    assert "27°C" in block
    assert "parcialmente nublado" in block
    assert "60%" in block


def test_receipt_expires_and_stops_being_injected():
    receipt = ToolReceipt(tool="currency", summary="Cotação USD→BRL: 5,43", ttl_seconds=10.0)
    # Fora da validade: nada é injetado, para a IA não responder com dado velho.
    assert receipt.to_context_block(now=receipt.at + 11.0) == ""
    # Dentro da validade: injeta normalmente.
    assert "DADO VERIFICADO" in receipt.to_context_block(now=receipt.at + 5.0)


def test_error_or_empty_results_do_not_create_receipts():
    assert build_receipt("currency", {"error": "sem rede"}) is None
    assert build_receipt("currency", {}) is None
    assert build_receipt("weather", {"location": "X"}) is None
    assert build_receipt("ferramenta_desconhecida", {"a": 1}) is None
    assert build_receipt("currency", "texto solto") is None


def test_number_formatting_uses_brazilian_decimal_separator():
    assert _fmt_number(5.4321) == "5,43"
    assert _fmt_number(1234.5) == "1.234,50"
    assert _fmt_number(27.4, 0) == "27"


# ------------------------------------------------------------------
# Integração com o cérebro
# ------------------------------------------------------------------

def test_brain_stores_receipt_after_tool_request():
    brain = VegaBrain.__new__(VegaBrain)
    brain.orchestrator = ConversationOrchestrator()
    brain.last_tool_receipt = None

    brain._store_tool_receipt("currency", CURRENCY_RESULT, user_question="cotação do dólar")

    assert brain.last_tool_receipt is not None
    assert brain.last_tool_receipt.tool == "currency"
    # O orquestrador recebe o assunto para resolver "e por isso?" depois.
    assert "5,43" in brain.orchestrator.last_tool_subject


def test_receipt_reaches_the_model_messages():
    brain = VegaBrain.__new__(VegaBrain)
    brain.orchestrator = ConversationOrchestrator()
    brain.last_tool_receipt = None
    brain._store_tool_receipt("currency", CURRENCY_RESULT, user_question="cotação do dólar")

    class _Context:
        @staticmethod
        def snapshot():
            return ""

    class _Conversation:
        context = _Context()

        def get_messages(self):
            return [{"role": "user", "content": "e por que isso?"}]

    brain.conversation = _Conversation()
    brain.memory = type(
        "M", (), {
            "build_memory_context": staticmethod(lambda _t: ""),
            "retrieve_relevant": staticmethod(lambda _t, limit=4: []),
            "contextual_recall": staticmethod(lambda _t, limit=4: []),
        },
    )()
    brain.agent_state = type("A", (), {"render_context_prompt": staticmethod(lambda: "")})()

    messages = brain._prepare_messages("e por que isso?")

    system_blocks = [m["content"] for m in messages if m.get("role") == "system"]
    assert any("DADO VERIFICADO" in block for block in system_blocks)
    assert any("5,43" in block for block in system_blocks)


# ------------------------------------------------------------------
# Resolução de referência: "isso" aponta para o dado consultado
# ------------------------------------------------------------------

def test_reference_resolves_to_the_last_tool_subject():
    orchestrator = ConversationOrchestrator()
    orchestrator.note_tool_receipt(
        "Cotação USD→BRL: 5,43 (queda de 0,83% hoje)",
        "a cotação USD→BRL",
    )

    rewritten, changed = orchestrator.resolve_references("e por que isso?")

    assert changed is True
    assert "a cotação USD→BRL" in rewritten
    assert "está nesse valor" in rewritten
    assert "isso" not in rewritten.lower()


def test_reference_without_receipt_falls_back_to_task():
    orchestrator = ConversationOrchestrator()
    orchestrator.start_task("open_app", target="chrome")

    rewritten, changed = orchestrator.resolve_references("fecha isso")

    assert changed is True
    assert "chrome" in rewritten


def test_clear_resets_the_tool_subject():
    orchestrator = ConversationOrchestrator()
    orchestrator.note_tool_receipt("Cotação USD→BRL: 5,43")
    orchestrator.clear()

    assert orchestrator.last_tool_subject == ""
    _, changed = orchestrator.resolve_references("e por que isso?")
    assert changed is False