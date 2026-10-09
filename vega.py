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
from core.orchestration import ConversationState, SESSION_METRICS
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
    """Classifica a resposta à oferta de ler as notícias.

    ANTES só reconhecia a confirmação quando ela era a PRIMEIRA palavra da
    fala. "oi vega, quero sim" não casava com nenhuma frase positiva (começa
    com "oi") e a resposta caía no fluxo normal — a VEGA ignorava o "sim" e
    respondia "o que a gente vai resolver agora?".

    Agora a confirmação é procurada em qualquer posição da fala, avaliando
    primeiro a parte final (onde a resposta real costuma estar) e respeitando
    negação explícita.

    Devolve True (confirmou), False (recusou) ou None (não é uma resposta a
    esta pergunta — o turno segue para o fluxo normal).
    """
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", str(text or "").lower())
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None

    # Vocativos e muletas de fala que podem vir antes da resposta de verdade.
    normalized = re.sub(
        r"^(?:oi|ola|opa|ei|hey|vega|por favor|pfv|bom|bem|entao|ah|hmm)+\s+",
        "",
        normalized,
    ).strip()
    if not normalized:
        return None

    # Negação explícita tem prioridade sobre qualquer termo positivo.
    negative_phrases = (
        "nao quero", "nao quero saber", "agora nao", "nao agora",
        "nao precisa", "nao obrigado", "deixa pra la", "deixa quieto",
        "mais tarde", "depois", "nao", "deixa", "esquece",
    )
    positive_phrases = (
        "sim", "quero", "quero saber", "quero sim", "pode falar", "pode ler",
        "manda ai", "manda ver", "claro", "com certeza", "isso", "isso mesmo",
        "leia", "le", "pode", "bora", "vai", "ok", "beleza",
    )

    # Busca por palavra inteira (não por prefixo): "sim" dentro de "simulado"
    # não deve confirmar nada.
    def _has(phrase: str) -> bool:
        return bool(re.search(rf"\b{re.escape(phrase)}\b", normalized))

    # Negação composta ("não quero", "não obrigado") contém um termo positivo
    # ("quero") como substring. Se o usuário negou com uma dessas formas, a
    # resposta é NÃO — sem depender de posição.
    strong_negatives = (
        "nao quero", "nao quero saber", "nao precisa", "nao obrigado",
        "nao agora", "agora nao", "deixa pra la", "deixa quieto",
    )
    if any(_has(phrase) for phrase in strong_negatives):
        return False

    has_negative = any(_has(phrase) for phrase in negative_phrases)
    has_positive = any(_has(phrase) for phrase in positive_phrases)

    if has_negative and not has_positive:
        return False
    if has_positive and not has_negative:
        return True
    if not has_positive and not has_negative:
        return None

    # Ambos presentes sem negação composta ("sim, mas não agora"): a última
    # intenção dita vence, porque reflete a decisão final do usuário.
    last_negative = max(
        (normalized.rfind(phrase) for phrase in negative_phrases if _has(phrase)),
        default=-1,
    )
    last_positive = max(
        (normalized.rfind(phrase) for phrase in positive_phrases if _has(phrase)),
        default=-1,
    )
    return last_positive > last_negative


def should_run_activation_update(memory=None):
    """Verdadeiro quando a atualização de notícias ainda não foi enviada hoje."""
    if memory is None:
        memory = VegaMemory()

    today = datetime.now().strftime("%Y-%m-%d")
    last_update = memory.recall("ultima atualizacao de noticias")
    if last_update is None:
        return True

    return str(last_update).strip() != today


# A resposta de ativação já termina em "Olá, Endriw." — repetir o prefixo
# fazia a VEGA dizer o próprio nome duas vezes na mesma fala.
_ACTIVATION_UPDATE_PREFIXES = ("VEGA: ", "Vega: ", "[VEGA] ", "[Vega] ")


def strip_activation_update_prefix(text):
    value = str(text or "").strip()
    for prefix in _ACTIVATION_UPDATE_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):].lstrip()
    return value


def mark_activation_update_sent(memory=None, text=None):
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
            microphone_index=None if text_only_mode else 1
        )
        voice.enabled = not text_only_mode

        print("[Inicialização] Preparando voz...", flush=True)
        speaker = VegaSpeaker()

        print("[Inicialização] Preparando interface...", flush=True)
        vega_ui = InterfaceVEGA()
        brain.tool_result_callback = vega_ui.tool_result_ready.emit
        # Canvas de desenho ao vivo (Fase 3): via SINAL, thread-safe como os
        # demais (o callback roda na thread do cérebro; o slot roda na UI).
        brain.canvas_callback = vega_ui.canvas_draw_requested.emit
        vega_ui.brain_view_requested.connect(open_database_viewer)
        brain.brain_view_callback = vega_ui.brain_view_requested.emit
        brain.database_view_callback = vega_ui.brain_view_requested.emit
        brain.holographic_command_callback = vega_ui.executar_comando_holografico
        # App aberto por voz já nasce SELECIONADO no ambiente holográfico.
        brain.app_opened_callback = vega_ui.selecionar_app_por_voz
        VegaCommands.register_database_callback(open_database_viewer)
        VegaHomework.status_callback = emit_vega_status

        # Monitor de conexão do celular: reconecta sem fio quando o MIUI
        # desliga a depuração (economia de bateria / queda de Wi-Fi).
        brain.start_phone_monitor()

        # A sessão de projeto reutiliza a mesma janela do núcleo da Vega.
        project_ui = vega_ui

        # ------------------------------------------------------
        # VISÃO
        # ------------------------------------------------------

        print("[Inicialização] Preparando câmera...", flush=True)
        # Descoberta de câmera é adiada até o usuário solicitar captura; uma
        # câmera ausente/ocupada não deve impedir a VEGA de iniciar.
        camera = VegaCamera(
            camera_index=0,
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

    # Máquina de estados única da conversa (IDLE/LISTENING/.../SPEAKING).
    # Substitui a combinação de booleanos independentes por transições
    # explícitas e validadas.
    def _on_state_change(old, new):
        print(f"[STATE] {old} -> {new}")

    conv_state = ConversationState(initial=ConversationState.IDLE, on_change=_on_state_change)

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

    # Câmera inicial: a WEBCAM DO PC, escolhida pelo NOME do dispositivo.
    #
    # O índice não é confiável — o Windows define a ordem conforme quem
    # registra primeiro, então a Iriun (celular) pode ficar no 0 e a webcam
    # física no 1. Por isso o padrão é resolvido por nome, e não por número.
    try:
        from config.camera_menu import load_preferred_camera

        preferred_camera = load_preferred_camera()
        if preferred_camera is not None:
            camera.switch_camera(preferred_camera)
            vega_ui.set_camera_index(preferred_camera)
            print(f"[VEGA Câmera] Usando a câmera {preferred_camera} (escolhida antes).")
        else:
            padrao = camera.select_camera()
            camera.switch_camera(padrao)
            vega_ui.set_camera_index(padrao)
            nomes = camera.camera_names()
            rotulo = nomes[padrao] if padrao < len(nomes) else f"índice {padrao}"
            print(f"[VEGA Câmera] Usando a câmera padrão do PC: {rotulo}.")
    except Exception as error:
        print(f"[VEGA Câmera] Preferência indisponível, usando a do PC: {error}")

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
            if value:
                conv_state.transition(ConversationState.SPEAKING)
            elif conv_state.state == ConversationState.SPEAKING:
                conv_state.transition(ConversationState.LISTENING)
        except Exception:
            pass

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
        try:
            conv_state.transition(ConversationState.INTERRUPTED)
        except Exception:
            pass
        atualizar_audio(0.0)
        set_speaking(False)
        try:
            conv_state.transition(ConversationState.LISTENING)
        except Exception:
            pass
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

    def falar(text, trace=None):

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
                text,
                turn_trace=trace,
            )

        except Exception as error:

            print(
                "\n[VegaSpeaker] "
                f"Erro: {error}"
            )

            SESSION_METRICS.record(errors=1)

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
        """Captura um frame pronto para análise, esperando a câmera aquecer.

        Duas correções que resolvem a falha na PRIMEIRA tentativa:

        1. Se a câmera estava desligada, ela é ligada e recebe um tempo de
           aquecimento ANTES de começar a espera pelo frame. Na primeira vez a
           webcam abre do zero (o driver inicializa o sensor) e isso demora —
           antes, a espera começava junto com a abertura e estourava.

        2. O limite total é maior e o laço só aceita um frame com conteúdo,
           não apenas o primeiro que aparecer (que é preto).
        """
        started_for_request = not camera_active
        if started_for_request:
            start_camera()
            # Deixa o laço de captura abrir o dispositivo e estabilizar antes
            # de exigir um frame bom. Sem isso, a primeira pergunta falhava e a
            # segunda funcionava — exatamente o que o usuário relatou.
            time.sleep(1.2)

        try:
            deadline = time.monotonic() + 10.0
            frame = camera.latest_frame
            while time.monotonic() < deadline:
                if not camera.is_running:
                    break
                frame = camera.latest_frame
                if frame is not None and not _frame_is_dark(frame):
                    break
                time.sleep(0.05)

            if frame is None:
                return None

            return Image.fromarray(frame[:, :, ::-1].copy())
        finally:
            if started_for_request:
                close_camera()

    def _frame_is_dark(frame):
        """True quando o frame ainda está praticamente preto (sem conteúdo)."""
        try:
            return float(frame.mean()) < 12.0
        except Exception:
            return False

    brain.camera_frame_callback = capture_camera_frame

    # Tela do PC: a VEGA sai da frente por um instante antes da captura.
    # O tema dela é quase preto, então capturar com ela em foco produz uma
    # imagem preta — e o modelo responde honestamente "estou vendo uma tela
    # escura". Esconder e restaurar resolve isso sem o usuário perceber.
    def _hide_self_for_screen_capture():
        try:
            vega_ui.hide()
            QApplication.processEvents()
            time.sleep(0.35)
        except Exception:
            pass

    def _restore_self_after_screen_capture():
        try:
            QMetaObject.invokeMethod(vega_ui, "show", Qt.QueuedConnection)
        except Exception:
            try:
                vega_ui.show()
            except Exception:
                pass

    brain.screen_hide_callback = _hide_self_for_screen_capture
    brain.screen_restore_callback = _restore_self_after_screen_capture

    def alternar_camera_interface(active):
        if active:
            start_camera()
        else:
            close_camera()
        vega_ui.set_camera_state(active)

    def selecionar_camera_interface(index):
        """Troca a câmera em uso (painel no botão de câmera).

        A troca vale para a captura por turnos E para o modo live: os dois
        usam o mesmo objeto `camera`. A escolha é lembrada entre execuções —
        o padrão continua sendo a câmera do PC até você escolher outra.

        A troca NÃO fecha e reabre a câmera: o `switch_camera` já aplica o
        índice (e descarta o frame da câmera anterior). Fechar/reabrir no meio
        fazia a captura seguinte pegar o frame velho — origem do "escolhi a
        câmera 1 e ela usou a do PC".
        """
        try:
            index = int(index)
        except (TypeError, ValueError):
            return
        try:
            from config.camera_menu import save_preferred_camera

            camera.switch_camera(index)
            vega_ui.set_camera_index(index)
            # Guarda o índice E o nome: se o Windows mudar a ordem dos
            # dispositivos, o nome permite reencontrar a mesma câmera.
            nome = ""
            try:
                nomes = camera.camera_names()
                if index < len(nomes):
                    nome = nomes[index]
            except Exception:
                nome = ""
            save_preferred_camera(index, nome)
            print(f"[VEGA Câmera] Usando a câmera {index}{' - ' + nome if nome else ''}.")
        except Exception as error:
            print(f"[VEGA Câmera] Não consegui trocar para a câmera {index}: {error}")

    vega_ui.camera_selected.connect(selecionar_camera_interface)

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
                trace = brain.current_trace
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
                        falar(sentence, trace=trace)
                        spoke_sentences = True
                print()
                remaining_text = speech_buffer.strip()
                if not response_cancelled.is_set():
                    if remaining_text:
                        falar(remaining_text, trace=trace)
                    elif full_response.strip() and not spoke_sentences:
                        falar(full_response.strip(), trace=trace)
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
        """Saudação de ativação.

        As notícias são oferecidas APENAS na primeira ativação do dia
        (`should_run_activation_update`), para não virar repetição a cada vez
        que a VEGA acorda. Nas demais ativações ela só cumprimenta.

        A ferramenta de notícias continua disponível a qualquer momento por
        pedido direto ("abre as notícias").
        """
        nonlocal pending_news_items
        if not is_session_active():
            return

        # Já falou das notícias hoje? Então é só um cumprimento breve.
        if not should_run_activation_update(brain.memory):
            try:
                greeting = "Oi, Endriw. Estou pronta. O que a gente vai fazer agora?"
                print(f"Vega: {greeting}")
                emit_vega_status(greeting)
                set_speaking(True)
                falar(greeting)
                speaker.wait_until_done(timeout_seconds=15.0)
            except Exception:
                pass
            finally:
                atualizar_audio(0.0)
                set_speaking(False)
            return

        # PRIMEIRA vez do dia: oferece o resumo, como o usuário pediu.
        try:
            items = fetch_startup_news(limit=3)
        except Exception:
            items = []
        with news_items_lock:
            pending_news_items = items

        try:
            question = "Oi, Endriw. Quer que eu leia o resumo das três principais notícias?"
            print(f"Vega: {question}")
            emit_vega_status(question)
            # A interface já prefixa com "VEGA: " — não duplicar o prefixo aqui.
            if items:
                vega_ui.tool_result_ready.emit("news", {"items": items, "startup": True})
            news_prompt_active.set()
            set_speaking(True)
            awaiting_news_confirmation.set()

            def _news_answer(confirmed, _items=list(items)):
                if confirmed:
                    with news_items_lock:
                        current = list(pending_news_items) or _items
                    return build_startup_news_briefing(limit=3, items=current)
                vega_ui.tool_result_cleared.emit()
                return "Tudo bem. Não vou ler as notícias agora."

            brain.ask_pending_question("news", _news_answer)
            falar(question)
            speaker.wait_until_done(timeout_seconds=30.0)
        except Exception:
            pass
        finally:
            news_prompt_active.clear()

        # Marca o dia: só depois de oferecer, para não suprimir a oferta se
        # algo falhar antes de a pergunta ser feita.
        try:
            mark_activation_update_sent(brain.memory)
        except Exception:
            pass
        atualizar_audio(0.0)
        set_speaking(False)

    def encerrar_sessao():

        stop_background_music()

        # Modo live ligado? Encerra junto — ele segura microfone e alto-falante.
        try:
            brain.stop_live_mode()
        except Exception:
            pass

        set_session_active(
            False
        )

        try:
            conv_state.transition(ConversationState.IDLE)
        except Exception:
            pass

        # Resumo objetivo de métricas da sessão.
        print(SESSION_METRICS.summary_line())

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

    def _handle_news_confirmation(user_input):
        """Trata a resposta do usuário quando a VEGA ofereceu ler as notícias.

        Devolve True quando o turno foi consumido (não seguir para o fluxo
        normal).

        Uma fala que NÃO é resposta clara ("abre o chrome", por exemplo) encerra
        a espera: a VEGA segue com o pedido do usuário em vez de reter o estado
        e tentar interpretar a próxima fala como se fosse um sim/não.
        """
        confirmation = classify_news_confirmation(user_input)
        if confirmation is None:
            # A pessoa mudou de assunto: a oferta perde a validade e some o
            # painel de notícias, mas o comando dela segue normalmente.
            awaiting_news_confirmation.clear()
            return False

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
            # Pós-leitura: a VEGA PERGUNTA se quer saber mais, em vez de um
            # texto fixo de oferta. A pergunta é registrada no CÉREBRO — quando
            # o usuário confirmar (pela intenção, não por palavra-chave), ela
            # ABRE AS NOTÍCIAS que leu, no app padrão. Antes era um if com
            # texto fixo sobre "vídeos bombando", e um "abre aí" seguinte
            # morria como "abrir o quê?".
            if confirmation and not response_cancelled.is_set():
                followup = "Quer saber mais sobre as notícias?"
                emit_vega_status(followup)

                def _news_more_answer(wants_more, _items=None):
                    with news_items_lock:
                        current = list(pending_news_items)
                    if not wants_more or not current:
                        return "Tudo bem, ficamos por aqui."
                    opened = []
                    for item in current[:3]:
                        link = str(item.get("link") or "").strip()
                        if not link:
                            continue
                        try:
                            os.startfile(link)
                            opened.append(link)
                        except Exception as error:
                            print(f"[VEGA Notícias] Falha ao abrir {link}: {error}")
                    if not opened:
                        return "Não consegui abrir as notícias agora."
                    return f"Abri {len(opened)} notícia(s) para você."

                brain.ask_pending_question("news_more", _news_more_answer)
                falar(followup)
                speaker.wait_until_done(timeout_seconds=20.0)
        except Exception:
            pass
        finally:
            news_prompt_active.clear()
        atualizar_audio(0.0)
        set_speaking(False)
        return True

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

            if awaiting_news_confirmation.is_set() and _handle_news_confirmation(
                user_input
            ):
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

            # Modo live: pedido explícito de conversa em tempo real. A fala
            # precisa citar "tempo real"/"ao vivo" junto de um verbo, para não
            # capturar conversa normal que só menciona a palavra "live".
            live_requested = (
                bool(command_words & {"ativar", "ativa", "ligar", "liga", "entrar", "abrir", "abre", "modo"})
                and bool(command_words & {"live", "realtime", "tempo", "real", "vivo", "aovivo"})
            )
            live_stop_requested = (
                bool(command_words & {"desativar", "desativa", "desligar", "desliga", "sair", "encerrar", "parar", "para", "fechar", "fecha"})
                and bool(command_words & {"live", "realtime", "tempo", "real", "vivo", "aovivo"})
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
            # MODO LIVE (conversa em tempo real)
            # ==================================================
            # Tratado ANTES dos comandos visuais: é um regime de conversa, não
            # uma ação de sistema, e precisa falar imediatamente.

            if live_requested or live_stop_requested:
                if live_stop_requested:
                    try:
                        brain.stop_live_mode()
                    except Exception:
                        pass
                    resposta_live = "Modo em tempo real encerrado."
                else:
                    def _live_text(texto):
                        if texto and str(texto).strip():
                            print(f"Vega (live): {str(texto).strip()}")

                    started = False
                    try:
                        started = brain.start_live_mode(
                            video_mode="camera" if camera_active else None,
                            on_text=_live_text,
                            on_status=lambda msg: print(f"[VEGA Live] {msg}"),
                        )
                    except Exception as error:
                        print(f"[VEGA Live] Falha ao iniciar: {error}")
                    resposta_live = (
                        "Modo em tempo real ativo. Pode falar comigo normalmente."
                        if started
                        else "Não consegui iniciar o modo em tempo real agora."
                    )

                print(f"Vega: {resposta_live}")
                emit_vega_status(resposta_live)
                set_speaking(True)
                falar(resposta_live)
                try:
                    if speaker.is_speaking:
                        speaker.wait_until_done()
                except Exception:
                    pass
                atualizar_audio(0.0)
                set_speaking(False)
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

            # Fala anterior terminou (ou foi interrompida): sai de SPEAKING
            # antes de iniciar o turno novo. Sem isso, o próximo set_speaking
            # tentaria SPEAKING -> SPEAKING e a conversa ficava presa.
            try:
                if conv_state.state in {
                    ConversationState.SPEAKING,
                    ConversationState.INTERRUPTED,
                    ConversationState.EXECUTING,
                    ConversationState.THINKING,
                }:
                    conv_state.transition(ConversationState.LISTENING)
            except Exception:
                pass

            set_speaking(
                True
            )

            atualizar_processamento(True)
            trace = brain.current_trace

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
                        falar(sentence, trace=trace)
                        spoke_sentences = True

                else:
                    final_text = full_response.strip()
                    remaining_text = speech_buffer.strip()
                    if remaining_text:
                        falar(remaining_text, trace=trace)
                    elif final_text and not spoke_sentences:
                        falar(final_text, trace=trace)

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
            # RELATÓRIO DE LATÊNCIA DO TURNO
            # ==================================================

            turn_trace = brain.current_trace
            if turn_trace is not None:
                print(f"[LATENCY] {turn_trace.report()}")

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
        try:
            conv_state.transition(ConversationState.LISTENING)
        except Exception:
            pass
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
