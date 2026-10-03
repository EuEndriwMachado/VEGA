"""Local face reference enrollment and validation for sensitive actions."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional


class LocalFaceValidator:
    """Compares normalized face crops without storing clothing or background."""

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

    def __init__(self, reference_path="data/face_reference.json", threshold=0.5):
        self.reference_path = Path(reference_path)
        self.threshold = float(threshold)
        self._reference: Optional[list[float]] = self._load_reference()

    def _load_reference(self):
        try:
            import json
            payload = json.loads(self.reference_path.read_text(encoding="utf-8"))
            reference = payload.get("embedding")
            return [float(value) for value in reference] if reference else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    @property
    def ready(self):
        return bool(self._reference)

    @staticmethod
    def _detector():
        import cv2
        model_path = Path(__file__).resolve().parents[2] / "models" / "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(str(model_path))
        if detector.empty():
            raise RuntimeError(f"Modelo facial não encontrado: {model_path}")
        return detector

    @staticmethod
    def _detectors():
        import cv2
        model_dir = Path(__file__).resolve().parents[2] / "models"
        detectors = []
        for filename in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
            detector = cv2.CascadeClassifier(str(model_dir / filename))
            if not detector.empty():
                detectors.append(detector)
        if not detectors:
            raise RuntimeError(f"Modelos faciais não encontrados: {model_dir}")
        return detectors

    @classmethod
    def _embedding(cls, image):
        import cv2
        import numpy as np

        if image is None:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        candidates = []
        for detector in cls._detectors():
            for candidate_image in (gray, cv2.flip(gray, 1)):
                faces = detector.detectMultiScale(candidate_image, scaleFactor=1.05, minNeighbors=3, minSize=(45, 45))
                candidates.extend((candidate_image, face) for face in faces)
        if not candidates:
            return None
        candidate_image, (x, y, width, height) = max(candidates, key=lambda item: int(item[1][2]) * int(item[1][3]))
        crop = candidate_image[max(0, y):y + height, max(0, x):x + width]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)
        crop = cv2.equalizeHist(crop).astype(np.float32) / 255.0
        vector = crop.reshape(-1)
        norm = float(np.linalg.norm(vector))
        return (vector / norm).tolist() if norm else None

    @classmethod
    def enroll_from_directory(cls, images_dir="Uploads", reference_path="data/face_reference.json"):
        import cv2
        import numpy as np

        directory = Path(images_dir)
        embeddings = []
        for image_path in sorted(directory.iterdir()) if directory.exists() else []:
            if image_path.suffix.lower() not in cls.IMAGE_EXTENSIONS:
                continue
            image = cv2.imread(str(image_path))
            embedding = cls._embedding(image)
            if embedding:
                embeddings.append(embedding)
        if not embeddings:
            raise ValueError("Nenhuma foto com rosto detectável foi encontrada.")

        average = np.mean(np.asarray(embeddings, dtype=np.float32), axis=0)
        norm = float(np.linalg.norm(average))
        if not norm:
            raise ValueError("Não foi possível criar a referência facial.")
        reference = (average / norm).tolist()
        target = Path(reference_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"embedding": reference, "samples": len(embeddings)}, indent=2),
            encoding="utf-8",
        )
        return {"samples": len(embeddings), "path": str(target)}

    def validate_image(self, image):
        if not self.ready:
            return None
        distance = self._distance(image)
        return distance is not None and distance <= self.threshold

    def _distance(self, image):
        embedding = self._embedding(image)
        if not embedding or len(embedding) != len(self._reference):
            return None
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(embedding, self._reference)))

    def validate_camera(self, camera_index=0):
        try:
            import cv2
            camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera.release()
                return False, None
            success, frame = camera.read()
            camera.release()
            return bool(success), frame if success else None
        except Exception:
            return False, None

    def validate_camera_sequence(self, camera_index=0, samples=5):
        """Read several frames and require a majority match for stability."""
        try:
            import cv2
            camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera.release()
                return False, False, None

            frames = []
            for _ in range(max(1, int(samples))):
                success, frame = camera.read()
                if success and frame is not None:
                    frames.append(frame)
            camera.release()
            if not frames:
                return False, False, None

            scored = [(self._distance(frame), frame) for frame in frames]
            valid = [item for item in scored if item[0] is not None and item[0] <= self.threshold]
            best_distance, best_frame = min(
                (item for item in scored if item[0] is not None),
                key=lambda item: item[0],
                default=(None, frames[-1]),
            )
            required = max(2, (len(frames) // 2) + 1)
            return True, len(valid) >= required, best_frame
        except Exception:
            return False, False, None
