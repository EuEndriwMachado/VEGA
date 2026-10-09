# -*- coding: utf-8 -*-
"""Varre app_ids da Samsung (numéricos e org.tizen.*) na REST da TV."""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HOST = "192.168.15.6"

found = []


def check(app_id):
    try:
        req = urllib.request.Request(
            f"http://{HOST}:8001/api/v2/applications/{app_id}",
            headers={"User-Agent": "Chrome/36"},
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
        found.append(data)
        print("OK", data.get("id"), "|", data.get("name"))
        return True
    except Exception:
        return False


# faixa numérica plausível de apps Samsung 2016-2025
for n in range(11090000000, 11090000000 + 0):
    pass

# ids conhecidos publicamente (bares) + guesses próximos do YouTube/Spotify
candidates = []
for base in (1112, 1110, 1109, 3201, 3201606, 3201512):
    for i in range(0, 20):
        candidates.append(f"{base}{i:09d}")

for i in range(110910000, 110910100):
    candidates.append(str(11091000000 + i))

for cid in candidates:
    check(cid)

print("---", len(found), "apps encontrados")
