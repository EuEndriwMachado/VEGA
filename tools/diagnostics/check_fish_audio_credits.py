import os

import requests
from config.environment import load_vega_environment

load_vega_environment()
chave = os.getenv("FISH_API_KEY", "").strip()

if not chave:
    raise SystemExit("FISH_API_KEY não encontrada no arquivo .env")

try:
    response = requests.get(
        "https://api.fish.audio/wallet/self/api-credit",
        headers={"Authorization": "Bearer " + chave},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    account = str(data.get("user_id", "?"))[:12]
    print(f"conta {account}... | credit={data.get('credit')} | topup={data.get('cumulative_top_up')}")
except Exception as error:
    print(f"ERRO {str(error)[:120]}")
