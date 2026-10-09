# -*- coding: utf-8 -*-
"""Base de conhecimento da VEGA (RAG local).

Ver `core.knowledge.knowledge_base`.
"""

from core.knowledge.knowledge_base import (
    KnowledgeBase,
    Chunk,
    ingest_pdf,
    split_into_chunks,
)

__all__ = [
    "KnowledgeBase",
    "Chunk",
    "ingest_pdf",
    "split_into_chunks",
]
