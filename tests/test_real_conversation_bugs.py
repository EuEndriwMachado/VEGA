# -*- coding: utf-8 -*-
"""Regressões dos bugs encontrados nos testes reais do Endriw.

Cada teste corresponde a uma falha observada na conversa de verdade:

1. "aumenta o brilho do pc" -> "Não consegui alterar o brilho."
   (PowerShell não expõe WmiSetBrightness como método direto)

2. "deixa mais claro" -> a VEGA respondeu sobre CLIMA.
   (o reconhecedor exigia a palavra "brilho"; sem ela, a fala escapava)

3. "cria uma apresentação sobre IA com 4 slides" ->
   "got an unexpected keyword argument 'content'"
   (o bloco antigo de _build_tool_parameters injetava params errados)

4. "cria uma planilha no Excel" ->
   "missing 2 required positional arguments: 'title' and 'rows'"

5. "desenha um cenário" -> "O desenho precisa de ao menos uma forma."
   (mesma causa do 3/4: params inventados por regex)
"""

import re

import pytest

from core.actions.commands import VegaCommands
from core.actions.commands_parser import VegaCommandsParser


class _FakeCommands:
    """Comandos falsos que registram as chamadas (para testar o parser)."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name == "has_pending_homework":
            return lambda: False

        def generic(*args, **kwargs):
            self.calls.append(name)
            return name

        return generic


@pytest.fixture
def parser():
    return VegaCommandsParser(_FakeCommands())


# ------------------------------------------------------------------
# Bug 1 — brilho precisa de Invoke-CimMethod
# ------------------------------------------------------------------

def test_brightness_uses_invoke_cim_method():
    """A forma antiga (.WmiSetBrightness(...)) falha em várias versões."""
    script = VegaCommands._set_brightness_ps(70)
    assert "Invoke-CimMethod" in script
    assert "WmiSetBrightness" in script
    assert "Brightness=70" in script
    # A forma antiga, que causava a falha, não pode voltar.
    assert ".WmiSetBrightness(" not in script


def test_brightness_percent_is_clamped_in_script():
    assert "Brightness=0" in VegaCommands._set_brightness_ps(-50)
    assert "Brightness=100" in VegaCommands._set_brightness_ps(999)


def test_brightness_set_validates_non_numeric():
    assert "número" in VegaCommands.set_brightness("muito claro")


# ------------------------------------------------------------------
# Bug 2 — "mais claro/escuro" sem a palavra "brilho"
# ------------------------------------------------------------------

@pytest.mark.parametrize("fala,esperado", [
    ("deixa mais claro", "brightness_up"),
    ("deixa o pc mais claro", "brightness_up"),
    ("deixa a tela mais clara", "brightness_up"),
    ("deixa o pc mais escuro", "brightness_down"),
    ("deixa mais escuro aqui", "brightness_down"),
])
def test_natural_brightness_phrases_are_recognized(parser, fala, esperado):
    """Antes, essas falas não tinham "brilho" e escapavam para a IA,
    que respondia sobre o clima."""
    assert parser.parse(fala) is not None
    assert esperado in parser.commands.calls


def test_weather_talk_is_not_brightness(parser):
    """"claro" também significa tempo bom: não pode virar brilho."""
    for fala in ("o dia está claro hoje", "o céu está claro", "vai fazer sol"):
        parser.commands.calls = []
        parser.parse(fala)
        assert "brightness_up" not in parser.commands.calls
        assert "brightness_down" not in parser.commands.calls


def test_brightness_with_word_still_works(parser):
    assert parser.parse("aumenta o brilho") is not None
    assert "brightness_up" in parser.commands.calls


# ------------------------------------------------------------------
# Bug 3/4/5 — ferramentas estruturadas não podem ser montadas por regex
# ------------------------------------------------------------------

def test_structured_tools_are_not_resolved_by_regex():
    """O reconhecedor por texto não deve disparar as ferramentas de estrutura.

    Elas exigem listas (slides, linhas, formas) que só a IA consegue montar.
    Quando o caminho por regex disparava, injetava params errados e a
    ferramenta falhava com 'unexpected keyword argument' / 'missing arguments'.
    """
    from tools.api_tools import build_tool_registry

    manager = build_tool_registry()

    for fala in (
        "cria uma apresentacao sobre inteligencia artificial com 4 slides",
        "cria uma planilha no excel com lista de compras",
        "cria um mapa mental sobre o sistema solar",
        "desenha um cenario com sol e grama",
        "corta esse video",
    ):
        resolved = manager.resolve_for_text(fala)
        assert resolved is None, (
            f"{fala!r} foi resolvido por regex para {resolved.name!r}; "
            "deveria ir para a IA, que monta a estrutura"
        )


def test_structured_tools_are_registered_for_the_model():
    """As ferramentas existem e estão disponíveis para a IA chamar."""
    from tools.api_tools import build_tool_registry

    names = {spec.name for spec in build_tool_registry().list_tools()}
    assert {
        "create_presentation_pptx",
        "create_document_docx",
        "create_spreadsheet_xlsx",
        "create_mindmap_image",
        "draw_image",
        "edit_video",
    } <= names


def test_structured_tools_are_marked_semantic_only():
    from tools.api_tools import build_tool_registry

    specs = {spec.name: spec for spec in build_tool_registry().list_tools()}
    for name in (
        "create_presentation_pptx",
        "create_document_docx",
        "create_spreadsheet_xlsx",
        "create_mindmap_image",
        "draw_image",
        "edit_video",
    ):
        assert specs[name].semantic_only is True, f"{name} deveria ser semantic_only"


def test_build_tool_parameters_does_not_invent_structured_params():
    """O extrator por regex não pode inventar content/slides/path."""
    from core.brain.brain import VegaBrain
    from tools.api_tools import build_tool_registry

    brain = VegaBrain.__new__(VegaBrain)
    manager = build_tool_registry()

    for fala, tool_name in (
        ("cria uma apresentacao sobre IA com 4 slides", "create_presentation_pptx"),
        ("cria uma planilha no excel", "create_spreadsheet_xlsx"),
        ("desenha um cenario com sol", "draw_image"),
    ):
        tool = manager.get_tool(tool_name)
        params = brain._build_tool_parameters(fala, tool)
        # O marcador "request" sinaliza "deixe a IA montar a estrutura".
        assert params == {"request": fala}


# ------------------------------------------------------------------
# Bug extra — arquivo gerado deve ser ABERTO, não só citado
# ------------------------------------------------------------------

def test_created_file_is_announced_and_opened(tmp_path, monkeypatch):
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    target = tmp_path / "mapa.png"
    target.write_bytes(b"png")

    opened = {}
    monkeypatch.setattr(
        "os.startfile", lambda path: opened.setdefault("path", path), raising=False
    )

    message = brain._announce_created_file(
        "create_mindmap_image", {"path": str(target), "status": "created"}
    )

    assert opened.get("path") == str(target)
    assert "mapa mental" in message
    assert "mapa.png" in message


def test_announce_handles_missing_file(tmp_path):
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    message = brain._announce_created_file(
        "create_mindmap_image", {"path": str(tmp_path / "sumiu.png")}
    )
    assert "não consegui localizar" in message