# -*- coding: utf-8 -*-
"""Testes de identidade de dispositivos da VEGA.

Cobrem o reconhecimento do celular do usuário (Redmi Note 13 4G, do
Endriw), aparelhos de outras marcas, aparelhos desconhecidos e a
identificação por serial (estável) em vez de IP/porta (que mudam).
"""

import os
import tempfile

import pytest

from core.connectivity.identity import DeviceIdentity
from core.connectivity.registry import DeviceRegistry


@pytest.fixture
def registry():
    path = tempfile.mkdtemp()
    return DeviceRegistry(path=os.path.join(path, "devices.json"))


@pytest.fixture
def identity(registry):
    return DeviceIdentity(registry=registry)


# ==============================================================
# RECONHECIMENTO DO APARELHO
# ==============================================================

class TestReconhecimento:
    def test_reconhece_redmi_note_13_como_do_endriw(self, identity):
        result = identity.identify(
            model="Redmi Note 13 4G", brand="Redmi", serial="23129RA5FL",
        )
        assert result["name"] == "Redmi Note 13 4G"
        assert result["owner"] == "Endriw"
        assert result["kind"] == "phone"

    def test_reconhece_por_variacao_do_modelo(self, identity):
        """Variações do nome do modelo também reconhecem."""
        for model in ("Redmi Note 13", "redmi note13", "2312DRA50I"):
            result = identity.identify(model=model)
            assert result["owner"] == "Endriw", model

    def test_reconhece_samsung_sem_dono(self, identity):
        result = identity.identify(model="SM-S911B", brand="samsung")
        assert "Samsung" in result["name"]
        assert result["owner"] == ""

    def test_reconhece_motorola(self, identity):
        result = identity.identify(model="moto g84", brand="motorola")
        assert "Motorola" in result["name"]

    def test_reconhece_pixel(self, identity):
        result = identity.identify(model="Pixel 8", brand="google")
        assert "Pixel" in result["name"]

    def test_aparelho_desconhecido_usa_o_modelo(self, identity):
        result = identity.identify(model="Aparelho Xyz 3000")
        assert result["name"] == "Aparelho Xyz 3000"
        assert result["owner"] == ""

    def test_aparelho_sem_dados_nao_quebra(self, identity):
        result = identity.identify()
        assert result["name"] == "Celular"
        assert result["kind"] == "phone"

    def test_aliases_incluem_serial_e_endereco(self, identity):
        result = identity.identify(
            model="Redmi Note 13 4G", serial="ABC123", address="192.168.15.4",
        )
        assert "ABC123" in result["aliases"]
        assert "192.168.15.4" in result["aliases"]
        assert "meu celular" in result["aliases"]


# ==============================================================
# REGISTRO E BUSCA
# ==============================================================

class TestRegistro:
    def test_registrar_guarda_identidade(self, identity, registry):
        identity.register(
            model="Redmi Note 13 4G", brand="Redmi",
            serial="23129RA5FL", address="192.168.15.4",
        )
        device = registry.find("Redmi Note 13 4G")
        assert device is not None
        assert device.metadata["owner"] == "Endriw"
        assert device.metadata["serial"] == "23129RA5FL"

    def test_encontra_por_serial_mesmo_com_ip_novo(self, identity, registry):
        """O serial é estável; IP e porta mudam. A busca tem de funcionar."""
        identity.register(
            model="Redmi Note 13 4G", serial="23129RA5FL",
            address="192.168.15.4",
        )
        # Celular reconecta com outro IP.
        found = identity.find_by_serial("23129RA5FL")
        assert found is not None
        assert found.metadata["owner"] == "Endriw"

    def test_encontra_por_apelido_falado(self, identity, registry):
        identity.register(model="Redmi Note 13 4G", serial="ABC")
        device = registry.find("meu celular")
        assert device is not None
        assert device.metadata["owner"] == "Endriw"

    def test_serial_inexistente_retorna_none(self, identity):
        assert identity.find_by_serial("NAO_EXISTE") is None

    def test_sem_registry_nao_quebra(self):
        identity = DeviceIdentity(registry=None)
        result = identity.register(model="Redmi Note 13 4G")
        assert result["owner"] == "Endriw"


# ==============================================================
# DESCRIÇÃO EM LINGUAGEM NATURAL
# ==============================================================

class TestDescricao:
    def test_descreve_celular_do_usuario(self, identity, registry):
        identity.register(model="Redmi Note 13 4G", serial="ABC")
        device = registry.find("Redmi Note 13 4G")
        text = identity.describe(device)
        assert "Redmi Note 13 4G" in text
        assert "Endriw" in text

    def test_descreve_aparelho_sem_dono(self, identity, registry):
        identity.register(model="SM-S911B", brand="samsung", serial="XYZ")
        device = registry.find("Celular Samsung")
        text = identity.describe(device)
        assert "Samsung" in text
        assert "Endriw" not in text

    def test_descreve_sem_dispositivo(self, identity):
        assert "não tenho" in identity.describe(None).lower()

    def test_is_primary_identifica_o_celular_do_usuario(self, identity, registry):
        identity.register(model="Redmi Note 13 4G", serial="ABC")
        primary = registry.find("Redmi Note 13 4G")
        assert identity.is_primary(primary)

    def test_aparelho_de_outra_pessoa_nao_e_primario(self, identity, registry):
        identity.register(model="SM-S911B", brand="samsung", serial="XYZ")
        other = registry.find("Celular Samsung")
        assert not identity.is_primary(other)


# ==============================================================
# DONO NA FALA
# ==============================================================

class TestDonoNaFala:
    @pytest.mark.parametrize("fala,dono", [
        ("o celular do joao", "joao"),
        ("o celular da maria", "maria"),
        ("celular do pedro", "pedro"),
    ])
    def test_extrai_dono_da_fala(self, identity, fala, dono):
        assert identity.resolve_owner(fala) == dono

    @pytest.mark.parametrize("fala", [
        "meu celular", "abre o instagram no celular", "tira um print",
    ])
    def test_sem_dono_explicito(self, identity, fala):
        assert identity.resolve_owner(fala) == ""

    def test_encontra_aparelho_por_dono(self, identity, registry):
        identity.register(
            model="Redmi Note 13 4G", serial="ABC",
        )
        # O dono "Endriw" está no metadata.
        device = identity.find_by_owner("endriw")
        assert device is not None
        assert device.metadata["owner"] == "Endriw"


# ==============================================================
# INTEGRAÇÃO COM O PARSER
# ==============================================================

class TestComandosDeVoz:
    def test_pergunta_que_celular_e_esse(self):
        from core.actions.commands_parser import VegaCommandsParser
        from core.brain.text_utils import normalize_text

        for fala in (
            "que celular e esse",
            "qual e o meu celular",
            "identifica o celular",
            "de quem e esse celular",
        ):
            result = VegaCommandsParser._resolve_connectivity_request(
                normalize_text(fala)
            )
            assert result is not None, fala
            assert result[0] == "identify", fala

    def test_pergunta_sobre_celular_nao_dispara_em_pc(self):
        from core.actions.commands_parser import VegaCommandsParser
        from core.brain.text_utils import normalize_text

        result = VegaCommandsParser._resolve_connectivity_request(
            normalize_text("que horas sao")
        )
        assert result is None