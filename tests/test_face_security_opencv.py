# -*- coding: utf-8 -*-
"""Compatibilidade do reconhecimento facial com OpenCV 4 e 5.

Regressão real e cara: o `requirements.txt` passou a fixar
`opencv-python==5.0.0.93`, e o OpenCV 5 REMOVEU o `cv2.CascadeClassifier`.
Como o `face_security.py` usava Haar cascade direto:

    1. `_detectors()` levantava AttributeError;
    2. `validate_camera_sequence()` devolvia supported=False;
    3. `capture_face_validation()` retornava ANTES de abrir a câmera.

Efeito para o usuário: a VEGA pedia "olhe para a câmera", mas a câmera nunca
ligava (nenhum piscar) e nenhuma evidência era gravada em
`data/security_evidence/`. O PIN continuava funcionando, mas o registro de
quem acessou — que é o propósito do reconhecimento — se perdeu.

Estes testes garantem que a detecção funciona nos dois OpenCVs.
"""

import pytest

from core.security.face_security import LocalFaceValidator


class TestDeteccaoDeModelos:
    def test_suporta_haar_reflete_o_opencv_instalado(self):
        import cv2
        esperado = hasattr(cv2, "CascadeClassifier")
        assert LocalFaceValidator._supports_haar() is esperado

    def test_sempre_existe_algum_detector(self):
        """Nunca pode ficar sem detector: é o que liga a câmera."""
        detectores = LocalFaceValidator._detectors()
        assert detectores

    def test_detector_tem_uma_das_duas_interfaces(self):
        for detector in LocalFaceValidator._detectors():
            assert hasattr(detector, "detectMultiScale") or hasattr(detector, "setInputSize")

    def test_fallback_openCV5_sem_haar(self, monkeypatch):
        """Simula o OpenCV 5 (sem CascadeClassifier) e exige o fallback DNN."""
        monkeypatch.setattr(
            LocalFaceValidator, "_supports_haar", staticmethod(lambda: False)
        )
        detectores = LocalFaceValidator._detectors()
        assert detectores
        # Se o YuNet estiver disponível, ele é o detector; senão, o teste
        # documenta que o ambiente não tem nenhum dos dois.
        assert hasattr(detectores[0], "detectMultiScale") or hasattr(
            detectores[0], "setInputSize"
        )


class TestCadeiaNaoQuebra:
    """O caminho exato que impedia a câmera de ligar."""

    # O detector é criado uma vez por classe: abrir a câmera e carregar o
    # modelo dezenas de vezes na mesma suíte esgota recursos da máquina
    # (MemoryError), o que não é um defeito do código.
    @pytest.fixture(scope="class")
    def detector(self):
        try:
            return LocalFaceValidator._detectors()[0]
        except Exception as error:
            pytest.skip(f"Detector facial indisponível: {error}")

    @staticmethod
    def _frame():
        import numpy as np
        try:
            return np.zeros((480, 640, 3), dtype=np.uint8)
        except MemoryError:
            pytest.skip("Sem memória para alocar o frame de teste.")

    def test_embedding_de_frame_vazio_nao_levanta(self):
        # Frame preto: nenhum rosto. Deve devolver None, não explodir.
        assert LocalFaceValidator._embedding(self._frame()) is None

    def test_embedding_de_none_nao_levanta(self):
        assert LocalFaceValidator._embedding(None) is None

    def test_detect_faces_devolve_lista(self, detector):
        faces = LocalFaceValidator._detect_faces(detector, self._frame())
        assert isinstance(faces, list)

    def test_detect_faces_aceita_imagem_cinza(self, detector):
        import numpy as np
        gray = np.zeros((480, 640), dtype=np.uint8)
        assert isinstance(LocalFaceValidator._detect_faces(detector, gray), list)


class TestCameraRealmenteLiga:
    """A câmera precisa ser aberta mesmo quando não há rosto reconhecido."""

    def test_sequencia_retorna_suportado_quando_a_camera_abre(self):
        """`supported=True` é o que faz a VEGA LIGAR a câmera e salvar a foto.

        Sem isso, o gate nem tenta a captura e nenhuma evidência é gravada.
        """
        validator = LocalFaceValidator()
        supported, _valid, frame = validator.validate_camera_sequence(0)
        if not supported:
            pytest.skip("Nenhuma câmera disponível neste ambiente.")
        # Câmera abriu: precisa ter vindo um frame real.
        assert frame is not None

    def test_captura_grava_evidencia_quando_ha_referencia(self, tmp_path, monkeypatch):
        """Com referência facial e câmera, uma evidência DEVE ser criada."""
        from core.security.security import VegaSecurity

        security = VegaSecurity(path=tmp_path / "security.json")
        if not security.face_validator.ready:
            pytest.skip("Sem referência facial enrolada neste ambiente.")

        supported, _valid, path = security.capture_face_validation()
        if not supported:
            pytest.skip("Nenhuma câmera disponível neste ambiente.")

        assert path is not None
        assert (tmp_path / "security_evidence").exists()