# -*- coding: utf-8 -*-
"""Auditoria de segurança: registro de TODO pedido perigoso.

O que o usuário pediu e antes não acontecia:

1. a foto e o registro devem ser feitos no MOMENTO do pedido, não só quando
   o PIN está certo;
2. errar o PIN precisa ficar registrado no log;
3. o celular também precisa gerar auditoria (antes não gerava nada);
4. o texto gravado não pode conter o PIN digitado;
5. os registros aparecem na aba "Segurança" da interface do banco.
"""

import json
import pathlib

import pytest

from core.security.security import VegaSecurity


@pytest.fixture
def security(tmp_path):
    instance = VegaSecurity(path=tmp_path / "security.json")
    instance.set_pin("4321")
    return instance


def _log(security):
    path = security.path.parent / "security_audit.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


class TestRegistroDoPedido:
    def test_pedido_e_registrado_sem_senha(self, security):
        """O registro existe ANTES de qualquer PIN."""
        security.register_security_request("apaga o arquivo garfo", source="voz")
        entries = _log(security)
        assert len(entries) == 1
        assert entries[0]["event"] == "requested"
        assert entries[0]["confirmed"] is False
        assert entries[0]["pin_valid"] is False

    def test_pedido_registra_a_origem(self, security):
        security.register_security_request("esvaziar a lixeira", source="celular")
        assert _log(security)[0]["source"] == "celular"

    def test_registros_sao_acumulados_em_ordem(self, security):
        security.register_security_request("primeiro pedido")
        security.register_security_request("segundo pedido")
        entries = _log(security)
        assert len(entries) == 2
        assert entries[1]["timestamp"] >= entries[0]["timestamp"]


class TestPinErradoFicaRegistrado:
    def test_tentativa_negada_e_registrada(self, security):
        security.register_security_request("apaga o arquivo garfo")
        security.register_auth_attempt(
            "apaga o arquivo garfo", pin_valid=False, authorized=False, source="voz"
        )
        entries = _log(security)
        assert len(entries) == 2
        negado = entries[-1]
        assert negado["event"] == "denied"
        assert negado["pin_valid"] is False
        assert negado["authorized"] is False

    def test_tentativa_aceita_e_registrada(self, security):
        security.register_auth_attempt(
            "apaga o arquivo garfo", pin_valid=True, authorized=True, source="voz"
        )
        assert _log(security)[-1]["event"] == "authorized"

    def test_fluxo_completo_pedido_negado(self, security):
        """Pedido -> PIN errado: os DOIS eventos ficam no log."""
        security.register_security_request("esvaziar a lixeira", source="celular")
        security.register_auth_attempt(
            "esvaziar a lixeira", pin_valid=False, authorized=False, source="celular"
        )
        eventos = [entry["event"] for entry in _log(security)]
        assert eventos == ["requested", "denied"]


class TestPinNuncaVaza:
    def test_pin_digitado_nao_aparece_no_log(self, security):
        security.register_security_request("apaga o arquivo garfo 4321")
        conteudo = json.dumps(_log(security), ensure_ascii=False)
        assert "4321" not in conteudo

    def test_resumo_remove_qualquer_sequencia_longa_de_digitos(self):
        resumo = VegaSecurity._summarize_action("apaga o arquivo relatorio 987654")
        assert "987654" not in resumo
        assert "relatorio" in resumo

    def test_acao_em_dict_nao_quebra(self):
        resumo = VegaSecurity._summarize_action({"kind": "empty_recycle_bin"})
        assert resumo == "empty_recycle_bin"


class TestCompatibilidadeComLogAntigo:
    def test_registro_antigo_continua_legivel(self, security):
        """O log antigo não tem 'event'; não pode quebrar a leitura."""
        log_path = security.path.parent / "security_audit.json"
        log_path.write_text(json.dumps([{
            "timestamp": "2026-09-20T23:09:21+00:00",
            "action": "apague o arquivo garfo",
            "confirmed": True,
            "pin_valid": True,
            "face_supported": True,
            "face_valid": True,
            "evidence_path": "data/security_evidence/x.jpg",
            "authorized": True,
        }]), encoding="utf-8")

        security.register_security_request("novo pedido")
        entries = _log(security)
        assert len(entries) == 2
        # O registro antigo preservou sua ação original.
        assert entries[0]["action"] == "apague o arquivo garfo"


class TestAbaSegurancaDaInterface:
    """A aba 'Segurança' do visualizador precisa mostrar os registros."""

    @pytest.fixture
    def reader(self, tmp_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "viewer", pathlib.Path(__file__).resolve().parents[1] / "database" / "viewer.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.DatabaseReader(tmp_path, "json")

    def test_le_os_registros_do_log(self, reader, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "security_audit.json").write_text(json.dumps([
            {
                "timestamp": "2026-10-04T02:36:05+00:00",
                "event": "requested",
                "action": "dangerous_action",
                "action_summary": "apaga o arquivo garfo",
                "pin_valid": False,
                "face_supported": True,
                "face_valid": None,
                "evidence_path": "data/security_evidence/x.jpg",
                "authorized": False,
                "source": "voz",
            }
        ]), encoding="utf-8")

        rows = reader.security_rows()
        assert len(rows) == 1
        assert rows[0][1] == "Pedido registrado"
        assert rows[0][2] == "apaga o arquivo garfo"
        assert rows[0][3] == "não"
        assert rows[0][7].endswith("x.jpg")

    def test_log_ausente_nao_quebra(self, reader):
        assert reader.security_rows() == []

    def test_log_corrompido_nao_quebra(self, reader, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "security_audit.json").write_text("{isso nao e json", encoding="utf-8")
        assert reader.security_rows() == []

    def test_rotulo_do_rosto(self):
        assert VegaSecurity is not None
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "viewer", pathlib.Path(__file__).resolve().parents[1] / "database" / "viewer.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        reader = module.DatabaseReader(".", "json")
        assert reader._face_label({"face_valid": True}) == "reconhecido"
        assert reader._face_label({"face_valid": False}) == "não reconhecido"
        assert reader._face_label({"face_supported": True}) == "foto tirada"
        assert reader._face_label({}) == "sem foto"