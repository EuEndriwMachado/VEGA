# -*- coding: utf-8 -*-
"""Testes do fluxo de pareamento/reconexão sem fio e das melhorias de
desempenho do espelhamento.

Rodam sem rede e sem celular: o ADB é simulado por um runner falso.
"""

import os
import tempfile

import pytest

from core.connectivity import PhoneConnector
from core.connectivity.registry import DeviceRegistry
from core.connectivity.wireless import WirelessADB


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


# ==============================================================
# RECONEXÃO
# ==============================================================

class TestReconexao:
    def test_reconecta_via_mdns(self, registry):
        responses = {
            "mdns": (
                True,
                "adb-222a6388-xyz _adb-tls-connect._tcp 192.168.15.4:39407",
                "",
            ),
            "connect": (True, "connected to adb-222a6388-xyz", ""),
            "devices": (True, "List of devices attached\n192.168.15.4:39407\tdevice\n", ""),
        }
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory(responses))
        assert wireless.connect_via_mdns() is True

    def test_mdns_sem_servico_retorna_false(self, registry):
        responses = {"mdns": (True, "no services found", "")}
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory(responses))
        assert wireless.connect_via_mdns() is False

    def test_mdns_sem_adb(self, registry):
        wireless = WirelessADB(adb_path="", registry=registry, runner=lambda a: (True, "", ""))
        wireless._adb_path = ""
        import unittest.mock as mock
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.path.exists", return_value=False):
            assert wireless.connect_via_mdns() is False

    def test_reconnect_known_usa_mdns_primeiro(self, registry):
        registry.remember("Celular", kind="phone", address="192.168.15.4",
                          connector="phone", wireless=True)
        responses = {
            "mdns": (True, "svc _adb-tls-connect._tcp 192.168.15.4:39407", ""),
            "connect": (True, "connected to svc", ""),
        }
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory(responses))
        messages = wireless.reconnect_known()
        assert messages
        assert "reconectado" in messages[0].lower()

    def test_reconnect_usa_porta_salva(self, registry):
        registry.remember("Celular", kind="phone", address="192.168.15.4",
                          connector="phone", wireless=True, port=39407)
        responses = {
            "mdns": (False, "", "sem mdns"),
            "devices": (True, "List of devices attached\n", ""),
            "connect": (True, "connected to 192.168.15.4:39407", ""),
        }
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory(responses))
        messages = wireless.reconnect_known()
        assert messages

    def test_reconnect_sem_dispositivos(self, registry):
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory({"mdns": (True, "", "")}))
        assert wireless.reconnect_known() == []

    def test_nao_reconecta_aparelho_usb(self, registry):
        registry.remember("Celular USB", kind="phone", address="ABC123", connector="phone")
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory({"mdns": (True, "", "")}))
        assert wireless.reconnect_known() == []

    def test_conexao_salva_porta_no_registro(self, registry):
        registry.remember("Celular", kind="phone", address="192.168.15.4",
                          connector="phone", wireless=True)
        responses = {"connect": (True, "connected to 192.168.15.4:39407", "")}
        wireless = WirelessADB(adb_path="adb", registry=registry,
                               runner=runner_factory(responses))
        wireless.connect("192.168.15.4", 39407)
        device = registry.find("192.168.15.4")
        assert device.metadata.get("port") == 39407


# ==============================================================
# PERFIS DE ESPELHAMENTO
# ==============================================================

class TestPerfisEspelhamento:
    def test_perfis_existem(self):
        assert "balanced" in PhoneConnector.MIRROR_PROFILES
        assert "fast" in PhoneConnector.MIRROR_PROFILES
        assert "quality" in PhoneConnector.MIRROR_PROFILES
        assert "silent" in PhoneConnector.MIRROR_PROFILES

    def test_perfil_balanced_limita_recursos(self):
        options = PhoneConnector.MIRROR_PROFILES["balanced"]
        assert "--max-size" in options
        assert "--max-fps" in options
        assert "--video-bit-rate" in options

    def test_perfil_silent_desliga_audio(self):
        assert "--no-audio" in PhoneConnector.MIRROR_PROFILES["silent"]

    def test_mirror_usa_perfil_escolhido(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
        started = {}

        class _FakeProcess:
            # O mirror confere o ciclo de vida do scrcpy (poll()/stdout); a
            # lista usada antes não tinha poll() e o AttributeError virava
            # "Falha ao executar mirror".
            def poll(self):
                return None

            stdout = None

        def _fake_popen(args, **kwargs):
            started["args"] = args
            return _FakeProcess()

        monkeypatch.setattr("core.connectivity.phone.subprocess.Popen", _fake_popen)
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        monkeypatch.setattr(connector, "_find_scrcpy", lambda: "/usr/bin/scrcpy")
        # O mirror espera 1.5s para confirmar que o scrcpy subiu de verdade.
        monkeypatch.setattr("core.connectivity.phone.time.sleep", lambda _seconds: None)

        result = connector.execute("mirror", profile="fast")
        assert result.ok
        assert "--max-fps" in started["args"]
        assert "--video-bit-rate" in started["args"]

    def test_perfil_invalido_usa_balanced(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
        started = {}
        monkeypatch.setattr(
            "core.connectivity.phone.subprocess.Popen",
            lambda args, **kwargs: started.setdefault("args", args),
        )
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        monkeypatch.setattr(connector, "_find_scrcpy", lambda: "/usr/bin/scrcpy")

        connector.execute("mirror", profile="perfil_que_nao_existe")
        assert "--max-size" in started["args"]  # balanced tem max-size

    def test_nao_abre_dois_espelhamentos(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
        monkeypatch.setattr(
            "core.connectivity.phone.subprocess.Popen",
            lambda args, **kwargs: type("P", (), {"poll": lambda self: None})(),
        )
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        monkeypatch.setattr(connector, "_find_scrcpy", lambda: "/usr/bin/scrcpy")

        first = connector.execute("mirror")
        second = connector.execute("mirror")
        assert first.ok and second.ok
        assert "já está aberto" in second.message.lower()

    def test_stop_mirror(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
        terminated = {"count": 0}

        class FakeProcess:
            def poll(self):
                return None

            def terminate(self):
                terminated["count"] += 1

        monkeypatch.setattr(
            "core.connectivity.phone.subprocess.Popen",
            lambda args, **kwargs: FakeProcess(),
        )
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        monkeypatch.setattr(connector, "_find_scrcpy", lambda: "/usr/bin/scrcpy")

        connector.execute("mirror")
        result = connector.execute("stop_mirror")
        assert result.ok
        assert terminated["count"] == 1

    def test_stop_mirror_sem_espelhamento(self, registry):
        connector = PhoneConnector(registry=registry, runner=runner_factory({}))
        result = connector.execute("stop_mirror")
        assert not result.ok
        assert "não há" in result.message.lower()


# ==============================================================
# PRINT DO CELULAR MOSTRA NO PC
# ==============================================================

class TestPrintMostraNoPc:
    def test_screenshot_abre_no_pc(self, monkeypatch, registry, tmp_path):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "screencap": (True, "", ""),
            "pull": (True, "1 file pulled", ""),
        }
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "core.connectivity.phone.os.startfile",
            lambda path: None,
            raising=False,
        )
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        result = connector.execute("screenshot", show=True)
        # Sem o arquivo real criado pelo runner falso, reporta falha clara.
        assert not result.ok or "abri" in result.message.lower()

    def test_screenshot_sem_show_nao_abre(self, monkeypatch, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "screencap": (True, "", ""),
            "pull": (True, "1 file pulled", ""),
        }
        opened = {"count": 0}
        monkeypatch.setattr(
            "core.connectivity.phone.PhoneConnector._open_on_pc",
            staticmethod(lambda path: opened.__setitem__("count", opened["count"] + 1) or True),
        )
        connector = PhoneConnector(registry=registry, runner=runner_factory(responses))
        connector.execute("screenshot", show=False)
        assert opened["count"] == 0