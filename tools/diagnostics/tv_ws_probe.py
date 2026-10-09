# -*- coding: utf-8 -*-
"""Testa se a TV Samsung aceita conexão WebSocket do controle remoto."""
import sys

sys.stdout.reconfigure(encoding="utf-8")

TV = "192.168.15.6"
import socket

for port in (8001, 8002):
    try:
        with socket.create_connection((TV, port), timeout=2):
            print(f"porta {port}: ABERTA (WebSocket Samsung)")
    except OSError as error:
        print(f"porta {port}: fechada ({error})")
