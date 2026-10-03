import os
import sys
import platform
import subprocess
import webbrowser
import shutil
import re
import ctypes
import time
import unicodedata

from pathlib import Path
from urllib.parse import quote_plus


class VegaCommands:

    def __init__(self, ai=None):
        self.ai = ai

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
        """

        if not VegaCommands._is_windows():
            return None

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
                    return path

        # Última tentativa: PATH.
        found = shutil.which("chrome.exe")

        if found:
            path = Path(found)

            if path.is_file() and path.name.lower() == "chrome.exe":
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

        candidates = []

        for start_path in VegaCommands._get_start_menu_paths():

            try:
                for shortcut in start_path.rglob("*.lnk"):

                    shortcut_name = VegaCommands._normalize_name(
                        shortcut.stem
                    )

                    if not shortcut_name:
                        continue

                    if shortcut_name == target:
                        return shortcut

                    if target in shortcut_name:
                        candidates.append(
                            (shortcut, shortcut_name)
                        )

                    elif shortcut_name in target:
                        candidates.append(
                            (shortcut, shortcut_name)
                        )

            except Exception:
                continue

        if candidates:
            candidates.sort(key=lambda item: len(item[1]))
            return candidates[0][0]

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

        for executable in names:

            found = shutil.which(executable)

            if found:
                path = Path(found)

                if path.name.lower() == executable.lower():
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

        for base in dirs:

            for executable_name in names:

                try:
                    for exe in base.rglob(executable_name):

                        if exe.is_file():
                            return exe

                except Exception:
                    continue

        for base in dirs:
            try:
                for exe in base.rglob("*.exe"):
                    if not exe.is_file():
                        continue

                    exe_name = VegaCommands._normalize_name(exe.name)

                    if not exe_name:
                        continue

                    if exe_name == target or target in exe_name or exe_name in target:
                        return exe

            except Exception:
                continue

        return None

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
    def close_app(app_name):

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

        for process in processes:

            try:

                result = subprocess.run(
                    [
                        "taskkill",
                        "/IM",
                        process,
                        "/T",
                        "/F"
                    ],
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

    @staticmethod
    def set_volume(percent):

        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."

        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return "O volume precisa ser um número."

        percent = max(0, min(100, percent))

        try:

            for _ in range(50):
                VegaCommands._press_key(0xAE)

            for _ in range(round(percent / 2)):
                VegaCommands._press_key(0xAF)

            return f"Volume ajustado para {percent}%."

        except Exception:
            return "Não consegui ajustar o volume."

    @staticmethod
    def volume_up():

        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."

        for _ in range(5):
            VegaCommands._press_key(0xAF)

        return "Aumentando o volume."

    @staticmethod
    def volume_down():

        if not VegaCommands._is_windows():
            return "O controle de volume está configurado para Windows."

        for _ in range(5):
            VegaCommands._press_key(0xAE)

        return "Diminuindo o volume."

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
    def media_play_pause():

        if VegaCommands._press_key(0xB3):
            return "Pausando ou continuando a mídia."

        return "Não consegui controlar a mídia."

    @staticmethod
    def media_next():

        if VegaCommands._press_key(0xB0):
            return "Próxima mídia."

        return "Não consegui avançar a mídia."

    @staticmethod
    def media_previous():

        if VegaCommands._press_key(0xB1):
            return "Voltando para a mídia anterior."

        return "Não consegui voltar a mídia."

    @staticmethod
    def media_stop():

        if VegaCommands._press_key(0xB2):
            return "Parando a mídia."

        return "Não consegui parar a mídia."

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
        if VegaCommands._press_key(0x7A):
            return "Ativando tela cheia."
        return "Não consegui ativar a tela cheia."

    # ==========================================================
    # BRILHO
    # ==========================================================

    @staticmethod
    def set_brightness(percent):

        if not VegaCommands._is_windows():
            return "O controle de brilho está configurado para Windows."

        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return "O brilho precisa ser um número."

        percent = max(0, min(100, percent))

        result = VegaCommands._run_powershell(
            f"""
            (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1, {percent})
            """
        )

        if result and result.returncode == 0:
            return f"Brilho ajustado para {percent}%."

        return "Não consegui alterar o brilho."

    @staticmethod
    def brightness_up():

        result = VegaCommands._run_powershell(
            """
            $brightness = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness
            $new = [Math]::Min(100, $brightness + 10)
            (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1, $new)
            """
        )

        if result and result.returncode == 0:
            return "Aumentando o brilho."

        return "Não consegui alterar o brilho."

    @staticmethod
    def brightness_down():

        result = VegaCommands._run_powershell(
            """
            $brightness = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness
            $new = [Math]::Max(0, $brightness - 10)
            (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1, $new)
            """
        )

        if result and result.returncode == 0:
            return "Diminuindo o brilho."

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

        try:
            subprocess.Popen(["shutdown", "/r", "/t", "5"])

            return "O computador será reiniciado em 5 segundos."

        except Exception:
            return "Não consegui reiniciar o computador."

    @staticmethod
    def shutdown_pc():

        if not VegaCommands._is_windows():
            return "O desligamento está configurado para Windows."

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

    # ==========================================================
    # FUNK
    # ==========================================================

    @staticmethod
    def play_funk():
        return VegaCommands.search_youtube("funk")

    # ==========================================================
    # AMBIENTE
    # ==========================================================

    @staticmethod
    def play_ambient():

        url = (
            "https://www.youtube.com/results?search_query="
            + quote_plus("música ambiente paisagem relaxante")
        )

        webbrowser.open(url)
        VegaCommands.volume_up()

        return "Iniciando música ambiente."

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

        time.sleep(1)

        VegaCommands.open_whatsapp()

        time.sleep(1)

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
