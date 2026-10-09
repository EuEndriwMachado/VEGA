# -*- coding: utf-8 -*-
"""Autoriza via protocolo legado Samsung (porta 8000,.popup confiável)."""
import base64
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

TV = "192.168.15.6"
NAME = "VEGA"

# --- Passo 1: pedir autorização (popup aparece na TV) ---
payload = (
    chr(0x00) + chr(0x14) + "0" * 14
    + base64.b64encode(NAME.encode()).decode()
    + chr(0x0A)
    + "192.168.15.7"
    + chr(0x0A)
    + base64.b64encode(NAME.encode()).decode()
)
sock = socket.create_connection((TV, 8000), timeout=30)
print("enviando pedido de autorizacao... OLHE PARA A TV")
sock.send(payload.encode())
sock.settimeout(60)
try:
    resp = sock.recv(1024)
    code = resp[0]
    if code == 0x00:
        print("autorizado (sem token)")
    elif code == 0x01:
        token = resp[1:].decode(errors="ignore")
        print("TOKEN:", token)
        with open("data/security/samsung_tv_token.txt", "w", encoding="utf-8") as f:
            f.write(token)
        print("token salvo em data/security/samsung_tv_token.txt")
    elif code == 0x02:
        print("ACEITE O POPUP NA TV (aguardando...)")
        resp2 = sock.recv(1024)
        token = resp2[1:].decode(errors="ignore")
        print("TOKEN:", token)
        with open("data/security/samsung_tv_token.txt", "w", encoding="utf-8") as f:
            f.write(token)
        print("token salvo!")
except socket.timeout:
    print("popup nao aceito a tempo")
finally:
    sock.close()
