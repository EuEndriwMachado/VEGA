# -*- coding: utf-8 -*-
"""Conector de celular da VEGA.

Dois caminhos, em ordem de preferência:

1. **ADB** (`adb` no PATH ou no Android SDK padrão) — controle direto do
   aparelho Android: listar, abrir app, tirar print, enviar/receber arquivo,
   ver bateria, espelhar tela com `scrcpy`.
2. **KDE Connect** — quando presente, cobre notificações, bateria e
   transferência sem precisar de depuração USB.

Nenhum dos dois é obrigatório: se ausentes, a VEGA explica o que instalar
em vez de falhar. Todas as ações retornam `ConnectorResult`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from core.connectivity.base import Connector, ConnectorResult, ConnectorStatus


class PhoneConnector(Connector):
    """Controla um celular Android via ADB (e KDE Connect quando disponível)."""

    name = "phone"
    install_hint = (
        "Instale o ADB (Android Platform Tools) e ative a Depuração USB "
        "no celular. Alternativa sem cabo: instale o KDE Connect no PC e "
        "no celular."
    )

    # Tempo máximo de um comando adb curto.
    TIMEOUT = 12

    def __init__(self, registry=None, runner=None):
        self._adb_path = None
        self._kdeconnect_path = None
        super().__init__(registry=registry, runner=runner)

    # ==========================================================
    # DISPONIBILIDADE
    # ==========================================================

    def _find_adb(self) -> str:
        """Procura o adb no PATH e nos locais usuais do Android SDK."""
        if self._adb_path:
            return self._adb_path

        found = shutil.which("adb")
        if found:
            self._adb_path = found
            return found

        candidates = [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Android", "Sdk", "platform-tools", "adb.exe",
            ),
            os.path.join(
                os.environ.get("USERPROFILE", ""),
                "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe",
            ),
            r"C:\Program Files\Android\platform-tools\adb.exe",
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                self._adb_path = candidate
                return candidate

        return ""

    def _find_kdeconnect(self) -> str:
        if self._kdeconnect_path:
            return self._kdeconnect_path
        for binary in ("kdeconnect-cli", "kdeconnect-cli.exe"):
            found = shutil.which(binary)
            if found:
                self._kdeconnect_path = found
                return found
        return ""

    def status(self) -> ConnectorStatus:
        adb = self._find_adb()
        if adb:
            return ConnectorStatus(
                name=self.name,
                available=True,
                detail=f"ADB encontrado em {adb}.",
            )

        kde = self._find_kdeconnect()
        if kde:
            return ConnectorStatus(
                name=self.name,
                available=True,
                detail=f"KDE Connect encontrado em {kde}.",
            )

        return ConnectorStatus(
            name=self.name,
            available=False,
            detail="Não encontrei o ADB nem o KDE Connect neste computador.",
            install_hint=self.install_hint,
        )

    # ==========================================================
    # EXECUÇÃO
    # ==========================================================

    def _run(self, args, timeout=None):
        """Executa um comando externo com segurança. Retorna (ok, stdout, stderr)."""
        if self._runner is not None:
            return self._runner(args)

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout or self.TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return False, "", "tempo esgotado"
        except (OSError, ValueError) as error:
            return False, "", str(error)

        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        return completed.returncode == 0, output, error

    def _adb(self, *args, serial=None):
        adb = self._find_adb()
        if not adb:
            return False, "", "adb não encontrado"
        command = [adb]
        if serial:
            command += ["-s", serial]
        command += list(args)
        return self._run(command)

    def _resolve_serial(self, device_name=None) -> str:
        """Descobre o serial ADB do dispositivo pedido (ou o único conectado)."""
        ok, output, _error = self._adb("devices")
        if not ok:
            return ""

        serials = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])

        if not serials:
            return ""

        if device_name and self.registry is not None:
            device = self.registry.find(device_name, kind="phone")
            if device and device.address in serials:
                return device.address

        return serials[0]

    # ==========================================================
    # AÇÕES
    # ==========================================================

    def _register_actions(self):
        self._actions = {
            "list": self._action_list,
            "battery": self._action_battery,
            "open_app": self._action_open_app,
            "screenshot": self._action_screenshot,
            "send_file": self._action_send_file,
            "pull_file": self._action_pull_file,
            "mirror": self._action_mirror,
            "media_pause": self._action_media_pause,
            "media_play": self._action_media_play,
            "media_next": self._action_media_next,
        }

    def validate(self, action: str, **kwargs) -> str:
        """Valida argumentos obrigatórios antes de checar disponibilidade."""
        if action == "open_app" and not str(kwargs.get("app") or "").strip():
            return "Qual aplicativo você quer abrir no celular?"
        if action == "send_file" and not str(kwargs.get("path") or "").strip():
            return "Qual arquivo você quer enviar para o celular?"
        if action == "pull_file" and not str(kwargs.get("remote") or "").strip():
            return "Qual arquivo do celular você quer trazer?"
        return ""

    def discover(self) -> list:
        """Lista celulares conectados via ADB e os registra."""
        if not self._find_adb():
            return []

        ok, output, _error = self._adb("devices")
        if not ok:
            return []

        found = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device":
                continue
            serial = parts[0]
            model = self._device_prop(serial, "ro.product.model") or "Celular"
            if self.registry is not None:
                self.registry.remember(
                    model, kind="phone", address=serial, connector="phone",
                    aliases=[serial],
                )
            found.append({"name": model, "address": serial, "kind": "phone"})
        return found

    def _device_prop(self, serial, prop) -> str:
        ok, output, _error = self._adb("shell", "getprop", prop, serial=serial)
        return output.strip() if ok else ""

    def _action_list(self, **_kwargs) -> ConnectorResult:
        devices = self.discover()
        if not devices:
            return ConnectorResult.failure(
                "Não encontrei nenhum celular conectado. Conecte o cabo USB "
                "e ative a Depuração USB no aparelho."
            )
        names = ", ".join(item["name"] for item in devices)
        return ConnectorResult.success(
            f"Encontrei {len(devices)} celular(es): {names}.",
            data=devices,
        )

    def _action_battery(self, device=None, **_kwargs) -> ConnectorResult:
        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado para consultar a bateria.")

        ok, output, _error = self._adb("shell", "dumpsys", "battery", serial=serial)
        if not ok:
            return ConnectorResult.failure("Não consegui ler a bateria do celular.")

        level = ""
        charging = ""
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("level:"):
                level = line.split(":", 1)[1].strip()
            elif line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
                charging = "carregando" if status in {"2", "5"} else "descarregando"

        if not level:
            return ConnectorResult.failure("Não consegui interpretar o nível da bateria.")

        detail = f"bateria em {level}%"
        if charging:
            detail += f", {charging}"
        return ConnectorResult.success(f"A {detail}.", data={"level": level})

    def _action_open_app(self, app="", device=None, **_kwargs) -> ConnectorResult:
        app = str(app or "").strip()
        if not app:
            return ConnectorResult.failure("Qual aplicativo você quer abrir no celular?")

        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        # 1. Tenta por pacote direto (monkey abre o launcher padrão).
        ok, output, error = self._adb(
            "shell", "monkey", "-p", app, "-c",
            "android.intent.category.LAUNCHER", "1", serial=serial,
        )
        if ok and "No activities found" not in output:
            return ConnectorResult.success(f"Abrindo {app} no celular.")

        # 2. Fallback: abre por intent genérico.
        ok, _output, _error = self._adb(
            "shell", "am", "start", "-a",
            "android.intent.action.MAIN", "-c",
            "android.intent.category.LAUNCHER", serial=serial,
        )
        if ok:
            return ConnectorResult.success(f"Tentei abrir {app} no celular.")

        return ConnectorResult.failure(
            f"Não encontrei o aplicativo {app} no celular. "
            "Diga o nome do pacote (ex.: com.instagram.android)."
        )

    def _action_screenshot(self, device=None, **_kwargs) -> ConnectorResult:
        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        remote = "/sdcard/vega_screen.png"
        local_dir = os.path.join("Uploads", "phone")
        os.makedirs(local_dir, exist_ok=True)
        local = os.path.join(local_dir, f"screen_{int(time.time())}.png")

        ok, _out, _err = self._adb("shell", "screencap", "-p", remote, serial=serial)
        if not ok:
            return ConnectorResult.failure("Não consegui capturar a tela do celular.")

        ok, _out, _err = self._adb("pull", remote, local, serial=serial)
        self._adb("shell", "rm", remote, serial=serial)
        if not ok or not os.path.exists(local):
            return ConnectorResult.failure("Não consegui transferir o print do celular.")

        return ConnectorResult.success(
            "Capturei a tela do celular.",
            data={"path": local},
        )

    def _action_send_file(self, path="", device=None, **_kwargs) -> ConnectorResult:
        path = str(path or "").strip()
        if not path or not os.path.exists(path):
            return ConnectorResult.failure(f"Não encontrei o arquivo {path} para enviar.")

        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        filename = os.path.basename(path)
        ok, _out, _err = self._adb("push", path, f"/sdcard/Download/{filename}", serial=serial)
        if ok:
            return ConnectorResult.success(f"Enviei {filename} para o celular.")
        return ConnectorResult.failure(f"Não consegui enviar {filename} para o celular.")

    def _action_pull_file(self, remote="", device=None, dest="", **_kwargs) -> ConnectorResult:
        remote = str(remote or "").strip()
        if not remote:
            return ConnectorResult.failure("Qual arquivo do celular você quer trazer?")

        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        if not dest:
            dest = os.path.join("Uploads", "phone", os.path.basename(remote))
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

        ok, _out, _err = self._adb("pull", remote, dest, serial=serial)
        if ok:
            return ConnectorResult.success(
                f"Trouxe {os.path.basename(remote)} do celular.",
                data={"path": dest},
            )
        return ConnectorResult.failure(f"Não consegui trazer {remote} do celular.")

    def _action_mirror(self, device=None, **_kwargs) -> ConnectorResult:
        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        scrcpy = shutil.which("scrcpy")
        if not scrcpy:
            return ConnectorResult.failure(
                "Para espelhar a tela do celular, instale o scrcpy "
                "(github.com/Genymobile/scrcpy)."
            )

        try:
            subprocess.Popen([scrcpy, "-s", serial])
        except OSError as error:
            return ConnectorResult.failure(f"Não consegui iniciar o espelhamento: {error}")
        return ConnectorResult.success("Espelhando a tela do celular.")

    # ----------------------------------------------------------
    # Controle de mídia do celular
    # ----------------------------------------------------------

    def _media_key(self, code, device):
        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")
        ok, _out, _err = self._adb("shell", "input", "keyevent", str(code), serial=serial)
        if ok:
            return ConnectorResult.success("")
        return ConnectorResult.failure("Não consegui controlar a mídia do celular.")

    def _action_media_pause(self, device=None, **_kwargs) -> ConnectorResult:
        result = self._media_key(127, device)  # KEYCODE_MEDIA_PAUSE
        if result.ok:
            return ConnectorResult.success("Pausei a mídia no celular.")
        return result

    def _action_media_play(self, device=None, **_kwargs) -> ConnectorResult:
        result = self._media_key(126, device)  # KEYCODE_MEDIA_PLAY
        if result.ok:
            return ConnectorResult.success("Dei play na mídia do celular.")
        return result

    def _action_media_next(self, device=None, **_kwargs) -> ConnectorResult:
        result = self._media_key(87, device)  # KEYCODE_MEDIA_NEXT
        if result.ok:
            return ConnectorResult.success("Pulei para a próxima no celular.")
        return result