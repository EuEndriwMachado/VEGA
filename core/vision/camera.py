from __future__ import annotations

import threading
import time

import cv2

from core.vision.advanced_perception import CameraCalibration, OptionalVisionFeatures


class VegaCamera:
    """Captura frames e mantém ferramentas de análise visual não gestual."""

    def __init__(self, camera_index=0, width=1280, height=720):
        self.camera_index = self.select_camera() if camera_index is None else int(camera_index)
        self.width = int(width)
        self.height = int(height)
        self.window_name = "VEGA Camera"
        self.show_camera = False
        self.is_running = False
        self._thread = None
        self._lock = threading.Lock()
        self._latest_frame = None
        self._frame_callback = None
        self._requested_camera_index = None
        self.calibration = CameraCalibration()
        self.advanced_perception = OptionalVisionFeatures()

    @property
    def latest_frame(self):
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    @staticmethod
    def list_cameras(max_indices=10):
        cameras = []
        backend = getattr(cv2, "CAP_DSHOW", 0)
        for index in range(max_indices):
            camera = cv2.VideoCapture(index, backend)
            try:
                if camera.isOpened():
                    cameras.append(index)
            finally:
                camera.release()
        return cameras

    @classmethod
    def select_camera(cls, max_indices=10):
        cameras = cls.list_cameras(max_indices)
        return cameras[0] if cameras else 0

    def set_frame_callback(self, callback):
        self._frame_callback = callback

    def start(self, show_camera=False):
        if self.is_running:
            self.show_camera = bool(show_camera)
            return
        self.show_camera = bool(show_camera)
        with self._lock:
            self._latest_frame = None
        self.is_running = True
        self._thread = threading.Thread(
            target=self._camera_loop,
            name="VegaCameraCapture",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self.is_running = False
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread is None or not thread.is_alive():
            self._thread = None

    def switch_camera(self, camera_index):
        try:
            index = int(camera_index)
        except (TypeError, ValueError):
            return False
        if index < 0:
            return False
        with self._lock:
            self._requested_camera_index = index
            if not self.is_running:
                self.camera_index = index
        return True

    def next_camera(self):
        cameras = self.list_cameras()
        if not cameras:
            return None
        try:
            position = cameras.index(self.camera_index)
        except ValueError:
            position = -1
        index = cameras[(position + 1) % len(cameras)]
        return index if self.switch_camera(index) else None

    def _open_camera(self, index):
        backend = getattr(cv2, "CAP_DSHOW", 0)
        camera = cv2.VideoCapture(index, backend)
        if camera.isOpened():
            return camera
        camera.release()
        camera = cv2.VideoCapture(index)
        return camera

    def _camera_loop(self):
        camera = self._open_camera(self.camera_index)
        if not camera.isOpened():
            print("[VEGA Camera] Não foi possível abrir a câmera.")
            camera.release()
            self.is_running = False
            return

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        try:
            while self.is_running:
                with self._lock:
                    requested_index = self._requested_camera_index
                    self._requested_camera_index = None

                if requested_index is not None:
                    camera.release()
                    camera = self._open_camera(requested_index)
                    if not camera.isOpened():
                        print(f"[VEGA Camera] Não foi possível abrir a câmera {requested_index}.")
                        break
                    self.camera_index = requested_index
                    camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                success, frame = camera.read()
                if not success or frame is None:
                    time.sleep(0.01)
                    continue

                frame = cv2.flip(frame, 1)
                with self._lock:
                    self._latest_frame = frame.copy()

                if self._frame_callback is not None:
                    try:
                        self._frame_callback(frame.copy())
                    except Exception as error:
                        print(f"[VEGA Camera] Erro no callback de frame: {error}")

                if self.show_camera:
                    cv2.imshow(self.window_name, frame)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break
        except Exception as error:
            print(f"[VEGA Camera] Erro na captura: {error}")
        finally:
            camera.release()
            if self.show_camera:
                try:
                    cv2.destroyWindow(self.window_name)
                except Exception:
                    pass
            self.is_running = False

    def analyze_image_text(self, image):
        return self.advanced_perception.ocr(image)

    def detect_image_objects(self, image):
        return self.advanced_perception.detect_objects(image)

    def detect_image_faces(self, image):
        return self.advanced_perception.detect_faces(image)

    def register_face_profile(self, name, embedding):
        return self.advanced_perception.register_face(name, embedding)

    def match_face_profile(self, embedding, threshold=0.75):
        return self.advanced_perception.match_face(embedding, threshold)

    def lighting_score(self, image):
        return self.advanced_perception.lighting_score(image)

    def estimate_head_pose(self, face_landmarks):
        return self.advanced_perception.head_pose(face_landmarks)