# -*- coding: utf-8 -*-
"""Regras de APLICATIVOS, PASTAS, BUSCA e SITES.

Substituem os antigos blocos de `if` que abriam programas, pastas do usuário,
pesquisavam no YouTube/Google e abriam sites específicos.

A ordem preserva a intenção original: pesquisa nomeada antes de "abrir site",
pastas e apps específicos antes do `open_app` genérico (que é a última
tentativa, quando nada mais casou).
"""

from __future__ import annotations

from core.actions.intent_catalog import (
    PRIORITY_APPS,
    PRIORITY_SEARCH,
    Rule,
)


# Sites reconhecidos explicitamente, com o executor correspondente.
_KNOWN_SITES = (
    ("google", ("google", "navegador google"), "open_google"),
    ("youtube", ("youtube",), "open_youtube"),
    ("canva", ("canva",), "open_canva"),
    ("whatsapp", ("whatsapp", "whatsapp web"), "open_whatsapp"),
    ("spotify", ("spotify",), "open_spotify"),
)

# Pastas do usuário: nome reconhecido -> executor.
_USER_FOLDERS = (
    ("documents", ("documentos", "meus documentos"), "open_documents"),
    ("downloads", ("downloads", "meus downloads"), "open_downloads"),
    ("desktop", ("desktop", "área de trabalho"), "open_desktop"),
)

# Nomes de app que têm executor dedicado em vez do open_app genérico.
_APP_ALIASES = {
    "google": "open_google",
    "youtube": "open_youtube",
    "spotify": "open_spotify",
    "canva": "open_canva",
    "whatsapp": "open_whatsapp",
    "whatsapp web": "open_whatsapp",
    "documentos": "open_documents",
    "meus documentos": "open_documents",
    "downloads": "open_downloads",
    "meus downloads": "open_downloads",
    "desktop": "open_desktop",
    "área de trabalho": "open_desktop",
}


def build_app_rules(parser) -> list:
    """Monta as regras de apps, pastas, sites e busca."""

    rules = []

    # ------------------------------------------------------------------
    # FUNK — pedido específico de playlist, antes da busca genérica.
    # ------------------------------------------------------------------

    rules.append(Rule(
        name="play_funk",
        domain="midia",
        priority=PRIORITY_SEARCH - 1,
        resolver=lambda ctx: bool(parser._is_funk_request(ctx.text)),
        handler=lambda ctx: ctx.call("play_funk"),
    ))

    # ------------------------------------------------------------------
    # PESQUISA NOMEADA — YouTube antes de Google.
    # ------------------------------------------------------------------

    def search_youtube(context):
        query = parser._extract_search_query(context.text, "youtube")
        if not query:
            return None
        return context.call("search_youtube", query)

    rules.append(Rule(
        name="search_youtube",
        domain="busca",
        priority=PRIORITY_SEARCH,
        resolver=lambda ctx: bool(parser._extract_search_query(ctx.text, "youtube")),
        handler=search_youtube,
    ))

    def search_google(context):
        query = parser._extract_google_query(context.text)
        if not query:
            return None
        return context.call("search_google", query)

    rules.append(Rule(
        name="search_google",
        domain="busca",
        priority=PRIORITY_SEARCH,
        resolver=lambda ctx: bool(parser._extract_google_query(ctx.text)),
        handler=search_google,
    ))

    # ------------------------------------------------------------------
    # ABRIR SITE EXPLÍCITO — "abre o youtube", "abre o whatsapp web".
    # ------------------------------------------------------------------

    for site_key, aliases, action in _KNOWN_SITES:
        rules.append(Rule(
            name=f"open_site_{site_key}",
            domain="apps",
            priority=PRIORITY_APPS,
            resolver=(lambda names: lambda ctx: bool(parser._is_open_request(ctx.text, names)))(aliases),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))

    # ------------------------------------------------------------------
    # PASTAS DO USUÁRIO — exigem ação de abertura ("abre downloads").
    # ------------------------------------------------------------------

    for folder_key, aliases, action in _USER_FOLDERS:
        rules.append(Rule(
            name=f"open_folder_{folder_key}",
            domain="apps",
            priority=PRIORITY_APPS,
            resolver=(
                lambda names: lambda ctx: (
                    ctx.contains(*names) and parser._contains_open_action(ctx.text)
                )
            )(aliases),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))

    # ------------------------------------------------------------------
    # ABRIR APLICATIVO GENÉRICO — última tentativa.
    # Se o alvo tiver executor dedicado (site/pasta), usa o dedicado; senão,
    # cai no open_app, que resolve pelo menu Iniciar e pelos executáveis.
    # ------------------------------------------------------------------

    def open_named_app(context):
        app_name = parser._extract_app_name(context.text)
        if not app_name:
            return None
        if parser._normalize_app_name is not None:
            app_name = parser._normalize_app_name(app_name)

        dedicated = _APP_ALIASES.get(app_name)
        if dedicated:
            return context.call(dedicated)

        return context.call("open_app", app_name)

    rules.append(Rule(
        name="open_named_app",
        domain="apps",
        priority=PRIORITY_APPS,
        resolver=lambda ctx: bool(parser._extract_app_name(ctx.text)),
        handler=open_named_app,
    ))

    return rules