import threading
import time
import numpy as np
import sounddevice as sd
import openwakeword
from openwakeword.model import Model
class WakeWordDetector:

    # Backoff de reconexão do stream de áudio (segundos).
    RECONNECT_BACKOFF_BASE = 0.5
    RECONNECT_BACKOFF_MAX = 8.0
    RECONNECT_BACKOFF_FACTOR = 2.0

    def __init__(
        self,
        model_names: list = None,
        threshold: float = 0.5,
        on_wake_word_callback=None
    ):
        """
        Detector de palavras de ativação usando openWakeWord.
        :param model_names: Lista de caminhos para arquivos .onnx ou nomes de modelos padrão.
        :param threshold: Sensibilidade para ativação (0.0 a 1.0).
        :param on_wake_word_callback: Função executada ao detectar qualquer uma das palavras.
        """
        if model_names is None:
            self.model_names = ["acorda_vega.onnx", "ei_vega.onnx", "vega.onnx"]
        else:
            self.model_names = model_names

        self.threshold = threshold
        self.on_wake_word = on_wake_word_callback

        self.is_running = False
        self._stream = None
        self.oww_model = None

    def _audio_callback(self, indata, frames, time_info, status):

        if not self.is_running or self.oww_model is None:
            return
        try:

            audio_data = (indata[:, 0] * 32767).astype(np.int16)
            prediction = self.oww_model.predict(audio_data)

            for model, score in prediction.items():

                if score >= self.threshold:

                    self.oww_model.reset()

                    if self.on_wake_word:

                        threading.Thread(
                            target=self.on_wake_word,
                            daemon=True
                        ).start()

                    break
        except Exception as error:

            # Nunca deixa uma falha transitória no callback derrubar a thread
            # de áudio do sounddevice; apenas registra e segue.
            print(f"[WakeWordDetector] Erro no callback de áudio: {error}")

    def start(self, device_index: int = None):

        """Inicia a escuta das palavras de ativação em segundo plano."""
        if self.is_running:
            return

        try:

            # Baixa os modelos padrão do openwakeword se ainda não existirem no ambiente
            openwakeword.utils.download_models()

            self.oww_model = Model(
                wakeword_models=self.model_names,
                inference_framework="onnx"
            )
            self._stream = self._build_stream(device_index)

            self._stream.start()

            self.is_running = True
        except Exception as error:

            print(f"[WakeWordDetector] Erro ao iniciar: {error}")
            self.is_running = False
            self._close_stream()

    def _build_stream(self, device_index):

        """Cria (mas não inicia) o InputStream do sounddevice."""

        return sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=16000,
            blocksize=1280,
            callback=self._audio_callback
        )

    def _close_stream(self):

        """Fecha o stream atual sem propagar erros de dispositivo."""

        if self._stream is not None:

            try:

                self._stream.stop()
                self._stream.close()

            except Exception:

                pass
            self._stream = None
    def run_with_reconnect(self, device_index: int = None):

        """Reconecta o stream de áudio com backoff em falhas transitórias.

        Um microfone desconectado ou um stream morto não encerra o listener:
        o stream é recriado com backoff exponencial até a captura voltar.
        """

        backoff = self.RECONNECT_BACKOFF_BASE
        while self.is_running:

            stream = self._stream
            if stream is None or (hasattr(stream, "active") and not stream.active):

                print("[WakeWordDetector] Stream inativo; reconectando...")
                self._close_stream()

                try:

                    self._stream = self._build_stream(device_index)
                    self._stream.start()
                    backoff = self.RECONNECT_BACKOFF_BASE
                except Exception as error:

                    print(f"[WakeWordDetector] Falha ao reconectar: {error}")
                    self._stream = None
            if self._stream is None:

                time.sleep(backoff)
                backoff = min(
                    backoff * self.RECONNECT_BACKOFF_FACTOR,
                    self.RECONNECT_BACKOFF_MAX
                )

            else:

                time.sleep(0.25)

    def stop(self):

        """Para a escuta das palavras de ativação."""

        self.is_running = False
        self._close_stream()
