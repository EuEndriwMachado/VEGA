# -*- coding: utf-8 -*-
"""Autoriza a VEGA como controle remoto da TV e lista os apps instalados.

OLHE PARA A TV ao rodar: se aparecer o popup "Permitir VEGA", aceite
(de preferencia "Permitir sempre") — o token fica salvo e nao pede mais.
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import websocket

TV = "192.168.15.6"
NAME = "VEGA"
TOKEN_PATH = os.path.join("data", "security", "samsung_tv_token.txt")

token = ""
if os.path.exists(TOKEN_PATH):
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()

url = f"ws://{TV}:8001/api/v2/channels/samsung.remote.control?name={NAME.encode().hex()}"
if token:
    url += f"&token={token}"

print("conectando... OLHE PARA A TV e aceite o popup se aparecer")
ws = websocket.create_connection(url, timeout=60)

deadline = time.time() + 60
while time.time() < deadline:
    try:
        msg = ws.recv()
    except websocket.WebSocketTimeoutException:
        print("...aguardando autorizacao (aceite na TV)")
        continue
    except Exception as e:
        print("recv parou:", e)
        break
    data = json.loads(msg)
    event = data.get("event", "")
    print("evento:", event, str(data.get("data"))[:100])
    if event == "ms.channel.connect":
        new_token = str((data.get("data") or {}).get("token") or "")
        if new_token and new_token != token:
            os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(new_token)
            print("TOKEN SALVO em", TOKEN_PATH)
        print("AUTORIZADO. Listando apps...")
        ws.send(json.dumps({
            "method": "ms.channel.emit",
            "params": {"event": "ed_installedApps.get", "to": "host"},
        }))
        dl = time.time() + 10
        while time.time() < dl:
            try:
                m2 = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            d2 = json.loads(m2)
            payload = d2.get("data", {})
            if d2.get("event") == "ed.installedApp.get":
                apps = []
                if isinstance(payload, dict):
                    apps = payload.get("data") or []
                elif isinstance(payload, list):
                    apps = payload
                print(f"--- {len(apps)} apps ---")
                for a in apps[:40]:
                    if isinstance(a, dict):
                        print(f"  {str(a.get('appId','?')):34s} {a.get('name','?')}")
                break
        break
    if event == "ms.channel.unauthorized":
        print(">>> nao autorizado ainda - aceite o popup na TV")

ws.close()
