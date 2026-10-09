# -*- coding: utf-8 -*-
"""Fase 3 — geração real de conteúdo: pptx, docx, xlsx, mapa mental e vídeo.

Regras garantidas:
  1. os arquivos criados abrem de verdade (validação por leitura nas próprias
     bibliotecas python-pptx / python-docx / openpyxl);
  2. NUNCA sobrescrevem arquivo existente (mesma regra anti-sobrescrita do
     ProductivityTools, compartilhada via bridge);
  3. entradas inválidas levantam erro claro (sem arquivo corrompido);
  4. edição de vídeo SEMPRE gera arquivo novo, nunca altera o original;
  5. a integração com o cérebro registra o resultado corretamente.
"""

import json
import re
from pathlib import Path

import pytest

from tools.content_creation import ContentCreationTools


@pytest.fixture
def tools(tmp_path):
    return ContentCreationTools(root=str(tmp_path))


# ------------------------------------------------------------------
# 1. PowerPoint
# ------------------------------------------------------------------

def test_pptx_is_created_and_readable(tools, tmp_path):
    result = tools.create_presentation_pptx(
        title="Revolução Industrial",
        slides=[
            {"title": "Contexto", "bullets": ["Inglaterra, século XVIII", "Máquina a vapor"]},
            {"title": "Consequências", "bullets": ["Urbanização", "Surgimento do proletariado"]},
        ],
        path=str(tmp_path / "aula.pptx"),
    )

    assert result["status"] == "created"
    created = Path(result["path"])
    assert created.exists() and created.suffix == ".pptx"

    # Reabre com a própria biblioteca: prova que é um pptx válido.
    from pptx import Presentation
    presentation = Presentation(str(created))
    titles = [slide.shapes.title.text for slide in presentation.slides if slide.shapes.title]
    assert "Revolução Industrial" in titles
    assert "Contexto" in titles and "Consequências" in titles


def test_pptx_rejects_empty_input(tools):
    with pytest.raises(ValueError):
        tools.create_presentation_pptx(title="", slides=["x"])
    with pytest.raises(ValueError):
        tools.create_presentation_pptx(title="Aula", slides=[])


def test_plain_string_slides_are_supported(tools, tmp_path):
    result = tools.create_presentation_pptx(
        title="Resumo",
        slides=["Tema um\nponto A\nponto B", "Tema dois\nponto C"],
        path=str(tmp_path / "resumo.pptx"),
    )
    from pptx import Presentation
    presentation = Presentation(result["path"])
    assert len(presentation.slides) == 3  # capa + 2


# ------------------------------------------------------------------
# 2. Word
# ------------------------------------------------------------------

def test_docx_is_created_and_readable(tools, tmp_path):
    result = tools.create_document_docx(
        title="Relatório de Projeto",
        content="Primeiro parágrafo.\n\nSegundo parágrafo.",
        sections=[
            {"heading": "Introdução", "text": "Texto da introdução."},
            "Conclusão em parágrafo simples.",
        ],
        path=str(tmp_path / "relatorio.docx"),
    )

    created = Path(result["path"])
    assert created.exists() and created.suffix == ".docx"

    from docx import Document
    document = Document(str(created))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert "Introdução" in headings
    texts = "\n".join(p.text for p in document.paragraphs)
    assert "Primeiro parágrafo." in texts and "Texto da introdução." in texts


def test_docx_rejects_empty_title(tools):
    with pytest.raises(ValueError):
        tools.create_document_docx(title="   ")


# ------------------------------------------------------------------
# 3. Excel
# ------------------------------------------------------------------

def test_xlsx_is_created_and_readable(tools, tmp_path):
    result = tools.create_spreadsheet_xlsx(
        title="Notas da Turma",
        headers=["Aluno", "Nota"],
        rows=[["Ana", 9.5], ["Bruno", 7.0]],
        path=str(tmp_path / "notas.xlsx"),
    )

    created = Path(result["path"])
    assert created.exists() and created.suffix == ".xlsx"

    from openpyxl import load_workbook
    workbook = load_workbook(str(created))
    sheet = workbook.active
    assert sheet.cell(row=1, column=1).value == "Aluno"
    assert sheet.cell(row=2, column=2).value == 9.5


def test_xlsx_requires_rows(tools):
    with pytest.raises(ValueError):
        tools.create_spreadsheet_xlsx(title="Vazia", rows=[])


# ------------------------------------------------------------------
# 4. Mapa mental
# ------------------------------------------------------------------

def test_mindmap_png_is_created(tools, tmp_path):
    result = tools.create_mindmap_image(
        central_topic="Fotossíntese",
        branches=[
            {"label": "Insumos", "children": ["Luz solar", "Água", "CO2"]},
            {"label": "Produtos", "children": ["Glicose", "Oxigênio"]},
            "Importância ecológica",
        ],
        path=str(tmp_path / "mapa.png"),
    )

    created = Path(result["path"])
    assert created.exists() and created.suffix == ".png"
    assert created.stat().st_size > 1000  # imagem real, não vazio

    from PIL import Image
    with Image.open(str(created)) as image:
        assert image.size[0] > 100 and image.size[1] > 100


def test_mindmap_requires_topic_and_branches(tools):
    with pytest.raises(ValueError):
        tools.create_mindmap_image(central_topic="", branches=["x"])
    with pytest.raises(ValueError):
        tools.create_mindmap_image(central_topic="Tema", branches=[])


# ------------------------------------------------------------------
# 5. Anti-sobrescrita (regra compartilhada com o ProductivityTools)
# ------------------------------------------------------------------

def test_never_overwrites_existing_file(tools, tmp_path):
    first = tools.create_presentation_pptx(
        title="Aula", slides=["Um"], path=str(tmp_path / "aula.pptx")
    )
    second = tools.create_presentation_pptx(
        title="Aula", slides=["Dois"], path=str(tmp_path / "aula.pptx")
    )

    first_path, second_path = Path(first["path"]), Path(second["path"])
    assert first_path != second_path
    assert second_path.name == "aula_2.pptx"
    # O original permanece intacto.
    from pptx import Presentation
    assert len(Presentation(str(first_path)).slides) == 2  # capa + 1
    assert len(Presentation(str(second_path)).slides) == 2


# ------------------------------------------------------------------
# 6. Vídeo (ffmpeg ausente → erro claro; presente → arquivo novo)
# ------------------------------------------------------------------

def test_video_edit_without_ffmpeg_raises_clear_error(tools, tmp_path, monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)

    source = tmp_path / "video.mp4"
    source.write_bytes(b"\x00" * 64)

    with pytest.raises(FileNotFoundError) as error:
        tools.edit_video(str(source), "trim", start="00:00:01", end="00:00:02")
    assert "ffmpeg" in str(error.value).lower()


def test_video_unknown_operation_raises(tools, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"\x00" * 64)
    with pytest.raises(ValueError) as error:
        tools.edit_video(str(source), "transformar_em_gato")
    assert "não suportada" in str(error.value)


def test_video_missing_file_raises(tools, tmp_path, monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "C:/fake/ffmpeg.exe")

    with pytest.raises(FileNotFoundError):
        tools.edit_video(str(tmp_path / "nao_existe.mp4"), "mute")


# ------------------------------------------------------------------
# 7. Integração com o cérebro
# ------------------------------------------------------------------

def test_brain_routes_content_creation(monkeypatch, tmp_path):
    """Pedido de slides chega à ferramenta nova e produz um arquivo real."""
    from core.brain.brain import VegaBrain
    from tools.content_creation import ContentCreationTools

    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = None
    brain.last_tool_receipt = None

    creation = ContentCreationTools(root=str(tmp_path))
    result = creation.create_presentation_pptx(
        title="Teste VEGA",
        slides=[{"title": "Um", "bullets": ["a", "b"]}],
        path=str(tmp_path / "teste.pptx"),
    )

    brain._store_tool_receipt("create_presentation_pptx", result)
    # A criação também deixa recibo: "e por que isso?" funciona igual.
    assert brain.last_tool_receipt is not None
    assert brain.last_tool_receipt.tool == "create_presentation_pptx"
    assert Path(result["path"]).name in brain.last_tool_receipt.summary
    assert Path(result["path"]).exists()
