import time

from core.audio.clap_detector import ClapDetector
from core.audio.wake_word import WakeWordDetector


class ActivationManager:

    TRIGGER_COOLDOWN = 1.0

    def __init__(
        self,
        wake_models: list = None,
        on_trigger_callback=None
    ):
        """
        Gerencia o disparo da VEGA por múltiplas palavras de ativação OU por palmas duplas.
        :param wake_models: Lista de modelos .onnx ("Acorda Vega", "Ei Vega", "Vega").
        :param on_trigger_callback: Função executada ao ativar.
        """
        self.on_trigger = on_trigger_callback
        self._last_trigger_time = 0.0

        if wake_models is None:
            wake_models = ["acorda_vega.onnx", "ei_vega.onnx", "vega.onnx"]

        # Detector de Palmas Duplas
        self.clap_detector = ClapDetector(
            threshold=0.25,
            on_double_clap_callback=self._handle_trigger
        )

        # Detector de Palavras de Ativação
        self.wake_detector = WakeWordDetector(
            model_names=wake_models,
            threshold=0.5,
            on_wake_word_callback=self._handle_trigger
        )

    def _handle_trigger(self):

        now = time.monotonic()
        # Debounce: wake word e palma dupla rodam em threads separadas e podem
        # disparar em rajada para o mesmo evento; janela de refratário evita
        # ativações duplicadas.
        if now - self._last_trigger_time < self.TRIGGER_COOLDOWN:
            return
        self._last_trigger_time = now

        if self.on_trigger:
            try:
                self.on_trigger()
            except Exception:
                # Nunca derrubar o detector em background por falha do callback.
                pass

    def start(self, device_index: int = None):

        """Inicia ambos os detectores em segundo plano."""
        self.clap_detector.start(device_index=device_index)
        self.wake_detector.start(device_index=device_index)

    def stop(self):

        """Para ambos os detectores."""
        self.clap_detector.stop()
        self.wake_detector.stop()