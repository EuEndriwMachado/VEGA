# -*- coding: utf-8 -*-
"""Captura de tela do PC para a VEGA.

Usa `mss` (já é dependência do projeto) e salva em `Uploads/pc/`.
A imagem é opcionalmente aberta no visualizador padrão do Windows,
para o usuário ver o print na hora.
"""

from __future__ import annotations

import os
import time


SCREENSHOT_DIR = os.path.join("Uploads", "pc")


def capture_image(monitor=0):
    """Captura a tela do PC e devolve um objeto PIL.Image (sem salvar).

    Fonte única de captura de tela: usada tanto pelo print do usuário
    (que salva em disco) quanto pela visão do Gemini (que envia a imagem).
    Retorna None se a captura não estiver disponível.
    """
    try:
        import mss
        from PIL import Image
    except ImportError:
        return None

    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            index = monitor if 0 <= monitor < len(monitors) else 0
            shot = sct.grab(monitors[index])
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        return None


def capture_screen(show=True, monitor=0):
    """Captura a tela do PC e salva em disco.

    Retorna um dict com:
      ok      -> bool
      message -> texto pronto para falar
      path    -> caminho absoluto do arquivo salvo
    """
    image = capture_image(monitor=monitor)
    if image is None:
        return {
            "ok": False,
            "message": "Não consegui capturar a tela. Verifique se o mss está instalado.",
            "path": "",
        }

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"pc_{int(time.time())}.png")

    try:
        image.save(path, "PNG")
    except Exception as error:
        return {
            "ok": False,
            "message": f"Não consegui salvar o print: {error}",
            "path": "",
        }

    absolute = os.path.abspath(path)
    shown = False
    if show:
        shown = open_on_pc(absolute)

    message = "Tirei um print da tela" + (" e abri aqui." if shown else ".")
    return {"ok": True, "message": message, "path": absolute, "shown": shown}


def open_on_pc(path) -> bool:
    """Abre um arquivo local com o visualizador padrão do sistema."""
    try:
        os.startfile(os.path.abspath(path))  # type: ignore[attr-defined]
        return True
    except (OSError, AttributeError):
        return False