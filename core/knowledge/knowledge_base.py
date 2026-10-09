# -*- coding: utf-8 -*-
"""Base de conhecimento consultável da VEGA (RAG local, sem dependências).

Por que existe
--------------
A VEGA respondia bem sobre o que estava no código e na memória diária, mas não
sabia nada sobre o que o usuário tinha em documentos, páginas e vídeos. Era a
lacuna mais visível frente a assistentes que fazem RAG: qualquer pergunta sobre
um PDF ou um artigo ficava por conta do que o modelo já sabia de treino.

O que este módulo faz
---------------------
- INGERE um texto longo (de PDF, página, transcrição, arquivo) e o guarda em
  pedaços ("chunks") com a origem preservada;
- CONSULTA por relevância e devolve os trechos mais próximos da pergunta,
  prontos para virar contexto do modelo.

Por que não embeddings
----------------------
Embeddings de verdade exigiriam baixar um modelo e manter um índice vetorial —
peso, latência e dependência de rede que a VEGA não tem hoje (ela é
offline-first por decisão). A busca aqui é LÉXICA, mas feita com cuidado:

- normalização (sem acentos, sem pontuação, minúsculas);
- remoção de palavras vazias;
- peso IDF: um termo raro ("fotossíntese") vale muito mais que um comum
  ("trabalho"), e é isso que separa relevância de coincidência;
- busca por radicais simples, para casar "calcular" com "cálculo".

Isso não substitui um índice vetorial em escala de milhares de documentos, mas
entrega a experiência real de RAG: a VEGA responde sobre o que o usuário
ingeriu, com a fonte citada. O índice pode ser trocado por vetorial depois sem
mudar a interface (`KnowledgeBase.search`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# Tamanho dos pedaços. Grande o bastante para conter uma ideia inteira, pequeno
# o bastante para caber no contexto sem desperdiçar tokens.
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150

# Palavras vazias do português. Não discriminam nada e inflariam o IDF.
_STOP_WORDS = frozenset({
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos",
    "das", "em", "no", "na", "nos", "nas", "por", "para", "pra", "com", "sem",
    "e", "ou", "que", "qual", "quais", "quando", "onde", "como", "porque",
    "porquê", "se", "ao", "aos", "à", "às", "pelo", "pela", "pelos", "pelas",
    "este", "esta", "esse", "essa", "isso", "isto", "aquele", "aquela",
    "aquilo", "seu", "sua", "seus", "suas", "meu", "minha", "meus", "minhas",
    "ele", "ela", "eles", "elas", "eu", "voce", "você", "nos", "nós", "me",
    "te", "lhe", "muito", "mais", "menos", "ja", "já", "tambem", "também",
    "so", "só", "tem", "ser", "sao", "são", "esta", "está", "foi", "eram",
})


def fold(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação."""
    value = str(text or "").lower()
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def tokens(text: str) -> List[str]:
    """Palavras significativas (sem vazias, sem números soltos curtos)."""
    return [
        token
        for token in fold(text).split()
        if token and token not in _STOP_WORDS and len(token) >= 3
    ]


def _stems(token: str) -> List[str]:
    """Radical simples: casa "calcular" com "cálculo" e "estudos" com "estudo".

    Português tem sufixos previsíveis (-ar, -er, -ir, -ção, -s). Cortar os mais
    comuns resolve a maior parte dos casos sem precisar de stemmer externo.
    """
    forms = {token}
    for suffix in ("coes", "cao", "mentos", "mento", "ando", "endo", "indo",
                   "acoes", "acao", "ares", "ar", "er", "ir", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            forms.add(token[: -len(suffix)])
    return sorted(forms)


def _stem_set(token: str) -> set:
    return set(_stems(token))


@dataclass
class Chunk:
    """Um pedaço de um documento ingerido, com a origem preservada."""

    text: str
    source: str
    position: int
    document_id: str
    kind: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source,
            "position": self.position,
            "document_id": self.document_id,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chunk":
        return cls(
            text=str(data.get("text", "")),
            source=str(data.get("source", "")),
            position=int(data.get("position", 0) or 0),
            document_id=str(data.get("document_id", "")),
            kind=str(data.get("kind", "text")),
        )


def split_into_chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Divide o texto em pedaços com sobreposição.

    A sobreposição existe para que uma ideia cortada na fronteira ainda apareça
    inteira em algum pedaço. O corte busca o fim de frase mais próximo quando
    possível, para não quebrar no meio de uma palavra.
    """
    cleaned = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    if len(cleaned) <= size:
        return [cleaned]

    chunks: List[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(length, start + size)
        if end < length:
            # Procura uma fronteira natural perto do fim do pedaço.
            window = cleaned[start:end]
            boundary = max(
                window.rfind(". "),
                window.rfind("! "),
                window.rfind("? "),
                window.rfind("\n"),
            )
            if boundary > size * 0.5:
                end = start + boundary + 1
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(start + 1, end - overlap)
    return chunks


def _document_id(source: str) -> str:
    digest = hashlib.sha1(str(source or "").encode("utf-8", "replace")).hexdigest()
    return f"doc_{digest[:12]}"


class KnowledgeBase:
    """Armazena documentos ingeridos e responde consultas por relevância."""

    def __init__(self, path: str = "data/knowledge/index.json"):
        self.path = path
        self._lock = threading.RLock()
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.chunks: List[Chunk] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(payload, dict):
            return
        documents = payload.get("documents", {})
        if isinstance(documents, dict):
            self.documents = documents
        raw_chunks = payload.get("chunks", [])
        if isinstance(raw_chunks, list):
            self.chunks = [
                Chunk.from_dict(item)
                for item in raw_chunks
                if isinstance(item, dict) and item.get("text")
            ]

    def _save(self) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        payload = {
            "documents": self.documents,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)

    # ------------------------------------------------------------------
    # Ingestão
    # ------------------------------------------------------------------

    def ingest(self, text: str, source: str = "", kind: str = "text") -> Dict[str, Any]:
        """Guarda um texto como conhecimento consultável.

        Reingerir a MESMA origem substitui o conteúdo anterior em vez de
        duplicá-lo — o índice cresce com o conhecimento, não com as tentativas.
        """
        content = str(text or "").strip()
        if not content:
            return {"status": "empty", "chunks": 0}

        label = str(source or "").strip() or "documento sem nome"
        document_id = _document_id(label)
        pieces = split_into_chunks(content)

        with self._lock:
            if document_id in self.documents:
                self.chunks = [
                    chunk for chunk in self.chunks if chunk.document_id != document_id
                ]
            record = {
                "id": document_id,
                "source": label,
                "kind": kind,
                "chunks": len(pieces),
                "chars": len(content),
                "ingested_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.documents[document_id] = record
            for position, piece in enumerate(pieces):
                self.chunks.append(Chunk(
                    text=piece,
                    source=label,
                    position=position,
                    document_id=document_id,
                    kind=kind,
                ))
            self._save()

        return {"status": "ingested", "document_id": document_id, "chunks": len(pieces)}

    def forget(self, source_or_id: str) -> bool:
        """Remove um documento do conhecimento."""
        key = str(source_or_id or "").strip()
        if not key:
            return False
        with self._lock:
            document_id = key if key in self.documents else _document_id(key)
            if document_id not in self.documents:
                return False
            self.documents.pop(document_id, None)
            self.chunks = [chunk for chunk in self.chunks if chunk.document_id != document_id]
            self._save()
        return True

    def list_documents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self.documents.values()]

    def is_empty(self) -> bool:
        with self._lock:
            return not self.chunks

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def _idf(self, term_forms: set) -> float:
        """Peso de raridade: termos que aparecem em muitos pedaços valem menos."""
        total = len(self.chunks) or 1
        containing = 0
        for chunk in self.chunks:
            chunk_forms = set()
            for token in tokens(chunk.text):
                chunk_forms |= _stem_set(token)
            if term_forms & chunk_forms:
                containing += 1
        # Suavizado: nunca zero, para um termo presente em tudo ainda pontuar.
        return math.log(1.0 + (total / (1.0 + containing)))

    def search(self, query: str, limit: int = 4) -> List[Dict[str, Any]]:
        """Devolve os trechos mais relevantes para a consulta.

        Retorna lista de dicts com `text`, `source`, `score` e `position`,
        ordenada por relevância (maior primeiro). Lista vazia quando não há
        conhecimento ou nada casa.
        """
        query_tokens = tokens(query)
        if not query_tokens:
            return []

        with self._lock:
            if not self.chunks:
                return []
            chunks_snapshot = list(self.chunks)

        # Radicais da consulta com peso IDF (raro = importante).
        weighted_query: Dict[str, float] = {}
        for token in query_tokens:
            forms = _stem_set(token)
            weight = self._idf(forms)
            for form in forms:
                weighted_query[form] = max(weighted_query.get(form, 0.0), weight)

        scored = []
        for chunk in chunks_snapshot:
            chunk_forms = set()
            for token in tokens(chunk.text):
                chunk_forms |= _stem_set(token)
            score = sum(weight for form, weight in weighted_query.items() if form in chunk_forms)
            if score > 0:
                # Normaliza pelo tamanho da consulta para comparar trechos.
                scored.append((score / len(query_tokens), chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, chunk in scored[: max(1, int(limit))]:
            results.append({
                "text": chunk.text,
                "source": chunk.source,
                "position": chunk.position,
                "score": round(float(score), 4),
            })
        return results

    def build_context(self, query: str, limit: int = 4, max_chars: int = 4000) -> str:
        """Monta o bloco de contexto (para o modelo) com os trechos relevantes.

        Devolve string vazia quando não há nada relevante — o chamador segue
        sem inventar contexto. Cada trecho traz a fonte, para a VEGA poder
        dizer de onde tirou a informação.
        """
        matches = self.search(query, limit=limit)
        if not matches:
            return ""

        lines = [
            "CONHECIMENTO INGERIDO (documentos e páginas que o usuário forneceu):",
            "Use estas informações como fonte quando forem relevantes à pergunta.",
            "Cite a origem de forma natural; não invente nada fora delas.",
        ]
        used = 0
        for match in matches:
            block = f"\n[{match['source']}] {match['text']}"
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Utilitários de leitura de origem
# ----------------------------------------------------------------------

def ingest_pdf(knowledge: KnowledgeBase, path: str, max_chars: int = 200_000) -> Dict[str, Any]:
    """Lê um PDF e o ingere na base de conhecimento."""
    from tools.productivity import ProductivityTools

    reader = ProductivityTools()
    data = reader.read_text(path, max_chars=max_chars)
    content = str(data.get("content", "")).strip()
    if not content:
        return {"status": "empty", "chunks": 0, "source": path}
    result = knowledge.ingest(content, source=os.path.basename(path) or path, kind="pdf")
    result["pages"] = data.get("pages")
    return result
