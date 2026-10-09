# -*- coding: utf-8 -*-
"""Geração real de conteúdo: PowerPoint, Word, Excel, mapa mental e vídeo.

O que existia e permanece (intocado)
------------------------------------
- ``ProductivityTools.create_document``            → Markdown
- ``ProductivityTools.create_presentation_outline`` → roteiro em Markdown
- ``ProductivityTools.create_project``             → projeto com arquivos no VS Code
- ``ProductivityTools._creation_path``             → proteção anti-sobrescrita
  (``relatorio.txt`` existente vira ``relatorio_2.txt`` em vez de sobrescrever)

O que é NOVO aqui
-----------------
Formatos de verdade, que abrem nos aplicativos nativos:

- ``create_presentation_pptx``  → .pptx (PowerPoint)
- ``create_document_docx``      → .docx (Word)
- ``create_spreadsheet_xlsx``   → .xlsx (Excel)
- ``create_mindmap_image``      → .png (matplotlib)
- ``VideoEditingTools``         → cortes, extração de áudio, GIF (ffmpeg)

Todos reutilizam a MESMA proteção anti-sobrescrita já testada: nenhuma função
deste módulo sobrescreve arquivo existente — gera nome irmão ``_2``, ``_3``...
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _sanitize_filename(value: str, fallback: str = "VEGA") -> str:
    """Remove caracteres inválidos para nome de arquivo no Windows."""
    text = re.sub(r'[\\/:*?"<>|]+', " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:80] or fallback


class ContentCreationTools:
    """Criação de arquivos em formatos nativos (Office, imagem)."""

    def __init__(self, root: str | None = None):
        # MESMA raiz do ProductivityTools: arquivos nascem onde o usuário espera.
        self.root = Path(root or Path.home()).expanduser().resolve()
        self._anti_overwrite = ProductivityBridge()

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _path(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve()

    def _unique_path(self, value: str) -> Path:
        """Caminho livre para criação: nunca sobrescreve (mesma regra já testada)."""
        return self._anti_overwrite.unique(self._path(value))

    def _last_result(self, path: Path, kind: str) -> Dict[str, Any]:
        return {
            "path": str(path),
            "kind": kind,
            "name": path.name,
            "status": "created",
        }

    # ------------------------------------------------------------------
    # PowerPoint (.pptx)
    # ------------------------------------------------------------------

    def create_presentation_pptx(
        self,
        title: str,
        slides: Sequence[Any],
        path: str = "",
        theme_color: str = "1F4E79",
    ) -> Dict[str, Any]:
        """Cria um .pptx real que abre no PowerPoint.

        ``slides`` aceita:
          - str  → um slide de texto corrido;
          - dict → {"title": ..., "bullets": [...], "notes": ...}
        """
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor

        if not title or not str(title).strip():
            raise ValueError("Informe o título da apresentação.")
        if not slides:
            raise ValueError("A apresentação precisa de ao menos um slide.")

        target = self._unique_path(
            path or f"{_sanitize_filename(title)}.pptx"
        )

        presentation = Presentation()
        accent = RGBColor.from_string(re.sub(r"[^0-9A-Fa-f]", "", theme_color)[:6] or "1F4E79")

        # Slide de capa
        cover = presentation.slides.add_slide(presentation.slide_layouts[0])
        cover.shapes.title.text = str(title)
        if len(cover.placeholders) > 1:
            cover.placeholders[1].text = "criado pela VEGA"

        for slide_data in slides:
            if isinstance(slide_data, dict):
                slide_title = str(slide_data.get("title") or "").strip() or "Sem título"
                bullets = slide_data.get("bullets") or []
                notes = str(slide_data.get("notes") or "")
            else:
                # Texto corrido: primeira linha vira título, resto vira bullets.
                lines = [line.strip() for line in str(slide_data).splitlines() if line.strip()]
                slide_title = lines[0] if lines else "Sem título"
                bullets = lines[1:]
                notes = ""

            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = slide_title
            body = slide.placeholders[1].text_frame
            body.clear()

            if bullets:
                for index, bullet in enumerate(bullets[:12]):
                    paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
                    paragraph.text = str(bullet)[:300]
                    paragraph.font.size = Pt(20)
            else:
                body.paragraphs[0].text = ""

            if notes:
                slide.notes_slide.notes_text_frame.text = notes[:1000]

        # Rodapé discreto com a cor do tema
        for slide in presentation.slides:
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text == "criado pela VEGA":
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            run.font.color.rgb = accent

        presentation.save(str(target))
        return self._last_result(target, "pptx")

    # ------------------------------------------------------------------
    # Word (.docx)
    # ------------------------------------------------------------------

    def create_document_docx(
        self,
        title: str,
        content: str = "",
        sections: Iterable[Any] = (),
        path: str = "",
    ) -> Dict[str, Any]:
        """Cria um .docx real que abre no Word.

        ``sections`` aceita str (parágrafo) ou dict {"heading": ..., "text": ...}.
        """
        from docx import Document
        from docx.shared import Pt, RGBColor

        if not title or not str(title).strip():
            raise ValueError("Informe o título do documento.")

        target = self._unique_path(path or f"{_sanitize_filename(title)}.docx")

        document = Document()
        document.add_heading(str(title), level=0)

        if content:
            for paragraph_text in re.split(r"\n\s*\n", str(content)):
                text = paragraph_text.strip()
                if text:
                    document.add_paragraph(text)

        for section in sections:
            if isinstance(section, dict):
                heading = str(section.get("heading") or "").strip()
                text = str(section.get("text") or "")
            else:
                text = str(section).strip()
                heading = ""
            if heading:
                document.add_heading(heading, level=1)
            for paragraph_text in re.split(r"\n\s*\n", text):
                if paragraph_text.strip():
                    document.add_paragraph(paragraph_text.strip())

        document.save(str(target))
        return self._last_result(target, "docx")

    # ------------------------------------------------------------------
    # Excel (.xlsx)
    # ------------------------------------------------------------------

    def create_spreadsheet_xlsx(
        self,
        title: str,
        rows: Sequence[Sequence[Any]],
        headers: Optional[Sequence[str]] = None,
        path: str = "",
    ) -> Dict[str, Any]:
        """Cria um .xlsx real que abre no Excel.

        ``rows`` são listas de valores; ``headers`` é a primeira linha, se houver.
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        if not rows:
            raise ValueError("A planilha precisa de ao menos uma linha de dados.")

        target = self._unique_path(path or f"{_sanitize_filename(title)}.xlsx")

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = _sanitize_filename(title, "Planilha")[:28] or "Planilha"

        cursor = 1
        if headers:
            sheet.append([str(header) for header in headers])
            for column in range(1, len(headers) + 1):
                cell = sheet.cell(row=1, column=column)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", start_color="1F4E79")
            cursor = 2

        for row in rows[:5000]:
            sheet.append([value for value in row])

        # Largura de coluna razoável (até 40 caracteres)
        for column in range(1, sheet.max_column + 1):
            letter = get_column_letter(column)
            longest = max(
                (len(str(sheet.cell(row=r, column=column) or "")) for r in range(1, min(sheet.max_row, 200) + 1)),
                default=8,
            )
            sheet.column_dimensions[letter].width = min(40, max(8, longest + 2))

        workbook.save(str(target))
        return self._last_result(target, "xlsx")

    # ------------------------------------------------------------------
    # Mapa mental (.png)
    # ------------------------------------------------------------------

    def create_mindmap_image(
        self,
        central_topic: str,
        branches: Sequence[Any],
        path: str = "",
    ) -> Dict[str, Any]:
        """Gera um mapa mental em .png (matplotlib).

        ``branches`` aceita:
          - str          → ramo simples com texto;
          - dict         → {"label": ..., "children": ["...", ...]}
        """
        if not central_topic or not str(central_topic).strip():
            raise ValueError("Informe o tema central do mapa mental.")
        if not branches:
            raise ValueError("O mapa mental precisa de ao menos um ramo.")

        target = self._unique_path(path or f"mapa_mental_{_sanitize_filename(central_topic)[:40]}.png")

        import matplotlib
        matplotlib.use("Agg")  # sem janela: só gera o arquivo
        import matplotlib.pyplot as plt
        import numpy as np

        def _leaf_text(item: Any) -> tuple:
            if isinstance(item, dict):
                label = str(item.get("label") or "Ramo").strip() or "Ramo"
                children = [str(c) for c in (item.get("children") or []) if str(c).strip()]
                return label, children
            return str(item).strip() or "Ramo", []

        parsed = [_leaf_text(item) for item in branches][:8]
        if not parsed:
            raise ValueError("O mapa mental precisa de ao menos um ramo válido.")

        fig, ax = plt.subplots(figsize=(14, 8))
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        ax.axis("off")

        # Nó central
        ax.add_patch(plt.Circle((5, 5), 0.9, color="#1F4E79", zorder=3))
        ax.text(5, 5, str(central_topic)[:28], ha="center", va="center",
                color="white", fontsize=12, fontweight="bold", zorder=4)

        count = len(parsed)
        angles = np.linspace(0, 2 * np.pi, count, endpoint=False) + np.pi / 2

        palette = ["#2E75B6", "#C55A11", "#548235", "#7030A0", "#BF8F00",
                   "#2E75B6", "#C55A11", "#548235"]

        for index, (angle, (label, children)) in enumerate(zip(angles, parsed)):
            bx, by = 5 + 3.2 * np.cos(angle), 5 + 3.2 * np.sin(angle)
            color = palette[index % len(palette)]

            ax.plot([5, bx], [5, by], color=color, linewidth=2, zorder=1)
            ax.add_patch(plt.Circle((bx, by), 0.55, color=color, zorder=3))
            ax.text(bx, by, label[:18], ha="center", va="center", color="white",
                    fontsize=9, fontweight="bold", zorder=4)

            # Filhos: pequenos textos ao lado do ramo
            for child_index, child in enumerate(children[:4]):
                offset = (child_index + 1) * 0.75
                cx = bx + 1.4 * np.cos(angle)
                cy = by + offset * 0.55 * (1 if np.sin(angle) >= 0 else -1)
                ax.plot([bx, cx], [by, cy], color=color, linewidth=1, alpha=0.6, zorder=1)
                ax.text(cx, cy, f"• {child[:24]}", ha="left", va="center",
                        fontsize=8, color="#333333", zorder=4)

        fig.suptitle(f"Mapa mental — {str(central_topic)[:50]}", fontsize=11, color="#555555")
        fig.tight_layout()
        fig.savefig(str(target), dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)

        return self._last_result(target, "mindmap")

    # ------------------------------------------------------------------
    # Mídia: vídeo e áudio
    # ------------------------------------------------------------------

    def _ffmpeg(self) -> str:
        found = shutil.which("ffmpeg")
        if not found:
            raise FileNotFoundError(
                "O ffmpeg não foi encontrado no sistema. Instale-o (ex.: winget install ffmpeg) para edição de vídeo."
            )
        return found

    # ------------------------------------------------------------------
    # Desenho ao vivo (canvas)
    # ------------------------------------------------------------------
    # A execução real acontece na interface (config/live_canvas.py). Esta
    # função é o "adaptador" chamado pelo registro de ferramentas: ela valida
    # o plano e devolve um resultado descritivo. A interface abre a janela e
    # desenha animado quando recebe o sinal.

    def draw_image_live(self, title: str, shapes, canvas_callback=None) -> dict:
        """Desenha ao vivo no canvas (janela própria, orbe intocado).

        canvas_callback: função(title, spec) chamada para abrir o painel.
        Sem callback (ex.: em testes), o plano é validado e devolvido.
        """
        if not title or not str(title).strip():
            raise ValueError("Informe o título do desenho.")
        if not isinstance(shapes, list) or not shapes:
            raise ValueError("O desenho precisa de ao menos uma forma.")

        spec = {"title": str(title).strip(), "shapes": shapes}
        command_count = len(shapes)

        if callable(canvas_callback):
            canvas_callback(str(title).strip(), spec)

        return {
            "status": "drawing" if callable(canvas_callback) else "validated",
            "title": str(title).strip(),
            "shapes": command_count,
            "spec": spec,
        }

    def edit_video(self, source: str, operation: str, **options: Any) -> Dict[str, Any]:
        """Edita um vídeo via ffmpeg: trim, extract_audio, gif, speed, mute.

        Sempre gera um ARQUIVO NOVO — nunca sobrescreve o original.
        """
        ffmpeg = self._ffmpeg()
        source_path = self._path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"Vídeo não encontrado: {source_path}")

        operation = str(operation or "").strip().lower()
        suffix_map = {
            "trim": ".mp4",
            "extract_audio": ".mp3",
            "gif": ".gif",
            "speed": ".mp4",
            "mute": ".mp4",
        }
        if operation not in suffix_map:
            raise ValueError(
                f"Operação '{operation}' não suportada. Use: {', '.join(sorted(suffix_map))}."
            )

        start = options.get("start")
        end = options.get("end")
        speed = options.get("speed")
        stem = f"{source_path.stem}_{operation}"
        if start is not None:
            stem += f"_{str(start).replace(':', '').replace('.', '')}"
        target = self._unique_path(str(source_path.with_name(stem + suffix_map[operation])))

        if operation == "trim":
            if start is None or end is None:
                raise ValueError("trim exige start e end (formato HH:MM:SS).")
            command = [
                ffmpeg, "-y", "-i", str(source_path),
                "-ss", str(start), "-to", str(end),
                "-c", "copy", str(target),
            ]
        elif operation == "extract_audio":
            command = [ffmpeg, "-y", "-i", str(source_path), "-vn", str(target)]
        elif operation == "gif":
            command = [
                ffmpeg, "-y", "-ss", str(start or 0), "-t", str(options.get("duration", 5)),
                "-i", str(source_path),
                "-vf", "fps=12,scale=480:-1:flags=lanczos", str(target),
            ]
        elif operation == "speed":
            try:
                factor = float(speed or 1.0)
            except (TypeError, ValueError):
                factor = 1.0
            if factor <= 0:
                factor = 1.0
            command = [
                ffmpeg, "-y", "-i", str(source_path),
                "-filter:v", f"setpts={1.0 / factor:.6f}*PTS",
                "-filter:a", f"atempo={min(factor, 2.0):.6f}",
                str(target),
            ]
        else:  # mute
            command = [ffmpeg, "-y", "-i", str(source_path), "-an", str(target)]

        process = subprocess.run(
            command, capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg falhou: {(process.stderr or '')[-400:]}")

        result = self._last_result(target, f"video_{operation}")
        result["source"] = str(source_path)
        result["operation"] = operation
        return result


class ProductivityBridge:
    """Ponte para a regra anti-sobrescrita já testada do ProductivityTools.

    Em vez de reimplementar a lógica de ``_creation_path`` (que geraria risco
    de divergência), delegamos à implementação original — assim as duas
    ferramentas compartilham exatamente a mesma regra.
    """

    def __init__(self):
        from tools.productivity import ProductivityTools

        self._tools = ProductivityTools()

    def unique(self, target: Path) -> Path:
        return self._tools._creation_path(str(target))
