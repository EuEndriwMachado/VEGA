# -*- coding: utf-8 -*-
"""Testa quick_play do YouTube na TV: deve abrir o APP NATIVO e tocar."""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import pychromecast
from pychromecast.quick_play import quick_play

casts, browser = pychromecast.get_chromecasts(timeout=10)
if not casts:
    print("TV nao encontrada")
    sys.exit(1)
cast = casts[0]
cast.wait(timeout=5)
print("TV:", cast.cast_info.friendly_name)
browser.stop_discovery()

# Busca "lofi" -> pega o primeiro video_id
import json
import re
import urllib.request

q = "lofi hip hop"
html = urllib.request.Request(
    f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}",
    headers={"User-Agent": "Mozilla/5.0"},
)
data = urllib.request.urlopen(html, timeout=10).read().decode("utf-8", "ignore")
match = re.search(r'"videoId":"([\w-]{11})"', data)
if not match:
    print("sem resultado de busca")
    sys.exit(1)
video_id = match.group(1)
print("video_id:", video_id)

print("quick_play youtube...")
quick_play(cast, "youtube", {"media_id": video_id}, timeout=30.0)
time.sleep(4)
print("app ativo:", cast.app_id, "| status:", cast.status.status_text)
