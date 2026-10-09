# -*- coding: utf-8 -*-
"""Catálogo unificado de capacidades: agregação e seleção por relevância.

Garante:
  1. as duas camadas vivas (regras locais + ferramentas) entram no catálogo;
  2. o inventário mínimo preserva o formato que o roteador semântico consome;
  3. a seleção por relevância NÃO deixa o catálogo cego (o corte cego das
     primeiras 45 era um bug: a IA ficava sem ver metade das capacidades);
  4. uma fala ambígua nunca coloca ações de alto impacto (energia) no topo;
  5. o ranqueamento acerta o assunto (brilho, volume, clima, desenho).
"""

import pytest

from core.actions.commands import VegaCommands
from core.actions.commands_parser import VegaCommandsParser
from core.capabilities import Capability, CapabilityCatalog
from core.capabilities.catalog import KIND_COMMAND, KIND_TOOL, tokenize
from tools.api_tools import build_tool_registry


@pytest.fixture(scope="module")
def catalog():
    parser = VegaCommandsParser(VegaCommands())
    return CapabilityCatalog.build(
        commands_parser=parser,
        tool_manager=build_tool_registry(),
    )


def _top(catalog, text, limit=4):
    return [entry["name"] for entry in catalog.describe_relevant(text, limit=limit)]


# ------------------------------------------------------------------
# 1. Agregação das duas camadas
# ------------------------------------------------------------------

def test_catalog_aggregates_commands_and_tools(catalog):
    kinds = {capability.kind for capability in catalog.capabilities()}
    assert KIND_COMMAND in kinds
    assert KIND_TOOL in kinds


def test_catalog_names_are_unique(catalog):
    names = catalog.names()
    assert len(names) == len(set(names))


def test_catalog_contains_rules_and_tools(catalog):
    names = catalog.names()
    # Uma regra local conhecida...
    assert "brightness_up" in names
    # ...e uma ferramenta conhecida.
    assert "weather" in names


def test_inventory_keeps_router_format(catalog):
    """O roteador semântico consome {"name", "domain"} — deve continuar assim."""
    for entry in catalog.describe():
        assert set(entry) == {"name", "domain"}


def test_lookup_by_name_returns_capability(catalog):
    capability = catalog.get("weather")
    assert isinstance(capability, Capability)
    assert capability.kind == KIND_TOOL
    assert capability.description


def test_missing_layer_does_not_break():
    """Sem parser e sem ferramentas, o catálogo simplesmente fica vazio."""
    empty = CapabilityCatalog.build()
    assert empty.capabilities() == []
    assert empty.describe() == []


# ------------------------------------------------------------------
# 2. Seleção por relevância
# ------------------------------------------------------------------

def test_selection_respects_limit(catalog):
    selected = catalog.select_relevant("abre o chrome", limit=5)
    assert len(selected) == 5


def test_selection_covers_whole_catalog_not_just_the_head(catalog):
    """O fim do catálogo (prioridade alta) precisa poder ser escolhido.

    Com o corte cego antigo, essas capacidades nunca chegavam ao modelo.
    """
    assert len(catalog.capabilities()) > 45
    selected = catalog.select_relevant("aumenta o brilho da tela", limit=45)
    picked = {capability.name for capability in selected}
    assert "brightness_up" in picked


def test_selection_is_deterministic(catalog):
    first = _top(catalog, "faz um desenho de um foguete")
    second = _top(catalog, "faz um desenho de um foguete")
    assert first == second


@pytest.mark.parametrize("phrase,expected", [
    ("deixa o pc mais claro", "brightness"),
    ("abaixa o brilho da tela", "brightness"),
    ("aumenta o som", "volume"),
    ("silencia o computador", "volume"),
    ("qual a previsao do tempo em londres", "weather"),
    ("faz um desenho de um foguete", "draw_image"),
])
def test_selection_picks_the_right_subject(catalog, phrase, expected):
    picked = _top(catalog, phrase, limit=3)
    assert any(expected in name for name in picked), (phrase, picked)


@pytest.mark.parametrize("phrase", [
    "deixa o pc mais claro",
    "faz um desenho de um foguete",
    "qual a previsao do tempo",
])
def test_high_impact_actions_are_not_at_the_top(catalog, phrase):
    """Uma fala ambígua não pode oferecer desligar o PC como primeira opção."""
    assert _top(catalog, phrase, limit=1)[0] not in {"shutdown_pc", "restart_pc"}


def test_ambiguous_speech_still_returns_options(catalog):
    """Fala sem termos reconhecíveis nunca devolve lista vazia."""
    selected = catalog.describe_relevant("xyz abc", limit=10)
    assert len(selected) == 10


# ------------------------------------------------------------------
# 3. Tokenização (usada pela seleção)
# ------------------------------------------------------------------

def test_tokenize_drops_accents_and_stopwords():
    tokens = tokenize("Deixa o Pc mais CLARO")
    assert "pc" in tokens
    assert "claro" in tokens
    assert "o" not in tokens


def test_fold_normalizes_punctuation():
    from core.capabilities.catalog import fold

    assert fold("Aumenta, o VOLUME!") == "aumenta o volume"
