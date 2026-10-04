# -*- coding: utf-8 -*-
"""Gerenciador de conectividade: porta única para celular, TV e afins.

Responsabilidades:

- manter os conectores registrados;
- traduzir um pedido em linguagem natural ("meu celular", "a TV da sala")
  para o dispositivo + conector corretos;
- rotear a ação e devolver sempre uma mensagem pronta para falar.

Não conhece o cérebro da VEGA nem o Gemini: quem chama é o `VegaBrain`.
Isso mantém a camada isolada e testável sem rede nem hardware.
"""

from __future__ import annotations

import re

from core.connectivity.base import ConnectorResult, fold_text as _fold
from core.connectivity.phone import PhoneConnector
from core.connectivity.registry import DeviceRegistry
from core.connectivity.tv import TVConnector


class ConnectivityManager:
    """Fachada dos conectores de dispositivo da VEGA."""

    # Palavras que indicam "o celular" / "a TV" sem nome próprio.
    _PHONE_HINTS = ("celular", "telefone", "smartphone", "android", "meu phone")
    _TV_HINTS = ("tv", "televisao", "televisão", "televisor", "chromecast", "sala")

    def __init__(self, registry=None, connectors=None):
        # Atenção: usar `is None` (e não `or`), porque DeviceRegistry define
        # __len__ — um registro vazio é falsy e seria substituído por um novo.
        self.registry = registry if registry is not None else DeviceRegistry()
        if connectors is not None:
            self.connectors = {connector.name: connector for connector in connectors}
        else:
            self.connectors = {
                "phone": PhoneConnector(registry=self.registry),
                "tv": TVConnector(registry=self.registry),
            }

    # ==========================================================
    # ESTADO
    # ==========================================================

    def status_report(self) -> str:
        """Resumo legível do que está disponível (usado no diagnóstico)."""
        lines = ["Estado da conectividade:"]
        for name, connector in self.connectors.items():
            status = connector.status()
            marker = "disponível" if status.available else "indisponível"
            lines.append(f"- {name}: {marker} — {status.detail}")
        return "\n".join(lines)

    def available_connectors(self) -> list:
        return [
            name for name, connector in self.connectors.items()
            if connector.is_available()
        ]

    # ==========================================================
    # ROTEAMENTO
    # ==========================================================

    def _guess_kind(self, text: str) -> str:
        folded = _fold(text)
        if any(hint in folded for hint in self._PHONE_HINTS):
            return "phone"
        if any(hint in folded for hint in self._TV_HINTS):
            return "tv"
        return ""

    def _guess_device_name(self, text: str, kind: str) -> str:
        """Extrai um nome próprio de dispositivo, se houver.

        Ex.: "joga na tv da sala" -> "sala"; "manda pro celular do joao" ->
        "joao". Sem nome explícito, devolve "" (o conector usa o padrão).
        """
        folded = _fold(text)

        patterns = {
            "tv": (
                r"\b(?:tv|televisao|televisor)\s+(?:d[aeo]s?\s+)?([a-z0-9]+)"
            ),
            "phone": r"\bcelular\s+(?:d[aeo]s?\s+)?([a-z0-9]+)",
        }
        pattern = patterns.get(kind)
        if not pattern:
            return ""

        match = re.search(pattern, folded)
        if not match:
            return ""

        candidate = match.group(1)
        # Ignora palavras que não são nome (artigos, verbos comuns).
        stopwords = {
            "da", "do", "de", "na", "no", "para", "pra", "pro", "a", "o",
            "meu", "minha", "quando", "onde", "ligada", "ligado", "desligada",
            "desligado", "agora", "tambem", "também",
        }
        return "" if candidate in stopwords else candidate

    def route(self, action: str, text: str = "", **kwargs) -> ConnectorResult:
        """Roteia uma ação para o conector certo.

        `text` é a fala original do usuário (usada para inferir o alvo).
        Retorna sempre um ConnectorResult (nunca levanta).
        """
        kind = kwargs.pop("kind", "") or self._guess_kind(text)

        if not kind:
            return ConnectorResult.failure(
                "Você quer fazer isso no celular ou na TV?"
            )

        connector = self.connectors.get(kind)
        if connector is None:
            return ConnectorResult.failure(
                f"Ainda não tenho um conector para {kind}."
            )

        if "device" not in kwargs and "tv" not in kwargs:
            name = self._guess_device_name(text, kind)
            if name:
                if kind == "tv":
                    kwargs["tv"] = name
                else:
                    kwargs["device"] = name

        return connector.execute(action, **kwargs)

    # ==========================================================
    # DESCOBERTA
    # ==========================================================

    def discover_all(self) -> dict:
        """Procura dispositivos em todos os conectores disponíveis."""
        found = {}
        for name, connector in self.connectors.items():
            if not connector.is_available():
                found[name] = []
                continue
            try:
                found[name] = connector.discover()
            except Exception:
                found[name] = []
        return found

    def describe_devices(self) -> str:
        """Lista os dispositivos conhecidos, por tipo."""
        devices = self.registry.all()
        if not devices:
            return "Ainda não conheço nenhum dispositivo seu."

        by_kind = {}
        for device in devices:
            by_kind.setdefault(device.kind, []).append(device.name)

        parts = []
        for kind, names in by_kind.items():
            label = {"phone": "celular", "tv": "TV"}.get(kind, kind)
            parts.append(f"{label}: {', '.join(names)}")
        return "Dispositivos conhecidos — " + "; ".join(parts) + "."

    def describe_primary(self) -> str:
        """Descreve o celular principal (o do usuário), se conhecido."""
        from core.connectivity.identity import DeviceIdentity
        identity = DeviceIdentity(registry=self.registry)
        for device in self.registry.by_kind("phone"):
            if identity.is_primary(device):
                return identity.describe(device)
        return "Não tenho um celular principal identificado."