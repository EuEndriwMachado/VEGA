import os
import platform
import subprocess
import webbrowser
import random
import threading
import cv2


LIVE_MODE_ACTIVE = False


class VegaModes:

    @staticmethod
    def is_live_active():
        return LIVE_MODE_ACTIVE

    # ==========================================================
    # UTILIDADES
    # ==========================================================

    @staticmethod
    def _is_windows():
        return platform.system() == "Windows"

    # ==========================================================
    # MODO GAMER
    # ==========================================================

    @staticmethod
    def gamer(commands):

        results = []

        # ------------------------------------------------------
        # BRILHO 100%
        # ------------------------------------------------------

        try:
            results.append(
                commands.brightness_max()
            )
        except Exception as error:
            results.append(
                f"Erro ao ajustar brilho: {error}"
            )

        # ------------------------------------------------------
        # VOLUME 50%
        # ------------------------------------------------------

        try:
            results.append(
                commands.set_volume(50)
            )
        except Exception as error:
            results.append(
                f"Erro ao ajustar volume: {error}"
            )

        # ------------------------------------------------------
        # BLUETOOTH
        # ------------------------------------------------------

        try:
            results.append(
                commands.set_bluetooth(True)
            )
        except Exception as error:
            results.append(
                f"Erro ao ativar Bluetooth: {error}"
            )

        # ------------------------------------------------------
        # FECHAR SPOTIFY
        # ------------------------------------------------------

        try:
            results.append(
                commands.close_app("spotify")
            )
        except Exception as error:
            results.append(
                f"Erro ao fechar Spotify: {error}"
            )

        # ------------------------------------------------------
        # ABRIR STEAM
        # ------------------------------------------------------

        try:
            results.append(
                commands.open_app("steam")
            )
        except Exception as error:
            results.append(
                f"Erro ao abrir Steam: {error}"
            )

        # ------------------------------------------------------
        # ABRIR EPIC GAMES
        # ------------------------------------------------------

        try:
            results.append(
                commands.open_app("epic games")
            )
        except Exception as error:
            results.append(
                f"Erro ao abrir Epic Games: {error}"
            )

        return (
            "Modo Gamer ativado.\n\n"
            + "\n".join(results)
        )

    # ==========================================================
    # MODO TRABALHO
    # ==========================================================

    @staticmethod
    def trabalho(commands):

        results = []

        # ------------------------------------------------------
        # SILENCIAR NOTIFICAÇÕES
        # ------------------------------------------------------

        try:
            results.append(
                VegaModes._enable_do_not_disturb()
            )
        except Exception as error:
            results.append(
                f"Erro ao silenciar notificações: {error}"
            )

        # ------------------------------------------------------
        # VOLUME 100%
        # ------------------------------------------------------

        try:
            results.append(
                commands.volume_max()
            )
        except Exception as error:
            results.append(
                f"Erro ao aumentar volume: {error}"
            )

        # ------------------------------------------------------
        # CANVA
        # ------------------------------------------------------

        try:
            webbrowser.open(
                "https://www.canva.com"
            )

            results.append(
                "Abrindo o Canva."
            )

        except Exception as error:
            results.append(
                f"Erro ao abrir Canva: {error}"
            )

        # ------------------------------------------------------
        # WHATSAPP
        # ------------------------------------------------------

        try:
            webbrowser.open(
                "https://web.whatsapp.com"
            )

            results.append(
                "Abrindo o WhatsApp."
            )

        except Exception as error:
            results.append(
                f"Erro ao abrir WhatsApp: {error}"
            )

        # ------------------------------------------------------
        # GOOGLE
        # ------------------------------------------------------

        try:
            results.append(
                commands.open_google()
            )
        except Exception as error:
            results.append(
                f"Erro ao abrir Google: {error}"
            )

        return (
            "Modo Trabalho ativado.\n\n"
            + "\n".join(results)
        )

    # ==========================================================
    # MODO AMBIENTE
    # ==========================================================

    @staticmethod
    def ambiente(commands):

        results = []

        # ------------------------------------------------------
        # AUMENTAR VOLUME
        # ------------------------------------------------------

        try:
            results.append(
                commands.volume_up()
            )
        except Exception as error:
            results.append(
                f"Erro ao aumentar volume: {error}"
            )

        # ------------------------------------------------------
        # RODAR VÍDEO AMBIENTE
        # ------------------------------------------------------

        try:
            results.append(
                VegaModes._play_ambient_video()
            )
        except Exception as error:
            results.append(
                f"Erro ao iniciar vídeo ambiente: {error}"
            )

        return (
            "Modo Ambiente ativado.\n\n"
            + "\n".join(results)
        )

    # ==========================================================
    # MODO LIVE / MULTIMODAL
    # ==========================================================

    @staticmethod
    def disable_live():
        global LIVE_MODE_ACTIVE

        if not LIVE_MODE_ACTIVE:
            return "O modo Live já está desligado. A VEGA continua no modo texto."

        LIVE_MODE_ACTIVE = False
        return "Modo Live desligado. Voltando para o Gemini em texto."

    @staticmethod
    def live(commands):
        global LIVE_MODE_ACTIVE

        if LIVE_MODE_ACTIVE:
            return "O modo Live já está ativo."

        try:
            from core.brain.ai import VegaLiveSession
        except Exception as error:
            return (
                "O modo live da VEGA não está disponível agora: "
                f"{error}"
            )

        if not getattr(__import__("config.ai", fromlist=["GEMINI_LIVE_MODEL"]), "GEMINI_LIVE_MODEL", ""):
            return (
                "Modo Live indisponível neste ambiente. A VEGA está usando o Gemini em texto, "
                "que é mais estável e rápido para conversas normais."
            )

        try:
            from core.audio.speaker import VegaSpeaker
            speaker = VegaSpeaker()
        except Exception:
            speaker = None

        try:
            session = VegaLiveSession(
                video_mode="camera",
                speaker=speaker,
                camera_index=1,
                microphone_index=1,
            )
            if not session._api_key:
                return (
                    "Modo Live indisponível: GEMINI_API_KEY não está configurada. "
                    "Adicione sua chave ao arquivo .env e reinicie a VEGA."
                )

            camera = cv2.VideoCapture(1, cv2.CAP_DSHOW)
            camera_ok, frame = camera.read()
            camera.release()
            if not camera_ok or frame is None:
                return "Modo Live indisponível: não consegui obter imagem da câmera 1."

            LIVE_MODE_ACTIVE = True

            def run_live():
                global LIVE_MODE_ACTIVE
                try:
                    session.run()
                finally:
                    LIVE_MODE_ACTIVE = False

            thread = threading.Thread(
                target=run_live,
                name="VegaGeminiLive",
                daemon=True,
            )
            thread.start()
        except Exception as error:
            return f"Não consegui preparar o live mode: {error}"

        return "Modo Live ativado."

    # ==========================================================
    # NÃO PERTURBE / NOTIFICAÇÕES
    # ==========================================================

    @staticmethod
    def _enable_do_not_disturb():

        if not VegaModes._is_windows():
            return (
                "O modo Não Perturbe está "
                "configurado para Windows."
            )

        try:

            subprocess.Popen(
                [
                    "explorer.exe",
                    "ms-settings:notifications"
                ]
            )

            return (
                "Configurações de notificações abertas."
            )

        except Exception:

            return (
                "Não consegui acessar "
                "as configurações de notificações."
            )

    # ==========================================================
    # VÍDEIO AMBIENTE
    # ==========================================================

    @staticmethod
    def _play_ambient_video():

        # ------------------------------------------------------
        # LOCAL DA PASTA DE VÍDEOS
        # ------------------------------------------------------
        #
        # A pasta "ambiente" fica na mesma pasta
        # onde o projeto da Vega está sendo executado.
        #
        # Exemplo:
        #
        # Vega/
        # ├── core/
        # ├── config/
        # ├── ambiente/
        # │   ├── Mar.mp4
        # │   ├── runas.mp4
        # │   └── floresta.mp4
        #
        # ------------------------------------------------------

        base_dir = os.path.dirname(
            os.path.abspath(__file__)
        )

        project_dir = os.path.dirname(
            base_dir
        )

        ambient_dir = os.path.join(
            project_dir,
            "ambiente"
        )

        # ------------------------------------------------------
        # VERIFICAR SE A PASTA EXISTE
        # ------------------------------------------------------

        if not os.path.isdir(
            ambient_dir
        ):

            return (
                "A pasta de vídeos ambientes "
                "não foi encontrada.\n"
                f"Crie a pasta: {ambient_dir}"
            )

        # ------------------------------------------------------
        # ENCONTRAR VÍDEOS MP4
        # ------------------------------------------------------

        videos = []

        try:

            for filename in os.listdir(
                ambient_dir
            ):

                full_path = os.path.join(
                    ambient_dir,
                    filename
                )

                if not os.path.isfile(
                    full_path
                ):

                    continue

                if filename.lower().endswith(
                    ".mp4"
                ):

                    videos.append(
                        full_path
                    )

        except Exception as error:

            return (
                "Não consegui acessar "
                f"a pasta de vídeos: {error}"
            )

        # ------------------------------------------------------
        # NENHUM VÍDEO
        # ------------------------------------------------------

        if not videos:

            return (
                "Não encontrei nenhum vídeo "
                ".mp4 na pasta de ambiente."
            )

        # ------------------------------------------------------
        # ESCOLHER VÍDEO ALEATÓRIO
        # ------------------------------------------------------

        selected_video = random.choice(
            videos
        )

        # ------------------------------------------------------
        # ABRIR VÍDEO
        # ------------------------------------------------------

        try:

            if VegaModes._is_windows():

                os.startfile(
                    selected_video
                )

            else:

                subprocess.Popen(
                    [
                        "xdg-open",
                        selected_video
                    ]
                )

            video_name = os.path.basename(
                selected_video
            )

            return (
                f"Reproduzindo ambiente: "
                f"{video_name}"
            )

        except Exception as error:

            return (
                "Não consegui abrir o vídeo: "
                f"{error}"
            )

    # ==========================================================
    # ATIVAR MODO PELO NOME
    # ==========================================================

    @staticmethod
    def activate(mode_name, commands):

        if not mode_name:

            return (
                "Qual modo você quer ativar?"
            )

        mode = (
            mode_name
            .strip()
            .lower()
        )

        aliases = {

            "gamer": "gamer",
            "game": "gamer",
            "jogo": "gamer",
            "jogos": "gamer",

            "modo gamer": "gamer",
            "modo game": "gamer",
            "modo jogo": "gamer",
            "modo jogos": "gamer",

            "trabalho": "trabalho",
            "work": "trabalho",

            "modo trabalho": "trabalho",
            "modo work": "trabalho",

            "ambiente": "ambiente",
            "ambient": "ambiente",

            "modo ambiente": "ambiente",
            "modo ambient": "ambiente",
            "live": "live",
            "modo live": "live",
            "modo em tempo real": "live",
            "tempo real": "live",
        }

        mode = aliases.get(
            mode,
            mode
        )

        if mode == "gamer":

            return VegaModes.gamer(
                commands
            )

        if mode == "trabalho":

            return VegaModes.trabalho(
                commands
            )

        if mode == "ambiente":

            return VegaModes.ambiente(
                commands
            )

        if mode == "live":

            return VegaModes.live(
                commands
            )

        return (
            f"Não conheço o modo '{mode_name}'. "
            "Os modos disponíveis são: "
            "Gamer, Trabalho, Ambiente e Live."
        )


# ==============================================================
# MODOS DISPONÍVEIS
# ==============================================================

MODES = {

    "gamer": VegaModes.gamer,

    "trabalho": VegaModes.trabalho,

    "ambiente": VegaModes.ambiente,

}