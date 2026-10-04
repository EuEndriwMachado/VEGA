# -*- coding: utf-8 -*-
"""Monitor de conexão do celular (watchdog).

O MIUI desliga a depuração sem fio sozinho (economia de bateria, queda de
Wi-Fi). Em vez de exigir que o usuário reconecte na mão, este monitor
verifica o estado periodicamente e reconecta quando o aparelho reaparece
na rede.

Características:
- roda em thread própria, sem bloquear a VEGA;
- só age quando já existe um celular conhecido (não fica varrendo à toa);
- avisa o usuário apenas em transições (caiu / voltou), sem poluir a fala;
- nunca levanta: falhas de rede são registradas e ignoradas.
"""

from __future__ import annotations

import threading
import time


class PhoneConnectionMonitor:
    """Mantém o celular conectado, reconectando quando ele cair."""

    def __init__(self, wireless=None, registry=None, interval=90.0, on_event=None,
                 startup_delay=10.0):
        self.wireless = wireless
        self.registry = registry
        self.interval = float(interval or 90.0)
        self.startup_delay = float(startup_delay or 0.0)
        self.on_event = on_event

        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

        self._connected = False
        self._last_check = 0.0

    # ==========================================================
    # CICLO DE VIDA
    # ==========================================================

    def start(self):
        """Inicia o monitor em segundo plano (idempotente)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="VegaPhoneMonitor", daemon=True,
            )
            self._thread.start()
        return True

    def stop(self):
        with self._lock:
            self._stop.set()
            self._thread = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    # ==========================================================
    # VERIFICAÇÃO
    # ==========================================================

    def check_now(self) -> dict:
        """Verifica o estado e tenta reconectar. Retorna um resumo."""
        if self.wireless is None:
            return {"connected": False, "changed": False, "message": ""}

        try:
            devices = self.wireless.connected_devices()
        except Exception:
            devices = []

        connected = bool(devices)

        # Estava desconectado e voltou? Tenta reconectar.
        if not connected and self._had_known_phone():
            try:
                messages = self.wireless.reconnect_known()
                if messages:
                    devices = self.wireless.connected_devices()
                    connected = bool(devices)
            except Exception:
                pass

        changed = connected != self._connected
        self._connected = connected
        self._last_check = time.time()

        return {
            "connected": connected,
            "changed": changed,
            "devices": devices,
        }

    def _had_known_phone(self) -> bool:
        """Só tenta reconectar se algum celular já foi pareado."""
        if self.registry is None:
            return False
        try:
            return any(
                device.metadata.get("wireless")
                for device in self.registry.by_kind("phone")
            )
        except Exception:
            return False

    # ==========================================================
    # LOOP
    # ==========================================================

    def _loop(self):
        # Espera inicial: deixa a VEGA terminar de subir antes da primeira
        # verificação (configurável para testes rápidos).
        if self.startup_delay and self._stop.wait(self.startup_delay):
            return

        while not self._stop.is_set():
            try:
                result = self.check_now()
                if result["changed"] and callable(self.on_event):
                    try:
                        self.on_event(result)
                    except Exception:
                        pass
            except Exception:
                # O monitor nunca deve derrubar a VEGA.
                pass

            if self._stop.wait(self.interval):
                return

    # ==========================================================
    # ESTADO
    # ==========================================================

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self.is_running,
                "connected": self._connected,
                "last_check": self._last_check,
                "interval": self.interval,
            }