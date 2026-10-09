# -*- coding: utf-8 -*-
"""Gatilhos de visão da VEGA (tela e câmera) como DADOS, não como `if`.

Por que existe
--------------
As frases que pedem "olhe a minha tela" ou "o que você está vendo na câmera?"
estavam espalhadas em TRÊS listas quase idênticas dentro do cérebro, cada uma
com pequenas diferenças de acentuação e escopo. Manter as três em sincronia era
impossível: uma frase nova exigia lembrar de todos os lugares, e a guarda que
decide "tela vence câmera" era um remendo em cima da duplicação.

Aqui os gatilhos são um catálogo único e declarativo. O reconhecimento consulta
o catálogo; adicionar uma frase é adicionar um item, nunca um `if` novo.

Regra de ouro (a mesma que já existia, agora garantida por construção):
    Se a fala cita EXPLICITAMENTE a tela/monitor/print, é captura de TELA.
    Caso contrário, um gatilho de visão ambíguo ("o que você está vendo?")
    significa CÂMERA — que é o padrão do usuário.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional

# Palavras que forçam a interpretação como TELA, seja qual for o resto da fala.
# Sem esta guarda, "o que esta vendo na minha tela" casava com o gatilho
# genérico de câmera e a VEGA abria a webcam em vez de fotografar a tela.
SCREEN_MARKERS = ("tela", "monitor", "screenshot", "print")

# Gatilhos de TELA: pedem explicitamente a captura do que está na tela.
#
# As formas curtas ("olha minha tela", "me mostra a tela") estavam de fora e a
# VEGA simplesmente NÃO FAZIA NADA ao ouvi-las — o usuário pedia para olhar a
# tela e não recebia resposta. Aqui a lista cobre o pedido em si; o alvo (tela
# do PC ou do celular) é decidido depois, pela menção ao dispositivo.
SCREEN_TRIGGERS = (
    "olha minha tela",
    "olha a minha tela",
    "olha pra minha tela",
    "olha para a minha tela",
    "olha na minha tela",
    "olha pra tela",
    "olha para a tela",
    "olha a tela",
    "olha isso na minha tela",
    "olha isso na tela",
    "olha o que esta na tela",
    "mostra a tela",
    "mostre a tela",
    "me mostra a tela",
    "me mostre a tela",
    "exibe a tela",
    "quero ver a tela",
    "deixa eu ver a tela",
    "ve minha tela",
    "ver minha tela",
    "analisa a tela",
    "analisa minha tela",
    "analisa a minha tela",
    "descreve a tela",
    "da uma olhada na tela",
    "o que esta na minha tela",
    "o que tem na minha tela",
    "o que esta na tela",
    "o que tem na tela",
    "o que aparece na tela",
    "o que esta aparecendo na tela",
    "o que voce esta vendo na minha tela",
    "o que voce esta vendo na tela",
    "o que esta vendo na tela",
    "o que esta vendo na minha tela",
)

# Gatilhos de AJUDA com a tela: pedem análise do que está sendo trabalhado.
# São o caminho escolar/código: o usuário quer ajuda com o que está vendo.
ASSISTANCE_TRIGGERS = (
    "olha isso",
    "o que tem de errado aqui",
    "o que esta errado aqui",
    "o que tem de errado nisso",
    "me ajuda com isso",
    "me ajuda aqui",
    "me ajuda com esse codigo",
    "me ajuda com esse trabalho",
    "me ajuda com essa atividade",
    "revisa esse codigo",
    "analisa esse codigo",
    "corrige esse erro",
    "o que eu faco aqui",
    "qual o proximo passo",
    "como eu continuo daqui",
)

# Gatilhos de CÂMERA: citam a câmera/webcam explicitamente.
CAMERA_TRIGGERS = (
    "o que voce esta vendo na camera",
    "o que voce esta vendo na minha camera",
    "o que esta vendo na camera",
    "descreve a camera",
    "analisa a camera",
    "olha pela camera",
    "olha na camera",
    "o que aparece na webcam",
    "o que tem na webcam",
)

# Gatilhos de câmera SEM citar a câmera: fala natural do dia a dia.
# "o que você está vendo?" é um pedido de visão da câmera por padrão.
#
# Como as abreviações são expandidas na normalização (`fold`), aqui basta a
# forma por extenso — "o que vc esta vendo" e "oq vc ta vendo" chegam iguais.
CAMERA_IMPLICIT_TRIGGERS = (
    "o que voce esta vendo agora",
    "o que voce esta vendo ai",
    "o que voce esta vendo",
    "o que voce ve agora",
    "o que voce ve",
    "o que esta vendo",
    "que voce esta vendo",
    "que esta vendo",
    "voce esta vendo",
    "esta vendo",
)

# Pedido explícito de mapa mental da tela (exige resumo anterior disponível).
MINDMAP_TRIGGERS = ("mapa mental",)


def fold(text: str) -> str:
    """Forma canônica de comparação: minúsculas, sem acento, sem abreviação.

    Os gatilhos são escritos SEM acento de propósito: assim a mesma lista vale
    para texto digitado ("está") e transcrito ("esta"), sem duplicar entradas.

    As abreviações são expandidas aqui — e não na lista de gatilhos — porque
    listar cada variação ("vc"/"voce", "oq"/"o que") fazia o reconhecimento
    depender de adivinhação: "o que voce esta vendo" funcionava, mas
    "o que vc esta vendo" NÃO, e o usuário ficava sem resposta. Normalizar na
    entrada cobre qualquer combinação de uma vez só.
    """
    value = str(text or "").lower()
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    return expand_abbreviations(value)


# Abreviações comuns em transcrição de voz e digitação. Expandidas na
# normalização para que uma única forma de gatilho valha para todas elas.
_ABBREVIATIONS = (
    (r"\bvc\b", "voce"),
    (r"\bvcs\b", "voces"),
    (r"\boq\b", "o que"),
    (r"\bq\b", "que"),
    (r"\bta\b", "esta"),
    (r"\bt\b", "te"),
    (r"\bto\b", "estou"),
    (r"\btd\b", "tudo"),
    (r"\bn\b", "nao"),
    (r"\bcmg\b", "comigo"),
    (r"\bqq\b", "qualquer"),
    (r"\bpq\b", "porque"),
    (r"\bqdo\b", "quando"),
    (r"\bqnd\b", "quando"),
    (r"\bmto\b", "muito"),
    (r"\bmta\b", "muita"),
    (r"\bmsm\b", "mesmo"),
    (r"\btbm\b", "tambem"),
    (r"\bagr\b", "agora"),
    (r"\bporfavor\b", "por favor"),
)

_ABBREVIATION_PATTERNS = tuple(
    (re.compile(pattern), replacement) for pattern, replacement in _ABBREVIATIONS
)


def expand_abbreviations(text: str) -> str:
    """Troca abreviações pelas palavras completas, respeitando fronteira.

    Só substitui palavras inteiras: "vc" vira "voce", mas "vcs" não vira
    "voces" por acidente nem "tva" é tocada. É idempotente.
    """
    value = str(text or "")
    for pattern, replacement in _ABBREVIATION_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _contains_any(folded: str, triggers: Iterable[str]) -> bool:
    return any(trigger in folded for trigger in triggers)


def mentions_screen(text: str) -> bool:
    """A fala cita explicitamente a tela/monitor/print?"""
    folded = fold(text)
    return any(marker in folded for marker in SCREEN_MARKERS)


def is_screen_request(text: str) -> bool:
    """A fala pede uma captura/análise da TELA?"""
    folded = fold(text)
    if not folded:
        return False
    return _contains_any(folded, SCREEN_TRIGGERS) or _contains_any(
        folded, ASSISTANCE_TRIGGERS
    )


def is_mindmap_request(text: str, has_previous_summary: bool = False) -> bool:
    """Pedido de mapa mental — só vale quando já há um resumo visual anterior.

    Sem o resumo, "mapa mental" é um pedido de conteúdo novo (não de visão) e
    deve seguir o fluxo normal da IA.
    """
    folded = fold(text)
    return bool(has_previous_summary) and _contains_any(folded, MINDMAP_TRIGGERS)


def is_camera_request(text: str) -> bool:
    """A fala pede visão da CÂMERA?

    Tela vence câmera: qualquer menção a tela/monitor/print desqualifica a
    interpretação como câmera, mesmo que o gatilho seja ambíguo.
    """
    folded = fold(text)
    if not folded:
        return False
    if any(marker in folded for marker in SCREEN_MARKERS):
        return False
    return _contains_any(folded, CAMERA_TRIGGERS) or _contains_any(
        folded, CAMERA_IMPLICIT_TRIGGERS
    )


@dataclass(frozen=True)
class VisualRequest:
    """Resultado do reconhecimento de um pedido de visão.

    kind: "screen", "camera" ou None (não é pedido de visão).
    """

    kind: Optional[str] = None

    @property
    def is_visual(self) -> bool:
        return self.kind in {"screen", "camera"}

    @property
    def wants_camera(self) -> bool:
        return self.kind == "camera"


def classify_visual_request(text: str, has_previous_summary: bool = False) -> VisualRequest:
    """Classifica a fala em pedido de TELA, CÂMERA ou nenhum dos dois.

    Ordem deliberada:
      1. mapa mental (só com resumo anterior) -> tela;
      2. câmera explícita/implícita -> câmera (tela vence, ver guarda acima);
      3. gatilhos de tela/ajuda -> tela.

    A câmera vem antes da tela porque um gatilho implícito de câmera
    ("o que você está vendo") apareceria também dentro de frases de tela —
    que já foram descartadas pela guarda de marcadores em `is_camera_request`.
    """
    if not str(text or "").strip():
        return VisualRequest(None)

    if is_mindmap_request(text, has_previous_summary):
        return VisualRequest("screen")

    if is_camera_request(text):
        return VisualRequest("camera")

    if is_screen_request(text):
        return VisualRequest("screen")

    return VisualRequest(None)


def describe() -> List[dict]:
    """Inventário dos gatilhos, para diagnóstico e teste."""
    return [
        {"kind": "screen", "trigger": trigger}
        for trigger in (*SCREEN_TRIGGERS, *ASSISTANCE_TRIGGERS)
    ] + [
        {"kind": "camera", "trigger": trigger}
        for trigger in (*CAMERA_TRIGGERS, *CAMERA_IMPLICIT_TRIGGERS)
    ]
