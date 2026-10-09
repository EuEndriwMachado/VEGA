# -*- coding: utf-8 -*-
"""Catálogo unificado de capacidades da VEGA.

Uma fonte única que agrega as duas camadas vivas de capacidade — as regras do
parser de comandos (execução local, custo zero) e as ferramentas registradas
(execução externa, custo de rede) — num formato só, consumível por qualquer
componente que precise saber "o que a VEGA sabe fazer".

Ver `core.capabilities.catalog`.
"""

from core.capabilities.catalog import (
    Capability,
    CapabilityCatalog,
    KIND_COMMAND,
    KIND_TOOL,
)

__all__ = [
    "Capability",
    "CapabilityCatalog",
    "KIND_COMMAND",
    "KIND_TOOL",
]
