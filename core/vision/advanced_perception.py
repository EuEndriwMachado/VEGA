"""Optional perception services that remain usable without camera hardware."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


class CameraCalibration:
    def __init__(self, path="data/camera_calibration.json"):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"camera_index": None, "brightness": 0.5}

    def save(self, **values):
        self.data.update(values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(self.data)

class OptionalVisionFeatures:
    """OCR, object and face-analysis hooks with graceful fallbacks."""
    def __init__(self, face_path="data/face_profiles.json"):
        self.face_path = Path(face_path)
        self.face_profiles = self._load(self.face_path)

    @staticmethod
    def _load(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def register_face(self, name, embedding):
        if not name or not embedding:
            raise ValueError("Nome e assinatura facial são obrigatórios.")
        self.face_profiles[str(name)] = list(embedding)
        self.face_path.parent.mkdir(parents=True, exist_ok=True)
        self.face_path.write_text(json.dumps(self.face_profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.face_profiles[str(name)]

    def match_face(self, embedding, threshold=0.75):
        if not embedding:
            return None
        best_name = None
        best_score = 0.0
        for name, saved in self.face_profiles.items():
            if len(saved) != len(embedding):
                continue
            distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(saved, embedding)))
            score = 1.0 / (1.0 + distance)
            if score > best_score:
                best_name, best_score = name, score
        return {"name": best_name, "confidence": best_score} if best_name and best_score >= threshold else None

    @staticmethod
    def lighting_score(image):
        try:
            import cv2
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            return float(gray.mean()) / 255.0
        except Exception:
            return 0.5

    @staticmethod
    def ocr(image):
        try:
            import pytesseract
            return pytesseract.image_to_string(image, lang="por+eng").strip()
        except Exception:
            return ""

    @staticmethod
    def detect_objects(image):
        """Return an empty result unless an optional detector is installed."""
        try:
            from ultralytics import YOLO
            model = YOLO("yolo11n.pt")
            result = model(image, verbose=False)[0]
            return [{"label": result.names[int(box.cls[0])], "confidence": float(box.conf[0])} for box in result.boxes]
        except Exception:
            return []

    @staticmethod
    def detect_faces(image):
        try:
            import cv2
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cascade.detectMultiScale(gray, 1.1, 5)
            return [{"x": int(x), "y": int(y), "width": int(w), "height": int(h)} for x, y, w, h in faces]
        except Exception:
            return []


    @staticmethod
    def head_pose(face_landmarks):
        if not face_landmarks or len(face_landmarks) < 2:
            return {"present": False}
        left = face_landmarks[0]
        right = face_landmarks[1]
        return {"present": True, "yaw": float(right[0]) - float(left[0]), "gaze": "right" if right[0] > left[0] else "left"}


class ScenePersistence:
    @staticmethod
    def save(scene, path):
        payload = []
        for obj in scene.objects():
            payload.append({"object_id": obj.object_id, "position": obj.position, "scale": obj.scale, "rotation": obj.rotation, "properties": obj.properties})
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(target)

    @staticmethod
    def load(scene, path, object_factory):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in payload:
            obj = object_factory(item)
            scene.add(obj)
        return len(payload)
