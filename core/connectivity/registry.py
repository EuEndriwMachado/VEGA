# -*- coding: utf-8 -*-
"""Registro de dispositivos conhecidos da VEGA (celular, TV, etc.).

Fonte única de verdade sobre os dispositivos do usuário. É persistido em
`data/memory/devices.json`, seguindo o mesmo padrão de armazenamento da
memória existente (JSON + escrita atômica simples), sem criar um segundo
sistema de memória paralelo.

Uso típico:
    registry.remember("celular", kind="phone", address="192.168.0.10")
    registry.find("celular")  -> Device | None
    registry.by_kind("tv")    -> [Device, ...]
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime


def _normalize_key(value) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Device:
    """Um dispositivo conhecido da VEGA."""

    name: str
    kind: str = "unknown"            # "phone", "tv", "tablet", "speaker", ...
    address: str = ""                # IP, hostname, id de cast, serial adb...
    connector: str = ""              # conector responsável: "phone", "tv"
    aliases: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    last_seen: str = ""

    def matches(self, query: str) -> bool:
        key = _normalize_key(query)
        if not key:
            return False
        candidates = [self.name] + list(self.aliases)
        for candidate in candidates:
            normalized = _normalize_key(candidate)
            if not normalized:
                continue
            if key == normalized or key in normalized or normalized in key:
                return True
        return False

    def as_dict(self) -> dict:
        return asdict(self)


class DeviceRegistry:
    """Guarda e recupera dispositivos, com persistência em JSON."""

    def __init__(self, path: str = "data/memory/devices.json"):
        self.path = path
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {}
        self._load()

    # --------------------------------------------------------------
    # Persistência
    # --------------------------------------------------------------

    def _load(self):
        try:
            if not os.path.exists(self.path):
                return
            with open(self.path, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        for key, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            try:
                self._devices[key] = Device(
                    name=str(entry.get("name", key)),
                    kind=str(entry.get("kind", "unknown")),
                    address=str(entry.get("address", "")),
                    connector=str(entry.get("connector", "")),
                    aliases=list(entry.get("aliases", []) or []),
                    metadata=dict(entry.get("metadata", {}) or {}),
                    last_seen=str(entry.get("last_seen", "")),
                )
            except (TypeError, ValueError):
                continue

    def _save(self):
        payload = {key: device.as_dict() for key, device in self._devices.items()}
        folder = os.path.dirname(self.path)
        try:
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
        except OSError:
            # Persistência é best-effort: a VEGA continua funcionando em memória.
            pass

    # --------------------------------------------------------------
    # API
    # --------------------------------------------------------------

    def remember(self, name, kind="unknown", address="", connector="",
                 aliases=None, **metadata) -> Device:
        """Registra ou atualiza um dispositivo."""
        device = Device(
            name=str(name).strip(),
            kind=str(kind or "unknown").strip().lower(),
            address=str(address or "").strip(),
            connector=str(connector or "").strip().lower(),
            aliases=[str(a).strip() for a in (aliases or []) if str(a).strip()],
            metadata=dict(metadata),
            last_seen=datetime.now().isoformat(timespec="seconds"),
        )
        key = _normalize_key(device.name)
        with self._lock:
            self._devices[key] = device
            self._save()
        return device

    def find(self, query, kind=None) -> Device | None:
        """Encontra um dispositivo por nome/apelido, opcionalmente por tipo."""
        key = _normalize_key(query)
        with self._lock:
            devices = list(self._devices.values())

        if kind:
            kind_key = str(kind).lower()
            devices = [d for d in devices if d.kind == kind_key]

        if not key:
            return devices[0] if devices else None

        # Correspondência exata primeiro, depois aproximada.
        for device in devices:
            if _normalize_key(device.name) == key:
                return device
        for device in devices:
            if device.matches(query):
                return device
        return None

    def by_kind(self, kind) -> list:
        kind_key = str(kind or "").lower()
        with self._lock:
            return [d for d in self._devices.values() if d.kind == kind_key]

    def all(self) -> list:
        with self._lock:
            return list(self._devices.values())

    def forget(self, query) -> bool:
        device = self.find(query)
        if device is None:
            return False
        with self._lock:
            self._devices.pop(_normalize_key(device.name), None)
            self._save()
        return True

    def __len__(self):
        return len(self._devices)