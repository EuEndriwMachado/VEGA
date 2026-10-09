# -*- coding: utf-8 -*-
"""Testes do modo cognitivo.

A garantia que estes testes protegem: o PADRÃO é `legacy`. Se alguém mudar o
default sem querer, a VEGA passa a falar diferente para todos os usuários de
uma vez — e este arquivo acusa antes.
"""

import importlib

import pytest

import config.cognitive as cognitive


def _reload_with_mode(monkeypatch, value):
    """Recarrega o módulo com VEGA_COGNITIVE_MODE definido (ou removido)."""
    if value is None:
        monkeypatch.delenv("VEGA_COGNITIVE_MODE", raising=False)
    else:
        monkeypatch.setenv("VEGA_COGNITIVE_MODE", value)
    return importlib.reload(cognitive)


@pytest.fixture(autouse=True)
def _restore_module():
    """Garante que o reload de um teste não vaze para os outros."""
    yield
    importlib.reload(cognitive)


class TestModoPadrao:
    def test_sem_variavel_o_modo_e_legacy(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, None)
        assert module.COGNITIVE_MODE == module.LEGACY
        assert module.mode_is_generated() is False

    def test_modo_generated_liga_a_fala_gerada(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, "generated")
        assert module.COGNITIVE_MODE == module.GENERATED
        assert module.mode_is_generated() is True
    def test_modo_pode_mudar_em_runtime_sem_reload(self, monkeypatch):
        _reload_with_mode(monkeypatch, None)
        monkeypatch.setenv("VEGA_COGNITIVE_MODE", "generated")
        assert cognitive.mode_is_generated() is True
        monkeypatch.setenv("VEGA_COGNITIVE_MODE", "legacy")
        assert cognitive.mode_is_generated() is False

    def test_modo_e_case_insensitive(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, "GENERATED")
        assert module.mode_is_generated() is True


class TestValorInvalido:
    def test_valor_desconhecido_cai_no_legacy_sem_estourar(self, monkeypatch, capsys):
        module = _reload_with_mode(monkeypatch, "turbo")
        assert module.COGNITIVE_MODE == module.LEGACY
        # O aviso importa: um typo no .env silencioso faria o usuário achar que
        # ligou o modo novo quando não ligou.
        assert "turbo" in capsys.readouterr().out

    def test_espacos_sao_ignorados(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, "  generated  ")
        assert module.mode_is_generated() is True

    def test_string_vazia_e_legacy(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, "   ")
        assert module.COGNITIVE_MODE == module.LEGACY


class TestContextoParaAIA:
    def test_no_legacy_nao_injeta_nada_no_prompt(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, None)
        assert module.build_cognitive_context() == ""

    def test_no_generated_ensina_a_narrar_sem_inventar(self, monkeypatch):
        module = _reload_with_mode(monkeypatch, "generated")
        context = module.build_cognitive_context()
        assert context
        # As duas regras que impedem a IA de alucinar o resultado da ação.
        assert "NUNCA invente" in context
        assert "falhou" in context.lower()

    def test_descricao_existe_para_os_dois_modos(self, monkeypatch):
        for mode in ("legacy", "generated"):
            module = _reload_with_mode(monkeypatch, mode)
            assert module.describe_cognitive_mode()
