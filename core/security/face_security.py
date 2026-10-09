"""Local face reference enrollment and validation for sensitive actions."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

# Brilho médio abaixo do qual o quadro é considerado INÚTIL para validação.
# Webcams entregam quadros quase pretos enquanto o sensor aquece; comparar o
# rosto do dono com uma imagem preta reprovava a própria pessoa.
_BLANK_FRAME_THRESHOLD = 8.0


def _is_blank(frame) -> bool:
    """True quando o quadro é praticamente preto (sem informação útil)."""
    try:
        import cv2

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        return float(gray.mean()) < _BLANK_FRAME_THRESHOLD
    except Exception:
        return False


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
    def _supports_haar():
        """O OpenCV instalado tem Haar cascade?

        O OpenCV 5 REMOVEU o `CascadeClassifier`. Verificar isso antes de
        usar evita o AttributeError que derrubava toda a cadeia facial (a
        câmera nem ligava e nenhuma evidência era gravada).
        """
        try:
            import cv2
            return hasattr(cv2, "CascadeClassifier")
        except Exception:
            return False

    @staticmethod
    def _detector():
        import cv2
        model_path = Path(__file__).resolve().parents[2] / "models" / "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(str(model_path))
        if detector.empty():
            raise RuntimeError(f"Modelo facial não encontrado: {model_path}")
        return detector

    #: Detectores já carregados, reaproveitados entre chamadas.
    #: Recarregar o modelo a cada captura era desperdício (o modelo é
    #: grande) e, em uso repetido, esgotava a memória do processo.
    _detectors_cache = None

    @classmethod
    def _detectors(cls):
        """Detectores de rosto disponíveis, sem quebrar em nenhum OpenCV.

        - OpenCV 4.x: usa os Haar cascades de `models/` (comportamento
          original, mantendo a `face_reference.json` já enrolada válida).
        - OpenCV 5+: os cascades não existem mais; cai para o detector DNN
          (`FaceDetectorYN`), que é mais preciso.

        O resultado é memorizado: carregar o modelo é caro e não muda
        enquanto o processo vive.
        """
        import cv2

        if cls._detectors_cache is not None:
            return cls._detectors_cache

        if cls._supports_haar():
            model_dir = Path(__file__).resolve().parents[2] / "models"
            detectors = []
            for filename in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
                detector = cv2.CascadeClassifier(str(model_dir / filename))
                if not detector.empty():
                    detectors.append(detector)
            if detectors:
                cls._detectors_cache = detectors
                return detectors

        # OpenCV 5 (ou cascades ausentes): detector baseado em rede neural.
        dnn = cls._dnn_detector()
        if dnn is not None:
            cls._detectors_cache = [dnn]
            return cls._detectors_cache

        raise RuntimeError(
            "Nenhum detector facial disponível. Instale opencv-python 4.x "
            "(com Haar cascade) ou baixe o modelo YuNet para o OpenCV 5."
        )

    # Modelo YuNet usado pelo FaceDetectorYN (OpenCV 5+). É baixado uma vez
    # e guardado em models/, junto dos cascades.
    _YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
    _YUNET_URL = (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    )

    @classmethod
    def _dnn_detector(cls):
        """Cria o FaceDetectorYN do OpenCV 5, baixando o modelo se preciso."""
        try:
            import cv2
            if not hasattr(cv2, "FaceDetectorYN"):
                return None
            model_path = Path(__file__).resolve().parents[2] / "models" / cls._YUNET_FILENAME
            if not model_path.exists():
                model_path.parent.mkdir(parents=True, exist_ok=True)
                if not cls._download_yunet(model_path):
                    return None
            return cv2.FaceDetectorYN.create(
                str(model_path), "", (320, 320),
                score_threshold=0.7, nms_threshold=0.3, top_k=50,
            )
        except Exception:
            return None

    @staticmethod
    def _download_yunet(target) -> bool:
        """Baixa o modelo YuNet uma única vez (best effort)."""
        try:
            import requests
            response = requests.get(LocalFaceValidator._YUNET_URL, timeout=60)
            if response.status_code == 200 and response.content:
                target.write_bytes(response.content)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _detect_faces(detector, image):
        """Detecta rostos com Haar (OpenCV 4) ou YuNet (OpenCV 5).

        Devolve uma lista de retângulos (x, y, w, h).
        """
        if hasattr(detector, "detectMultiScale"):
            return list(
                detector.detectMultiScale(
                    image, scaleFactor=1.05, minNeighbors=3, minSize=(45, 45)
                )
            )

        # FaceDetectorYN: precisa da imagem em BGR e do tamanho ajustado.
        import cv2
        if len(image.shape) == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        height, width = image.shape[:2]
        detector.setInputSize((width, height))
        _ok, faces = detector.detect(image)
        if faces is None:
            return []
        # Cada linha: x, y, w, h, 5 landmarks, score.
        return [(int(face[0]), int(face[1]), int(face[2]), int(face[3])) for face in faces]

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
                faces = cls._detect_faces(detector, candidate_image)
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
        """Lê vários quadros da câmera e valida o rosto contra a referência.

        Devolve (supported, face_valid, best_frame):

        - `supported` = a câmera abriu E entregou quadros utilizáveis. É o que
          autoriza tentar esta câmera; se for False, o chamador deve tentar a
          PRÓXIMA da lista.
        - `face_valid` = o rosto bateu com a referência em pelo menos um quadro.
        """
        try:
            import cv2
            camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera.release()
                return False, False, None

            # Descarta os primeiros quadros: a webcam começa entregando imagem
            # preta enquanto o sensor estabiliza. Poucos descartes faziam a
            # validação comparar um quadro PRETO e reprovar o próprio dono.
            for _ in range(5):
                camera.read()
                time.sleep(0.04)

            frames = []
            for _ in range(max(1, int(samples))):
                success, frame = camera.read()
                if success and frame is not None:
                    frames.append(frame)
                time.sleep(0.03)
            camera.release()
            if not frames:
                return False, False, None

            # Quadros totalmente pretos não servem para validar NEM para
            # procurar rosto: se todos forem assim, esta câmera não é útil.
            usable = [frame for frame in frames if not _is_blank(frame)]
            if not usable:
                return False, False, None

            scored = [(self._distance(frame), frame) for frame in usable]
            valid = [item for item in scored if item[0] is not None and item[0] <= self.threshold]
            best_distance, best_frame = min(
                (item for item in scored if item[0] is not None),
                key=lambda item: item[0],
                default=(None, usable[-1]),
            )
            # Basta UMA leitura boa: exigir maioria reprovava quem se mexeu
            # durante a captura, mesmo com o rosto aparecendo em várias fotos.
            return True, len(valid) >= 1, best_frame
        except Exception:
            return False, False, None
