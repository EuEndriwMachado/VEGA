# -*- coding: utf-8 -*-
"""Testes do fluxo de música da VEGA: tocar música/playlist específica
no Spotify, YouTube Music e Deezer, por voz.

Cobrem extração do pedido, roteamento por plataforma, correção de
transcrição ("spotfy" → spotify) e que o genérico play/pause não captura
o pedido específico. Sem rede: os comandos de abertura são mockados.
"""

import pytest

from core.actions.commands_parser import VegaCommandsParser
from core.actions.commands import VegaCommands
from core.brain.text_utils import normalize_text


# ==============================================================
# EXTRAÇÃO DO PEDIDO
# ==============================================================

class TestExtracaoMusica:
    @pytest.mark.parametrize("utterance,expected", [
        ("toca a musica radio ga ga", ("radio ga ga", "youtube", False)),
        ("toca a musica radio ga ga no spotify", ("radio ga ga", "spotify", False)),
        ("toca a playlist meus funk no spotify", ("meus funk", "spotify", True)),
        ("poe a playlist estudo lofi", ("estudo lofi", "youtube", True)),
        ("coloca a musica all star por nando reis", ("all star por nando reis", "youtube", False)),
        ("toca a musica e tals no youtube", ("e tals", "youtube", False)),
        ("solta a playlist foco", ("foco", "youtube", True)),
        ("manda a musica algo mais no deezer", ("algo mais", "deezer", False)),
    ])
    def test_pedidos_validos(self, utterance, expected):
        assert VegaCommandsParser._extract_music_request(normalize_text(utterance)) == expected

    @pytest.mark.parametrize("utterance", [
        "toca musica",           # genérico → play/pause de mídia
        "toca uma musica",       # genérico
        "play", "pause",         # mídia
        "pausa a musica",        # mídia
        "abre o spotify",        # abrir app
        "pesquisa no youtube por radio ga ga",  # pesquisa
        "procura no google bolo",  # pesquisa
        "",                      # vazio
    ])
    def test_nao_e_pedido_de_musica(self, utterance):
        assert VegaCommandsParser._extract_music_request(normalize_text(utterance)) is None

    def test_transcricao_errada_ainda_toca(self):
        """'spotfy' é corrigido para spotify antes do parser."""
        normalized = normalize_text("toca a playlist lofi no spotfy")
        request = VegaCommandsParser._extract_music_request(normalized)
        assert request == ("lofi", "spotify", True)

    def test_com_prefixo_vega(self):
        normalized = normalize_text("vega toca a musica sempre viva")
        request = VegaCommandsParser._extract_music_request(normalized)
        assert request is not None and request[0] == "sempre viva"


# ==============================================================
# ROTEAMENTO POR PLATAFORMA (mockado, sem abrir nada)
# ==============================================================

class TestRoteamentoPlataforma:
    def test_youtube_abe_url_de_busca_music(self, monkeypatch):
        opened = {}
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: opened.setdefault("url", url),
        )
        response = VegaCommands.play_music_youtube("radio ga ga")
        assert "radio ga ga" in response.lower() or "radio" in response.lower()
        assert "music.youtube.com" in opened["url"]

    def test_spotify_windows_usa_deeplink(self, monkeypatch):
        opened = {}

        class FakeOS:
            @staticmethod
            def startfile(target):
                opened["target"] = target

        monkeypatch.setattr("core.actions.commands.os", FakeOS)
        monkeypatch.setattr(VegaCommands, "_is_windows", staticmethod(lambda: True))
        monkeypatch.setattr(VegaCommands, "media_play_pause", staticmethod(lambda: None))
        monkeypatch.setattr("core.actions.commands.time.sleep", lambda s: None)

        response = VegaCommands.play_music_spotify("radio ga ga")
        assert opened["target"].startswith("spotify:search:")
        assert "Tocando" in response

    def test_spotify_sem_windows_abre_web(self, monkeypatch):
        opened = {}
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: opened.setdefault("url", url),
        )
        monkeypatch.setattr(VegaCommands, "_is_windows", staticmethod(lambda: False))
        response = VegaCommands.play_music_spotify("lofi")
        assert "open.spotify.com" in opened["url"]

    def test_deezer_abre_busca(self, monkeypatch):
        opened = {}
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: opened.setdefault("url", url),
        )
        response = VegaCommands.play_music("evidencias", platform="deezer")
        assert "deezer.com/search" in opened["url"]

    def test_playlist_e_indicado_na_resposta(self, monkeypatch):
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: None,
        )
        response = VegaCommands.play_music_youtube("academia", playlist=True)
        assert "playlist" in response.lower()

    def test_query_vazia_ped_de_esclarecimento(self):
        assert "Qual música" in VegaCommands.play_music_youtube("")

    def test_rota_padrao_e_youtube(self, monkeypatch):
        opened = {}
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: opened.setdefault("url", url),
        )
        VegaCommands.play_music("bohemian rhapsody")
        assert "music.youtube.com" in opened["url"]


# ==============================================================
# INTEGRAÇÃO COM O PARSER COMPLETO
# ==============================================================

class TestParserIntegrado:
    def _parser(self, monkeypatch):
        """Parser com instância real de commands (não só a classe)."""
        return VegaCommandsParser(VegaCommands())

    def test_parse_toca_musica_especifica(self, monkeypatch):
        opened = {}
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: opened.setdefault("url", url),
        )
        parser = self._parser(monkeypatch)
        response = parser.parse("toca a musica always gonna be there")
        assert response is not None
        assert "music.youtube.com" in opened["url"]

    def test_parse_playlist_spotify(self, monkeypatch):
        opened = {}
        monkeypatch.setattr("core.actions.commands.os", type("O", (), {"startfile": staticmethod(lambda t: opened.setdefault("t", t))}))
        monkeypatch.setattr(VegaCommands, "_is_windows", staticmethod(lambda: True))
        monkeypatch.setattr(VegaCommands, "media_play_pause", staticmethod(lambda: None))
        monkeypatch.setattr("core.actions.commands.time.sleep", lambda s: None)

        parser = self._parser(monkeypatch)
        response = parser.parse("toca a playlist treino no spotify")
        assert response is not None
        assert opened["t"].startswith("spotify:search:")

    def test_play_generico_continua_como_midia(self, monkeypatch):
        called = {"media": False}
        monkeypatch.setattr(VegaCommands, "media_play_pause", staticmethod(lambda: called.__setitem__("media", True)))
        monkeypatch.setattr("webbrowser.open", lambda url: None)

        parser = self._parser(monkeypatch)
        parser.parse("play")
        assert called["media"]

    def test_pesquisa_youtube_nao_vira_musica(self, monkeypatch):
        opened = {}
        monkeypatch.setattr(
            "webbrowser.open",
            lambda url: opened.setdefault("url", url),
        )
        parser = self._parser(monkeypatch)
        parser.parse("pesquisa no youtube por radio ga ga")
        # Pesquisa abre a página de RESULTADOS, não o YouTube Music.
        assert "youtube.com/results" in opened.get("url", "")
