# -*- coding: utf-8 -*-
"""Testes de playlists pessoais da VEGA: guardar, listar e tocar por voz.

Cobrem extração no parser, persistência na memória, roteamento por
plataforma, URI exato do Spotify e degradação graciosa quando a playlist
não existe ou a memória está indisponível.
"""

import tempfile
import shutil

import pytest

from core.actions.commands import VegaCommands
from core.actions.commands_parser import VegaCommandsParser
from core.actions.music_playlist import PersonalPlaylistService
from core.brain.text_utils import normalize_text
from core.memory.memory import VegaMemory


# ==============================================================
# FIXTURES
# ==============================================================

@pytest.fixture
def memory():
    path = tempfile.mkdtemp()
    yield VegaMemory(base_path=path)
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def commands(memory):
    return VegaCommands(memory=memory)


@pytest.fixture
def parser(commands):
    return VegaCommandsParser(commands)


@pytest.fixture
def no_open(monkeypatch):
    """Impede qualquer abertura real de app/navegador."""
    opened = {}
    monkeypatch.setattr("webbrowser.open", lambda url: opened.setdefault("url", url))
    monkeypatch.setattr(
        "core.actions.music_playlist.os",
        type("O", (), {"startfile": staticmethod(lambda t: opened.setdefault("target", t))}),
    )
    monkeypatch.setattr(
        "core.actions.music_playlist.PersonalPlaylistService._ensure_playback",
        staticmethod(lambda: None),
    )
    return opened


# ==============================================================
# EXTRAÇÃO NO PARSER
# ==============================================================

class TestExtracao:
    @pytest.mark.parametrize("utterance,expected", [
        ("toca minha playlist de funk", ("funk", "youtube")),
        ("toca a minha playlist treino no spotify", ("treino", "spotify")),
        ("coloca minha playlist lofi", ("lofi", "youtube")),
        ("solta a minha playlist rock", ("rock", "youtube")),
        ("toca minhas playlists favoritas", ("favoritas", "youtube")),
    ])
    def test_pedidos_de_playlist_pessoal(self, utterance, expected):
        assert VegaCommandsParser._extract_personal_playlist(normalize_text(utterance)) == expected

    def test_playlist_sem_nome(self):
        result = VegaCommandsParser._extract_personal_playlist(normalize_text("toca minha playlist"))
        assert result == ("", "youtube")

    @pytest.mark.parametrize("utterance", [
        "toca a musica radio ga ga",      # música normal, não playlist pessoal
        "toca a playlist academia",        # playlist por nome, não "minha"
        "abre o spotify",
        "play",
    ])
    def test_nao_e_playlist_pessoal(self, utterance):
        assert VegaCommandsParser._extract_personal_playlist(normalize_text(utterance)) is None

    def test_salvar_playlist(self):
        assert VegaCommandsParser._extract_save_playlist(
            normalize_text("guarda essa playlist trabalho no spotify")
        ) == ("trabalho", "spotify")

    def test_salvar_playlist_sem_plataforma(self):
        assert VegaCommandsParser._extract_save_playlist(
            normalize_text("salva essa playlist estudo")
        ) == ("estudo", "youtube")

    def test_listar_playlists(self):
        assert VegaCommandsParser._extract_list_playlists(normalize_text("quais sao minhas playlists"))
        assert VegaCommandsParser._extract_list_playlists(normalize_text("me mostra minhas playlists"))
        assert not VegaCommandsParser._extract_list_playlists(normalize_text("toca minha playlist funk"))


# ==============================================================
# PERSISTÊNCIA NA MEMÓRIA
# ==============================================================

class TestMemoriaPlaylists:
    def test_salvar_e_recuperar(self, memory):
        assert memory.save_playlist("trabalho", "spotify:playlist:ABC", platform="spotify")
        entry = memory.get_playlist("trabalho")
        assert entry["name"] == "trabalho"
        assert entry["target"] == "spotify:playlist:ABC"
        assert entry["platform"] == "spotify"

    def test_salvar_sem_target_e_permitido(self, memory):
        """Bug corrigido: salvar só com o nome deve funcionar."""
        assert memory.save_playlist("estudo", platform="youtube")
        entry = memory.get_playlist("estudo")
        assert entry is not None
        assert entry["target"] == ""

    def test_salvar_sem_nome_falha(self, memory):
        assert memory.save_playlist("", "spotify:playlist:ABC") is False

    def test_busca_aproximada(self, memory):
        memory.save_playlist("playlist de treino", platform="spotify")
        assert memory.get_playlist("treino") is not None

    def test_listar(self, memory):
        memory.save_playlist("a")
        memory.save_playlist("b")
        assert len(memory.list_playlists()) == 2

    def test_remover(self, memory):
        memory.save_playlist("temporaria")
        assert memory.remove_playlist("temporaria")
        assert memory.get_playlist("temporaria") is None

    def test_playlists_persistem_entre_instancias(self):
        path = tempfile.mkdtemp()
        try:
            VegaMemory(base_path=path).save_playlist("fixa", "spotify:playlist:XYZ")
            assert VegaMemory(base_path=path).get_playlist("fixa")["target"] == "spotify:playlist:XYZ"
        finally:
            shutil.rmtree(path, ignore_errors=True)


# ==============================================================
# REPRODUÇÃO
# ==============================================================

class TestReproducao:
    def test_uri_spotify_toca_exato(self, memory, commands, no_open):
        memory.save_playlist("treino", "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", platform="spotify")
        response = commands.play_personal_playlist("treino")
        assert no_open["target"] == "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        assert "treino" in response

    def test_url_direta_e_aberta(self, memory, commands, no_open):
        memory.save_playlist("lofi", "https://music.youtube.com/playlist?list=ABC", platform="youtube")
        response = commands.play_personal_playlist("lofi")
        assert no_open["url"].startswith("https://music.youtube.com/playlist")
        assert "lofi" in response

    def test_sem_uri_cai_na_busca(self, memory, commands, no_open):
        memory.save_playlist("trabalho", platform="youtube")
        commands.play_personal_playlist("trabalho")
        assert "music.youtube.com/search" in no_open["url"]

    def test_playlist_nao_salva_avisa(self, commands, no_open):
        response = commands.play_personal_playlist("inexistente")
        assert "não tenho" in response.lower() or "nao tenho" in response.lower()
        # Mesmo assim tenta tocar por busca.
        assert "music.youtube.com" in no_open.get("url", "")

    def test_nome_vazio_pede_esclarecimento(self, commands):
        assert "Qual playlist" in commands.play_personal_playlist("")

    def test_plataforma_spotify_sem_uri(self, commands, no_open):
        commands.play_personal_playlist("rock", platform="spotify")
        assert no_open["target"].startswith("spotify:search:")


# ==============================================================
# INTEGRAÇÃO PARSER -> COMANDOS -> MEMÓRIA
# ==============================================================

class TestIntegracao:
    def test_ciclo_completo_salvar_listar_tocar(self, parser, memory, no_open):
        saved = parser.parse("guarda essa playlist trabalho no spotify")
        assert "trabalho" in saved

        listed = parser.parse("quais sao minhas playlists")
        assert "trabalho" in listed

        memory.save_playlist("trabalho", "spotify:playlist:WORK", platform="spotify")
        played = parser.parse("toca minha playlist trabalho")
        assert no_open["target"] == "spotify:playlist:WORK"
        assert "trabalho" in played

    def test_salvar_reporta_falha_de_memoria(self):
        """Se a memória falhar, o usuário precisa saber (não fingir sucesso)."""
        broken = VegaCommands(memory=None)
        response = broken.save_personal_playlist("teste")
        assert "não consigo" in response.lower() or "nao consigo" in response.lower()

    def test_memoria_que_retorna_false_e_reportada(self):
        class RejectingMemory:
            def save_playlist(self, *_args, **_kwargs):
                return False

        service = PersonalPlaylistService(memory=RejectingMemory())
        response = service.save("teste")
        assert "não consegui" in response.lower() or "nao consegui" in response.lower()

    def test_lista_vazia_orienta_o_usuario(self, commands):
        response = commands.list_personal_playlists()
        assert "guarda essa playlist" in response.lower()

    def test_playlist_pessoal_nao_salva_ensina_a_guardar(self, parser, no_open):
        """'toca minha playlist X' passa pelo fluxo pessoal, não pelo genérico.

        A URL é a mesma busca de fallback por design; o que distingue os
        fluxos é a mensagem: o fluxo pessoal avisa que a playlist não está
        guardada e ensina o comando para fixar.
        """
        response = parser.parse("toca minha playlist foco")
        assert "ainda não tenho" in response.lower() or "ainda nao tenho" in response.lower()
        assert "guarda essa playlist" in response.lower()
        assert "music.youtube.com" in no_open.get("url", "")

    def test_playlist_por_nome_nao_ensina_a_guardar(self, parser, no_open):
        """'toca a playlist X' é busca comum: não menciona guardar.

        A fixture no_open é usada por efeito colateral (impede abrir o
        navegador de verdade); não é referenciada no corpo do teste.
        """
        response = parser.parse("toca a playlist academia")
        assert "guarda essa playlist" not in response.lower()
        assert "Tocando a playlist" in response

    def test_musica_normal_nao_menciona_playlist_pessoal(self, parser, no_open):
        """'toca a musica X' não deve ser capturado pelo fluxo de playlist."""
        response = parser.parse("toca a musica radio ga ga")
        assert "Tocando a música" in response
        assert "minha playlist" not in response.lower()