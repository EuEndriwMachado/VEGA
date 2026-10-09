from __future__ import annotations

import threading
import time

import cv2

from core.vision.advanced_perception import CameraCalibration, OptionalVisionFeatures

# Brilho médio abaixo do qual consideramos o quadro "escuro". Webcams entregam
# quadros quase totalmente pretos enquanto o sensor aquece; publicá-los faz a
# IA responder que está vendo uma tela preta.
_DARK_FRAME_THRESHOLD = 12.0


def _is_dark_frame(frame) -> bool:
    """True quando o quadro é praticamente preto (sensor ainda aquecendo)."""
    try:
        mean = float(frame.mean())
    except Exception:
        return False
    return mean < _DARK_FRAME_THRESHOLD


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

    # Varredura jogada em cache.
    #
    # Abrir cada índice custa ~0,4s (mesmo quando o índice não existe, o
    # DirectShow tenta e desiste). Varrer 10 índices levava 4,2 SEGUNDOS — e
    # isso rodava na thread da interface quando o painel de câmera abria, o que
    # congelava a VEGA inteira. Com o cache, reabrir o painel é instantâneo.
    _devices_cache = None
    _devices_cache_at = 0.0
    _DEVICES_CACHE_SECONDS = 20.0

    @classmethod
    def list_camera_devices(cls, max_indices=10, use_cache=True):
        """Lista as câmeras disponíveis com nome legível.

        Devolve uma lista de dicts `{"index", "label", "name"}`, usada pelo
        painel de seleção da interface. Quando o nome real do dispositivo não
        está acessível, o rótulo é numerado — nunca um nome inventado.

        O resultado é cacheado por alguns segundos: a varredura abre cada
        dispositivo em sequência e é caríssima (segundos), então não pode
        rodar a cada abertura do painel.
        """
        import time as _time

        now = _time.monotonic()
        if (
            use_cache
            and cls._devices_cache is not None
            and (now - cls._devices_cache_at) < cls._DEVICES_CACHE_SECONDS
        ):
            return [dict(item) for item in cls._devices_cache]

        devices = []
        names = cls.camera_names()
        prefer, _avoid = cls._classify_names(names) if names else ([], [])
        for index in cls.list_cameras(max_indices):
            name = names[index] if index < len(names) else cls.camera_name(index)
            if name:
                # Marca se é a webcam física ou uma câmera virtual (celular).
                is_physical = index in prefer if names else index == 0
                sufixo = "  (PC)" if is_physical else "  (celular/virtual)"
                label = f"{name}{sufixo}"
            else:
                label = f"Câmera {index}"
            devices.append({"index": index, "label": label, "name": name})

        cls._devices_cache = [dict(item) for item in devices]
        cls._devices_cache_at = now
        return devices

    @classmethod
    def invalidate_camera_cache(cls):
        """Descarta o cache de câmeras (usado por "procurar novamente")."""
        cls._devices_cache = None
        cls._devices_cache_at = 0.0

    @staticmethod
    def camera_name(index):
        """Nome do dispositivo de vídeo, quando o sistema informa.

        No Windows, o DirectShow não expõe o nome de forma simples pelo
        OpenCV. Quando não há nome disponível, devolvemos uma descrição
        honesta em vez de inventar um modelo.
        """
        try:
            from pygrabber.dshow_graph import FilterGraph  # type: ignore

            names = FilterGraph().get_input_devices()
            position = int(index)
            if 0 <= position < len(names):
                return str(names[position])
        except Exception:
            pass
        return ""

    @classmethod
    def select_camera(cls, max_indices=10):
        """Índice da câmera a usar por padrão: a WEBCAM DO PC.

        O índice NÃO é confiável: o Windows decide a ordem conforme quais
        dispositivos registram primeiro, então "Iriun Webcam" pode ficar no 0 e
        a webcam física no 1 (ou vice-versa, dependendo do dia). Antes,
        assumir "0 = PC" fazia a VEGA usar a câmera do CELULAR sem o usuário
        pedir.

        Aqui a escolha é por NOME: preferimos dispositivos que parecem webcam
        física e evitamos os que parecem câmera virtual de celular. Sem nome
        disponível, caímos no índice 0 como antes.
        """
        names = cls.camera_names()
        if names:
            prefer, avoid = cls._classify_names(names)
            if prefer:
                return prefer[0]
            # Nenhuma física identificada: usa a primeira que não seja virtual.
            for index in range(len(names)):
                if index not in avoid:
                    return index
        cameras = cls.list_cameras(max_indices)
        if not cameras:
            return 0
        return cameras[0]

    # Nomes que indicam câmera VIRTUAL (do celular ou de software). Elas são
    # úteis quando funcionam, mas não podem ser escolhidas AUTOMATICAMENTE.
    _VIRTUAL_CAMERA_HINTS = (
        "iriun", "droidcam", "epoccam", "camofusion", "ivcam", "nvidia broadcast",
        "obs virtual", "virtual camera", "manycam", "iriun webcam",
    )

    @classmethod
    def _classify_names(cls, names):
        """Separa câmeras físicas de virtuais pelos nomes dos dispositivos.

        Devolve (indices_de_fisicas, indices_de_virtuais). "Física" é "não é
        virtual": qualquer nome que não tenha marca de câmera espelhada conta
        como webcam real do computador.
        """
        prefer, avoid = [], []
        for index, name in enumerate(names):
            lowered = str(name or "").lower()
            if any(hint in lowered for hint in cls._VIRTUAL_CAMERA_HINTS):
                avoid.append(index)
            else:
                prefer.append(index)
        return prefer, avoid

    @classmethod
    def camera_names(cls):
        """Nomes dos dispositivos de vídeo, na ordem usada pelo OpenCV.

        A lista de saída do DirectShow é a mesma ordem que o OpenCV usa com
        `CAP_DSHOW`, então o índice do nome é o índice da câmera.
        """
        try:
            from pygrabber.dshow_graph import FilterGraph  # type: ignore

            return [str(name) for name in FilterGraph().get_input_devices()]
        except Exception:
            return []

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
        # Descarta o último frame.
        #
        # Sem isto, o frame da câmera ANTERIOR sobrevive ao stop() e é ele que
        # a próxima captura entrega — o usuário escolhia a câmera 1 no painel e
        # a VEGA continuava mostrando a imagem da câmera 0 ("ela usa a do PC").
        with self._lock:
            self._latest_frame = None

    def switch_camera(self, camera_index):
        """Troca a câmera, ligada ou desligada.

        BUG CORRIGIDO: antes, com a câmera DESLIGADA, o índice só ficava
        pendente em `_requested_camera_index` e `camera_index` não mudava. Como
        o pedido pendente só é consumido pelo laço de captura, uma câmera que
        nunca fosse ligada... continuava a antiga. O usuário escolhia "Câmera 1"
        no painel e a VEGA seguia usando a do PC.
        """
        try:
            index = int(camera_index)
        except (TypeError, ValueError):
            return False
        if index < 0:
            return False
        with self._lock:
            # O índice vale SEMPRE — é a fonte de verdade.
            self.camera_index = index
            if self.is_running:
                # Ligada: o laço de captura consome a troca no próximo ciclo.
                self._requested_camera_index = index
            else:
                # Desligada: nada a consumir. O índice já foi aplicado acima e
                # será usado na próxima abertura.
                self._requested_camera_index = None
            # O frame em memória é da câmera ANTERIOR: descartar é essencial.
            # Sem isto, a próxima captura entrega a imagem da câmera antiga.
            self._latest_frame = None
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

    def _warm_up(self, camera, reads=8, delay=0.03):
        """Descarta as primeiras leituras da câmera.

        Toda webcam (e especialmente câmeras virtuais, como o celular
        espelhado) entrega imagens PRETAS nos primeiros quadros, enquanto o
        sensor e o driver estabilizam a exposição. Se o primeiro frame
        publicado for um desses, a IA recebe uma tela preta e responde
        "vejo uma tela preta" — foi exatamente o que acontecia.

        Aqui descartamos algumas leituras até obter um frame com conteúdo
        real. Se todas vierem escuras, publica a última mesmo assim (melhor
        uma imagem escura que nenhuma).
        """
        last = None
        for _ in range(max(1, int(reads))):
            if not self.is_running:
                break
            success, frame = camera.read()
            if success and frame is not None:
                last = frame
                if not _is_dark_frame(frame):
                    return frame
            time.sleep(delay)
        return last

    def _publish_first_frame(self, camera):
        """Aquece a câmera e PUBLICA o primeiro frame, sempre.

        Importante: se todos os quadros vierem escuros (quarto sem luz, ou uma
        câmera que não está transmitindo de verdade), o último é publicado
        mesmo assim. Descartar tudo deixaria `latest_frame` vazio para sempre e
        a VEGA diria que não conseguiu acessar a câmera — pior do que mostrar a
        imagem escura, porque esconde a causa real do usuário.
        """
        first = self._warm_up(camera)
        if first is None:
            return False
        first = cv2.flip(first, 1)
        with self._lock:
            self._latest_frame = first.copy()
        return True

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

        # Aquece ANTES de publicar qualquer frame: sem isto, o primeiro frame
        # era preto e a VEGA respondia que estava vendo uma tela escura.
        self._publish_first_frame(camera)

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