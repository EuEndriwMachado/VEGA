# -*- coding: utf-8 -*-
"""Canvas de desenho ao vivo da VEGA — ela desenha na frente de você.

Um painel flutuante INDEPENDENTE, que a VEGA usa como tela: ela desenha
recebendo comandos passo a passo (traço a traço, com pequenas pausas), e você
pode pedir mudanças no meio ("deixa o sol maior"), porque o desenho é uma
LISTA DE COMANDOS reexecutável.

Garantia de não interferência
-----------------------------
Este módulo NÃO toca no orbe: é um widget próprio (QDialog frameless), com o
seu próprio paintEvent. O orbe vive no paintEvent da InterfaceVEGA e continua
exatamente como está.
"""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class LiveCanvasDialog(QDialog):
    """Painel de desenho da VEGA — widget independente (não toca no orbe)."""

    drawing_finished = Signal(str)

    COLORS = {
        "preto": "#000000", "black": "#000000",
        "branco": "#FFFFFF", "white": "#FFFFFF",
        "vermelho": "#E53935", "red": "#E53935",
        "verde": "#43A047", "green": "#43A047",
        "azul": "#1E88E5", "blue": "#1E88E5",
        "amarelo": "#FDD835", "yellow": "#FDD835",
        "laranja": "#FB8C00", "orange": "#FB8C00",
        "roxo": "#8E24AA", "purple": "#8E24AA",
        "rosa": "#EC407A", "pink": "#EC407A",
        "marrom": "#6D4C41", "brown": "#6D4C41",
        "cinza": "#9E9E9E", "gray": "#9E9E9E",
        "ciano": "#00ACC1", "cyan": "#00ACC1",
    }

    PAPER = "#FFFFFF"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VEGA — Desenho ao vivo")
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self._commands: List[Dict[str, Any]] = []
        self._index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._step_delay_ms = 260
        # Traço progressivo: cada comando é desenhado PONTO A PONTO, em vez de
        # aparecer inteiro de uma vez. É o que dá o efeito de "a mão desenhando"
        # (o mesmo das demos de assistente em tempo real). O canvas aceita um
        # `progress` por comando: as linhas/polilinhas são desenhadas até a
        # fração indicada; as formas fechadas aparecem inteiras no fim do passo.
        self._progress = 0.0
        self._progress_steps = 6

        self._build_ui()

    def _build_ui(self):
        self.setFixedSize(560, 560)
        self.setStyleSheet(
            "QDialog { background: #0B1116; }"
            "QLabel { color: #CFE9F5; font-size: 13px; }"
            "QPushButton { color: #07121C; background: #BFE8F7; border: none;"
            " border-radius: 14px; padding: 6px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #FFFFFF; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.title_label = QLabel("VEGA está desenhando…")
        self.title_label.setStyleSheet("color: #8FD3EA; font-size: 15px; font-weight: 600;")
        layout.addWidget(self.title_label)

        self.canvas = _CanvasWidget(self)
        self.canvas.setFixedSize(532, 440)
        layout.addWidget(self.canvas)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for text, handler in (
            ("Salvar", self._save),
            ("Limpar", self.clear),
            ("Fechar", self.close),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # API usada pela VEGA
    # ------------------------------------------------------------------

    def start_drawing(self, title: str = "Desenho"):
        self._commands = []
        self._index = 0
        self.title_label.setText(f"VEGA está desenhando: {title}")
        self.canvas.set_commands([])
        self.show()
        self.raise_()
        self.activateWindow()

    def set_commands(self, commands):
        """Define a lista completa e começa a desenhar animado."""
        self._commands = list(commands or [])
        self._index = 0
        self._progress = 0.0
        self.canvas.set_commands(self._commands)
        self.canvas.set_visible_count(0)
        self.canvas.set_progress(0.0)
        self._timer.start(self._step_delay_ms)

    def add_commands(self, extra):
        """Adiciona comandos — é o que permite editar o desenho no meio."""
        added = list(extra or [])
        if not added:
            return
        start_index = len(self._commands)
        self._commands.extend(added)
        self.canvas.set_commands(self._commands)
        if self._timer.isActive():
            return  # já está desenhando; entram na fila naturalmente
        self._index = start_index
        self.canvas.set_visible_count(start_index)
        self._timer.start(self._step_delay_ms)

    def is_drawing(self) -> bool:
        return self._timer.isActive()

    def clear(self):
        self._commands = []
        self._index = 0
        self.canvas.set_commands([])
        self._timer.stop()
        self.title_label.setText("VEGA está desenhando…")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar desenho", "vega_desenho.png", "PNG (*.png)"
        )
        if path:
            self.canvas.save_to(path)

    # ------------------------------------------------------------------
    # Motor de animação (comando a comando)
    # ------------------------------------------------------------------

    def _step(self):
        """Avança um sub-passo do traço.

        Cada comando é desenhado em `_progress_steps` sub-passos, para o traço
        "crescer" em vez de saltar inteiro. Ao terminar um comando, o progresso
        volta a zero para o próximo.
        """
        if self._index >= len(self._commands):
            self._timer.stop()
            self.canvas.set_progress(1.0)
            self.title_label.setText("Desenho concluído.")
            self.drawing_finished.emit("done")
            return

        self._progress += 1.0 / max(1, self._progress_steps)
        if self._progress >= 1.0:
            # Comando concluído: fixa-o e passa para o próximo.
            self._progress = 0.0
            self._index += 1
            self.canvas.set_visible_count(self._index)
            self.canvas.set_progress(0.0)
        else:
            self.canvas.set_progress(self._progress)
        self.canvas.update()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class _CanvasWidget(QWidget):
    """A superfície de desenho em si (papel branco)."""

    def __init__(self, dialog: LiveCanvasDialog):
        super().__init__(dialog)
        self._commands: List[Dict[str, Any]] = []
        self._visible_count = 0
        self._progress = 0.0

    def set_commands(self, commands):
        self._commands = list(commands or [])
        self.update()

    def set_visible_count(self, count: int):
        self._visible_count = max(0, int(count))
        self.update()

    def set_progress(self, value: float):
        """Fração do comando ATUAL já desenhada (0.0 a 1.0)."""
        self._progress = min(1.0, max(0.0, float(value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.PAPER))
        for command in self._commands[: self._visible_count]:
            try:
                self._paint_command(painter, command)
            except Exception:
                continue  # comando inválido não quebra o desenho
        # Comando em andamento: desenhado com o traço parcial.
        if self._visible_count < len(self._commands) and self._progress > 0.0:
            try:
                self._paint_command(
                    painter, self._commands[self._visible_count], self._progress
                )
            except Exception:
                pass

    def _paint_command(self, painter, command, progress: float = 1.0):
        """Desenha um comando; `progress` < 1 desenha o traço PARCIAL.

        Linhas e polilinhas crescem ponto a ponto (é o efeito de mão
        desenhando). Formas fechadas (retângulo/elipse) aparecem quando o
        traço chega ao fim, porque não existe "meio retângulo" natural.
        """
        kind = str(command.get("type", "")).lower()
        pen = QPen(self._color(command.get("color")), float(command.get("width", 4)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QBrush())  # sem preenchimento por padrão
        progress = min(1.0, max(0.0, float(progress)))

        if kind == "line":
            p1, p2 = self._point(command.get("from")), self._point(command.get("to"))
            if p1 and p2:
                if progress >= 1.0:
                    painter.drawLine(p1, p2)
                else:
                    # Traço parcial: a linha cresce até a fração indicada.
                    tip = self._point([
                        p1.x() + (p2.x() - p1.x()) * progress,
                        p1.y() + (p2.y() - p1.y()) * progress,
                    ])
                    if tip:
                        painter.drawLine(p1, tip)
        elif kind == "rect":
            p1, p2 = self._point(command.get("from")), self._point(command.get("to"))
            if p1 and p2 and progress >= 1.0:
                painter.drawRect(self._norm_rect(p1, p2))
        elif kind == "ellipse":
            center = self._point(command.get("center"))
            radii = command.get("radius") or [60, 60]
            if center and progress >= 1.0:
                painter.drawEllipse(center, float(radii[0]), float(radii[1]))
        elif kind == "polyline":
            points = [p for p in (self._point(item) for item in command.get("points", [])) if p]
            if len(points) >= 2:
                if progress < 1.0:
                    points = self._partial_points(points, progress)
                    if len(points) < 2:
                        return
                path = QPainterPath(points[0])
                for point in points[1:]:
                    path.lineTo(point)
                painter.drawPath(path)

        # Preenchimento depois do traço, para não apagar o contorno.
        if command.get("fill") and progress >= 1.0:
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            fill_color = self._color(command.get("fill"))
            painter.setBrush(QBrush(fill_color))
            if kind == "ellipse":
                center = self._point(command.get("center"))
                radii = command.get("radius") or [60, 60]
                if center:
                    painter.drawEllipse(center, float(radii[0]), float(radii[1]))
            elif kind == "rect":
                p1, p2 = self._point(command.get("from")), self._point(command.get("to"))
                if p1 and p2:
                    painter.fillRect(self._norm_rect(p1, p2), fill_color)

    @staticmethod
    def _partial_points(points, progress: float):
        """Fatia uma polilinha até a fração de percurso indicada.

        O ponto final é interpolado, para o traço crescer suavemente em vez de
        pular de vértice em vértice.
        """
        total = len(points) - 1
        if total <= 0:
            return points
        traveled = progress * total
        complete = int(traveled)
        remainder = traveled - complete
        sliced = points[: complete + 1]
        if remainder > 0 and complete < total:
            start, end = points[complete], points[complete + 1]
            sliced.append(
                start + (end - start) * remainder
            )
        return sliced

    @staticmethod
    def _norm_rect(p1, p2):
        return QRectF(
            min(p1.x(), p2.x()), min(p1.y(), p2.y()),
            abs(p2.x() - p1.x()), abs(p2.y() - p1.y()),
        )

    def _color(self, value) -> QColor:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in LiveCanvasDialog.COLORS:
                return QColor(LiveCanvas.COLORS[lowered])
        try:
            return QColor(value)
        except (TypeError, ValueError):
            return QColor("#333333")

    @staticmethod
    def _point(value):
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                from PySide6.QtCore import QPointF
                return QPointF(float(value[0]), float(value[1]))
            except (TypeError, ValueError):
                return None
        return None

    def save_to(self, path: str):
        pixmap = QPixmap(self.size())
        pixmap.fill(QColor(self.PAPER))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for command in self._commands[: self._visible_count]:
            try:
                self._paint_command(painter, command)
            except Exception:
                continue
        painter.end()
        pixmap.save(path, "PNG")


# ======================================================================
# Tradução: plano da IA -> comandos do canvas
# ======================================================================

def build_commands_from_spec(spec) -> List[Dict[str, Any]]:
    """Converte o plano declarativo da IA em comandos de traço executáveis.

    Formato esperado:
        {"title": "Cenário", "shapes": [
            {"shape": "circle", "center": [140,120], "radius": [60,60], "fill": "amarelo"},
            {"shape": "rect", "from": [60,300], "to": [470,420], "fill": "verde"},
        ]}
    """
    commands: List[Dict[str, Any]] = []
    if not isinstance(spec, dict):
        return commands

    for shape in spec.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        kind = str(shape.get("shape", "")).lower()
        color = shape.get("color") or "#333333"
        width = shape.get("width", 4)
        fill = shape.get("fill")

        if kind == "circle":
            commands.append({
                "type": "ellipse",
                "center": shape.get("center", [280, 200]),
                "radius": shape.get("radius", [80, 80]),
                "color": color, "width": width, "fill": fill,
            })
        elif kind == "rect":
            commands.append({
                "type": "rect",
                "from": shape.get("from", [100, 100]),
                "to": shape.get("to", [400, 300]),
                "color": color, "width": width, "fill": fill,
            })
        elif kind == "line":
            commands.append({
                "type": "line",
                "from": shape.get("from", [100, 100]),
                "to": shape.get("to", [400, 300]),
                "color": color, "width": width,
            })
        elif kind == "polyline":
            points = shape.get("points") or []
            if isinstance(points, list) and len(points) >= 2:
                commands.append({
                    "type": "polyline",
                    "points": points,
                    "color": color, "width": width,
                })

    return commands
