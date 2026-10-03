"""Respostas de hora e data em português, extraídas de `core.brain.brain`."""

import re
from datetime import datetime


_MONTHS = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def portuguese_number(value):
    """Converte um inteiro em palavras em português (0-59 é o uso real)."""

    if value < 0:
        return "menos " + portuguese_number(-value)

    unidades = [
        "zero", "um", "dois", "três", "quatro",
        "cinco", "seis", "sete", "oito", "nove",
    ]
    dezenas = ["", "", "vinte", "trinta", "quarenta", "cinquenta"]

    if value < 10:
        return unidades[value]

    if value < 20:
        names = [
            "dez", "onze", "doze", "treze", "quatorze",
            "quinze", "dezesseis", "dezessete", "dezoito", "dezenove",
        ]
        return names[value - 10]

    tens, unit = divmod(value, 10)
    if unit == 0:
        return dezenas[tens]
    return f"{dezenas[tens]} e {unidades[unit]}"


def answer_time_or_date_question(text, now=None):
    """Responde perguntas de hora/data, ou None se o texto não for uma delas."""

    lowered = text.lower().strip()
    if not lowered:
        return None

    normalized = re.sub(r"[^a-z0-9\sáàãâéêíóõôúç]", " ", lowered)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    has_time_word = bool(re.search(r"\b(hora|horas|hotas|hota)\b", normalized))
    has_date_word = bool(re.search(r"\b(dia|data|hoje)\b", normalized))
    has_question_word = bool(re.search(r"\b(que|qual|me diga|diga|pra mim|agora)\b", normalized))

    if not has_question_word and not (has_time_word or has_date_word):
        return None

    if not (has_time_word or has_date_word):
        return None

    now = now or datetime.now()

    hour_words = portuguese_number(now.hour)
    minute_words = portuguese_number(now.minute)

    if now.hour == 1:
        hour_phrase = "uma hora"
    else:
        hour_phrase = f"{hour_words} horas"

    minute_phrase = f"{minute_words} minutos"
    if now.minute == 1:
        minute_phrase = "um minuto"
    elif now.minute == 0:
        minute_phrase = "zero minutos"

    time_sentence = f"Agora são {hour_phrase} e {minute_phrase}."
    date_sentence = f"Hoje é dia {now.day} de {_MONTHS[now.month - 1]} de {now.year}."

    if has_time_word and has_date_word:
        return f"{time_sentence} {date_sentence}"

    if has_time_word:
        return time_sentence

    return date_sentence
