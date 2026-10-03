"""Reconhece pedidos naturais para abrir a visualização do segundo cérebro."""

import re


_VIEW_PATTERNS = (
    re.compile(r"\b(deixa eu|quero|posso|me deixa)\b.*\b(ver|mostrar|conhecer)\b.*\b(cerebro|mente|memoria)\b"),
    re.compile(r"\b(abre|mostra|exibe|visualiza)\b.*\b(cerebro|mente|rede neural|memorias)\b"),
    re.compile(r"\b(cerebro|mente)\b.*\b(da vega|dela|visual|grafico|gráfico)\b"),
)


def is_brain_view_request(text):
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    normalized = normalized.replace("ç", "c").replace("é", "e").replace("ó", "o")
    return any(pattern.search(normalized) for pattern in _VIEW_PATTERNS)
