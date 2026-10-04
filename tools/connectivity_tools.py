# -*- coding: utf-8 -*-
"""Ferramentas de conectividade expostas ao modelo (celular e TV).

Permite que o Gemini solicite ações estruturadas como:
    tool: control_phone
    arguments: {"action": "battery"}
    tool: control_tv
    arguments: {"action": "play_url", "url": "..."}

A execução é sempre delegada ao `ConnectivityManager`, que valida e
degrada com mensagem clara. Nada aqui acessa o sistema diretamente.
"""

from __future__ import annotations


class ConnectivityTools:
    """Implementações das ferramentas `control_phone` e `control_tv`."""

    def __init__(self, manager=None):
        self._manager = manager

    # --------------------------------------------------------------
    # Gerenciador (preguiçoso: só cria quando a ferramenta é usada)
    # --------------------------------------------------------------

    @property
    def manager(self):
        if self._manager is None:
            try:
                from core.connectivity import ConnectivityManager
                self._manager = ConnectivityManager()
            except Exception as error:
                print(f"[VEGA] Conectividade indisponível: {error}")
                self._manager = False
        return self._manager

    def _run(self, action, kind, **kwargs):
        manager = self.manager
        if not manager:
            return "A camada de conectividade não está disponível agora."
        result = manager.route(action, kind=kind, **kwargs)
        return result.message or ("Feito." if result.ok else "Não consegui fazer isso.")

    # --------------------------------------------------------------
    # Ferramentas
    # --------------------------------------------------------------

    def control_phone(self, action: str = "list", app: str = "", path: str = "",
                      remote: str = "", device: str = ""):
        """Controla o celular conectado: listar, bateria, abrir app, print,
        enviar/receber arquivo, espelhar tela e controle de mídia."""
        kwargs = {}
        if app:
            kwargs["app"] = app
        if path:
            kwargs["path"] = path
        if remote:
            kwargs["remote"] = remote
        if device:
            kwargs["device"] = device
        return self._run(action, "phone", **kwargs)

    def control_tv(self, action: str = "list", url: str = "", level=None,
                   delta=None, tv: str = ""):
        """Controla a TV na rede: listar, enviar mídia, pausar, retomar,
        parar, ajustar volume e desligar."""
        kwargs = {}
        if url:
            kwargs["url"] = url
        if level is not None:
            kwargs["level"] = level
        if delta is not None:
            kwargs["delta"] = delta
        if tv:
            kwargs["tv"] = tv
        return self._run(action, "tv", **kwargs)


def register_connectivity_tools(manager, connectivity_tools=None):
    """Registra as ferramentas de conectividade no registry existente.

    Recebe o `ToolManager` já construído e adiciona duas ferramentas.
    Falhas no registro não derrubam a construção do registry.
    """
    from tools.api_manager import ToolSpec

    tools = connectivity_tools or ConnectivityTools()

    try:
        manager.register(ToolSpec(
            name="control_phone",
            purpose=(
                "Controla o celular Android conectado por USB: consultar bateria, "
                "abrir aplicativo, tirar print da tela, enviar arquivo do PC para o "
                "celular, trazer arquivo do celular, espelhar a tela e controlar a "
                "mídia (pausar, tocar, próxima)."
            ),
            category="conectividade",
            parameters=["action", "app", "path", "remote", "device"],
            tags=["celular", "telefone", "android", "smartphone", "bateria", "print"],
            implementation=tools.control_phone,
        ))
    except Exception as error:
        print(f"[VEGA] Não registrei control_phone: {error}")

    try:
        manager.register(ToolSpec(
            name="control_tv",
            purpose=(
                "Controla a TV na rede local: listar TVs, enviar mídia (URL de "
                "vídeo/áudio), pausar, retomar, parar, ajustar volume e desligar."
            ),
            category="conectividade",
            parameters=["action", "url", "level", "delta", "tv"],
            tags=["tv", "televisao", "chromecast", "cast", "sala"],
            implementation=tools.control_tv,
        ))
    except Exception as error:
        print(f"[VEGA] Não registrei control_tv: {error}")

    return manager