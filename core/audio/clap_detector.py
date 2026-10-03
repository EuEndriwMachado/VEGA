import time
import threading

import numpy as np
import sounddevice as sd


class ClapDetector:

    def __init__(
        self,
        threshold=0.35,
        min_interval=0.12,
        max_interval=0.40,
        on_double_clap_callback=None
    ):
        self.threshold = threshold
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.on_double_clap = on_double_clap_callback

        self.last_clap_time = 0.0
        self.is_running = False
        self._stream = None

        # Controle para evitar que um único som
        # seja contado várias vezes.
        self._last_detection_time = 0.0
        self._cooldown = 0.18

        # Controle de duração do som.
        self._loud_blocks = 0
        self._required_loud_blocks = 1

    # ==========================================================
    # CALLBACK DO MICROFONE
    # ==========================================================

    def _audio_callback(
        self,
        indata,
        frames,
        time_info,
        status
    ):

        if not self.is_running:
            return

        if indata is None or len(indata) == 0:
            return

        # ------------------------------------------------------
        # AMPLITUDE
        # ------------------------------------------------------

        audio = np.asarray(indata, dtype=np.float32)

        amplitude = float(
            np.max(
                np.abs(audio)
            )
        )

        current_time = time.time()

        # ------------------------------------------------------
        # SOM FRACO
        # ------------------------------------------------------

        if amplitude < self.threshold:

            self._loud_blocks = 0

            return

        # ------------------------------------------------------
        # COOLDOWN
        #
        # Impede que uma única palma seja detectada
        # várias vezes em blocos consecutivos.
        # ------------------------------------------------------

        if (
            current_time - self._last_detection_time
            < self._cooldown
        ):

            return

        self._loud_blocks += 1

        # ------------------------------------------------------
        # EXIGIR UM ESTALO REAL
        # ------------------------------------------------------

        if self._loud_blocks < self._required_loud_blocks:
            return

        self._loud_blocks = 0
        self._last_detection_time = current_time

        # ------------------------------------------------------
        # INTERVALO DESDE A PRIMEIRA PALMA
        # ------------------------------------------------------

        time_since_last = (
            current_time
            - self.last_clap_time
        )

        # ------------------------------------------------------
        # SEGUNDA PALMA
        # ------------------------------------------------------

        if (
            self.min_interval
            <= time_since_last
            <= self.max_interval
        ):

            self.last_clap_time = 0.0

            if self.on_double_clap:

                threading.Thread(
                    target=self.on_double_clap,
                    daemon=True
                ).start()

            return

        # ------------------------------------------------------
        # PRIMEIRA PALMA
        # ------------------------------------------------------

        self.last_clap_time = current_time

    # ==========================================================
    # INICIAR
    # ==========================================================

    def start(self, device_index=None):

        if self.is_running:
            return

        try:

            self._stream = sd.InputStream(
                device=device_index,
                channels=1,
                samplerate=44100,
                blocksize=512,
                dtype="float32",
                callback=self._audio_callback
            )

            self.is_running = True

            self._stream.start()

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

    # ==========================================================
    # PARAR
    # ==========================================================

    def stop(self):

        self.is_running = False
        self.last_clap_time = 0.0
        self._last_detection_time = 0.0
        self._loud_blocks = 0

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
