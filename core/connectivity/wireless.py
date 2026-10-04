# -*- coding: utf-8 -*-
"""Pareamento e conexão de celular sem cabo (ADB over Wi-Fi).

Dois modos suportados:

1. **Android 11+ (pareamento por código).** O usuário abre "Depuração sem
   fio" no celular, escolhe "Parear com código" e informa o IP:porta e o
   código de 6 dígitos. É o modo do seu Redmi (Android 15).
2. **Android 10 e anteriores (via USB uma única vez).** Conecta por cabo,
   roda `adb tcpip 5555` e depois conecta por IP. O cabo é necessário só
   uma vez.

O pareamento é salvo em `data/memory/devices.json` (reaproveita o registro
existente), então conexões seguintes são automáticas.
"""

from __future__ import annotations

import re
import shutil
import socket

from core.connectivity.base import run_external


class WirelessADB:
    """Cuida do pareamento e da reconexão sem fio do celular."""

    PAIR_TIMEOUT = 25
    CONNECT_TIMEOUT = 15

    def __init__(self, adb_path="", registry=None, runner=None):
        self._adb_path = adb_path
        self.registry = registry
        self._runner = runner

    # ==========================================================
    # LOCALIZAÇÃO DO ADB
    # ==========================================================

    def adb(self) -> str:
        """Devolve o caminho do adb (reutiliza a mesma busca do PhoneConnector)."""
        if self._adb_path:
            return self._adb_path

        found = shutil.which("adb")
        if found:
            self._adb_path = found
            return found

        import os
        candidates = [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Android", "Sdk", "platform-tools", "adb.exe",
            ),
            os.path.join(
                os.environ.get("USERPROFILE", ""),
                "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe",
            ),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                self._adb_path = candidate
                return candidate

        return ""

    def _run(self, args, timeout=None):
        """Executa um comando externo (delega para o utilitário compartilhado)."""
        return run_external(args, timeout=timeout or self.CONNECT_TIMEOUT, runner=self._runner)

    # ==========================================================
    # PARECIMENTO (Android 11+)
    # ==========================================================

    @staticmethod
    def parse_pair_input(value) -> tuple:
        """Aceita 'IP:porta' ou o endereço com espaço e valida o formato.

        Retorna (host, porta) ou ('', 0) se inválido. Cada octeto do IP
        precisa estar entre 0 e 255.
        """
        text = str(value or "").strip()
        # Aceita "192.168.15.4:37000" ou "192.168.15.4 37000".
        text = text.replace(" ", ":")
        match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})", text)
        if not match:
            return "", 0

        host = match.group(1)
        port = int(match.group(2))

        # Valida cada octeto (0-255) e a porta (1-65535).
        if any(int(part) > 255 for part in host.split(".")):
            return "", 0
        if not (1 <= port <= 65535):
            return "", 0

        return host, port

    @staticmethod
    def validate_code(code) -> bool:
        """O código de pareamento tem exatamente 6 dígitos."""
        return bool(re.fullmatch(r"\d{6}", str(code or "").strip()))

    def pair(self, address, code):
        """Pareia com o celular. Retorna (ok, mensagem)."""
        adb = self.adb()
        if not adb:
            return False, "O ADB não está instalado."

        host, port = self.parse_pair_input(address)
        if not host:
            return False, (
                "Endereço inválido. No celular, use o formato 192.168.15.4:37000."
            )

        if not self.validate_code(code):
            return False, "O código de pareamento tem 6 dígitos."

        ok, output, error = self._run(
            [adb, "pair", f"{host}:{port}", str(code).strip()],
            timeout=self.PAIR_TIMEOUT,
        )

        combined = f"{output} {error}".lower()
        if ok and ("successfully paired" in combined or "paired" in combined):
            if self.registry is not None:
                self.registry.remember(
                    f"Celular {host}", kind="phone", address=host,
                    connector="phone", aliases=[host], wireless=True,
                )
            return True, f"Pareado com o celular em {host}."

        if "cannot connect" in combined or "failed to connect" in combined:
            return False, (
                "Não consegui conectar. Verifique se o IP:porta está certo e "
                "se o celular está na mesma rede Wi-Fi."
            )
        if "wrong password" in combined or "authentication" in combined:
            return False, "Código de pareamento incorreto. Confira os 6 dígitos."

        return False, f"Pareamento falhou: {error or output or 'erro desconhecido'}"

    # ==========================================================
    # CONEXÃO
    # ==========================================================

    def connect(self, address, port=5555):
        """Conecta a um celular já pareado. Retorna (ok, mensagem)."""
        adb = self.adb()
        if not adb:
            return False, "O ADB não está instalado."

        target = f"{address}:{port}" if ":" not in str(address) else str(address)
        ok, output, error = self._run([adb, "connect", target])

        combined = f"{output} {error}".lower()
        if ok and "connected to" in combined:
            # Guarda a porta efetiva para reconexões futuras.
            if self.registry is not None:
                host = target.split(":")[0]
                device = self.registry.find(host, kind="phone")
                if device is not None:
                    device.metadata["wireless"] = True
                    device.metadata["port"] = int(target.split(":")[-1])
                    self.registry.remember(
                        device.name, kind="phone", address=host,
                        connector="phone",
                        aliases=list(device.aliases) + [host],
                        **device.metadata,
                    )
            return True, f"Conectado ao celular em {target}."
        if "already connected" in combined:
            return True, f"O celular em {target} já estava conectado."
        if "failed" in combined or "cannot" in combined:
            return False, (
                f"Não consegui conectar em {target}. Se o celular reiniciou, "
                "talvez seja preciso parear de novo."
            )
        return False, f"Conexão falhou: {error or output or 'erro desconhecido'}"

    # ==========================================================
    # MODO ANTIGO (Android 10-): USB uma vez, depois sem fio
    # ==========================================================

    def enable_tcpip(self, port=5555):
        """Ativa o modo TCP/IP no celular conectado por USB (uma única vez)."""
        adb = self.adb()
        if not adb:
            return False, "O ADB não está instalado."

        ok, output, error = self._run([adb, "tcpip", str(port)], timeout=20)
        if ok and "restarting" in f"{output} {error}".lower():
            return True, f"Modo sem fio ativado na porta {port}."
        if ok:
            return True, f"Modo sem fio ativado na porta {port}."
        return False, f"Não consegui ativar o modo sem fio: {error or output}"

    # ==========================================================
    # DESCOBERTA E ESTADO
    # ==========================================================

    def current_ip(self):
        """IP Wi-Fi do celular conectado por USB (para o modo antigo)."""
        adb = self.adb()
        if not adb:
            return ""
        ok, output, _error = self._run(
            [adb, "shell", "ip", "route"], timeout=10
        )
        if not ok:
            return ""
        match = re.search(r"src\s+(\d{1,3}(?:\.\d{1,3}){3})", output)
        return match.group(1) if match else ""

    def connected_devices(self) -> list:
        """Lista dispositivos conectados (USB e sem fio)."""
        adb = self.adb()
        if not adb:
            return []
        ok, output, _error = self._run([adb, "devices"], timeout=10)
        if not ok:
            return []

        devices = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def reconnect_known(self) -> list:
        """Tenta reconectar os celulares sem fio já conhecidos.

        Estratégia em ordem: usa a porta salva, tenta o mDNS (descobre
        automaticamente) e, por fim, a porta padrão 5555.
        Retorna a lista de mensagens de resultado (para log/voz).
        """
        messages = []
        if self.registry is None:
            return messages

        # 1. Tenta primeiro via mDNS: é o caminho mais confiável no
        #    Android 11+, porque a porta muda a cada reconexão.
        if self.connect_via_mdns():
            messages.append("Celular reconectado sem fio.")

        for device in self.registry.by_kind("phone"):
            if not device.metadata.get("wireless"):
                continue
            host = device.address
            if not host or ":" in host:
                continue

            # Já conectou via mDNS? Não repete.
            if self._is_connected(host):
                continue

            port = int(device.metadata.get("port") or 5555)
            ok, message = self.connect(host, port)
            if not ok and port != 5555:
                ok, message = self.connect(host, 5555)
            messages.append(message if ok else f"{device.name}: {message}")
        return messages

    def _is_connected(self, host) -> bool:
        """Confere se o host já aparece como conectado no adb."""
        for device in self.connected_devices():
            if device == host or device.startswith(f"{host}:"):
                return True
        return False

    def connect_via_mdns(self) -> bool:
        """Conecta usando o mDNS do ADB (descobre o serviço automaticamente).

        No Android 11+, `adb mdns services` lista o serviço TLS já pareado,
        e a conexão por ele dispensa informar IP e porta.
        """
        adb = self.adb()
        if not adb:
            return False

        ok, output, _error = self._run([adb, "mdns", "services"], timeout=12)
        if not ok or not output:
            return False

        for line in output.splitlines():
            # Formato: "adb-<serial>-<id> _adb-tls-connect._tcp <ip>:<porta>"
            if "_adb-tls-connect" not in line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            service = parts[0]
            ok2, _out2, _err2 = self._run([adb, "connect", service], timeout=12)
            if ok2:
                return True
        return False

    @staticmethod
    def is_same_network(host, timeout=1.5) -> bool:
        """Confirma que o celular responde na rede (sem abrir socket ADB)."""
        try:
            socket.create_connection((host, 5555), timeout=timeout).close()
            return True
        except OSError:
            return False