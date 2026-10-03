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
    camera.stop()

    assert camera.latest_frame.shape == (12, 16, 3)
    assert capture.released
    assert not camera.is_running