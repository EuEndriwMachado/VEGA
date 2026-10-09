import threading
import time

import numpy as np

from core.vision.camera import VegaCamera
import core.vision.camera as camera_module


class FakeCapture:
    def __init__(self):
        self.frame = np.zeros((12, 16, 3), dtype=np.uint8)
        self.released = False

    def isOpened(self):
        return True

    def set(self, *_args):
        return True

    def read(self):
        time.sleep(0.005)
        return True, self.frame.copy()

    def release(self):
        self.released = True


def test_camera_captures_and_publishes_frames(monkeypatch):
    capture = FakeCapture()
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", lambda *_args: capture)
    camera = VegaCamera(camera_index=0)
    received_frame = threading.Event()
    camera.set_frame_callback(lambda _frame: received_frame.set())

    camera.start()
    assert received_frame.wait(timeout=2)
    # Enquanto roda, o último frame fica disponível para a captura.
    assert camera.latest_frame is not None
    assert camera.latest_frame.shape == (12, 16, 3)
    camera.stop()

    assert capture.released
    assert not camera.is_running


def _wait_for_frame(camera, timeout=2.0):
    """Espera o primeiro quadro ficar disponível (o aquecimento leva tempo)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if camera.latest_frame is not None:
            return True
        time.sleep(0.02)
    return False


def test_stop_discards_the_last_frame(monkeypatch):
    """Parar a câmera descarta o frame — ele é da câmera ANTERIOR.

    Sem isto, trocar de câmera entregava a imagem da câmera antiga: o usuário
    escolhia "Câmera 1" no painel e a VEGA continuava mostrando a câmera do PC,
    porque `latest_frame` ainda guardava o quadro da anterior.
    """
    capture = FakeCapture()
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", lambda *_args: capture)
    camera = VegaCamera(camera_index=0)
    camera.start()
    assert _wait_for_frame(camera)
    assert camera.latest_frame is not None

    camera.stop()
    assert camera.latest_frame is None


def test_switch_camera_discards_the_previous_frame(monkeypatch):
    """Trocar de câmera descarta o frame da anterior imediatamente."""
    capture = FakeCapture()
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", lambda *_args: capture)
    camera = VegaCamera(camera_index=0)
    camera.start()
    assert _wait_for_frame(camera)
    assert camera.latest_frame is not None

    camera.switch_camera(1)
    # O frame em memória era da câmera 0: não pode sobreviver à troca.
    assert camera.latest_frame is None
    assert camera.camera_index == 1
    camera.stop()


def test_dark_camera_still_publishes_something(monkeypatch):
    """Câmera que só entrega imagem escura ainda publica o último quadro.

    Se todos os quadros fossem descartados, a VEGA diria que não conseguiu
    acessar a câmera — escondendo do usuário que a câmera existe mas não está
    transmitindo imagem (webcam tampada ou usada por outro programa).
    """
    capture = FakeCapture()  # entrega frames totalmente pretos
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", lambda *_args: capture)
    camera = VegaCamera(camera_index=0)
    camera.start()
    assert _wait_for_frame(camera)
    assert camera.latest_frame is not None
    camera.stop()