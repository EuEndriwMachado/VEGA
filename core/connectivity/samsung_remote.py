# -*- coding: utf-8 -*-
"""Controle remoto de TVs Samsung Tizen via rede.

Duas vias complementares:

- REST (porta 8001, ``/api/v2/applications``): LISTA apps instalados e
  LANÇA apps nativos (YouTube, Spotify, Netflix...) sem token — em muitos
  modelos DU7700 este caminho funciona sem autorização alguma.
- WebSocket (porta 8001, ``samsung.remote.control``): teclas do controle
  (KEY_POWER, KEY_ENTER...) — exige token; o token vem do popup de
  autorização que a TV mostra na primeira conexão (ou é concedido pelo
  Gerenciador de dispositivos da TV).

Nada aqui levanta exceção para fora: erros viram returnos com ok=False.
`ws_factory` e `http_get` são pontos de injeção para testes com fakes.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

TOKEN_PATH = "data/security/samsung_tv_token.txt"

# app_id -> (nome bonito, aliases de busca). Usado para resolver o nome
# falado ("netflix", "spotify", "youtube") no app_id real da TV.
# app_id verificados na TV real (UN50DU7700, via REST /api/v2/applications/<id>):
# youtube 111299001912, spotify 3201606009684, netflix 3201907018807,
# prime 3201910019365, disney 3201901017640, apple tv 3201807016597,
# browser 3202010022079. Lançamento: POST na mesma URL (retorna "true").
KNOWN_APPS = {
    "111299001912": ("YouTube", ("youtube", "yt")),
    "3201606009684": ("Spotify", ("spotify",)),
    "3201907018807": ("Netflix", ("netflix",)),
    "3201910019365": ("Prime Video", ("prime", "prime video", "amazon prime")),
    "3201901017640": ("Disney+", ("disney", "disney plus", "disney+")),
    "3201807016597": ("Apple TV", ("apple tv", "apple")),
    "3202010022079": ("Browser", ("browser", "navegador", "internet")),
    "3201601007230": ("HBO Max", ("hbo", "hbo max", "max")),
    "3201512006963": ("Plex", ("plex",)),
    "3201908019022": ("Globoplay", ("globoplay", "globoplay")),
}

# Aliases falados -> app_id canônico (fallback quando a consulta à TV falha).
ALIAS_TO_APP_ID = {
    alias: app_id
    for app_id, (_name, aliases) in KNOWN_APPS.items()
    for alias in aliases
}

_TELEVISION_OFF = "KEY_POWER"


def _b64_name(name: str) -> str:
    """A TV espera o nome do controle em base64."""
    return base64.b64encode(name.encode("utf-8")).decode("ascii")


class SamsungRemote:
    """Cliente de controle remoto de uma TV Samsung Tizen por IP."""

    def __init__(self, host, token_path=TOKEN_PATH,
                 ws_factory=None, http_get=None, timeout=6.0):
        self.host = str(host or "").strip()
        self.token_path = token_path
        self._ws_factory = ws_factory or self._default_ws_factory
        self._http_get = http_get or self._default_http_get
        self._http_post = getattr(http_get, "__self__", None)
        if self._http_post is None:
            self._http_post = self._default_http_post
        self.timeout = timeout

    # --------------------------------------------------------------
    # HTTP / REST (sem token na maioria dos modelos)
    # --------------------------------------------------------------

    @staticmethod
    def _default_http_get(url, timeout):
        request = urllib.request.Request(url, headers={"User-Agent": "VEGA/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")

    @staticmethod
    def _default_http_post(url, timeout):
        request = urllib.request.Request(
            url, method="POST", headers={"User-Agent": "Chrome/36"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")

    def _rest(self, path, timeout=None):
        """GET no endpoint REST da TV. Retorna (ok, payload_dict)."""
        if not self.host:
            return False, {}
        url = f"http://{self.host}:8001{path}"
        try:
            body = self._http_get(url, timeout or self.timeout)
            return True, json.loads(body)
        except Exception:
            return False, {}
    def list_apps(self):
        """Apps instalados na TV: [(app_id, nome), ...]. []."""
        ok, payload = self._rest("/api/v2/applications")
        if not ok:
            return []
        apps = []
        for item in payload.get("data") or []:
            app_id = str(item.get("appId") or "")
            name = str(item.get("name") or "")
            if app_id:
                apps.append((app_id, name))
        return apps

    def resolve_app(self, name):
        """Resolve nome falado -> (app_id, nome_bonito).

        Verifica os apps conhecidos contra a TV (estado real) e casa por
        alias; o fallback é a tabela local, sem rede.
        """
        wanted = re.sub(r"[^a-z0-9+ ]", " ", str(name or "").strip().lower())
        wanted = re.sub(r"\s+", " ", wanted).strip()
        if not wanted:
            return None, ""

        for app_id, (pretty, aliases) in KNOWN_APPS.items():
            for alias in aliases:
                if wanted == alias or wanted in alias or alias in wanted:
                    if self.get_app_status(app_id) is not None:
                        return app_id, pretty
        return None, wanted

    def get_app_status(self, app_id):
        """Estado de um app na TV: dict ou None se não existir."""
        ok, payload = self._rest(f"/api/v2/applications/{app_id}")
        return payload if ok and payload else None

    def launch_app(self, app_id):
        """Lança um app nativo via REST POST. Retorna (ok, mensagem)."""
        if not self.host:
            return False, "Sem IP da TV."
        app_id = urllib.parse.quote(str(app_id), safe="")
        url = f"http://{self.host}:8001/api/v2/applications/{app_id}"
        try:
            body = self._http_post(url, self.timeout)
            ok = body.strip().lower() == "true"
            return (True, "") if ok else (False, f"TV respondeu: {body.strip()}")
        except Exception as error:
            return False, str(error)

    def launch_app_deep_link(self, app_id, meta_tag=""):
        """Lança um app com conteúdo via deep link.

        CAMINHO PRINCIPAL: POST REST com metaTag na query
        (`/applications/<id>?metaTag=...`) — alguns firmwares (24_KSUE,
        DU7700) aceitam e o app abre NA FRENTE da tela com o conteúdo.
        O POST sem query também traz o app para frente (visible=true),
        enquanto o `ms.channel.emit` roda o app escondido (visible=false).
        Retorna (ok, mensagem).
        """
        if not self.host:
            return False, "Sem IP da TV."
        app_id = urllib.parse.quote(str(app_id), safe="")
        url = f"http://{self.host}:8001/api/v2/applications/{app_id}"
        if meta_tag:
            url += "?metaTag=" + urllib.parse.quote(str(meta_tag), safe="")
        try:
            body = self._http_post(url, self.timeout)
            ok = body.strip().lower() == "true"
            if not ok:
                return False, f"TV respondeu: {body.strip()}"
        except Exception as error:
            # REST fora: tenta o canal WS como backup.
            ok_ws, error_ws = self._send_on_persistent({
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.apps.launch",
                    "to": "host",
                    "data": {
                        "action_type": "DEEP_LINK",
                        "appId": str(app_id),
                        "metaTag": str(meta_tag or ""),
                    },
                },
            })
            if not ok_ws:
                return False, error_ws
        time.sleep(1.5)  # app abre em segundo
        return True, ""

    def open_app_by_name(self, name):
        """Resolve e lança um app pelo nome falado. Retorna (ok, mensagem)."""
        # 1) tabela local: resolve e lança direto (o POST confirma se existe)
        wanted = re.sub(r"\s+", " ", str(name or "").strip().lower())
        for app_id, (pretty, aliases) in KNOWN_APPS.items():
            for alias in aliases:
                if wanted == alias or wanted in alias or alias in wanted:
                    ok, error = self.launch_app(app_id)
                    if ok:
                        return True, pretty
                    return False, f"A TV recusou abrir {pretty}: {error}"
        return False, f"Não encontrei o app '{name}' na TV."

    # --------------------------------------------------------------
    # WebSocket (teclas) — exige token após o primeiro pareamento
    # --------------------------------------------------------------

    @staticmethod
    def _default_ws_factory(url, timeout):
        """WSS na porta 8002 (modelos novos exigem TLS; ws/8001 dá
        unauthorized sem popup)."""
        import ssl
        import websocket
        url = url.replace("ws://", "wss://").replace(":8001", ":8002")
        return websocket.create_connection(
            url, timeout=timeout, sslopt={"cert_reqs": ssl.CERT_NONE}
        )

    def _load_token(self):
        try:
            with open(self.token_path, "r", encoding="utf-8") as file:
                return file.read().strip()
        except OSError:
            return ""

    def _save_token(self, token):
        try:
            folder = os.path.dirname(self.token_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.token_path, "w", encoding="utf-8") as file:
                file.write(token.strip())
        except OSError:
            pass

    def connect(self, name="VEGA"):
        """Abre o canal WebSocket. Retorna (ws|None, autorizado, token_novo)."""
        url = (
            f"ws://{self.host}:8002/api/v2/channels/samsung.remote.control"
            f"?name={_b64_name(name)}"
        )
        token = self._load_token()
        if token:
            url += f"&token={token}"

        try:
            ws = self._ws_factory(url, self.timeout)
        except Exception:
            return None, False, ""

        authorized = False
        new_token = ""
        deadline = time.time() + 30  # janela para o usuário aceitar o popup
        while time.time() < deadline:
            try:
                data = json.loads(ws.recv())
            except Exception:
                break
            event = data.get("event", "")
            if event == "ms.channel.connect":
                authorized = True
                new_token = str((data.get("data") or {}).get("token") or "")
                if new_token and new_token != token:
                    self._save_token(new_token)
                break
            if event == "ms.channel.unauthorized":
                # Popup autorização pendente: ficar esperando; a TV mostra
                # o popup enquanto o canal fica aberto — o usuário aceita
                # e o evento de conexão chega.
                continue
        return ws, authorized, new_token

    # ------------------------------------------------------------------
    # Conexão persistente: evita o popup de autorização a cada comando.
    # A TV só pede autorização quando a conexão é nova sem token; mantendo
    # um canal vivo (com ping periódico) os comandos saem direto.
    # ------------------------------------------------------------------

    _persistent_ws = None
    _persistent_until = 0.0

    def _get_persistent(self, max_age=240.0):
        """Canal autorizado vivo, ou None. Recria se expirou."""
        if self._persistent_ws is not None and time.time() < self._persistent_until:
            return self._persistent_ws
        self._drop_persistent()
        ws, authorized, _token = self.connect()
        if ws is None or not authorized:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            return None
        self._persistent_ws = ws
        self._persistent_until = time.time() + max_age
        return ws

    def _drop_persistent(self):
        if self._persistent_ws is not None:
            try:
                self._persistent_ws.close()
            except Exception:
                pass
            self._persistent_ws = None
        self._persistent_until = 0.0

    def _send_on_persistent(self, payload):
        """Envia um comando pelo canal vivo. Renova 1x se a conexão caiu."""
        ws = self._get_persistent()
        if ws is None:
            return False, (
                "A TV precisa autorizar a VEGA: aceite o pedido na tela "
                "da TV (ou permita a VEGA no Gerenciador de dispositivos)."
            )
        try:
            ws.send(json.dumps(payload))
        except Exception:
            # Conexão caiu (TV desligada, etc.): renova e tenta mais 1x.
            self._drop_persistent()
            ws = self._get_persistent()
            if ws is None:
                return False, "A TV não respondeu ao canal de controle remoto."
            try:
                ws.send(json.dumps(payload))
            except Exception as error:
                return False, f"Falha ao enviar comando: {error}"
        return True, ""

    def send_key(self, key):
        """Envia uma tecla do controle (ex.: 'KEY_POWER'). Retorna (ok, msg)."""
        if not self.host:
            return False, "Sem IP da TV."
        ok, error = self._send_on_persistent({
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Click",
                "DataOfCmd": key,
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        })
        if ok:
            time.sleep(0.2)
        return ok, error

    def power_off(self):
        ok, error = self.send_key(_TELEVISION_OFF)
        if ok:
            return True, "Desliguei a TV."
        return False, error

    # --------------------------------------------------------------
    # Diagnóstico
    # --------------------------------------------------------------

    def probe(self) -> dict:
        """Estado das duas vias, para diagnóstico sem tocar na TV."""
        ok_rest, _payload = self._rest("/api/v2/applications", timeout=3)
        return {
            "host": self.host,
            "rest_apps": ok_rest,
            "token_saved": bool(self._load_token()),
        }
