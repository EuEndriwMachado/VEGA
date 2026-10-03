from datetime import datetime

from core.brain.text_utils import (
    is_short_nonsense,
    normalize_text,
    remove_vega_prefix,
)
from core.brain.time_answer import answer_time_or_date_question, portuguese_number
from core.memory.memory_context import build_memory_message, inject_memory


# ==========================================================
# text_utils
# ==========================================================


def test_normalize_text_lowercases_and_applies_corrections():
    assert normalize_text("ABRE O SPOTFY") == "abre o spotify"
    assert normalize_text("liga o chorme") == "liga o chrome"
    assert normalize_text("sobe o SOM") == "sobe o volume"


def test_normalize_text_strips_punctuation_and_extra_spaces():
    assert normalize_text("olá,,,   tudo   bem!!!") == "olá tudo bem"


def test_remove_vega_prefix():
    assert remove_vega_prefix("Vega, abre o chrome") == "abre o chrome"
    assert remove_vega_prefix("vega") == ""
    assert remove_vega_prefix("abre o chrome") == "abre o chrome"


def test_is_short_nonsense_accepts_safe_short_answers():
    assert is_short_nonsense("sim") is False
    assert is_short_nonsense("nao") is False
    assert is_short_nonsense("não") is False
    assert is_short_nonsense("talvez") is False


def test_is_short_nonsense_flags_noise():
    assert is_short_nonsense("") is False
    assert is_short_nonsense("zz") is True
    assert is_short_nonsense("!!!") is True


# ==========================================================
# time_answer
# ==========================================================


def test_portuguese_number():
    assert portuguese_number(0) == "zero"
    assert portuguese_number(9) == "nove"
    assert portuguese_number(15) == "quinze"
    assert portuguese_number(21) == "vinte e um"
    assert portuguese_number(30) == "trinta"


def test_answer_time_only():
    now = datetime(2026, 1, 5, 14, 30)
    answer = answer_time_or_date_question("que horas são", now=now)
    assert answer == "Agora são quatorze horas e trinta minutos."


def test_answer_date_only():
    now = datetime(2026, 3, 9, 10, 0)
    answer = answer_time_or_date_question("qual é a data de hoje", now=now)
    assert "9 de março de 2026" in answer


def test_answer_time_and_date():
    now = datetime(2026, 3, 9, 10, 5)
    answer = answer_time_or_date_question("me diga a hora e o dia", now=now)
    assert "Agora são" in answer
    assert "9 de março de 2026" in answer


def test_answer_returns_none_for_non_time_question():
    assert answer_time_or_date_question("abre o chrome") is None
    assert answer_time_or_date_question("") is None


# ==========================================================
# memory_context
# ==========================================================


def test_build_memory_message_contains_rules():
    message = build_memory_message("MEMÓRIA", context_snapshot="CTX", relevant_memories=[])
    assert message["role"] == "system"
    assert "REGRAS DE MEMÓRIA" in message["content"]
    assert "CTX" in message["content"]


def test_inject_memory_prepends_system_message():
    messages = [{"role": "user", "content": "oi"}]
    result = inject_memory(messages, "MEM", relevant_memories=[{"summary": "gosta de café"}])
    assert result[0]["role"] == "system"
    assert "gosta de café" in result[0]["content"]
    assert result[1] == {"role": "user", "content": "oi"}
    assert len(result) == 2
