import json
from datetime import datetime

from core.memory.daily_query import DailyQuery
from core.memory.memory import VegaMemory


def test_daily_query_understands_relative_and_named_dates(tmp_path):
    memory = VegaMemory(tmp_path / "memory")
    source = tmp_path / "memory" / "daily" / "2026-09-18.json"
    source.write_text(
        json.dumps([{"role": "user", "content": "Estudamos tabelas no MySQL."}]),
        encoding="utf-8",
    )
    query = DailyQuery(memory)

    answer = query.answer("o que rolou hoje?", now=datetime(2026, 9, 18, 12, 0))

    assert "Estudamos tabelas" in answer
    assert "18/09/2026" in answer


def test_daily_query_understands_numeric_date(tmp_path):
    memory = VegaMemory(tmp_path / "memory")
    source = tmp_path / "memory" / "daily" / "2026-09-17.json"
    source.write_text(json.dumps([{"role": "user", "content": "Evento antigo"}]), encoding="utf-8")
    query = DailyQuery(memory)

    answer = query.answer("o que aconteceu em 17/09/2026?", now=datetime(2026, 9, 18))

    assert "Evento antigo" in answer


def test_daily_query_does_not_capture_regular_questions(tmp_path):
    memory = VegaMemory(tmp_path / "memory")
    query = DailyQuery(memory)

    assert query.answer("o que é uma tabela no MySQL?") is None
