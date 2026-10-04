# -*- coding: utf-8 -*-
"""Identidade dos dispositivos da VEGA.

O celular do Endriw é o Redmi Note 13 4G. A VEGA precisa saber disso para:

- tratar o aparelho como "meu celular" (e não "um celular qualquer");
- reconhecer o mesmo aparelho mesmo quando o IP ou a porta mudam;
- distinguir aparelhos diferentes quando outros forem conectados.

A identidade é ancorada no **serial do hardware**, que é estável. IP e porta
mudam a cada reconexão, então nunca são usados como identidade.

Os aparelhos ficam em `data/memory/devices.json` (mesmo registro usado pelo
resto da conectividade) — não há um segundo cadastro paralelo.
"""

from __future__ import annotations

import re

from core.connectivity.base import fold_text as _fold


class DeviceIdentity:
    """Resolve e mantém a identidade dos dispositivos conhecidos."""

    # Perfis conhecidos: casam por marca/modelo e definem o dono e o nome.
    # A ordem importa: o primeiro que casar vence.
    KNOWN_PROFILES = (
        {
            # Redmi Note 13 4G: o Android reporta o código do modelo
            # (23129RA5FL / sapphire) em vez do nome comercial.
            "match": (
                "redmi note 13", "redmi note13",
                "23129ra5fl", "2312dra50i", "23124ra7eo",
                "sapphire",
            ),
            "name": "Redmi Note 13 4G",
            "owner": "Endriw",
            "aliases": ["meu celular", "celular do endriw", "redmi"],
            "kind": "phone",
        },
        {
            "match": ("redmi", "xiaomi", "mi "),
            "name": "Celular Xiaomi",
            "owner": "",
            "aliases": ["celular xiaomi"],
            "kind": "phone",
        },
        {
            "match": ("sm-", "samsung", "galaxy"),
            "name": "Celular Samsung",
            "owner": "",
            "aliases": ["celular samsung", "galaxy"],
            "kind": "phone",
        },
        {
            "match": ("moto", "motorola"),
            "name": "Celular Motorola",
            "owner": "",
            "aliases": ["celular motorola", "moto"],
            "kind": "phone",
        },
        {
            "match": ("pixel", "google"),
            "name": "Celular Google Pixel",
            "owner": "",
            "aliases": ["celular pixel"],
            "kind": "phone",
        },
    )

    def __init__(self, registry=None):
        self.registry = registry

    # ==========================================================
    # RECONHECIMENTO
    # ==========================================================

    @staticmethod
    def _fold(value) -> str:
        return _fold(value)

    def match_profile(self, model="", brand="", serial=""):
        """Descobre o perfil conhecido a partir do modelo/marca/serial."""
        haystack = " ".join(
            self._fold(value) for value in (model, brand, serial) if value
        )
        if not haystack:
            return None

        for profile in self.KNOWN_PROFILES:
            for marker in profile["match"]:
                if marker in haystack:
                    return profile
        return None

    def identify(self, model="", brand="", serial="", address=""):
        """Monta a identidade completa de um aparelho.

        Retorna um dict com name, owner, kind, aliases e o serial estável.
        """
        profile = self.match_profile(model=model, brand=brand, serial=serial)

        if profile:
            name = profile["name"]
            owner = profile["owner"]
            kind = profile["kind"]
            aliases = list(profile["aliases"])
        else:
            name = str(model or "").strip() or "Celular"
            owner = ""
            kind = "phone"
            aliases = []

        if serial:
            aliases.append(serial)
        if address:
            aliases.append(address)

        return {
            "name": name,
            "owner": owner,
            "kind": kind,
            "aliases": aliases,
            "serial": str(serial or "").strip(),
            "model": str(model or "").strip(),
            "brand": str(brand or "").strip(),
        }

    # ==========================================================
    # PERSISTÊNCIA
    # ==========================================================

    def register(self, model="", brand="", serial="", address="", **extra):
        """Reconhece o aparelho e o guarda no registro compartilhado."""
        identity = self.identify(
            model=model, brand=brand, serial=serial, address=address,
        )
        if self.registry is None:
            return identity

        metadata = {
            "serial": identity["serial"],
            "model": identity["model"],
            "brand": identity["brand"],
            "owner": identity["owner"],
        }
        metadata.update({k: v for k, v in extra.items() if v is not None})

        self.registry.remember(
            identity["name"],
            kind=identity["kind"],
            address=address or identity["serial"],
            connector="phone",
            aliases=identity["aliases"],
            **metadata,
        )
        return identity

    def find_by_serial(self, serial):
        """Encontra o aparelho pelo serial, independente de IP/porta."""
        if not serial or self.registry is None:
            return None
        target = str(serial).strip()
        for device in self.registry.by_kind("phone"):
            if device.metadata.get("serial") == target:
                return device
            if target in (device.aliases or []):
                return device
        return None

    # ==========================================================
    # LINGUAGEM NATURAL
    # ==========================================================

    def describe(self, device=None) -> str:
        """Descreve o aparelho em linguagem natural.

        Ex.: "Esse é o seu Redmi Note 13 4G, Endriw."
        """
        if device is None:
            return "Não tenho nenhum celular identificado agora."

        owner = device.metadata.get("owner") or ""
        name = device.name or "celular"

        if owner:
            return f"Esse é o seu {name}, {owner}."
        return f"Esse é o {name}."

    def owner_of(self, device=None) -> str:
        if device is None:
            return ""
        return str(device.metadata.get("owner") or "")

    def is_primary(self, device=None) -> bool:
        """True quando o aparelho é o celular principal (o do Endriw)."""
        return bool(self.owner_of(device))

    def resolve_owner(self, text) -> str:
        """Extrai o dono mencionado na fala ("o celular do joão" -> "joão")."""
        match = re.search(
            r"celular\s+d[ao]\s+([a-zà-ú]+)", self._fold(text)
        )
        if not match:
            return ""
        return match.group(1)

    def find_by_owner(self, owner):
        """Encontra o aparelho de um dono específico."""
        if not owner or self.registry is None:
            return None
        target = self._fold(owner)
        for device in self.registry.by_kind("phone"):
            if self._fold(device.metadata.get("owner")) == target:
                return device
            for alias in device.aliases or []:
                if target in self._fold(alias):
                    return device
        return None