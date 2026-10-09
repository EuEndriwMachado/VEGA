# -*- coding: utf-8 -*-
"""Modo cognitivo da VEGA — controle de como a fala vira ação e resposta.

Contexto
--------
Historicamente o cérebro (`core/brain/brain.py`) resolvia a fala com uma
sequência longa de portões: cada `if` reconhecia uma frase e devolvia TEXTO
PRONTO ("Estou ouvindo.", "Abrindo o paint.", "Tudo bem, cancelo isso."). Isso
funciona, mas engessa a VEGA: ela deixa de ser uma IA que reage e passa a ser
um menu de respostas — o famoso "quando ele fala isso, eu falo isso".

O modo cognitivo desacopla duas coisas que estavam grudadas:

1. **DECIDIR/EXECUTAR** a ação local (rápida, determinística, custo zero);
2. **FALAR** sobre o que aconteceu (sempre gerado pela IA, com personalidade).

Com `VEGA_COGNITIVE_MODE=generated`, as ações locais continuam sendo
executadas sem gastar token nenhum (você está certo: não faz sentido pagar IA
para "aumentar o volume"), mas o resultado volta para a IA como um FATO, e é
ela que escolhe como contar. Nenhum recurso é removido: os portões antigos
continuam existindo para reconhecer a intenção.

Modos
-----
- ``legacy`` (padrão): comportamento atual, byte a byte. Nada muda.
- ``generated``: ações locais executam igual, mas a fala final é da IA.

O padrão é ``legacy`` de propósito: a migração é verificável, não é um salto
de fé. Ligue o modo novo quando os testes estiverem verdes nas duas rotas.
"""

import os

from config.environment import load_vega_environment

load_vega_environment()


LEGACY = "legacy"
GENERATED = "generated"

_VALID_MODES = (LEGACY, GENERATED)

_MODE_DESCRIPTIONS = {
    LEGACY: (
        "VEGA fala com as respostas prontas dos portões de comando. "
        "Comportamento histórico, sem alteração."
    ),
    GENERATED: (
        "VEGA executa a ação local sem gastar IA, mas a fala final é gerada "
        "pelo modelo com a personalidade dela. Fim das respostas enlatadas."
    ),
}


def _read_mode() -> str:
    raw = os.getenv("VEGA_COGNITIVE_MODE", "").strip().lower()
    if raw in _VALID_MODES:
        return raw
    if raw:
        # Valor inválido não derruba a VEGA: ela cai no modo seguro e avisa no
        # console, em vez de levantar no import e impedir o startup.
        print(
            f"[VEGA Cognição] VEGA_COGNITIVE_MODE='{raw}' não é um modo conhecido "
            f"({', '.join(_VALID_MODES)}). Usando '{LEGACY}'."
        )
    return LEGACY


COGNITIVE_MODE = _read_mode()


def mode_is_generated() -> bool:
    """True quando a fala final deve ser gerada pela IA, não enlatada."""
    return get_mode() == GENERATED


def get_mode() -> str:
    """Lê o modo efetivo sem recarregar módulos em runtime."""
    raw = os.getenv("VEGA_COGNITIVE_MODE", "").strip().lower()
    return raw if raw in _VALID_MODES else LEGACY


def describe_cognitive_mode() -> str:
    return _MODE_DESCRIPTIONS.get(get_mode(), "")


def build_cognitive_context() -> str:
    """Trecho de sistema que explica à IA como narrar uma ação já executada.

    Só entra no prompt no modo `generated`. O ponto central: a IA não deve
    inventar o que aconteceu nem repetir a frase do sistema — ela recebe o
    resultado real e conta com a própria voz.
    """
    if not mode_is_generated():
        return ""
    return (
        "\n\nNARRAÇÃO DE AÇÕES EXECUTADAS:\n"
        "- Quando você receber o resultado de uma ação que já foi executada no "
        "computador, narre o que aconteceu com a sua própria voz e o seu jeito.\n"
        "- NUNCA invente um resultado que não foi informado. Se a ação falhou, "
        "diga que falhou, com o motivo real.\n"
        "- Não repita a descrição técnica da ferramenta; conte como uma pessoa "
        "contaria: curto, natural, sem cerimônia.\n"
        "- Não peça permissão para o que já foi feito, e não ofereça ajuda "
        "genérica no fim."
    )
