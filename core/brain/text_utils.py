"""Funções puras de normalização e análise de texto para o cérebro da VEGA.

Extraídas de `core.brain.brain` para reduzir o tamanho do módulo de orquestração
e permitir testes focados.
"""

import re


# Correções de erros comuns de transcrição/grafia de voz.
_VEGA_TEXT_CORRECTIONS = {
    "spotfy": "spotify",
    "spotif": "spotify",
    "spofity": "spotify",
    "spotifyy": "spotify",
    "chorme": "chrome",
    "crome": "chrome",
    "discrod": "discord",
    "discor": "discord",
    "stema": "steam",
    "steem": "steam",
    "vscod": "vscode",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "whats app": "whatsapp",
    "wpp": "whatsapp",
    "som": "volume",
    "áudio": "audio",
    "brilho da tela": "brilho",
    "luminosidade": "brilho",
    "computador": "pc",
    "computadorzinho": "pc",
    "apgue": "apague",
    "apga": "apaga",
    "apagarar": "apagar",
    "esvazie": "esvazie",
    "esvaziaa": "esvazia",
    "exclui": "exclua",
    "exclue": "exclua",
    "removae": "remova",
    "desliguee": "desligue",
    "reiniciaa": "reinicia",
    "formatei": "formate",
    "abri": "abrir",
    "fechi": "feche",
    "ligaa": "liga",
    "aumente o som": "aumente o volume",
    "diminua o som": "diminua o volume",
}

_SAFE_SHORT_WORDS = {
    "oi", "ola", "olá", "eai", "ei", "hey", "como", "vai", "bem", "tudo",
    "quem", "qual", "onde", "quando", "porque", "oque", "seu", "nome",
    "você", "voce", "vega", "ajuda", "ajudar", "hora", "horas", "dia",
    "data", "hoje", "música", "musica", "sim", "nao", "não", "talvez",
    "claro", "certo",
}

_ALLOWED_KEYWORDS = (
    "abrir", "fechar", "ligar", "desligar", "playlist", "youtube", "spotify",
    "play", "pause", "memoria", "lembre", "esqueça", "esqueca",
    "who", "what", "when", "where",
)

_VALID_SINGLE_WORDS = {
    "oi", "ola", "olá", "ei", "hey", "sair", "exit", "quit", "ajuda", "hoje",
    "hora", "horas", "dia", "data", "vega", "musica", "música", "spotify",
    "youtube", "play", "pause", "memoria", "sim", "nao", "não", "talvez",
    "claro", "certo",
}


def normalize_text(text):
    """Normaliza fala transcrita: minúsculas, correções de grafia e espaços."""

    text = text.lower().strip()

    for wrong, correct in _VEGA_TEXT_CORRECTIONS.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", correct, text)

    text = re.sub(r"[.,!?;:]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def remove_vega_prefix(text):
    """Remove o prefixo "vega" no começo de uma fala."""

    text = text.strip()
    for pattern in (r"^vega[\s,:;-]+", r"^vega$"):
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

    return text


def is_short_nonsense(text):
    """Detecta falas curtas sem conteúdo real (ruído de transcrição).

    Números NÃO são ruído: uma sequência numérica curta é exatamente como o
    usuário informa o PIN da VEGA ("5679"). Antes, "5679" era descartado aqui
    e a VEGA respondia "Estou ouvindo." sem nunca concluir a autorização — o
    PIN morria antes de chegar ao portão de segurança.
    """

    if not text:
        return False

    normalized = re.sub(r"[^a-z0-9\sáàãâéêíóõôúç]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return True

    # Qualquer token puramente numérico é tratado como conteúdo válido (PIN,
    # valores, horários etc.). Não pode ser descartado como ruído.
    if any(token.isdigit() for token in normalized.split()):
        return False

    words = normalized.split()
    if len(words) > 3:
        return False

    if any(word in _SAFE_SHORT_WORDS for word in words):
        return False

    if any(keyword in normalized for keyword in _ALLOWED_KEYWORDS):
        return False

    if len(words) == 1:
        word = words[0]
        if word in _VALID_SINGLE_WORDS:
            return False
        if len(word) <= 2:
            return True
        if re.fullmatch(r"[a-z]+", word) and not any(ch in word for ch in "aeiou"):
            return True
        if re.fullmatch(r"[a-z]{1,3}", word):
            return True
        if len(word) <= 4 and not any(
            word.startswith(prefix)
            for prefix in ("abr", "fech", "lig", "deslig", "lemb", "esque", "como", "qual")
        ):
            return True
        return False

    if len(words) == 2:
        if all(len(w) <= 2 for w in words):
            return True
        if any(w in {"ta", "tá", "ue", "ué", "eh", "ok"} for w in words):
            return True

    return False
