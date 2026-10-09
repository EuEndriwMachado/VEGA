# -*- coding: utf-8 -*-
"""Comandos específicos da TV: spotify, youtube, ligar, teclas."""
import pytest

from core.actions.commands_parser import VegaCommandsParser


class _StubCommands:
    def __getattr__(self, name):
        def neutral(*args, **kwargs):
            return "stub"
        return neutral


@pytest.fixture
def parser():
    return VegaCommandsParser(_StubCommands())


# ---- Spotify da TV ----

def test_toca_musica_no_spotify_da_tv(parser):
    action, kwargs = parser.interpret("toca menos é mais no spotify da tv")[0:2]
    matched = parser.interpret("toca menos é mais no spotify da tv")[0]
    assert matched


def test_extract_spotify_query():
    text = "toca menos é mais no spotify da tv"
    query = VegaCommandsParser._extract_tv_spotify_query(text)
    assert query == "menos é mais"


def test_extract_spotify_query_sem_da_tv():
    text = "toca lofi no spotify"
    query = VegaCommandsParser._extract_tv_spotify_query(text)
    assert query == "lofi"


# ---- YouTube da TV ----

def test_pesquisa_eleicoes_youtube_da_tv(parser):
    matched, _ = parser.interpret("pesquisa por eleições 2026 no youtube da tv")
    assert matched


# ---- Ligar a TV ----

def test_liga_a_tv(parser):
    matched, response = parser.interpret("liga a tv")
    assert matched


def test_resolve_wake_on_direct():
    result = VegaCommandsParser._resolve_connectivity_request("liga a tv")
    assert result == ("wake_on", {})


def test_resolve_play_spotify_direct():
    result = VegaCommandsParser._resolve_connectivity_request(
        "toca menos é mais no spotify da tv"
    )
    assert result == ("play_spotify", {"query": "menos é mais"})


def test_resolve_youtube_search_direct():
    result = VegaCommandsParser._resolve_connectivity_request(
        "pesquisa por eleições 2026 no youtube da tv"
    )
    assert result[0] == "search_youtube"


# ---- Teclas ----

def test_aperte_enter_na_tv():
    result = VegaCommandsParser._resolve_connectivity_request("aperte enter na tv")
    assert result == ("key", {"key": "enter"})


def test_volta_na_tv():
    result = VegaCommandsParser._resolve_connectivity_request("volta na tv")
    # "volta" pode ser retomar mídia OU tecla RETURN; deve ser um dos dois.
    assert result is not None and result[0] in ("resume", "key")
