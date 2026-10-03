# -*- coding: utf-8 -*-
"""Gera o ícone da VEGA (assets/vega.ico) programaticamente.

Desenha um orbe estelar (tema da interface) em vários tamanhos e
empacota como .ico multi-resolução para o atalho da área de trabalho.

Execução manual:
    python tools/make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw


SIZE = 256
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
OUT_FILE = os.path.join(OUT_DIR, "vega.ico")


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _glow_ring(draw, cx, cy, radius, width, color, alpha):
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=color + (alpha,),
        width=width,
    )


def build_icon(size=SIZE):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size * 0.46

    # --- halo externo (glow) ---
    glow_layers = 22
    for i in range(glow_layers, 0, -1):
        t = i / glow_layers
        radius = r * (1 + 0.16 * t)
        alpha = int(70 * (1 - t) ** 2)
        color = _lerp((120, 190, 255), (90, 60, 200), t)
        _glow_ring(draw, cx, cy, radius, max(2, int(size * 0.012)), color, alpha)

    # --- corpo do orbe: gradiente radial azul -> violeta ---
    body = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    body_draw = ImageDraw.Draw(body)
    steps = 120
    for i in range(steps, 0, -1):
        t = i / steps
        radius = r * t
        color = _lerp((18, 32, 64), (120, 60, 220), 1 - t)
        body_draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color + (255,),
        )
    # máscara circular
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    img.paste(body, (0, 0), mask)

    # --- brilho especular no topo-esquerda ---
    shine = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shine_draw = ImageDraw.Draw(shine)
    sr = r * 0.55
    sx, sy = cx - r * 0.3, cy - r * 0.35
    for i in range(30, 0, -1):
        t = i / 30
        radius = sr * t
        alpha = int(60 * (1 - t) ** 1.5)
        shine_draw.ellipse(
            [sx - radius, sy - radius, sx + radius, sy + radius],
            fill=(210, 235, 255, alpha),
        )
    img = Image.alpha_composite(img, shine)
    draw = ImageDraw.Draw(img)

    # --- anéis orbitais ---
    ring_color = (170, 225, 255, 230)
    ring_width = max(3, int(size * 0.016))
    for tilt, rx_scale in ((-0.32, 1.0), (0.42, 0.86)):
        # elipse inclinada simulada via arco
        rx = r * 1.06 * rx_scale
        ry = rx * abs(tilt) * 0.6 + r * 0.14
        bbox = [cx - rx, cy - ry, cx + rx, cy + ry]
        if tilt < 0:
            draw.arc(bbox, start=200, end=340, fill=ring_color, width=ring_width)
        else:
            draw.arc(bbox, start=20, end=160, fill=ring_color, width=ring_width)

    # --- estrela de 4 pontas (Vega, a estrela) ---
    def star_points(cx, cy, outer, inner):
        pts = []
        for k in range(8):
            angle = math.pi / 2 * (k / 2) + (math.pi / 8 if False else 0)
            # alterna pontas externas/internas
            radius = outer if k % 2 == 0 else inner
            angle = k * math.pi / 4 - math.pi / 2
            pts.append((cx + radius * math.sin(angle), cy - radius * math.cos(angle)))
        return pts

    star_r = r * 0.46
    draw.polygon(
        star_points(cx, cy, star_r, star_r * 0.22),
        fill=(240, 250, 255, 255),
    )
    # núcleo brilhante da estrela
    core = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    core_draw = ImageDraw.Draw(core)
    for i in range(24, 0, -1):
        t = i / 24
        radius = star_r * 0.30 * (1 / max(t, 0.01)) ** 0.3 * t
        alpha = int(180 * (1 - t))
        core_draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(255, 255, 255, alpha),
        )
    img = Image.alpha_composite(img, core)

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    base = build_icon(SIZE)
    sizes = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]
    base.save(OUT_FILE, format="ICO", sizes=sizes)
    # PNG grande de referência (para documentação/README futuro)
    base.save(os.path.join(OUT_DIR, "vega_256.png"), format="PNG")
    print(f"Icone gerado: {OUT_FILE}")


if __name__ == "__main__":
    main()
