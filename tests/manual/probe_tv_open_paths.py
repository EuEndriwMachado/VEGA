# -*- coding: utf-8 -*-
"""Diagnóstico: prova todas as vias de abertura de apps nativos na TV Samsung.

1. REST /api/v2/applications (lista e lançamento, geralmente sem token)
2. DIAL legado /ws/apps/<App> (Chrome UA)
3. Widget Connect /ws/apps/<id> com sID/sPin se houver
4. Teclas via WebSocket (se autorizado)
"""
import base64
import json
import socket
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST = "192.168.15.6"


def rest_apps():
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:8001/api/v2/applications", timeout=15
        ) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        apps = data.get("data") or []
        print(f"[1] REST apps: {len(apps)} apps")
        for item in apps:
            print("   ", item.get("appId"), "|", item.get("name"))
        return apps
    except Exception as error:
        print("[1] REST apps falhou:", error)
        return []


def dial(app):
    req = urllib.request.Request(
        f"http://{HOST}:8001/ws/apps/{app}", headers={"User-Agent": "Chrome/36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            print(f"[2] DIAL {app}: {response.status}")
            print("   ", response.read(200).decode("utf-8", "ignore"))
            return True
    except Exception as error:
        print(f"[2] DIAL {app}: {error}")
        return False


def dial_launch(app, app_id):
    """POST no DIAL para lançar o app nativo."""
    req = urllib.request.Request(
        f"http://{HOST}:8001/ws/apps/{app}",
        data=f"appId={app_id}".encode(),
        headers={
            "User-Agent": "Chrome/36",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            print(f"[3] DIAL POST {app}: {response.status}")
            return True
    except Exception as error:
        print(f"[3] DIAL POST {app}: {error}")
        return False


def ssdp_scan():
    msg = "\r\n".join([
        "M-SEARCH * HTTP/1.1",
        "HOST:239.255.255.250:1900",
        'MAN:"ssdp:discover"',
        "MX:2",
        "ST:urn:samsung.com:service:Authentication:1",
        "",
        "",
    ]).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4)
    s.sendto(msg, ("239.255.255.250", 1900))
    try:
        while True:
            data, addr = s.recvfrom(4096)
            print("[SSDP]", addr[0], data.decode("utf-8", "ignore")[:300])
    except socket.timeout:
        pass


if __name__ == "__main__":
    print("=== SSDP Samsung ===")
    ssdp_scan()
    print("\n=== [1] REST moderna ===")
    apps = rest_apps()
    print("\n=== [2] DIAL legado (status) ===")
    for app in ("YouTube", "Netflix", "Spotify"):
        dial(app)
    print("\n=== [4] WebSocket teclas (se autorizado) ===")
    try:
        import websocket
        token = ""
        try:
            with open("data/security/samsung_tv_token.txt", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            pass
        url = (
            f"ws://{HOST}:8001/api/v2/channels/samsung.remote.control"
            f"?name={base64.b64encode(b'VEGA').decode()}"
        )
        if token:
            url += f"&token={token}"
        ws = websocket.create_connection(url, timeout=10)
        msg = ws.recv()
        print("WS:", msg)
        if "connect" in msg:
            ws.send(json.dumps({
                "method": "ms.remote.control",
                "params": {"Cmd": "Click", "DataOfCmd": "KEY_ENTER",
                           "Option": "false", "TypeOfRemote": "SendRemoteKey"},
            }))
            print("Tecla KEY_ENTER enviada com sucesso.")
        ws.close()
    except Exception as error:
        print("WS falhou:", error)
