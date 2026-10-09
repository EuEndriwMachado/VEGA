# -*- coding: utf-8 -*-
"""Câmera: aquecimento, escolha do dispositivo e menu de seleção.

Garante:
  1. a câmera padrão é a do PC (índice 0), nunca a do celular por acidente;
  2. frames pretos (sensor aquecendo) são descartados antes de publicar;
  3. a lista de dispositivos é honesta (nunca inventa nome de câmera);
  4. o menu é um widget próprio — não é o orbe.
"""

from unittest.mock import patch

import pytest

from core.vision.camera import VegaCamera, _is_dark_frame


# ------------------------------------------------------------------
# 1. Câmera padrão = a do PC
# ------------------------------------------------------------------

def test_default_camera_prefers_the_physical_webcam_over_the_phone():
    """O padrão é a webcam do PC — mesmo quando ela NÃO está no índice 0.

    A ordem dos índices é decidida pelo Windows conforme os dispositivos
    registram. Na máquina do usuário a Iriun (celular) ficou no 0 e a webcam
    física no 1; assumir "0 = PC" fazia a VEGA usar a câmera do CELULAR sem
    ninguém pedir.
    """
    with patch.object(
        VegaCamera,
        "camera_names",
        return_value=["Iriun Webcam", "USB2.0 HD UVC WebCam"],
    ), patch.object(VegaCamera, "list_cameras", return_value=[0, 1]):
        assert VegaCamera.select_camera() == 1


def test_default_camera_prefers_zero_when_it_is_physical():
    with patch.object(
        VegaCamera,
        "camera_names",
        return_value=["USB2.0 HD UVC WebCam", "Iriun Webcam"],
    ), patch.object(VegaCamera, "list_cameras", return_value=[0, 1]):
        assert VegaCamera.select_camera() == 0


def test_default_camera_without_names_falls_back_to_zero():
    """Sem nomes disponíveis, mantém o comportamento antigo (índice 0)."""
    with patch.object(VegaCamera, "camera_names", return_value=[]), \
         patch.object(VegaCamera, "list_cameras", return_value=[0, 1]):
        assert VegaCamera.select_camera() == 0


def test_default_camera_with_no_devices():
    with patch.object(VegaCamera, "camera_names", return_value=[]), \
         patch.object(VegaCamera, "list_cameras", return_value=[]):
        assert VegaCamera.select_camera() == 0


def test_virtual_cameras_are_classified_as_such():
    prefer, avoid = VegaCamera._classify_names(
        ["Iriun Webcam", "USB2.0 HD UVC WebCam", "OBS Virtual Camera"]
    )
    assert 0 in avoid  # Iriun = celular
    assert 2 in avoid  # OBS = virtual
    assert prefer == [1]


# ------------------------------------------------------------------
# 2. Aquecimento (frames pretos)
# ------------------------------------------------------------------

def test_dark_frame_is_detected():
    import numpy as np

    assert _is_dark_frame(np.zeros((10, 10, 3), dtype="uint8")) is True


def test_bright_frame_is_not_dark():
    import numpy as np

    bright = np.full((10, 10, 3), 200, dtype="uint8")
    assert _is_dark_frame(bright) is False


def test_dark_detection_is_safe_with_garbage():
    assert _is_dark_frame("nao e imagem") is False


def test_warm_up_skips_dark_frames():
    """O aquecimento descarta os quadros pretos e devolve um com conteúdo."""
    import numpy as np

    dark = np.zeros((8, 8, 3), dtype="uint8")
    bright = np.full((8, 8, 3), 180, dtype="uint8")

    class FakeCapture:
        def __init__(self):
            self.frames = [dark, dark, bright]

        def read(self):
            frame = self.frames.pop(0) if self.frames else bright
            return True, frame

    camera = VegaCamera.__new__(VegaCamera)
    camera.is_running = True
    result = camera._warm_up(FakeCapture(), reads=5, delay=0)
    assert result is not None
    assert float(result.mean()) > 100


def test_warm_up_returns_last_frame_when_all_dark():
    """Se todas vierem escuras, devolve a última (melhor que nada)."""
    import numpy as np

    dark = np.full((8, 8, 3), 5, dtype="uint8")

    class FakeCapture:
        def read(self):
            return True, dark

    camera = VegaCamera.__new__(VegaCamera)
    camera.is_running = True
    result = camera._warm_up(FakeCapture(), reads=3, delay=0)
    assert result is not None


# ------------------------------------------------------------------
# 3. Lista de dispositivos
# ------------------------------------------------------------------

def test_device_list_has_index_and_label():
    devices = VegaCamera.list_camera_devices(max_indices=0)
    assert devices == [] or all({"index", "label"} <= set(item) for item in devices)


def test_device_list_never_invents_a_name():
    """Sem nome real do sistema, o rótulo é numerado."""
    with patch.object(VegaCamera, "list_cameras", return_value=[0, 3]), \
         patch.object(VegaCamera, "camera_names", return_value=[]), \
         patch.object(VegaCamera, "camera_name", return_value=""):
        devices = VegaCamera.list_camera_devices(use_cache=False)
    assert devices[0]["name"] == ""
    assert "Câmera 0" in devices[0]["label"]
    assert "Câmera 3" in devices[1]["label"]


def test_device_list_uses_real_name_when_available():
    with patch.object(VegaCamera, "list_cameras", return_value=[0]), \
         patch.object(
             VegaCamera,
             "camera_names",
             return_value=["USB2.0 HD UVC WebCam"],
         ):
        devices = VegaCamera.list_camera_devices(use_cache=False)
    assert devices[0]["name"] == "USB2.0 HD UVC WebCam"
    assert "USB2.0 HD UVC WebCam" in devices[0]["label"]
    assert "(PC)" in devices[0]["label"]


def test_device_list_marks_the_phone_camera():
    """A câmera do celular aparece marcada, para não confundir a escolha."""
    with patch.object(VegaCamera, "list_cameras", return_value=[0, 1]), \
         patch.object(
             VegaCamera,
             "camera_names",
             return_value=["Iriun Webcam", "USB2.0 HD UVC WebCam"],
         ):
        devices = VegaCamera.list_camera_devices(use_cache=False)
    assert "celular" in devices[0]["label"]
    assert "(PC)" in devices[1]["label"]


def test_device_list_is_cached():
    """A varredura é caríssima (abre cada dispositivo) — precisa de cache.

    Sem cache, abrir o painel custava 4,2 SEGUNDOS na thread da interface e
    congelava a VEGA inteira.
    """
    VegaCamera.invalidate_camera_cache()
    with patch.object(VegaCamera, "list_cameras", return_value=[0]) as scan, \
         patch.object(VegaCamera, "camera_name", return_value=""):
        VegaCamera.list_camera_devices()
        VegaCamera.list_camera_devices()
    assert scan.call_count == 1
    VegaCamera.invalidate_camera_cache()


def test_invalidating_cache_forces_a_new_scan():
    VegaCamera.invalidate_camera_cache()
    with patch.object(VegaCamera, "list_cameras", return_value=[0]) as scan, \
         patch.object(VegaCamera, "camera_name", return_value=""):
        VegaCamera.list_camera_devices()
        VegaCamera.invalidate_camera_cache()
        VegaCamera.list_camera_devices()
    assert scan.call_count == 2
    VegaCamera.invalidate_camera_cache()


# ------------------------------------------------------------------
# 4. O menu é independente do orbe
# ------------------------------------------------------------------

def test_camera_menu_is_a_native_qmenu():
    """O menu é um `QMenu` do Qt — não um widget desenhado à mão.

    Três tentativas anteriores falharam: `Qt.Popup` travava a VEGA, janela
    flutuante sumia no meio do clique, e o widget filho virava uma "barrinha"
    porque a geometria era medida errado em janela frameless. O `QMenu` calcula
    o próprio tamanho e posiciona-se sozinho.
    """
    from PySide6.QtWidgets import QApplication, QMenu

    QApplication.instance() or QApplication([])
    from config.camera_menu import CameraMenu
    from config.interface import InterfaceVEGA

    assert issubclass(CameraMenu, QMenu)
    assert not issubclass(CameraMenu, InterfaceVEGA)


def test_menu_has_real_height_with_cameras():
    """Com câmeras listadas, o menu tem altura de menu (não uma tirinha)."""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from config.camera_menu import CameraMenu

    menu = CameraMenu()
    menu.populate([
        {"index": 0, "label": "Iriun Webcam (celular)", "name": "Iriun Webcam"},
        {"index": 1, "label": "USB2.0 HD UVC WebCam (PC)", "name": "USB"},
    ], current_index=1)
    assert menu.sizeHint().height() > 50


def test_menu_lists_every_camera_and_marks_the_current():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from config.camera_menu import CameraMenu

    menu = CameraMenu()
    menu.populate([
        {"index": 0, "label": "Camera 0", "name": ""},
        {"index": 1, "label": "Camera 1", "name": ""},
    ], current_index=1)
    texts = [
        a.text() for a in menu.actions()
        if not a.isSeparator() and a.isEnabled()
    ]
    assert any("Camera 0" in t for t in texts)
    assert any("Camera 1" in t for t in texts)


def test_menu_emits_the_chosen_index():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from config.camera_menu import CameraMenu

    menu = CameraMenu()
    menu.populate([{"index": 3, "label": "Camera 3", "name": ""}], current_index=None)
    recebidos = []
    menu.camera_selected.connect(recebidos.append)
    for action in menu.actions():
        if not action.isSeparator() and action.isEnabled():
            action.trigger()
            break
    assert recebidos == [3]


def test_empty_menu_shows_a_placeholder_not_a_crash():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from config.camera_menu import CameraMenu

    menu = CameraMenu()
    menu.populate([], current_index=None)
    assert menu.sizeHint().height() > 20


def test_camera_preference_defaults_to_none():
    """Sem escolha salva, a VEGA usa a webcam do PC (o padrão)."""
    import os

    from config.camera_menu import PREFERENCE_PATH, load_preferred_camera

    if os.path.exists(PREFERENCE_PATH):
        os.remove(PREFERENCE_PATH)
    assert load_preferred_camera() is None


def test_camera_preference_round_trip():
    import os

    from config.camera_menu import (
        PREFERENCE_PATH,
        load_preferred_camera,
        save_preferred_camera,
    )

    try:
        save_preferred_camera(1)
        assert load_preferred_camera() == 1
        save_preferred_camera(0)
        assert load_preferred_camera() == 0
    finally:
        if os.path.exists(PREFERENCE_PATH):
            os.remove(PREFERENCE_PATH)


# ------------------------------------------------------------------
# 5. Troca de câmera (bug: escolher no painel não mudava nada)
# ------------------------------------------------------------------

def _bare_camera():
    """VegaCamera sem abrir hardware."""
    import threading

    camera = VegaCamera.__new__(VegaCamera)
    camera.camera_index = 0
    camera.is_running = False
    camera._requested_camera_index = None
    camera._lock = threading.Lock()
    return camera


def test_switch_camera_updates_index_when_stopped():
    """Com a câmera DESLIGADA, o índice precisa mudar na hora.

    Antes, o pedido ficava pendente em `_requested_camera_index` esperando o
    laço de captura — que só existe quando a câmera está ligada. O usuário
    escolhia "Câmera 1" no painel e a VEGA seguia usando a do PC.
    """
    camera = _bare_camera()
    assert camera.switch_camera(1) is True
    assert camera.camera_index == 1
    assert camera._requested_camera_index is None


def test_switch_camera_queues_index_when_running():
    """Com a câmera LIGADA, a troca é aplicada pelo laço de captura."""
    camera = _bare_camera()
    camera.is_running = True
    assert camera.switch_camera(2) is True
    assert camera.camera_index == 2
    assert camera._requested_camera_index == 2


def test_switch_camera_rejects_invalid_values():
    camera = _bare_camera()
    assert camera.switch_camera("nao e numero") is False
    assert camera.switch_camera(-1) is False
    assert camera.camera_index == 0


# ------------------------------------------------------------------
# 6. Falsos positivos de ferramenta (frase aleatória virava chamada)
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def tool_manager():
    from tools.api_tools import build_tool_registry

    return build_tool_registry()


@pytest.mark.parametrize("frase", [
    "que dia bonito hoje",
    "que dia voce nasceu",
    "que horas o filme comeca",
    "que horas a loja abre",
    "que dia ele chegou",
])
def test_casual_speech_does_not_call_a_tool(tool_manager, frase):
    """Frase que só MENCIONA dia/hora não pode virar consulta.

    O filtro de intenção existia, mas a pontuação por tags escolhia a
    ferramenta de qualquer forma ("dia" está nas tags de current_date).
    """
    assert tool_manager.resolve_for_text(frase) is None


@pytest.mark.parametrize("frase,esperado", [
    ("que horas sao", "current_time"),
    ("que horas sao agora", "current_time"),
    ("que dia e hoje", "current_date"),
    ("qual a data de hoje", "current_date"),
])
def test_real_time_and_date_questions_still_work(tool_manager, frase, esperado):
    tool = tool_manager.resolve_for_text(frase)
    assert tool is not None
    assert tool.name == esperado


@pytest.mark.parametrize("frase", [
    "eu gosto de musica",
    "meu filme favorito e bom",
    "bom dia vega",
    "hoje foi um dia cansativo",
])
def test_conversation_is_never_a_tool_call(tool_manager, frase):
    assert tool_manager.resolve_for_text(frase) is None


# ==============================================================
# CÂMERA DA VALIDAÇÃO FACIAL (segurança)
# ==============================================================
# A validação facial é o que autoriza desligar o PC, apagar arquivos e
# controlar o celular. Usar a câmera ERRADA é grave: quanto a lista era
# `[0, 1]` fixa, a Iriun (câmera do CELULAR) ficava no índice 0 em algumas
# máquinas e era ela que validava o rosto — podendo autorizar outra pessoa.

class TestCamerasDaValidacaoFacial:
    def _security(self):
        from core.security.security import VegaSecurity

        return VegaSecurity.__new__(VegaSecurity)

    def test_first_camera_is_the_pc_webcam_not_the_phone(self, monkeypatch):
        """A PRIMEIRA câmera tentada é a webcam física do PC.

        Usa a máquina real do usuário como referência: a Iriun (celular)
        ocupa o índice 0 e a webcam física o índice 1.
        """
        from core.vision.camera import VegaCamera

        monkeypatch.setattr(
            VegaCamera, "camera_names",
            staticmethod(lambda: ["Iriun Webcam", "USB2.0 HD UVC WebCam"]),
        )
        monkeypatch.setattr(VegaCamera, "list_cameras", staticmethod(lambda *a: [0, 1]))

        ordem = self._security()._face_camera_candidates()
        assert ordem[0] == 1, "a validação facial não pode começar pela câmera do celular"

    def test_explicit_camera_index_wins(self):
        """Se o chamador informa a câmera, ela é a única tentada."""
        assert self._security()._face_camera_candidates(2) == [2]

    def test_user_choice_wins_over_automatic(self, monkeypatch):
        """A escolha do menu tem prioridade sobre a heurística por nome."""
        from config import camera_menu

        monkeypatch.setattr(camera_menu, "load_preferred_camera", lambda: 0)
        ordem = self._security()._face_camera_candidates()
        assert ordem[0] == 0

    def test_fallback_list_never_repeats(self, monkeypatch):
        from core.vision.camera import VegaCamera

        monkeypatch.setattr(
            VegaCamera, "camera_names",
            staticmethod(lambda: ["Iriun Webcam", "USB2.0 HD UVC WebCam"]),
        )
        monkeypatch.setattr(VegaCamera, "list_cameras", staticmethod(lambda *a: [0, 1]))
        ordem = self._security()._face_camera_candidates()
        assert len(ordem) == len(set(ordem))


class TestQuadrosPretosNaoAutorizam:
    def test_blank_frames_are_not_supported(self, monkeypatch):
        """Câmera que só entrega quadro preto NÃO conta como utilizável.

        Antes, `supported` era True só por a câmera ter aberto — a validação
        parava nela, falhava o rosto e nunca tentava a webcam boa.
        """
        import numpy as np

        from core.security.face_security import LocalFaceValidator

        class BlackCamera:
            def isOpened(self):
                return True

            def read(self):
                return True, np.zeros((48, 64, 3), dtype=np.uint8)

            def release(self):
                pass

        monkeypatch.setattr("cv2.VideoCapture", lambda *a, **k: BlackCamera())
        validator = LocalFaceValidator.__new__(LocalFaceValidator)
        supported, face_valid, frame = validator.validate_camera_sequence(0)
        assert supported is False
        assert face_valid is False

    def test_usable_frames_are_reported_as_supported(self, monkeypatch):
        """Câmera com imagem real é utilizável (mesmo sem rosto reconhecido)."""
        import numpy as np

        from core.security.face_security import LocalFaceValidator

        class BrightCamera:
            def isOpened(self):
                return True

            def read(self):
                return True, np.full((48, 64, 3), 180, dtype=np.uint8)

            def release(self):
                pass

        monkeypatch.setattr("cv2.VideoCapture", lambda *a, **k: BrightCamera())
        validator = LocalFaceValidator.__new__(LocalFaceValidator)
        supported, face_valid, frame = validator.validate_camera_sequence(0)
        assert supported is True
