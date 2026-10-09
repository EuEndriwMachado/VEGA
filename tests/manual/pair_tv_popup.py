# -*- coding: utf-8 -*-
"""Pareamento com a TV Samsung: fica esperando o popup de autorização.

Rode com a TV LIGADA e você de olho na tela. Se aparecer o popup de
"Permitir dispositivo", aceite. Também mostra o diagnóstico da via REST
(que NÃO precisa de token) para abrir apps nativos.
"""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import base64
import urllib.request
import websocket

HOST = "192.168.15.6"
TOKEN_PATH = "data/security/samsung_tv_token.txt"


def probe_rest():
    url = f"http://{HOST}:8001/api/v2/applications"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        apps = data.get("data") or []
        print(f"REST OK: {len(apps)} apps instalados.")
        for item in apps[:15]:
            print(" -", item.get("appId"), "|", item.get("name"))
        return True
    except Exception as error:
        print("REST falhou:", error)
        return False


def list_apps_rest(host):
    """Tenta listar apps por todas as vias conhecidas."""
    # 1) REST moderna
    try:
        with urllib.request.urlopen(f"http://{host}:8001/api/v2/applications",
                                    timeout=15) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        apps = data.get("data") or []
        print(f"[REST /api/v2/applications] {len(apps)} apps")
        for item in apps:
            print(" -", item.get("appId"), "|", item.get("name"))
        return True
    except Exception as error:
        print("[REST /api/v2/applications] falhou:", error)

    # 2) DIAL legado (pode exigir token -> 401)
    for app in ("YouTube", "Netflix", "Spotify", "Browser"):
        req = urllib.request.Request(
            f"http://{host}:8001/ws/apps/{app}",
            headers={"User-Agent": "Chrome/36"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read(200).decode("utf-8", "ignore")
            print(f"[DIAL /ws/apps/{app}] OK: {body[:120]}")
        except Exception as error:
            print(f"[DIAL /ws/apps/{app}]", error)
    return False


def pair():
    name = base64.b64encode(b"VEGA").decode()
    url = f"ws://{HOST}:8001/api/v2/channels/samsung.remote.control?name={name}"
    token = ""
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as file:
            token = file.read().strip()
    except OSError:
        pass
    if token:
        url += f"&token={token}"

    print("Conectando ao canal de controle remoto...")
    print(">>> OLHE A TV: um popup de autorizacao pode aparecer. Aceite. <<<")
    ws = websocket.create_connection(url, timeout=90)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            data = json.loads(ws.recv())
        except Exception as error:
            print("Canal fechado:", error)
            break
        event = data.get("event", "")
        print("evento:", event)
        if event == "ms.channel.connect":
            new_token = str((data.get("data") or {}).get("token") or "")
            if new_token:
                with open(TOKEN_PATH, "w", encoding="utf-8") as file:
                    file.write(new_token)
                print("TOKEN SALVO em", TOKEN_PATH)
            print("AUTORIZADO. VEGA agora e um controle remoto da TV.")
            ws.close()
            return 0
        if event == "ms.channel.unauthorized":
            print("Ainda nao autorizado. Se nenhum popup apareceu, use:")
            print("Configuracoes > Conexao > Gerenciador de dispositivo externo")
            print("> Gerenciamento de dispositivos > permita 'VEGA'.")
    ws.close()
    print("Tempo esgotado sem autorizacao.")
    return 1


if __name__ == "__main__":
    print("=== Diagnostico de apps (REST + DIAL legado) ===")
    list_apps_rest(HOST)
    print()
    print("=== Pareamento WebSocket (teclas, exige popup) ===")
    sys.exit(pair())
