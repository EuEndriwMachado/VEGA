# -*- coding: utf-8 -*-
"""Base de conhecimento (RAG local): ingestão, busca e contexto.

Garante:
  1. ingestão guarda trechos com a origem preservada;
  2. reingerir a mesma fonte substitui em vez de duplicar;
  3. a busca encontra por PARÁFRASE, não só por palavra idêntica;
  4. a busca NÃO inventa resultado quando não há nada relevante;
  5. o contexto para o modelo cita a origem e respeita o limite;
  6. a persistência sobrevive a um novo carregamento.
"""

import os
import tempfile

import pytest

from core.knowledge import KnowledgeBase, split_into_chunks
from core.knowledge.knowledge_base import fold, tokens


@pytest.fixture
def knowledge():
    folder = tempfile.mkdtemp()
    return KnowledgeBase(path=os.path.join(folder, "index.json"))


BIOLOGIA = (
    "A fotossintese e o processo pelo qual as plantas convertem luz solar em "
    "energia quimica. A clorofila, pigmento verde presente nos cloroplastos, "
    "absorve luz nas faixas azul e vermelha. O oxigenio e liberado como "
    "subproduto dessa reacao."
)
HISTORIA = (
    "A Revolucao Frances teve inicio em 1789, com a queda da Bastilha. Ela "
    "derrubou a monarquia absoluta e deu origem a Declaracao dos Direitos do "
    "Homem e do Cidadao."
)


# ------------------------------------------------------------------
# 1. Ingestão
# ------------------------------------------------------------------

def test_ingest_stores_document_and_chunks(knowledge):
    result = knowledge.ingest(BIOLOGIA, source="biologia.pdf", kind="pdf")
    assert result["status"] == "ingested"
    assert result["chunks"] >= 1
    assert knowledge.is_empty() is False


def test_ingest_empty_text_is_rejected(knowledge):
    result = knowledge.ingest("", source="vazio.pdf")
    assert result["status"] == "empty"
    assert knowledge.is_empty() is True


def test_ingest_keeps_source(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    documents = knowledge.list_documents()
    assert [doc["source"] for doc in documents] == ["biologia.pdf"]


def test_reingest_same_source_replaces_instead_of_duplicating(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    before = len(knowledge.list_documents())
    knowledge.ingest(BIOLOGIA + " Texto adicional sobre fotossintese.", source="biologia.pdf")
    assert len(knowledge.list_documents()) == before
    assert len(knowledge.search("fotossintese", limit=50)) <= 5


def test_forget_removes_document(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    assert knowledge.forget("biologia.pdf") is True
    assert knowledge.is_empty() is True


def test_forget_unknown_returns_false(knowledge):
    assert knowledge.forget("nao-existe.pdf") is False


# ------------------------------------------------------------------
# 2. Busca por relevância
# ------------------------------------------------------------------

def test_search_finds_by_paraphrase(knowledge):
    """A pergunta não usa as mesmas palavras do texto — e ainda deve encontrar."""
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    matches = knowledge.search("como as plantas produzem energia")
    assert matches
    assert matches[0]["source"] == "biologia.pdf"


def test_search_ranks_the_right_document_first(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    knowledge.ingest(HISTORIA, source="historia.pdf")
    matches = knowledge.search("quando comecou a revolucao francesa")
    assert matches[0]["source"] == "historia.pdf"


def test_search_returns_nothing_when_unrelated(knowledge):
    """Sem relação, a resposta é vazia — a VEGA não inventa fonte."""
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    assert knowledge.search("cotacao do dolar bitcoin") == []


def test_search_on_empty_base_is_empty(knowledge):
    assert knowledge.search("qualquer coisa") == []


def test_search_respects_limit(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    knowledge.ingest(HISTORIA, source="historia.pdf")
    assert len(knowledge.search("energia revolucao", limit=1)) == 1


def test_search_results_carry_source_and_score(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    match = knowledge.search("fotossintese")[0]
    assert match["source"] == "biologia.pdf"
    assert match["score"] > 0
    assert match["text"]


# ------------------------------------------------------------------
# 3. Contexto para o modelo
# ------------------------------------------------------------------

def test_context_mentions_the_source(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    context = knowledge.build_context("fotossintese clorofila")
    assert "biologia.pdf" in context
    assert "CONHECIMENTO INGERIDO" in context


def test_context_is_empty_without_relevant_knowledge(knowledge):
    knowledge.ingest(BIOLOGIA, source="biologia.pdf")
    assert knowledge.build_context("cotacao do dolar") == ""


def test_context_respects_max_chars(knowledge):
    long_text = "palavra " * 3000
    knowledge.ingest(long_text, source="grande.txt")
    context = knowledge.build_context("palavra", limit=50, max_chars=500)
    assert len(context) < 1500


# ------------------------------------------------------------------
# 4. Persistência
# ------------------------------------------------------------------

def test_index_survives_reload():
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "index.json")
    first = KnowledgeBase(path=path)
    first.ingest(BIOLOGIA, source="biologia.pdf")

    second = KnowledgeBase(path=path)
    assert second.is_empty() is False
    assert second.search("fotossintese")[0]["source"] == "biologia.pdf"


def test_missing_file_starts_empty():
    folder = tempfile.mkdtemp()
    base = KnowledgeBase(path=os.path.join(folder, "nao-existe.json"))
    assert base.is_empty() is True


# ------------------------------------------------------------------
# 5. Divisão em blocos
# ------------------------------------------------------------------

def test_short_text_is_a_single_chunk():
    assert split_into_chunks("Texto curto.") == ["Texto curto."]


def test_long_text_is_split_with_overlap():
    text = "Frase de teste. " * 200
    chunks = split_into_chunks(text, size=300, overlap=50)
    assert len(chunks) > 1
    # Cada pedaço cabe no limite.
    assert all(len(chunk) <= 320 for chunk in chunks)


def test_empty_text_has_no_chunks():
    assert split_into_chunks("   ") == []


# ------------------------------------------------------------------
# 6. Normalização
# ------------------------------------------------------------------

def test_fold_removes_accents_and_punctuation():
    assert fold("Fotossíntese, CLOROFILA!") == "fotossintese clorofila"


def test_tokens_drop_stopwords_and_short_words():
    result = tokens("A energia das plantas é o processo")
    assert "energia" in result
    assert "plantas" in result
    assert "a" not in result
    assert "das" not in result
