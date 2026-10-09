# -*- coding: utf-8 -*-
"""Espera a TV voltar à rede (para validar Wake-on-LAN)."""
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
time.sleep(15)
for attempt in range(5):
    sock = socket.socket()
    sock.settimeout(3)
    r = sock.connect_ex(("192.168.15.6", 8001))
    sock.close()
    state = "ABERTA (TV ligada)" if r == 0 else "fechada"
    print(f"tentativa {attempt}: porta 8001 {state}")
    if r == 0:
        print("TV VOLTOU — Wake-on-LAN FUNCIONOU!")
        sys.exit(0)
    time.sleep(10)
print("TV não voltou. WoL pode estar desativado na TV (Configurações > Geral")
print("> Gerenciamento de dispositivo > 'Ligar por rede/Wake-on-LAN').")
sys.exit(1)
