import re
import time
from collections import deque

import numpy as np
import sounddevice as sd
import speech_recognition as sr


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

        self.recognizer.pause_threshold = 0.38
        self.recognizer.phrase_threshold = 0.06
        self.recognizer.non_speaking_duration = 0.08

        # ==========================================================
        # DETECÇÃO DE FALA
        # ==========================================================

        self.block_duration = 0.04

        # RMS values for int16 audio. The former thresholds (500/350) lost
        # the beginning of quiet, short answers such as "sim" and "não".
        self.start_threshold = 180
        self.silence_threshold = 120

        # Tempo de silêncio necessário para considerar
        # que o usuário terminou de falar.
        self.silence_duration = 0.40

        # Limites de segurança.
        self.max_duration = 9.0
        self.start_timeout = 4.5

        # Preserve a small portion of audio that comes before speech is
        # detected. This keeps the first consonant of a short word.
        self.pre_roll_duration = 0.32
        self.minimum_speech_duration = 0.28
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

        if not self.enabled:
            time.sleep(0.05)
            return None

        block_size = int(
            self.sample_rate * self.block_duration
        )

        recorded_blocks = []

        pre_roll_blocks = deque(
            maxlen=max(
                1,
                int(self.pre_roll_duration / self.block_duration)
            )
        )

        speaking = False
        speech_start = None
        last_voice_time = None
        trigger_blocks = 0

        start_time = time.time()

        try:

            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.microphone_index,
                blocksize=block_size
            ) as stream:

                while True:

                    data, _ = stream.read(block_size)

                    audio_block = np.asarray(
                        data
                    ).flatten()

                    level = self._audio_level(
                        audio_block
                    )

                    current_time = time.time()

                    # ==================================================
                    # ESPERANDO O USUÁRIO COMEÇAR A FALAR
                    # ==================================================

                    if not speaking:

                        if stop_waiting is not None:
                            try:
                                if stop_waiting():
                                    return None
                            except Exception:
                                pass

                        # Update the ambient baseline only while waiting for
                        # speech, then derive thresholds that work in both a
                        # quiet room and with a fan/PC in the background.
                        if self._noise_floor <= 0.0:
                            self._noise_floor = level
                        else:
                            self._noise_floor = (
                                self._noise_floor * 0.92
                                + level * 0.08
                            )

                        start_threshold = max(
                            self.start_threshold,
                            self._noise_floor * 2.8,
                        )
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
                            trigger_blocks += 1
                            if trigger_blocks >= self.start_trigger_count:
                                speaking = True

                                speech_start = current_time
                                last_voice_time = current_time

                                recorded_blocks.extend(pre_roll_blocks)
                                recorded_blocks.append(
                                    audio_block.copy()
                                )
                                if on_speech_start is not None:
                                    try:
                                        on_speech_start()
                                    except Exception:
                                        pass

                                continue
                        else:
                            trigger_blocks = 0

                        pre_roll_blocks.append(
                            audio_block.copy()
                        )

                        # ------------------------------------------------
                        # DURAÇÃO ESPECÍFICA
                        # ------------------------------------------------

                        if duration is not None:

                            if (
                                current_time - start_time
                                >= duration
                            ):
                                return None

                        # ------------------------------------------------
                        # TIMEOUT
                        # ------------------------------------------------

                        elif (
                            current_time - start_time
                            >= self.start_timeout
                        ):
                            return None

                        continue

                    # ==================================================
                    # USUÁRIO ESTÁ FALANDO
                    # ==================================================

                    recorded_blocks.append(
                        audio_block.copy()
                    )

                    # ==================================================
                    # DETECTAR VOZ
                    # ==================================================

                    silence_threshold = max(
                        self.silence_threshold,
                        self._noise_floor * 1.35
                    )

                    if level >= silence_threshold:

                        last_voice_time = current_time

                    if level_callback is not None:
                        try:
                            level_callback(level)
                        except Exception:
                            pass

                    # ==================================================
                    # DETECTAR FIM DA FRASE
                    # ==================================================

                    if (
                        last_voice_time is not None
                        and current_time - last_voice_time
                        >= self.silence_duration
                        and current_time - speech_start
                        >= self.minimum_speech_duration
                    ):

                        break

                    # ==================================================
                    # LIMITE MÁXIMO
                    # ==================================================

                    if (
                        speech_start is not None
                        and current_time - speech_start
                        >= self.max_duration
                    ):

                        break

        except Exception as error:

            raise RuntimeError(
                f"Erro no microfone: {error}"
            )

        # ==========================================================
        # NENHUM ÁUDIO
        # ==========================================================

        if not recorded_blocks:
            return None

        # ==========================================================
        # JUNTAR GRAVAÇÃO
        # ==========================================================

        recording = np.concatenate(
            recorded_blocks
        )

        audio = sr.AudioData(
            recording.tobytes(),
            self.sample_rate,
            2
        )

        # ==========================================================
        # RECONHECIMENTO
        # ==========================================================

        try:

            text = self.recognizer.recognize_google(
                audio,
                language="pt-BR"
            )

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
