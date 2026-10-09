# -*- coding: utf-8 -*-
"""Separação de números ditados (PIN falado por voz).

O reconhecimento de fala costuma colar as palavras: "umdoistres" em vez de
"um dois três". Sem separar, o PIN ditado nunca era reconhecido e a
autorização de segurança ficava impossível por voz.
"""

import pytest

from core.brain.number_words import separate_glued_digits


class TestSeparacaoDeNumeros:
    @pytest.mark.parametrize("entrada,esperado", [
        ("umdoistresquatro", "um dois tres quatro"),
        ("um dois tres quatro", "um dois tres quatro"),
        ("um, dois, três, quatro", "um, dois, três, quatro"),
        ("senha umdoiszero", "senha um dois zero"),
        ("quatrocincoseis", "quatro cinco seis"),
    ])
    def test_separa_numeros_colados(self, entrada, esperado):
        assert separate_glued_digits(entrada) == esperado

    @pytest.mark.parametrize("nome", [
        "cassia", "miguel", "rita", "responde", "whatsapp", "vega",
    ])
    def test_preserva_nomes_comuns(self, nome):
        # Nomes não podem ser mutilados por parecerem números.
        assert separate_glued_digits(nome) == nome

    def test_texto_vazio_nao_quebra(self):
        assert separate_glued_digits("") == ""
        assert separate_glued_digits(None) == ""

    def test_e_idempotente(self):
        uma_vez = separate_glued_digits("umdoistres")
        assert separate_glued_digits(uma_vez) == uma_vez

    def test_nao_separa_palavra_de_numero_sozinha(self):
        # "dois" já é uma palavra válida: não vira "do is".
        assert separate_glued_digits("dois") == "dois"