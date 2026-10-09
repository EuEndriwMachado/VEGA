# -*- coding: utf-8 -*-
"""Compatibilidade: o registro único de ferramentas é o `ToolManager`.

Histórico
---------
Este módulo era um segundo registro de ferramentas, paralelo ao oficial
(`tools/api_manager.ToolManager`), com formato incompatível (`"function"` em
vez de `implementation`). Nenhum código de produção o consultava: era uma
"segunda verdade" sobre as mesmas ações, que só servia para divergir com o
tempo.

As cinco capacidades que ele registrava (hora, data, abrir site, abrir
programa, abrir pasta) foram movidas para o registro OFICIAL, em
`tools/api_tools.build_tool_registry` — nenhuma foi removida.

`ToolRegistry` permanece como fachada fina sobre o `ToolManager`, para que
qualquer import antigo continue funcionando em vez de quebrar.
"""

from __future__ import annotations

from typing import Any, Optional


class ToolRegistry:
    """Fachada de compatibilidade sobre o registro oficial de ferramentas."""

    def __init__(self, manager: Optional[Any] = None):
        if manager is None:
            from tools.api_tools import build_tool_registry

            manager = build_tool_registry()
        self._manager = manager

    def register(self, name, description, function):
        """Registra uma ferramenta no registro oficial.

        Mantém a assinatura antiga (nome, descrição, função) por
        compatibilidade, traduzindo para o `ToolSpec` oficial.
        """
        from tools.api_manager import ToolSpec

        self._manager.register(ToolSpec(
            name=str(name),
            purpose=str(description or ""),
            category="compatibilidade",
            parameters=[],
            implementation=function,
        ))
        return self._manager.get_tool(str(name))

    def get(self, name):
        return self._manager.get_tool(name)

    def list_tools(self):
        return self._manager.list_tools()

    def has(self, name):
        return self._manager.get_tool(name) is not None

    def execute(self, name, *args, **kwargs) -> Any:
        spec = self._manager.get_tool(name)
        if spec is None or spec.implementation is None:
            raise ValueError(f"Ferramenta '{name}' não encontrada.")
        return spec.implementation(*args, **kwargs)
