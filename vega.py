import os
import io
import sys
import re
import time
import threading
import unicodedata
import random
import xml.etree.ElementTree as ET
import concurrent.futures
import queue
from datetime import datetime

import wave

import numpy as np
import miniaudio
import sounddevice as sd
import requests
from PIL import Image

from PySide6.QtCore import (
    QMetaObject,
    Qt,
    Q_ARG
)

from PySide6.QtWidgets import QApplication

from config.interface import InterfaceVEGA
from core.brain.brain import VegaBrain
from core.actions.homework import VegaHomework
from core.actions.modes import VegaModes
from core.memory.memory import VegaMemory
from core.audio.voice import VegaVoice
from core.audio.speaker import VegaSpeaker
from core.vision.camera import VegaCamera
from core.actions.commands import VegaCommands
from database.viewer import DatabaseViewer


# ==============================================================
# DETECTOR DE PALMA DUPLA
# ==============================================================

class ClapDetector:

    def __init__(
        self,
        threshold=0.08,
        min_interval=0.10,
        max_interval=0.65,
        on_double_clap_callback=None
    ):

        self.threshold = threshold
        self.min_interval = min_interval
        self.max_interval = max_interval

        self.on_double_clap = (
            on_double_clap_callback
        )

        self.last_clap_time = 0.0

        self.is_running = False

        self._stream = None

    def _audio_callback(
        self,
        indata,
        frames,
        time_info,
        status
    ):

        if not self.is_running:
            return

        if len(indata) == 0:
            return

        amplitude = float(
            np.max(
                np.abs(indata)
            )
        )

        if amplitude <= self.threshold:
            return

        current_time = time.time()

        elapsed = (
            current_time
            - self.last_clap_time
        )

        if (
            self.min_interval
            <= elapsed
            <= self.max_interval
        ):

            self.last_clap_time = 0.0

            if self.on_double_clap:

                print(
                    "[ClapDetector] "
                    "Palma dupla detectada."
                )

                threading.Thread(
                    target=self.on_double_clap,
                    daemon=True
                ).start()

        else:

            self.last_clap_time = (
                current_time
            )

    def start(
        self,
        device_index=None
    ):

        if self.is_running:
            return

        try:

            self._stream = sd.InputStream(

                device=device_index,

                channels=1,

                samplerate=44100,

                blocksize=1024,

                callback=self._audio_callback
            )

            self._stream.start()

            self.is_running = True

            print(
                "[ClapDetector] "
                "Monitorando palmas duplas..."
            )

        except Exception as error:

            self.is_running = False

            self._stream = None

            print(
                "[ClapDetector] "
                f"Erro ao iniciar microfone: {error}"
            )

    def stop(self):

        self.is_running = False

        self.last_clap_time = 0.0

        if self._stream:

            try:
                self._stream.stop()
            except Exception:
                pass

            try:
                self._stream.close()
            except Exception:
                pass

            self._stream = None


# ==============================================================
# BACKGROUND MUSIC
# ==============================================================

BACKGROUND_MUSIC_PATH = os.getenv(
    "VEGA_BACKGROUND_MUSIC",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Uploads",
        "nucleo_startup.mp3",
    ),
)
_background_music_stop = threading.Event()
_background_music_thread = None
_background_music_lock = threading.Lock()
_database_viewer = None


def open_database_viewer(source="auto"):
    global _database_viewer
    source = str(source or "auto").lower()
    if source not in {"json", "mysql", "auto"}:
        source = "auto"

    if (
        _database_viewer is None
        or _database_viewer.reader.source != source
    ):
        if _database_viewer is not None:
            _database_viewer.close()
        _database_viewer = DatabaseViewer(
            os.path.dirname(os.path.abspath(__file__)),
            source,
        )
    _database_viewer.show()
    _database_viewer.raise_()
    _database_viewer.activateWindow()


def choose_random_background_music(folder=None):
    if folder is None:
        folder = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "Uploads",
        )

    if not os.path.isdir(folder):
        return BACKGROUND_MUSIC_PATH

    supported = (".mp3", ".wav", ".ogg", ".flac")
    candidates = []
    for name in os.listdir(folder):
        full_path = os.path.join(folder, name)
        if os.path.isfile(full_path) and full_path.lower().endswith(supported):
            candidates.append(full_path)

    if not candidates:
        return BACKGROUND_MUSIC_PATH

    return random.choice(candidates)


def _load_background_audio(path):
    lower = path.lower()

    if lower.endswith(".wav"):
        with wave.open(path, "rb") as wf:
            samplerate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            frames = wf.readframes(-1)

        if sampwidth == 1:
            dtype = np.uint8
            data = np.frombuffer(frames, dtype=dtype)
            if channels > 1:
                data = data.reshape(-1, channels)
            audio = data.astype(np.float32)
            audio = (audio - 128.0) / 128.0
        elif sampwidth == 2:
            dtype = np.int16
            data = np.frombuffer(frames, dtype=dtype)
            if channels > 1:
                data = data.reshape(-1, channels)
            audio = data.astype(np.float32) / 32768.0
        elif sampwidth == 3:
            raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
            data = (
                raw[:, 0].astype(np.int32)
                | (raw[:, 1].astype(np.int32) << 8)
                | (raw[:, 2].astype(np.int32) << 16)
            )
            negative = (data & 0x800000) != 0
            data[negative] -= 0x1000000
            if channels > 1:
                data = data.reshape(-1, channels)
            audio = data.astype(np.float32) / 2147483648.0
        elif sampwidth == 4:
            dtype = np.int32
            data = np.frombuffer(frames, dtype=dtype)
            if channels > 1:
                data = data.reshape(-1, channels)
            audio = data.astype(np.float32) / 2147483648.0
        else:
            raise RuntimeError(f"Formato WAV não suportado: {sampwidth} bytes")

        return audio.astype(np.float32), samplerate

    if lower.endswith((".mp3", ".ogg", ".flac")):
        decoded = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.FLOAT32)
        samples = np.asarray(decoded.samples, dtype=np.float32)
        if decoded.nchannels > 1:
            samples = samples.reshape((-1, decoded.nchannels))
        return samples, decoded.sample_rate

    raise RuntimeError(f"Formato de áudio não suportado: {path}")


def calculate_background_music_volume(base_volume, elapsed_seconds, fade_start_seconds=90.0, fade_duration=10.0):
    base_volume = float(base_volume or 0.0)
    elapsed_seconds = float(elapsed_seconds or 0.0)
    fade_duration = max(0.001, float(fade_duration or 0.0))
    if base_volume <= 0.0:
        return 0.0
    if elapsed_seconds < fade_start_seconds:
        return base_volume
    fade_progress = min(1.0, max(0.0, (elapsed_seconds - fade_start_seconds) / fade_duration))
    return max(0.0, base_volume * (1.0 - fade_progress))


def start_background_music(path=None, volume=0.20):
    global _background_music_thread, _background_music_stop

    if path is None:
        path = choose_random_background_music()

    if not path or not os.path.exists(path):
        return None

    with _background_music_lock:
        if _background_music_thread is not None and _background_music_thread.is_alive():
            return _background_music_stop

        stop_event = threading.Event()
        _background_music_stop = stop_event

    def _loop():
        stream = None
        try:
            audio, samplerate = _load_background_audio(path)
            if audio.size == 0:
                return

            audio = np.clip(audio * float(volume), -1.0, 1.0)

            stream = sd.OutputStream(
                samplerate=samplerate,
                channels=1 if audio.ndim == 1 else audio.shape[1],
                dtype="float32",
                blocksize=0,
                latency="high",
            )
            stream.start()
            cursor = 0
            start_time = time.monotonic()

            while not stop_event.is_set():
                elapsed = time.monotonic() - start_time
                current_volume = calculate_background_music_volume(volume, elapsed)

                remaining = audio[cursor:]
                if remaining.size == 0:
                    cursor = 0
                    remaining = audio

                chunk_size = min(4096, remaining.shape[0])
                chunk = remaining[:chunk_size].copy()
                if current_volume < volume:
                    chunk = np.clip(chunk * (current_volume / max(volume, 1e-9)), -1.0, 1.0)
                stream.write(chunk)
                cursor += chunk_size

                if cursor >= audio.shape[0]:
                    cursor = 0

                if elapsed >= 100.0:
                    break

                if stop_event.is_set():
                    break

        except Exception as error:
            print(f"\n[VEGA Música] Erro na música de fundo: {error}")
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

    with _background_music_lock:
        music_thread = threading.Thread(target=_loop, daemon=True)
        _background_music_thread = music_thread
        music_thread.start()
    return stop_event


def stop_background_music():
    global _background_music_stop, _background_music_thread
    with _background_music_lock:
        stop_event = _background_music_stop
        music_thread = _background_music_thread
        if stop_event is not None:
            stop_event.set()

    if music_thread is not None and music_thread is not threading.current_thread():
        music_thread.join()

    with _background_music_lock:
        if _background_music_thread is music_thread:
            _background_music_thread = None


def classify_news_confirmation(text):
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", str(text or "").lower())
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    negative_phrases = (
        "nao", "nao quero", "agora nao", "nao agora", "depois", "mais tarde",
    )
    if any(normalized == phrase or normalized.startswith(phrase + " ") for phrase in negative_phrases):
        return False

    positive_phrases = (
        "sim", "quero", "quero saber", "pode falar", "pode ler", "manda ai",
        "claro", "com certeza", "isso", "leia", "pode",
    )
    if any(normalized == phrase or normalized.startswith(phrase + " ") for phrase in positive_phrases):
        return True
    return None


def should_run_activation_update(memory=None):
    if memory is None:
        memory = VegaMemory()

    today = datetime.now().strftime("%Y-%m-%d")
    last_update = memory.recall("ultima atualizacao de noticias")
    if last_update is None:
        return True

    return str(last_update).strip() != today


def mark_activation_update_sent(memory=None):
    if memory is None:
        memory = VegaMemory()

    today = datetime.now().strftime("%Y-%m-%d")
    return memory.remember(
        "ultima atualizacao de noticias",
        today,
        category="sistema",
        importance=1.0,
        source="system",
    )


def normalize_news_title(title):
    cleaned = str(title or "").strip()
    cleaned = re.sub(r"\s*[-|–:]\s*(BBC|BBC News|BBC News Brasil|Reuters|G1)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(BBC|BBC News|BBC News Brasil|Reuters|G1)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = cleaned.strip(" -:|/")
    return cleaned.strip()


def fetch_startup_news(limit: int = 3):
    limit = max(1, int(limit))
    news_sources = [
        ("BBC", "https://feeds.bbci.co.uk/portuguese/rss.xml", 2),
        ("Reuters", "https://feeds.reuters.com/reuters/worldNews", 1),
        ("G1", "https://g1.globo.com/dynamo/rss2.xml", 1),
    ]

    def fetch_titles(source_name, url, max_items):
        try:
            response = requests.get(url, timeout=4, headers={"User-Agent": "VEGA/1.0"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            items = []
            for item in root.findall("./channel/item")[:max_items]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                cleaned = normalize_news_title(title)
                if cleaned:
                    items.append({
                        "source": source_name,
                        "title": cleaned,
                        "link": link,
                    })
            return items
        except Exception:
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_titles, source_name, url, max_items) for source_name, url, max_items in news_sources]
        items = []
        for future in futures:
            items.extend(future.result())

    ordered = []
    by_source = {}
    for item in items:
        source_name = str(item.get("source") or "")
        by_source.setdefault(source_name, []).append(item)

    for source_name, _, _ in news_sources:
        if by_source.get(source_name):
            ordered.append(by_source[source_name][0])

    for item in items:
        if item not in ordered:
            ordered.append(item)

    unique = []
    seen = set()
    for item in ordered:
        key = (item.get("source"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:limit]


def build_startup_news_briefing(limit: int = 3, items=None):
    selected = (
        fetch_startup_news(limit=max(1, int(limit)))
        if items is None
        else list(items)[:max(1, int(limit))]
    )

    if not selected:
        return "Olá, Endriw. Não consegui atualizar as manchetes agora, mas estou pronta para ouvir você."

    intro = random.choice([
        "Olá, Endriw. Vou te atualizar com as principais notícias do Brasil e do mundo.",
        "Olá, Endriw. Aqui vai um resumo rápido do que está em foco agora.",
        "Olá, Endriw. Deixa eu te passar o resumo mais importante de hoje."
    ])

    headline_parts = []
    for item in selected[:3]:
        source_name = str(item.get("source") or "")
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if source_name == "BBC":
            headline_parts.append(f"A BBC informa: {title}.")
        elif source_name == "Reuters":
            headline_parts.append(f"A Reuters informa: {title}.")
        elif source_name == "G1":
            headline_parts.append(f"O G1 informa: {title}.")
        else:
            headline_parts.append(f"{source_name}: {title}.")

    if not headline_parts:
        return f"{intro} Não consegui encontrar manchetes relevantes agora."

    return f"{intro} {' '.join(headline_parts[:3])}"


def build_activation_update():
    return build_startup_news_briefing(limit=3)

# ==============================================================
# BANNER
# ==============================================================

def print_banner():

    print()

    print("=" * 50)

    print(
        "                    VEGA"
    )

    print(
        "             Assistente Pessoal"
    )

    print("=" * 50)

    print()

    print(
        "Carregando inteligência..."
    )

    print()


# ==============================================================
# EXTRAIR FRASES
# ==============================================================

def extract_sentences(buffer):

    sentences = []

    pattern = (
        r"(.+?[.!?]+(?:\s+|$)|.+?\n+)"
    )

    while True:

        match = re.match(
            pattern,
            buffer,
            flags=re.DOTALL
        )

        if not match:
            break

        sentence = (
            match.group(1)
            .strip()
        )

        if sentence:

            sentences.append(
                sentence
            )

        buffer = buffer[
            match.end():
        ]

    return sentences, buffer


# ==============================================================
# MAIN
# ==============================================================

def format_vega_status_message(text):
    if text is None:
        return ""

    value = str(text).strip()
    if not value:
        return ""

    value = re.sub(r"^\[VEGA\]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\[Vega\]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^VEGA:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^Vega:\s*", "", value, flags=re.IGNORECASE)

    if re.search(r"[A-Za-z]:\\", value):
        lowered = value.lower()
        if "documents" in lowered or "documentos" in lowered:
            value = re.sub(r"[A-Za-z]:\\[^\n]*?(?:Documents|Documentos)", "dentro da pasta de documentos", value, flags=re.IGNORECASE)
        elif "downloads" in lowered:
            value = re.sub(r"[A-Za-z]:\\[^\n]*?(?:Downloads|Downloads)", "dentro da pasta de downloads", value, flags=re.IGNORECASE)
        elif "desktop" in lowered or "área de trabalho" in lowered or "area de trabalho" in lowered:
            value = re.sub(r"[A-Za-z]:\\[^\n]*?(?:Desktop|Área de Trabalho|Area de Trabalho)", "na área de trabalho", value, flags=re.IGNORECASE)
        else:
            value = "dentro da pasta do sistema"

    value = value.replace("\\", "/")
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    return f"VEGA: {value}"


def emit_vega_status(text):
    if not text:
        return
    message = format_vega_status_message(text)
    if not message:
        return

    try:
        if "vega_ui" in globals() and vega_ui is not None:
            QMetaObject.invokeMethod(
                vega_ui,
                "push_status_message",
                Qt.QueuedConnection,
                Q_ARG(str, message),
            )
    except Exception:
        pass

    lowered = message.lower()
    if (
        "exercício" in lowered or "exercicio" in lowered or "lista" in lowered
        or "faltam" in lowered or "terminei" in lowered or "resolvido" in lowered
    ) and random.random() < 0.35:
        try:
            speaker_fn = globals().get("vega_status_speaker")
            if callable(speaker_fn):
                clean_text = message.replace("VEGA:", "", 1).strip()
                if clean_text:
                    speaker_fn(clean_text)
        except Exception:
            pass


def main():
    global vega_ui
    global project_ui

    vega_ui = None
    project_ui = None
    text_only_mode = (
        "--text" in sys.argv
        or os.getenv("VEGA_START_ACTIVE", "").lower() in {"1", "true", "yes"}
    )

    app = QApplication(
        sys.argv
    )

    print_banner()

    # ==========================================================
    # INICIALIZAÇÃO
    # ==========================================================

    try:

        print("[Inicialização] Preparando cérebro...", flush=True)
        brain = VegaBrain()

        print("[Inicialização] Preparando microfone...", flush=True)
        voice = VegaVoice(
            microphone_index=1
        )
        voice.enabled = not text_only_mode

        print("[Inicialização] Preparando voz...", flush=True)
        speaker = VegaSpeaker()

        print("[Inicialização] Preparando interface...", flush=True)
        vega_ui = InterfaceVEGA()
        brain.tool_result_callback = vega_ui.tool_result_ready.emit
        vega_ui.brain_view_requested.connect(open_database_viewer)
        brain.brain_view_callback = vega_ui.brain_view_requested.emit
        brain.database_view_callback = vega_ui.brain_view_requested.emit
        brain.holographic_command_callback = vega_ui.executar_comando_holografico
        # App aberto por voz já nasce SELECIONADO no ambiente holográfico.
        brain.app_opened_callback = vega_ui.selecionar_app_por_voz
        VegaCommands.register_database_callback(open_database_viewer)
        VegaHomework.status_callback = emit_vega_status

        # A sessão de projeto reutiliza a mesma janela do núcleo da Vega.
        project_ui = vega_ui

        # ------------------------------------------------------
        # VISÃO
        # ------------------------------------------------------

        print("[Inicialização] Preparando câmera...", flush=True)
        camera = VegaCamera(
            camera_index=1,
            width=1366,
            height=768
        )

        project_ui.conectar_camera(
            camera
        )

        print("[Inicialização] Componentes prontos.", flush=True)

    except Exception as error:

        print(
            "Vega: Falha ao inicializar:"
        )

        print(error)

        return

    # ==========================================================
    # ESTADO
    # ==========================================================

    state_lock = threading.Lock()

    session_active = False

    speaking = False
    response_cancelled = threading.Event()
    barge_in_inputs = queue.Queue()
    microphone_lock = threading.Lock()
    news_prompt_active = threading.Event()
    awaiting_news_confirmation = threading.Event()
    news_items_lock = threading.Lock()
    pending_news_items = []

    shutting_down = False

    clap_detector = None

    camera_active = False

    activation_count = 0

    # ==========================================================
    # SESSION ACTIVE
    # ==========================================================

    def set_session_active(value):

        nonlocal session_active

        with state_lock:

            session_active = value

    def is_session_active():

        with state_lock:

            return session_active

    # ==========================================================
    # SPEAKING
    # ==========================================================

    def set_speaking(value):

        nonlocal speaking

        with state_lock:

            speaking = value

        try:

            QMetaObject.invokeMethod(
                vega_ui,
                "set_speaking",
                Qt.QueuedConnection,
                Q_ARG(bool, bool(value))
            )

        except Exception:

            pass

    def is_speaking():

        with state_lock:

            return speaking

    def interrupt_current_response():
        if not speaker.is_speaking or response_cancelled.is_set():
            return False
        response_cancelled.set()
        speaker.interrupt()
        brain.cancel_current_response()
        atualizar_audio(0.0)
        set_speaking(False)
        return True

    # ==========================================================
    # AUDIO DA INTERFACE
    # ==========================================================

    def atualizar_audio(nivel):

        try:

            QMetaObject.invokeMethod(

                vega_ui,

                "atualizar_audio",

                Qt.QueuedConnection,

                Q_ARG(
                    float,
                    float(nivel)
                )
            )

        except Exception:

            pass

    def atualizar_processamento(active):

        try:

            QMetaObject.invokeMethod(

                vega_ui,

                "set_processing",

                Qt.QueuedConnection,

                Q_ARG(bool, bool(active))
            )

        except Exception:

            pass

    # ==========================================================
    # FALAR
    # ==========================================================

    def falar(text):

        if not text:
            return

        text = str(
            text
        ).strip()

        if not text:
            return

        try:
            atualizar_audio(
                0.6
            )

            speaker.speak(
                text
            )

        except Exception as error:

            print(
                "\n[VegaSpeaker] "
                f"Erro: {error}"
            )

    globals()["vega_status_speaker"] = falar

    def notify_scheduled_item(item):
        reminder_text = str(item.get("text") or "").strip()
        if reminder_text:
            falar(f"Lembrete: {reminder_text}")

    brain.scheduler_notification_callback = notify_scheduled_item

    # ==========================================================
    # ENVIAR COMANDO PARA INTERFACE
    # ==========================================================

    def enviar_comando_interface(comando):

        if not comando:
            return False

        try:

            return bool(
                QMetaObject.invokeMethod(

                    project_ui,

                    "executar_comando",

                    Qt.QueuedConnection,

                    Q_ARG(
                        str,
                        str(comando)
                    )
                )
            )

        except Exception as error:

            print(
                "[VEGA] "
                f"Erro enviando comando para interface: "
                f"{error}"
            )

            return False

    # ==========================================================
    # ABRIR CÂMERA
    # ==========================================================

    def start_camera(show_camera=False):

        nonlocal camera_active

        if camera_active:
            return

        camera_active = True

        print(
            "[VEGA Camera] "
            "Ativando câmera..."
        )

        try:

            camera.start(
                show_camera=show_camera
            )

            print(
                "[VEGA Camera] "
                "Câmera ativada."
            )

        except Exception as error:

            camera_active = False

            print(
                "[VEGA Camera] "
                f"Erro ao ativar câmera: {error}"
            )

    def open_holographic_environment():
        """Open the VEGA interface without activating camera capture."""
        enviar_comando_interface("abrir ambiente holográfico")

    brain.environment_callback = open_holographic_environment

    # ==========================================================
    # FECHAR CÂMERA
    # ==========================================================

    def close_camera():

        nonlocal camera_active

        if not camera_active:
            return

        camera_active = False

        print(
            "[VEGA Camera] "
            "Encerrando câmera..."
        )

        try:

            camera.stop()

        except Exception as error:

            print(
                "[VEGA Camera] "
                f"Erro ao parar câmera: {error}"
            )

        print(
            "[VEGA Camera] "
            "Câmera encerrada."
        )

    def capture_camera_frame():
        started_for_request = not camera_active
        if started_for_request:
            start_camera()

        try:
            deadline = time.monotonic() + 3.0
            frame = camera.latest_frame
            while frame is None and time.monotonic() < deadline:
                if not camera.is_running:
                    break
                time.sleep(0.05)
                frame = camera.latest_frame

            if frame is None:
                return None

            return Image.fromarray(frame[:, :, ::-1].copy())
        finally:
            if started_for_request:
                close_camera()

    brain.camera_frame_callback = capture_camera_frame

    def alternar_camera_interface(active):
        if active:
            start_camera()
        else:
            close_camera()
        vega_ui.set_camera_state(active)

    def alternar_microfone_interface(active):
        voice.enabled = bool(active)
        if not active:
            try:
                voice.stop()
            except Exception:
                pass
        vega_ui.set_microphone_state(active)

    def _qimage_to_pil(image):
        if image is None or image.isNull():
            return None
        from PySide6.QtCore import QBuffer, QIODevice

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")

    def processar_texto_interface(texto, imagem=None):
        if not texto:
            return

        def worker():
            response_cancelled.clear()
            set_speaking(True)
            atualizar_processamento(True)
            full_response = ""
            speech_buffer = ""
            spoke_sentences = False
            try:
                print(f"Você: {texto}")
                print("Vega: ", end="", flush=True)
                image = _qimage_to_pil(imagem) if imagem is not None else None
                for chunk in brain.process_stream(str(texto), image=image):
                    if response_cancelled.is_set():
                        break
                    if chunk is None:
                        break
                    chunk_text = str(chunk) if not isinstance(chunk, str) else chunk
                    if chunk_text.strip().lower() == "estou ouvindo.":
                        continue
                    emit_vega_status(chunk_text)
                    print(chunk_text, end="", flush=True)
                    full_response += chunk_text
                    speech_buffer += chunk_text
                    sentences, speech_buffer = extract_sentences(speech_buffer)
                    for sentence in sentences:
                        falar(sentence)
                        spoke_sentences = True
                print()
                remaining_text = speech_buffer.strip()
                if not response_cancelled.is_set():
                    if remaining_text:
                        falar(remaining_text)
                    elif full_response.strip() and not spoke_sentences:
                        falar(full_response.strip())
                if not response_cancelled.is_set() and speaker.is_speaking:
                    speaker.wait_until_done()
            except Exception as error:
                print(f"\nVega: Erro processando texto: {error}")
            finally:
                atualizar_processamento(False)
                atualizar_audio(0.0)
                set_speaking(False)

        threading.Thread(
            target=worker,
            name="VegaTextInput",
            daemon=True,
        ).start()

    vega_ui.message_submitted.connect(processar_texto_interface)
    vega_ui.camera_toggled.connect(alternar_camera_interface)
    vega_ui.microphone_toggled.connect(alternar_microfone_interface)
    vega_ui.set_microphone_state(voice.enabled)

    # ==========================================================
    # ENCERRAR SESSÃO
    # ==========================================================

    def _speak_activation_update():
        nonlocal pending_news_items
        if not is_session_active():
            return

        items = fetch_startup_news(limit=3)
        with news_items_lock:
            pending_news_items = items

        try:
            question = "Oi, Endriw. Aqui é a VEGA. Quer que eu leia o resumo das três principais notícias?"
            print(f"Vega: {question}")
            emit_vega_status(question)
            news_prompt_active.set()
            set_speaking(True)
            falar(question)
            speaker.wait_until_done(timeout_seconds=30.0)
        except Exception:
            pass
        finally:
            news_prompt_active.clear()

        vega_ui.tool_result_ready.emit("news", {"items": items, "startup": True})
        awaiting_news_confirmation.set()

        mark_activation_update_sent()
        atualizar_audio(0.0)
        set_speaking(False)

    def encerrar_sessao():

        stop_background_music()

        set_session_active(
            False
        )

        # ------------------------------------------------------
        # ESPERAR VOZ TERMINAR
        # ------------------------------------------------------

        try:

            speaker.wait_until_done()

        except Exception:

            pass

        atualizar_audio(
            0.0
        )

        set_speaking(
            False
        )

        # ------------------------------------------------------
        # FECHAR VISION
        # ------------------------------------------------------

        close_camera()

        # ------------------------------------------------------
        # ESCONDER INTERFACE
        # ------------------------------------------------------

        try:

            QMetaObject.invokeMethod(

                vega_ui,

                "hide",

                Qt.QueuedConnection
            )

        except Exception:

            pass

        try:

            QMetaObject.invokeMethod(

                project_ui,

                "hide",

                Qt.QueuedConnection
            )

        except Exception:

            pass

        # ------------------------------------------------------
        # REATIVAR PALMA
        # ------------------------------------------------------

        if clap_detector:

            clap_detector.start(
                device_index=voice.microphone_index
            )

    # ==========================================================
    # LOOP DE ESCUTA
    # ==========================================================

    def loop_escuta():

        while not shutting_down:

            if VegaModes.is_live_active():
                time.sleep(0.05)
                continue

            if not voice.enabled:
                time.sleep(0.05)
                continue

            # --------------------------------------------------
            # ESPERAR VEGA SER DESPERTADA
            # --------------------------------------------------

            if not is_session_active():

                time.sleep(
                    0.04
                )

                continue

            if news_prompt_active.is_set():
                time.sleep(0.03)
                continue

            # --------------------------------------------------
            # NÃO OUVIR ENQUANTO FALA
            # --------------------------------------------------

            # --------------------------------------------------
            # OUVIR
            # --------------------------------------------------

            try:
                try:
                    user_input = barge_in_inputs.get_nowait()
                except queue.Empty:
                    if not microphone_lock.acquire(blocking=False):
                        time.sleep(0.02)
                        continue
                    try:
                        user_input = voice.listen(
                            level_callback=lambda level: atualizar_audio(
                                min(1.0, max(0.0, level / 240.0))
                            ),
                            on_speech_start=interrupt_current_response,
                            barge_in=True,
                            output_active=lambda: speaker.is_speaking,
                            stop_waiting=lambda: news_prompt_active.is_set(),
                        )
                    finally:
                        microphone_lock.release()

                if user_input and speaker.is_speaking:
                    interrupt_current_response()

            except Exception as error:

                print(
                    "\nVega: "
                    f"Erro no microfone: "
                    f"{error}\n"
                )

                time.sleep(
                    0.3
                )

                continue

            # --------------------------------------------------
            # SESSÃO FECHADA
            # --------------------------------------------------

            if not is_session_active():

                continue

            if news_prompt_active.is_set():
                continue

            # --------------------------------------------------
            # NADA ENTENDIDO
            # --------------------------------------------------

            if not user_input:
                time.sleep(0.2)
                continue

            if awaiting_news_confirmation.is_set():
                confirmation = classify_news_confirmation(user_input)
                if confirmation is not None:
                    awaiting_news_confirmation.clear()
                    if confirmation:
                        with news_items_lock:
                            items = list(pending_news_items)
                        response = build_startup_news_briefing(limit=3, items=items)
                    else:
                        response = "Tudo bem. Não vou ler as notícias agora."
                        vega_ui.tool_result_cleared.emit()

                    emit_vega_status(response)
                    set_speaking(True)
                    news_prompt_active.set()
                    try:
                        falar(response)
                        speaker.wait_until_done(timeout_seconds=45.0)
                    except Exception:
                        pass
                    finally:
                        news_prompt_active.clear()
                    atualizar_audio(0.0)
                    set_speaking(False)
                    continue

            response_cancelled.clear()

            print(
                f"Você: {user_input}"
            )

            user_clean = (
                user_input
                .lower()
                .strip()
            )

            user_plain = "".join(
                char
                for char in unicodedata.normalize(
                    "NFD",
                    user_clean
                )
                if unicodedata.category(char) != "Mn"
            )

            command_words = set(
                re.findall(
                    r"\b\w+\b",
                    user_plain
                )
            )

            camera_start_requested = (
                "camera" in command_words
                and bool(
                    command_words
                    & {
                        "ativar",
                        "ativa",
                        "ligar",
                        "liga",
                        "abrir",
                        "abre",
                    }
                )
            )

            camera_stop_requested = (
                "camera" in command_words
                and bool(
                    command_words
                    & {
                        "desativar",
                        "desativa",
                        "desligar",
                        "desliga",
                        "fechar",
                        "fecha",
                    }
                )
            )

            camera_preview_requested = (
                "camera" in command_words
                and bool(
                    command_words
                    & {
                        "mostrar",
                        "mostre",
                        "exibir",
                        "exibe",
                        "ver",
                    }
                )
            )

            project_open_requested = any(
                phrase in user_plain
                for phrase in (
                    "abrir projeto novo",
                    "abrir novo projeto",
                    "abrir projeto",
                    "abrir o projeto",
                    "abra projeto",
                    "abra o projeto",
                    "novo projeto",
                    "criar projeto",
                    "criar o projeto",
                    "projeto novo",
                    "abrir ambiente holografico",
                    "abrir o ambiente holografico",
                    "iniciar ambiente holografico",
                )
            )

            # ==================================================
            # SAIR
            # ==================================================

            if user_clean in {

                "sair",
                "exit",
                "quit"

            }:

                print(
                    "Vega: Até mais!"
                )

                set_speaking(
                    True
                )

                falar(
                    "Até mais, Endriw."
                )

                try:

                    speaker.wait_until_done()

                except Exception:

                    pass

                atualizar_audio(
                    0.0
                )

                set_speaking(
                    False
                )

                encerrar_sessao()

                continue

            # ==================================================
            # COMANDOS VISUAIS
            # ==================================================

            interface_handled = False

            try:

                visual_commands = (

                    "abrir projeto novo",

                    "abrir novo projeto",

                    "abrir projeto",

                    "abrir o projeto",

                    "abra projeto",

                    "abra o projeto",

                    "novo projeto",

                    "criar projeto",

                    "projeto novo",

                    "abrir ambiente holografico",

                    "abrir o ambiente holografico",

                    "iniciar ambiente holografico",

                    "fechar interface",

                    "fechar projeto",

                    "fechar holograma",

                    "ativar camera",

                    "ligar camera",

                    "abrir camera",

                    "mostrar camera",

                    "mostre camera",

                    "desativar camera",

                    "desligar camera",

                    "fechar camera"

                )

                if any(
                    command in user_plain
                    for command in visual_commands
                ) or project_open_requested or (
                    camera_start_requested
                    or camera_stop_requested
                    or camera_preview_requested
                ):

                    interface_handled = True

                    enviar_comando_interface(
                        user_input
                    )

            except Exception:

                pass

            # ==================================================
            # COMANDO VISUAL
            # ==================================================

            if interface_handled:

                resposta = None

                # --------------------------------------------------
                # NOVO PROJETO
                # --------------------------------------------------

                if project_open_requested:

                    resposta = (
                        "Ambiente holográfico "
                        "inicializado."
                    )

                # --------------------------------------------------
                # CAMERA
                # --------------------------------------------------

                elif camera_preview_requested:

                    if camera_active:

                        close_camera()

                    start_camera(
                        show_camera=True
                    )

                    resposta = "Mostrando a câmera."

                elif camera_start_requested:

                    start_camera()

                    resposta = (
                        "Câmera ativada para captura de imagem."
                    )

                elif camera_stop_requested:

                    close_camera()

                    resposta = "C\u00e2mera desativada."

                # --------------------------------------------------
                # FECHAR
                # --------------------------------------------------

                elif (
                    "fechar interface"
                    in user_clean
                    or "fechar projeto"
                    in user_clean
                    or "fechar holograma"
                    in user_clean
                ):

                    resposta = (
                        "Interface holográfica "
                        "encerrada."
                    )

                    close_camera()

                # --------------------------------------------------
                # FALAR
                # --------------------------------------------------

                if resposta:

                    print(
                        f"Vega: {resposta}"
                    )
                    emit_vega_status(resposta)

                    set_speaking(
                        True
                    )

                    falar(
                        resposta
                    )

                    try:

                        if speaker.is_speaking:
                            speaker.wait_until_done()

                    except Exception:

                        pass

                    atualizar_audio(
                        0.0
                    )

                    set_speaking(
                        False
                    )

                continue

            # ==================================================
            # PROCESSAMENTO DO CÉREBRO
            # ==================================================

            print(
                "Vega: ",
                end="",
                flush=True
            )

            full_response = ""
            speech_buffer = ""
            spoke_sentences = False

            response_cancelled.clear()
            set_speaking(
                True
            )

            atualizar_processamento(True)

            try:

                for chunk in brain.process_stream(
                    user_input
                ):

                    if response_cancelled.is_set():
                        break

                    if chunk is None:

                        print()

                        falar(
                            "Até mais, Endriw."
                        )

                        break

                    chunk_text = str(chunk) if not isinstance(chunk, str) else chunk

                    if chunk_text.strip().lower() == "estou ouvindo.":
                        continue

                    print(
                        chunk_text,
                        end="",
                        flush=True
                    )
                    emit_vega_status(chunk_text)

                    full_response += chunk_text
                    speech_buffer += chunk_text

                    sentences, speech_buffer = extract_sentences(speech_buffer)
                    for sentence in sentences:
                        falar(sentence)
                        spoke_sentences = True

                else:
                    final_text = full_response.strip()
                    remaining_text = speech_buffer.strip()
                    if remaining_text:
                        falar(remaining_text)
                    elif final_text and not spoke_sentences:
                        falar(final_text)

                print()
                print()

            except Exception as error:

                print(
                    "\nVega: "
                    "Erro durante processamento: "
                    f"{error}\n"
                )

            atualizar_processamento(False)

            if response_cancelled.is_set():
                atualizar_audio(0.0)
                set_speaking(False)
                continue

            # ==================================================
            # ESPERAR ÁUDIO
            # ==================================================

            try:

                if speaker.is_speaking:
                    speaker.wait_until_done()

            except Exception as error:

                print(
                    "\n[VegaSpeaker] "
                    "Erro aguardando áudio: "
                    f"{error}"
                )

            atualizar_audio(
                0.0
            )

            set_speaking(
                False
            )

            time.sleep(
                0.15
            )

    # ==========================================================
    # PALMA DUPLA
    # ==========================================================

    def ao_detectar_palma_dupla():

        nonlocal activation_count

        if is_session_active():
            return

        # ------------------------------------------------------
        # PARAR PALMAS ENQUANTO VEGA ESTÁ ATIVA
        # ------------------------------------------------------

        if clap_detector:
            clap_detector.stop()

        # ------------------------------------------------------
        # ATIVAR SESSÃO E SOM DE FUNDO SILENCIOSO
        # ------------------------------------------------------

        set_session_active(True)
        news_prompt_active.clear()
        awaiting_news_confirmation.clear()
        start_background_music(path=choose_random_background_music(), volume=0.08)

        activation_count += 1

        try:
            QMetaObject.invokeMethod(
                vega_ui,
                "despertar",
                Qt.QueuedConnection
            )
        except Exception as error:
            print(
                "[VEGA] "
                f"Erro ao despertar interface: {error}"
            )

        threading.Thread(
            target=_speak_activation_update,
            daemon=True,
        ).start()

        time.sleep(0.2)

    # ==========================================================
    # CRIAR DETECTOR
    # ==========================================================

    clap_detector = ClapDetector(

        on_double_clap_callback=(
            ao_detectar_palma_dupla
        )
    )

    # ==========================================================
    # INICIAR PALMA
    # ==========================================================

    if not text_only_mode:
        clap_detector.start(
            device_index=voice.microphone_index
        )

    # Em computadores sem microfone/câmera, a interface pode iniciar ativa
    # por argumento ou variável de ambiente e aceitar apenas texto/imagens.
    if text_only_mode:
        ao_detectar_palma_dupla()

    # ==========================================================
    # THREAD DE ESCUTA
    # ==========================================================

    threading.Thread(

        target=loop_escuta,

        daemon=True

    ).start()

    def loop_interrupcao_fala():
        while not shutting_down:
            if (
                not voice.enabled
                or not is_session_active()
                or news_prompt_active.is_set()
                or not speaker.is_speaking
                or not microphone_lock.acquire(blocking=False)
            ):
                time.sleep(0.03)
                continue

            user_input = None
            try:
                if speaker.is_speaking:
                    user_input = voice.listen(
                        level_callback=lambda level: atualizar_audio(
                            min(1.0, max(0.0, level / 240.0))
                        ),
                        on_speech_start=interrupt_current_response,
                        barge_in=True,
                        output_active=lambda: speaker.is_speaking,
                        stop_waiting=lambda: (
                            news_prompt_active.is_set()
                            or not speaker.is_speaking
                        ),
                    )
                if user_input and response_cancelled.is_set():
                    barge_in_inputs.put(user_input)
            except Exception as error:
                print(f"[VEGA] Erro no monitor de interrupção: {error}")
            finally:
                microphone_lock.release()

    threading.Thread(
        target=loop_interrupcao_fala,
        name="VegaBargeInMonitor",
        daemon=True,
    ).start()

    # ==========================================================
    # MENSAGEM INICIAL
    # ==========================================================

    print()

    print(
        "VEGA pronta! "
        "Use uma palma dupla ou inicie com --text "
        "para abrir a interface."
    )

    print()

    # ==========================================================
    # EVENT LOOP QT
    # ==========================================================

    try:

        sys.exit(
            app.exec()
        )

    except KeyboardInterrupt:

        pass

    finally:

        shutting_down = True

        if clap_detector:
            clap_detector.stop()

        # ------------------------------------------------------
        # PARAR CÂMERA
        # ------------------------------------------------------

        try:
            close_camera()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        try:
            speaker.stop()
        except Exception:
            pass


# ==============================================================
# EXECUÇÃO
# ==============================================================

if __name__ == "__main__":

    main()
