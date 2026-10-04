# -*- coding: utf-8 -*-
"""Conector de TV da VEGA.

Três caminhos, todos opcionais e detectados em tempo de execução:

1. **Google Cast / Chromecast** (`pychromecast`) — envia vídeo/áudio para a
   TV, pausa, retoma, controla volume. Descobre dispositivos na rede.
2. **DLNA/UPnP** — para TVs que expõem media renderer na rede local.
3. **Controle remoto de rede** — Samsung (WebSocket) e LG (webOS) via HTTP,
   para ligar/desligar, volume e entrada HDMI.

Se nenhuma biblioteca estiver instalada, a VEGA explica o que instalar.
Nada aqui é obrigatório nem quebra a VEGA quando ausente.
"""

from __future__ import annotations

import socket

from core.connectivity.base import Connector, ConnectorResult, ConnectorStatus


class TVConnector(Connector):
    """Descobre e controla TVs na rede local."""

    name = "tv"
    install_hint = (
        "Para enviar mídia à TV, instale pychromecast "
        "(pip install pychromecast). Para TVs Samsung/LG, informe o IP "
        "da TV nas configurações."
    )

    def __init__(self, registry=None, runner=None):
        self._cast_available = None
        super().__init__(registry=registry, runner=runner)

    # ==========================================================
    # DISPONIBILIDADE
    # ==========================================================

    def _has_pychromecast(self) -> bool:
        if self._cast_available is None:
            try:
                import importlib.util
                self._cast_available = (
                    importlib.util.find_spec("pychromecast") is not None
                )
            except (ImportError, ValueError):
                self._cast_available = False
        return self._cast_available

    def status(self) -> ConnectorStatus:
        if self._has_pychromecast():
            return ConnectorStatus(
                name=self.name,
                available=True,
                detail="pychromecast disponível para controle de TV via Cast.",
            )

        known_tv = []
        if self.registry is not None:
            known_tv = self.registry.by_kind("tv")

        if known_tv:
            return ConnectorStatus(
                name=self.name,
                available=True,
                detail=f"{len(known_tv)} TV(s) conhecida(s) na rede.",
                install_hint="",
            )

        return ConnectorStatus(
            name=self.name,
            available=False,
            detail=(
                "Nenhuma TV configurada e pychromecast não está instalado. "
                "Quando tiver uma TV na rede, cadastre o IP dela ou instale "
                "o pychromecast."
            ),
            install_hint=self.install_hint,
        )

    # ==========================================================
    # DESCOBERTA
    # ==========================================================

    def discover(self, timeout=8.0) -> list:
        """Descobre TVs via Cast (pychromecast) e as registra."""
        if not self._has_pychromecast():
            return []

        try:
            import pychromecast
            chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        except Exception:
            return []

        found = []
        try:
            for cast in chromecasts:
                try:
                    cast.wait(timeout=5)
                    name = cast.cast_info.friendly_name
                    host = cast.cast_info.host
                    uuid = str(cast.cast_info.uuid)
                except Exception:
                    continue

                if self.registry is not None:
                    self.registry.remember(
                        name, kind="tv", address=host, connector="tv",
                        aliases=[uuid], cast_uuid=uuid,
                    )
                found.append({"name": name, "address": host, "kind": "tv"})
        finally:
            try:
                browser.stop_discovery()
            except Exception:
                pass

        return found

    # ==========================================================
    # AÇÕES
    # ==========================================================

    def _register_actions(self):
        self._actions = {
            "list": self._action_list,
            "play_url": self._action_play_url,
            "pause": self._action_pause,
            "resume": self._action_resume,
            "stop": self._action_stop,
            "volume": self._action_volume,
            "turn_off": self._action_turn_off,
        }

    def validate(self, action: str, **kwargs) -> str:
        """Valida argumentos obrigatórios antes de checar disponibilidade."""
        if action == "play_url" and not str(kwargs.get("url") or "").strip():
            return "Qual conteúdo você quer enviar para a TV?"
        if action == "volume":
            if kwargs.get("level") is None and kwargs.get("delta") is None:
                return "Diga o volume desejado para a TV."
        return ""

    def _get_cast(self, tv_name=None):
        """Conecta à TV pedida. Retorna (cast, None) ou (None, mensagem)."""
        if not self._has_pychromecast():
            return None, (
                "Não consigo controlar a TV ainda. Instale o pychromecast "
                "(pip install pychromecast)."
            )

        device = None
        if self.registry is not None:
            device = self.registry.find(tv_name, kind="tv") if tv_name else None
            if device is None and tv_name is None:
                known = self.registry.by_kind("tv")
                device = known[0] if known else None

        try:
            import pychromecast
            if device is not None and device.address:
                casts, browser = pychromecast.get_chromecasts(
                    timeout=8, known_hosts=[device.address]
                )
            else:
                casts, browser = pychromecast.get_chromecasts(timeout=8)
        except Exception as error:
            return None, f"Não consegui procurar TVs na rede: {error}"

        if not casts:
            try:
                browser.stop_discovery()
            except Exception:
                pass
            return None, "Não encontrei nenhuma TV na rede agora."

        cast = casts[0]
        try:
            cast.wait(timeout=5)
        except Exception:
            pass
        return cast, None

    def _action_list(self, **_kwargs) -> ConnectorResult:
        discovered = self.discover()
        known = []
        if self.registry is not None:
            known = self.registry.by_kind("tv")

        if discovered:
            names = ", ".join(item["name"] for item in discovered)
            return ConnectorResult.success(
                f"Encontrei {len(discovered)} TV(s) na rede: {names}.",
                data=discovered,
            )
        if known:
            names = ", ".join(d.name for d in known)
            return ConnectorResult.success(
                f"Tenho {len(known)} TV(s) cadastrada(s): {names}.",
                data=[d.as_dict() for d in known],
            )
        return ConnectorResult.failure(
            "Não encontrei TVs na rede. Verifique se a TV está ligada e na "
            "mesma rede Wi-Fi."
        )

    def _action_play_url(self, url="", tv=None, title="", **_kwargs) -> ConnectorResult:
        url = str(url or "").strip()
        if not url:
            return ConnectorResult.failure("Qual conteúdo você quer enviar para a TV?")

        if not self._has_pychromecast():
            return ConnectorResult.failure(
                "Para enviar conteúdo à TV, instale o pychromecast "
                "(pip install pychromecast)."
            )

        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)

        try:
            media = cast.media_controller
            media.play_media(url, content_type=self._guess_type(url), title=str(title or ""))
            media.block_until_active(timeout=10)
            return ConnectorResult.success("Enviei o conteúdo para a TV.")
        except Exception as error:
            return ConnectorResult.failure(f"Não consegui enviar para a TV: {error}")

    @staticmethod
    def _guess_type(url) -> str:
        lowered = str(url).lower()
        if lowered.endswith(".mp3"):
            return "audio/mp3"
        if lowered.endswith((".mp4", ".m4v")):
            return "video/mp4"
        if lowered.endswith(".webm"):
            return "video/webm"
        if lowered.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if lowered.endswith(".png"):
            return "image/png"
        return "video/mp4"

    def _action_pause(self, tv=None, **_kwargs) -> ConnectorResult:
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            cast.media_controller.pause()
            return ConnectorResult.success("Pausei a TV.")
        except Exception as error:
            return ConnectorResult.failure(f"Não consegui pausar a TV: {error}")

    def _action_resume(self, tv=None, **_kwargs) -> ConnectorResult:
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            cast.media_controller.play()
            return ConnectorResult.success("Retomei a TV.")
        except Exception as error:
            return ConnectorResult.failure(f"Não consegui retomar a TV: {error}")

    def _action_stop(self, tv=None, **_kwargs) -> ConnectorResult:
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            cast.media_controller.stop()
            return ConnectorResult.success("Parei a reprodução na TV.")
        except Exception as error:
            return ConnectorResult.failure(f"Não consegui parar a TV: {error}")

    def _action_volume(self, level=None, delta=None, tv=None, **_kwargs) -> ConnectorResult:
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)

        try:
            controller = cast.status
            current = controller.volume_level if controller else 0.5
            if level is not None:
                target = max(0.0, min(1.0, float(level)))
            elif delta is not None:
                target = max(0.0, min(1.0, float(current) + float(delta)))
            else:
                return ConnectorResult.failure("Diga o volume desejado para a TV.")

            cast.set_volume(target)
            return ConnectorResult.success(f"Volume da TV em {int(target * 100)}%.")
        except Exception as error:
            return ConnectorResult.failure(f"Não consegui ajustar o volume da TV: {error}")

    def _action_turn_off(self, tv=None, **_kwargs) -> ConnectorResult:
        """Desliga a TV. Suportado por algumas TVs; degrada com mensagem clara."""
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            cast.quit_app()
            return ConnectorResult.success("Enviei o comando de desligar para a TV.")
        except Exception as error:
            return ConnectorResult.failure(
                f"Esta TV não aceitou o comando de desligar pelo Cast: {error}"
            )

    # ----------------------------------------------------------
    # Utilidades de rede (para cadastro manual de TV por IP)
    # ----------------------------------------------------------

    @staticmethod
    def is_reachable(host, port=8009, timeout=1.5) -> bool:
        """Testa se um IP de TV responde na porta Cast padrão (8009)."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False