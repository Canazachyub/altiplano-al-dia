"""Encuadre de fotos: dónde está lo importante (las caras) para recortar sin cortar cabezas.

foco(ruta) -> (fx, fy) en 0..1: el punto que debe quedar dentro del recorte. Se calcula con los
detectores de caras de OpenCV (frente y perfil); sin caras, un punto algo arriba del centro (en las
fotos de noticias lo importante suele estar en el tercio superior). Se guarda en caché por archivo.

recortar(im, w, h, foco) -> recorte con la proporción w/h centrado en el foco.
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image

from .config import BASE_DIR

CACHE = BASE_DIR / "data" / "encuadre.json"
POR_DEFECTO = (0.5, 0.38)
_lock = threading.Lock()
_cache: dict | None = None


def _leer():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _cache = {}
    return _cache


def _guardar():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


MODELO = Path(__file__).resolve().parent / "modelos" / "face_detection_yunet_2023mar.onnx"
_det_lock = threading.Lock()


@lru_cache(maxsize=1)
def _detector():
    import cv2
    return cv2.FaceDetectorYN.create(str(MODELO), "", (320, 320), 0.75, 0.3, 50)


def caras(ruta) -> list:
    """[(x0, y0, x1, y1)] en fracciones de la imagen (detector YuNet de OpenCV)."""
    import numpy as np
    with Image.open(ruta) as im:
        im = im.convert("RGB")
        esc = min(1.0, 960 / max(im.size))
        if esc < 1:
            im = im.resize((round(im.width * esc), round(im.height * esc)))
        arr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])
    h, w = arr.shape[:2]
    with _det_lock:
        d = _detector()
        d.setInputSize((w, h))
        _, res = d.detect(arr)
    out = []
    for f in (res if res is not None else []):
        x, y, cw, ch = (float(v) for v in f[:4])
        if cw * ch < 0.0015 * w * h:            # caras diminutas del fondo no mandan el encuadre
            continue
        out.append((max(0, x / w), max(0, y / h), min(1, (x + cw) / w), min(1, (y + ch) / h)))
    return out


def foco(ruta) -> tuple:
    if not ruta:
        return POR_DEFECTO
    p = Path(ruta)
    try:
        clave = f"{p.resolve()}|{p.stat().st_mtime_ns}"
    except OSError:
        return POR_DEFECTO
    with _lock:
        c = _leer()
        if clave in c:
            return tuple(c[clave])
    try:
        cs = caras(p)
    except Exception:  # noqa: BLE001
        cs = []
    if cs:
        # las caras más grandes mandan; el foco va un poco por debajo de los ojos para dejar aire arriba
        pesos = [(c[2] - c[0]) * (c[3] - c[1]) for c in cs]
        tot = sum(pesos)
        fx = sum((c[0] + c[2]) / 2 * p_ for c, p_ in zip(cs, pesos)) / tot
        fy = sum((c[1] + c[3]) / 2 * p_ for c, p_ in zip(cs, pesos)) / tot
        r = (round(fx, 3), round(min(0.9, fy + 0.08), 3))
    else:
        r = POR_DEFECTO
    with _lock:
        _leer()[clave] = list(r)
        _guardar()
    return r


def caja_recorte(W, H, r, f) -> tuple:
    """(x0, y0, x1, y1) del recorte más grande con proporción r (=w/h) que deja el foco f lo más
    centrado posible sin salirse de la imagen."""
    fx, fy = f
    if W / H > r:
        nw, nh = int(H * r), H
    else:
        nw, nh = W, int(W / r)
    x0 = min(max(0, int(fx * W - nw / 2)), W - nw)
    y0 = min(max(0, int(fy * H - nh / 2)), H - nh)
    return x0, y0, x0 + nw, y0 + nh


def recortar(im: Image.Image, w, h, f) -> Image.Image:
    return im.crop(caja_recorte(im.width, im.height, w / h, f))


def css(f) -> str:
    """object-position para la web."""
    return f"{round(f[0] * 100)}% {round(f[1] * 100)}%"
