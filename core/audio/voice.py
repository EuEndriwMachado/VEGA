import re
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import sounddevice as sd
import speech_recognition as sr


@dataclass
class _ListenContext:
    """Estado do ciclo de escuta do `listen` (fases espera/falando)."""

    recorded: list = field(default_factory=list)
    pre_roll: deque = field(default_factory=deque)
    speaking: bool = False
    speech_start: float | None = None
    last_voice_time: float | None = None
    trigger_blocks: int = 0
    start_time: float = 0.0
    duration: float | None = None


class VegaVoice:

    def __init__(
        self,
        microphone_index=1,
        sample_rate=16000,
        channels=1
    ):

        # ==========================================================
        # CONFIGURAÇÃO DO MICROFONE
        # ==========================================================

        self.microphone_index = microphone_index
        self.sample_rate = sample_rate
        self.channels = channels

        # ==========================================================
        # RECONHECIMENTO DE VOZ
        # ==========================================================

        self.recognizer = sr.Recognizer()

        self.recognizer.energy_threshold = 180
        self.recognizer.dynamic_energy_threshold = True

        # `pause_threshold` é o silêncio que o reconhecedor aceita DENTRO de
        # uma frase. Em 0.38s a VEGA cortava a pessoa no meio ("...o que você
        # está vendo" chegava como "o que você"). 0.7s dá tempo de respirar
        # sem esperar demais entre turnos.
        self.recognizer.pause_threshold = 0.70
        self.recognizer.phrase_threshold = 0.06
        self.recognizer.non_speaking_duration = 0.20

        # ==========================================================
        # DETECÇÃO DE FALA
        # ==========================================================

        self.block_duration = 0.04

        # RMS values for int16 audio. The former thresholds (500/350) lost
        # the beginning of quiet, short answers such as "sim" and "não".
        self.start_threshold = 180
        self.silence_threshold = 120

        # Tempo de silêncio necessário para considerar
        # que o usuário terminou de falar. Antes (0.40s) a pausa natural de
        # respiração era lida como fim de turno e a fala era cortada.
        # 0.85s é curto o bastante para não travar a conversa e longo o
        # bastante para esperar uma frase completa.
        self.silence_duration = 0.85

        # Limites de segurança.
        # `max_duration` era 9.0s: pedidos longos ("pesquise sobre as eleições
        # de 2026 no celular e me manda o resultado") eram cortados. 14s cabe
        # uma frase longa sem risco de gravar minutos de silêncio.
        self.max_duration = 14.0
        self.start_timeout = 4.5

        # Preserve a small portion of audio that comes before speech is
        # detected. This keeps the first consonant of a short word.
        self.pre_roll_duration = 0.32
        # 0.28s descartava respostas curtas ("sim", "não", "toca"). 0.18s
        # ainda ignora estalos e tose, mas preserva uma palavra curta.
        self.minimum_speech_duration = 0.18
        self.start_trigger_count = 2
        self._noise_floor = 0.0

        # ==========================================================
        # CONTROLE
        # ==========================================================

        self.enabled = True

    # ==========================================================
    # NÍVEL DO ÁUDIO
    # ==========================================================

    @staticmethod
    def _audio_level(audio):

        if audio is None or len(audio) == 0:
            return 0.0

        audio_float = audio.astype(np.float32)

        return float(
            np.sqrt(
                np.mean(
                    audio_float * audio_float
                )
            )
        )

    # ==========================================================
    # OUVIR
    # ==========================================================

    def listen(
        self,
        duration=None,
        level_callback=None,
        on_speech_start=None,
        barge_in=False,
        output_active=None,
        stop_waiting=None,
    ):
        """Escuta até captar uma frase e devolve o texto transcrito.

        Estrutura (antes era um bloco único com 8 níveis de aninhamento):
        - o loop de captação só decide ENTRE fases; cada fase é um método:
            * `_waiting_state`   — espera o usuário começar a falar;
            * `_speaking_state`  — grava enquanto fala e decide o fim.
        """

        if not self.enabled:
            time.sleep(0.05)
            return None

        block_size = int(self.sample_rate * self.block_duration)

        ctx = _ListenContext(
            recorded=[],
            pre_roll=deque(maxlen=max(1, int(self.pre_roll_duration / self.block_duration))),
            speaking=False,
            speech_start=None,
            last_voice_time=None,
            trigger_blocks=0,
            start_time=time.time(),
            duration=duration,
        )

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.microphone_index,
                blocksize=block_size,
            ) as stream:
                while True:
                    data, _ = stream.read(block_size)
                    audio_block = np.asarray(data).flatten()
                    level = self._audio_level(audio_block)
                    current_time = time.time()

                    if not ctx.speaking:
                        action = self._waiting_state(
                            audio_block, level, current_time, ctx,
                            stop_waiting, on_speech_start, barge_in, output_active,
                        )
                    else:
                        action = self._speaking_state(
                            audio_block, level, current_time, ctx, level_callback,
                        )

                    if action == "return":
                        return None
                    if action == "break":
                        break
        except Exception as error:
            raise RuntimeError(f"Erro no microfone: {error}")

        return self._finish_recording(ctx.recorded)

    # ==========================================================
    # ESTADO: ESPERANDO O USUÁRIO COMEÇAR A FALAR
    # ==========================================================

    def _waiting_state(self, audio_block, level, current_time, ctx,
                       stop_waiting, on_speech_start, barge_in, output_active):
        """Fase de espera. Devolve 'return', 'break' ou None (continuar)."""
        if stop_waiting is not None:
            try:
                if stop_waiting():
                    return "return"
            except Exception:
                pass

        # Baseline do ruído ambiente: derivada só enquanto se espera falar,
        # funciona em sala silenciosa e com ventilador/PC ao fundo.
        if self._noise_floor <= 0.0:
            self._noise_floor = level
        else:
            self._noise_floor = self._noise_floor * 0.92 + level * 0.08

        start_threshold = max(self.start_threshold, self._noise_floor * 2.8)
        if barge_in and output_active is not None:
            try:
                if output_active():
                    start_threshold = max(
                        start_threshold * 2.5,
                        self.start_threshold * 3.0,
                        self._noise_floor * 3.8,
                    )
            except Exception:
                pass

        if level >= start_threshold:
            ctx.trigger_blocks += 1
            if ctx.trigger_blocks >= self.start_trigger_count:
                # O usuário começou a falar: muda de fase e grava o bloco
                # atual junto com o pré-roll (áudio anterior ao disparo).
                ctx.speaking = True
                ctx.speech_start = current_time
                ctx.last_voice_time = current_time
                ctx.recorded.extend(ctx.pre_roll)
                ctx.recorded.append(audio_block.copy())
                if on_speech_start is not None:
                    try:
                        on_speech_start()
                    except Exception:
                        pass
                return None
        else:
            ctx.trigger_blocks = 0

        ctx.pre_roll.append(audio_block.copy())

        # Duração específica ou timeout sem nenhuma fala.
        elapsed = current_time - ctx.start_time
        if ctx.duration is not None and elapsed >= ctx.duration:
            return "return"
        if elapsed >= self.start_timeout:
            return "return"
        return None

    # ==========================================================
    # ESTADO: USUÁRIO ESTÁ FALANDO (GRAVANDO)
    # ==========================================================

    def _speaking_state(self, audio_block, level, current_time, ctx, level_callback):
        """Fase de gravação. Devolve 'break' ou None (continuar)."""
        ctx.recorded.append(audio_block.copy())

        silence_threshold = max(
            self.silence_threshold,
            self._noise_floor * 1.35,
        )
        if level >= silence_threshold:
            ctx.last_voice_time = current_time

        if level_callback is not None:
            try:
                level_callback(level)
            except Exception:
                pass

        # Fim da frase: silêncio longo o bastante após fala longa o bastante.
        if (
            ctx.last_voice_time is not None
            and current_time - ctx.last_voice_time >= self.silence_duration
            and current_time - ctx.speech_start >= self.minimum_speech_duration
        ):
            return "break"

        # Limite máximo de duração da frase.
        if current_time - ctx.speech_start >= self.max_duration:
            return "break"
        return None

    # ==========================================================
    # JUNTAR GRAVAÇÃO E RECONHECER
    # ==========================================================

    def _finish_recording(self, recorded_blocks):
        if not recorded_blocks:
            return None

        recording = np.concatenate(recorded_blocks)
        audio = sr.AudioData(recording.tobytes(), self.sample_rate, 2)

        try:
            text = self.recognizer.recognize_google(audio, language="pt-BR")
            if not text:
                return None
            return text.strip()
        except sr.UnknownValueError:
            return None
        except sr.RequestError as error:
            raise RuntimeError(
                "Erro no serviço de reconhecimento: "
                f"{error}"
            )

    # ==========================================================
    # LIMPAR TEXTO PARA TTS
    # ==========================================================

    @staticmethod
    def clean_for_speech(text):

        if not text:
            return ""

        text = str(text)

        # Markdown
        text = re.sub(
            r"[*_`#~]+",
            "",
            text
        )

        # Links Markdown
        text = re.sub(
            r"\[([^\]]+)\]\([^)]+\)",
            r"\1",
            text
        )

        # URLs
        text = re.sub(
            r"https?://\S+",
            "",
            text
        )

        # Alguns símbolos/emoji que não precisam
        # ser enviados para a voz.
        text = re.sub(
            r"[•▪◾🔹🔸⭐✨🔥🎮💻🚀❤️😂🤣😉😏]",
            "",
            text
        )

        # Espaços duplicados
        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text.strip()

    # ==========================================================
    # DESATIVAR MICROFONE
    # ==========================================================

    def disable(self):

        self.enabled = False

    # ==========================================================
    # ATIVAR MICROFONE
    # ==========================================================

    def enable(self):

        self.enabled = True

    # ==========================================================
    # PARAR CAPTURA
    # ==========================================================

    @staticmethod
    def stop():

        try:

            sd.stop()

        except Exception:

            pass
