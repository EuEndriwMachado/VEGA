# -*- coding: utf-8 -*-
"""Testes do modo sem fio (ADB over Wi-Fi) e do dicionário de apps.

Cobrem validação de entrada, pareamento, reconexão e resolução de nomes
de apps para pacotes Android. Rodam sem rede e sem celular conectado.
"""

import os
import tempfile

import pytest

from core.connectivity.apps import AppDictionary, DEFAULT_APPS, PACKAGE_VARIANTS
from core.connectivity.registry import DeviceRegistry
from core.connectivity.wireless import WirelessADB


# ==============================================================
# FIXTURES
# ==============================================================

@pytest.fixture
def registry():
    path = tempfile.mkdtemp()
    return DeviceRegistry(path=os.path.join(path, "devices.json"))


# ==============================================================
# VALIDAÇÃO DE ENTRADA (PARECAMENTO)
# ==============================================================

class TestValidacaoPareamento:
    @pytest.mark.parametrize("value,expected", [
        ("192.168.15.4:37000", ("192.168.15.4", 37000)),
        ("192.168.15.4 37000", ("192.168.15.4", 37000)),
        ("  10.0.0.1:5555  ", ("10.0.0.1", 5555)),
    ])
    def test_enderecos_validos(self, value, expected):
        assert WirelessADB.parse_pair_input(value) == expected

    @pytest.mark.parametrize("value", [
        "", "abc", "192.168.15.4", "192.168.15.4:", "não é um ip",
        "999.999.999.999:37000",
    ])
    def test_enderecos_invalidos(self, value):
        host, port = WirelessADB.parse_pair_input(value)
        assert host == "" and port == 0

    @pytest.mark.parametrize("code,expected", [
        ("123456", True), ("000000", True),
        ("12345", False), ("1234567", False), ("abcdef", False), ("", False),
    ])
    def test_codigo_de_seis_digitos(self, code, expected):
        assert WirelessADB.validate_code(code) is expected


# ==============================================================
# PARECAMENTO E CONEXÃO (ADB SIMULADO)
# ==============================================================

class TestPareamento:
    def _wireless(self, responses, registry=None):
        def runner(args):
            for key, value in responses.items():
                if key in args:
                    return value
            return False, "", "não mapeado"

        return WirelessADB(adb_path="adb", registry=registry, runner=runner)

    def test_pareamento_bem_sucedido(self, registry):
        wireless = self._wireless(
            {"pair": (True, "Successfully paired to 192.168.15.4:37000", "")},
            registry,
        )
        ok, message = wireless.pair("192.168.15.4:37000", "123456")
        assert ok
        assert "pareado" in message.lower()

    def test_pareamento_registra_dispositivo(self, registry):
        wireless = self._wireless(
            {"pair": (True, "Successfully paired", "")},
            registry,
        )
        wireless.pair("192.168.15.4:37000", "123456")
        device = registry.find("192.168.15.4")
        assert device is not None
        assert device.metadata.get("wireless") is True

    def test_codigo_errado(self, registry):
        wireless = self._wireless(
            {"pair": (False, "", "Wrong password")},
            registry,
        )
        ok, message = wireless.pair("192.168.15.4:37000", "999999")
        assert not ok
        assert "incorreto" in message.lower() or "código" in message.lower()

    def test_endereco_invalido_nao_chama_adb(self, registry):
        wireless = self._wireless({}, registry)
        ok, message = wireless.pair("endereco-ruim", "123456")
        assert not ok
        assert "inválido" in message.lower()

    def test_codigo_invalido_nao_chama_adb(self, registry):
        wireless = self._wireless({}, registry)
        ok, message = wireless.pair("192.168.15.4:37000", "12")
        assert not ok
        assert "6 dígitos" in message

    def test_sem_adb(self, registry):
        wireless = WirelessADB(adb_path="", registry=registry, runner=lambda a: (True, "", ""))
        wireless._adb_path = ""
        import unittest.mock as mock
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.path.exists", return_value=False):
            ok, message = wireless.pair("192.168.15.4:37000", "123456")
        assert not ok
        assert "ADB" in message


class TestConexao:
    def _wireless(self, responses, registry=None):
        def runner(args):
            for key, value in responses.items():
                if key in args:
                    return value
            return False, "", "não mapeado"
        return WirelessADB(adb_path="adb", registry=registry, runner=runner)

    def test_conexao_bem_sucedida(self, registry):
        wireless = self._wireless({"connect": (True, "connected to 192.168.15.4:5555", "")}, registry)
        ok, message = wireless.connect("192.168.15.4")
        assert ok
        assert "conectado" in message.lower()

    def test_ja_conectado(self, registry):
        wireless = self._wireless({"connect": (True, "already connected to 192.168.15.4:5555", "")}, registry)
        ok, _message = wireless.connect("192.168.15.4")
        assert ok

    def test_falha_de_conexao(self, registry):
        wireless = self._wireless({"connect": (False, "", "failed to connect")}, registry)
        ok, message = wireless.connect("192.168.15.4")
        assert not ok
        assert "não consegui" in message.lower()

    def test_reconectar_conhecidos(self, registry):
        registry.remember("Celular", kind="phone", address="192.168.15.4",
                          connector="phone", wireless=True)
        wireless = self._wireless({"connect": (True, "connected to 192.168.15.4:5555", "")}, registry)
        messages = wireless.reconnect_known()
        assert messages
        assert "conectado" in messages[0].lower()

    def test_reconectar_sem_dispositivos(self, registry):
        wireless = self._wireless({}, registry)
        assert wireless.reconnect_known() == []

    def test_reconectar_ignora_dispositivos_com_cabo(self, registry):
        registry.remember("Celular USB", kind="phone", address="ABC123", connector="phone")
        wireless = self._wireless({"connect": (True, "connected", "")}, registry)
        assert wireless.reconnect_known() == []


class TestModoAntigo:
    def _wireless(self, responses, registry=None):
        def runner(args):
            for key, value in responses.items():
                if key in args:
                    return value
            return False, "", "não mapeado"
        return WirelessADB(adb_path="adb", registry=registry, runner=runner)

    def test_ativar_tcpip(self, registry):
        wireless = self._wireless({"tcpip": (True, "restarting in TCP mode port: 5555", "")}, registry)
        ok, message = wireless.enable_tcpip()
        assert ok
        assert "5555" in message

    def test_extrair_ip_do_roteamento(self, registry):
        wireless = self._wireless(
            {"ip": (True, "192.168.15.0/24 dev wlan0 src 192.168.15.4", "")},
            registry,
        )
        assert wireless.current_ip() == "192.168.15.4"

    def test_ip_ausente(self, registry):
        wireless = self._wireless({"ip": (True, "sem ip aqui", "")}, registry)
        assert wireless.current_ip() == ""


# ==============================================================
# DICIONÁRIO DE APPS
# ==============================================================

class TestDicionarioDeApps:
    @pytest.fixture
    def apps(self):
        path = tempfile.mkdtemp()
        return AppDictionary(extra_path=os.path.join(path, "apps.json"))

    @pytest.mark.parametrize("nome,esperado", [
        ("instagram", "com.instagram.android"),
        ("insta", "com.instagram.android"),
        ("whatsapp", "com.whatsapp"),
        ("zap", "com.whatsapp"),
        ("youtube", "com.google.android.youtube"),
        ("spotify", "com.spotify.music"),
        ("configuracoes", "com.android.settings"),
        ("calculadora", "com.miui.calculator"),
    ])
    def test_nomes_conhecidos(self, apps, nome, esperado):
        assert apps.resolve(nome) == esperado

    def test_corrige_erro_de_transcricao(self, apps):
        """'spotfy' deve ser corrigido para spotify antes de resolver."""
        assert apps.resolve("spotfy") == "com.spotify.music"

    def test_pacote_direto_e_aceito(self, apps):
        assert apps.resolve("com.exemplo.app") == "com.exemplo.app"

    def test_nome_desconhecido_retorna_vazio(self, apps):
        assert apps.resolve("aplicativo que nao existe mesmo") == ""

    def test_palavra_curta_nao_casa_errado(self, apps):
        """'x' não deve capturar apps por acaso em frases longas."""
        assert apps.resolve("aplicativo inexistente aqui") == ""

    def test_label_amigavel(self, apps):
        assert apps.label("insta") == "Instagram"
        assert apps.label("zap") == "WhatsApp"

    def test_adicionar_app_personalizado(self, apps):
        assert apps.add("meu app", "com.meu.app", label="Meu App")
        assert apps.resolve("meu app") == "com.meu.app"
        assert apps.label("meu app") == "Meu App"

    def test_app_personalizado_persiste(self, tmp_path):
        path = str(tmp_path / "apps.json")
        AppDictionary(extra_path=path).add("custom", "com.custom.app")
        reloaded = AppDictionary(extra_path=path)
        assert reloaded.resolve("custom") == "com.custom.app"

    def test_arquivo_corrompido_nao_quebra(self, tmp_path):
        path = tmp_path / "apps.json"
        path.write_text("{ nao e json", encoding="utf-8")
        apps = AppDictionary(extra_path=str(path))
        assert apps.resolve("instagram") == "com.instagram.android"


class TestVariantesDeFabricante:
    @pytest.fixture
    def apps(self):
        path = tempfile.mkdtemp()
        return AppDictionary(extra_path=os.path.join(path, "apps.json"))

    def test_candidatos_incluem_variantes(self, apps):
        candidates = apps.candidates("calculadora")
        assert "com.miui.calculator" in candidates
        assert "com.google.android.calculator" in candidates
        assert len(candidates) > 1

    def test_primeiro_candidato_e_o_principal(self, apps):
        candidates = apps.candidates("galeria")
        assert candidates[0] == "com.miui.gallery"

    def test_sem_duplicatas(self, apps):
        candidates = apps.candidates("calculadora")
        assert len(candidates) == len(set(candidates))

    def test_app_sem_variantes(self, apps):
        candidates = apps.candidates("whatsapp")
        assert candidates == ["com.whatsapp"]

    def test_nome_desconhecido_sem_candidatos(self, apps):
        assert apps.candidates("app inexistente xyz") == []

    def test_todas_as_variantes_sao_pacotes_validos(self):
        import re
        for key, packages in PACKAGE_VARIANTS.items():
            assert packages, f"{key} não tem variantes"
            for package in packages:
                assert re.fullmatch(r"[a-zA-Z][\w]*(\.[\w]+)+", package), \
                    f"pacote inválido em {key}: {package}"

    def test_dicionario_padrao_sem_duplicatas_exatas(self):
        # Chaves duplicadas seriam silenciosamente sobrescritas no dict.
        assert len(DEFAULT_APPS) == len(set(DEFAULT_APPS.keys()))