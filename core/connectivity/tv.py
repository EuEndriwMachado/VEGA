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

import json
import re
import socket
import threading
import time
import urllib.parse

from core.connectivity.base import Connector, ConnectorResult, ConnectorStatus


class TVConnector(Connector):
    """Descobre e controla TVs na rede local."""

    name = "tv"
    install_hint = (
        "Para enviar mídia à TV, instale pychromecast "
        "(pip install pychromecast). Para TVs Samsung/LG, informe o IP "
        "da TV nas configurações."
    )

    #: Tempo (segundos) que uma conexão Cast fica em cache antes de ser
    #: reconectada. Reutilizar a conexão mantém a SESSÃO de mídia viva — sem
    #: isso, cada comando abria e fechava o Cast, a sessão sumia e
    #: pausar/retomar/parar falhavam com "Failed to execute pause".
    _CAST_CACHE_TTL = 30.0

    def __init__(self, registry=None, runner=None):
        self._cast_available = None
        self._cast_cache = None      # (cast, browser, timestamp)
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
        known_tv = []
        if self.registry is not None:
            known_tv = self.registry.by_kind("tv")

        # As TVs conhecidas são informadas mesmo com o pychromecast presente:
        # é a informação mais útil ("tenho esta TV cadastrada") e antes era
        # escondida quando a biblioteca estava instalada.
        if known_tv:
            names = ", ".join(d.name for d in known_tv)
            detail = f"{len(known_tv)} TV(s) conhecida(s) na rede: {names}."
            if self._has_pychromecast():
                detail += " Controle via Cast disponível."
            return ConnectorStatus(
                name=self.name,
                available=True,
                detail=detail,
                install_hint="",
            )

        if self._has_pychromecast():
            return ConnectorStatus(
                name=self.name,
                available=True,
                detail="pychromecast disponível para controle de TV via Cast.",
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
            "play_youtube": self._action_play_youtube,
            "search_youtube": self._action_search_youtube,
            "play_spotify": self._action_play_spotify,
            "wake_on": self._action_wake_on,
            "open_app": self._action_open_app,
            "pause": self._action_pause,
            "resume": self._action_resume,
            "stop": self._action_stop,
            "volume": self._action_volume,
            "key": self._action_key,
            "turn_off": self._action_turn_off,
        }

    def validate(self, action: str, **kwargs) -> str:
        """Valida argumentos obrigatórios antes de checar disponibilidade."""
        if action == "play_url" and not str(kwargs.get("url") or "").strip():
            return "Qual conteúdo você quer enviar para a TV?"
        if action == "play_youtube" and not str(kwargs.get("query") or "").strip():
            return "O que você quer assistir na TV?"
        if action == "search_youtube" and not str(kwargs.get("query") or "").strip():
            return "O que você quer pesquisar no YouTube da TV?"
        if action == "open_app" and not str(kwargs.get("app") or "").strip():
            return "Qual app você quer abrir na TV?"
        if action == "play_spotify" and not str(kwargs.get("query") or "").strip():
            return "O que você quer ouvir no Spotify da TV?"
        if action == "wake_on":
            return ""

    # Teclas do controle remoto Samsung (via WSS).
    _KEY_MAP = {
        "enter": "KEY_ENTER", "ok": "KEY_ENTER",
        "cima": "KEY_UP", "up": "KEY_UP", "sobe": "KEY_UP",
        "baixo": "KEY_DOWN", "down": "KEY_DOWN", "desce": "KEY_DOWN",
        "esquerda": "KEY_LEFT", "left": "KEY_LEFT",
        "direita": "KEY_RIGHT", "right": "KEY_RIGHT",
        "voltar": "KEY_RETURN", "volta": "KEY_RETURN", "back": "KEY_RETURN",
        "sair": "KEY_EXIT", "exit": "KEY_EXIT", "fecha": "KEY_EXIT",
        "home": "KEY_HOME", "inicio": "KEY_HOME", "menu": "KEY_MENU",
        "info": "KEY_INFO", "canal+": "KEY_CHUP", "canal-": "KEY_CHDOWN",
        "play": "KEY_PLAYBACK", "proxima": "KEY_CHNEXT",
    }

    def _action_key(self, key="", tv=None, **_kwargs) -> ConnectorResult:
        """Envia uma tecla do controle ("aperte enter na tv" -> key='enter')."""
        remote = self._samsung_remote()
        if remote is None:
            return ConnectorResult.failure(
                "Não sei o IP da TV para enviar teclas."
            )
        mapped = self._KEY_MAP.get(str(key or "").strip().lower())
        if not mapped:
            return ConnectorResult.failure(
                f"Não conheço a tecla '{key}'. Posso: "
                + ", ".join(sorted(self._KEY_MAP)) + "."
            )
        ok, error = remote.send_key(mapped)
        if ok:
            return ConnectorResult.success(f"Tecla {key} enviada para a TV.")
        return ConnectorResult.failure(error)
        if action == "volume":
            if kwargs.get("level") is None and kwargs.get("delta") is None:
                return "Diga o volume desejado para a TV."
        return ""

    def _get_cast(self, tv_name=None, reuse=True):
        """Conecta à TV pedida. Retorna (cast, None) ou (None, mensagem).

        A conexão fica em cache por alguns segundos para preservar a sessão
        de mídia (pause/retoma/para dependem dela).
        """
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

        cached = self._get_cached_cast(device, reuse)
        if cached is not None:
            return cached, None

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
        self._remember_cast(cast, browser, device)
        return cast, None

    # ----------------------------------------------------------
    # Cache da conexão Cast
    # ----------------------------------------------------------

    def _get_cached_cast(self, device, reuse=True):
        """Devolve o cast em cache se ainda for válido e do mesmo aparelho."""
        if not reuse or self._cast_cache is None:
            return None
        import time as _time
        cast, _browser, cached_at, cached_address = self._cast_cache
        if (_time.time() - cached_at) > self._CAST_CACHE_TTL:
            self._drop_cached_cast()
            return None
        if device is not None and device.address and device.address != cached_address:
            return None
        # O socket ainda responde? Se não, descarta e reconecta.
        try:
            if not cast.socket_client.is_connected:
                self._drop_cached_cast()
                return None
        except Exception:
            self._drop_cached_cast()
            return None
        return cast

    def _remember_cast(self, cast, browser, device):
        import time as _time
        self._drop_cached_cast()
        address = getattr(device, "address", "") if device is not None else ""
        self._cast_cache = (cast, browser, _time.time(), address)

    def _drop_cached_cast(self):
        if self._cast_cache is None:
            return
        _cast, browser, _at, _address = self._cast_cache
        self._cast_cache = None
        try:
            if browser is not None:
                browser.stop_discovery()
        except Exception:
            pass

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

    # ----------------------------------------------------------
    # YouTube na TV: usa o APP do YouTube (não o receiver genérico).
    # Enviar uma página HTML de busca ao Default Receiver só mostrava
    # "transmitindo" sem tocar nada — o receiver não renderiza páginas.
    # O caminho correto é resolver a busca para um videoId e mandar ao
    # app do YouTube da TV via YouTubeController.
    # ----------------------------------------------------------

    @staticmethod
    def resolve_youtube_video(query):
        """Busca no YouTube e devolve (video_id, titulo) do primeiro resultado.

        Usa a página de resultados do YouTube (sem API key): o HTML embute
        ytInitialData com os ids. Falha limpa quando não acha.
        """
        try:
            import requests
            response = requests.get(
                "https://www.youtube.com/results",
                params={"search_query": str(query).strip()},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            match = re.search(
                r'"videoRenderer":\{"videoId":"([\w-]{11})"',
                response.text,
            )
            if not match:
                return None, None
            video_id = match.group(1)
            title = re.search(
                r'"videoRenderer":\{"videoId":"' + video_id
                + r'.{0,600}?"title":\{"runs":\[\{"text":"([^"]{0,120})',
                response.text,
            )
            return video_id, (title.group(1) if title else "")
        except Exception:
            return None, None

    def _action_play_youtube(self, query="", tv=None, **_kwargs) -> ConnectorResult:
        """Toca uma busca no app do YouTube da TV (vídeo de verdade).

        Caminho preferido: APP NATIVO via deep link (áudio PT-BR, navegação
        livre, sem 'ready to cast'). Sem Samsung/autorização, cai no Cast.
        """
        query = str(query or "").strip()
        if not query:
            return ConnectorResult.failure("O que você quer assistir na TV?")

        video_id, title = self.resolve_youtube_video(query)
        if not video_id:
            return ConnectorResult.failure(
                f"Não encontrei vídeo para '{query}' no YouTube."
            )

        # 1) App nativo (sem token/Cast): deep link v=<videoId>.
        native = self._play_youtube_native(video_id, title)
        if native is not None:
            return native

        # 2) Fallback Cast.
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            from pychromecast.controllers.youtube import YouTubeController
            youtube = YouTubeController()
            cast.register_handler(youtube)
            youtube.quick_play(media_id=video_id, timeout=30.0)
            label = f" ({title})" if title else ""
            return ConnectorResult.success(
                f"Tocando '{query}'{label} no YouTube da TV.",
                data={"video_id": video_id, "title": title},
            )
        except Exception as error:
            return ConnectorResult.failure(
                f"Não consegui tocar no YouTube da TV: {error}"
            )

    def _play_youtube_native(self, video_id, title=""):
        """Abre o vídeo no APP NATIVO do YouTube.

        POST REST com query `?v=<videoId>`: o app abre NA FRENTE (visible)
        já no vídeo — áudio PT-BR do app, navegação livre, sem 'ready to
        cast'. Retorna ConnectorResult ou None se a Samsung não responder.
        """
        remote = self._samsung_remote()
        if remote is None:
            return None
        # Deep link oficial do YouTube TV: URL /tv#/watch/... como metaTag.
        # ("v=<id>" sozinho abre o app mas não seleciona o vídeo.)
        deep_link = (
            "https://www.youtube.com/tv#/watch/video/control?v=" + video_id
        )
        ok, _ = remote.launch_app_deep_link("111299001912", meta_tag=deep_link)
        if not ok:
            return None
        label = f" ({title})" if title else ""
        return ConnectorResult.success(
            f"Tocando no YouTube da TV{label} — app nativo, com áudio em "
            "português. Você pode navegar à vontade.",
            data={"video_id": video_id, "title": title, "native": True},
        )

    def _action_search_youtube(self, query="", tv=None, **_kwargs) -> ConnectorResult:
        """Pesquisa no app do YouTube da TV e toca o melhor resultado.

        Mesmo caminho do play_youtube: app NATIVO via deep link.
        """
        query = str(query or "").strip()
        if not query:
            return ConnectorResult.failure(
                "O que você quer pesquisar no YouTube da TV?"
            )

        video_id, title = self.resolve_youtube_video(query)
        if not video_id:
            return ConnectorResult.failure(
                f"Não encontrei vídeo para '{query}' no YouTube."
            )

        native = self._play_youtube_native(video_id, title)
        if native is not None:
            return native

        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            from pychromecast.controllers.youtube import YouTubeController
            youtube = YouTubeController()
            cast.register_handler(youtube)
            youtube.quick_play(media_id=video_id, timeout=30.0)
            label = f" ({title})" if title else ""
            return ConnectorResult.success(
                f"Pesquisei '{query}' no YouTube da TV e estou tocando{label}.",
                data={"video_id": video_id, "title": title},
            )
        except Exception as error:
            return ConnectorResult.failure(
                f"Não consegui pesquisar no YouTube da TV: {error}"
            )

    # Aplicativos comuns da TV: apelido falado -> app_id do Cast.
    # IMPORTANTE: numa TV Samsung Tizen, só apps com versão Cast nativa
    # (YouTube, Spotify) podem ser iniciados pelo protocolo Cast. Apps como
    # Netflix/Prime rodam só como app Tizen e o Cast os recusa — o conector
    # avisa isso em vez de falhar em silêncio.
    TV_APPS = {
        "youtube": "youtube",      # via quick_play (abre o app nativo e toca)
        "youtube music": "youtube",
    }
    # Apps populares que o Cast NÃO inicia numa TV Samsung Tizen.
    TV_APPS_UNSUPPORTED = {
        "spotify": "Spotify",
        "netflix": "Netflix",
        "prime video": "Prime Video",
        "amazon prime": "Prime Video",
        "disney": "Disney+",
        "disney plus": "Disney+",
        "globoplay": "Globoplay",
        "max": "Max",
        "hbo max": "Max",
        "hbo": "Max",
        "pluto tv": "Pluto TV",
        "twitch": "Twitch",
    }

    @staticmethod
    def _youtube_first_video(query):
        """Primeiro video_id do YouTube para uma busca (para abrir o app)."""
        import urllib.request
        request = urllib.request.Request(
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(query),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        try:
            data = urllib.request.urlopen(request, timeout=10).read().decode("utf-8", "ignore")
        except Exception:
            return ""
        match = re.search(r'"videoId":"([\w-]{11})"', data)
        return match.group(1) if match else ""

    @classmethod
    def resolve_tv_app(cls, name):
        """Resolve um apelido falado para (app_id, nome_limpo)."""
        key = str(name or "").strip().lower()
        key = re.sub(r"^(?:o|a|meu|minha)\s+", "", key).strip()
        for alias, app_id in cls.TV_APPS.items():
            if alias in key:
                return app_id, alias
        return None, key

    def _samsung_remote(self):
        """Cliente de controle remoto Samsung (teclas + apps nativos)."""
        from core.connectivity.samsung_remote import SamsungRemote
        host = self._samsung_host()
        if not host:
            return None
        return SamsungRemote(host)

    def _action_play_spotify(self, query="", tv=None, **_kwargs) -> ConnectorResult:
        """Toca uma busca no Spotify da TV.

        Caminho comprovado: POST REST com metaTag (o app abre NA FRENTE na
        busca), depois 2x KEY_ENTER para selecionar e tocar o 1º resultado.
        Fallback: Cast.
        """
        query = str(query or "").strip()
        if not query:
            return ConnectorResult.failure("O que você quer ouvir no Spotify da TV?")

        # 1) Native: app na frente na busca + teclas para tocar.
        remote = self._samsung_remote()
        if remote is not None:
            ok, _ = remote.launch_app_deep_link("3201606009684", meta_tag=query)
            if ok:
                time.sleep(8.0)  # app carrega a busca (app pesado)
                remote.send_key("KEY_DOWN")   # 1º resultado da busca
                time.sleep(1.0)
                remote.send_key("KEY_ENTER")  # seleciona e toca
                return ConnectorResult.success(
                    f"Tocando '{query}' no Spotify da TV."
                )

        # 2) Fallback: Cast (o Spotify tem receiver Cast nativo).
        if not self._has_pychromecast():
            return ConnectorResult.failure(
                "Para tocar no Spotify da TV, instale o pychromecast."
            )
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            from pychromecast.quick_play import quick_play
            quick_play(cast, "spotify", {"query": query}, timeout=30.0)
            return ConnectorResult.success(f"Tocando '{query}' no Spotify da TV.")
        except Exception as error:
            return ConnectorResult.failure(
                f"Não consegui tocar no Spotify da TV: {error}"
            )

    def _action_wake_on(self, tv=None, **_kwargs) -> ConnectorResult:
        """Liga a TV via Wake-on-LAN (pacote mágico).

        Comprovado na DU7700: precisa de "Ligar com o celular por Wi-Fi"
        ativo (Configurações > Geral > Rede), que é o mesmo WoL do modelo.
        """
        import socket

        device = None
        if self.registry is not None:
            known = self.registry.by_kind("tv")
            device = known[0] if known else None

        mac = ""
        if device is not None:
            mac = str(device.metadata.get("mac") or "").strip()

        if not mac:
            return ConnectorResult.failure(
                "Não sei o endereço MAC da TV ainda. Ligue-a uma vez pelo "
                "controle que eu registro o MAC e depois ligá por voz."
            )

        try:
            mac_clean = mac.replace(":", "").replace("-", "").replace(" ", "")
            mac_bytes = bytes.fromhex(mac_clean)
            packet = b"\xff" * 6 + mac_bytes * 16
            # Envia em broadcast nas portas usuais (9 e 7), nos dois
            # endereços de broadcast da sub-rede — a DU7700 responde.
            import socket as sk
            with sk.socket(sk.AF_INET, sk.SOCK_DGRAM) as sock:
                sock.setsockopt(sk.SOL_SOCKET, sk.SO_BROADCAST, 1)
                for port in (9, 7):
                    sock.sendto(packet, ("255.255.255.255", port))
                    sock.sendto(packet, ("192.168.15.255", port))
            # Confirma esperando a rede voltar (standby: rede viva, tela
            # apagada). Depois KEY_POWER acende a tela.
            time.sleep(5)
            for _attempt in range(9):  # até ~90s (boot da TV é lento)
                time.sleep(10)
                try:
                    with sk.create_connection(("192.168.15.6", 8001), timeout=3):
                        break
                except OSError:
                    continue
            else:
                return ConnectorResult.failure(
                    "Mandei o comando para ligar a TV, mas ela não respondeu "
                    "ainda. Se não acendeu, verifique se 'Ligar com o celular' "
                    "está ativo nas configurações."
                )
            # Rede viva ≠ tela acesa (standby). KEY_POWER acorda a tela.
            remote = self._samsung_remote()
            if remote is not None:
                for _try in range(4):
                    ok, _err = remote.send_key("KEY_POWER")
                    time.sleep(4)
                    try:
                        import urllib.request as _u
                        import json as _json
                        _d = _json.loads(
                            _u.urlopen(
                                f"http://{'192.168.15.6'}:8001/api/v2/", timeout=3
                            ).read()
                        )
                        if (
                            (_d.get("device", {}) or {}).get("PowerState", "standby")
                            != "standby"
                        ):
                            return ConnectorResult.success("Liguei a TV.")
                    except Exception:
                        pass
            # Não consegui confirmar o estado da tela; assume que ligou.
            return ConnectorResult.success(
                "Comando enviado. Se a TV não acendeu, diga 'liga a tv' de novo."
            )
        except Exception as error:
            return ConnectorResult.failure(
                f"Não consegui mandar o pacote para ligar a TV: {error}"
            )

    def _action_open_app(self, app="", tv=None, **_kwargs) -> ConnectorResult:
        """Abre um aplicativo na TV.

        YouTube abre o app NATIVO via quick_play — antes usava start_app do
        Cast, que só mostrava a tela "ready to cast" sem abrir nada. Os
        demais apps (Netflix, Spotify...) são nativos Tizen: precisam do
        token de controle remoto da TV (WebSocket), que exige autorização.
        """
        app = str(app or "").strip()
        if not app:
            return ConnectorResult.failure("Qual app você quer abrir na TV?")

        app_id, resolved_name = self.resolve_tv_app(app)
        if not app_id:
            if any(alias in app.lower() for alias in self.TV_APPS_UNSUPPORTED):
                name = self.TV_APPS_UNSUPPORTED.get(app.lower(), app)
                # Apps nativos Tizen: tenta a API REST da Samsung (abre o app
                # de verdade, sem Cast). Se não estiver disponível, explica.
                remote = self._samsung_remote()
                if remote is not None:
                    ok, opened = remote.open_app_by_name(app)
                    if ok:
                        return ConnectorResult.success(f"Abrindo {opened} na TV.")
                if app.lower().startswith("spotify"):
                    return ConnectorResult.failure(
                        f"{name} precisa ser aberto pelo controle remoto da TV. "
                        "Autorize a VEGA no menu Configurações > Conexão > "
                        "Gerenciador de dispositivos da TV e eu abro pra você."
                    )
                return ConnectorResult.failure(
                    f"{name} é um app nativo da TV e o Chromecast não consegue "
                    "abri-lo. Abra pelo controle da TV; eu consigo abrir o "
                    "YouTube e tocar música nele."
                )
            return ConnectorResult.failure(
                f"Não conheço o app '{app}' na TV. Posso abrir: "
                + ", ".join(sorted(self.TV_APPS)) + "."
            )

        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)

        # Abre o YouTube de verdade com um vídeo de abertura — sem isso o
        # receptor Cast mostrava só a logo "ready to cast".
        video_id = self._youtube_first_video("lofi hip hop")
        if not video_id:
            return ConnectorResult.failure(
                "Não consegui buscar o conteúdo de abertura do YouTube."
            )
        try:
            from pychromecast.quick_play import quick_play
            quick_play(cast, "youtube", {"media_id": video_id}, timeout=30.0)
            return ConnectorResult.success(f"Abrindo o YouTube na TV.")
        except Exception as error:
            return ConnectorResult.failure(
                f"Não consegui abrir {resolved_name} na TV: {error}"
            )

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
        """Desliga a TV.

        Duas rotas:
        - Samsung (Tizen): tecla KEY_POWER via WebSocket do controle remoto
          (porta 8001). A primeira conexão exige que o usuário aceite o
          popup na tela da TV; depois o token fica salvo.
        - Demais TVs: fecha o app em execução via Cast (o efeito depende do
          modelo; em algumas TVs isso coloca em standby profundo).
        """
        # 1) Samsung: tecla KEY_POWER via WSS (desligamento real).
        remote = self._samsung_remote()
        if remote is not None:
            ok, _msg = remote.power_off()
            if ok:
                return ConnectorResult.success("Desliguei a TV.")

        # 2) Cast: fecha o app em execução.
        cast, error = self._get_cast(tv)
        if cast is None:
            return ConnectorResult.failure(error)
        try:
            cast.quit_app()
            return ConnectorResult.success(
                "Fechei o app da TV. Se ela não apagou, use o controle: "
                "o desligamento físico por rede precisa da autorização "
                "aceita na tela da TV uma primeira vez."
            )
        except Exception as error:
            return ConnectorResult.failure(
                f"Esta TV não aceitou o comando de desligar pelo Cast: {error}"
            )

    # ------------------------------------------------------------------
    # Samsung (Tizen): controle remoto via WebSocket — desligamento real
    # ------------------------------------------------------------------

    _SAMSUNG_TOKEN_PATH = "data/security/samsung_tv_token.txt"

    def _samsung_host(self):
        """IP da TV registrada (para o WebSocket da Samsung)."""
        if self.registry is not None:
            known = self.registry.by_kind("tv")
            if known and known[0].address:
                return known[0].address
        return ""

    def _save_samsung_token(self, token):
        """Guarda o token de autorização do controle remoto da TV."""
        import os
        path = self._SAMSUNG_TOKEN_PATH
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(token.strip())
        except OSError:
            pass

    def _samsung_power_off(self):
        """Envia KEY_POWER pela API do controle Samsung. None se não aplicável."""
        host = self._samsung_host()
        if not host:
            return None
        try:
            import json
            import time
            import websocket
        except ImportError:
            return None  # biblioteca ausente: cai no caminho Cast

        import os
        token = ""
        try:
            with open(self._SAMSUNG_TOKEN_PATH, "r", encoding="utf-8") as file:
                token = file.read().strip()
        except OSError:
            token = ""

        name_hex = "VEGA".encode().hex()
        url = (
            f"ws://{host}:8001/api/v2/channels/samsung.remote.control"
            f"?name={name_hex}"
        )
        if token:
            url += f"&token={token}"

        try:
            ws = websocket.create_connection(url, timeout=5)
            # Aguarda o evento de conexão/autorização. Quando a TV autoriza,
            # ela envia um TOKEN na resposta: salvamos para as próximas vezes
            # não pedirem autorização de novo.
            authorized = False
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    message = ws.recv()
                    data = json.loads(message)
                except Exception:
                    break
                event = data.get("event", "")
                if event == "ms.channel.connect":
                    authorized = True
                    new_token = str((data.get("data") or {}).get("token") or "")
                    if new_token and new_token != token:
                        self._save_samsung_token(new_token)
                    break
                if event == "ms.channel.unauthorized":
                    break
            if not authorized:
                try:
                    ws.close()
                except Exception:
                    pass
                return ConnectorResult.failure(
                    "A TV precisa autorizar a VEGA como controle remoto: "
                    "aceite o pedido que aparece na tela da TV e tente de novo."
                )
            ws.send(json.dumps({
                "method": "ms.remote.control",
                "params": {
                    "Cmd": "Click",
                    "DataOfCmd": "KEY_POWER",
                    "Option": "false",
                    "TypeOfRemote": "SendRemoteKey",
                },
            }))
            time.sleep(0.5)
            try:
                ws.close()
            except Exception:
                pass
            return ConnectorResult.success("Desliguei a TV.")
        except Exception as error:
            # TV desligada ou fora da rede: o Cast também não estará lá.
            return ConnectorResult.failure(
                f"Não consegui falar com a TV para desligar: {error}"
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