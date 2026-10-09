# -*- coding: utf-8 -*-
"""Sequência para autorizar: 1) acorda a TV via Cast, 2) conecta e pede popup."""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import pychromecast
import websocket

TV = "192.168.15.6"
NAME = "VEGA"
TOKEN_PATH = os.path.join("data", "security", "samsung_tv_token.txt")

# 1) Acorda a TV / coloca numa fonte ativa (o Cast mostra a home dele).
print("1) acordando a TV via Cast...")
casts, browser = pychromecast.get_chromecasts(timeout=10)
if not casts:
    print("TV nao encontrada via Cast — verifique se ela esta ligada")
    sys.exit(1)
cast = casts[0]
cast.wait(timeout=5)
print(f"   acordada: {cast.cast_info.friendly_name}")
browser.stop_discovery()
time.sleep(2)

# 2) Conecta no WebSocket e aguarda o popup.
token = ""
if os.path.exists(TOKEN_PATH):
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()

url = f"ws://{TV}:8001/api/v2/channels/samsung.remote.control?name={NAME.encode().hex()}"
if token:
    url += f"&token={token}"

print("2) conectando... OLHE PARA A TV e aceite 'Permitir'")
ws = websocket.create_connection(url, timeout=90)
deadline = time.time() + 90
while time.time() < deadline:
    try:
        msg = ws.recv()
    except websocket.WebSocketTimeoutException:
        print("   ...aguardando voce aceitar na TV")
        continue
    except Exception as e:
        print("recv parou:", e)
        break
    data = json.loads(msg)
    event = data.get("event", "")
    print("   evento:", event)
    if event == "ms.channel.connect":
        new_token = str((data.get("data") or {}).get("token") or "")
        if new_token and new_token != token:
            os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(new_token)
            print("TOKEN SALVO em", TOKEN_PATH)
        print("AUTORIZADO!")
        break
    if event == "ms.channel.unauthorized":
        print("   >>> aceite o popup na TV agora")
ws.close()
