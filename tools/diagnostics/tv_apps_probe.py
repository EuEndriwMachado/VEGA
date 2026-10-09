# -*- coding: utf-8 -*-
"""Lista os app_ids disponiveis na TV (via DIAL/descricao do dispositivo)."""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import requests

TV = "192.168.15.6"
for port in (8008, 8009):
    try:
        response = requests.get(f"http://{TV}:{port}/ssdp/device-desc.xml", timeout=5)
        print(f"porta {port}:", response.status_code)
        print(response.text[:1500])
        break
    except Exception as error:
        print(f"porta {port} falha:", error)
