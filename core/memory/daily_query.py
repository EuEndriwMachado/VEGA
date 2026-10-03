"""Interpreta perguntas naturais sobre eventos registrados no diário."""

import re
from datetime import date, datetime, timedelta


_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

_INTENT_RULES = (
    re.compile(r"\b(o que|que|qual|como foi|me diga|lembra)\b.*\b(aconteceu|ocorreu|rolou|teve|fizemos|falamos|falou|conversamos|conversou|discutimos|discutiu|evento|dia)\b"),
    re.compile(r"\b(aconteceu|ocorreu|rolou|teve|fizemos|falamos|falou|conversamos|conversou|discutimos|discutiu)\b.*\b(ontem|hoje|anteontem|dia|data|\d{1,2}[/-]\d{1,2})\b"),
    re.compile(r"\b(lembrancas?|historico|histórico|eventos?)\b.*\b(ontem|hoje|dia|data)\b"),
)

_DATE_PATTERNS = (
    (re.compile(r"\banteontem\b"), lambda current: current - timedelta(days=2)),
    (re.compile(r"\bontem\b"), lambda current: current - timedelta(days=1)),
    (re.compile(r"\bhoje\b"), lambda current: current),
)


class DailyQuery:
    def __init__(self, memory):
        self.memory = memory

    def answer(self, text, now=None):
        normalized = self._normalize(text)
        if not self._is_daily_question(normalized):
            return None

        current = (now or datetime.now()).date()
        event_date = self._extract_date(normalized, current)
        if event_date is None:
            return "Qual data você quer consultar? Pode dizer, por exemplo, 'ontem' ou '18 de setembro'."

        events = self.memory.get_daily(event_date.strftime("%Y-%m-%d"))
        if not events:
            return f"Não encontrei eventos registrados em {self._format_date(event_date)}."

        lines = [f"Em {self._format_date(event_date)}, encontrei:"]
        for event in events:
            if not isinstance(event, dict):
                continue
            role = event.get("role", "sistema")
            content = str(event.get("content", "")).strip()
            if content:
                lines.append(f"- {role}: {content}")
        return "\n".join(lines) if len(lines) > 1 else f"Não encontrei eventos registrados em {self._format_date(event_date)}."

    @staticmethod
    def _normalize(text):
        text = str(text or "").lower().strip()
        text = re.sub(r"[!?;,]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _is_daily_question(text):
        return any(rule.search(text) for rule in _INTENT_RULES)

    @staticmethod
    def _extract_date(text, current):
        for pattern, resolver in _DATE_PATTERNS:
            if pattern.search(text):
                return resolver(current)

        numeric = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?\b", text)
        if numeric:
            day, month, year = (int(value) if value else None for value in numeric.groups())
            try:
                return date(year or current.year, month, day)
            except ValueError:
                return None

        named = re.search(
            r"\b(?:dia\s+)?(\d{1,2})\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?\b",
            text,
        )
        if named:
            day = int(named.group(1))
            month = _MONTHS.get(named.group(2).replace("ç", "c"))
            year = int(named.group(3)) if named.group(3) else current.year
            if month:
                try:
                    return date(year, month, day)
                except ValueError:
                    return None

        day_only = re.search(r"\bdia\s+(\d{1,2})\b", text)
        if day_only:
            try:
                return date(current.year, current.month, int(day_only.group(1)))
            except ValueError:
                return None
        return None

    @staticmethod
    def _format_date(value):
        return value.strftime("%d/%m/%Y")
