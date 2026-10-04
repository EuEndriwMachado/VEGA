# -*- coding: utf-8 -*-
"""Testes de desambiguação PC/celular e do gate de segurança do celular.

Cobrem:
- print do PC vs print do celular;
- abrir app no PC vs abrir app no celular;
- espelhamento ("me mostra a tela do celular");
- autenticação por PIN + rosto, liberação por sessão e revogação.
"""

import time

import pytest

from core.actions.commands_parser import VegaCommandsParser
from core.brain.text_utils import normalize_text
from core.connectivity.security_gate import PhoneAccessGuard


# ==============================================================
# DESAMBIGUAÇÃO PC x CELULAR
# ==============================================================

class TestDesambiguacaoPrint:
    @pytest.mark.parametrize("fala", [
        "tira um print",
        "tira um print da tela",
        "print da tela",
        "screenshot",
        "captura a tela",
    ])
    def test_print_sem_mencao_vai_para_o_pc(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "__pc_screenshot__"

    @pytest.mark.parametrize("fala", [
        "tira um print do celular",
        "tira um print da tela do celular",
        "print do celular",
        "tira print no celular",
    ])
    def test_print_mencionando_celular_vai_para_o_celular(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "__phone_screenshot__"

    @pytest.mark.parametrize("fala", [
        "tira um print do computador",
        "print da tela do pc",
        "captura a tela do monitor",
    ])
    def test_print_mencionando_pc_vai_para_o_pc(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result[0] == "__pc_screenshot__"

    def test_texto_sem_print_nao_dispara(self):
        assert VegaCommandsParser._resolve_connectivity_request(
            normalize_text("abre o chrome")
        ) is None


class TestDesambiguacaoApps:
    @pytest.mark.parametrize("fala", [
        "abre o instagram",
        "abre o whatsapp",
        "abre o chrome",
        "abre o spotify",
    ])
    def test_app_sem_mencao_nao_e_do_celular(self, fala):
        """Sem citar o celular, o comando é do PC (fluxo normal)."""
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is None

    @pytest.mark.parametrize("fala", [
        "abre o instagram no celular",
        "abre o whatsapp no telefone",
        "executa o spotify no android",
    ])
    def test_app_mencionando_celular_e_do_celular(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "open_app"
        assert result[1].get("app")

    def test_extrai_nome_do_app(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("abre o instagram no celular")
        )
        assert "instagram" in result[1]["app"].lower()


class TestEspelhamento:
    @pytest.mark.parametrize("fala", [
        "me mostra a tela do celular",
        "mostra a tela do celular",
        "espelha a tela do celular",
        "espelhar o celular",
        "quero ver a tela do celular",
    ])
    def test_pedidos_de_espelhamento(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "mirror"

    @pytest.mark.parametrize("fala", [
        "para de espelhar o celular",
        "para o espelhamento do celular",
        "fecha a tela do celular",
    ])
    def test_pedidos_de_parar_espelhamento(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "stop_mirror"

    def test_me_mostra_a_tela_sem_celular_nao_espelha(self):
        """Sem citar o celular, não deve espelhar nada."""
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("me mostra a tela")
        )
        assert result is None


# ==============================================================
# GATE DE SEGURANÇA
# ==============================================================

class FakeSecurity:
    """Segurança simulada: controla PIN e resultado do rosto."""

    def __init__(self, pin="1234", has_pin=True, face_supported=False, face_valid=False):
        self._pin = pin
        self._has_pin = has_pin
        self._face_supported = face_supported
        self._face_valid = face_valid

    def has_pin(self):
        return self._has_pin

    def verify(self, pin):
        return str(pin).strip() == self._pin

    def capture_face_validation(self):
        return self._face_supported, self._face_valid, None


class TestPhoneAccessGuard:
    def test_comeca_bloqueado(self):
        guard = PhoneAccessGuard(FakeSecurity())
        assert not guard.is_granted()

    def test_pin_correto_libera(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="4321"))
        ok, message = guard.authenticate("4321")
        assert ok
        assert "liberado" in message.lower() or "confirmada" in message.lower()
        assert guard.is_granted()

    def test_pin_errado_nao_libera(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="4321"))
        ok, message = guard.authenticate("0000")
        assert not ok
        assert not guard.is_granted()
        assert "incorreto" in message.lower()

    def test_sem_pin_configurado_bloqueia(self):
        guard = PhoneAccessGuard(FakeSecurity(has_pin=False))
        ok, message = guard.authenticate("1234")
        assert not ok
        assert "pin" in message.lower()

    def test_rosto_invalido_bloqueia_mesmo_com_pin_certo(self):
        security = FakeSecurity(pin="4321", face_supported=True, face_valid=False)
        guard = PhoneAccessGuard(security)
        ok, message = guard.authenticate("4321")
        assert not ok
        assert "facial" in message.lower()
        assert not guard.is_granted()

    def test_rosto_valido_libera(self):
        security = FakeSecurity(pin="4321", face_supported=True, face_valid=True)
        guard = PhoneAccessGuard(security)
        ok, message = guard.authenticate("4321")
        assert ok
        assert "facial" in message.lower()

    def test_camera_indisponivel_ainda_libera_com_pin(self):
        """Sem câmera, o PIN sozinho basta (não trava o usuário)."""
        security = FakeSecurity(pin="4321", face_supported=False)
        guard = PhoneAccessGuard(security)
        ok, _message = guard.authenticate("4321")
        assert ok

    def test_autorizacao_persiste_entre_comandos(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="4321"))
        guard.authenticate("4321")
        # Vários comandos seguidos não pedem PIN de novo.
        assert guard.is_granted()
        assert guard.is_granted()
        assert guard.is_granted()

    def test_trocar_de_aparelho_revoga(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="4321"))
        guard.authenticate("4321")
        guard.grant("CELULAR_A")
        assert guard.is_granted("CELULAR_A")
        # Aparelho diferente: precisa autorizar de novo.
        assert not guard.is_granted("CELULAR_B")

    def test_revogacao_explicita(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="4321"))
        guard.authenticate("4321")
        guard.revoke()
        assert not guard.is_granted()

    def test_expiracao_por_tempo(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="4321"), session_ttl_seconds=0.05)
        guard.authenticate("4321")
        assert guard.is_granted()
        time.sleep(0.1)
        assert not guard.is_granted()

    def test_acao_pendente_e_recuperada(self):
        guard = PhoneAccessGuard(FakeSecurity())
        guard.set_pending("abre o instagram no celular", device_serial="ABC")
        assert guard.has_pending()
        pending = guard.take_pending()
        assert pending["action"] == "abre o instagram no celular"
        assert pending["device_serial"] == "ABC"
        # Consumida uma vez, não fica pendente.
        assert not guard.has_pending()

    def test_acao_pendente_expira(self):
        guard = PhoneAccessGuard(FakeSecurity())
        guard.set_pending("comando antigo")
        guard._pending_action["created_at"] = time.time() - 200
        assert guard.take_pending() is None

    def test_mensagem_de_desafio_menciona_camera_e_pin(self):
        guard = PhoneAccessGuard(FakeSecurity())
        message = guard.challenge_message().lower()
        assert "pin" in message
        assert "câmera" in message or "camera" in message


# ==============================================================
# INTEGRAÇÃO COM O BRAIN
# ==============================================================

class TestIntegracaoBrain:
    @pytest.fixture
    def brain(self, monkeypatch):
        from core.brain.brain import VegaBrain
        monkeypatch.setattr(
            "core.connectivity.security_gate.PhoneAccessGuard.authenticate",
            lambda self, pin: (False, "PIN incorreto.") if pin != "9999"
            else (self.grant(), "PIN confirmado.")[::-1],
        )
        return VegaBrain()

    def test_comando_pc_nao_pede_pin(self, brain):
        assert not brain._is_phone_control_request("abre o instagram")
        assert not brain._is_phone_control_request("tira um print")

    def test_comando_celular_pede_pin(self, brain):
        assert brain._is_phone_control_request("abre o instagram no celular")
        assert brain._is_phone_control_request("espelha a tela do celular")

    def test_consulta_de_dispositivos_nao_pede_pin(self, brain):
        assert not brain._is_phone_control_request("quais dispositivos estao conectados")

    def test_bloqueio_inicial_e_desafio(self, brain):
        response = brain._check_phone_access("abre o instagram no celular")
        assert response is not None
        assert "pin" in response.lower()
        assert brain.phone_guard.has_pending()

    def test_liberado_nao_pede_nada(self, brain):
        brain.phone_guard.grant(brain._current_phone_serial())
        assert brain._check_phone_access("abre o instagram no celular") is None

    def test_revogacao_apos_desconexao(self, brain):
        # Autoriza para o aparelho atual.
        serial = brain._current_phone_serial()
        brain.phone_guard.grant(serial)
        assert brain._check_phone_access("pausa a musica no celular") is None
        # Desconectou: a autorização é revogada e o PIN é exigido de novo.
        brain.revoke_phone_access()
        response = brain._check_phone_access("pausa a musica no celular")
        assert response is not None
        assert "pin" in response.lower()

    def test_troca_de_aparelho_revoga_autorizacao(self, brain, monkeypatch):
        """Autorizado para um celular, outro aparelho exige nova autenticação."""
        # Fixa o aparelho atual para o teste ser determinístico
        # (sem depender de haver um celular conectado na máquina).
        monkeypatch.setattr(brain, "_current_phone_serial", lambda: "CELULAR_B")
        brain.phone_guard.grant("APARELHO_ANTIGO")
        response = brain._check_phone_access("pausa a musica no celular")
        # O serial atual difere do autorizado -> pede PIN de novo.
        assert response is not None
        assert "pin" in response.lower()

    def test_sem_aparelho_identificado_nao_revoga(self, brain, monkeypatch):
        """Sem saber qual é o aparelho, a autorização se mantém."""
        monkeypatch.setattr(brain, "_current_phone_serial", lambda: "")
        brain.phone_guard.grant("CELULAR_A")
        assert brain._check_phone_access("pausa a musica no celular") is None