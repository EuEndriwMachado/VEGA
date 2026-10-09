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


def _brightness(image) -> float:
    """Brilho médio (0-255). Usado para detectar captura preta."""
    try:
        grayscale = image.convert("L")
        return float(sum(grayscale.getdata()) / (grayscale.width * grayscale.height))
    except Exception:
        return 255.0


def capture_image(monitor=0, retry_dark=True):
    """Captura a tela do PC e devolve um objeto PIL.Image (sem salvar).

    Fonte única de captura de tela: usada tanto pelo print do usuário
    (que salva em disco) quanto pela visão do Gemini (que envia a imagem).
    Retorna None se a captura não estiver disponível.

    ``retry_dark``: uma captura quase preta costuma significar que a janela da
    PRÓPRIA VEGA estava na frente (o tema dela é `#010305`, quase preto).
    Entregar isso ao modelo faz a VEGA responder "estou vendo uma tela escura"
    — verdadeiro e inútil. Nesse caso tenta de novo, com uma breve espera.
    """
    try:
        import mss
        from PIL import Image
    except ImportError:
        return None

    def _grab():
        with mss.mss() as sct:
            monitors = sct.monitors
            index = monitor if 0 <= monitor < len(monitors) else 0
            shot = sct.grab(monitors[index])
            return Image.frombytes("RGB", shot.size, shot.rgb)

    try:
        image = _grab()
    except Exception:
        return None

    if image is None or not retry_dark:
        return image

    attempts = 0
    while _brightness(image) < 8.0 and attempts < 3:
        attempts += 1
        time.sleep(0.25)
        try:
            candidate = _grab()
        except Exception:
            break
        if candidate is None:
            break
        if _brightness(candidate) >= _brightness(image):
            image = candidate
        if _brightness(image) >= 8.0:
            break

    return image


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