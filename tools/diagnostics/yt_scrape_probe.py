aa# -*- coding: utf-8 -*-
"""Teste rapido: busca do YouTube sem API key resolve videoId?"""
import re
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

query = sys.argv[1] if len(sys.argv) > 1 else "lofi hip hop"
response = requests.get(
    "https://www.youtube.com/results",
    params={"search_query": query},
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=10,
)
match = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', response.text)
print("videoId:", match.group(1) if match else None)
title = re.search(r'"videoRenderer":\{"videoId":"[\w-]{11}".{0,400}?"title":\{"runs":\[\{"text":"([^"]{0,80})', response.text)
print("titulo:", title.group(1) if title else None)
