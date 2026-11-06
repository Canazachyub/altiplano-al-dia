"""Dibuja el logo en vectores (desde un SVG simple) sobre un canvas de reportlab.

Lee solo lo que traen los logos de la marca: <rect> y <path> con fill / fill-rule, y en `d`
los comandos M L H V Q T C S Z (absolutos y relativos). Sin dependencias además de reportlab.
"""
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfgen.canvas import FILL_EVEN_ODD, FILL_NON_ZERO

_TOKEN = re.compile(r"[MmLlHhVvQqTtCcSsZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "Q": 4, "T": 2, "C": 6, "S": 4, "Z": 0}


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _segmentos(d):
    """Convierte `d` en una lista de ('M', x, y) / ('L', x, y) / ('C', x1, y1, x2, y2, x, y) / ('Z',)
    en coordenadas absolutas del SVG."""
    toks = _TOKEN.findall(d)
    out, i, cmd = [], 0, None
    x = y = sx = sy = 0.0
    ctrl = None          # último punto de control (para T y S)
    prev = None          # comando anterior en mayúscula
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                out.append(("Z",))
                x, y, ctrl, prev = sx, sy, None, "Z"
                continue
        if cmd is None:
            raise ValueError("path sin comando inicial")
        up, rel = cmd.upper(), cmd.islower()
        n = _ARGS[up]
        a = [float(t) for t in toks[i:i + n]]
        if len(a) < n:
            break
        i += n
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == "M":
            x, y = ox + a[0], oy + a[1]
            sx, sy = x, y
            out.append(("M", x, y))
            cmd = "l" if rel else "L"     # pares siguientes = lineto implícito
            ctrl = None
        elif up == "L":
            x, y = ox + a[0], oy + a[1]
            out.append(("L", x, y))
            ctrl = None
        elif up == "H":
            x = (x if rel else 0.0) + a[0]
            out.append(("L", x, y))
            ctrl = None
        elif up == "V":
            y = (y if rel else 0.0) + a[0]
            out.append(("L", x, y))
            ctrl = None
        elif up in ("Q", "T"):
            if up == "Q":
                qx, qy = ox + a[0], oy + a[1]
                ex, ey = ox + a[2], oy + a[3]
            else:
                qx, qy = (2 * x - ctrl[0], 2 * y - ctrl[1]) if ctrl and prev in ("Q", "T") else (x, y)
                ex, ey = ox + a[0], oy + a[1]
            # cuadrática -> cúbica
            c1 = (x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y))
            c2 = (ex + 2 / 3 * (qx - ex), ey + 2 / 3 * (qy - ey))
            out.append(("C", *c1, *c2, ex, ey))
            x, y, ctrl = ex, ey, (qx, qy)
        elif up in ("C", "S"):
            if up == "C":
                c1 = (ox + a[0], oy + a[1])
                c2 = (ox + a[2], oy + a[3])
                ex, ey = ox + a[4], oy + a[5]
            else:
                c1 = (2 * x - ctrl[0], 2 * y - ctrl[1]) if ctrl and prev in ("C", "S") else (x, y)
                c2 = (ox + a[0], oy + a[1])
                ex, ey = ox + a[2], oy + a[3]
            out.append(("C", *c1, *c2, ex, ey))
            x, y, ctrl = ex, ey, c2
        prev = up
    return out


@lru_cache(maxsize=8)
def _cargar(ruta: str, mtime: float):
    raiz = ET.parse(ruta).getroot()
    vb = raiz.get("viewBox")
    if vb:
        vx, vy, vw, vh = (float(v) for v in vb.replace(",", " ").split())
    else:
        vx, vy = 0.0, 0.0
        vw, vh = float(raiz.get("width")), float(raiz.get("height"))
    formas = []
    for el in raiz.iter():
        t = _tag(el)
        fill = el.get("fill", "#000000")
        if fill == "none" or t not in ("rect", "path"):
            continue
        if t == "rect":
            rx, ry = float(el.get("x", 0)), float(el.get("y", 0))
            rw, rh = float(el.get("width")), float(el.get("height"))
            segs = [("M", rx, ry), ("L", rx + rw, ry), ("L", rx + rw, ry + rh), ("L", rx, ry + rh), ("Z",)]
            regla = "nonzero"
        else:
            segs = _segmentos(el.get("d", ""))
            regla = el.get("fill-rule", "nonzero")
        formas.append((colors.HexColor(fill), regla == "evenodd", segs))
    return (vx, vy, vw, vh), formas


def proporcion(ruta) -> float:
    """Ancho / alto del viewBox."""
    p = Path(ruta)
    (_, _, vw, vh), _ = _cargar(str(p), p.stat().st_mtime)
    return vw / vh


def dibujar(c, ruta, x, y, w, h, anchor="c"):
    """Dibuja el SVG dentro de la caja (x, y, w, h) en puntos, conservando la proporción.
    anchor: 'c' centro, 'w' izquierda, 'e' derecha (horizontal; vertical siempre centrado),
    o 'nw'/'sw'/... combinando. Devuelve (x, y, ancho, alto) realmente usados."""
    p = Path(ruta)
    (vx, vy, vw, vh), formas = _cargar(str(p), p.stat().st_mtime)
    s = min(w / vw, h / vh)
    dw, dh = vw * s, vh * s
    ox = x + (0 if "w" in anchor else (w - dw) if "e" in anchor else (w - dw) / 2)
    oy = y + (0 if "s" in anchor else (h - dh) if "n" in anchor else (h - dh) / 2)

    def tx(px, py):  # SVG (y hacia abajo) -> PDF (y hacia arriba)
        return ox + (px - vx) * s, oy + dh - (py - vy) * s

    c.saveState()
    for color, evenodd, segs in formas:
        path = c.beginPath()
        for sg in segs:
            if sg[0] == "M":
                path.moveTo(*tx(sg[1], sg[2]))
            elif sg[0] == "L":
                path.lineTo(*tx(sg[1], sg[2]))
            elif sg[0] == "C":
                path.curveTo(*tx(sg[1], sg[2]), *tx(sg[3], sg[4]), *tx(sg[5], sg[6]))
            else:
                path.close()
        c.setFillColor(color)
        c.drawPath(path, stroke=0, fill=1, fillMode=FILL_EVEN_ODD if evenodd else FILL_NON_ZERO)
    c.restoreState()
    return ox, oy, dw, dh
