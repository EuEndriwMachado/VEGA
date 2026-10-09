# -*- coding: utf-8 -*-
"""O portão único de segurança do celular.

Princípio pedido pelo usuário:

    "a vega só tem que identificar se é perigoso ou não, se for, ela toma as
     medidas de segurança e depois que passar das medidas, ela faz a tarefa
     normalmente"

E os três defeitos reais que este arquivo trava:

1. **PIN em loop**: o serial guardado na autorização vinha de uma fonte
   (WirelessADB) e o serial usado nas ações vinha de outra (adb devices).
   Como o celular está no Wi-Fi, os dois nunca batiam, o guard achava que o
   aparelho tinha trocado e pedia o PIN de novo — para sempre.
2. **Rosto negando o dono**: uma captura sem rosto detectado bloqueava quem
   tinha o PIN certo. O usuário digitava a senha e "não acontecia nada".
3. **Decisão duplicada**: dois blocos quase iguais decidiam segurança, com
   condições que se anulavam.
"""

import pytest

from core.connectivity.security_gate import PhoneAccessGuard


class FakeSecurity:
    def __init__(self, pin="4321", has_pin=True, face_supported=False, face_valid=False):
        self._pin = pin
        self._has_pin = has_pin
        self._face_supported = face_supported
        self._face_valid = face_valid

    def has_pin(self):
        return self._has_pin

    def verify(self, pin):
        return str(pin).strip() == self._pin

    def capture_face_validation(self, camera_index=None):
        return self._face_supported, self._face_valid, None


# ==============================================================
# PIN AUTORIZA; ROSTO REGISTRA
# ==============================================================

class TestPapeisDePinERosto:
    def test_pin_certo_libera_mesmo_sem_rosto(self):
        """O defeito do \"falei a senha e não aconteceu nada\"."""
        guard = PhoneAccessGuard(FakeSecurity(face_supported=True, face_valid=False))
        granted, _message = guard.authenticate("4321")
        assert granted
        assert guard.is_granted()

    def test_pin_certo_libera_quando_a_camera_falha(self):
        security = FakeSecurity()

        def explode(camera_index=None):
            raise RuntimeError("câmera ocupada")

        security.capture_face_validation = explode
        guard = PhoneAccessGuard(security)
        granted, _message = guard.authenticate("4321")
        assert granted

    def test_pin_errado_nunca_libera(self):
        guard = PhoneAccessGuard(FakeSecurity(face_supported=True, face_valid=True))
        granted, _message = guard.authenticate("0000")
        assert not granted
        assert not guard.is_granted()

    def test_rosto_reconhecido_aparece_na_mensagem(self):
        guard = PhoneAccessGuard(
            FakeSecurity(face_supported=True, face_valid=True)
        )
        _granted, message = guard.authenticate("4321")
        assert "facial" in message.lower() or "rosto" in message.lower()

    def test_rosto_nao_reconhecido_avisa_sem_bloquear(self):
        guard = PhoneAccessGuard(
            FakeSecurity(face_supported=True, face_valid=False)
        )
        granted, message = guard.authenticate("4321")
        assert granted
        assert "registrada" in message.lower() or "rosto" in message.lower()

    def test_require_face_volta_a_obrigar_o_rosto(self):
        guard = PhoneAccessGuard(
            FakeSecurity(face_supported=True, face_valid=False)
        )
        guard.require_face = True
        granted, _message = guard.authenticate("4321")
        assert not granted


# ==============================================================
# SERIAL CONSISTENTE (O LOOP DO PIN)
# ==============================================================

class TestSerialConsistente:
    def test_autorizacao_sem_serial_adota_o_aparelho_atual(self):
        """PIN informado antes de o celular ser detectado não pode revogar."""
        guard = PhoneAccessGuard(FakeSecurity())
        guard.authenticate("4321")          # grant sem serial
        assert guard.is_granted("SERIAL_REAL") is True

    def test_serial_vazio_nao_revoga(self):
        guard = PhoneAccessGuard(FakeSecurity())
        guard.authenticate("4321")
        assert guard.is_granted("") is True

    def test_mesmo_serial_continua_liberado(self):
        guard = PhoneAccessGuard(FakeSecurity())
        guard.grant("APARELHO_A")
        assert guard.is_granted("APARELHO_A") is True

    def test_troca_real_de_aparelho_revoga(self):
        guard = PhoneAccessGuard(FakeSecurity())
        guard.grant("APARELHO_A")
        assert guard.is_granted("APARELHO_B") is False

    def test_brain_usa_o_mesmo_serial_das_acoes(self):
        """O serial do guard e o das ações vêm da MESMA fonte."""
        from core.brain.brain import VegaBrain
        from core.connectivity import PhoneConnector

        brain = VegaBrain()
        assert brain._current_phone_serial() == PhoneConnector()._resolve_serial()


# ==============================================================
# PORTÃO ÚNICO
# ==============================================================

class TestPortaoUnico:
    @pytest.fixture
    def brain(self, tmp_path, monkeypatch):
        from core.brain.brain import VegaBrain
        from core.security.security import VegaSecurity

        brain = VegaBrain()
        security = VegaSecurity(path=tmp_path / "security.json")
        security.set_pin("4321")
        brain.security = security
        brain.phone_guard = PhoneAccessGuard(security)
        return brain

    def test_fala_nao_perigosa_nao_pede_nada(self, brain):
        assert brain._phone_security_gate("qual e a capital da franca") is None

    def test_fala_perigosa_pede_o_pin(self, brain):
        resposta = brain._phone_security_gate("responde o miguel")
        assert resposta is not None
        assert "pin" in resposta.lower()

    def test_depois_do_pin_a_tarefa_segue(self, brain):
        """O ponto central: aprovado o portão, a tarefa não é limitada."""
        brain._phone_security_gate("responde o miguel")   # desafio
        brain._phone_security_gate("4321")                # PIN correto
        # Agora a mesma tarefa passa direto pelo portão.
        assert brain._phone_security_gate("responde o miguel") is None

    def test_pin_errado_mantem_o_portao_fechado(self, brain):
        brain._phone_security_gate("responde o miguel")
        resposta = brain._phone_security_gate("0000")
        assert resposta is not None
        assert "incorreto" in resposta.lower()
        # E a tarefa continua exigindo autenticação.
        assert brain._phone_security_gate("responde o miguel") is not None

    def test_uma_unica_decisao_de_seguranca_no_fluxo(self):
        """Não pode voltar a existir a checagem duplicada no cérebro."""
        import pathlib
        source = pathlib.Path("core/brain/brain.py").read_text(encoding="utf-8")
        # O bloco antigo tinha esta condição exata (a duplicata anulada).
        duplicata = (
            "if not self._is_phone_control_request(normalized) "
            "and self._looks_like_phone_action(normalized):"
        )
        assert duplicata not in source

    def test_o_portao_e_o_mesmo_para_voz_e_comando(self):
        """Os dois fluxos (stream de voz e comandos) usam o MESMO portão."""
        import pathlib
        source = pathlib.Path("core/brain/brain.py").read_text(encoding="utf-8")
        assert source.count("def _phone_security_gate(") == 1   # uma única definição
        assert source.count("self._phone_security_gate(") == 2  # os dois fluxos