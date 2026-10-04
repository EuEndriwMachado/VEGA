# -*- coding: utf-8 -*-
"""Testes das ações avançadas no celular e do monitor de conexão.

Cobrem: notificações, alarme (com limitação do MIUI), localizar, foto,
ligação, mensagem, bluetooth e o watchdog de reconexão.

Rodam sem rede e sem celular: o ADB é simulado por um runner falso.
"""

import os
import tempfile
import time

import pytest

from core.connectivity import PhoneConnector
from core.connectivity.monitor import PhoneConnectionMonitor
from core.connectivity.phone_actions import PhoneActions
from core.connectivity.registry import DeviceRegistry


@pytest.fixture
def registry():
    path = tempfile.mkdtemp()
    return DeviceRegistry(path=os.path.join(path, "devices.json"))


def runner_factory(responses):
    def runner(args):
        for key, value in responses.items():
            if key in args:
                return value
        return False, "", "não mapeado"
    return runner


def connected_runner(extra=None):
    """Runner com um celular conectado + respostas extras."""
    base = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
    if extra:
        base.update(extra)
    return runner_factory(base)


# ==============================================================
# NOTIFICAÇÕES
# ==============================================================

class TestNotificacoes:
    def test_le_titulo_e_texto_entre_parenteses(self):
        """O Android 12+ escreve android.title=String (texto)."""
        output = """
NotificationRecord(0x123 pkg=com.whatsapp ...)
  android.title=String (João)
  android.text=SpannableString (Cheguei em casa)
"""
        entries = PhoneActions._parse_notifications(output)
        assert len(entries) == 1
        assert "WhatsApp" in entries[0]
        assert "Cheguei em casa" in entries[0]

    def test_usa_titulo_quando_sem_texto(self):
        output = """
NotificationRecord(0x1 pkg=com.instagram.android)
  android.title=String (Nova curtida)
"""
        entries = PhoneActions._parse_notifications(output)
        assert entries
        assert "Nova curtida" in entries[0]

    def test_ignora_notificacoes_do_sistema(self):
        output = """
NotificationRecord(0x1 pkg=com.android.systemui)
  android.title=String (Bateria fraca)
NotificationRecord(0x2 pkg=android)
  android.title=String (USB conectado)
"""
        assert PhoneActions._parse_notifications(output) == []

    def test_descarta_referencia_sem_conteudo(self):
        """\"String [length=34]\" não é texto útil."""
        output = """
NotificationRecord(0x1 pkg=com.exemplo.app)
  android.text=String [length=34]
"""
        assert PhoneActions._parse_notifications(output) == []

    def test_remove_duplicatas(self):
        output = """
NotificationRecord(0x1 pkg=com.whatsapp)
  android.text=String (Oi)
NotificationRecord(0x2 pkg=com.whatsapp)
  android.text=String (Oi)
"""
        assert len(PhoneActions._parse_notifications(output)) == 1

    def test_respeita_o_limite(self):
        blocks = "\n".join(
            f"NotificationRecord(0x{i} pkg=com.app{i})\n  android.text=String (msg {i})"
            for i in range(10)
        )
        assert len(PhoneActions._parse_notifications(blocks, limit=3)) == 3

    def test_nome_amigavel_do_app(self):
        assert PhoneActions._friendly_app("com.whatsapp") == "WhatsApp"
        assert PhoneActions._friendly_app("com.instagram.android") == "Instagram"
        assert PhoneActions._friendly_app("com.exemplo.meuapp") == "Meuapp"

    def test_sem_notificacoes(self, registry):
        # dumpsys responde com sucesso, mas sem notificações.
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "dumpsys": (True, "No notifications\n", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("notifications")
        assert result.ok
        assert "nenhuma notificação" in result.message.lower()


# ==============================================================
# ALARME
# ==============================================================

class TestAlarme:
    def test_horario_invalido_pede_de_novo(self, registry):
        connector = PhoneConnector(registry=registry, runner=connected_runner())
        result = connector.execute("set_alarm")
        assert not result.ok
        assert "horário" in result.message.lower()

    def test_hora_fora_do_intervalo(self, registry):
        connector = PhoneConnector(registry=registry, runner=connected_runner())
        result = connector.execute("set_alarm", hour=25, minute=0)
        assert not result.ok

    def test_miui_nao_confirma_e_avisa_o_usuario(self, registry):
        """O MIUI bloqueia criação remota: a VEGA não pode mentir que criou."""
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "am": (True, "Starting: Intent", ""),
            "content": (False, "", "Permission Denial"),
            "monkey": (True, "", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("set_alarm", hour=6, minute=30)
        assert not result.ok
        assert "miui" in result.message.lower() or "não permite" in result.message.lower()

    def test_cria_quando_o_sistema_confirma(self, registry):
        # O runner casa por substring nos args reais do adb: o comando é
        # "shell am start -a android.intent.action.SET_ALARM", então a
        # chave é "am" (o nome da ação fica em minúsculas no argumento).
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "am": (True, "Starting: Intent", ""),
            "content": (True, "Row: 0 hour=6, minutes=30, enabled=1", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("set_alarm", hour=6, minute=30)
        assert result.ok
        assert "06:30" in result.message
    def test_sem_celular(self, registry):
        connector = PhoneConnector(registry=registry, runner=runner_factory({}))
        result = connector.execute("set_alarm", hour=6, minute=30)
        assert not result.ok


# ==============================================================
# LOCALIZAR
# ==============================================================

class TestLocalizar:
    def test_faz_o_celular_tocar(self, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "media_session": (True, "", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("locate")
        assert result.ok
        assert "tocar" in result.message.lower()

    def test_sem_celular_avisa(self, registry):
        connector = PhoneConnector(registry=registry, runner=runner_factory({}))
        result = connector.execute("locate")
        assert not result.ok


# ==============================================================
# LIGAÇÃO E MENSAGEM
# ==============================================================

class TestLigacao:
    def test_liga_por_numero(self, registry):
        # O fake casa por substring nos args; o intent aparece como
        # 'android.intent.action.CALL' (minúsculo), então usamos 'am'.
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "am": (True, "Starting: Intent", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("call", number="+5521999999999")
        assert result.ok
        assert "ligando" in result.message.lower()

    def test_sem_destino_pede_informacao(self, registry):
        connector = PhoneConnector(registry=registry, runner=connected_runner())
        result = connector.execute("call")
        assert not result.ok
        assert "para quem" in result.message.lower()

    def test_contato_nao_encontrado(self, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "content": (False, "", "não achou"),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("call", contact="Fulano")
        assert not result.ok
        assert "não encontrei" in result.message.lower()


class TestMensagem:
    def test_prepara_mensagem_whatsapp(self, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "content": (True, "Row: 0 display_name=Joao, data1=+5521988888888", ""),
            "am": (True, "Starting: Intent", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("send_message", contact="Joao", text="Já vou")
        assert result.ok
        assert "joao" in result.message.lower()

    def test_sem_texto_pede_conteudo(self, registry):
        connector = PhoneConnector(registry=registry, runner=connected_runner())
        result = connector.execute("send_message", contact="Joao")
        assert not result.ok
        assert "escrevo" in result.message.lower() or "mensagem" in result.message.lower()

    def test_sem_contato_pede_destino(self, registry):
        connector = PhoneConnector(registry=registry, runner=connected_runner())
        result = connector.execute("send_message", text="Oi")
        assert not result.ok


# ==============================================================
# BLUETOOTH DO CELULAR
# ==============================================================

class TestBluetoothCelular:
    def test_liga_bluetooth(self, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "bluetooth": (True, "", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("bluetooth", enabled=True)
        assert result.ok
        assert "ligado" in result.message.lower()

    def test_desliga_bluetooth(self, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "bluetooth": (True, "", ""),
        }
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("bluetooth", enabled=False)
        assert result.ok
        assert "desligado" in result.message.lower()


# ==============================================================
# MONITOR DE CONEXÃO (WATCHDOG)
# ==============================================================

class TestMonitorConexao:
    class FakeWireless:
        def __init__(self, connected=True, reconnect=True):
            self.connected = connected
            self.reconnect_ok = reconnect
            self.reconnect_calls = 0

        def connected_devices(self):
            return ["192.168.15.4:5555"] if self.connected else []

        def reconnect_known(self):
            self.reconnect_calls += 1
            if self.reconnect_ok:
                self.connected = True
                return ["Conectado."]
            return []

    def test_detecta_conexao_inicial(self, registry):
        monitor = PhoneConnectionMonitor(
            wireless=self.FakeWireless(connected=True), registry=registry,
        )
        result = monitor.check_now()
        assert result["connected"] is True

    def test_detecta_mudanca_de_estado(self, registry):
        wireless = self.FakeWireless(connected=True)
        monitor = PhoneConnectionMonitor(wireless=wireless, registry=registry)
        assert monitor.check_now()["changed"] is True
        assert monitor.check_now()["changed"] is False  # sem mudança

    def test_reconecta_quando_cai(self, registry):
        registry.remember("Celular", kind="phone", address="192.168.15.4",
                          connector="phone", wireless=True)
        wireless = self.FakeWireless(connected=False, reconnect=True)
        monitor = PhoneConnectionMonitor(wireless=wireless, registry=registry)

        result = monitor.check_now()
        assert wireless.reconnect_calls == 1
        assert result["connected"] is True

    def test_nao_tenta_reconectar_sem_celular_conhecido(self, registry):
        wireless = self.FakeWireless(connected=False)
        monitor = PhoneConnectionMonitor(wireless=wireless, registry=registry)
        monitor.check_now()
        assert wireless.reconnect_calls == 0

    def test_evento_disparado_na_transicao(self, registry):
        registry.remember("Celular", kind="phone", address="192.168.15.4",
                          connector="phone", wireless=True)
        events = []
        wireless = self.FakeWireless(connected=True)
        monitor = PhoneConnectionMonitor(
            wireless=wireless, registry=registry, interval=0.05,
            startup_delay=0.0, on_event=events.append,
        )
        # O evento é emitido pelo loop da thread, não pelo check_now().
        monitor.start()
        time.sleep(0.3)
        monitor.stop()
        assert events
        assert events[0]["connected"] is True

    def test_rede_quebrada_nao_derruba_o_monitor(self, registry):
        class BrokenWireless:
            def connected_devices(self):
                raise RuntimeError("rede caiu")

            def reconnect_known(self):
                raise RuntimeError("rede caiu")

        monitor = PhoneConnectionMonitor(wireless=BrokenWireless(), registry=registry)
        result = monitor.check_now()
        assert result["connected"] is False  # degrada, não explode

    def test_sem_wireless_nao_quebra(self, registry):
        monitor = PhoneConnectionMonitor(wireless=None, registry=registry)
        assert monitor.check_now()["connected"] is False

    def test_start_e_stop(self, registry):
        monitor = PhoneConnectionMonitor(
            wireless=self.FakeWireless(), registry=registry, interval=0.05,
        )
        assert monitor.start() is True
        assert monitor.is_running is True
        # Chamar start duas vezes não cria outra thread.
        assert monitor.start() is False
        monitor.stop()
        time.sleep(0.1)
        assert monitor.is_running is False

    def test_status(self, registry):
        monitor = PhoneConnectionMonitor(
            wireless=self.FakeWireless(), registry=registry,
        )
        status = monitor.status()
        assert "running" in status
        assert "connected" in status