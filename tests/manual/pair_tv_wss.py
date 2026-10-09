# -*- coding: utf-8 -*-
"""Pareamento via WSS na porta 8002 (modelos Samsung novos, ex. DU7700)."""
import base64
import json
import os
import ssl
import sys
import time

import websocket

sys.stdout.reconfigure(encoding="utf-8")

HOST = "192.168.15.6"
TOKEN_PATH = "data/security/samsung_tv_token.txt"

token = ""
try:
    with open(TOKEN_PATH, encoding="utf-8") as file:
        token = file.read().strip()
except OSError:
    pass

url = (
    f"wss://{HOST}:8002/api/v2/channels/samsung.remote.control"
    f"?name={base64.b64encode(b'VEGA').decode()}"
)
if token:
    url += f"&token={token}"

print("Conectando via WSS na porta 8002 ...")
print(">>> OLHE A TV AGORA: aceite o popup de autorizacao se aparecer. <<<")
try:
    ws = websocket.create_connection(
        url, timeout=70, sslopt={"cert_reqs": ssl.CERT_NONE}
    )
except Exception as error:
    print("Falha na conexao:", error)
    sys.exit(1)

print("Canal aberto. Aguardando autorizacao (60s)...")
deadline = time.time() + 60
authorized = False
while time.time() < deadline:
    try:
        message = ws.recv()
    except Exception as error:
        print("Canal fechou:", error)
        break
    print("<<", message)
    if "connect" in message:
        authorized = True
        new_token = str((json.loads(message).get("data") or {}).get("token") or "")
        if new_token:
            os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
            with open(TOKEN_PATH, "w", encoding="utf-8") as file:
                file.write(new_token)
            print("TOKEN SALVO em", TOKEN_PATH)
        break
    if "unauthorized" in message:
        print("(ainda sem autorizacao — popup pendente ou bloqueado)")

try:
    ws.close()
except Exception:
    pass
print("AUTORIZADO" if authorized else "NAO AUTORIZADO")
sys.exit(0 if authorized else 1)
