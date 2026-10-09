# -*- coding: utf-8 -*-
"""Testa o desligamento real da TV Samsung via WebSocket (KEY_POWER)."""
import json
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import websocket

TV = "192.168.15.6"
NAME = "VEGA"

url = f"ws://{TV}:8001/api/v2/channels/samsung.remote.control?name={NAME.encode().hex()}"
print("conectando:", url)
ws = websocket.create_connection(url, timeout=5)
print("conectado!")

# Escutar resposta em background
events = []

def listen():
    try:
        while True:
            message = ws.recv()
            events.append(message)
            data = json.loads(message)
            if data.get("event") == "ms.channel.connect":
                print("canal conectado")
            if data.get("event") == "ms.key" or "KEY" in str(data.get("data", {})):
                print("evento de tecla:", str(data)[:120])
    except Exception as error:
        print("listen parou:", error)

threading.Thread(target=listen, daemon=True).start()
time.sleep(1)

ws.send(json.dumps({
    "method": "ms.remote.control",
    "params": {
        "Cmd": "Click",
        "DataOfCmd": "KEY_POWER",
        "Option": "false",
        "TypeOfRemote": "SendRemoteKey",
    },
}))
print("KEY_POWER enviada.")
time.sleep(3)
print("eventos recebidos:", len(events))
for event in events[:4]:
    print("-", str(event)[:150])
ws.close()
