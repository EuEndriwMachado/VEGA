# -*- coding: utf-8 -*-
"""Seletor de câmera da VEGA — menu nativo do Qt, aberto no botão de câmera.

Histórico (para não repetir os erros)
-------------------------------------
Tentei três abordagens antes desta, e todas deram problema:

1. `Qt.Popup` — capturava mouse/teclado e travava a VEGA;
2. `Qt.Tool` flutuante — roubava foco e sumia no meio do clique;
3. widget filho com `QFrame` — em janela frameless + always-on-top o layout
   media geometria inválida e o painel virava uma TIRA de poucos pixels
   (o usuário via só "uma barrinha").

O problema de fundo: **posicionar e dimensionar um widget customizado dentro de
uma janela frameless é frágil** — a geometria depende de quando o layout é
medido em relação ao conteúdo existente.

Aqui o menu é um `QMenu` do próprio Qt: ele foi feito exatamente para isto,
calcula o próprio tamanho pelo conteúdo, posiciona-se sozinho na tela e fecha
com clique-fora ou Esc. Não há geometria para errar.

A câmera padrão é a WEBCAM DO PC, resolvida pelo NOME do dispositivo (o índice
não é confiável: o Windows decide a ordem conforme os dispositivos registram,
e a Iriun do celular pode ficar no 0).
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QActionGroup, QCursor
from PySide6.QtWidgets import QMenu

# Onde a escolha da câmera é lembrada, entre execuções da VEGA.
PREFERENCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "camera_preference.json",
)


def load_preferred_camera() -> Optional[int]:
    """Câmera escolhida pelo usuário, ou None (usa o padrão do PC).

    O índice é conferido contra o NOME gravado: se o Windows mudou a ordem dos
    dispositivos (basta plugar algo ou reiniciar), o número antigo apontaria
    para outra câmera — e a VEGA usaria a do celular sem o usuário pedir.
    """
    try:
        with open(PREFERENCE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None

    index = data.get("camera_index")
    if index is None:
        return None
    try:
        index = int(index)
    except (TypeError, ValueError):
        return None

    saved_name = str(data.get("camera_name") or "")
    if saved_name:
        try:
            from core.vision.camera import VegaCamera

            names = VegaCamera.camera_names()
            if names:
                if index < len(names) and names[index] == saved_name:
                    return index
                for position, name in enumerate(names):
                    if name == saved_name:
                        return position
                return None
        except Exception:
            pass
    return index


def save_preferred_camera(index, name: str = "") -> None:
    """Guarda a câmera escolhida (índice e nome), sem quebrar se o disco falhar."""
    try:
        os.makedirs(os.path.dirname(PREFERENCE_PATH), exist_ok=True)
        payload = {"camera_index": int(index)}
        if name:
            payload["camera_name"] = str(name)
        with open(PREFERENCE_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except (OSError, ValueError, TypeError):
        pass


class CameraMenu(QMenu):
    """Menu de escolha de câmera, aberto a partir do botão de câmera."""

    camera_selected = Signal(int)
    rescan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QMenu { background: #0B1116; border: 1px solid #1E3A47;"
            " border-radius: 10px; padding: 6px; }"
            "QMenu::item { color: #CFE9F5; padding: 8px 16px;"
            " border-radius: 7px; font-size: 12px; min-width: 240px; }"
            "QMenu::item:selected { background: #17303D; color: #FFFFFF; }"
            "QMenu::item:disabled { color: #4A6572; }"
            "QMenu::separator { height: 1px; background: #1E3A47;"
            " margin: 5px 8px; }"
        )
        self._actions: List = []
        self._current_index: Optional[int] = None

    # ------------------------------------------------------------------
    # Conteúdo
    # ------------------------------------------------------------------

    def populate(self, cameras: List[dict], current_index: Optional[int] = None) -> None:
        """Monta a lista de câmeras.

        `cameras` é a lista de `{"index", "label", "name"}` devolvida por
        `core.vision.camera.list_camera_devices`.
        """
        self.clear()
        self._actions = []
        self._current_index = current_index
        group = QActionGroup(self)
        group.setExclusive(True)

        if not cameras:
            placeholder = self.addAction("Procurando câmeras…")
            placeholder.setEnabled(False)
        else:
            for camera in cameras:
                index = int(camera.get("index", 0))
                label = str(camera.get("label") or f"Câmera {index}")
                action = self.addAction(label)
                action.setCheckable(True)
                action.setChecked(current_index == index)
                action.triggered.connect(
                    (lambda value: lambda _checked=False: self._choose(value))(index)
                )
                group.addAction(action)
                self._actions.append(action)

        self.addSeparator()
        rescan = self.addAction("↻  Procurar novamente")
        rescan.triggered.connect(self._request_rescan)

    def _choose(self, index: int) -> None:
        self.close()
        self.camera_selected.emit(int(index))

    def _request_rescan(self) -> None:
        self.close()
        self.rescan_requested.emit()

    # ------------------------------------------------------------------
    # Abertura
    # ------------------------------------------------------------------

    def open_at(self, anchor) -> None:
        """Abre o menu junto do botão.

        O Qt calcula o tamanho pelo conteúdo e ajusta a posição se o menu não
        couber na tela — que era justamente o que eu errava ao posicionar um
        widget na mão.
        """
        try:
            ponto = anchor.mapToGlobal(QPoint(0, 0))
            altura = self.sizeHint().height()
            # Acima do botão (fica perto do ícone); se não couber, abre abaixo.
            self.popup(QPoint(ponto.x(), ponto.y() - altura - 6))
        except Exception:
            self.popup(QCursor.pos())


def describe_camera(index: int, total: int) -> str:
    """Rótulo curto para uma câmera (compatibilidade com código antigo)."""
    if index == 0:
        return "Câmera 0"
    return f"Câmera {index}"
