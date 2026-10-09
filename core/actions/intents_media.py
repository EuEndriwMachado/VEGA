# -*- coding: utf-8 -*-
"""Regras de MÍDIA, JANELAS e TECLADO.

Substituem os antigos blocos de `if` de play/pause, próxima/anterior, parar,
fechar janela, minimizar/maximizar/restaurar, alt-tab e atalhos de teclado.

A ordem preserva as regras que os comentários originais do parser exigiam:
mídia nomeada antes do play/pause genérico, e "fecha o programa X" antes do
fechar janela genérico (caso contrário, "fecha o Spotify" só fecharia a janela).
"""

from __future__ import annotations

from core.actions.intent_catalog import (
    PRIORITY_SPECIFIC,
    PRIORITY_SYSTEM,
    Rule,
)


def build_media_rules(parser, extract_close_app_name=None, normalize_app_name=None) -> list:
    """Monta as regras de mídia/janelas/teclado.

    `extract_close_app_name` e `normalize_app_name` recebem os extratores do
    parser, porque o fechamento nomeado precisa resolver o alvo antes de
    decidir se a regra se aplica.
    """

    def recognize(method_name):
        def resolver(context):
            method = getattr(parser, method_name, None)
            if not callable(method):
                return False
            return bool(method(context.text))

        return resolver

    rules = []

    # ------------------------------------------------------------------
    # FECHAR APLICATIVO NOMEADO — precisa vir ANTES do fechar janela genérico.
    # "fecha o programa Spotify" contém a frase genérica "fecha o programa",
    # mas tem alvo nomeado: deve encerrar o app, não só fechar a janela ativa.
    # ------------------------------------------------------------------

    def close_named_app(context):
        if extract_close_app_name is None:
            return None
        app_name = extract_close_app_name(context.text)
        if not app_name:
            return None
        if normalize_app_name is not None:
            app_name = normalize_app_name(app_name)
        if not app_name:
            return None
        return context.call("close_app", app_name)

    rules.append(Rule(
        name="close_named_app",
        domain="apps",
        priority=PRIORITY_SPECIFIC,
        resolver=lambda ctx: bool(extract_close_app_name and extract_close_app_name(ctx.text)),
        handler=close_named_app,
    ))

    # ------------------------------------------------------------------
    # MÍDIA NOMEADA — "toca a música X", playlist pessoal.
    # Vem antes do play/pause genérico: senão "toca musica X" seria comido
    # pelo "toca musica".
    # ------------------------------------------------------------------

    def playlist_play(context):
        extracted = parser._extract_personal_playlist(context.text)
        if extracted is None:
            return None
        name, platform = extracted
        return context.call("play_personal_playlist", name, platform=platform)

    rules.append(Rule(
        name="play_personal_playlist",
        domain="midia",
        priority=PRIORITY_SPECIFIC,
        resolver=lambda ctx: parser._extract_personal_playlist(ctx.text) is not None,
        handler=playlist_play,
    ))

    def playlist_save(context):
        extracted = parser._extract_save_playlist(context.text)
        if extracted is None:
            return None
        name, platform = extracted
        return context.call("save_personal_playlist", name, platform=platform)

    rules.append(Rule(
        name="save_personal_playlist",
        domain="midia",
        priority=PRIORITY_SPECIFIC,
        resolver=lambda ctx: parser._extract_save_playlist(ctx.text) is not None,
        handler=playlist_save,
    ))

    rules.append(Rule(
        name="list_personal_playlists",
        domain="midia",
        priority=PRIORITY_SPECIFIC,
        resolver=lambda ctx: bool(parser._extract_list_playlists(ctx.text)),
        handler=lambda ctx: ctx.call("list_personal_playlists"),
    ))

    def play_music(context):
        request = parser._extract_music_request(context.text)
        if not request:
            return None
        query, platform, playlist = request
        return context.call("play_music", query, platform=platform, playlist=playlist)

    rules.append(Rule(
        name="play_music",
        domain="midia",
        priority=PRIORITY_SPECIFIC,
        resolver=lambda ctx: bool(parser._extract_music_request(ctx.text)),
        handler=play_music,
    ))

    # ------------------------------------------------------------------
    # MÍDIA GENÉRICA — play/pause, próxima, anterior, parar.
    # ------------------------------------------------------------------

    # Pedido genérico de TOCAR música não é play/pause: sem faixa tocando, o
    # teclado de mídia não faz nada. Encaminha para play_music, que pergunta
    # qual música tocar quando nenhuma foi dita.
    rules.append(Rule(
        name="play_music_generic",
        domain="midia",
        priority=PRIORITY_SYSTEM,
        resolver=lambda ctx: ctx.equals(
            "toca musica", "tocar musica", "toque musica",
            "toca uma musica", "tocar uma musica", "toque uma musica",
            "toca uma música", "tocar uma música", "toque uma música",
        ),
        handler=lambda ctx: ctx.call("play_music", "", platform="youtube"),
    ))

    for name, recognizer, action in (
        ("media_play_pause", "_is_media_play_pause_request", "media_play_pause"),
        ("media_next", "_is_media_next_request", "media_next"),
        ("media_previous", "_is_media_previous_request", "media_previous"),
        ("media_stop", "_is_media_stop_request", "media_stop"),
    ):
        rules.append(Rule(
            name=name,
            domain="midia",
            priority=PRIORITY_SYSTEM,
            resolver=recognize(recognizer),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))

    # ------------------------------------------------------------------
    # JANELAS
    # ------------------------------------------------------------------

    for name, recognizer, action in (
        ("close_window", "_is_close_window_request", "close_window"),
        ("minimize_all", "_is_minimize_all_request", "minimize_all"),
        ("fullscreen", "_is_fullscreen_request", "fullscreen"),
        # "maximiza" e "restaura" antes de "minimiza": são alvos distintos,
        # mas a ordem fixa evita que uma frase ambígua caia no genérico.
        ("maximize_window", "_is_maximize_request", "maximize_window"),
        ("restore_window", "_is_restore_request", "restore_window"),
        ("minimize_window", "_is_minimize_request", "minimize_window"),
        ("switch_window", "_is_switch_window_request", "switch_window"),
    ):
        rules.append(Rule(
            name=name,
            domain="janelas",
            priority=PRIORITY_SYSTEM,
            resolver=recognize(recognizer),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))

    # ------------------------------------------------------------------
    # TECLADO
    # ------------------------------------------------------------------

    for name, recognizer, action in (
        ("select_all", "_is_select_all_request", "select_all"),
        ("copy", "_is_copy_request", "copy"),
        ("paste", "_is_paste_request", "paste"),
        ("cut", "_is_cut_request", "cut"),
        # "desfazer" antes de "refazer": a raiz é a mesma palavra, e a ordem
        # evita que "refaz" seja lido como "faz".
        ("undo", "_is_undo_request", "undo"),
        ("redo", "_is_redo_request", "redo"),
        ("save", "_is_save_request", "save"),
        ("new", "_is_new_request", "new"),
        ("alt_tab", "_is_alt_tab_request", "alt_tab"),
    ):
        rules.append(Rule(
            name=name,
            domain="teclado",
            priority=PRIORITY_SYSTEM,
            resolver=recognize(recognizer),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))

    return rules