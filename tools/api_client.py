from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests

from config.tooling import TOOL_TIMEOUT_SECONDS, TOOL_USER_AGENT


class ExternalToolClient:
    """Cliente HTTP seguro para ferramentas externas da VEGA."""

    def __init__(self, timeout_seconds: float = TOOL_TIMEOUT_SECONDS):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": TOOL_USER_AGENT})
        self.timeout_seconds = timeout_seconds

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Timeout ao acessar {url}")
        except requests.exceptions.HTTPError as exc:
            raise ValueError(f"Erro HTTP ao acessar {url}: {exc}")
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"API indisponível ou não acessível: {exc}")

    def get_text(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> str:
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.text
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Timeout ao acessar {url}")
        except requests.exceptions.HTTPError as exc:
            raise ValueError(f"Erro HTTP ao acessar {url}: {exc}")
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"API indisponível ou não acessível: {exc}")
