import os
import sys
import platform
import subprocess
import webbrowser
import shutil
import re
import ctypes
import time
import threading
import unicodedata

from pathlib import Path
from urllib.parse import quote_plus

from core.actions.music_playlist import PersonalPlaylistService
from core.actions import spotify_bridge


# ===========================================================
# CACHE DE LATÊNCIA — MENU INICIAR / EXECUTÁVEIS
# Antes, cada "abre X" varria TODOS os .lnk do Menu Iniciar
# (e, em fallback, todos os .exe de Program Files/LocalAppData)
# no disco: centenas de milhares de arquivos, vários segundos.
# Agora o índice é construído 1x e revalidado por TTL em background.
# ===========================================================

_START_MENU_TTL = 300.0  # segundos
_start_menu_index = {}
_start_menu_index_at = 0.0
_start_menu_lock = threading.Lock()

_executable_cache = {}
_executable_cache_lock = threading.Lock()

# CAMINHO DO CHROME em cache (sem TTL de disco: invalida sozinho se o
# executável sumir). Antes, cada "abre o chrome" podia pagar um spawn de
# PowerShell (1-3s) para ler o registro quando os caminhos conhecidos falhavam.
_chrome_path = None
_chrome_path_lock = threading.Lock()


def _build_start_menu_index():
    """Varre os .lnk do Menu Iniciar 1x e devolve {nome_normalizado: Path}.
    Nomes colididos mantêm o caminho mais curto (mais provável de ser o app)."""
    index = {}
    for start_path in VegaCommands._get_start_menu_paths():
        try:
            for shortcut in start_path.rglob("*.lnk"):
                name = VegaCommands._normalize_name(shortcut.stem)
                if not name:
                    continue
                existing = index.get(name)
                if existing is None or len(str(shortcut)) < len(str(existing)):
                    index[name] = shortcut
        except Exception:
            continue
    return index


def _get_start_menu_index():
    """Índice com TTL: após 5 min, o próximo lookup reconstrói em background
    (o lookup atual usa o cache — nunca bloqueia o comando)."""
    global _start_menu_index, _start_menu_index_at
    with _start_menu_lock:
        index = _start_menu_index
        built_at = _start_menu_index_at
    if index and time.monotonic() - built_at < _START_MENU_TTL:
        return index
    with _start_menu_lock:
        index = _start_menu_index
        built_at = _start_menu_index_at
        if index and time.monotonic() - built_at < _START_MENU_TTL:
            return index
        if not index or time.monotonic() - built_at > _START_MENU_TTL:
            if built_at == 0.0:
                # Primeira construção: precisa bloquear (não há cache).
                _start_menu_index = _build_start_menu_index()
                _start_menu_index_at = time.monotonic()
                return _start_menu_index
            # Revalidação em background: usa o cache antigo agora.
            threading.Thread(
                target=_refresh_start_menu_index,
                name="VegaStartMenuIndexRefresh",
                daemon=True,
            ).start()
        return index


def _refresh_start_menu_index():
    global _start_menu_index, _start_menu_index_at
    with _start_menu_lock:
        _start_menu_index = _build_start_menu_index()
        _start_menu_index_at = time.monotonic()


class VegaCommands:

    def __init__(self, ai=None, memory=None):
        self.ai = ai
        self.memory = memory
        self.playlists = PersonalPlaylistService(memory=memory)
        self._register_instance()
        # Pré-aquece o índice do Menu Iniciar em background: o primeiro
        # "abre X" não paga a varredura do disco (~0,5-2s).
        threading.Thread(
            target=_refresh_start_menu_index,
            name="VegaStartMenuIndexWarmup",
            daemon=True,
        ).start()

    # ==========================================================
    # UTILIDADES WINDOWS
    # ==========================================================

    @staticmethod
    def _is_windows():
        return platform.system() == "Windows"

    @staticmethod
    def _run_powershell(command, timeout=5):
        try:
            return subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command
                ],
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except Exception:
            return None

    # ==========================================================
    # TECLAS WINDOWS
    # ==========================================================

    @staticmethod
    def _press_key(vk):
        if not VegaCommands._is_windows():
            return False

        try:
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _hotkey(*keys):
        if not VegaCommands._is_windows():
            return False

        try:
            for key in keys:
                ctypes.windll.user32.keybd_event(key, 0, 0, 0)

            for key in reversed(keys):
                ctypes.windll.user32.keybd_event(key, 0, 2, 0)

            return True
        except Exception:
            return False

    # ==========================================================
    # CHROME
    # ==========================================================

    @staticmethod
    def _find_chrome():
        """
        Procura especificamente o chrome.exe real.
        Não usa ChromeSetup.exe.

        CACHE: o resultado fica em memória (não precisa TTL: se o executável
        deixar de existir, o cache é descartado na próxima chamada). Sem isto,
        cada "abre o chrome" podia pagar um spawn de PowerShell para ler o
        registro do Windows quando os caminhos conhecidos falhavam.
        """

        if not VegaCommands._is_windows():
            return None

        global _chrome_path
        with _chrome_path_lock:
            cached = _chrome_path
        if cached is not None:
            if cached.exists():
                return cached
            with _chrome_path_lock:
                _chrome_path = None

        candidates = []

        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("PROGRAMFILES")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)")

        if local_app_data:
            candidates.append(
                Path(local_app_data)
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            )

        if program_files:
            candidates.append(
                Path(program_files)
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            )

        if program_files_x86:
            candidates.append(
                Path(program_files_x86)
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            )

        # Verificação direta dos caminhos conhecidos.
        for chrome in candidates:
            try:
                if chrome.is_file():
                    with _chrome_path_lock:
                        _chrome_path = chrome
                    return chrome
            except Exception:
                continue

        # Registro do Windows como segunda tentativa.
        powershell = VegaCommands._run_powershell(
            r"""
            $paths = @(
                "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            )

            foreach ($path in $paths) {
                try {
                    $value = (Get-ItemProperty -Path $path -ErrorAction Stop).'(default)'
                    if ($value) {
                        Write-Output $value
                        exit
                    }
                }
                catch {}
            }
            """
        )

        if powershell and powershell.returncode == 0:
            found = powershell.stdout.strip().strip('"')

            if found:
                path = Path(found)

                if path.is_file() and path.name.lower() == "chrome.exe":
                    with _chrome_path_lock:
                        _chrome_path = path
                    return path

        # Última tentativa: PATH.
        found = shutil.which("chrome.exe")

        if found:
            path = Path(found)

            if path.is_file() and path.name.lower() == "chrome.exe":
                with _chrome_path_lock:
                    _chrome_path = path
                return path

        return None

    @staticmethod
    def open_chrome():
        """
        Abre o Google Chrome instalado no computador.
        Nunca abre ChromeSetup.exe.
        """

        if not VegaCommands._is_windows():
            webbrowser.open("https://www.google.com")
            return "Abrindo o Chrome."

        chrome = VegaCommands._find_chrome()

        if chrome:
            try:
                subprocess.Popen(
                    [str(chrome)],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )

                return "Abrindo o Chrome."

            except Exception:
                pass

        # Fallback usando o protocolo do Chrome.
        try:
            os.startfile("googlechrome:")

            return "Abrindo o Chrome."

        except Exception:
            pass

        return (
            "Não encontrei o Google Chrome instalado. "
            "Não vou abrir o instalador automaticamente."
        )

    # ==========================================================
    # ABRIR SITES
    # ==========================================================

    @staticmethod
    def open_google():
        webbrowser.open("https://www.google.com")
        return "Abrindo o Google."

    @staticmethod
    def open_youtube():
        webbrowser.open("https://www.youtube.com")
        return "Abrindo o YouTube."

    @staticmethod
    def open_canva():
        webbrowser.open("https://www.canva.com")
        return "Abrindo o Canva."

    @staticmethod
    def open_whatsapp():

        if not VegaCommands._is_windows():
            webbrowser.open("https://web.whatsapp.com")
            return "Abrindo o WhatsApp Web."

        try:
            os.startfile("whatsapp:")
            return "Abrindo o WhatsApp."
        except Exception:
            pass

        shortcut = VegaCommands._find_start_menu_app("whatsapp")

        if shortcut and VegaCommands._open_shortcut(shortcut):
            return "Abrindo o WhatsApp."

        executable = VegaCommands._find_executable("whatsapp")

        if executable:
            try:
                subprocess.Popen([str(executable)])
                return "Abrindo o WhatsApp."
            except Exception:
                pass

        webbrowser.open("https://web.whatsapp.com")

        return (
            "Não encontrei o aplicativo WhatsApp. "
            "Abrindo o WhatsApp Web."
        )

    # ==========================================================
    # BANCO DE DADOS
    # ==========================================================

    _database_callback = None

    @classmethod
    def register_database_callback(cls, callback):
        """Registra callback/sinal Qt para exibir a interface do banco de dados."""
        cls._database_callback = callback

    @classmethod
    def open_database(cls):
        """
        Abre o visualizador do banco de dados da VEGA.
        Aciona a janela Qt já ativa se houver callback; caso contrário,
        inicia o visualizador de banco como processo independente.
        """
        callback = getattr(cls, "_database_callback", None)
        if callable(callback):
            try:
                callback()
                return "Abrindo seu banco de dados."
            except Exception as error:
                print(f"[VegaCommands] Erro no callback do banco de dados: {error}")

        viewer_path = Path(__file__).resolve().parents[2] / "database" / "viewer.py"
        if viewer_path.is_file():
            try:
                flags = subprocess.CREATE_NEW_PROCESS_GROUP if cls._is_windows() else 0
                subprocess.Popen(
                    [sys.executable, str(viewer_path), "--source", "auto"],
                    creationflags=flags,
                )
                return "Abrindo seu banco de dados."
            except Exception as error:
                return f"Não consegui abrir o banco de dados: {error}"

        return "Não encontrei o visualizador de banco de dados."

    # ==========================================================
    # SPOTIFY
    # ==========================================================

    @staticmethod
    def open_spotify():

        if not VegaCommands._is_windows():
            return (
                "O Spotify pelo aplicativo está "
                "configurado para Windows."
            )

        try:
            os.startfile("spotify:")
            return "Abrindo o Spotify."
        except Exception:
            pass

        shortcut = VegaCommands._find_start_menu_app("spotify")

        if shortcut and VegaCommands._open_shortcut(shortcut):
            return "Abrindo o Spotify."

        executable = VegaCommands._find_executable("spotify")

        if executable:
            try:
                subprocess.Popen([str(executable)])
                return "Abrindo o Spotify."
            except Exception:
                pass

        return "Não consegui encontrar o aplicativo Spotify instalado."

    # ==========================================================
    # PASTAS
    # ==========================================================

    @staticmethod
    def _open_folder(folder, name):

        if not folder.exists():
            return f"Não encontrei a pasta {name}."

        try:
            if VegaCommands._is_windows():
                os.startfile(str(folder))
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])

            return f"Abrindo {name}."

        except Exception as error:
            return f"Não consegui abrir {name}: {error}"

    @staticmethod
    def open_documents():
        documents = Path.home() / "Documents"

        if not documents.exists():
            documents = Path.home() / "Documentos"

        return VegaCommands._open_folder(
            documents,
            "seus documentos"
        )

    @staticmethod
    def open_downloads():
        return VegaCommands._open_folder(
            Path.home() / "Downloads",
            "seus downloads"
        )

    @staticmethod
    def open_desktop():

        desktop = Path.home() / "Desktop"

        if not desktop.exists():
            desktop = Path.home() / "Área de Trabalho"

        return VegaCommands._open_folder(
            desktop,
            "sua área de trabalho"
        )

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    @staticmethod
    def _normalize_name(name):

        if not name:
            return ""

        name = str(name).lower().strip()
        name = unicodedata.normalize("NFD", name)
        name = "".join(char for char in name if unicodedata.category(char) != "Mn")

        replacements = {
            "spotfy": "spotify",
            "spotif": "spotify",
            "spofity": "spotify",
            "spotifyy": "spotify",
            "vs code": "visual studio code",
            "vscode": "visual studio code",
            "visualstudio code": "visual studio code",
            "whats app": "whatsapp",
            "what's app": "whatsapp",
            "google chrome": "chrome",
            "microsoft edge": "edge",
            "configuracoes": "configuracoes",
            "gerenciador de tarefas": "gerenciador de tarefas",
        }

        for old, new in replacements.items():
            if name == old:
                name = new

        name = re.sub(r"\.(exe|lnk|url)$", "", name)

        name = re.sub(
            r"[^a-z0-9\s\-–—]",
            " ",
            name
        )

        name = name.replace("-", " ").replace("–", " ").replace("—", " ")

        name = re.sub(r"\s+", " ", name)

        return name.strip()

    # ==========================================================
    # MENU INICIAR
    # ==========================================================

    @staticmethod
    def _get_start_menu_paths():

        if not VegaCommands._is_windows():
            return []

        paths = []

        appdata = os.environ.get("APPDATA")
        programdata = os.environ.get("PROGRAMDATA")

        if appdata:
            path = (
                Path(appdata)
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
            )

            if path.exists():
                paths.append(path)

        if programdata:
            path = (
                Path(programdata)
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
            )

            if path.exists():
                paths.append(path)

        return paths

    @staticmethod
    def _find_start_menu_app(app_name):

        target = VegaCommands._normalize_name(app_name)

        if not target:
            return None

        # CACHE: o índice completo é construído 1x (5 min de TTL) e revalidado
        # em background. Nunca mais varre o disco em cada comando.
        index = _get_start_menu_index()

        exact = index.get(target)
        if exact is not None and exact.exists():
            return exact

        candidates = []

        for shortcut_name, shortcut in index.items():

            if target in shortcut_name:
                candidates.append((shortcut, shortcut_name))

            elif shortcut_name in target:
                candidates.append((shortcut, shortcut_name))

        if candidates:
            candidates.sort(key=lambda item: len(item[1]))
            best = candidates[0][0]
            if best.exists():
                return best

        return None

    # ==========================================================
    # EXECUTÁVEIS
    # ==========================================================

    @staticmethod
    def _find_matching_processes(app_name):

        if not VegaCommands._is_windows():
            return []

        target = VegaCommands._normalize_name(app_name)

        if not target:
            return []

        safe_name = target.replace("'", "").replace('"', "")

        command = (
            "Get-CimInstance Win32_Process | "
            "Select-Object -ExpandProperty Name | "
            "Where-Object { $_ -and $_.ToString().ToLower() -like '*"
            f"{safe_name.lower()}*'"
            " } | Select-Object -Unique"
        )

        result = VegaCommands._run_powershell(command, timeout=5)

        if not result or result.returncode != 0:
            return []

        matches = []

        for line in result.stdout.splitlines():
            process_name = line.strip()
            if not process_name:
                continue
            if process_name.lower().endswith(".exe"):
                matches.append(process_name)

        return matches

    @staticmethod
    def _find_executable(app_name):

        target = VegaCommands._normalize_name(app_name)

        if not target:
            return None

        aliases = {
            "chrome": ["chrome.exe"],
            "discord": ["discord.exe"],
            "spotify": ["spotify.exe"],
            "steam": ["steam.exe"],
            "epic games": ["EpicGamesLauncher.exe"],
            "epic games launcher": ["EpicGamesLauncher.exe"],
            "visual studio code": ["code.exe"],
            "edge": ["msedge.exe"],
            "firefox": ["firefox.exe"],
            "vlc": ["vlc.exe"],
            "word": ["winword.exe"],
            "excel": ["excel.exe"],
            "powerpoint": ["powerpnt.exe"],
            "teams": ["ms-teams.exe"],
            "whatsapp": ["WhatsApp.exe"],
        }

        names = aliases.get(target, [])

        # CACHE: resolução de executável por app fica em memória (TTL 5 min).
        with _executable_cache_lock:
            cached = _executable_cache.get(target)
        if cached is not None:
            if cached == "__MISS__":
                return None
            if cached.exists():
                return cached
            with _executable_cache_lock:
                _executable_cache.pop(target, None)

        for executable in names:

            found = shutil.which(executable)

            if found:
                path = Path(found)

                if path.name.lower() == executable.lower():
                    with _executable_cache_lock:
                        _executable_cache[target] = path
                    return path

        dirs = []

        for env_name in (
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "LOCALAPPDATA",
        ):

            value = os.environ.get(env_name)

            if value:
                path = Path(value)

                if path.exists():
                    dirs.append(path)

        found = None

        # NÍVEL 2 (síncrono): rglob do nome específico (com ou sem alias).
        # Procura o padrão direto — rápido o suficiente para o turno.
        for base in dirs:

            for executable_name in (names or [f"{target}.exe"]):

                try:
                    for exe in base.rglob(executable_name):

                        if exe.is_file():
                            found = exe
                            break

                    if found:
                        break

                except Exception:
                    continue

            if found:
                break

        if found:
            with _executable_cache_lock:
                _executable_cache[target] = found
            return found

        # NÍVEL 3 (pesado): varredura de TODOS os .exe com casamento difuso
        # de nome. Pode demorar segundos — vai para background e grava no
        # cache; o próximo "abre X" resolve sem custo. O turno atual segue
        # para os caminhos seguintes (Get-StartApps) sem bloquear.
        threading.Thread(
            target=VegaCommands._find_executable_slow,
            args=(target,),
            name=f"VegaExeLookup-{target[:20]}",
            daemon=True,
        ).start()

        return None

    @staticmethod
    def _find_executable_slow(app_name):
        """Varredura PESADA (todos os *.exe das pastas conhecidas), roda em
        background thread. Grava o resultado direto no cache — o próximo
        "abre X" resolve sem custo. Chamada pelo open_app quando os caminhos
        rápidos falharam; nunca bloqueia o turno.
        """
        target = VegaCommands._normalize_name(app_name)

        if not target:
            return

        aliases = {
            "chrome": ["chrome.exe"],
            "discord": ["discord.exe"],
            "spotify": ["spotify.exe"],
            "steam": ["steam.exe"],
            "epic games": ["EpicGamesLauncher.exe"],
            "epic games launcher": ["EpicGamesLauncher.exe"],
            "visual studio code": ["code.exe"],
            "edge": ["msedge.exe"],
            "firefox": ["firefox.exe"],
            "vlc": ["vlc.exe"],
            "word": ["winword.exe"],
            "excel": ["excel.exe"],
            "powerpoint": ["powerpnt.exe"],
            "teams": ["ms-teams.exe"],
            "whatsapp": ["WhatsApp.exe"],
        }

        names = aliases.get(target, [])
        if not names:
            return

        dirs = []

        for env_name in (
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "LOCALAPPDATA",
        ):

            value = os.environ.get(env_name)

            if value:
                path = Path(value)

                if path.exists():
                    dirs.append(path)

        # Fallback pesado: varre TODOS os .exe do disco. Só acontece aqui,
        # fora do turno — o resultado fica no cache para a próxima fala.
        found = None

        for base in dirs:
            try:
                for exe in base.rglob("*.exe"):
                    if not exe.is_file():
                        continue

                    exe_name = VegaCommands._normalize_name(exe.name)

                    if not exe_name:
                        continue

                    if exe_name == target or target in exe_name or exe_name in target:
                        found = exe
                        break

                if found:
                    break

            except Exception:
                continue

        # Grava no cache apenas se encontrou: um miss permanente aqui não
        # deve impedir o Get-StartApps de tentar no próximo turno.
        if found is not None:
            with _executable_cache_lock:
                _executable_cache[target] = found

        return found

    # ==========================================================
    # ATALHO
    # ==========================================================

    @staticmethod
    def _open_shortcut(shortcut):

        try:
            os.startfile(str(shortcut))
            return True
        except Exception:
            return False

    # ==========================================================
    # APLICATIVOS
    # ==========================================================

    @staticmethod
    def open_app(app_name):

        if not app_name or not app_name.strip():
            return "Qual aplicativo você quer que eu abra?"

        original_name = app_name.strip()

        app_name = VegaCommands._normalize_name(
            original_name
        )

        aliases = {
            "spotfy": "spotify",
            "spotif": "spotify",
            "spofity": "spotify",
            "vscode": "visual studio code",
            "vs code": "visual studio code",
            "calculadora": "calculadora",
            "calculator": "calculadora",
            "notepad": "bloco de notas",
            "task manager": "gerenciador de tarefas",
            "taskmgr": "gerenciador de tarefas",
            "config settings": "configuracoes",
            "settings": "configuracoes",
            "configuracao": "configuracoes",
            "configuracoes": "configuracoes",
            "ajustes": "configuracoes",
            "gerenciador de tarefas": "gerenciador de tarefas",
            "gerenciar tarefas": "gerenciador de tarefas",
        }

        app_name = aliases.get(app_name, app_name)
        display_name = original_name.strip()

        if app_name == "configuracoes":
            display_name = "configurações"
        elif app_name == "gerenciador de tarefas":
            display_name = "gerenciador de tarefas"

        # ======================================================
        # CHROME — CAMINHO DIRETO E SEGURO
        # ======================================================

        if app_name == "chrome":
            return VegaCommands.open_chrome()

        # ======================================================
        # APLICATIVOS DO WINDOWS
        # ======================================================

        windows_apps = {
            "calculadora": "calc.exe",
            "bloco de notas": "notepad.exe",
            "paint": "mspaint.exe",
            "cmd": "cmd.exe",
            "prompt de comando": "cmd.exe",
            "powershell": "powershell.exe",
            "explorador": "explorer.exe",
            "explorador de arquivos": "explorer.exe",
            "gerenciador de tarefas": "taskmgr.exe",
            "task manager": "taskmgr.exe",
            "painel de controle": "control.exe",
            "configuracoes": "ms-settings:",
            "settings": "ms-settings:",
            "ajustes": "ms-settings:",
        }

        if app_name in {"configuracoes", "settings", "ajustes"}:
            try:
                subprocess.Popen(
                    ["explorer.exe", "ms-settings:"],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                return f"Abrindo {display_name}."
            except Exception:
                pass

        executable = windows_apps.get(app_name)

        if executable:

            try:
                subprocess.Popen(
                    [executable],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )

                return f"Abrindo {original_name}."

            except Exception:
                pass

        # ======================================================
        # APLICATIVOS ESPECIAIS
        # ======================================================

        special_apps = {
            "spotify": VegaCommands.open_spotify,
            "google": VegaCommands.open_google,
            "youtube": VegaCommands.open_youtube,
            "canva": VegaCommands.open_canva,
            "whatsapp": VegaCommands.open_whatsapp,
            "banco de dados": VegaCommands.open_database,
            "banco": VegaCommands.open_database,
        }

        if app_name in special_apps:
            return special_apps[app_name]()

        # ======================================================
        # MENU INICIAR
        # ======================================================

        shortcut = VegaCommands._find_start_menu_app(
            app_name
        )

        if shortcut:

            if VegaCommands._open_shortcut(shortcut):
                return f"Abrindo {original_name}."

        # ======================================================
        # EXECUTÁVEL
        # ======================================================

        executable_path = VegaCommands._find_executable(
            app_name
        )

        if executable_path:

            try:
                subprocess.Popen(
                    [str(executable_path)],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )

                return f"Abrindo {original_name}."

            except Exception:
                pass

        # ======================================================
        # WINDOWS APPS
        # ======================================================

        if VegaCommands._is_windows():

            try:

                safe_name = (
                    original_name
                    .replace("'", "")
                    .replace('"', "")
                )

                result = VegaCommands._run_powershell(
                    (
                        "Get-StartApps | "
                        f"Where-Object {{$_.Name -like '*{safe_name}*'}} | "
                        "Select-Object -First 1 -ExpandProperty AppID"
                    )
                )

                if result:

                    app_id = result.stdout.strip()

                    if app_id:

                        subprocess.Popen(
                            [
                                "explorer.exe",
                                f"shell:AppsFolder\\{app_id}"
                            ]
                        )

                        return f"Abrindo {original_name}."

            except Exception:
                pass

        return (
            f"Não encontrei o aplicativo "
            f"{original_name} no computador."
        )

    # ==========================================================
    # FECHAR APLICATIVO
    # ==========================================================

    @staticmethod
    def close_app(app_name, force=False):

        if not app_name or not app_name.strip():
            return "Qual aplicativo você quer que eu feche?"

        if not VegaCommands._is_windows():
            return "O fechamento de aplicativos está configurado para Windows."

        app_name = VegaCommands._normalize_name(app_name)

        aliases = {
            "spotify": ["Spotify.exe"],
            "steam": ["steam.exe"],
            "epic games": ["EpicGamesLauncher.exe"],
            "epic games launcher": ["EpicGamesLauncher.exe"],
            "discord": ["Discord.exe"],
            "chrome": ["chrome.exe"],
            "edge": ["msedge.exe"],
            "firefox": ["firefox.exe"],
            "vscode": ["Code.exe"],
            "visual studio code": ["Code.exe"],
            "whatsapp": ["WhatsApp.exe"],
            "vlc": ["vlc.exe"],
            "bloco de notas": ["notepad.exe"],
            "notepad": ["notepad.exe"],
            "calculadora": ["CalculatorApp.exe", "calc.exe"],
            "calculator": ["CalculatorApp.exe", "calc.exe"],
            "paint": ["mspaint.exe"],
            "gerenciador de tarefas": ["taskmgr.exe"],
            "task manager": ["taskmgr.exe"],
            "configuracoes": ["SystemSettings.exe"],
            "settings": ["SystemSettings.exe"],
            "word": ["WINWORD.EXE"],
            "excel": ["EXCEL.EXE"],
            "powerpoint": ["POWERPNT.EXE"],
        }

        processes = aliases.get(app_name)

        if not processes:
            processes = VegaCommands._find_matching_processes(app_name)

        if not processes:
            return (
                f"Não sei qual processo corresponde "
                f"ao aplicativo {app_name}."
            )

        closed = False

        # Guarda contra perda de trabalho não salvo: taskkill /F mata sem
        # chance de salvar. Para estes apps, primeiro tenta o fechamento
        # gentil (WM_CLOSE via taskkill SEM /F); só força quando o usuário
        # já pediu de forma explícita e repetida ("fecha à força"), o que
        # o parser sinaliza pelo parâmetro.
        force = bool(force)
        _UNSAVEABLE = {"code.exe", "winword.exe", "excel.exe", "powerpnt.exe"}
        for process in processes:
            try:
                args = ["taskkill", "/IM", process, "/T"]
                if force or process.lower() not in _UNSAVEABLE:
                    args.append("/F")
                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result.returncode == 0:
                    closed = True

            except Exception:
                continue

        if closed:
            return f"Fechando {app_name}."

        return (
            f"O aplicativo {app_name} "
            f"não parece estar aberto."
        )

    # ==========================================================
    # VOLUME
    # ==========================================================

    # ----------------------------------------------------------
    # VOLUME — via Core Audio (pycaw) quando disponível.
    # Antes: teclas de volume (50x volume-down para zerar), lento e
    # dependente do estado atual. Com pycaw, o volume é absoluto em uma
    # chamada e também podemos LER o valor atual — o que habilita
    # "quanto tá o volume?" e passos relativos ("aumenta 10").
    # Fallback: teclas de mídia, como antes.
    # ----------------------------------------------------------

    _PYCAW_CACHE = {"ok": None}

    @staticmethod
    def _pycaw_endpoint():
        """Endpoint de volume (pycaw) ou None.

        Cache simples: se pycaw não estiver disponível no ambiente, não
        tentamos de novo a cada comando (o fallback de teclas continua).
        """
        if VegaCommands._PYCAW_CACHE["ok"] is False:
            return None
        try:
            from pycaw.pycaw import AudioUtilities
            device = AudioUtilities.GetSpeakers()
            VegaCommands._PYCAW_CACHE["ok"] = True
            return device.EndpointVolume
        except Exception:
            VegaCommands._PYCAW_CACHE["ok"] = False
            return None

    @staticmethod
    def _pycaw_volume():
        """(volume_atual_0_100, True) via pycaw, ou (None, False)."""
        endpoint = VegaCommands._pycaw_endpoint()
        if endpoint is None:
            return None, False
        try:
            scalar = float(endpoint.GetMasterVolumeLevelScalar())
            return int(round(scalar * 100)), True
        except Exception:
            return None, False

    @staticmethod
    def _pycaw_set_volume(percent):
        """Define o volume absoluto (0-100) via pycaw. True se ok."""
        endpoint = VegaCommands._pycaw_endpoint()
        if endpoint is None:
            return False
        try:
            endpoint.SetMasterVolumeLevelScalar(
                max(0, min(100, percent)) / 100.0, None)
            return True
        except Exception:
            return False

    @staticmethod
    def get_volume():
        """Volume atual do sistema em % (None se não souber ler)."""
        volume, ok = VegaCommands._pycaw_volume()
        return volume if ok else None

    @staticmethod
    def set_volume(percent):

        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."

        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return "O volume precisa ser um número."

        percent = max(0, min(100, percent))

        # Undo: guardar o volume anterior para 'desfaz' reverter.
        from core.undo import push_undo
        previous = VegaCommands.get_volume()
        if previous is not None:
            push_undo(
                f"volume → {percent}%",
                lambda: VegaCommands.set_volume(previous) or f"Volume de volta para {previous}%.",
            )

        # Caminho preferido: pycaw (absoluto, instantâneo, sem depender
        # do volume atual). Fallback: teclas, como antes.
        if VegaCommands._pycaw_set_volume(percent):
            confirmed, _ = VegaCommands._pycaw_volume()
            if confirmed == percent:
                return f"Volume ajustado para {percent}%."  # verificado lendo de volta
            return f"Volume enviado para {percent}%, mas não consegui confirmar o valor atual."

        try:

            for _ in range(50):
                VegaCommands._press_key(0xAE)

            for _ in range(round(percent / 2)):
                VegaCommands._press_key(0xAF)

            return f"Volume ajustado para {percent}%."

        except Exception:
            return "Não consegui ajustar o volume."

    @staticmethod
    def volume_up(percent=10):

        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."

        try:
            percent = int(percent)
        except (TypeError, ValueError):
            percent = 10
        percent = max(1, min(100, percent))

        current, ok = VegaCommands._pycaw_volume()
        if ok:
            return VegaCommands.set_volume(min(100, current + percent))

        for _ in range(5):
            VegaCommands._press_key(0xAF)

        return "Aumentando o volume."

    @staticmethod
    def volume_down(percent=10):

        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."

        try:
            percent = int(percent)
        except (TypeError, ValueError):
            percent = 10
        percent = max(1, min(100, percent))

        current, ok = VegaCommands._pycaw_volume()
        if ok:
            return VegaCommands.set_volume(max(0, current - percent))

        for _ in range(5):
            VegaCommands._press_key(0xAE)

        return "Diminuindo o volume."

    @staticmethod
    def volume_status():
        """Responde 'quanto tá o volume?' com o valor real, quando dá."""
        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."
        current, ok = VegaCommands._pycaw_volume()
        if not ok:
            return "Não consegui ler o volume agora."
        if current <= 0:
            return "O volume está zerado."
        return f"O volume está em {current}%."

    @staticmethod
    def volume_max():
        return VegaCommands.set_volume(100)

    @staticmethod
    def volume_min():
        return VegaCommands.set_volume(0)

    @staticmethod
    def volume_mute():

        if VegaCommands._press_key(0xAD):
            return "Volume mutado."

        return "Não consegui mutar o volume."

    @staticmethod
    def volume_unmute():

        if VegaCommands._press_key(0xAD):
            return "Volume ativado."

        return "Não consegui ativar o volume."

    # ==========================================================
    # MÍDIA
    # ==========================================================

    @staticmethod
    def media_shuffle(state=None):
        """Embaralha a fila da mídia do PC.

        Caminho preferido: bridge do Spotify (Spicetify), que confirma a
        ação. Sem bridge, tecla shuffle de mídia (funciona no YouTube/
        players de mídia que respeitam a tecla). `state` True/False força
        embaralhar/desembaralhar; None só alterna.
        """
        if state is False:
            # "sem aleatório" só faz sentido via bridge (a tecla só alterna).
            if spotify_bridge.shuffle() is not None:
                return "Ordem normal ativada."
            return (
                "Para tirar o aleatório preciso do Spotify com a extensão "
                "PlaybackApi conectada."
            )
        if spotify_bridge.shuffle() is not None:
            return "Aleatório ativado."
        if VegaCommands._press_key(0x53):  # Não é tecla de mídia padrão:
            return "Aleatório ativado."
        return "Não consegui ativar o aleatório."

    @staticmethod
    def media_repeat(state=None):
        """Repete a faixa atual (bridge do Spotify ou tecla de repeat).

        Player interno primeiro: 'repete essa música' com o clipe do YouTube
        tocando deve REINICIAR o clipe (seek 0 + play), não apertar tecla de
        repeat que o SO manda para o app errado (mesmo bug do play/pause).
        """
        if VegaCommands._internal_player_active() and VegaCommands._player_control("replay"):
            return "Repetindo a música."
        if spotify_bridge.repeat() is not None:
            return "Repetir ativado."
        if VegaCommands._press_key(0x52):  # Sem tecla de mídia padrão:
            return "Repetir ativado."
        return "Não consegui ativar o repetir."

    @staticmethod
    def media_play_pause(text=""):
        # ALVO pela fala: "pausa a música" com vídeo E música abertos pausa a
        # MÚSICA (antes o toggle sem alvo ia para o player mais recente —
        # pausava o VÍDEO, BUG REAL do teste de conversa).
        lowered = str(text or "").lower()
        if "música" in lowered or "musica" in lowered:
            players = VegaCommands._open_players()
            if "music" in players:
                if VegaCommands._player_control("toggle", target="music"):
                    return "Pausando ou continuando a música."
        elif "vídeo" in lowered or "video" in lowered:
            players = VegaCommands._open_players()
            if "video" in players:
                if VegaCommands._player_control("toggle", target="video"):
                    return "Pausando ou continuando o vídeo."

        # Player interno primeiro: se a VEGA mandou um vídeo/música para a aba
        # interna, é o que está tocando de verdade — a tecla de mídia iria para
        # o app errado (ou para nada).
        if VegaCommands._internal_player_active() and VegaCommands._player_control("toggle"):
            return "Pausando ou continuando o player."

        if spotify_bridge.toggle_play():
            return "Pausando ou continuando o Spotify."

        if VegaCommands._press_key(0xB3):
            return "Pausando ou continuando a mídia."

        return "Não consegui controlar a mídia."

    @staticmethod
    def media_next(text=""):
        lowered = str(text or "").lower()
        # "Próxima música" com vídeo e música: o alvo é a MÚSICA, mesmo com
        # o vídeo sendo o player mais recente.
        targeted_music = ("música" in lowered or "musica" in lowered)
        targeted_video = ("vídeo" in lowered or "video" in lowered)
        # "próximo vídeo" ALVO NO VÍDEO: vídeo tem UMA faixa — "next" do
        # IFrame não pula; busca e TOCA outro vídeo do mesmo assunto
        # (BUG REAL da bateria: "proximo video" com vídeo aberto era
        # ignorado e caía na tecla de mídia).
        players = VegaCommands._open_players()
        video = players.get("video")
        if targeted_video and video and VegaCommands._internal_player_active():
            current = str(video.get("query") or "").strip()
            if current:
                result = VegaCommands.watch_video(f"{current} video seguinte")
                if result:
                    return f"Próximo vídeo: {result}"
        # TROCA DE MÚSICA de verdade: o player interno de música normalmente
        # tem UMA faixa — o "next" do IFrame não tem para onde pular e a
        # resposta "Próxima no player" era MENTIRA (o usuário testou: disse
        # que ia trocar e não trocou). Com uma faixa só no player de música,
        # busca e TOCA outra faixa relacionada à atual.
        players = VegaCommands._open_players()
        music = players.get("music")
        if music and VegaCommands._internal_player_active() and (targeted_music or "video" not in players):
            current = str(music.get("query") or "").strip()
            if current:
                related = current if current.lower().startswith("radio ") else f"radio {current}"
                result = VegaCommands.play_music_youtube(related)
                if result:
                    return f"Trocando: {result}"

        if VegaCommands._internal_player_active() and VegaCommands._player_control("next"):
            return "Próxima no player."

        if spotify_bridge.next_track():
            return "Próxima música no Spotify."

        if VegaCommands._press_key(0xB0):
            return "Próxima mídia."

        return "Não consegui avançar a mídia."

    @staticmethod
    def media_previous(text=""):
        if VegaCommands._internal_player_active() and VegaCommands._player_control("previous"):
            return "Voltando no player."

        if spotify_bridge.previous_track():
            return "Voltando para a música anterior no Spotify."

        if VegaCommands._press_key(0xB1):
            return "Voltando para a mídia anterior."

        return "Não consegui voltar a mídia."

    @staticmethod
    def media_stop():
        # Player interno primeiro (mesmo raciocínio do play/pause).
        if VegaCommands._internal_player_active() and VegaCommands._player_control("stop"):
            return "Parando o player."

        # Simetria com play/pause/next/prev: bridge do Spotify antes da tecla.
        if spotify_bridge.stop() is not None:
            return "Parando a mídia do Spotify."

        if VegaCommands._press_key(0xB2):
            return "Parando a mídia."

        return "Não consegui parar a mídia."

    @staticmethod
    def now_playing():
        """Diz qual música está tocando (player interno do YouTube primeiro,
        depois Spotify via PlaybackApi)."""
        player = VegaCommands._INTERNAL_PLAYER
        queue = player.get("queue") or []
        pos = player.get("queue_pos", -1)
        if VegaCommands._internal_player_active() and 0 <= pos < len(queue):
            title = str(queue[pos].get("title") or "").strip()
            if title:
                return f"Tocando {title} no player do YouTube."

        state = spotify_bridge.get_now_playing()
        if not state:
            return (
                "Não consegui ver o que está tocando. "
                "O Spotify está aberto?"
            )

        title = str(state.get("title") or "").strip()
        artist = str(state.get("artist") or "").strip()
        album = str(state.get("album") or "").strip()
        playing = str(state.get("state") or "").strip().upper()

        if not title:
            return "Não tem nenhuma música tocando agora."

        parts = []
        if artist:
            parts.append(f"{title} de {artist}")
        else:
            parts.append(title)
        if album:
            parts.append(f"do álbum {album}")

        resposta = "Tocando " + " ".join(parts) + "."
        if playing == "PAUSADO":
            resposta += " Está pausado."
        return resposta

    # ==========================================================
    # JANELAS
    # ==========================================================

    @staticmethod
    def close_window():
        if VegaCommands._hotkey(0x12, 0x73):
            return "Fechando a janela."
        return "Não consegui fechar a janela."

    @staticmethod
    def minimize_window():
        if VegaCommands._hotkey(0x5B, 0x28):
            return "Minimizando a janela."
        return "Não consegui minimizar a janela."

    @staticmethod
    def maximize_window():
        if VegaCommands._hotkey(0x5B, 0x26):
            return "Maximizando a janela."
        return "Não consegui maximizar a janela."

    @staticmethod
    def restore_window():

        if VegaCommands._hotkey(0x5B, 0x28):

            time.sleep(0.15)

            if VegaCommands._hotkey(0x5B, 0x26):
                return "Restaurando a janela."

        return "Não consegui restaurar a janela."

    @staticmethod
    def switch_window():
        if VegaCommands._hotkey(0x12, 0x09):
            return "Alternando entre as janelas."
        return "Não consegui alternar as janelas."

    @staticmethod
    def minimize_all():
        if VegaCommands._hotkey(0x5B, 0x44):
            return "Minimizando todas as janelas."
        return "Não consegui minimizar as janelas."

    @staticmethod
    def show_desktop():
        if VegaCommands._hotkey(0x5B, 0x44):
            return "Mostrando a área de trabalho."
        return "Não consegui mostrar a área de trabalho."

    @staticmethod
    def fullscreen():
        # Player interno primeiro: "tela cheia" com vídeo da VEGA tocando é
        # fullscreen do PLAYER — F11 iria para o app em foco (app errado).
        player_result = VegaCommands.player_fullscreen()
        if player_result is not None:
            return player_result
        if VegaCommands._press_key(0x7A):
            return "Ativando tela cheia."
        return "Não consegui ativar a tela cheia."

    # ==========================================================
    # BRILHO
    # ==========================================================

    @staticmethod
    def _set_brightness_ps(percent):
        """Script PowerShell que ALTERA o brilho de forma compatível.

        Antes usava-se `.WmiSetBrightness(1, N)` como método direto, que falha
        em várias versões do PowerShell/WMI ("method not found") — era a causa
        de "Não consegui alterar o brilho" mesmo em notebook com o controle
        disponível. `Invoke-CimMethod` é a forma oficial e funciona em todas.
        """
        return (
            "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
            f"| Invoke-CimMethod -MethodName WmiSetBrightness "
            f"-Arguments @{{Brightness={max(0, min(100, int(percent)))};Timeout=1}}"
        )

    @staticmethod
    def set_brightness(percent):

        if not VegaCommands._is_windows():
            return "O controle de brilho está configurado para Windows."

        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return "O brilho precisa ser um número."

        percent = max(0, min(100, percent))

        # Undo: guardar o brilho anterior para 'desfaz' reverter.
        from core.undo import push_undo
        previous = VegaCommands._current_brightness()
        result = VegaCommands._run_powershell(
            VegaCommands._set_brightness_ps(percent)
        )

        if result and result.returncode == 0:
            if previous is not None:
                push_undo(
                    f"brilho → {percent}%",
                    lambda: VegaCommands.set_brightness(previous) or f"Brilho de volta para {previous}%.",
                )
            return f"Brilho ajustado para {percent}%."

        return "Não consegui alterar o brilho."

    @staticmethod
    def _current_brightness():
        """Lê o brilho atual; devolve None quando o monitor não suporta."""
        result = VegaCommands._run_powershell(
            "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"
        )
        if not result or result.returncode != 0:
            return None
        match = re.search(r"\d+", result.stdout or "")
        return int(match.group(0)) if match else None

    @staticmethod
    def brightness_up(step=10):

        current = VegaCommands._current_brightness()
        # Sem leitura (monitor externo sem suporte), tenta um valor seguro
        # em vez de desistir — antes o comando falhava direto.
        target = min(100, current + step) if current is not None else 80

        result = VegaCommands._run_powershell(
            VegaCommands._set_brightness_ps(target)
        )

        if result and result.returncode == 0:
            # Undo: captura do estado anterior só depois de saber que houve
            # mudança — e só quando sabíamos o valor original.
            if current is not None:
                from core.undo import push_undo
                push_undo(
                    f"brilho → {target}%",
                    lambda: VegaCommands.set_brightness(current) or f"Brilho de volta para {current}%.",
                )
            return f"Brilho subindo para {target}%."

        return "Não consegui alterar o brilho."

    @staticmethod
    def brightness_down(step=10):

        current = VegaCommands._current_brightness()
        target = max(0, current - step) if current is not None else 30

        result = VegaCommands._run_powershell(
            VegaCommands._set_brightness_ps(target)
        )

        if result and result.returncode == 0:
            if current is not None:
                from core.undo import push_undo
                push_undo(
                    f"brilho → {target}%",
                    lambda: VegaCommands.set_brightness(current) or f"Brilho de volta para {current}%.",
                )
            return f"Brilho baixando para {target}%."

        return "Não consegui alterar o brilho."

    @staticmethod
    def brightness_max():
        return VegaCommands.set_brightness(100)

    @staticmethod
    def brightness_min():
        return VegaCommands.set_brightness(0)

    # ==========================================================
    # BLUETOOTH
    # ==========================================================

    @staticmethod
    def set_bluetooth(enabled):

        if not VegaCommands._is_windows():
            return "O controle de Bluetooth está configurado para Windows."

        state = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"

        command = f"""
        Get-PnpDevice -Class Bluetooth |
        Where-Object {{ $_.Status -ne 'Unknown' }} |
        ForEach-Object {{
            {state} -InstanceId $_.InstanceId -Confirm:$false
        }}
        """

        result = VegaCommands._run_powershell(
            command,
            timeout=10
        )

        if result and result.returncode == 0:
            return "Bluetooth ativado." if enabled else "Bluetooth desativado."

        return "Não consegui alterar o Bluetooth."

    # ==========================================================
    # NOTIFICAÇÕES
    # ==========================================================

    @staticmethod
    def set_notifications(enabled):

        if not VegaCommands._is_windows():
            return "O controle de notificações está configurado para Windows."

        value = 1 if enabled else 0

        command = f"""
        Set-ItemProperty `
            -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications" `
            -Name "ToastEnabled" `
            -Value {value} `
            -Type DWord
        """

        result = VegaCommands._run_powershell(command)

        if result and result.returncode == 0:
            return (
                "Notificações ativadas."
                if enabled
                else
                "Notificações silenciadas."
            )

        return "Não consegui alterar as notificações."

    # ==========================================================
    # TELA / SEGURANÇA
    # ==========================================================

    @staticmethod
    def turn_off_screen():

        if not VegaCommands._is_windows():
            return "O controle do monitor está configurado para Windows."

        try:
            ctypes.windll.user32.SendMessageW(
                0xFFFF,
                0x0112,
                0xF170,
                2
            )

            return "Desligando a tela."

        except Exception:
            return "Não consegui desligar a tela."

    @staticmethod
    def lock_pc():

        if not VegaCommands._is_windows():
            return "O bloqueio está configurado para Windows."

        try:
            ctypes.windll.user32.LockWorkStation()
            return "Bloqueando o computador."
        except Exception:
            return "Não consegui bloquear o computador."

    # ==========================================================
    # TECLADO
    # ==========================================================

    @staticmethod
    def copy():
        return "Copiado." if VegaCommands._hotkey(0x11, 0x43) else "Não consegui copiar."

    @staticmethod
    def paste():
        return "Colado." if VegaCommands._hotkey(0x11, 0x56) else "Não consegui colar."

    @staticmethod
    def cut():
        return "Recortado." if VegaCommands._hotkey(0x11, 0x58) else "Não consegui recortar."

    @staticmethod
    def select_all():
        return "Selecionando tudo." if VegaCommands._hotkey(0x11, 0x41) else "Não consegui selecionar tudo."

    @staticmethod
    def undo():
        # Primeiro o desfazer da própria VEGA (ações que ela mudou: volume,
        # arquivos que escreveu...). Sem pilha, cai para o Ctrl+Z do app ativo,
        # comportamento antigo preservado.
        from core import undo as vega_undo
        if vega_undo.can_undo():
            result = vega_undo.undo_last()
            if result.get("status") == "done":
                detail = str(result.get("detail") or "").strip()
                return f"Desfeito: {result.get('label', 'ação')}." + (f" {detail}" if detail else "")
            reason = str(result.get("reason") or "")
            return reason or "Não consegui desfazer."
        return "Desfazendo." if VegaCommands._hotkey(0x11, 0x5A) else "Não consegui desfazer."

    @staticmethod
    def redo():
        return "Refazendo." if VegaCommands._hotkey(0x11, 0x59) else "Não consegui refazer."

    @staticmethod
    def save():
        return "Salvando." if VegaCommands._hotkey(0x11, 0x53) else "Não consegui salvar."

    @staticmethod
    def new():
        return "Criando novo." if VegaCommands._hotkey(0x11, 0x4E) else "Não consegui criar."

    @staticmethod
    def alt_tab():
        return "Trocando de janela." if VegaCommands._hotkey(0x12, 0x09) else "Não consegui trocar de janela."

    # ==========================================================
    # SISTEMA
    # ==========================================================

    @staticmethod
    def restart_pc():

        if not VegaCommands._is_windows():
            return "O reinício está configurado para Windows."

        from core.actions.repeat_gate import check_and_register
        if not check_and_register("restart"):
            return "O computador já está agendado para reiniciar."

        try:
            subprocess.Popen(["shutdown", "/r", "/t", "5"])

            return "O computador será reiniciado em 5 segundos."

        except Exception:
            return "Não consegui reiniciar o computador."

    @staticmethod
    def shutdown_pc():

        if not VegaCommands._is_windows():
            return "O desligamento está configurado para Windows."

        from core.actions.repeat_gate import check_and_register
        if not check_and_register("shutdown"):
            return "O computador já está agendado para desligar."

        try:
            subprocess.Popen(["shutdown", "/s", "/t", "5"])

            return "O computador será desligado em 5 segundos."

        except Exception:
            return "Não consegui desligar o computador."

    @staticmethod
    def cancel_shutdown():

        if not VegaCommands._is_windows():
            return "O cancelamento está configurado para Windows."

        try:
            subprocess.Popen(["shutdown", "/a"])
            return "Cancelando o desligamento."

        except Exception:
            return "Não consegui cancelar o desligamento."

    # ==========================================================
    # PESQUISA
    # ==========================================================

    @staticmethod
    def search_google(query):

        if not query or not query.strip():
            return "O que você quer pesquisar no Google?"

        query = query.strip()

        webbrowser.open(
            "https://www.google.com/search?q="
            + quote_plus(query)
        )

        return f"Pesquisando por {query} no Google."

    @staticmethod
    def search_youtube(query):

        if not query or not query.strip():
            return "O que você quer pesquisar no YouTube?"

        query = query.strip()

        webbrowser.open(
            "https://www.youtube.com/results?search_query="
            + quote_plus(query)
        )

        return f"Pesquisando por {query} no YouTube."

    # CACHE de resolução query -> videoId (TTL longo, sem invalidação:
    # videoIds do YouTube são estáveis). "toca X de novo" e piadas repetidas
    # ficam INSTANTÂNEOS em vez de baixar a página de resultados de novo.
    _YT_ID_CACHE = {}
    _YT_ID_CACHE_LOCK = threading.Lock()

    @staticmethod
    def _first_youtube_video_id(query, videos_only=True):
        """Resolve o videoId do 1º resultado da busca do YouTube.

        Busca a página de resultados (HTML mesmo) e extrai o videoId do
        primeiro ytInitialData do primeiro resultado de VÍDEO (o filtro
        sp=EgIQAQ%3D%3D exclui shorts/playlists/canais, que vinham antes
        e davam "tocando X" com um canal aberto parado). Devolve None em
        qualquer falha (sem rede, layout novo, etc.).

        CACHE: consultas já resolvidas (mesma query + filtro) saem na hora.
        Timeout reduzido de 6s para 4s: se a rede demora, o fallback
        (busca no Chrome) acontece antes — melhor tocar via busca do que
        o usuário ficar 6s no silêncio.
        """
        import re as _re
        from urllib.request import Request, urlopen

        query = str(query or "").strip()
        if not query:
            return None

        cache_key = (query.lower(), bool(videos_only))
        with VegaCommands._YT_ID_CACHE_LOCK:
            cached = VegaCommands._YT_ID_CACHE.get(cache_key)
        if cached:
            return cached

        try:
            url = (
                "https://www.youtube.com/results?search_query="
                + quote_plus(query)
                + ("&sp=EgIQAQ%253D%253D" if videos_only else "")
            )
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=4) as response:
                html = response.read().decode("utf-8", "ignore")
            match = _re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
            video_id = match.group(1) if match else None
            if video_id:
                with VegaCommands._YT_ID_CACHE_LOCK:
                    VegaCommands._YT_ID_CACHE[cache_key] = video_id
            return video_id
        except Exception:
            return None

    @staticmethod
    def _open_youtube_autoplay(query, videos_only=True):
        """Abre o 1º resultado do YouTube JÁ TOCANDO.

        Página de resultados nunca dá autoplay — só um watch URL toca sozinho.
        Então resolvemos o videoId do 1º vídeo e abrimos youtube.com/watch
        direto. Se a resolução falhar (sem rede/timeout), cai na busca comum.
        """
        video_id = VegaCommands._first_youtube_video_id(query, videos_only)
        if video_id:
            try:
                # Truque anti-anúncio: ponto entre o domínio e a barra
                # (youtube.com./watch) faz o player carregar sem anúncios.
                webbrowser.open(
                    "https://www.youtube.com./watch?v=" + video_id
                )
                return True
            except Exception:
                pass
        # Fallback: busca simples (sem autoplay, mas nunca falha de abrir).
        try:
            webbrowser.open(
                "https://www.youtube.com/results?search_query="
                + quote_plus(query)
            )
        except Exception:
            pass
        return False

    @staticmethod
    def watch_video(query):
        """Abre o 1º vídeo do YouTube direto TOCANDO.

        "vamos assistir felipe neto": resolvemos o primeiro resultado de
        vídeo da busca e abrimos o watch URL — que toca automaticamente,
        sem clicar em nada. Página de resultados não tem autoplay.
        """
        if not query or not query.strip():
            return "O que você quer assistir?"

        query = query.strip()
        video_id = VegaCommands._first_youtube_video_id(query, videos_only=True)
        if video_id:
            # Aba interna da VEGA (mesma decisão do play_music_youtube):
            # vídeo abre no player embutido da interface, NÃO no Chrome.
            callback = VegaCommands._get_result_callback()
            if callback:
                try:
                    callback("watch_video", {
                        "items": [{"videoId": video_id, "title": query}],
                        "autoplay": True,
                        "query": query,
                        "source": "YouTube",
                    })
                    VegaCommands._mark_internal_player_active()
                    # Memória do que abriu: "tira o vídeo do felipe" depois
                    # precisa saber QUE vídeo está na tela.
                    VegaCommands._remember_open_media("video", query, video_id)
                    VegaCommands._stop_background_music()
                    return f"Vamos assistir {query}."
                except Exception:
                    pass
        # Sem interface conectada (ou videoId não resolvido): fallback
        # Chrome, que pelo menos toca — nunca fica parado na busca.
        VegaCommands._open_youtube_autoplay(query, videos_only=True)
        return f"Vamos assistir {query}."

    # ==========================================================
    # MÚSICA — TOCAR DE VERDADE (não só pesquisar)
    # ==========================================================

    # Callback do player interno: injetado NA INSTÂNCIA pelo brain
    # (self.commands._result_callback = self._emit_tool_result). Métodos
    # estáticos não recebem self, então usam este helper para ler da
    # instância pendente — antes lia da CLASSE e nunca achava o callback,
    # fazendo TODA música/vídeo abrir no Chrome.
    _LAST_INSTANCE = None

    # Estado do PLAYER INTERNO: setado quando a VEGA manda um vídeo/música
    # para a aba interna. Controles de mídia ("pausa a música") vão para o
    # player interno PRIMEIRO, depois Spotify, depois tecla de mídia — antes
    # só a tecla era apertada e o SO mandava para o app errado (BUG REAL:
    # pausar a música do player interno não fazia nada).
    #
    # VÍDEO e MÚSICA são players SEPARADOS (2026-10-25): tocar uma música
    # NÃO fecha/substitui o vídeo que estava tocando — são coisas diferentes
    # e convivem na interface. Cada tipo guarda o SEU estado (o que está
    # tocando) para o fecho contextual ("tira o video do felipe" fecha só o
    # vídeo; "tira isso" fecha o mais recente).
    _INTERNAL_PLAYER = {
        "active_until": 0.0, "queue": [], "queue_pos": -1,
        # Estado por TIPO de player: {"video": {...}|None, "music": {...}|None}
        # Cada item: {"kind", "query", "video_id"}. "tira o vídeo" fecha o
        # vídeo; "tira" sem alvo fecha o mais RECENTE dos dois.
        "open": {},
        "last_open_kind": "",
    }

    @staticmethod
    def _open_players():
        """Players de mídia abertos ({kind: {query, video_id}}), só os vivos."""
        state = VegaCommands._INTERNAL_PLAYER
        return {
            kind: info for kind, info in (state.get("open") or {}).items()
            if isinstance(info, dict) and time.time() < state["active_until"]
        } if time.time() < state["active_until"] else {}

    @staticmethod
    def _remember_open_media(kind, query, video_id=""):
        """Registra que a VEGA abriu um player deste tipo com este conteúdo.

        É a memória que falta: "tira o vídeo do felipe" depois de "vamos
        assistir felipe neto" precisa saber QUE vídeo está na tela.
        """
        state = VegaCommands._INTERNAL_PLAYER
        open_players = state.setdefault("open", {})
        open_players[kind] = {
            "kind": kind, "query": str(query or "").strip(),
            "video_id": str(video_id or ""),
            "at": time.time(),
        }
        state["last_open_kind"] = kind

    @staticmethod
    def _forget_open_media(kind):
        open_players = VegaCommands._INTERNAL_PLAYER.setdefault("open", {})
        open_players.pop(kind, None)

    # Hook registrado pela interface (vega.py): para a música de fundo da
    # inicialização sempre que algo novo começa a tocar.
    _stop_background_hook = None

    # Hook registrado pela interface (vega.py): fecha UMA ABA do workspace
    # pelo texto da fala ("tira o clima", "fecha a cotação"). Usado pelo
    # player_close quando não há player interno — o alvo da fala é uma aba,
    # não um player.
    close_card_hook = None

    @staticmethod
    def _stop_background_music():
        hook = VegaCommands._stop_background_hook
        if callable(hook):
            try:
                hook()
            except Exception:
                pass

    @staticmethod
    def _mark_internal_player_active(seconds=3600):
        VegaCommands._INTERNAL_PLAYER["active_until"] = time.time() + seconds

    @staticmethod
    def _internal_player_active():
        return time.time() < VegaCommands._INTERNAL_PLAYER["active_until"]

    @staticmethod
    def queue_music_youtube(query):
        """Adiciona uma música à fila do player interno do YouTube.

        "Coloca essa música na fila" sem citar Spotify: enfileira no player
        da interface (a fila toca sozinha quando a faixa atual termina, via
        onStateEnded do IFrame). Com Spotify citado, o caminho é o spotify_queue.
        Devolve dict com a posição na fila para a VEGA narrar.
        """
        if not query or not query.strip():
            return "Qual música você quer colocar na fila?"
        query = query.strip()
        video_id = VegaCommands._first_youtube_video_id(query, videos_only=True)
        if not video_id:
            return f"Não encontrei '{query}' no YouTube para enfileirar."
        player = VegaCommands._INTERNAL_PLAYER
        player["queue"].append({"videoId": video_id, "title": query})
        pos = len(player["queue"])
        # Se nada está tocando, a fila vira o player: toca a primeira agora
        # (mas como um PLAY de verdade, com o estado da fila apontando pra ela).
        if player["queue_pos"] < 0:
            player["queue_pos"] = pos - 1
            resultado = VegaCommands.play_music_youtube(query)
            player["queue_pos"] = pos - 1  # play_music_youtube sobrescreveu
            return f"Nada estava tocando: comecei por '{query}'. {resultado}"
        callback = VegaCommands._get_result_callback()
        if callback:
            try:
                callback("player_queue", {
                    "queue": player["queue"][-5:],
                    "position": pos,
                    "title": query,
                })
            except Exception:
                pass
        return f"'{query}' entrou na fila (posição {pos})."

    @staticmethod
    def _player_control(action, target=""):
        """Manda um comando para o player interno (IFrame) da interface.

        Usa o MESMO canal de callback do play_music_youtube: a interface
        reconhece tool_name == 'player_control' e executa no QWebEngineView
        (pauseVideo/playVideo/nextVideo/stopVideo) sem abrir nada.
        ``target`` ("video"/"music") diz qual dos players separados agir —
        vídeo e música coexistem na interface.
        """
        callback = VegaCommands._get_result_callback()
        if not callback:
            return False
        try:
            payload = {"action": action}
            if target:
                payload["target"] = target
            callback("player_control", payload)
            return True
        except Exception:
            return False

    # ==========================================================
    # PLAYER INTERNO — CONTROLE TOTAL (Endriw: "a VEGA colocou o
    # player na interface dela, então ela tira ele, pausa, põe
    # em tela cheia, troca")
    # ==========================================================

    @staticmethod
    def player_fullscreen():
        """"Tela cheia" com player interno ativo: fullscreen NO PLAYER, não
        no app em foco. Sem isso "tela cheia" apertava F11 no app que
        estivesse em primeiro plano."""
        if VegaCommands._internal_player_active() and VegaCommands._player_control("fullscreen"):
            return "Player em tela cheia."
        return None

    @staticmethod
    def player_exit_fullscreen():
        if VegaCommands._internal_player_active() and VegaCommands._player_control("exit_fullscreen"):
            return "Saindo da tela cheia."
        return None

    @staticmethod
    def player_close(target=""):
        """Remove UM player da interface, pelo alvo da fala.

        Vídeo e música são players SEPARADOS: "tira o video do felipe" fecha
        só o player de vídeo; "tira a musica" fecha só o de música; "tira
        isso" (sem alvo) fecha o mais RECENTE dos dois. Antes havia um
        player único — tocar música matava o vídeo do Felipe que o usuário
        tinha pedido para abrir.
        """
        # "tira/fecha a dúvida" NÃO é comando de player: é pedido de IA.
        # Sem o guarda, a música parava com "tira essa dúvida" (BUG REAL).
        if re.search(r"\bd[uú]vidas?\b|\bpergunta\b", target):
            return None
        if not VegaCommands._internal_player_active():
            # SEM PLAYER INTERNO: a fala pode ser sobre uma ABA da interface
            # ("tira o clima", "fecha a cotação", "tira isso" com um card
            # aberto). Devolver "Não há player aberto" era responder errado —
            # repassa para o callback de comando de interface, que fecha a aba
            # certa (BUG REAL do Endriw, 30/10/2026).
            hook = getattr(VegaCommands, "close_card_hook", None)
            if callable(hook):
                try:
                    resposta = hook(str(target or ""))
                    if resposta:
                        return resposta
                except Exception:
                    pass
            return "Não há player aberto."
        # "tira tudo" / "fecha tudo": TODOS os players de uma vez.
        if re.search(r"\btudo\b|\btodos\b", str(target or "")):
            closed = []
            for kind in list(VegaCommands._open_players()):
                if VegaCommands._player_control("close", target=kind):
                    closed.append(kind)
                    VegaCommands._forget_open_media(kind)
            VegaCommands._INTERNAL_PLAYER["active_until"] = 0.0
            VegaCommands._INTERNAL_PLAYER["last_open_kind"] = ""
            if not closed:
                return "Não há player aberto."
            labels = ["o vídeo" if k == "video" else "a música" for k in closed]
            return f"Fechando {' e '.join(labels)}."
        target = str(target or "").strip().lower()
        open_players = VegaCommands._open_players()
        # Alvo específico ("tira o vídeo", "tira a música", "tira o clipe"...).
        kind = ""
        if any(word in target for word in ("video", "vídeo", "clipe", "filme")):
            kind = "video"
        elif any(word in target for word in ("musica", "música", "som", "audio", "áudio", "faixa", "playlist")):
            kind = "music"
        # Alvo pelo CONTEÚDO citado: "tira o felipe neto" — a VEGA lembra o
        # que abriu, então compara o nome citado com o conteúdo de cada
        # player aberto e fecha o que bater (vídeo e música separados).
        # COM UM SÓ player vivo, "tira isso" fecha ELE: o referente é único
        # — perguntar "quer fechar música?" era atrito (o usuário já tinha
        # dito o que queria).
        if not kind and len(open_players) == 1:
            kind = next(iter(open_players))
        if not kind:
            folded = re.sub(r"[^a-z0-9\s]", " ", target)
            # Stopwords extras: nomes citados por conteúdo não podem conter
            # palavras de outra intenção ("tira essa roupa da lista" não é
            # player — a música fechava por acaso do "essa").
            STOP = {"tira", "tirar", "fecha", "fechar", "feche", "fechi",
                    "encerra", "encerrar", "encerre", "o", "a", "os", "as",
                    "esse", "essa", "isso", "isto", "do", "da", "de", "das",
                    "dos", "player", "remove", "remover", "sai", "sair",
                    "nao", "não", "aí", "ai", "já", "ja", "pronto",
                    "cansei", "chega", "para", "favor", "por", "lista",
                    "roupa", "duvida", "dúvida", "compras", "tela", "cheia"}
            for candidate_kind, info in open_players.items():
                query_words = set(re.findall(
                    r"[a-z0-9]+",
                    re.sub(r"[^a-z0-9\s]", " ", str(info.get("query", "")).lower()),
                ))
                cited = set(folded.split()) - STOP
                if not cited:
                    continue
                # Nome de 1 palavra casa quando é exatamente o título inteiro
                # do player ("tira o akon" com akon aberto). Nomes de 2+
                # palavras casam por interseção ("tira o felipe neto").
                if len(cited) >= 2 and query_words and cited & query_words:
                    kind = candidate_kind
                    break
                if len(cited) == 1 and query_words == cited:
                    kind = candidate_kind
                    break
        # Sem alvo claro: fecha o mais recente (é o que "isso" aponta).
        if not kind:
            kind = VegaCommands._INTERNAL_PLAYER.get("last_open_kind") or ""
            if not kind:
                open_players = open_players or VegaCommands._open_players()
                if len(open_players) == 1:
                    kind = next(iter(open_players))
                elif open_players:
                    kind = max(
                        open_players.items(), key=lambda kv: kv[1].get("at", 0.0)
                    )[0]
                else:
                    kind = "video"  # legado: player único era o de vídeo
        # ALVO INEXISTENTE: "tira o video" com o vídeo já fechado não pode
        # responder "Fechando o vídeo" (mentira — nada foi fechado; o usuário
        # testou exatamente isto). Checa o registro ANTES de agir.
        open_now = VegaCommands._open_players()
        if kind not in open_now:
            label = "vídeo" if kind == "video" else "música"
            other = next(iter(open_now), None)
            if other:
                other_label = "vídeo" if other == "video" else "música"
                return f"Não há {label} aberto — só {other_label}. Quer fechar {other_label.replace('o ', 'o ')}?"
            return "Não há player aberto."
        if not VegaCommands._player_control("close", target=kind):
            return "Não consegui fechar o player."
        VegaCommands._forget_open_media(kind)
        # Outros players continuam vivos: só zera o estado quando não sobrou
        # nenhum (o vídeo e a música são independentes). O last_open_kind
        # também segue o que SOBROU — senão um "tira isso" seguinte aponta
        # para o player que acabou de morrer.
        remaining = VegaCommands._open_players()
        VegaCommands._INTERNAL_PLAYER["last_open_kind"] = (
            next(iter(remaining), "")
        )
        VegaCommands._INTERNAL_PLAYER["active_until"] = (
            time.time() + 1.0 if remaining else 0.0
        )
        label = "o vídeo" if kind == "video" else "a música"
        return f"Fechando {label}."

    @staticmethod
    def player_return_to_media(target_text=""):
        """"Volta para o vídeo" — reproduz de novo o que a VEGA abriu antes.

        O usuário falou 'volta para o video' com a música tocando: quer
        REVER o vídeo do Felipe (ou retomar a música), não pular faixa.
        O alvo é deduzido da fala ("vídeo" -> player de vídeo) e o
        conteúdo vem do registro _open_players — sem nova busca.
        """
        players = VegaCommands._open_players()
        if not players:
            return "Não há vídeo ou música aberta para voltar."
        target_text = str(target_text or "").lower()
        # Alvo explícito na fala vence; senão, o que NÃO está tocando agora
        # ("volta para o vídeo" com música tocando = mostrar o vídeo).
        kind = ""
        if any(word in target_text for word in ("vídeo", "video", "clipe")):
            kind = "video"
        elif any(word in target_text for word in ("música", "musica", "player de musica")):
            kind = "music"
        if not kind:
            last = VegaCommands._INTERNAL_PLAYER.get("last_open_kind") or ""
            # Se o último aberto É o que está tocando, o "volta" aponta para
            # o OUTRO player (o que a VEGA abriu antes).
            others = [k for k in players if k != last]
            kind = others[0] if others else last
        info = players.get(kind)
        if not info:
            label = "vídeo" if kind == "video" else "música"
            return f"Não há {label} aberto para voltar."
        query = str(info.get("query") or "").strip()
        if not query:
            return "Não sei qual conteúdo era."
        callback = VegaCommands._get_result_callback()
        if not callback:
            return "Não consegui mostrar de novo agora."
        try:
            tool = "watch_video" if kind == "video" else "play_music_youtube"
            callback(tool, {
                "items": [{"videoId": info.get("video_id"), "title": query}],
                "autoplay": True,
                "query": query,
                "source": "YouTube",
            })
            VegaCommands._mark_internal_player_active()
            VegaCommands._remember_open_media(kind, query, info.get("video_id"))
            label = "Vídeo" if kind == "video" else "Música"
            return f"{label} de volta: {query}."
        except Exception:
            return "Não consegui mostrar de novo agora."

    @staticmethod
    def player_volume_up(percent=10):
        if VegaCommands._internal_player_active() and VegaCommands._player_control("volume_up"):
            return "Aumentando o volume do player."
        return None

    @staticmethod
    def player_volume_down(percent=10):
        if VegaCommands._internal_player_active() and VegaCommands._player_control("volume_down"):
            return "Diminuindo o volume do player."
        return None

    def _register_instance(self):
        VegaCommands._LAST_INSTANCE = self
        return self

    @staticmethod
    def _get_result_callback():
        # 1) callback injetado na instância pendente (o brain faz
        #    self.commands._result_callback = self._emit_tool_result);
        # 2) callback injetado na CLASSE (compatibilidade com testes e com
        #    quem seta VegaCommands._result_callback direto).
        callback = getattr(VegaCommands._LAST_INSTANCE, "_result_callback", None)
        if callable(callback):
            return callback
        callback = getattr(VegaCommands, "_result_callback", None)
        if callable(callback):
            return callback
        return None

    @staticmethod
    def play_music_youtube(query, playlist=False):
        """Toca música/playlist no player EMBUTIDO da interface da VEGA.

        Decisão do Endriw: o clipe da música pedida deve abrir na aba interna
        da interface (a mesma que mostra as notícias), NÃO no Chrome. O
        videoId do 1º resultado de vídeo é resolvido aqui e devolvido como
        dict {'items': [...], 'autoplay': True, 'query': ...} — o mesmo
        formato que o painel da interface já entende e reproduz com autoplay
        via YouTube IFrame API (result_panel._play_video).

        Fallback: se a resolução do videoId falhar (sem rede/timeout), abre
        o watch URL no Chrome como antes — tocar é melhor que não tocar.
        """

        if not query or not query.strip():
            return "Qual música você quer ouvir?"

        query = query.strip()
        video_id = VegaCommands._first_youtube_video_id(query, videos_only=True)
        label = "a playlist" if playlist else "a música"
        # Query que já contém a palavra ("playlist foco") não ganha o label
        # repetido — "Tocando a playlist playlist foco" (bug de narração).
        if query.lower().startswith("playlist"):
            label = ""
        def _narrate(suffix=""):
            head = f"{label} " if label else ""
            return f"Tocando {head}{query}{suffix}."
        if video_id:
            # Fila do player interno: esta faixa vira a atual e a fila segue.
            player = VegaCommands._INTERNAL_PLAYER
            player["queue_pos"] = len(player["queue"])
            player["queue"].append({"videoId": video_id, "title": query})
            # Aba interna da VEGA: envia o clipe pelo MESMO canal do screenshot
            # (_result_callback -> interface -> result_panel._play_video), que
            # reproduz com autoplay via IFrame. A resposta volta como fala curta.
            callback = VegaCommands._get_result_callback()
            if callback:
                try:
                    callback("play_music_youtube", {
                        "items": [{"videoId": video_id, "title": query}],
                        "autoplay": True,
                        "query": query,
                        "source": "YouTube",
                    })
                    VegaCommands._mark_internal_player_active()
                    # Música é OUTRO player, separado do vídeo: registrar aqui
                    # NÃO apaga o vídeo que estava tocando. "Tira a música"
                    # fecha só ela; o clipe do vídeo continua na tela.
                    VegaCommands._remember_open_media("music", query, video_id)
                    VegaCommands._stop_background_music()
                    return _narrate()
                except Exception:
                    pass
            # Sem interface conectada (ou callback falhou): fallback Chrome.
            VegaCommands._open_youtube_autoplay(query, videos_only=True)
            return _narrate(" no YouTube")
        # Sem videoId (rede instável etc.): fallback Chrome, que pelo menos
        # toca (watch URL com autoplay) — nunca fica parado na busca.
        VegaCommands._open_youtube_autoplay(query, videos_only=True)
        return _narrate(" no YouTube")


    @staticmethod
    def play_music_spotify(query, playlist=False):
        """Toca música/playlist específica no app do Spotify.

        Usa a busca profunda do protocolo spotify:: abre o app já na tela
        de resultados e dispara a reprodução do primeiro item
        (funciona com o Spotify desktop instalado; sem API paga).
        """

        if not query or not query.strip():
            return "Qual música você quer ouvir no Spotify?"

        query = query.strip()

        if not VegaCommands._is_windows():
            # Sem Windows: fallback para a web app do Spotify.
            try:
                webbrowser.open(
                    "https://open.spotify.com/search/" + quote_plus(query)
                )
                return f"Abrindo {query} no Spotify Web."
            except Exception as error:
                return f"Não consegui abrir o Spotify: {error}"

        # Com a extensão PlaybackApi conectada, o comando SearchPlay faz a
        # busca no próprio app (CosmosAsync) e TOCA a faixa exata — não a
        # busca aberta nem a faixa anterior.
        try:
            from core.actions import spotify_bridge
            if spotify_bridge.bridge_responsive():
                state = spotify_bridge._emit_search_play(query)
                if state:
                    label = "a playlist" if playlist else "a música"
                    return f"Tocando {label} {query} no Spotify."
        except Exception:
            pass

        # DECISÃO do usuário: o Spotify BLOQUEIA deep-links de play e a API
        # anônima ficou instável. O único caminho confiável é abrir a BUSCA
        # no app e clicar no botão "Tocar <música>" do PRIMEIRO resultado
        # direto na interface — via UIAutomation (lembra a posição do botão
        # e clica com o mouse). A resolução de URI via API vira só o
        # fallback, para quando a UIA não estiver disponível.
        try:
            os.startfile("spotify:search:" + quote_plus(query))
        except Exception:
            shortcut = VegaCommands._find_start_menu_app("spotify")
            if shortcut and VegaCommands._open_shortcut(shortcut):
                pass
            else:
                return "Não encontrei o aplicativo Spotify instalado."

        if VegaCommands._spotify_search_keyboard_play(query):
            label = "a playlist" if playlist else "a música"
            return f"Tocando {label} {query} no Spotify."

        # Fallback: resolve o URI da primeira faixa via API anônima e abre
        # spotify:track:<uri> (o app toca na hora quando o deep-link
        # funciona; playlists recebem a tecla de mídia para dar play).
        uri = VegaCommands._spotify_search_uri(query, "track" if not playlist else "playlist")
        if uri:
            try:
                os.startfile(uri)
            except Exception:
                pass
            if playlist:
                time.sleep(2.5)
                VegaCommands._press_key(0xB3)
            label = "a playlist" if playlist else "a música"
            return f"Tocando {label} {query} no Spotify."

        label = "a playlist" if playlist else "a música"
        return f"Tocando {label} {query} no Spotify."

    _SPOTIFY_TOKEN = {"value": None, "at": 0.0}

    @staticmethod
    def _spotify_anon_token():
        """Token de acesso da API do Spotify, com cache de 45min.

        O endpoint antigo (accessToken no HTML / /get_access_token) foi
        bloqueado pelo Spotify; agora é o /api/token com desafio TOTP
        (veja core.actions.spotify_web_token). Com cookie sp_dc salvo
        (data/spotify_session.json) devolve token de CONTA; sem ele, o
        token sai anônimo, que já serve para buscar músicas/playlists.
        """
        now = time.time()
        cached = VegaCommands._SPOTIFY_TOKEN
        if cached["value"] and (now - cached["at"]) < 2700:
            return cached["value"]

        try:
            from core.actions.spotify_web_token import get_web_player_token
            token = get_web_player_token()
        except Exception:
            token = None
        if not token:
            return None

        cached["value"] = token
        cached["at"] = now
        return cached["value"]

    @staticmethod
    def _spotify_search_uri(query, kind="track", retries=3):
        """Devolve o URI (spotify:track:... / spotify:playlist:...) do
        PRIMEIRO resultado da busca — é exatamente o item que o usuário
        teria que clicar na tela de busca, resolvido automaticamente.

        A API anônima responde 429 (rate limit) com o header Retry-After:
        nesse caso retenta aguardando o tempo que o Spotify pediu (máx.
        ``retries`` tentativas). Sem internet/token, devolve None
        (o chamador usa o fallback).
        """
        from urllib.request import Request, urlopen
        from urllib.parse import urlencode
        from urllib.error import HTTPError

        token = VegaCommands._spotify_anon_token()
        if not token:
            return None

        url = (
            "https://api.spotify.com/v1/search?"
            + urlencode({"q": query, "type": kind, "limit": "1"})
        )

        data = None
        for attempt in range(max(1, retries)):
            req = Request(url, headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
            })
            try:
                import json as _json
                data = _json.loads(urlopen(req, timeout=8).read().decode("utf-8", "ignore"))
                break
            except HTTPError as error:
                if error.code != 429 or attempt == retries - 1:
                    return None
                try:
                    wait = int(error.headers.get("Retry-After", "15")) + 2
                except Exception:
                    wait = 15
                time.sleep(min(wait, 60))
            except Exception:
                return None

        if not data:
            return None
        try:
            if kind == "playlist":
                items = data["playlists"]["items"]
            else:
                items = data["tracks"]["items"]
            for item in items:
                uri = str(item.get("uri") or "")
                if uri.startswith("spotify:"):
                    return uri
        except Exception:
            pass
        return None

    @staticmethod
    def _spotify_search_keyboard_play(query=""):
        """Depois de abrir spotify:search:, espera os resultados carregarem e
        toca o PRIMEIRO RESULTADO da busca — a música pedida, não a faixa
        que estava rodando antes (a tecla de mídia 0xB3 só retomava ela).

        Precisão via UIAutomation: o Spotify desktop (app CEF) expõe a árvore
        de acessibilidade. Acho o botão de play do PRIMEIRO resultado e clico
        nele com o mouse (DoDefaultAction não funciona no CEF). Sem UIA
        disponível ou sem botão encontrado, cai no Tab x3 + Enter (impreciso,
        último caso).

        DADO REAL: a janela principal tem título "Artista - Faixa" (NUNCA
        "Spotify <busca>") — o foco é feito pelo pid do processo Spotify.exe.
        """
        if not VegaCommands._is_windows():
            return False

        import ctypes.wintypes

        user32 = ctypes.windll.user32
        VK_TAB = 0x09
        VK_RETURN = 0x0D

        def _focus_spotify():
            try:
                WNDENUMPROC = ctypes.WINFUNCTYPE(
                    ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
                )
                target = []
                spotify_pids = set()
                try:
                    import psutil
                    for proc in psutil.process_iter(["name"]):
                        if (proc.info["name"] or "").lower().startswith("spotify"):
                            spotify_pids.add(proc.pid)
                except Exception:
                    spotify_pids = set()

                def _cb(hwnd, _):
                    if user32.IsWindowVisible(hwnd):
                        buf_pid = ctypes.wintypes.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(buf_pid))
                        if buf_pid.value in spotify_pids:
                            target.append(hwnd)
                    return True

                user32.EnumWindows(WNDENUMPROC(_cb), 0)
                for hwnd in target:
                    if user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    if user32.SetForegroundWindow(hwnd):
                        return True
            except Exception:
                pass
            return False

        def _press(vk):
            try:
                user32.keybd_event(vk, 0, 0, 0)
                user32.keybd_event(vk, 0, 2, 0)
                return True
            except Exception:
                return False

        # O app abre/troca de tela; foca antes de qualquer tecla.
        time.sleep(0.8)
        _focus_spotify()

        # ESPERA os resultados carregarem — pela UIA, não pelo título.
        # DADOS REAIS: a janela principal do Spotify NUNCA muda o título
        # para "Spotify <busca>" (fica "Artista - Faixa"). Esperar por isso
        # queimava o timeout inteiro em TODO comando (era a demora).
        # O sinal real: os botões "Tocar <musica>" dos resultados aparecerem
        # na árvore de acessibilidade — checado dentro de
        # _spotify_uia_click_play_button, que já repete até achar.
        _focus_spotify()

        # PRECISO: acha o botão de play do PRIMEIRO resultado via UIA e clica.
        # max_wait alto: cobre app lento a abrir + resultados renderizando.
        # DADO REAL (dump de botões na máquina do usuário, busca "flowers"):
        # o 1º botão da coluna de resultados foi 'Tocar When I Was Your Man'
        # e 'Tocar Flowers' apareceu em 3º — ou seja, "menor Y" NÃO é
        # suficiente: precisa casar o TEXTO DA BUSCA no nome do botão.
        if VegaCommands._spotify_uia_click_play_button(query, max_wait=15.0):
            return True

        # Fallback impreciso (só se a UIA não estiver disponível): Tab x3
        # seleciona o primeiro resultado da busca; Enter toca ele.
        for _ in range(3):
            _press(VK_TAB)
            time.sleep(0.15)
        time.sleep(0.3)
        _press(VK_RETURN)
        return True

    @staticmethod
    def _spotify_uia_click_play_button(needle="", max_wait=6.0):
        """Via UIAutomation, acha o botão de play do PRIMEIRO resultado na
        janela do Spotify desktop e clica nele com o mouse.

        Critério simples (decisão do usuário): o botão de play do primeiro
        resultado é o de MENOR Y na tela entre os botões "Tocar ..."/
        "Play". Não casa com o texto da busca — o botão pode se chamar só
        "Tocar" e o app pode estar em inglês. Devolve True se clicou.
        """
        if not VegaCommands._is_windows():
            return False

        try:
            import comtypes
            import comtypes.client
        except ImportError:
            return False

        # DPI-AWARE por processo — OBRIGATÓRIO e antes de TODA leitura de
        # coordenadas: sem isso, com escala de tela >100% (ex.: 125% na
        # máquina do usuário), a UIA devolve coordenadas FÍSICAS mas o
        # SetCursorPos/mouse_event do processo não-aware são ESCALADOS para
        # baixo e o clique cai acima/esquerda do alvo (na máquina do usuário
        # caiu no botãozinho de curtir, à esquerda do play). Com awareness(2)
        # o mesmo ponto físico sai idêntico. Só precisa ser chamado UMA vez
        # por processo; se falhar (já definido), segue — é idempotente.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        import ctypes.wintypes  # noqa: F401 - POINT em _click_preserving_cursor
        user32 = ctypes.windll.user32

        def _norm(text):
            """minúsculas + sem acento, para casar 'Flowers'/'flowers' e nomes
            com ç/ã — o Spotify usa o idioma do app nos nomes dos botões."""
            try:
                text = text.lower()
                import unicodedata
                return "".join(
                    ch for ch in unicodedata.normalize("NFD", text)
                    if unicodedata.category(ch) != "Mn"
                )
            except Exception:
                return (text or "").lower()

        # DADOS REAIS (debug na máquina do usuário, busca "flowers"):
        #   'Play'                              (1154,156)-(1203,204)  <- botão VERDE GRANDE do 1º resultado
        #   'Tocar When I Was Your Man'         (402,256)-(451,304)    <- 2º resultado
        #   'Tocar Flowers'                     (402,320)-(451,368)    <- 3º resultado
        #   'Tocar Músicas curtidas'            (24,221)-(72,270)      <- ARMADILHA da sidebar (x pequeno)
        #   'Tocar Seus episódios'              (24,285)-(72,334)      <- ARMADILHA da sidebar
        # O alvo é o botão 'Play' do topo da área de resultados. Para achar:
        #   1) descarta botões da sidebar (x < 380 — sidebar vai até ~354);
        #   2) entre os restantes, menor Y = topo da lista;
        #   3) o botão do 1º resultado pode se chamar 'Play' (verde grande),
        #      'Tocar <nome>' ou 'Tocar' — aceito todos esses nomes.
        #   4) DADO REAL (repro na máquina): o botão 'Play' VERDE GRANDE do
        #      PLAYER fica em x≈1154 (canto sup. direito) e y≈156 — MENOR Y
        #      que os resultados (y≈320+). O critério antigo só limitava x por
        #      baixo (sidebar), então o Play do player vencia SEMPRE e o clique
        #      retomava a faixa anterior. Correção: os resultados da busca
        #      ficam na coluna x≈380–1100; botões fora dessa faixa (player à
        #      direita, sidebar à esquerda) são descartados.
        #   5) DADO REAL (dump ao vivo, busca "flowers"): o Spotify NÃO põe a
        #      música pedida primeiro — a lista veio 'Tocar When I Was Your
        #      Man' (y=256), 'Tocar Flowers' (y=320), ... O critério "menor Y"
        #      tocava a MÚSICA ERRADA. Correção (referência = os acertos):
        #      entre os botões da coluna de resultados, ESCOLHER o que casa
        #      com o texto da busca no nome ('Tocar Flowers' contém "flowers"
        #      — o nome do botão vem do app em pt/en, sem acento crítico);
        #      "menor Y" vira só o fallback quando NENHUM nome casar.
        SIDEBAR_X_MAX = 380
        PLAYER_X_MIN = 1100  # botão 'Play' verde grande do player: x≈1154
        needle_norm = _norm(needle)
        # candidatas na coluna de resultados, em ordem de Y.
        candidates = []  # (top, element)
        needle_match = [None]  # 1º botão cujo nome contém a busca
        best_lock = threading.Lock()

        def _find_play_button():
            try:
                comtypes.CoInitialize()
                from comtypes.client import GetModule
                GetModule("UIAutomationCore.dll")
                import comtypes.gen.UIAutomationClient as uia
                tm = comtypes.CoCreateInstance(
                    uia.CUIAutomation._reg_clsid_,
                    interface=uia.IUIAutomation,
                    clsctx=comtypes.CLSCTX_INPROC_SERVER,
                )
                root = tm.GetRootElement()
                # DADO REAL: a janela principal do Spotify NÃO tem "spotify"
                # no título (fica "Artista - Faixa"). Acho pelo PROCESSO:
                # janelas cujo pid é do Spotify.exe. Também ignora janelas
                # sem botões (a janela oculta "" que o app cria).
                wins = root.FindAll(uia.TreeScope_Children, tm.CreateTrueCondition())
                spotify_pids = set()
                try:
                    import psutil
                    for proc in psutil.process_iter(["name"]):
                        if (proc.info["name"] or "").lower().startswith("spotify"):
                            spotify_pids.add(proc.pid)
                except Exception:
                    spotify_pids = set()
                if not spotify_pids:
                    return None
                win = None
                for i in range(wins.Length):
                    w = wins.GetElement(i)
                    try:
                        hwnd = w.CurrentNativeWindowHandle
                    except Exception:
                        continue
                    if not hwnd:
                        continue
                    buf_pid = ctypes.wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(buf_pid))
                    if buf_pid.value in spotify_pids:
                        win = w
                        break
                if win is None:
                    return None
                btns = win.FindAll(
                    uia.TreeScope_Descendants,
                    tm.CreatePropertyCondition(
                        uia.UIA_ControlTypePropertyId, uia.UIA_ButtonControlTypeId
                    ),
                )
                for i in range(btns.Length):
                    b = btns.GetElement(i)
                    name = (b.CurrentName or "").lower().strip()
                    # Botões de play dos resultados: "Tocar <musica>" (pt),
                    # "Tocar"/"Play" (o botão verde grande do 1º resultado
                    # no Spotify desktop atual se chama só 'Play').
                    if not (name == "play" or name.startswith("tocar")):
                        continue
                    try:
                        rect = b.CurrentBoundingRectangle
                    except Exception:
                        continue
                    # Descarta botões de PLAY da sidebar ("Tocar Músicas
                    # curtidas", "Tocar Seus episódios", "Tocar <playlist>")
                    # e da barra superior: todos têm x pequeno.
                    if rect.left < SIDEBAR_X_MAX:
                        continue
                    # Descarta o botão 'Play' GRANDE do player (canto sup.
                    # direito, x≈1154): menor Y que os resultados, mas NÃO é
                    # um resultado da busca — é o controle do player.
                    if rect.left >= PLAYER_X_MIN:
                        continue
                    with best_lock:
                        candidates.append((rect.top, b))
                        # CASAMENTO POR NOME: 'Tocar Flowers' contém "flowers".
                        # O nome do botão inclui o título da faixa — compara
                        # sem acento e minúsculo contra o texto da busca.
                        if (
                            needle_norm
                            and needle_match[0] is None
                            and needle_norm in _norm(name)
                        ):
                            needle_match[0] = b
                return None  # o loop externo decide via needle_match/candidates
            except Exception:
                return None

        def _click_preserving_cursor(button):
            """Clique físico com SALVAGUARDA: salva a posição do cursor,
            clica no centro do botão e RESTAURA a posição — se o usuário
            estiver mexendo o mouse, ele só treme por 0,15s e volta.

            DPI-AWARE (obrigatório): sem isso, com escala de tela >100%
            (ex.: 125%), o Windows escala as coordenadas do SetCursorPos e
            o clique desloca — na máquina do usuário caiu no botãozinho à
            esquerda do play (o de curtir). SetProcessDpiAwareness fixa isso.

            SEM sleep entre mover e clicar: com sleep, se o usuário mexer o
            mouse nesse intervalo, o clique cai onde o cursor foi parar
            (foi exatamente isso que aconteceu — o clique errava só quando
            o usuário estava mexendo o mouse). Mover+clicar é atômico o
            bastante: microssegundos de distância.
            """
            try:
                try:
                    import ctypes.wintypes
                    # Per-process: torna as coordenadas físicas, não lógicas.
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    try:
                        ctypes.windll.user32.SetProcessDPIAware()
                    except Exception:
                        pass
                pt = ctypes.wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                rect = button.CurrentBoundingRectangle
                x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
                user32.SetCursorPos(x, y)
                user32.mouse_event(2, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTDOWN
                user32.mouse_event(4, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTUP
                time.sleep(0.05)
                user32.SetCursorPos(pt.x, pt.y)
                return True
            except Exception:
                return False

        deadline = time.time() + max_wait
        while time.time() < deadline:
            _find_play_button()
            # 1ª escolha: botão cujo nome casa com a busca ('Tocar Flowers').
            # Fallback: menor Y da coluna de resultados (comportamento antigo).
            with best_lock:
                if needle_match[0] is not None:
                    button = needle_match[0]
                elif candidates:
                    candidates.sort(key=lambda item: item[0])
                    button = candidates[0][1]
                else:
                    button = None
            if button is not None:
                clicked = _click_preserving_cursor(button)
                if clicked:
                    return True
                # Elemento stale (a lista re-renderizou): limpa e tenta de novo.
                with best_lock:
                    needle_match[0] = None
                    candidates.clear()
            time.sleep(0.2)
        return False

    @staticmethod
    def _ensure_spotify_play(max_wait=6.0):
        """Garante play no Spotify desktop depois de abrir um deep-link.

        Espera uma resposta FRESCA do PlaybackApi (estado que chegou depois
        do nosso getdata — estado em cache pode ser de minutos atrás) e só
        aperta play se estiver pausado. Sem resposta, cai na tecla de mídia.
        """
        try:
            from core.actions import spotify_bridge
        except Exception:
            spotify_bridge = None

        if spotify_bridge is not None and spotify_bridge.is_connected():
            try:
                baseline = spotify_bridge.get_state() or {}
                baseline_at = baseline.get("updated_at", 0.0)
            except Exception:
                baseline_at = 0.0
            deadline = time.time() + max_wait
            while time.time() < deadline:
                try:
                    spotify_bridge.request_state()
                except Exception:
                    pass
                time.sleep(0.5)
                try:
                    state = spotify_bridge.get_state() or {}
                except Exception:
                    state = {}
                if state.get("updated_at", 0.0) > baseline_at and state.get("title"):
                    if str(state.get("state") or "").strip().upper() != "TOCANDO":
                        spotify_bridge.toggle_play()
                    return

        # Sem PlaybackApi respondendo: tecla de play/pause de mídia (app em foco).
        time.sleep(2.0)
        VegaCommands._press_key(0xB3)

    @staticmethod
    def play_music(query, platform="youtube", playlist=False):
        """Toca música/playlist específica na plataforma CITADA na fala.

        Decisão do usuário (revisada): a plataforma que o usuário nomeou é
        respeitada. O Spotify desktop tem caminho real de reprodução
        (play_music_spotify resolve o URI da faixa/playlist via API anônima
        e abre spotify:track:<uri> TOCANDO, sem Premium). Deezer e YT Music
        não têm esse caminho: caem para o YouTube com autoplay real.

        Sem plataforma citada, o padrão continua sendo o YouTube.
        Playlists pessoais salvas continuam na plataforma em que foram
        gravadas — esse caminho (play_personal_playlist) funciona e não muda.
        """
        platform = (platform or "youtube").lower()
        # Qualquer pedido de música para a música de fundo: ela para, mesmo
        # que a plataforma pedida seja outra (Spotify/Deezer...).
        VegaCommands._stop_background_music()
        if "spotify" in platform:
            # Spotify tem reprodução real: resolve URI e toca no app.
            return VegaCommands.play_music_spotify(query, playlist=playlist)
        # Deezer e YouTube Music não tocam sozinhos sem Premium: o destino
        # é o YouTube, que abre JÁ TOCANDO a música pedida.
        return VegaCommands.play_music_youtube(query, playlist=playlist)

    # ==========================================================
    # PLAYLISTS PESSOAIS
    # ==========================================================

    def play_personal_playlist(self, name, platform=None):
        """Toca uma playlist do usuário (guardada por nome ou busca direta)."""
        return self.playlists.play(name, platform=platform)

    def save_personal_playlist(self, name, platform="youtube", target=""):
        """Guarda uma playlist pessoal pelo nome falado."""
        return self.playlists.save(name, platform=platform, target=target)

    def list_personal_playlists(self):
        """Lista as playlists pessoais guardadas."""
        entries = self.playlists.list_names()
        if not entries:
            return (
                "Você ainda não tem playlists guardadas. "
                "Diga 'guarda essa playlist trabalho' para criar uma."
            )

        lines = ["Suas playlists guardadas:"]
        for entry in entries:
            name = str(entry.get("name") or "").strip()
            platform = str(entry.get("platform") or "youtube").strip()
            if name:
                lines.append(f"- {name} ({platform})")
        return "\n".join(lines)

    # ==========================================================
    # FUNK
    # ==========================================================

    @staticmethod
    def play_funk():
        # Aba interna da VEGA (mesma decisão do play_music_youtube): o
        # funk toca no player embutido, não abre página de resultados no
        # Chrome. play_music_youtube resolve o videoId e manda pro painel.
        VegaCommands._stop_background_music()
        return VegaCommands.play_music_youtube("funk")

    # ==========================================================
    # AMBIENTE
    # ==========================================================

    @staticmethod
    def play_ambient():

        # Aba interna da VEGA: antes abria a página de RESULTADOS do
        # YouTube no Chrome (o usuário ficava na lista, sem tocar nada).
        # Agora resolve o videoId do 1º vídeo ambiente e toca no player
        # embutido — mesmo canal de play_music_youtube. O reforço de
        # volume continua, pois música ambiente pede volume alto.
        VegaCommands._stop_background_music()
        VegaCommands.play_music_youtube("música ambiente paisagem relaxante")
        VegaCommands.volume_up()

        return "Iniciando música ambiente."

    # ==========================================================
    # CONECTIVIDADE (CELULAR / TV)
    # ==========================================================

    def connectivity(self):
        """Cria sob demanda o gerenciador de conectividade.

        Preguiçoso de propósito: a VEGA não paga custo de descoberta de
        rede no startup, e se a camada falhar, só os comandos de
        conectividade são afetados.
        """
        manager = getattr(self, "_connectivity_manager", None)
        if manager is None:
            try:
                from core.connectivity import ConnectivityManager
                manager = ConnectivityManager()
            except Exception as error:
                print(f"[VEGA] Conectividade indisponível: {error}")
                manager = False
            self._connectivity_manager = manager
        return manager

    def connectivity_action(self, action, text="", **kwargs):
        # O parser manda a fala como `spoken_text` no kwargs; aqui o parâmetro
        # canônico é `text`. Sem remover, `manager.route()` recebe a fala duas
        # vezes e levanta "got multiple values for 'spoken_text'" (BUG REAL:
        # "abre o spotify no celular" morria na thread de fundo sem resposta).
        if "spoken_text" in kwargs:
            if not text:
                text = kwargs.pop("spoken_text") or ""
            else:
                kwargs.pop("spoken_text")
        """Executa uma ação de conectividade e devolve a mensagem para falar.

        Ações que falam com hardware (ADB no celular) podem demorar vários
        segundos — se rodarem no mesmo fluxo da resposta, a VEGA congela
        sem falar nada (o usuário ouvia silêncio e achava que tinha travado).
        A estratégia é responder IMEDIATAMENTE com uma mensagem de "estou
        fazendo" e executar de verdade numa thread; o resultado final vai
        pelo callback de resultado (mesma via do print do celular).
        """
        manager = self.connectivity()
        if not manager:
            return "A camada de conectividade não está disponível agora."

        # Ações lentas (abrir app, print, descoberta, tocar música no
        # celular...) rodam fora do fluxo: resposta falada instantânea.
        if action in self.SLOW_CONNECTIVITY_ACTIONS:
            spoken = self._quick_phone_reply(action, kwargs, text)
            self._dispatch_connectivity_background(
                manager, action, text, kwargs, spoken
            )
            return spoken

        result = manager.route(action, spoken_text=text, **kwargs)
        # Resultados com imagem (print do celular) vão para o painel da VEGA.
        if result.ok and isinstance(result.data, dict) and result.data.get("path"):
            if callable(getattr(self, "_result_callback", None)):
                try:
                    self._result_callback("screenshot", result.data)
                except Exception:
                    pass
        return result.message or ("Feito." if result.ok else "Não consegui fazer isso.")

    def list_devices(self):
        manager = self.connectivity()
        if not manager:
            return "A camada de conectividade não está disponível agora."
        return manager.describe_devices()

    def discover_devices(self):
        manager = self.connectivity()
        if not manager:
            return "A camada de conectividade não está disponível agora."
        found = manager.discover_all()
        total = sum(len(items) for items in found.values())
        if not total:
            return (
                "Não encontrei dispositivos agora. Verifique se estão ligados "
                "e na mesma rede."
            )
        names = [
            item["name"]
            for items in found.values()
            for item in items
            if isinstance(item, dict) and item.get("name")
        ]
        return f"Encontrei: {', '.join(names)}."

    def connect_phone(self, action, text="", **kwargs):
        """Atalho: ações no celular."""
        return self.connectivity_action(action, text=text, kind="phone", **kwargs)

    # ==========================================================
    # EXECUÇÃO EM FUNDO (ações do celular não travam a fala)
    # ==========================================================

    # Ações que conversam com ADB/rediscovery e podem levar segundos.
    # Tudo o que NÃO estiver aqui roda no fluxo (resposta final imediata).
    SLOW_CONNECTIVITY_ACTIONS = {
        "open_app",
        "screenshot",
        "send_file",
        "pull_file",
        "mirror",
        "stop_mirror",
        "identify",
        "list",
        "take_photo",
        "play_music",
        "send_whatsapp",
        "send_message",
        "call",
        "read_conversation",
        "search_web",
        "pair_wireless",
        "connect_wireless",
        "enable_wireless",
        "reconnect",
    }

    # Mensagem falada imediata para cada ação lenta, ANTES do resultado real.
    _QUICK_PHONE_REPLIES = {
        "open_app": "Abrindo {app} no celular.",
        "screenshot": "Tirando print do celular.",
        "send_file": "Enviando o arquivo para o celular.",
        "pull_file": "Buscando o arquivo no celular.",
        "mirror": "Espelhando a tela do celular.",
        "stop_mirror": "Parando o espelhamento.",
        "take_photo": "Tirando a foto.",
        "play_music": "Tocando no celular.",
        "send_whatsapp": "Enviando a mensagem pelo WhatsApp.",
        "send_message": "Enviando a mensagem.",
        "call": "Chamando.",
        "read_conversation": "Lendo as mensagens.",
        "search_web": "Pesquisando no celular.",
    }

    def _quick_phone_reply(self, action, kwargs, text):
        """Resposta falada instantânea para uma ação lenta do celular."""
        template = self._QUICK_PHONE_REPLIES.get(action)
        if not template:
            return "Fazendo isso no celular agora."
        app = str(kwargs.get("app") or "").strip()
        if "{app}" in template and not app:
            # "abre o youtube no celular" sem app extraído — extrai da fala.
            match = re.search(
                r"(?:abre|abrir|abra|executa)\s+(?:o\s+|a\s+)?(?:app\s+)?(.+?)\s+"
                r"(?:no|na)\s+(?:meu\s+)?(?:celular|telefone|smartphone|android)",
                (text or "").lower(),
            )
            app = match.group(1).strip(" ,.!") if match else "o aplicativo"
        return template.format(app=app)

    def _dispatch_connectivity_background(
        self, manager, action, text, kwargs, spoken
    ):
        """Roda a ação lenta numa thread e publica o resultado pelo callback."""

        def _run():
            try:
                result = manager.route(action, spoken_text=text, **kwargs)
                message = result.message or ("Feito." if result.ok else "Não consegui fazer isso.")
                if result.ok and isinstance(result.data, dict) and result.data.get("path"):
                    if callable(getattr(self, "_result_callback", None)):
                        try:
                            self._result_callback("screenshot", result.data)
                        except Exception:
                            pass
                if callable(getattr(self, "_result_callback", None)):
                    try:
                        self._result_callback("connectivity_result", {
                            "action": action,
                            "ok": result.ok,
                            "message": message,
                            "spoken": spoken,
                        })
                    except Exception:
                        pass
            except Exception as error:
                print(f"[VEGA] Ação de conectividade falhou em fundo: {error}")

        thread = threading.Thread(target=_run, daemon=True, name="vega-connectivity")
        thread.start()

    def connect_tv(self, action, text="", **kwargs):
        """Atalho: ações na TV."""
        return self.connectivity_action(action, text=text, kind="tv", **kwargs)

    def pair_phone_wireless(self, address, code):
        """Pareia o celular sem fio com o código de 6 dígitos do aparelho."""
        manager = self.connectivity()
        if not manager:
            return "A camada de conectividade não está disponível agora."
        result = manager.route(
            "pair_wireless", kind="phone", address=address, code=code,
        )
        return result.message or ("Pareado." if result.ok else "Não consegui parear.")

    # ==========================================================
    # CAPTURA DE TELA DO PC
    # ==========================================================

    def screenshot_pc(self, show=True):
        """Tira um print da tela do PC e mostra na hora."""
        from core.actions.screenshot import capture_screen
        result = capture_screen(show=show)
        if result.get("ok") and callable(getattr(self, "_result_callback", None)):
            try:
                self._result_callback("screenshot", result)
            except Exception:
                pass
        return result.get("message") or "Não consegui capturar a tela."

    # ==========================================================
    # MODOS
    # ==========================================================

    @staticmethod
    def mode_gamer():

        VegaCommands.set_brightness(100)
        VegaCommands.set_volume(50)
        VegaCommands.set_bluetooth(True)
        VegaCommands.open_app("Steam")

        time.sleep(1)

        VegaCommands.open_app("Epic Games")

        time.sleep(1)

        VegaCommands.close_app("Spotify")

        return (
            "Modo Gamer ativado.\n"
            "- Brilho: 100%\n"
            "- Volume: 50%\n"
            "- Bluetooth: ativado\n"
            "- Steam: abrindo\n"
            "- Epic Games: abrindo\n"
            "- Spotify: fechado"
        )

    @staticmethod
    def mode_work():

        VegaCommands.set_notifications(False)
        VegaCommands.set_volume(100)
        VegaCommands.open_canva()
        VegaCommands.open_whatsapp()
        VegaCommands.open_google()

        return (
            "Modo Trabalho ativado.\n"
            "- Notificações: silenciadas\n"
            "- Volume: 100%\n"
            "- Canva: abrindo\n"
            "- WhatsApp: abrindo\n"
            "- Google: abrindo"
        )

    @staticmethod
    def mode_ambient():

        result = VegaCommands.play_ambient()

        return (
            "Modo Ambiente ativado.\n"
            f"- {result}"
        )

    # ==========================================================
    # RESOLUÇÃO DE LISTA DE EXERCÍCIOS / TRABALHO (JR)
    # ==========================================================

    def solve_homework(self, folder_or_path=None, language=None):
        from core.actions.homework import VegaHomework

        ai = self.ai
        if ai is None:
            from core.brain.ai import VegaAI
            ai = VegaAI()

        return VegaHomework.solve_homework(
            ai=ai,
            folder_or_path=folder_or_path,
            language=language,
        )

    def has_pending_homework(self):
        from core.actions.homework import VegaHomework
        return VegaHomework.has_pending_action()

    def start_homework_dialog(self, folder_or_keyword=None, language=None):
        from core.actions.homework import VegaHomework
        return VegaHomework.start_homework_dialog(
            folder_or_keyword=folder_or_keyword,
            language=language,
        )

    def handle_homework_dialog(self, user_text):
        from core.actions.homework import VegaHomework
        ai = self.ai
        if ai is None:
            from core.brain.ai import VegaAI
            ai = VegaAI()
        return VegaHomework.handle_dialog(ai, user_text)


# ==============================================================
# COMANDOS FIXOS
# ==============================================================

COMMANDS = {

    "google": VegaCommands.open_google,
    "youtube": VegaCommands.open_youtube,
    "chrome": VegaCommands.open_chrome,
    "spotify": VegaCommands.open_spotify,
    "canva": VegaCommands.open_canva,
    "whatsapp": VegaCommands.open_whatsapp,

    "documentos": VegaCommands.open_documents,
    "downloads": VegaCommands.open_downloads,
    "desktop": VegaCommands.open_desktop,

    "volume_up": VegaCommands.volume_up,
    "volume_down": VegaCommands.volume_down,
    "volume_max": VegaCommands.volume_max,
    "volume_min": VegaCommands.volume_min,
    "volume_mute": VegaCommands.volume_mute,
    "volume_unmute": VegaCommands.volume_unmute,
    "set_volume": VegaCommands.set_volume,

    "media_play_pause": VegaCommands.media_play_pause,
    "media_next": VegaCommands.media_next,
    "media_previous": VegaCommands.media_previous,
    "media_stop": VegaCommands.media_stop,

    "close_window": VegaCommands.close_window,
    "minimize_window": VegaCommands.minimize_window,
    "maximize_window": VegaCommands.maximize_window,
    "restore_window": VegaCommands.restore_window,
    "switch_window": VegaCommands.switch_window,
    "minimize_all": VegaCommands.minimize_all,
    "show_desktop": VegaCommands.show_desktop,
    "fullscreen": VegaCommands.fullscreen,

    "brightness_up": VegaCommands.brightness_up,
    "brightness_down": VegaCommands.brightness_down,
    "brightness_max": VegaCommands.brightness_max,
    "brightness_min": VegaCommands.brightness_min,
    "set_brightness": VegaCommands.set_brightness,

    "set_bluetooth": VegaCommands.set_bluetooth,
    "set_notifications": VegaCommands.set_notifications,

    "turn_off_screen": VegaCommands.turn_off_screen,
    "lock_pc": VegaCommands.lock_pc,

    "copy": VegaCommands.copy,
    "paste": VegaCommands.paste,
    "cut": VegaCommands.cut,
    "select_all": VegaCommands.select_all,
    "undo": VegaCommands.undo,
    "redo": VegaCommands.redo,
    "save": VegaCommands.save,
    "new": VegaCommands.new,
    "alt_tab": VegaCommands.alt_tab,

    "restart": VegaCommands.restart_pc,
    "shutdown": VegaCommands.shutdown_pc,
    "cancel_shutdown": VegaCommands.cancel_shutdown,

    "funk": VegaCommands.play_funk,
    "search_google": VegaCommands.search_google,
    "search_youtube": VegaCommands.search_youtube,

    "modo_gamer": VegaCommands.mode_gamer,
    "modo_trabalho": VegaCommands.mode_work,
    "modo_ambiente": VegaCommands.mode_ambient,
}
