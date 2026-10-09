# -*- coding: utf-8 -*-
"""Regressões do fluxo de PIN: um PIN numérico não pode ser descartado.

Cenário do bug (print do usuário): a VEGA pedia o PIN para usar o celular,
o usuário ditava "5679" e a VEGA respondia "Estou ouvindo." sem liberar nada,
porque o texto numérico era classificado como ruído de transcrição antes de
chegar ao portão de segurança.
"""
from core.brain.text_utils import is_short_nonsense


def test_pin_numerico_nao_e_ruido():
    assert is_short_nonsense("5679") is False
    assert is_short_nonsense("1234") is False
    assert is_short_nonsense("0000") is False


def test_ruido_real_continua_sendo_descartado():
    assert is_short_nonsense("zz") is True
    assert is_short_nonsense("!!!") is True
    assert is_short_nonsense("") is False