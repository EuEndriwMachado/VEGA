# -*- coding: utf-8 -*-
"""Separação de números ditados por voz.

O reconhecimento de fala costuma escrever o PIN ditado de três formas:

    "um dois três quatro"    -> separado (o caso fácil)
    "umdoistresquatro"       -> tudo colado numa palavra só
    "um, dois, três, quatro" -> separado por pontuação

Sem separar as palavras coladas, o PIN ditado nunca era reconhecido e a
autorização de segurança ficava impossível por voz. Este módulo resolve o
terceiro caso (e não interfere nos outros dois).
"""

from __future__ import annotations

import re

# Ordem decrescente de tamanho: garante que "dezessete" seja lido antes de
# "dez" e "dezenove" antes de "nove".
_MAX_WORD_LENGTH = 10


def separate_glued_digits(text, number_words=None):
    """Separa palavras de número coladas: "umdoistres" -> "um dois tres".

    - mantém pontuação e espaços como estão;
    - só quebra quando os pedaços formam, de fato, palavras de número;
    - é idempotente: texto já separado não muda.
    """
    words = number_words or _DEFAULT_NUMBER_WORDS
    if not text:
        return text or ""

    # Só tenta separar tokens longos: os curtos já são palavras válidas.
    longest = max((len(word) for word in words), default=1)
    max_length = max(_MAX_WORD_LENGTH, longest)

    def split_token(token):
        if len(token) <= 3 or token in words:
            return token
        pieces = _greedy_split(token, words, max_length)
        return " ".join(pieces) if pieces else token

    return re.sub(
        r"[a-záàãâéêíóõôúç]+",
        lambda match: split_token(match.group(0)),
        text,
    )


def _greedy_split(token, words, max_length):
    """Quebra o token em palavras de número, da esquerda para a direita.

    Devolve None quando o token NÃO é um número colado — assim nomes comuns
    (ex.: "cassia", "miguel") são preservados intactos.
    """
    pieces = []
    index = 0
    length = len(token)

    while index < length:
        matched = ""
        for size in range(min(max_length, length - index), 0, -1):
            candidate = token[index:index + size]
            if candidate in words:
                matched = candidate
                break
        if not matched:
            return None
        pieces.append(matched)
        index += len(matched)

    # Exige pelo menos duas palavras: uma só não justifica reescrever o texto.
    return pieces if len(pieces) >= 2 else None


_DEFAULT_NUMBER_WORDS = {
    "zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2",
    "tres": "3", "três": "3", "quatro": "4", "cinco": "5",
    "seis": "6", "sete": "7", "oito": "8", "nove": "9",
    "dez": "10", "onze": "11", "doze": "12", "treze": "13",
    "quatorze": "14", "catorze": "14", "quinze": "15",
    "dezesseis": "16", "dezasseis": "16", "dezessete": "17",
    "dezoito": "18", "dezenove": "19",
    "vinte": "20", "trinta": "30", "quarenta": "40",
    "cinquenta": "50", "sessenta": "60", "setenta": "70",
    "oitenta": "80", "noventa": "90", "cem": "100",
}