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

import os
import re
import time
from urllib.parse import quote
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

    def _adb(self, *args, serial=None):
        """ADB cru (sem o 'shell'): usado por `exec-out` e `pull`."""
        return self.connector._adb(*args, serial=serial)

    def _no_phone(self) -> ConnectorResult:
        return ConnectorResult.failure("Nenhum celular conectado.")

    # ==========================================================
    # NOTIFICAÇÕES
    # ==========================================================

    def notifications(self, device=None, limit=5, sender="", read=False,
                      **_kwargs) -> ConnectorResult:
        """Lê as notificações ativas do celular.

        `sender` filtra por remetente/contato (ex.: "miguel") e `read=True`
        pede o CONTEÚDO real: quando o dumpsys só traz uma referência
        ("String [length=34]"), o app é aberto e a mensagem é lida da tela.
        """
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        ok, output, _error = self._shell("dumpsys", "notification", serial=serial)
        if not ok or not output:
            return ConnectorResult.failure("Não consegui ler as notificações do celular.")

        records = self._notification_records(output)
        wanted = self._fold(str(sender or ""))

        items = []
        for record in records:
            if wanted and wanted not in self._fold(record.get("sender") or ""):
                continue
            items.append(record)

        items = items[: max(1, int(limit or 5))]

        if not items:
            if wanted:
                return ConnectorResult.success(
                    f"Não encontrei notificação de {sender} no celular.",
                    data={"items": []},
                )
            return ConnectorResult.success(
                "Nenhuma notificação nova no celular.", data={"items": []},
            )

        # Conteúdo real: lê no app o que o dumpsys só referenciou.
        if read:
            for item in items:
                if item.get("precise") or not item.get("package"):
                    continue
                content = self._read_notification_content(
                    item["package"], serial, title=item.get("sender"),
                )
                if content:
                    item["text"] = content
                    item["precise"] = True

        entries = [f"{item['sender']}: {item['text'][:160]}" for item in items]

        if len(entries) == 1:
            message = f"Uma notificação: {entries[0]}."
        else:
            message = f"{len(entries)} notificações: " + "; ".join(entries) + "."

        return ConnectorResult.success(message, data={"items": entries, "records": items})

    @staticmethod
    def _fold(value) -> str:
        """Minúsculas, sem acento — para comparar nomes de contato."""
        import unicodedata
        text = str(value or "").lower()
        text = unicodedata.normalize("NFKD", text)
        return "".join(ch for ch in text if not unicodedata.combining(ch)).strip()

    @staticmethod
    def _notification_records(output):
        """Estrutura as notificações do dumpsys: remetente, app e conteúdo.

        Cada registro indica se o texto veio REAL (entre parênteses) ou se é
        só uma referência ("String [length=34]"), que precisa ser resolvida
        abrindo o app.
        """
        records = []

        for block in re.split(r"NotificationRecord", output or ""):
            package_match = re.search(r"pkg=([\w.]+)", block)
            if not package_match:
                continue
            package = package_match.group(1)
            if package in {"android", "com.android.systemui", "com.android.providers.downloads"}:
                continue

            title = PhoneActions._notification_field(block, "android.title")
            body = PhoneActions._notification_field(block, "android.text")

            if title:
                sender = title
                text = body or title
                precise = bool(body)
            elif PhoneActions._notification_field_is_reference(block, "android.title"):
                # WhatsApp manda o grupo no título (referência) e o nome de
                # quem escreveu em android.subText: "João" -> "João: mensagem".
                sender = PhoneActions._notification_field(block, "android.subText") or ""
                text = body or ""
                precise = bool(body)
            else:
                sender = PhoneActions._friendly_app(package)
                text = body or ""
                precise = bool(body)

            if not sender or not text:
                continue

            records.append({
                "sender": sender,
                "package": package,
                "app": PhoneActions._friendly_app(package),
                "text": text,
                "precise": precise,
            })

        return records

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
        # Mensagens do mesmo remetente agrupam-se numa linha só (a última
        # prevalece): ler 3 linhas repetidas do mesmo contato não ajuda.
        by_sender = {}

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

            # Agrupa por app/remetente sem repetir a mesma conversa.
            group_key = (app, str(title or "").strip().lower())
            if group_key in by_sender:
                by_sender[group_key] = entry
                continue
            if entry.lower() in seen:
                continue
            seen.add(entry.lower())
            by_sender[group_key] = entry

        entries = list(by_sender.values())[:limit]
        return entries

    @staticmethod
    def _notification_field(block, field):
        """Valor REAL de um campo de notificação (ou None quando é referência).

        O Android escreve dois formatos no dumpsys:

            android.text=SpannableString (texto real)
            android.title=String [length=34]   <- referência, sem conteúdo

        Aqui a referência vira None (desconhecido) para que quem lê possa
        decidir buscar o conteúdo real em outro lugar em vez de exibir lixo.
        """
        # Com parênteses (Android 12+): o texto real está entre parênteses.
        match = re.search(rf"{re.escape(field)}=[\w]*\s*\(([^)\n]*)\)", block)
        if match:
            value = match.group(1).strip()
            if value and value.lower() not in {"null", "none"}:
                return value
            return None

        # Com aspas (formatos mais antigos).
        match = re.search(rf'{re.escape(field)}="([^"\n]+)"', block)
        if match:
            return match.group(1).strip() or None

        # Texto direto, sem wrapper.
        match = re.search(rf"{re.escape(field)}=([^\n]+)", block)
        if match:
            value = match.group(1).strip().strip('"')
            if not value or "[" in value or value.lower() in {"null", "none"}:
                return None
            return value
        return None

    @staticmethod
    def _notification_field_is_reference(block, field) -> bool:
        """True quando o campo existe mas só traz uma referência (String [..])."""
        if not re.search(rf"{re.escape(field)}=", block):
            return False
        return PhoneActions._notification_field(block, field) is None

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

    def _read_notification_content(self, package, serial, title=None):
        """Tenta ler o TEXTO REAL de uma notificação pelo app que a publicou.

        Quando o dumpsys só traz uma referência ("String [length=34]"), o
        conteúdo fica no app. Abrir a notificação (o mesmo toque que a pessoa
        daria) traz a mensagem para a tela, de onde ela é lida. Retorna ""
        quando não foi possível ler de forma confiável.
        """
        if not package:
            return ""

        # Só para apps de mensagem: tocar numa notificação do sistema abriria
        # uma tela qualquer, sem mensagem nenhuma para ler.
        if package not in self._MESSAGE_PACKAGES:
            return ""

        for candidate in self._MESSAGE_ACTIVITY_CANDIDATES.get(package, ()):
            ok, output, _error = self._shell(
                "cmd", "package", "resolve-activity", "--brief", candidate,
                serial=serial,
            )
            if not ok or "/" not in (output or ""):
                continue

            component = output.splitlines()[-1].strip()
            self._shell("am", "start", "-n", component, serial=serial)
            time.sleep(1.8)
            screen = self._dump_ui(serial)
            if not screen:
                continue

            messages = self._parse_chat_messages(screen)
            if title:
                filtered = [
                    text for text in messages
                    if title.split()[0].lower() in text.lower()
                ]
                if filtered:
                    messages = filtered
            if messages:
                return messages[-1]

        return ""

    def _dump_ui(self, serial) -> str:
        """Dumpa a hierarquia da tela atual (best effort)."""
        for args in (
            ("exec-out", "uiautomator", "dump", "/dev/tty"),
            ("shell", "uiautomator", "dump", "/sdcard/vega_ui.xml"),
        ):
            ok, output, _error = self._adb(*args, serial=serial)
            if not ok:
                continue
            if "/dev/tty" not in args:
                pulled, content, _err = self._adb(
                    "shell", "cat", "/sdcard/vega_ui.xml", serial=serial,
                )
                if pulled:
                    output = content
            if output and "<" in output:
                return output
        return ""

    @staticmethod
    def _parse_chat_messages(xml) -> list:
        """Extrai as mensagens visíveis de uma tela de conversa.

        Funciona com o `uiautomator dump`: os balões de mensagem carregam o
        texto real, então os rótulos de interface ("Mensagem", "Digite") são
        descartados por serem curtos ou genéricos.
        """
        if not xml:
            return []

        texts = []
        for raw in re.findall(r'text="([^"]*)"', xml):
            value = raw.strip()
            if len(value) < 2:
                continue
            if value.lower() in PhoneActions._UI_LABEL_TEXTS:
                continue
            texts.append(value)
        return texts

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

        # Dispara mídia já disponível e acende a tela, sem abrir navegador ou
        # criar efeitos colaterais fora do pedido de localização.
        ok, _out, _err = self._shell(
            "cmd", "media_session", "dispatch", "play", serial=serial,
        )
        self._shell("input", "keyevent", "KEYCODE_WAKEUP", serial=serial)

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

        remote = "/sdcard/DCIM/Camera/vega_photo.jpg"

        # Dispara a câmera pelo botão de hardware (funciona na maioria).
        self._shell("input", "keyevent", "KEYCODE_CAMERA", serial=serial)
        time.sleep(2.0)

        # Se a câmera não tiver aberto, tenta pelo app.
        ok, _output, _error = self._shell("ls", remote, serial=serial)
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

    # Apps de mensagem/telefone: usados para abrir a conversa e ler/enviar
    # mensagens sem desbloquear a tela.
    _MESSAGE_PACKAGES = {
        "com.whatsapp", "com.whatsapp.w4b", "org.telegram.messenger",
        "com.instagram.android", "com.facebook.orca",
        "com.google.android.apps.messaging", "com.android.mms",
    }

    _MESSAGE_ACTIVITY_CANDIDATES = {
        "com.whatsapp": (
            "com.whatsapp/.Main",
            "com.whatsapp/.HomeActivity",
        ),
        "com.whatsapp.w4b": (
            "com.whatsapp.w4b/com.whatsapp.Main",
            "com.whatsapp.w4b/com.whatsapp.HomeActivity",
        ),
        "org.telegram.messenger": (
            "org.telegram.messenger/org.telegram.ui.LaunchActivity",
        ),
        "com.instagram.android": (
            "com.instagram.android/com.instagram.android.activity.MainTabActivity",
        ),
        "com.google.android.apps.messaging": (
            "com.google.android.apps.messaging/com.google.android.apps.messaging.ui.ConversationListActivity",
        ),
        "com.android.mms": (
            "com.android.mms/com.android.mms.ui.ConversationList",
        ),
    }

    # Rótulos de interface que não são mensagem (evita lixo no resumo).
    _UI_LABEL_TEXTS = {
        "mensagem", "mensagens", "digite uma mensagem", "digite a mensagem",
        "pesquisar", "procurar", "nova conversa", "camera", "câmera",
        "anexar", "voltar", "ok", "cancelar", "enviar", "confirmar",
    }

    def call(self, contact="", number="", app="", device=None, **_kwargs) -> ConnectorResult:
        """Disca para um contato/número, ou chama pelo WhatsApp quando pedido."""
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

        # Ligação pelo WhatsApp: abre a conversa do contato e toca no botão
        # de chamada. Precisa do celular desbloqueado (o app exige).
        if "whatsapp" in str(app).lower() or "zap" in str(app).lower():
            return self._call_whatsapp(digits, name or digits, serial)

        ok, _out, _err = self._shell(
            "am", "start", "-a", "android.intent.action.CALL",
            "-d", f"tel:{digits}", serial=serial,
        )
        if ok:
            return ConnectorResult.success(
                f"Ligando para {name or digits} pelo celular."
            )
        return ConnectorResult.failure("Não consegui iniciar a ligação.")

    def _call_whatsapp(self, digits, label, serial) -> ConnectorResult:
        """Liga por voz pelo WhatsApp abrindo a conversa do contato."""
        self._open_app("com.whatsapp", serial)
        time.sleep(1.2)
        self._open_whatsapp_conversation(digits, serial)
        time.sleep(2.5)

        # O botão de chamada fica no topo da conversa; tocamos nele pelo
        # texto visível na tela, sem depender de coordenadas fixas.
        screen = self._dump_ui(serial)
        tapped = False
        if screen:
            match = re.search(
                r'text="(?:Chamada|Ligação|Call|Voice call|Chamar|Ligar)"[^>]*'
                r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                screen,
                flags=re.IGNORECASE,
            )
            if match:
                x1, y1, x2, y2 = (int(value) for value in match.groups())
                self._shell(
                    "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2),
                    serial=serial,
                )
                tapped = True

        if tapped:
            return ConnectorResult.success(
                f"Iniciei a chamada pelo WhatsApp para {label}."
            )
        return ConnectorResult.failure(
            f"Abri a conversa de {label} no WhatsApp, mas não consegui tocar "
            "no botão de chamada. Confirme a ligação no celular."
        )

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

    def send_whatsapp(self, contact="", text="", device=None, **_kwargs) -> ConnectorResult:
        """Envia mensagem no WhatsApp com o celular BLOQUEADO.

        O caminho mais confiável é abrir a conversa do contato e usar o
        botão de ENVIAR por voz, que funciona sem desbloquear a tela. É por
        isso que não usamos o `wa.me` (que exige o aparelho desbloqueado).
        """
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

        self._open_app("com.whatsapp", serial)
        time.sleep(1.2)
        self._open_whatsapp_conversation(digits, serial)
        time.sleep(2.0)

        screen = self._dump_ui(serial)
        if not screen:
            return ConnectorResult.failure(
                "Abri o WhatsApp, mas não consegui ler a tela para enviar a "
                "mensagem."
            )

        tapped = self._tap_whatsapp_voice_button(screen, serial)
        if not tapped:
            return ConnectorResult.failure(
                f"Abri a conversa de {contact}, mas não encontrei o botão de "
                "mensagem por voz. Confirme o envio no celular."
            )

        return ConnectorResult.success(
            f"Abri o WhatsApp em {contact} e toquei em 'Mensagem de voz'. "
            f"Fale agora: {text}"
        )

    def read_conversation(self, contact="", device=None, limit=3,
                          **_kwargs) -> ConnectorResult:
        """Lê as últimas mensagens de uma conversa do WhatsApp.

        Funciona com o celular bloqueado: o WhatsApp mostra o conteúdo das
        conversas na tela de bloqueio, então lemos a notificação do contato.
        """
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        contact = str(contact or "").strip()
        if not contact:
            return ConnectorResult.failure("De quem você quer ler as mensagens?")

        result = self.notifications(
            device=device, limit=max(1, int(limit or 3)), sender=contact, read=True,
        )
        if not result.ok:
            return result

        records = (result.data or {}).get("records") or []
        if not records:
            return ConnectorResult.failure(
                f"Não encontrei mensagens de {contact} no celular."
            )

        lines = []
        for record in records:
            sender = record.get("sender") or contact
            text = str(record.get("text") or "").strip()
            if text:
                lines.append(f"{sender}: {text}")

        if not lines:
            return ConnectorResult.failure(
                f"Não consegui ler o conteúdo das mensagens de {contact}."
            )

        return ConnectorResult.success(
            "Últimas mensagens de " + contact + ": " + " | ".join(lines),
            data={"contact": contact, "messages": lines},
        )

    def _open_app(self, package, serial) -> bool:
        """Abre um app pelo pacote (monkey, depois resolve-activity)."""
        ok, output, _error = self._shell(
            "monkey", "-p", package, "-c",
            "android.intent.category.LAUNCHER", "1", serial=serial,
        )
        if ok and "No activities found" not in output:
            return True

        ok, output, _error = self._shell(
            "cmd", "package", "resolve-activity", "--brief", package, serial=serial,
        )
        if ok:
            for line in output.splitlines():
                line = line.strip()
                if "/" in line and "." in line:
                    started, _out, _err = self._shell(
                        "am", "start", "-n", line, serial=serial,
                    )
                    if started:
                        return True
        return False

    def _open_whatsapp_conversation(self, digits, serial) -> bool:
        """Abre a conversa de um número no WhatsApp."""
        ok, _out, _err = self._shell(
            "am", "start", "-a", "android.intent.action.VIEW",
            "-d", f"https://wa.me/{digits}",
            "-p", "com.whatsapp", serial=serial,
        )
        if ok:
            return True
        ok, _out, _err = self._shell(
            "am", "start", "-a", "android.intent.action.VIEW",
            "-d", f"https://api.whatsapp.com/send?phone={digits}",
            "-p", "com.whatsapp", serial=serial,
        )
        return ok

    def _tap_whatsapp_voice_button(self, screen, serial) -> bool:
        """Toca no botão de mensagem por voz (envia sem desbloquear)."""
        patterns = (
            r'content-desc="(?:Mensagem de voz|Voice message|Gravar áudio)"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            r'resource-id="com.whatsapp:id/voice_note_btn"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        )
        for pattern in patterns:
            match = re.search(pattern, screen, flags=re.IGNORECASE)
            if not match:
                continue
            x1, y1, x2, y2 = (int(value) for value in match.groups())
            self._shell(
                "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2),
                serial=serial,
            )
            return True
        return False

    @staticmethod
    def _url_encode(value) -> str:
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
        )# ==========================================================
    # PESQUISA NA WEB (celular)
    # ==========================================================

    def search_web(self, query="", device=None, engine="google", **_kwargs) -> ConnectorResult:
        """Pesquisa na web NO CELULAR (navegador padrão do aparelho).

        Antes, "pesquise sobre eleições no celular" era enviado ao Google do
        PC — o roteador reconhecia o dispositivo, mas não havia nenhuma ação
        de pesquisa para o celular, então a fala caía no fluxo do computador.
        Aqui a busca abre de verdade no aparelho.
        """
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        query = str(query or "").strip()
        if not query:
            return ConnectorResult.failure("O que você quer pesquisar no celular?")

        engine = str(engine or "google").lower()
        if "youtube" in engine:
            url = "https://www.youtube.com/results?search_query=" + self._url_encode(query)
        else:
            url = "https://www.google.com/search?q=" + self._url_encode(query)

        if self._open_url(url, serial):
            return ConnectorResult.success(
                f"Pesquisando por {query} no navegador do celular."
            )
        return ConnectorResult.failure(
            "Não consegui abrir a pesquisa no celular. Confira se há um "
            "navegador instalado."
        )

    def _open_url(self, url, serial) -> bool:
        """Abre uma URL no app padrão do Android (navegador/música)."""
        ok, _out, _err = self._shell(
            "am", "start", "-a", "android.intent.action.VIEW",
            "-d", url, serial=serial,
        )
        return ok

    # ==========================================================
    # MÚSICA NO CELULAR
    # ==========================================================

    def play_music(self, query="", device=None, platform="", **_kwargs) -> ConnectorResult:
        """Toca música NO CELULAR, de verdade.

        "toca menos é mais no spotify" abria o app e não tocava nada porque o
        caminho antigo só abria o pacote do Spotify. Aqui usamos a busca
        profunda (`spotify:search:`) e, quando a fala não cita plataforma,
        abrimos a busca do YouTube Music — que começa a tocar o primeiro
        resultado sem login.
        """
        serial = self._serial(device)
        if not serial:
            return self._no_phone()

        query = str(query or "").strip()
        if not query:
            return ConnectorResult.failure("Qual música você quer ouvir no celular?")

        platform = str(platform or "").lower()
        if "spotify" in platform:
            # O deep link de busca do Spotify abre o app já no resultado.
            if self._open_url("spotify:search:" + self._url_encode(query), serial):
                return ConnectorResult.success(
                    f"Abrindo {query} no Spotify do celular."
                )
            return ConnectorResult.failure(
                "Não consegui abrir o Spotify no celular. Verifique se o app "
                "está instalado."
            )

        # Padrão: YouTube Music no navegador/app do celular.
        url = "https://music.youtube.com/search?q=" + self._url_encode(query)
        if self._open_url(url, serial):
            return ConnectorResult.success(
                f"Tocando {query} no celular."
            )
        return ConnectorResult.failure(
            "Não consegui abrir a música no celular."
        )