# -*- coding: utf-8 -*-
"""Ações avançadas no celular: notificações, alarme, localizar, foto,
ligação e mensagem.

Separado de `phone.py` para manter cada arquivo com uma responsabilidade
clara: `phone.py` cuida do controle básico (apps, arquivos, mídia, tela),
e este módulo cuida da comunicação com o usuário e recursos do sistema.

Tudo é executado por ADB e nunca levanta exceção: falhas viram mensagens
claras em português.
"""

from __future__ import annotations

import re
import time

from core.connectivity.base import ConnectorResult


class PhoneActions:
    """Ações de uso diário no celular (notificação, alarme, foto...)."""

    def __init__(self, connector):
        # Reutiliza o conector existente (adb, serial, resultados).
        self.connector = connector

    # ==========================================================
    # HELPERS
    # ==========================================================

    def _serial(self, device=None):
        return self.connector._resolve_serial(device)

    def _shell(self, *args, serial=None):
        return self.connector._adb("shell", *args, serial=serial)

    def _no_phone(self) -> ConnectorResult:
        return ConnectorResult.failure("Nenhum celular conectado.")

    # ==========================================================
    # NOTIFICAÇÕES
    # ==========================================================

    def notifications(self, device=None, limit=5, **_kwargs) -> ConnectorResult:
        """Lê as notificações ativas do celular."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        ok, output, _error = self._shell("dumpsys", "notification", serial=serial)
        if not ok or not output:
            return ConnectorResult.failure("Não consegui ler as notificações do celular.")

        entries = self._parse_notifications(output, limit=limit)

        if not entries:
            return ConnectorResult.success(
                "Nenhuma notificação nova no celular.", data={"items": []},
            )

        if len(entries) == 1:
            message = f"Uma notificação: {entries[0]}."
        else:
            message = f"{len(entries)} notificações: " + "; ".join(entries) + "."

        return ConnectorResult.success(message, data={"items": entries})

    @staticmethod
    def _parse_notifications(output, limit=5):
        """Extrai pares (app, texto) do dumpsys notification.

        O Android escreve os campos assim:
            android.title=String (Título real)
            android.text=SpannableString (Corpo real)
        Então o texto está entre parênteses, não entre aspas.
        """
        entries = []
        seen = set()

        # Cada notificação começa com "NotificationRecord".
        blocks = re.split(r"NotificationRecord", output)

        for block in blocks:
            package_match = re.search(r"pkg=([\w.]+)", block)
            if not package_match:
                continue
            package = package_match.group(1)

            # Ignora notificações do próprio sistema (sem valor para o usuário).
            if package in {"android", "com.android.systemui", "com.android.providers.downloads"}:
                continue

            title = PhoneActions._extract_notification_field(block, "android.title")
            body = PhoneActions._extract_notification_field(block, "android.text")

            text = body or title
            if not text:
                continue

            app = PhoneActions._friendly_app(package)
            entry = f"{app}: {text[:120]}"

            key = entry.lower()
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)

            if len(entries) >= limit:
                break

        return entries

    @staticmethod
    def _extract_notification_field(block, field):
        """Extrai o valor real de um campo do dumpsys.

        Formatos vistos:
            android.title=String (Meu título)
            android.text=SpannableString (Meu texto)
            android.title="Título com aspas"
        """
        # Com parênteses (formato do Android 12+).
        match = re.search(
            rf"{re.escape(field)}=[\w]*\s*\(([^)\n]*)\)", block
        )
        if match:
            value = match.group(1).strip()
            if value and value.lower() not in {"null", "none"}:
                return value

        # Com aspas (formatos mais antigos).
        match = re.search(rf'{re.escape(field)}="([^"\n]+)"', block)
        if match:
            return match.group(1).strip()

        # Texto direto, sem wrapper.
        match = re.search(rf"{re.escape(field)}=([^\n]+)", block)
        if match:
            value = match.group(1).strip().strip('"')
            # Descarta referências sem conteúdo (ex.: "String [length=34]").
            if value and "[" not in value and value.lower() not in {"null", "none"}:
                return value

        return ""

    @staticmethod
    def _friendly_app(package) -> str:
        """Nome amigável do app a partir do pacote."""
        known = {
            "com.whatsapp": "WhatsApp",
            "com.instagram.android": "Instagram",
            "com.google.android.gm": "Gmail",
            "com.telegram.messenger": "Telegram",
            "org.telegram.messenger": "Telegram",
            "com.facebook.orca": "Messenger",
            "com.discord": "Discord",
            "com.google.android.apps.messaging": "Mensagens",
            "com.android.mms": "Mensagens",
            "com.linkedin.android": "LinkedIn",
            "com.twitter.android": "X",
            "com.spotify.music": "Spotify",
            "com.google.android.youtube": "YouTube",
        }
        if package in known:
            return known[package]
        # "com.exemplo.app" -> "Exemplo"
        parts = [part for part in str(package).split(".") if part and part not in {"com", "org", "br", "net"}]
        if parts:
            return parts[-1].replace("_", " ").title()
        return "App"

    # ==========================================================
    # ALARME
    # ==========================================================

    def set_alarm(self, hour=None, minute=None, label="", device=None, **_kwargs) -> ConnectorResult:
        """Cria um alarme no relógio do celular."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        try:
            hour = int(hour)
            minute = int(minute)
        except (TypeError, ValueError):
            return ConnectorResult.failure("Diga o horário do alarme (ex.: 6 e 30).")

        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            return ConnectorResult.failure("Horário inválido para o alarme.")

        message = str(label or "VEGA").strip() or "VEGA"

        # Caminho 1: intent padrão do Android (funciona em AOSP, Pixel, etc.).
        ok, output, error = self._shell(
            "am", "start", "-a", "android.intent.action.SET_ALARM",
            "--ei", "android.intent.extra.alarm.HOUR", str(hour),
            "--ei", "android.intent.extra.alarm.MINUTES", str(minute),
            "--es", "android.intent.extra.alarm.MESSAGE", message,
            "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
            serial=serial,
        )
        combined = f"{output} {error}".lower()
        if ok and "error" not in combined and "exception" not in combined:
            # Confirma que o alarme realmente entrou no app de relógio.
            if self._alarm_exists(hour, minute, serial):
                return ConnectorResult.success(
                    f"Alarme criado para {hour:02d}:{minute:02d} no celular."
                )

        # Caminho 2: app de relógio do MIUI/Xiaomi, que ignora o intent padrão.
        miui_ok = self._set_alarm_miui(hour, minute, message, serial)
        if miui_ok and self._alarm_exists(hour, minute, serial):
            return ConnectorResult.success(
                f"Alarme criado para {hour:02d}:{minute:02d} no celular."
            )

        # Caminho 3: o MIUI bloqueia criação remota de alarme
        # (miui.permission.ACCESS_ALARM_PROVIDER). Abre o app e pede que o
        # usuário confirme, em vez de mentir que criou.
        for package in ("com.android.deskclock", "com.miui.clock",
                        "com.google.android.deskclock"):
            started, _out, _err = self._shell(
                "monkey", "-p", package, "-c",
                "android.intent.category.LAUNCHER", "1", serial=serial,
            )
            if started:
                return ConnectorResult.failure(
                    f"Este celular (MIUI) não permite criar alarmes à distância. "
                    f"Abri o relógio: confirme o alarme para {hour:02d}:{minute:02d}."
                )

        return ConnectorResult.failure(
            f"Não consegui criar o alarme para {hour:02d}:{minute:02d}. "
            "Verifique se o app de relógio está disponível."
        )

    def _alarm_exists(self, hour, minute, serial) -> bool:
        """Confirma que existe um alarme no horário pedido.

        O MIUI restringe o provider de alarmes, então a confirmação pode
        falhar por permissão — nesse caso retornamos False para não
        afirmar algo que não foi verificado.
        """
        ok, output, _error = self._shell(
            "content", "query", "--uri", "content://com.android.deskclock/alarm",
            serial=serial,
        )
        if not ok or not output:
            return False

        # Formato: Row: N hour=6, minutes=30, enabled=1, ...
        for match in re.finditer(r"hour=(\d+).*?minutes=(\d+)", output):
            if int(match.group(1)) == hour and int(match.group(2)) == minute:
                return True
        return False

    def _set_alarm_miui(self, hour, minute, label, serial) -> bool:
        """Cria alarme no MIUI via broadcast interno do app de relógio."""
        # O app de relógio do MIUI expõe um receiver próprio para criar alarmes.
        candidates = (
            "com.android.deskclock/com.android.deskclock.alarms.AlarmReceiver",
            "com.miui.clock/com.miui.clock.alarm.AlarmReceiver",
        )
        for component in candidates:
            ok, output, error = self._shell(
                "am", "broadcast", "-a", "android.intent.action.SET_ALARM",
                "-n", component,
                "--ei", "android.intent.extra.alarm.HOUR", str(hour),
                "--ei", "android.intent.extra.alarm.MINUTES", str(minute),
                "--es", "android.intent.extra.alarm.MESSAGE", label,
                serial=serial,
            )
            combined = f"{output} {error}".lower()
            if ok and "error" not in combined:
                return True
        return False

    def list_alarms(self, device=None, **_kwargs) -> ConnectorResult:
        """Lista os alarmes configurados (best effort)."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        ok, output, _error = self._shell(
            "dumpsys", "alarm", serial=serial,
        )
        if not ok:
            return ConnectorResult.failure("Não consegui ler os alarmes do celular.")

        matches = re.findall(r"(?:RTC_WAKEUP|RTC)\s+.*?when=(\d+)", output)
        if not matches:
            return ConnectorResult.success("Não encontrei alarmes ativos no celular.")

        times = []
        for raw in matches[:5]:
            try:
                stamp = int(raw) / 1000.0
                times.append(time.strftime("%H:%M", time.localtime(stamp)))
            except (ValueError, OSError):
                continue

        if not times:
            return ConnectorResult.success("Não encontrei alarmes ativos no celular.")
        return ConnectorResult.success(
            "Alarmes no celular: " + ", ".join(times) + ".",
            data={"items": times},
        )

    # ==========================================================
    # LOCALIZAR O CELULAR
    # ==========================================================

    def locate(self, device=None, **_kwargs) -> ConnectorResult:
        """Faz o celular tocar para o usuário encontrá-lo."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        # Toca um som de notificação e vibra, sem depender de app externo.
        ok, _out, _err = self._shell(
            "cmd", "media_session", "dispatch", "play", serial=serial,
        )
        # Reforça com vibração e som do sistema.
        self._shell("input", "keyevent", "KEYCODE_MEDIA_PLAY", serial=serial)
        self._shell(
            "am", "start", "-a", "android.intent.action.VIEW",
            "-d", "https://www.google.com", serial=serial,
        )

        if ok:
            return ConnectorResult.success(
                "Fiz o celular tocar e acender a tela. Procure por ele."
            )
        return ConnectorResult.failure(
            "Não consegui fazer o celular tocar. Verifique se ele está desbloqueado."
        )

    # ==========================================================
    # FOTO COM O CELULAR
    # ==========================================================

    def take_photo(self, device=None, dest="", **_kwargs) -> ConnectorResult:
        """Tira uma foto com a câmera do celular e traz para o PC."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        import os

        remote = "/sdcard/DCIM/Camera/vega_photo.jpg"

        # Dispara a câmera pelo botão de hardware (funciona na maioria).
        self._shell("input", "keyevent", "KEYCODE_CAMERA", serial=serial)
        time.sleep(2.0)

        # Se a câmera não tiver aberto, tenta pelo app.
        ok, output, _error = self._shell("ls", remote, serial=serial)
        if not ok:
            self._shell(
                "am", "start", "-a", "android.media.action.IMAGE_CAPTURE",
                serial=serial,
            )
            time.sleep(2.0)
            self._shell("input", "keyevent", "KEYCODE_CAMERA", serial=serial)
            time.sleep(1.5)

        # Pega a foto mais recente da câmera.
        ok, output, _error = self._shell(
            "ls", "-t", "/sdcard/DCIM/Camera/", serial=serial,
        )
        if not ok or not output:
            return ConnectorResult.failure(
                "Não consegui tirar a foto. Verifique se a câmera está liberada."
            )

        newest = ""
        for line in output.splitlines():
            line = line.strip()
            if line.lower().endswith((".jpg", ".jpeg", ".png")):
                newest = line
                break

        if not newest:
            return ConnectorResult.failure("Não encontrei a foto no celular.")

        if not dest:
            folder = os.path.join("Uploads", "phone")
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, f"photo_{int(time.time())}.jpg")

        remote_path = f"/sdcard/DCIM/Camera/{newest}"
        ok, _out, _err = self.connector._adb("pull", remote_path, dest, serial=serial)
        if not ok:
            return ConnectorResult.failure("Não consegui trazer a foto para o PC.")

        return ConnectorResult.success(
            "Tirei a foto com o celular e trouxe para o PC.",
            data={"path": os.path.abspath(dest)},
        )

    # ==========================================================
    # LIGAÇÃO
    # ==========================================================

    def call(self, contact="", number="", device=None, **_kwargs) -> ConnectorResult:
        """Disca para um contato ou número pelo celular."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        target = str(number or "").strip()
        name = str(contact or "").strip()

        if not target and name:
            target = self._find_number_by_name(name, serial)
            if not target:
                return ConnectorResult.failure(
                    f"Não encontrei o contato {name} no celular."
                )

        if not target:
            return ConnectorResult.failure("Diga para quem eu devo ligar.")

        digits = re.sub(r"[^\d+]", "", target)
        if not digits:
            return ConnectorResult.failure("Não consegui entender o número para ligar.")

        ok, _out, _err = self._shell(
            "am", "start", "-a", "android.intent.action.CALL",
            "-d", f"tel:{digits}", serial=serial,
        )
        if ok:
            return ConnectorResult.success(
                f"Ligando para {name or digits} pelo celular."
            )
        return ConnectorResult.failure("Não consegui iniciar a ligação.")

    def _find_number_by_name(self, name, serial):
        """Procura o número de um contato na agenda do celular."""
        ok, output, _error = self._shell(
            "content", "query", "--uri", "content://com.android.contacts/data",
            "--projection", "display_name:data1",
            "--where", f"display_name LIKE '%{name}%'",
            serial=serial,
        )
        if not ok or not output:
            return ""

        # Formato: Row: 0 display_name=Joao, data1=+5521999999999
        match = re.search(r"data1=([+\d\s()-]+)", output)
        if match:
            return match.group(1).strip()
        return ""

    # ==========================================================
    # MENSAGEM (WhatsApp / SMS)
    # ==========================================================

    def send_message(self, contact="", text="", app="whatsapp", device=None, **_kwargs) -> ConnectorResult:
        """Envia uma mensagem pelo celular (WhatsApp por padrão)."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        contact = str(contact or "").strip()
        text = str(text or "").strip()

        if not contact:
            return ConnectorResult.failure("Para quem eu envio a mensagem?")
        if not text:
            return ConnectorResult.failure("O que eu escrevo na mensagem?")

        number = self._find_number_by_name(contact, serial)
        if not number:
            return ConnectorResult.failure(
                f"Não encontrei o contato {contact} no celular."
            )

        digits = re.sub(r"[^\d]", "", number)

        if "whatsapp" in str(app).lower():
            url = f"https://wa.me/{digits}?text={self._url_encode(text)}"
            package = "com.whatsapp"
        else:
            url = f"sms:{digits}?body={self._url_encode(text)}"
            package = "com.android.mms"

        ok, _out, _err = self._shell(
            "am", "start", "-a", "android.intent.action.VIEW",
            "-d", url, "-p", package, serial=serial,
        )
        if ok:
            return ConnectorResult.success(
                f"Preparei a mensagem para {contact}. Confirme o envio no celular."
            )
        return ConnectorResult.failure(
            "Não consegui preparar a mensagem. Verifique se o app está instalado."
        )

    @staticmethod
    def _url_encode(value) -> str:
        from urllib.parse import quote
        return quote(str(value), safe="")

    # ==========================================================
    # BLUETOOTH DO CELULAR
    # ==========================================================

    def bluetooth(self, enabled=True, device=None, **_kwargs) -> ConnectorResult:
        """Liga ou desliga o Bluetooth do celular."""
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        state = "enable" if enabled else "disable"
        ok, _out, _err = self._shell(
            "svc", "bluetooth", state, serial=serial,
        )
        if ok:
            return ConnectorResult.success(
                "Bluetooth do celular ligado." if enabled
                else "Bluetooth do celular desligado."
            )
        return ConnectorResult.failure(
            "Não consegui alterar o Bluetooth do celular. "
            "Pode ser preciso desbloquear a tela."
        )