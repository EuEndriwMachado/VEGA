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
import re
import shutil
import subprocess
import time

from core.connectivity.apps import AppDictionary
from core.connectivity.base import (
    Connector,
    ConnectorResult,
    ConnectorStatus,
    run_external,
)


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
        self._scrcpy_path = None
        self._mirror_process = None
        self.apps = AppDictionary()
        from core.connectivity.identity import DeviceIdentity
        from core.connectivity.phone_actions import PhoneActions
        self.identity = DeviceIdentity(registry=registry)
        # Ações avançadas (notificação, alarme, foto, ligação...) reutilizam
        # o mesmo conector: não há uma segunda conexão com o aparelho.
        # (nome diferente de "actions" para não colidir com o método da base)
        # Criado ANTES do super(): _register_actions() roda dentro dele.
        self.phone_actions = PhoneActions(self)
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
        """Executa um comando externo (delega para o utilitário compartilhado)."""
        return run_external(args, timeout=timeout or self.TIMEOUT, runner=self._runner)

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
            "stop_mirror": self._action_stop_mirror,
            "identify": self._action_identify,
            "notifications": self.phone_actions.notifications,
            "set_alarm": self.phone_actions.set_alarm,
            "list_alarms": self.phone_actions.list_alarms,
            "locate": self.phone_actions.locate,
            "take_photo": self.phone_actions.take_photo,
            "call": self.phone_actions.call,
            "send_message": self.phone_actions.send_message,
            "send_whatsapp": self.phone_actions.send_whatsapp,
            "read_conversation": self.phone_actions.read_conversation,
            "search_web": self.phone_actions.search_web,
            "play_music": self.phone_actions.play_music,
            "bluetooth": self.phone_actions.bluetooth,
            "media_pause": self._action_media_pause,
            "media_play": self._action_media_play,
            "media_next": self._action_media_next,
            "media_previous": self._action_media_previous,
            "media_stop": self._action_media_stop,
            "pair_wireless": self._action_pair_wireless,
            "connect_wireless": self._action_connect_wireless,
            "enable_wireless": self._action_enable_wireless,
            "reconnect": self._action_reconnect,
        }

    def validate(self, action: str, **kwargs) -> str:
        """Valida argumentos obrigatórios antes de checar disponibilidade."""
        if action == "open_app" and not str(kwargs.get("app") or "").strip():
            return "Qual aplicativo você quer abrir no celular?"
        if action == "send_file" and not str(kwargs.get("path") or "").strip():
            return "Qual arquivo você quer enviar para o celular?"
        if action == "pull_file" and not str(kwargs.get("remote") or "").strip():
            return "Qual arquivo do celular você quer trazer?"
        if action == "send_whatsapp":
            if not str(kwargs.get("contact") or "").strip():
                return "Para quem eu envio a mensagem?"
            if not str(kwargs.get("text") or "").strip():
                return "O que eu escrevo na mensagem?"
        if action == "read_conversation" and not str(kwargs.get("contact") or "").strip():
            return "De quem você quer ler as mensagens?"
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
            brand = self._device_prop(serial, "ro.product.brand") or ""

            # Guarda o IP Wi-Fi quando disponível: é o endereço que permite
            # reconectar sem cabo depois. O serial fica como apelido.
            ip = ""
            ok_ip, route, _err = self._adb("shell", "ip", "route", serial=serial)
            if ok_ip:
                match = re.search(r"src\s+(\d{1,3}(?:\.\d{1,3}){3})", route)
                if match:
                    ip = match.group(1)

            address = ip or serial

            # Identidade: reconhece o aparelho (ex.: Redmi Note 13 do Endriw)
            # pelo modelo/marca/serial, e guarda isso no registro.
            identity = self.identity.register(
                model=model, brand=brand, serial=serial, address=address,
                ip=ip,
            )
            found.append({
                "name": identity["name"],
                "address": address,
                "kind": "phone",
                "owner": identity["owner"],
                "serial": serial,
            })
        return found

    def _device_prop(self, serial, prop) -> str:
        ok, output, _error = self._adb("shell", "getprop", prop, serial=serial)
        return output.strip() if ok else ""

    def _action_identify(self, device=None, **_kwargs) -> ConnectorResult:
        """Diz qual é o aparelho conectado e de quem ele é."""
        devices = self.discover()
        if not devices:
            return ConnectorResult.failure("Nenhum celular conectado para identificar.")

        info = devices[0]
        owner = info.get("owner") or ""
        name = info.get("name") or "celular"

        if owner:
            return ConnectorResult.success(
                f"Esse é o seu {name}, {owner}.", data=info,
            )
        return ConnectorResult.success(f"Esse é o {name}.", data=info)

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
        temperature = ""
        health = ""
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("level:"):
                level = line.split(":", 1)[1].strip()
            elif line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
                charging = "carregando" if status in {"2", "5"} else "descarregando"
            elif line.startswith("temperature:"):
                raw_temperature = line.split(":", 1)[1].strip()
                try:
                    temperature = f"{int(raw_temperature) / 10:.1f}°C"
                except ValueError:
                    temperature = ""
            elif line.startswith("health:"):
                health = line.split(":", 1)[1].strip()

        if not level:
            return ConnectorResult.failure("Não consegui interpretar o nível da bateria.")

        detail = f"bateria em {level}%"
        if charging:
            detail += f", {charging}"
        if temperature:
            detail += f", temperatura de {temperature}"
        if health and health not in {"2", "1"}:
            detail += f", estado {health.lower()}"
        return ConnectorResult.success(
            f"A {detail}.",
            data={"level": level, "charging": charging, "temperature": temperature, "health": health},
        )

    def _action_open_app(self, app="", device=None, **_kwargs) -> ConnectorResult:
        app = str(app or "").strip()
        if not app:
            return ConnectorResult.failure("Qual aplicativo você quer abrir no celular?")

        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        # Traduz o nome falado ("instagram") para o pacote Android.
        package = self.apps.resolve(app)
        if not package:
            return ConnectorResult.failure(
                f"Não conheço o aplicativo {app}. Diga o nome do pacote "
                "(ex.: com.instagram.android) ou me ensine: diga 'guarda o app "
                f"{app} como <pacote>'."
            )

        label = self.apps.label(app)

        # Testa os candidatos (pacote padrão + variantes de fabricante) até
        # encontrar um realmente instalado no aparelho.
        candidates = self.apps.candidates(app) or [package]
        for candidate in candidates:
            if not self._is_installed(candidate, serial):
                continue
            if self._launch_package(candidate, serial):
                return ConnectorResult.success(f"Abrindo {label} no celular.")

        return ConnectorResult.failure(
            f"Não encontrei {label} instalado neste celular."
        )

    def _is_installed(self, package, serial) -> bool:
        """Confirma que o pacote existe no aparelho."""
        ok, output, _error = self._adb(
            "shell", "pm", "list", "packages", package, serial=serial,
        )
        return ok and f"package:{package}" in output

    def _launch_package(self, package, serial) -> bool:
        """Tenta abrir o pacote por dois caminhos (monkey e resolve-activity)."""
        # 1. monkey abre o launcher padrão do pacote.
        ok, output, _error = self._adb(
            "shell", "monkey", "-p", package, "-c",
            "android.intent.category.LAUNCHER", "1", serial=serial,
        )
        if ok and "No activities found" not in output:
            return True

        # 2. Resolve o componente do launcher e abre direto.
        ok, output, _error = self._adb(
            "shell", "cmd", "package", "resolve-activity", "--brief",
            package, serial=serial,
        )
        if ok:
            for line in output.splitlines():
                line = line.strip()
                if "/" in line and "." in line:
                    ok2, _out, _err = self._adb(
                        "shell", "am", "start", "-n", line, serial=serial,
                    )
                    if ok2:
                        return True
        return False

    def _action_stop_mirror(self, **_kwargs) -> ConnectorResult:
        if self.stop_mirror():
            return ConnectorResult.success("Parei o espelhamento.")
        return ConnectorResult.failure("Não há espelhamento aberto para parar.")

    def _action_screenshot(self, device=None, show=True, **_kwargs) -> ConnectorResult:
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

        # Mostra o print no PC automaticamente (o visualizador padrão abre).
        shown = False
        if show:
            shown = self._open_on_pc(local)

        message = "Capturei a tela do celular" + (
            " e abri o print aqui." if shown else "."
        )
        return ConnectorResult.success(
            message,
            data={"path": os.path.abspath(local), "shown": shown},
        )

    @staticmethod
    def _open_on_pc(path) -> bool:
        """Abre um arquivo local com o visualizador padrão do Windows."""
        try:
            os.startfile(os.path.abspath(path))  # type: ignore[attr-defined]
            return True
        except (OSError, AttributeError):
            return False

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

    # Perfis de espelhamento: equilibram fluidez x qualidade.
    #
    # ATENÇÃO: os nomes das opções precisam existir na versão instalada do
    # scrcpy. `--buffer-time` NÃO existe no scrcpy 3.x (o correto é
    # `--video-buffer`); com uma opção inválida o scrcpy sai na hora com
    # "unknown option" e nada aparece — era o "falou que espelhou mas não
    # aconteceu nada".
    MIRROR_PROFILES = {
        # Modo padrão: fluido, bom para uso geral e Wi-Fi.
        "balanced": [
            "--max-size", "1024",       # reduz a resolução enviada
            "--max-fps", "30",          # limita a taxa de quadros
            "--video-bit-rate", "4M",   # bitrate adequado sem saturar Wi-Fi
            "--audio-bit-rate", "96K",
            "--video-buffer", "50",     # menos latência de buffer
        ],
        # Modo rápido: máxima fluidez, qualidade menor.
        "fast": [
            "--max-size", "1024",
            "--max-fps", "30",
            "--video-bit-rate", "2M",
            "--audio-bit-rate", "64K",
            "--video-buffer", "35",
            "--video-codec", "h264",
        ],
        # Modo qualidade: imagem melhor, exige rede boa.
        "quality": [
            "--max-size", "1600",
            "--max-fps", "60",
            "--video-bit-rate", "8M",
            "--video-buffer", "80",
        ],
        # Modo sem áudio: para quem quer só a tela (mais leve ainda).
        "silent": [
            "--max-size", "1024",
            "--max-fps", "30",
            "--video-bit-rate", "4M",
            "--no-audio",
            "--video-buffer", "50",
        ],
    }

    def _action_mirror(self, device=None, profile="balanced", **_kwargs) -> ConnectorResult:
        serial = self._resolve_serial(device)
        if not serial:
            return ConnectorResult.failure("Nenhum celular conectado.")

        scrcpy = self._find_scrcpy()
        if not scrcpy:
            return ConnectorResult.failure(
                "Para espelhar a tela do celular, instale o scrcpy "
                "(github.com/Genymobile/scrcpy)."
            )

        # Um espelhamento por vez: evita duas janelas brigando pelo aparelho.
        if self._mirror_process is not None and self._mirror_process.poll() is None:
            return ConnectorResult.success("O espelhamento já está aberto.")

        options = self.MIRROR_PROFILES.get(
            str(profile or "balanced").lower(),
            self.MIRROR_PROFILES["balanced"],
        )

        command = [scrcpy, "-s", serial, *options]

        # O scrcpy usa o próprio adb empacotado, que pode ser um servidor
        # DIFERENTE do que a VEGA usou para conectar o celular sem fio — e aí
        # o scrcpy não enxerga o aparelho. Apontamos o scrcpy para o MESMO
        # adb da VEGA pela variável de ambiente ADB.
        env = dict(os.environ)
        adb_path = self._find_adb()
        if adb_path:
            env["ADB"] = adb_path

        try:
            # Sem CREATE_NO_WINDOW: essa flag escondia a janela do scrcpy em
            # alguns sistemas, então o espelhamento "abria" invisível.
            self._mirror_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        except OSError as error:
            self._mirror_process = None
            return ConnectorResult.failure(f"Não consegui iniciar o espelhamento: {error}")

        # O scrcpy pode sair imediatamente (opção inválida, sem dispositivo,
        # tela bloqueada...). Antes a VEGA anunciava "Espelhando a tela" sem
        # conferir nada — daí o "falou que espelhou mas não aconteceu nada".
        # Damos um instante e conferimos se o processo continua vivo.
        time.sleep(1.5)
        if self._mirror_process.poll() is not None:
            output = ""
            try:
                output = (self._mirror_process.stdout.read() or "").strip()
            except Exception:
                output = ""
            self._mirror_process = None
            detail = output.splitlines()[-1] if output else ""
            message = "O espelhamento não abriu."
            if detail:
                message += f" Motivo do scrcpy: {detail}"
            else:
                message += (
                    " Verifique se o celular está conectado e desbloqueado, "
                    "e se o scrcpy está instalado corretamente."
                )
            return ConnectorResult.failure(message)

        return ConnectorResult.success("Espelhando a tela do celular.")

    def stop_mirror(self) -> bool:
        """Encerra o espelhamento em andamento, se houver."""
        process = self._mirror_process
        if process is None or process.poll() is not None:
            self._mirror_process = None
            return False
        try:
            process.terminate()
        except OSError:
            return False
        self._mirror_process = None
        return True

    def _find_scrcpy(self) -> str:
        """Procura o scrcpy no PATH e no local padrão de instalação da VEGA."""
        if self._scrcpy_path:
            return self._scrcpy_path

        found = shutil.which("scrcpy")
        if found:
            self._scrcpy_path = found
            return found

        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "scrcpy", "scrcpy.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Downloads", "scrcpy", "scrcpy.exe"),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                self._scrcpy_path = candidate
                return candidate
        return ""

    # ----------------------------------------------------------
    # Sem fio (ADB over Wi-Fi)
    # ----------------------------------------------------------

    def _wireless(self):
        """Instância de WirelessADB compartilhando adb/registro."""
        from core.connectivity.wireless import WirelessADB
        return WirelessADB(
            adb_path=self._find_adb(),
            registry=self.registry,
            runner=self._runner,
        )

    def _action_pair_wireless(self, address="", code="", **_kwargs) -> ConnectorResult:
        ok, message = self._wireless().pair(address, code)
        return ConnectorResult(ok=ok, message=message)

    def _action_connect_wireless(self, address="", port=5555, **_kwargs) -> ConnectorResult:
        ok, message = self._wireless().connect(address, port)
        return ConnectorResult(ok=ok, message=message)

    def _action_enable_wireless(self, port=5555, **_kwargs) -> ConnectorResult:
        """Ativa o modo sem fio usando o cabo uma única vez."""
        wireless = self._wireless()
        ok, message = wireless.enable_tcpip(port)
        if not ok:
            return ConnectorResult.failure(message)

        ip = wireless.current_ip()
        if not ip:
            return ConnectorResult.success(
                f"{message} Agora conecte por Wi-Fi com 'adb connect <ip>:{port}'."
            )
        ok, connect_message = wireless.connect(ip, port)
        return ConnectorResult(ok=ok, message=f"{message} {connect_message}")

    def _action_reconnect(self, **_kwargs) -> ConnectorResult:
        """Reconecta celulares sem fio já pareados."""
        messages = self._wireless().reconnect_known()
        if not messages:
            return ConnectorResult.failure(
                "Não tenho nenhum celular sem fio salvo para reconectar."
            )
        return ConnectorResult.success(" ".join(messages))

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
    def _action_media_previous(self, device=None, **_kwargs) -> ConnectorResult:
        result = self._media_key(88, device)  # KEYCODE_MEDIA_PREVIOUS
        if result.ok:
            return ConnectorResult.success("Voltei para a mídia anterior no celular.")
        return result
    def _action_media_stop(self, device=None, **_kwargs) -> ConnectorResult:
        result = self._media_key(86, device)  # KEYCODE_MEDIA_STOP
        if result.ok:
            return ConnectorResult.success("Parei a mídia no celular.")
        return result
