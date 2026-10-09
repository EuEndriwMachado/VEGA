

# -*- coding: utf-8 -*-
"""A fala que cita um dispositivo vai para aquele dispositivo — sempre.

Regressão do pedido do usuário: "aumenta o volume DA TV" é comando para a
TV (nunca para o PC); "a tela DO CELULAR" é comando para o celular (nunca
para a TV). E "desliga a tv" NUNCA pode apagar o computador — foi exatamente
isso que aconteceu quando um teste executou o parser real.

Todos os testes aqui usam `interpret` (dry-run): o parser interpreta mas
NADA é executado — desligar, apagar, transmitir, nada.
"""
import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from core.actions.commands_parser import VegaCommandsParser
from core.actions.commands_parser import VegaCommandsParser as P
from core.brain.text_utils import normalize_text


def resolve(fala):
    return P._resolve_connectivity_request(normalize_text(fala))


# --------------------------------------------------------------
# TV: volume e desligar vão para a TV quando a fala cita a TV
# --------------------------------------------------------------

def test_volume_da_tv_nunca_e_do_pc():
    assert resolve("aumenta o volume da tv") == ("volume", {"delta": 0.1})
    assert resolve("abaixa o volume da tv") == ("volume", {"delta": -0.1})


def test_desliga_a_tv_e_turn_off_da_tv():
    assert resolve("desliga a tv") == ("turn_off", {})


@pytest.mark.parametrize("fala", [
    "desliga o computador",
    "desliga o pc",
    "reinicie o computador",
])
def test_pedidos_para_o_pc_nao_sao_conectividade(fala):
    # Vão para os handlers de sistema do PC, não para a camada de TV.
    assert resolve(fala) is None


# --------------------------------------------------------------
# Celular: a tela DO CELULAR é o celular
# --------------------------------------------------------------

@pytest.mark.parametrize("fala", [
    "deixa eu ver a tela do celular",
    "espelha a tela do celular",
    "me mostra a tela do celular",
])
def test_tela_do_celular_e_mirror_do_celular(fala):
    assert resolve(fala) == ("mirror", {})


# --------------------------------------------------------------
# TV: apps e YouTube
# --------------------------------------------------------------

def test_abrir_app_na_tv():
    assert resolve("abre o youtube na tv") == ("open_app", {"app": "youtube"})


def test_pesquisar_no_youtube_da_tv():
    action, kwargs = resolve("pesquisa lofi no youtube da tv")
    assert action == "search_youtube"
    assert kwargs.get("query") == "lofi"


# --------------------------------------------------------------
# Guarda de segurança: interpret nunca executa nada
# --------------------------------------------------------------

def test_interpret_nao_executa_comando_destrutivo():
    """'desliga o computador' via interpret NÃO pode chamar o desligamento real."""
    chamou = {"shutdown": 0}

    class FakeCommands:
        def has_pending_homework(self):
            return False

        def shutdown_pc(self):
            chamou["shutdown"] += 1
            return "Desligando o computador."

    parser = VegaCommandsParser(FakeCommands())
    matched, _resposta = parser.interpret("desliga o pc")
    assert matched
    assert chamou["shutdown"] == 0, "interpret NUNCA pode executar a ação real"