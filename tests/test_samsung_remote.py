# -*- coding: utf-8 -*-
"""SamsungRemote com fakes: nada aqui toca em rede real."""
import json

import pytest

from core.connectivity.samsung_remote import SamsungRemote


class FakeWS:
    def __init__(self, events):
        self._events = list(events)
        self.sent = []
        self.closed = False

    def recv(self):
        if not self._events:
            raise TimeoutError()
        return json.dumps(self._events.pop(0))

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def close(self):
        self.closed = True


def _remote(ws_events=None, tmp_path=None):
    factory = lambda url, timeout: FakeWS(ws_events or [])  # noqa: E731
    token_path = str(tmp_path / "token.txt") if tmp_path else SamsungRemote.TOKEN_PATH if hasattr(SamsungRemote, "TOKEN_PATH") else "data/security/samsung_tv_token.txt"
    return SamsungRemote(
        "192.168.15.6",
        token_path=token_path,
        ws_factory=factory,
        http_get=lambda url, timeout: "{}",
    )


def test_resolve_app_por_alias(tmp_path):
    remote = _remote(tmp_path=tmp_path)
    remote.get_app_status = lambda app_id: {"id": app_id}
    app_id, pretty = remote.resolve_app("spotify")
    assert app_id == "3201606009684"
    assert pretty == "Spotify"


def test_resolve_app_desconhecido(tmp_path):
    remote = _remote(tmp_path=tmp_path)
    remote.get_app_status = lambda app_id: None
    app_id, _ = remote.resolve_app("aplicativo inexistente")
    assert app_id is None


def test_send_key_sem_resposta(tmp_path):
    remote = _remote(tmp_path=tmp_path)
    ok, _msg = remote.send_key("KEY_POWER")
    assert ok is False


def test_send_key_nao_autorizado(tmp_path):
    events = [{"event": "ms.channel.unauthorized"}]
    remote = _remote(ws_events=events, tmp_path=tmp_path)
    ok, msg = remote.send_key("KEY_POWER")
    assert ok is False
    assert "autorizar" in msg.lower()


def test_send_key_autorizado_envia_e_salva_token(tmp_path):
    events = [
        {"event": "ms.channel.connect",
         "data": {"token": "TOKEN123"}},
    ]
    remote = _remote(ws_events=events, tmp_path=tmp_path)
    ok, _ = remote.send_key("KEY_POWER")
    assert ok is True
    assert (tmp_path / "token.txt").read_text(encoding="utf-8").strip() == "TOKEN123"


def test_launch_app_rest_ok(tmp_path):
    calls = []

    def fake_post(url, timeout):
        calls.append(url)
        return "true"

    remote = _remote(tmp_path=tmp_path)
    remote._http_post = fake_post
    ok, _ = remote.launch_app("3201606009684")
    assert ok is True
    assert "api/v2/applications/3201606009684" in calls[0]


def test_launch_app_rest_erro(tmp_path):
    def fake_post(url, timeout):
        raise OSError("recusado")

    remote = _remote(tmp_path=tmp_path)
    remote._http_post = fake_post
    ok, _msg = remote.launch_app("3201606009684")
    assert ok is False


def test_get_app_status(tmp_path):
    payload = json.dumps({"id": "3201606009684", "name": "Spotify",
                          "running": True})
    remote = _remote(tmp_path=tmp_path)
    remote._http_get = lambda url, timeout: payload
    status = remote.get_app_status("3201606009684")
    assert status["running"] is True


def test_open_app_por_lista_da_tv(tmp_path):
    launched = []
    remote = _remote(tmp_path=tmp_path)
    remote._http_post = lambda url, timeout: launched.append(url) or "true"
    ok, pretty = remote.open_app_by_name("netflix")
    assert ok is True
    assert pretty == "Netflix"
    assert any("applications/3201907018807" in url for url in launched)


def test_probe(tmp_path):
    remote = _remote(tmp_path=tmp_path)
    info = remote.probe()
    assert info["host"] == "192.168.15.6"
    assert info["token_saved"] is False
