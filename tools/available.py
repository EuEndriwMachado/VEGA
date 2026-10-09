"""Compatibilidade: as ações básicas já vivem no registro oficial.

Este módulo registrava cinco ferramentas (hora, data, abrir site, abrir
programa, abrir pasta) num segundo registro que nenhum código chamava. Elas
foram movidas para `tools/api_tools.build_tool_registry` — o registro único e
oficial — e continuam disponíveis pelo mesmo nome.

`create_tool_registry()` continua existindo para quem importava daqui: agora
ela simplesmente devolve o registro oficial, sem manter uma segunda verdade.
"""


# Nomes antigos -> nomes no registro oficial. O apelido e mantido para que
# `registry.get("hora")` continue respondendo como antes.
_LEGACY_ALIASES = {
    "hora": "current_time",
    "data": "current_date",
    "abrir_site": "open_website",
    "abrir_programa": "open_program",
    "abrir_pasta": "open_folder",
}


class _AliasedRegistry:
    """Fachada que traduz os nomes antigos para o registro oficial."""

    def __init__(self, manager):
        self._manager = manager

    def _resolve(self, name):
        return _LEGACY_ALIASES.get(str(name), name)

    def register(self, name, description, function):
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
        return self._manager.get_tool(self._resolve(name))

    def has(self, name):
        return self._manager.get_tool(self._resolve(name)) is not None

    def list_tools(self):
        return self._manager.list_tools()

    def execute(self, name, *args, **kwargs):
        spec = self.get(name)
        if spec is None or spec.implementation is None:
            raise ValueError(f"Ferramenta '{name}' não encontrada.")
        return spec.implementation(*args, **kwargs)


def create_tool_registry():
    """Devolve o registro oficial, com os apelidos antigos resolvidos."""
    from tools.api_tools import build_tool_registry

    return _AliasedRegistry(build_tool_registry())
