# -*- coding: utf-8 -*-
"""Testes da camada de conectividade da VEGA (celular, TV, registro).

Todos rodam sem rede, sem hardware e sem as ferramentas externas
instaladas — o foco é justamente verificar a degradação graciosa.
"""

import json
import os
import tempfile

import pytest

from core.connectivity import (
    ConnectivityManager,
    DeviceRegistry,
    PhoneConnector,
    TVConnector,
)
from core.connectivity.base import Connector, ConnectorResult, ConnectorStatus


# ==============================================================
# FIXTURES
# ==============================================================

@pytest.fixture
def registry():
    path = tempfile.mkdtemp()
    yield DeviceRegistry(path=os.path.join(path, "devices.json"))


def fake_runner_factory(responses):
    """Cria um runner falso: mapeia a lista de args para (ok, out, err)."""
    def runner(args):
        for key, value in responses.items():
            if key in args:
                return value
        return False, "", "não mapeado"
    return runner


# ==============================================================
# REGISTRO DE DISPOSITIVOS
# ==============================================================

class TestDeviceRegistry:
    def test_registrar_e_encontrar(self, registry):
        registry.remember("Meu Celular", kind="phone", address="ABC123")
        device = registry.find("meu celular")
        assert device is not None
        assert device.kind == "phone"
        assert device.address == "ABC123"

    def test_busca_aproximada_por_apelido(self, registry):
        registry.remember("Galaxy do João", kind="phone", aliases=["celular", "joao"])
        assert registry.find("joao") is not None
        assert registry.find("celular") is not None

    def test_filtrar_por_tipo(self, registry):
        registry.remember("Celular", kind="phone")
        registry.remember("TV da Sala", kind="tv")
        assert len(registry.by_kind("phone")) == 1
        assert len(registry.by_kind("tv")) == 1

    def test_nao_encontrado_retorna_none(self, registry):
        assert registry.find("inexistente") is None

    def test_persistencia_entre_instancias(self):
        path = tempfile.mkdtemp()
        file = os.path.join(path, "devices.json")
        DeviceRegistry(path=file).remember("TV", kind="tv", address="192.168.0.5")
        reloaded = DeviceRegistry(path=file)
        assert reloaded.find("tv").address == "192.168.0.5"

    def test_arquivo_corrompido_nao_quebra(self):
        path = tempfile.mkdtemp()
        file = os.path.join(path, "devices.json")
        with open(file, "w", encoding="utf-8") as handle:
            handle.write("{ isso não é json válido")
        registry = DeviceRegistry(path=file)
        assert len(registry) == 0  # degrada para vazio, sem exceção

    def test_esquecer(self, registry):
        registry.remember("TV", kind="tv")
        assert registry.forget("tv")
        assert registry.find("tv") is None

    def test_acentos_e_maiusculas(self, registry):
        registry.remember("Televisão da Sala", kind="tv")
        assert registry.find("televisao da sala") is not None


# ==============================================================
# CONECTOR BASE
# ==============================================================

class TestConnectorBase:
    def test_conector_indisponivel_explica(self):
        class Dummy(Connector):
            name = "dummy"
            install_hint = "Instale o Dummy."

            def status(self):
                return ConnectorStatus(
                    name=self.name, available=False,
                    detail="Não encontrei o Dummy.", install_hint=self.install_hint,
                )

            def _register_actions(self):
                self._actions = {"fazer": lambda **_k: ConnectorResult.success("ok")}

        connector = Dummy()
        result = connector.execute("fazer")
        assert not result.ok
        assert "Instale o Dummy" in result.message

    def test_acao_desconhecida_tem_prioridade_sobre_indisponibilidade(self):
        """Mesmo indisponível, uma ação inexistente deve ser reportada como tal."""
        class Dummy(Connector):
            name = "dummy"

            def status(self):
                return ConnectorStatus(name=self.name, available=False, detail="sumiu")

        result = Dummy().execute("nao_existe")
        assert not result.ok
        assert "não é suportada" in result.message.lower()

    def test_validate_roda_antes_da_disponibilidade(self):
        """Erro de uso vence erro de ferramenta ausente (mensagem mais útil)."""
        class Dummy(Connector):
            name = "dummy"

            def status(self):
                return ConnectorStatus(name=self.name, available=False, detail="sem ferramenta")

            def validate(self, action, **kwargs):
                return "Faltou o argumento."

            def _register_actions(self):
                self._actions = {"fazer": lambda **_k: ConnectorResult.success("ok")}

        result = Dummy().execute("fazer")
        assert not result.ok
        assert "Faltou o argumento" in result.message

    def test_acao_desconhecida(self):
        class Dummy(Connector):
            name = "dummy"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

        result = Dummy().execute("acao_inexistente")
        assert not result.ok
        assert "não é suportada" in result.message.lower()

    def test_handler_que_levanta_nao_propaga(self):
        class Dummy(Connector):
            name = "dummy"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def _register_actions(self):
                def boom(**_kwargs):
                    raise RuntimeError("falha interna")
                self._actions = {"boom": boom}

        result = Dummy().execute("boom")
        assert not result.ok
        assert "falha interna" in result.message

    def test_handler_retorna_string_vira_sucesso(self):
        class Dummy(Connector):
            name = "dummy"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def _register_actions(self):
                self._actions = {"oi": lambda **_k: "Olá!"}

        result = Dummy().execute("oi")
        assert result.ok and result.message == "Olá!"

    def test_argumentos_invalidos_nao_quebram(self):
        class Dummy(Connector):
            name = "dummy"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def _register_actions(self):
                self._actions = {"estrito": lambda obrigatorio: obrigatorio}

        result = Dummy().execute("estrito", outro=1)
        assert not result.ok


# ==============================================================
# CONECTOR DE CELULAR (ADB simulado)
# ==============================================================

class TestPhoneConnector:
    def test_sem_adb_avisa_o_que_instalar(self, monkeypatch):
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which", lambda _name: None
        )
        monkeypatch.setattr("core.connectivity.phone.os.path.exists", lambda _p: False)

        connector = PhoneConnector()
        result = connector.execute("list")
        assert not result.ok
        assert "ADB" in result.message or "KDE" in result.message

    def test_lista_dispositivos_com_adb(self, monkeypatch, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC123\tdevice\n", ""),
            "getprop": (True, "Galaxy S23\n", ""),
        }
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )

        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        result = connector.execute("list")
        assert result.ok
        # O modelo é reconhecido por perfil: "Galaxy S23" -> "Celular Samsung".
        assert "Samsung" in result.message
        assert registry.find("Celular Samsung") is not None

    def test_reconhece_redmi_note_13_do_endriw(self, monkeypatch, registry):
        """O aparelho principal é reconhecido pelo modelo, com dono."""
        responses = {
            "devices": (True, "List of devices attached\n23129RA5FL\tdevice\n", ""),
            "getprop": (True, "Redmi Note 13 4G\n", ""),
        }
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        result = connector.execute("list")
        assert result.ok
        assert "Redmi Note 13 4G" in result.message

        device = registry.find("Redmi Note 13 4G")
        assert device is not None
        assert device.metadata.get("owner") == "Endriw"

    def test_identifica_o_celular_do_usuario(self, monkeypatch, registry):
        responses = {
            "devices": (True, "List of devices attached\n23129RA5FL\tdevice\n", ""),
            "getprop": (True, "Redmi Note 13 4G\n", ""),
        }
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        result = connector.execute("identify")
        assert result.ok
        assert "Redmi Note 13 4G" in result.message
        assert "Endriw" in result.message

    def test_sem_dispositivo_conectado(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\n", "")}
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        result = connector.execute("list")
        assert not result.ok
        assert "depuração usb" in result.message.lower() or "conecte o cabo" in result.message.lower()

    def test_bateria(self, monkeypatch, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "battery": (True, "  level: 82\n  status: 2\n", ""),
        }
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        result = connector.execute("battery")
        assert result.ok
        assert "82" in result.message
        assert "carregando" in result.message

    def test_abrir_app_sem_nome_pede_esclarecimento(self, monkeypatch, registry):
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory({}))
        result = connector.execute("open_app", app="")
        assert not result.ok
        assert "qual aplicativo" in result.message.lower()

    def test_screenshot_transfere_arquivo(self, monkeypatch, registry, tmp_path):
        local = tmp_path / "Uploads" / "phone"
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "screencap": (True, "", ""),
            "pull": (True, "1 file pulled", ""),
        }
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        monkeypatch.chdir(tmp_path)
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        result = connector.execute("screenshot")
        # O runner falso não cria o arquivo, então o conector reporta falha clara.
        assert "não consegui transferir" in result.message.lower() or result.ok

    def test_enviar_arquivo_inexistente(self, monkeypatch, registry):
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory({}))
        result = connector.execute("send_file", path="/caminho/inexistente.xyz")
        assert not result.ok
        assert "não encontrei o arquivo" in result.message.lower()

    def test_mirror_sem_scrcpy_explica(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        # Isola o teste do scrcpy realmente instalado na máquina.
        monkeypatch.setattr(connector, "_find_scrcpy", lambda: "")
        result = connector.execute("mirror")
        assert not result.ok
        assert "scrcpy" in result.message.lower()

    def test_mirror_com_scrcpy_inicia(self, monkeypatch, registry):
        responses = {"devices": (True, "List of devices attached\nABC\tdevice\n", "")}
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        started = {}
        monkeypatch.setattr(
            "core.connectivity.phone.subprocess.Popen",
            lambda args, **kwargs: started.setdefault("args", args),
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        monkeypatch.setattr(connector, "_find_scrcpy", lambda: "/usr/bin/scrcpy")
        result = connector.execute("mirror")
        assert result.ok
        assert "/usr/bin/scrcpy" in started["args"]

    def test_controle_de_midia(self, monkeypatch, registry):
        responses = {
            "devices": (True, "List of devices attached\nABC\tdevice\n", ""),
            "keyevent": (True, "", ""),
        }
        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=fake_runner_factory(responses))
        assert connector.execute("media_pause").ok
        assert connector.execute("media_play").ok
        assert connector.execute("media_next").ok

    def test_runner_com_timeout_e_tratado(self, monkeypatch, registry):
        import subprocess

        def timeout_runner(_args):
            raise subprocess.TimeoutExpired(cmd="adb", timeout=1)

        monkeypatch.setattr(
            "core.connectivity.phone.shutil.which",
            lambda name: "/usr/bin/adb" if name == "adb" else None,
        )
        connector = PhoneConnector(registry=registry, runner=timeout_runner)
        result = connector.execute("list")
        assert not result.ok  # não propaga a exceção


# ==============================================================
# CONECTOR DE TV
# ==============================================================

class TestTVConnector:
    def test_sem_pychromecast_explica(self, monkeypatch, registry):
        connector = TVConnector(registry=registry)
        monkeypatch.setattr(connector, "_has_pychromecast", lambda: False)
        result = connector.execute("list")
        assert not result.ok
        assert "tv" in result.message.lower() or "chromecast" in result.message.lower()

    def test_tv_cadastrada_por_ip_e_reconhecida(self, registry):
        registry.remember("TV da Sala", kind="tv", address="192.168.0.50")
        connector = TVConnector(registry=registry)
        status = connector.status()
        assert status.available
        assert "1 TV" in status.detail or "conhecida" in status.detail.lower()

    def test_play_url_sem_url_pede_conteudo(self, registry):
        connector = TVConnector(registry=registry)
        result = connector.execute("play_url", url="")
        assert not result.ok
        assert "qual conteúdo" in result.message.lower()

    def test_guess_type_por_extensao(self):
        assert TVConnector._guess_type("http://x/video.mp4") == "video/mp4"
        assert TVConnector._guess_type("http://x/audio.mp3") == "audio/mp3"
        assert TVConnector._guess_type("http://x/foto.jpg") == "image/jpeg"

    def test_is_reachable_falso_para_ip_invalido(self):
        # 127.0.0.1:1 não tem serviço: deve retornar False sem levantar.
        assert TVConnector.is_reachable("127.0.0.1", port=1, timeout=0.2) is False

    def test_descoberta_sem_biblioteca_retorna_vazio(self, monkeypatch, registry):
        connector = TVConnector(registry=registry)
        monkeypatch.setattr(connector, "_has_pychromecast", lambda: False)
        assert connector.discover() == []


# ==============================================================
# GERENCIADOR (roteamento por linguagem natural)
# ==============================================================

class TestConnectivityManager:
    def test_roteia_celular_por_palavra_chave(self, registry):
        class FakePhone(Connector):
            name = "phone"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def _register_actions(self):
                self._actions = {"battery": lambda **_k: ConnectorResult.success("bateria 50%")}

        manager = ConnectivityManager(
            registry=registry,
            connectors=[FakePhone(registry=registry)],
        )
        result = manager.route("battery", text="como está a bateria do meu celular?")
        assert result.ok
        assert "50" in result.message

    def test_roteia_tv_por_palavra_chave(self, registry):
        class FakeTV(Connector):
            name = "tv"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def _register_actions(self):
                self._actions = {"pause": lambda **_k: ConnectorResult.success("pausada")}

        manager = ConnectivityManager(
            registry=registry,
            connectors=[FakeTV(registry=registry)],
        )
        result = manager.route("pause", text="pausa a tv")
        assert result.ok

    def test_sem_alvo_pede_esclarecimento(self, registry):
        manager = ConnectivityManager(registry=registry, connectors=[])
        result = manager.route("pause", text="pausa aí")
        assert not result.ok
        assert "celular ou na tv" in result.message.lower()

    def test_extrai_nome_do_dispositivo(self, registry):
        manager = ConnectivityManager(registry=registry, connectors=[])
        assert manager._guess_device_name("joga na tv da sala", "tv") == "sala"
        assert manager._guess_device_name("manda pro celular do joao", "phone") == "joao"

    def test_nao_confunde_artigo_com_nome(self, registry):
        manager = ConnectivityManager(registry=registry, connectors=[])
        # "na tv" sem nome próprio não deve virar dispositivo "na".
        assert manager._guess_device_name("liga a tv", "tv") == ""

    def test_status_report_sem_ferramentas(self, registry, monkeypatch):
        monkeypatch.setattr("core.connectivity.phone.shutil.which", lambda _n: None)
        monkeypatch.setattr("core.connectivity.phone.os.path.exists", lambda _p: False)
        manager = ConnectivityManager(registry=registry)
        report = manager.status_report()
        assert "phone" in report and "tv" in report

    def test_describe_devices(self, registry):
        registry.remember("Celular", kind="phone")
        registry.remember("TV da Sala", kind="tv")
        manager = ConnectivityManager(registry=registry, connectors=[])
        text = manager.describe_devices()
        assert "celular" in text.lower()
        assert "TV" in text

    def test_describe_sem_dispositivos(self, registry):
        manager = ConnectivityManager(registry=registry, connectors=[])
        assert "não conheço" in manager.describe_devices().lower()

    def test_discover_all_tolera_conector_quebrado(self, registry):
        class Broken(Connector):
            name = "phone"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def discover(self):
                raise RuntimeError("rede caiu")

        manager = ConnectivityManager(registry=registry, connectors=[Broken()])
        found = manager.discover_all()
        assert found == {"phone": []}  # degrada, não quebra

    def test_kind_explicito_tem_prioridade(self, registry):
        class FakePhone(Connector):
            name = "phone"

            def status(self):
                return ConnectorStatus(name=self.name, available=True)

            def _register_actions(self):
                self._actions = {"battery": lambda **_k: ConnectorResult.success("ok")}

        manager = ConnectivityManager(registry=registry, connectors=[FakePhone()])
        result = manager.route("battery", text="isso", kind="phone")
        assert result.ok