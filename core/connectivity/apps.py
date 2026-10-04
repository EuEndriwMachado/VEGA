# -*- coding: utf-8 -*-
"""Dicionário de aplicativos Android: nome comum -> pacote.

Permite falar "abre o instagram no celular" sem saber o pacote
(`com.instagram.android`). Cobre os apps mais usados no Brasil e aceita
correspondência parcial, então variações faladas ("whats", "zap",
"insta") também funcionam.

O usuário pode adicionar os próprios apelidos em `data/memory/apps.json`
sem tocar no código — o arquivo é mesclado por cima do dicionário padrão.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata

# nome normalizado -> (pacote, rótulo legível)
DEFAULT_APPS = {
    # Redes sociais
    "instagram": ("com.instagram.android", "Instagram"),
    "insta": ("com.instagram.android", "Instagram"),
    "facebook": ("com.facebook.katana", "Facebook"),
    "face": ("com.facebook.katana", "Facebook"),
    "twitter": ("com.twitter.android", "X"),
    "x": ("com.twitter.android", "X"),
    "tiktok": ("com.zhiliaoapp.musically", "TikTok"),
    "tik tok": ("com.zhiliaoapp.musically", "TikTok"),
    "linkedin": ("com.linkedin.android", "LinkedIn"),
    "reddit": ("com.reddit.frontpage", "Reddit"),
    "pinterest": ("com.pinterest", "Pinterest"),
    "threads": ("com.instagram.barcelona", "Threads"),

    # Mensagens
    "whatsapp": ("com.whatsapp", "WhatsApp"),
    "whats": ("com.whatsapp", "WhatsApp"),
    "zap": ("com.whatsapp", "WhatsApp"),
    "telegram": ("org.telegram.messenger", "Telegram"),
    "messenger": ("com.facebook.orca", "Messenger"),
    "signal": ("org.thoughtcrime.securesms", "Signal"),
    "discord": ("com.discord", "Discord"),

    # Música e vídeo
    "youtube": ("com.google.android.youtube", "YouTube"),
    "youtube music": ("com.google.android.apps.youtube.music", "YouTube Music"),
    "spotify": ("com.spotify.music", "Spotify"),
    "deezer": ("deezer.android.app", "Deezer"),
    "netflix": ("com.netflix.mediaclient", "Netflix"),
    "prime video": ("com.amazon.avod.thirdpartyclient", "Prime Video"),
    "disney": ("com.disney.disneyplus", "Disney+"),
    "disney plus": ("com.disney.disneyplus", "Disney+"),
    "twitch": ("tv.twitch.android.app", "Twitch"),

    # Google
    "chrome": ("com.android.chrome", "Chrome"),
    "maps": ("com.google.android.apps.maps", "Google Maps"),
    "google maps": ("com.google.android.apps.maps", "Google Maps"),
    "gmail": ("com.google.android.gm", "Gmail"),
    "email": ("com.google.android.gm", "Gmail"),
    "drive": ("com.google.android.apps.docs", "Google Drive"),
    "fotos": ("com.google.android.apps.photos", "Google Fotos"),
    "calendar": ("com.google.android.calendar", "Agenda"),
    "agenda": ("com.google.android.calendar", "Agenda"),

    # Utilitários (pacotes do MIUI/Xiaomi, que a VEGA também cobre por
    # variantes de fabricante em PACKAGE_VARIANTS).
    "camera": ("com.android.camera", "Câmera"),
    "galeria": ("com.miui.gallery", "Galeria"),
    "configuracoes": ("com.android.settings", "Configurações"),
    "relogio": ("com.android.deskclock", "Relógio"),
    "contatos": ("com.android.contacts", "Contatos"),
    "telefone": ("com.android.dialer", "Telefone"),
    "mensagens": ("com.android.mms", "Mensagens"),
    "calculadora": ("com.miui.calculator", "Calculadora"),
    "notas": ("com.miui.notes", "Notas"),
    "gravador": ("com.android.soundrecorder", "Gravador"),
    "arquivos": ("com.miui.filemanager", "Arquivos"),
    "clima": ("com.miui.weather2", "Clima"),
    "musica": ("com.miui.player", "Música"),
    "seguranca": ("com.miui.securitycenter", "Segurança"),
    "navegador": ("com.android.browser", "Navegador"),

    # Compras / bancos comuns no Brasil
    "mercado livre": ("com.mercadolibre", "Mercado Livre"),
    "shopee": ("com.shopee.br", "Shopee"),
    "amazon": ("com.amazon.mShop.android.shopping", "Amazon"),
    "nubank": ("com.nu.production", "Nubank"),
    "picpay": ("com.picpay", "PicPay"),
    "ifood": ("br.com.brainweb.ifood", "iFood"),
    "uber": ("com.ubercab", "Uber"),
    "99": ("com.taxis99", "99"),

    # Jogos comuns
    "free fire": ("com.dts.freefireth", "Free Fire"),
    "minecraft": ("com.mojang.minecraftpe", "Minecraft"),
    "roblox": ("com.roblox.client", "Roblox"),
    "clash royale": ("com.supercell.clashroyale", "Clash Royale"),
}


# Pacotes alternativos por fabricante: se o pacote padrão não estiver
# instalado no aparelho, a VEGA tenta estes antes de desistir.
# Chave = nome normalizado do app; valor = lista de pacotes candidatos.
PACKAGE_VARIANTS = {
    "calculadora": [
        "com.miui.calculator",
        "com.google.android.calculator",
        "com.sec.android.app.popupcalculator",
        "com.android.calculator2",
        "com.coloros.calculator",
        "com.oneplus.calculator",
    ],
    "galeria": [
        "com.miui.gallery",
        "com.google.android.apps.photos",
        "com.sec.android.gallery3d",
        "com.coloros.gallery3d",
        "com.android.gallery3d",
    ],
    "camera": [
        "com.android.camera",
        "com.miui.camera",
        "com.sec.android.app.camera",
        "com.google.android.GoogleCamera",
        "com.oplus.camera",
    ],
    "relogio": [
        "com.android.deskclock",
        "com.miui.clock",
        "com.sec.android.app.clockpackage",
        "com.coloros.alarmclock",
        "com.google.android.deskclock",
    ],
    "contatos": [
        "com.android.contacts",
        "com.miui.contacts",
        "com.sec.android.app.contacts",
        "com.google.android.contacts",
    ],
    "telefone": [
        "com.android.dialer",
        "com.miui.dialer",
        "com.sec.android.app.dialertab",
        "com.google.android.dialer",
        "com.android.phone",
    ],
    "mensagens": [
        "com.android.mms",
        "com.miui.smsextra",
        "com.google.android.apps.messaging",
        "com.samsung.android.messaging",
    ],
    "musica": [
        "com.miui.player",
        "com.google.android.apps.youtube.music",
        "com.spotify.music",
        "com.sec.android.app.music",
    ],
    "arquivos": [
        "com.miui.filemanager",
        "com.mi.android.globalFileexplorer",
        "com.android.documentsui",
        "com.google.android.documentsui",
        "com.google.android.apps.nbu.files",
        "com.sec.android.app.myfiles",
    ],
    "notas": [
        "com.miui.notes",
        "com.google.android.keep",
        "com.samsung.android.app.notes",
        "com.coloros.note",
    ],
    "clima": [
        "com.miui.weather2",
        "com.google.android.apps.weather",
        "com.sec.android.daemonapp",
    ],
    "navegador": [
        "com.android.browser",
        "com.android.chrome",
        "com.miui.browser",
        "com.sec.android.app.sbrowser",
    ],
}


def _normalize(value) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class AppDictionary:
    """Resolve nomes falados de apps para o pacote Android correspondente."""

    def __init__(self, extra_path="data/memory/apps.json"):
        self.extra_path = extra_path
        self._apps = dict(DEFAULT_APPS)
        self._load_extra()

    def _load_extra(self):
        """Mescla apelidos definidos pelo usuário (sobrepõem os padrões)."""
        try:
            if not os.path.exists(self.extra_path):
                return
            with open(self.extra_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        for name, value in payload.items():
            key = _normalize(name)
            if not key:
                continue
            if isinstance(value, dict):
                package = str(value.get("package", "")).strip()
                label = str(value.get("label", name)).strip()
            else:
                package = str(value).strip()
                label = name
            if package:
                self._apps[key] = (package, label)

    def add(self, name, package, label="") -> bool:
        """Adiciona/atualiza um app e persiste em disco."""
        key = _normalize(name)
        package = str(package or "").strip()
        if not key or not package:
            return False

        self._apps[key] = (package, label or name)

        payload = {}
        try:
            if os.path.exists(self.extra_path):
                with open(self.extra_path, "r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict):
                    payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}

        payload[str(name).strip()] = {
            "package": package,
            "label": label or name,
        }

        folder = os.path.dirname(self.extra_path)
        try:
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.extra_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
        except OSError:
            pass

        return True

    def resolve(self, name) -> str:
        """Devolve o pacote do app, ou '' se não reconhecido.

        Já devolve o próprio valor quando ele se parece com um pacote
        (`com.alguma.coisa`), permitindo o usuário falar o pacote direto.
        """
        raw = str(name or "").strip()
        if not raw:
            return ""

        # Já é um nome de pacote.
        if re.fullmatch(r"[a-zA-Z][\w]*(\.[\w]+)+", raw) and "." in raw:
            return raw

        # Aplica as correções de transcrição de voz já usadas pela VEGA
        # (ex.: "spotfy" -> "spotify") antes de procurar.
        try:
            from core.brain.text_utils import normalize_text
            raw = normalize_text(raw) or raw
        except Exception:
            pass

        key = _normalize(raw)
        if key in self._apps:
            return self._apps[key][0]

        # Correspondência parcial: "insta" casa com "instagram".
        # Exige que a chave seja um prefixo/sufixo de pelo menos 4 caracteres
        # e que a palavra não seja genérica, para não abrir o app errado.
        if len(key) >= 4:
            for app_key, (package, _label) in self._apps.items():
                if len(app_key) >= 4 and (app_key.startswith(key) or key.startswith(app_key)):
                    return package

        # Último recurso: nome composto com todas as palavras casando.
        # (Evita que "app que nao existe" vire "X" só por conter uma letra.)
        words = [word for word in key.split() if len(word) >= 4]
        if words:
            for app_key, (package, _label) in self._apps.items():
                app_words = app_key.split()
                if all(any(word in app_word or app_word.startswith(word)
                           for app_word in app_words) for word in words):
                    return package

        return ""

    def candidates(self, name) -> list:
        """Lista de pacotes possíveis para o app, na ordem de preferência.

        Útil quando o pacote padrão não existe no aparelho: o chamador pode
        testar cada candidato até encontrar um instalado.
        """
        primary = self.resolve(name)
        if not primary:
            return []

        candidates = [primary]

        # Acrescenta as variantes de fabricante conhecidas para este app.
        key = _normalize(str(name or ""))
        for variant_key, packages in PACKAGE_VARIANTS.items():
            if key == variant_key or key.startswith(variant_key) or variant_key.startswith(key):
                for package in packages:
                    if package not in candidates:
                        candidates.append(package)

        # Também considera variantes do pacote primário.
        for variant_key, packages in PACKAGE_VARIANTS.items():
            if primary in packages:
                for package in packages:
                    if package not in candidates:
                        candidates.append(package)

        return candidates

    def label(self, name) -> str:
        """Nome amigável do app (para a resposta falada)."""
        raw = str(name or "").strip()
        key = _normalize(raw)
        if key in self._apps:
            return self._apps[key][1]
        for app_key, (_package, label) in self._apps.items():
            if key and (key in app_key or app_key in key):
                return label
        return raw

    def known_names(self) -> list:
        """Lista os nomes conhecidos (para diagnóstico)."""
        return sorted(self._apps.keys())