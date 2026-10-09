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
        """Sem citar o celular, não deve espelhar nada — vai para o PC.

        Antes esta fala era ignorada (resultado None) e a VEGA não fazia nada.
        Agora ela é reconhecida como pedido de VER A TELA DO PC, que é o que o
        usuário quer quando não cita dispositivo. O que o teste protege —
        "sem citar o celular, não espelha" — continua garantido.
        """
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("me mostra a tela")
        )
        assert result is not None
        assert result[0] != "mirror"
        assert result[0] == "__pc_screenshot__"

    def test_tela_do_celular_continua_espelhando(self):
        """Com o celular explícito, "ver a tela" É espelhamento."""
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("me mostra a tela do celular")
        )
        assert result == ("mirror", {})


# ==============================================================
# LIGAR E RESPONDER (FALA NATURAL)
# ==============================================================

class TestLigacaoEMensagem:
    """"liga para a rita pelo meu celular" e "responde o miguel".

    A fala não precisa dizer "celular": ligar e responder é operar o
    aparelho, e por isso essas frases caem no fluxo do celular.
    """

    @pytest.mark.parametrize("fala", [
        "liga para rita cassia pelo meu celular",
        "liga para a rita cassia pelo meu celular",
        "liga pro joao no celular",
        "chama o miguel no whatsapp",
        "liga para a rita no whatsapp pelo celular",
        "liga para 21999998888 pelo celular",
    ])
    def test_ligacao_e_reconhecida(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "call"

    def test_extrai_contato_com_sobrenome(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("liga para rita cassia pelo meu celular")
        )
        assert result == ("call", {"contact": "rita cassia", "app": ""})

    def test_whatsapp_na_fala_liga_pelo_whatsapp(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("liga para a rita no whatsapp pelo celular")
        )
        assert result[0] == "call"
        assert result[1]["app"] == "whatsapp"

    def test_numero_explicito(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("liga para 21999998888 pelo celular")
        )
        assert "number" in result[1]

    def test_chama_no_whatsapp_sem_celular(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("chama o miguel no whatsapp")
        )
        assert result == ("call", {"contact": "miguel", "app": "whatsapp"})

    def test_responde_le_a_conversa_quando_nao_diz_o_que_escrever(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("responde o miguel")
        )
        assert result == ("read_conversation", {"contact": "miguel", "text": ""})

    def test_responde_com_conteudo_envia_direto(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("responde o miguel dizendo que eu vou chegar tarde")
        )
        assert result[0] == "send_whatsapp"
        assert result[1]["contact"] == "miguel"
        assert "chegar tarde" in result[1]["text"]

    def test_responde_no_celular_nao_confunde_o_meio_com_o_nome(self):
        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("responde o miguel no celular")
        )
        assert result[1]["contact"] == "miguel"

    @pytest.mark.parametrize("fala", [
        "o que o miguel me mandou",
        "le as mensagens do miguel",
        "mostra as mensagens do miguel",
    ])
    def test_le_conversa_de_alguem(self, fala):
        result = VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))
        assert result is not None
        assert result[0] == "read_conversation"
        assert result[1]["contact"] == "miguel"

    def test_abrir_app_sem_celular_continua_no_pc(self):
        assert VegaCommandsParser._resolve_connectivity_request(
            normalize_text("abre o instagram")
        ) is None


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

    def test_rosto_nao_reconhecido_ainda_libera_com_pin_certo(self):
        """O PIN autoriza; o rosto é o REGISTRO de quem acessou.

        Antes, uma captura sem rosto detectado (cabeça baixa, luz ruim)
        negava o acesso do próprio dono mesmo com o PIN certo — o usuário
        digitava a senha e "não acontecia nada".
        """
        security = FakeSecurity(pin="4321", face_supported=True, face_valid=False)
        guard = PhoneAccessGuard(security)
        ok, message = guard.authenticate("4321")
        assert ok
        assert guard.is_granted()
        # A mensagem explica que o rosto não foi reconhecido, sem bloquear.
        assert "rosto" in message.lower() or "registrada" in message.lower()

    def test_rosto_obrigatorio_bloqueia_quando_ativado(self):
        """Com `require_face = True`, o rosto volta a ser obrigatório."""
        security = FakeSecurity(pin="4321", face_supported=True, face_valid=False)
        guard = PhoneAccessGuard(security)
        guard.require_face = True
        ok, message = guard.authenticate("4321")
        assert not ok
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

    def test_ligar_e_responder_pedem_pin(self, brain):
        """Ligar e responder também exigem autenticação.

        Mesmo sem dizer "celular": 'liga para a rita pelo meu celular' e
        'responde o miguel' são ações no aparelho.
        """
        assert brain._is_phone_control_request("liga para a rita pelo meu celular")
        assert brain._is_phone_control_request("responde o miguel")
        assert brain._is_phone_control_request("chama o miguel no whatsapp")

    def test_conversa_aleatoria_nao_pede_pin(self, brain):
        # Falar de WhatsApp numa frase comum não pode exigir PIN.
        assert not brain._is_phone_control_request("o que e whatsapp")
        assert not brain._is_phone_control_request("me explica como funciona o zap")# ==============================================================
# PIN POR TEXTO E RECONHECIMENTO FACIAL AUTOMÁTICO
# ==============================================================

class TestPinPorTexto:
    """O PIN digitado (ou ditado) precisa ser aceito.

    Antes, um PIN ditado colado ("umdoistres") ou com letras era perdido, e a
    autorização nunca concluía — sem a VEGA explicar o motivo.
    """

    @pytest.fixture
    def brain(self):
        from core.brain.brain import VegaBrain
        return VegaBrain()

    @pytest.mark.parametrize("fala,esperado", [
        ("1234", "1234"),
        ("senha 1234", "1234"),
        ("meu pin é 4321", "4321"),
        ("um dois tres quatro", "1234"),
        ("umdoistresquatro", "1234"),
        ("senha um dois zero zero", "1200"),
        ("pin quatro dois um nove", "4219"),
    ])
    def test_extrai_pin(self, brain, fala, esperado):
        match = brain._extract_security_pin(fala)
        assert match is not None
        assert match.group(1) == esperado

    def test_sem_pin_devolve_none(self, brain):
        assert brain._extract_security_pin("responde o miguel") is None

    def test_pin_conclui_autorizacao_em_fala_natural(self, brain, monkeypatch):
        """\"responde o miguel\" -> desafio -> PIN -> liberado."""
        def fake_authenticate(self, pin):
            if pin == "9999":
                self.grant()
                return True, "PIN confirmado."
            return False, "PIN incorreto."

        monkeypatch.setattr(
            "core.connectivity.security_gate.PhoneAccessGuard.authenticate",
            fake_authenticate,
        )
        brain.phone_guard.set_pending("responde o miguel", device_serial="ABC")

        response = brain._complete_phone_authentication("9999")
        assert response is not None
        assert "pin confirmado" in response.lower()
        assert not brain.phone_guard.has_pending()

    def test_pin_errado_mantem_pendente(self, brain, monkeypatch):
        monkeypatch.setattr(
            "core.connectivity.security_gate.PhoneAccessGuard.authenticate",
            lambda self, pin: (False, "PIN incorreto."),
        )
        brain.phone_guard.set_pending("responde o miguel", device_serial="ABC")

        response = brain._complete_phone_authentication("0000")
        assert response is not None
        assert "incorreto" in response.lower()
        # A ação continua pendente: o usuário pode tentar de novo.
        assert brain.phone_guard.has_pending()

    def test_sem_pendencia_nao_faz_nada(self, brain):
        assert brain._complete_phone_authentication("1234") is None


class TestDesafioDeSeguranca:
    def test_mensagem_explica_que_o_pin_e_numerico(self):
        guard = PhoneAccessGuard(FakeSecurity(pin="1234"))
        message = guard.challenge_message()
        assert "números" in message.lower() or "numeros" in message.lower()

    def test_sem_pin_configurado_avisa_com_clareza(self):
        guard = PhoneAccessGuard(FakeSecurity(has_pin=False))
        assert "pin" in guard.challenge_message().lower()


class TestReconhecimentoFacialAutomatico:
    def test_camera_indisponivel_nao_bloqueia_quem_tem_o_pin(self):
        """Se a captura falhar, o PIN correto ainda libera o acesso."""
        security = FakeSecurity(pin="1234")
        security.capture_face_validation = lambda: (_ for _ in ()).throw(
            RuntimeError("câmera ocupada")
        )
        guard = PhoneAccessGuard(security)
        granted, message = guard.authenticate("1234")
        assert granted
        assert guard.is_granted()

    def test_face_valida_confirma_com_pin_e_rosto(self):
        guard = PhoneAccessGuard(
            FakeSecurity(pin="1234", face_supported=True, face_valid=True)
        )
        granted, message = guard.authenticate("1234")
        assert granted
        assert "rosto" in message.lower() or "facial" in message.lower()

    def test_face_nao_reconhecida_registra_mas_libera(self):
        """O rosto é registro: não reconhecer não bloqueia o PIN correto."""
        guard = PhoneAccessGuard(
            FakeSecurity(pin="1234", face_supported=True, face_valid=False)
        )
        granted, _message = guard.authenticate("1234")
        assert granted
        assert guard.is_granted()

    def test_face_obrigatoria_bloqueia_quando_configurada(self):
        guard = PhoneAccessGuard(
            FakeSecurity(pin="1234", face_supported=True, face_valid=False)
        )
        guard.require_face = True
        granted, _message = guard.authenticate("1234")
        assert not granted
        assert not guard.is_granted()

# ==============================================================
# PADRÃO DE TELA: sem dispositivo = PC; com celular = celular
# ==============================================================
# Regra pedida pelo usuário: "olha minha tela" sem citar dispositivo é a tela
# do PC. Antes, essa fala espelhava o celular — ela chegou a pedir para ver a
# tela e a VEGA tentou espelhar o telefone.

class TestPadraoDeTela:
    def _resolve(self, fala):
        return VegaCommandsParser._resolve_connectivity_request(normalize_text(fala))

    @pytest.mark.parametrize("fala", [
        "olha minha tela",
        "olha a minha tela",
        "me mostra a tela",
        "o que esta na minha tela",
        "analisa a minha tela",
        "descreve a tela",
    ])
    def test_tela_sem_dispositivo_vai_para_o_pc(self, fala):
        result = self._resolve(fala)
        assert result is not None, fala
        assert result[0] == "__pc_screenshot__", fala

    @pytest.mark.parametrize("fala", [
        "deixa eu ver a tela do celular",
        "me mostra a tela do celular",
        "espelha a tela do celular",
        "mostra o celular",
    ])
    def test_tela_do_celular_espelha(self, fala):
        assert self._resolve(fala) == ("mirror", {}), fala

    @pytest.mark.parametrize("fala", [
        "tira um print",
        "print da tela",
        "screenshot",
    ])
    def test_print_sem_dispositivo_e_do_pc(self, fala):
        result = self._resolve(fala)
        assert result is not None
        assert result[0] == "__pc_screenshot__", fala

    def test_print_do_celular_e_do_celular(self):
        result = self._resolve("print da tela do celular")
        assert result is not None
        assert result[0] == "__phone_screenshot__"
