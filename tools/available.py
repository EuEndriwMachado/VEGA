from tools.registry import ToolRegistry
from tools.system import (
    get_current_time,
    get_current_date,
    open_website,
    open_program,
    open_folder,
)


def create_tool_registry():
    registry = ToolRegistry()

    registry.register(
        "hora",
        "Informa a hora atual.",
        get_current_time
    )

    registry.register(
        "data",
        "Informa a data atual.",
        get_current_date
    )

    registry.register(
        "abrir_site",
        "Abre um site no navegador.",
        open_website
    )

    registry.register(
        "abrir_programa",
        "Abre um programa do Windows.",
        open_program
    )

    registry.register(
        "abrir_pasta",
        "Abre uma pasta do Windows.",
        open_folder
    )

    return registry
