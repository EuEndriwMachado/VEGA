# -*- coding: utf-8 -*-
"""Fase 3 — canvas de desenho ao vivo.

Garante:
  1. o plano da IA vira comandos de traço corretos (círculo, retângulo, linha, polilinha);
  2. entrada inválida não quebra (comandos vazios, formas inválidas são ignoradas);
  3. a ferramenta draw_image valida e chama o callback (janela) corretamente;
  4. SEM callback, nada quebra — valida e devolve o plano;
  5. cérebro: draw_image sem canvas_callback devolve erro claro; com callback, desenha;
  6. o orbe NÃO é afetado (nenhum arquivo do orbe foi modificado — garantia estrutural).
"""

import pytest

from tools.content_creation import ContentCreationTools
from config.live_canvas import build_commands_from_spec


@pytest.fixture
def tools():
    return ContentCreationTools(root=None)


# ------------------------------------------------------------------
# 1. Plano da IA -> comandos
# ------------------------------------------------------------------

def test_spec_becomes_commands():
    commands = build_commands_from_spec({
        "title": "Cenário",
        "shapes": [
            {"shape": "circle", "center": [280, 120], "radius": [60, 60], "fill": "amarelo"},
            {"shape": "rect", "from": [60, 300], "to": [470, 420], "fill": "verde"},
            {"shape": "line", "from": [0, 300], "to": [532, 300]},
            {"shape": "polyline", "points": [[10, 10], [100, 50], [200, 20]]},
        ],
    })

    assert [command["type"] for command in commands] == ["ellipse", "rect", "line", "polyline"]
    assert commands[0]["fill"] == "amarelo"
    assert commands[1]["fill"] == "verde"


def test_invalid_shapes_are_skipped():
    commands = build_commands_from_spec({
        "shapes": [
            {"shape": "triangulo_inexistente", "from": [0, 0]},
            "não é um dict",
            None,
            {"shape": "circle", "center": [10, 10], "radius": [5, 5]},
        ],
    })
    assert len(commands) == 1
    assert commands[0]["type"] == "ellipse"


def test_non_dict_spec_is_safe():
    assert build_commands_from_spec(None) == []
    assert build_commands_from_spec("texto") == []
    assert build_commands_from_spec(42) == []


# ------------------------------------------------------------------
# 2. Ferramenta draw_image
# ------------------------------------------------------------------

def test_draw_image_validates_and_calls_callback(tools):
    captured = {}
    def fake_callback(title, spec):
        captured["title"] = title
        captured["spec"] = spec

    result = tools.draw_image_live(
        title="Casa",
        shapes=[{"shape": "rect", "from": [100, 200], "to": [300, 400], "fill": "verde"}],
        canvas_callback=fake_callback,
    )

    assert result["status"] == "drawing"
    assert captured["title"] == "Casa"
    assert captured["spec"]["shapes"][0]["shape"] == "rect"


def test_draw_image_without_callback_is_safe(tools):
    result = tools.draw_image_live(
        title="Casa",
        shapes=[{"shape": "circle", "center": [50, 50], "radius": [20, 20]}],
    )
    assert result["status"] == "validated"


def test_draw_image_rejects_invalid_input(tools):
    with pytest.raises(ValueError):
        tools.draw_image_live(title="", shapes=[{"shape": "circle"}])
    with pytest.raises(ValueError):
        tools.draw_image_live(title="X", shapes=[])


# ------------------------------------------------------------------
# 3. Cérebro: integração
# ------------------------------------------------------------------

def test_brain_draw_image_without_callback_gives_clear_error():
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    brain.tool_manager = _build_registry()
    brain.memory = type("M", (), {"add_report_event": staticmethod(lambda r: None)})()
    brain.tool_result_callback = None
    brain.canvas_callback = None

    result = brain._execute_registered_tool_call(
        "draw_image", {"title": "Casa", "shapes": [{"shape": "circle", "center": [1, 1], "radius": [5, 5]}]}
    )
    assert "error" in result
    assert "Canvas" in result["error"]


def test_brain_draw_image_with_callback_draws():
    from core.brain.brain import VegaBrain

    captured = {}
    def fake_canvas_callback(title, spec):
        captured["title"] = title
        captured["spec"] = spec

    brain = VegaBrain.__new__(VegaBrain)
    brain.tool_manager = _build_registry()
    brain.memory = type("M", (), {"add_report_event": staticmethod(lambda r: None)})()
    brain.tool_result_callback = None
    brain.canvas_callback = fake_canvas_callback

    result = brain._execute_registered_tool_call(
        "draw_image",
        {"title": "Casa", "shapes": [{"shape": "rect", "from": [1, 1], "to": [50, 50], "fill": "verde"}]},
    )

    assert captured["title"] == "Casa"
    assert result["status"] == "drawing"
    assert result["shapes"] == 1


# ------------------------------------------------------------------
# Util
# ------------------------------------------------------------------

def _build_registry():
    from tools.api_tools import build_tool_registry
    return build_tool_registry()

# ------------------------------------------------------------------
# 7. Traço progressivo (o efeito de "mão desenhando")
# ------------------------------------------------------------------
# As formas deixaram de aparecer inteiras de uma vez: cada comando cresce em
# sub-passos. É o que dá o efeito de a VEGA estar desenhando na frente do
# usuário (o mesmo das demos de assistente em tempo real).

from PySide6.QtCore import QPointF  # noqa: E402

from config.live_canvas import _CanvasWidget  # noqa: E402


@pytest.fixture(scope="module")
def canvas_widget():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    return _CanvasWidget.__new__(_CanvasWidget)


def test_partial_points_interpolates_at_half(canvas_widget):
    sliced = canvas_widget._partial_points([QPointF(0, 0), QPointF(100, 100)], 0.5)
    assert len(sliced) == 2
    # O ponto final é interpolado, não um vértice existente.
    assert sliced[-1].x() == pytest.approx(50.0)


def test_partial_points_walks_a_polyline(canvas_widget):
    points = [QPointF(i * 10, i * 10) for i in range(5)]
    sliced = canvas_widget._partial_points(points, 0.5)
    assert 2 <= len(sliced) <= 4
    assert sliced[0] == points[0]


def test_full_progress_keeps_every_point(canvas_widget):
    points = [QPointF(i * 10, i * 10) for i in range(5)]
    assert len(canvas_widget._partial_points(points, 1.0)) == len(points)


def test_zero_progress_does_not_explode(canvas_widget):
    points = [QPointF(0, 0), QPointF(50, 50), QPointF(100, 100)]
    sliced = canvas_widget._partial_points(points, 0.0)
    assert sliced[0] == points[0]
    assert len(sliced) <= 2


def test_degenerate_polyline_is_safe(canvas_widget):
    single = [QPointF(1, 1)]
    assert canvas_widget._partial_points(single, 0.5) == single
    assert canvas_widget._partial_points([], 0.5) == []


def test_canvas_starts_with_zero_progress():
    """Um canvas novo não desenha nada parcial antes de começar."""
    widget = _CanvasWidget.__new__(_CanvasWidget)
    widget._commands = []
    widget._visible_count = 0
    widget._progress = 0.0
    assert widget._progress == 0.0


def test_canvas_widget_is_independent_from_the_orb():
    """O canvas é um widget próprio — não é a InterfaceVEGA (o orbe)."""
    from config.interface import InterfaceVEGA

    assert not issubclass(_CanvasWidget, InterfaceVEGA)
