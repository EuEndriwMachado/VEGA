# -*- coding: utf-8 -*-
"""Classificador universal de confirmação (sim/não) para perguntas da VEGA.

Usado por TODOS os lugares onde a VEGA faz uma pergunta de sim/não (oferta de
notícias, confirmação de ação perigosa, fechamento de app...). Viver aqui e não
espalhado em funções locais é o que garante o comportamento consistente.

Regras:
- A confirmação pode estar em qualquer posição da fala ("oi vega, quero sim").
- Vocativos iniciais ("oi", "vega", "então") são descartados antes da análise.
- Negação composta ("não quero") vence o termo positivo contido ("quero").
- Ambiguidade ("sim, mas não agora"): a última intenção dita vence.
- Retorna None quando a fala não é resposta de sim/não (segue o fluxo normal).
"""

from __future__ import annotations

import re
import unicodedata

# Vocativos e muletas de fala que podem preceder a resposta real.
_VOCATIVE_PATTERN = r"^(?:oi|ola|opa|ei|hey|vega|por favor|pfv|bom|bem|entao|ah|hmm|tá|ta|bele|bea)+\s+"

NEGATIVE_PHRASES = (
    "nao quero", "nao quero saber", "agora nao", "nao agora",
    "nao precisa", "nao obrigado", "deixa pra la", "deixa quieto",
    "mais tarde", "depois", "nao", "deixa", "esquece",
)

POSITIVE_PHRASES = (
    "sim", "quero", "quero saber", "quero sim", "pode falar", "pode ler",
    "manda ai", "manda ver", "claro", "com certeza", "isso", "isso mesmo",
    "leia", "le", "pode", "bora", "vai", "ok", "beleza",
)

# Negações que contêm um termo positivo como substring: decidem sozinhas.
STRONG_NEGATIVES = (
    "nao quero", "nao quero saber", "nao precisa", "nao obrigado",
    "nao agora", "agora nao", "deixa pra la", "deixa quieto",
)


def normalize_for_confirmation(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação, sem vocativo inicial."""
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", str(text or "").lower())
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    normalized = re.sub(_VOCATIVE_PATTERN, "", normalized).strip()
    return normalized


def _has(normalized: str, phrase: str) -> bool:
    """Busca por frase inteira: "sim" dentro de "simulado" não confirma."""
    return bool(re.search(rf"\b{re.escape(phrase)}\b", normalized))


def classify_confirmation(text, *, accept_imperative=False):
    """Devolve True (sim), False (não) ou None (não é resposta de sim/não).

    ``accept_imperative``: quando a VEGA acabou de fazer uma pergunta do tipo
    "quer que eu faça X?", uma resposta IMPERATIVA ("abre aí", "pode abrir",
    "vai", "manda") significa SIM — é a intenção do usuário, não uma
    palavra-chave. Antes, "abre aí" retornava None e a VEGA respondia
    "abrir o quê?" logo depois de ELA MESMA ter oferecido abrir algo.
    """
    normalized = normalize_for_confirmation(text)
    if not normalized:
        return None

    if any(_has(normalized, phrase) for phrase in STRONG_NEGATIVES):
        return False

    # Negação simples: decide, EXCETO quando há positivo DEPOIS dela
    # ("não, quer dizer, sim" — o usuário se corrigiu). Nesse caso cai na
    # regra da última intenção lá embaixo.
    if any(_has(normalized, word) for word in ("nao", "deixa", "esquece")):
        last_neg = max(normalized.rfind(w) for w in ("nao", "deixa", "esquece") if _has(normalized, w))
        if not (has_positive := any(
            _has(normalized, phrase) for phrase in POSITIVE_PHRASES
        )) or normalized.rfind(
            next((p for p in POSITIVE_PHRASES if _has(normalized, p)), "")
        ) < last_neg:
            return False

    # Imperativos: só fazem sentido como resposta quando havia uma pergunta
    # pendente (accept_imperative=True). "abre aí" solto na conversa NÃO é
    # um "sim" — é um comando, e deve ir para o fluxo normal de comandos.
    if accept_imperative:
        imperatives = (
            "pode abrir", "pode fazer", "pode ler", "manda", "vai",
            "bora", "vamo", "vamos", "toca", "leia", "le",
        )
        if any(_has(normalized, phrase) for phrase in imperatives):
            return True
        # "abre aí", "abre isso": imperativo SEM objeto específico. Quando a
        # VEGA acabou de oferecer abrir algo, é aceitação. Se a fala tem um
        # OBJETO próprio ("abre o chrome", "abre a planilha"), é um comando
        # novo — não uma resposta à pergunta.
        bare = re.sub(
            r"^(?:abre|abra|abrir|faz|faca|mostra|mostre|toca|le|leia)(?:\s+|$)",
            "",
            normalized,
        ).strip()
        object_words = (
            "chrome", "youtube", "spotify", "site", "arquivo", "planilha",
            "documento", "app", "aplicativo", "pasta", "camera", "tv", "celular",
            "janela", "notepad", "word", "excel", "powerpoint", "steam", "discord",
        )
        if not bare:
            # Verbo sozinho ("abre") após a oferta da VEGA: aceitação.
            return True
        if not any(_has(normalized, word) for word in object_words):
            if bare in {"ai", "aí", "isso", "pronto", "ja", "sim", "entao", "pra mim", "para mim", "de novo"}:
                return True

    has_negative = any(_has(normalized, phrase) for phrase in NEGATIVE_PHRASES)
    has_positive = any(_has(normalized, phrase) for phrase in POSITIVE_PHRASES)

    if has_negative and not has_positive:
        return False
    if has_positive and not has_negative:
        return True
    if not has_positive and not has_negative:
        return None

    # Ambos presentes sem negação composta ("sim, mas não agora"): a última
    # intenção dita vence, porque reflete a decisão final do usuário.
    last_negative = max(
        (normalized.rfind(phrase) for phrase in NEGATIVE_PHRASES if _has(normalized, phrase)),
        default=-1,
    )
    last_positive = max(
        (normalized.rfind(phrase) for phrase in POSITIVE_PHRASES if _has(normalized, phrase)),
        default=-1,
    )
    return last_positive > last_negative
