import io
import os
import queue
import re
import threading
import time
import wave
import asyncio

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd
from config.environment import load_vega_environment


class VegaSpeaker:

    @staticmethod
    def _resolve_tts_settings():
        voice = (os.getenv("EDGE_TTS_VOICE", "pt-BR-FranciscaNeural") or "pt-BR-FranciscaNeural").strip()
        if not voice:
            voice = "pt-BR-FranciscaNeural"
        return voice

    @staticmethod
    def _resolve_tts_config():
        voice = (os.getenv("EDGE_TTS_VOICE", "pt-BR-FranciscaNeural") or "pt-BR-FranciscaNeural").strip()
        rate = (os.getenv("EDGE_TTS_RATE", "+0%") or "+0%").strip()
        pitch = (os.getenv("EDGE_TTS_PITCH", "+0Hz") or "+0Hz").strip()
        if not voice:
            voice = "pt-BR-FranciscaNeural"
        return voice, rate, pitch

    @staticmethod
    def _split_text_for_speech(text, max_chars=260):
        if text is None:
            return []

        cleaned = re.sub(r"\s+", " ", str(text)).strip()
        if not cleaned:
            return []

        if len(cleaned) <= max_chars:
            return [cleaned]

        parts = cleaned.split()
        result = []
        current = ""

        for word in parts:
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= max_chars:
                current = candidate
                continue

            if current:
                result.append(current)
            current = word

        if current:
            result.append(current)

        return result or [cleaned]

    @staticmethod
    def _prepare_text_for_speech(text):
        if text is None:
            return ""
        t = str(text)

        # 1. Correção fonética do nome do usuário
        t = re.sub(r"\bendriw\b", "Êndriu", t, flags=re.IGNORECASE)

        # 2. Remoção de sintaxe markdown para evitar leitura de símbolos
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
        t = re.sub(r"[*_#`~]", "", t)
        t = re.sub(r"https?://\S+", "", t)

        # 3. Temperaturas: 22°C ou 22° -> 22 graus
        t = re.sub(r"(-?\d+(?:[.,]\d+)?)\s*°\s*C\b", r"\1 graus", t, flags=re.IGNORECASE)
        t = re.sub(r"(-?\d+(?:[.,]\d+)?)\s*°\b", r"\1 graus", t)

        # 4. Unidades de medida
        t = re.sub(r"(\d+(?:[.,]\d+)?)\s*km/h\b", r"\1 quilômetros por hora", t, flags=re.IGNORECASE)
        t = re.sub(r"(\d+(?:[.,]\d+)?)\s*m/s\b", r"\1 metros por segundo", t, flags=re.IGNORECASE)
        t = re.sub(r"(\d+(?:[.,]\d+)?)\s*mm\b", r"\1 milímetros", t, flags=re.IGNORECASE)

        # 5. Porcentagens
        t = re.sub(r"\+([0-9]+(?:[.,][0-9]+)?)\s*%", r"mais \1 por cento", t)
        t = re.sub(r"-([0-9]+(?:[.,][0-9]+)?)\s*%", r"menos \1 por cento", t)
        t = re.sub(r"([0-9]+(?:[.,][0-9]+)?)\s*%", r"\1 por cento", t)

        # 6. Moeda brasileira R$ (ex: R$ 5,14 -> 5 reais e 14 centavos)
        def _brl_to_speech(m):
            reais = int(m.group(1))
            cents = m.group(2)
            c = int(cents[:2].ljust(2, "0")) if cents else 0
            c_txt = "centavo" if c == 1 else "centavos"
            r_txt = "real" if reais == 1 else "reais"
            if reais == 0 and c > 0:
                return f"{c} {c_txt}"
            if c > 0:
                return f"{reais} {r_txt} e {c} {c_txt}"
            return f"{reais} {r_txt}"

        t = re.sub(r"R\$\s*(\d+)(?:[.,](\d{1,2}))?", _brl_to_speech, t)

        # 7. Símbolos de outras moedas
        t = re.sub(r"US\$\s*(\d+(?:[.,]\d{1,2})?)", r"\1 dólares", t)
        t = re.sub(r"€\s*(\d+(?:[.,]\d{1,2})?)", r"\1 euros", t)
        t = re.sub(r"£\s*(\d+(?:[.,]\d{1,2})?)", r"\1 libras", t)

        # 8. Siglas de moedas isoladas
        t = re.sub(r"\bUSD\b", "dólares", t)
        t = re.sub(r"\bBRL\b", "reais", t)
        t = re.sub(r"\bEUR\b", "euros", t)
        t = re.sub(r"\bBTC\b", "bitcoins", t)
        t = re.sub(r"\bGBP\b", "libras", t)

        # 9. Símbolo de igual em frases
        t = re.sub(r"\s*=\s*", " vale ", t)

        # 10. Decimais numéricos isolados com ponto (ex: 5.14 -> 5 vírgula 14)
        t = re.sub(r"(\d+)\.(\d+)", r"\1 vírgula \2", t)

        # 11. Normalização de espaços
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def configure_voice(self, voice=None, rate=None, pitch=None):
        if voice:
            self.voice = str(voice).strip()
        if rate:
            self.rate = str(rate).strip()
        if pitch:
            self.pitch = str(pitch).strip()

    def add_pronunciation(self, source, spoken):
        if source and spoken:
            self.pronunciations[str(source)] = str(spoken)

    def __init__(self):

        # ==========================================================
        # CONFIGURAÇÃO
        # ==========================================================

        load_vega_environment()

        self.voice, self.rate, self.pitch = self._resolve_tts_config()
        self.pronunciations = {
            "Endriw": "Êndriu",
            "ETEC": "etéqui",
        }

        # ==========================================================
        # CLIENTE EDGE TTS
        # ==========================================================

        # ==========================================================
        # FILA DE TEXTO
        # ==========================================================

        self.text_queue = queue.Queue()
        self.audio_queue = queue.Queue()
        self._generation = 0

        # ==========================================================
        # CONTROLE
        # ==========================================================

        self.is_running = True

        self._speaking = False

        self._state_lock = threading.Lock()

        # Evento usado para indicar que a Vega está ocupada
        # gerando ou reproduzindo áudio.
        self._speaking_event = threading.Event()

        # Evento para acordar as threads quando necessário.
        self._stop_event = threading.Event()

        # ==========================================================
        # THREAD DE TTS
        # ==========================================================

        self._worker_thread = threading.Thread(
            target=self._tts_worker,
            name="VegaEdgeTTSGenerator",
            daemon=True
        )

        self._worker_thread.start()

        self._playback_thread = threading.Thread(
            target=self._playback_worker,
            name="VegaEdgeTTSPlayback",
            daemon=True,
        )
        self._playback_thread.start()

    # ==========================================================
    # FALAR
    # ==========================================================

    def speak(self, text):

        if not self.is_running:
            return False

        if not text:
            return False

        text = self._prepare_text_for_speech(str(text).strip())

        if not text:
            return False

        chunks = self._split_text_for_speech(text)
        if not chunks:
            return False

        with self._state_lock:
            self._speaking = True
            generation = self._generation

        self._speaking_event.set()

        for chunk in chunks:
            self.text_queue.put((generation, chunk))

        return True

    # ==========================================================
    # WORKER PRINCIPAL
    # ==========================================================

    def _tts_worker(self):

        while self.is_running:

            try:

                item = self.text_queue.get()

                if item is None:
                    self.text_queue.task_done()
                    break

                generation, text = item
                with self._state_lock:
                    if generation != self._generation:
                        self.text_queue.task_done()
                        continue

                try:

                    audio_bytes = self._request_edge_audio(text)

                    self.audio_queue.put((audio_bytes, None, generation))

                except Exception as error:
                    self.audio_queue.put((None, error, generation))

                finally:
                    pass

            except Exception as error:

                print(
                    f"\n[VegaSpeaker] "
                    f"Erro interno: {error}"
                )

    def _playback_worker(self):
        while self.is_running or not self.audio_queue.empty():
            item = self.audio_queue.get()
            if item is None:
                self.audio_queue.task_done()
                break

            audio_bytes, error, generation = item
            try:
                with self._state_lock:
                    is_current = generation == self._generation
                if not is_current:
                    continue
                if error is not None:
                    print(f"\n[VegaSpeaker] Erro no Edge TTS: {error}")
                elif self.is_running and audio_bytes:
                    self._play_audio_bytes(audio_bytes)
            finally:
                self.text_queue.task_done()
                self.audio_queue.task_done()
                self._update_state()

    async def _generate_edge_audio(self, text):
        for source, spoken in self.pronunciations.items():
            text = re.sub(rf"\b{re.escape(source)}\b", spoken, text, flags=re.IGNORECASE)
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        audio = bytearray()

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])

        return bytes(audio)

    def _request_edge_audio(self, text):
        audio = asyncio.run(self._generate_edge_audio(text))
        if not audio:
            raise RuntimeError("Edge TTS retornou uma resposta de áudio vazia.")
        return audio

    # ==========================================================
    # ATUALIZAR ESTADO
    # ==========================================================

    def _update_state(self):

        if not self.text_queue.empty() or not self.audio_queue.empty():
            self._speaking_event.set()
            with self._state_lock:
                self._speaking = True
            return

        with self._state_lock:
            self._speaking = False
        self._speaking_event.clear()

    # ==========================================================
    # REPRODUZIR ÁUDIO (WAV / MP3 / FLAC)
    # ==========================================================

    def _play_audio_bytes(self, audio_data: bytes):

        if not audio_data:
            return

        # Tenta decodificar primeiro via miniaudio (suporta mp3, wav, flac, etc.)
        try:
            decoded = miniaudio.decode(audio_data)
            data = np.array(decoded.samples, dtype=np.int16)
            if decoded.nchannels > 1:
                data = data.reshape(-1, decoded.nchannels)
            samplerate = decoded.sample_rate
        except Exception:
            # Fallback direto via wave caso já seja WAV
            wav_bytes = io.BytesIO(audio_data)
            with wave.open(wav_bytes, "rb") as wf:
                samplerate = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())

            if sampwidth == 1:
                dtype = np.uint8
            elif sampwidth == 2:
                dtype = np.int16
            elif sampwidth == 4:
                dtype = np.int32
            else:
                raw = np.frombuffer(frames, dtype=np.uint8)
                raw = raw.reshape(-1, 3)
                data = (
                    raw[:, 0].astype(np.int32)
                    | (raw[:, 1].astype(np.int32) << 8)
                    | (raw[:, 2].astype(np.int32) << 16)
                )
                negative = (data & 0x800000) != 0
                data[negative] -= 0x1000000
                if channels > 1:
                    data = data.reshape(-1, channels)
                sd.play(data, samplerate, latency="high")
                sd.wait()
                return

            data = np.frombuffer(frames, dtype=dtype)
            if channels > 1:
                data = data.reshape(-1, channels)

        # Pequeno silêncio de padding
        pad_samples = int(samplerate * 0.02)
        if data.ndim > 1:
            channels = data.shape[1]
            silence = np.zeros((pad_samples, channels), dtype=data.dtype)
            data = np.concatenate((data, silence), axis=0)
        else:
            silence = np.zeros(pad_samples, dtype=data.dtype)
            data = np.concatenate((data, silence))

        sd.play(data, samplerate, latency="high")
        sd.wait()

    # ==========================================================
    # ESPERAR TERMINAR
    # ==========================================================

    def wait_until_done(self, timeout_seconds: float = 20.0):

        deadline = time.monotonic() + timeout_seconds
        with self._state_lock:
            generation = self._generation

        while time.monotonic() < deadline:
            with self._state_lock:
                if generation != self._generation:
                    return
                speaking = self._speaking
            if (
                not speaking
                and self.text_queue.unfinished_tasks == 0
                and self.audio_queue.unfinished_tasks == 0
            ):
                return
            time.sleep(0.02)

    # ==========================================================
    # STATUS
    # ==========================================================

    @property
    def is_speaking(self):

        with self._state_lock:

            return self._speaking

    # ==========================================================
    # PARAR
    # ==========================================================

    def interrupt(self):
        """Stop the current utterance and discard its queued audio, keeping TTS alive."""
        with self._state_lock:
            self._generation += 1
            self._speaking = False

        self._speaking_event.clear()
        try:
            sd.stop()
        except Exception:
            pass

        self._discard_pending(self.text_queue)
        self._discard_pending(self.audio_queue, paired_text=True)

    def _discard_pending(self, pending_queue, paired_text=False):
        while True:
            try:
                item = pending_queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                pending_queue.task_done()
                pending_queue.put(None)
                return
            pending_queue.task_done()
            if paired_text:
                self.text_queue.task_done()

    def stop(self):

        if not self.is_running:
            return

        self.is_running = False

        self._stop_event.set()

        # ----------------------------------------------------------
        # Parar áudio imediatamente.
        # ----------------------------------------------------------

        try:

            sd.stop()

        except Exception:

            pass

        # ----------------------------------------------------------
        # Liberar worker.
        # ----------------------------------------------------------

        try:

            self.text_queue.put_nowait(
                None
            )

        except Exception:

            pass

        try:
            self.audio_queue.put_nowait(None)
        except Exception:
            pass

        # ----------------------------------------------------------
        # Limpar estado.
        # ----------------------------------------------------------

        with self._state_lock:
            self._speaking = False

        self._speaking_event.clear()
