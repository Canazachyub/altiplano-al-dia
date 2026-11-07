"""Control final de imágenes: en el boletín y en la web solo van FOTOS, sin ningún texto encima.

Cada imagen candidata se lee con OCR (RapidOCR, local). Si no tiene texto, pasa tal cual. Si tiene,
se busca el recorte más grande sin texto (sin titulares, rótulos, logos con letras ni sellos de
medios) con proporción de foto; el recorte se vuelve a leer con OCR antes de aceptarlo. Si no queda
un recorte útil, la imagen se rechaza y quien llama prueba con otra foto de la nota (o la nota
sale sin foto). Nada con texto llega a publicarse.

Los resultados se guardan en data/fotos_sin_texto/indice.json (por ruta + fecha de modificación).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from .config import BASE_DIR

log = logging.getLogger("monitor")

DIR = BASE_DIR / "data" / "fotos_sin_texto"
INDICE = DIR / "indice.json"
REJILLA = 120            # el análisis de huecos libres se hace en una rejilla de 120 x 120
MARGEN = 0.02            # margen alrededor de cada texto (fracción del lado)
AREA_MIN = 0.30          # el recorte debe conservar al menos el 30 % de la imagen
LADO_MIN = 260           # px: un recorte más chico no sirve como foto de nota
PROP_MIN, PROP_MAX = 0.75, 1.9   # ancho / alto aceptables para una foto de nota
VERSION = 5
CORTE_ARRIBA_MAX = 0.35   # un recorte que pierde más de la parte de arriba deja cuerpos sin cabeza

_ocr = None
_ocr_lock = threading.Lock()
_idx_lock = threading.Lock()


def _motor():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def textos(ruta) -> list:
    """[(x0, y0, x1, y1, texto)] en píxeles de la imagen. Se lee en dos tamaños (el normal y uno
    ampliado) para que no se escape el texto chico de un cartel o de una marca de agua."""
    out = []
    with Image.open(ruta) as im0:
        im0 = im0.convert("RGB")
        w, h = im0.size
        # la copia que se publica en la web (máx. 1280 px, JPG 82): la compresión a veces deja ver
        # restos de letras que en la original no se leen
        import io
        web = im0.copy()
        web.thumbnail((1280, 1280))
        buf = io.BytesIO()
        web.save(buf, "JPEG", quality=82)
        buf.seek(0)
        with Image.open(buf) as wj:
            ew = wj.size[0] / w
            out += _leer_ocr(np.asarray(wj.convert("RGB"))[:, :, ::-1], ew, h)
        for objetivo in (1600, 2400):
            esc = min(objetivo / max(w, h), 2.0) if objetivo > 1600 else min(1.0, objetivo / max(w, h))
            im = im0.resize((round(w * esc), round(h * esc))) if esc != 1 else im0
            out += _leer_ocr(np.asarray(im)[:, :, ::-1], esc, h)    # RGB -> BGR (lo que espera el motor)
    return out


def _leer_ocr(arr, esc, h) -> list:
    with _ocr_lock:
        res, _ = _motor()(arr)
    out = []
    for caja, txt, score in res or []:
        txt = (txt or "").strip()
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if score < 0.45 or not txt:
            continue
        xs = [p[0] / esc for p in caja]
        ys = [p[1] / esc for p in caja]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        # un solo carácter suelto y chico suele ser ruido (textura, número de camiseta lejano)
        if len(txt) < 2 and (y1 - y0) < 0.03 * h:
            continue
        if sum(ch.isascii() and ch.isalnum() or ch in "áéíóúñÁÉÍÓÚÑ" for ch in txt) < 2:
            continue                                # ruido del OCR (caracteres sueltos o de otro alfabeto)
        if txt.isdigit() and len(txt) <= 3:
            continue                                # dorsales, placas: son parte de la foto
        out.append((x0, y0, x1, y1, txt))
    return out


def _mejor_recorte(w, h, cajas):
    """El rectángulo más grande sin texto, con proporción de foto. (x0, y0, x1, y1) o None."""
    n = REJILLA
    libre = np.ones((n, n), dtype=bool)
    mx, my = MARGEN * w, MARGEN * h
    for x0, y0, x1, y1, _ in cajas:
        c0 = max(0, int((x0 - mx) / w * n))
        c1 = min(n, int(np.ceil((x1 + mx) / w * n)))
        r0 = max(0, int((y0 - my) / h * n))
        r1 = min(n, int(np.ceil((y1 + my) / h * n)))
        libre[r0:r1, c0:c1] = False
    cw, ch = w / n, h / n
    mejor, area_mejor = None, 0.0
    for top in range(n):
        cols = libre[top].copy()
        if not cols.any():
            continue
        for bot in range(top, n):
            cols &= libre[bot]
            if not cols.any():
                break
            alto = (bot - top + 1) * ch
            # tramos de columnas libres
            d = np.diff(np.concatenate(([0], cols.astype(np.int8), [0])))
            ini, fin = np.where(d == 1)[0], np.where(d == -1)[0]
            for a, b in zip(ini, fin):
                ancho = (b - a) * cw
                use_w = min(ancho, PROP_MAX * alto)
                if use_w < PROP_MIN * alto:
                    continue                       # demasiado angosto para este alto
                area = use_w * alto
                if area > area_mejor:
                    cx = (a * cw + b * cw) / 2
                    x0 = max(a * cw, min(cx - use_w / 2, b * cw - use_w))
                    mejor = (x0, top * ch, x0 + use_w, (bot + 1) * ch)
                    area_mejor = area
    if not mejor or area_mejor < AREA_MIN * w * h:
        return None
    x0, y0, x1, y1 = (round(v) for v in mejor)
    if min(x1 - x0, y1 - y0) < LADO_MIN or y0 > CORTE_ARRIBA_MAX * h:
        return None
    return x0, y0, x1, y1


def _leer():
    try:
        return json.loads(INDICE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _guardar(clave, valor):
    with _idx_lock:
        d = _leer()
        d[clave] = valor
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = INDICE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(INDICE)


def _clave(ruta: Path) -> str:
    return f"v{VERSION}|{ruta.resolve()}|{ruta.stat().st_mtime_ns}"


def verificada(ruta: str | None) -> str | None:
    """La imagen lista para publicar: la misma si no tiene texto, un recorte sin texto, o None si no
    se puede sacar una foto limpia de ella."""
    if not ruta:
        return None
    p = Path(ruta)
    if not p.exists():
        return None
    clave = _clave(p)
    previo = _leer().get(clave)
    if previo is not None:
        if previo.get("ruta") and Path(previo["ruta"]).exists():
            return previo["ruta"]
        if previo.get("ruta") is None and previo.get("motivo"):
            return None
    try:
        cajas = textos(p)
    except Exception as e:  # noqa: BLE001
        log.warning("OCR no disponible para %s: %s", p, e)
        return None                                  # sin poder verificar, no se publica
    if not cajas:
        _guardar(clave, {"ruta": str(p), "textos": 0})
        return str(p)
    with Image.open(p) as im:
        im = im.convert("RGB")
        w, h = im.size
        caja = _mejor_recorte(w, h, cajas)
        if not caja:
            _guardar(clave, {"ruta": None, "motivo": "texto en toda la imagen",
                             "textos": [c[4] for c in cajas][:12]})
            log.info("Foto rechazada (texto en toda la imagen): %s %s", p.name, [c[4] for c in cajas][:6])
            return None
        DIR.mkdir(parents=True, exist_ok=True)
        dst = DIR / (hashlib.sha1(clave.encode()).hexdigest()[:16] + ".jpg")
        im.crop(caja).save(dst, "JPEG", quality=90)
    try:
        resto = textos(dst)
    except Exception:  # noqa: BLE001
        resto = ["?"]
    if resto:
        dst.unlink(missing_ok=True)
        _guardar(clave, {"ruta": None, "motivo": "el recorte sigue con texto",
                         "textos": [c[4] for c in resto if isinstance(c, tuple)][:12]})
        return None
    _guardar(clave, {"ruta": str(dst), "recorte": list(caja), "textos": [c[4] for c in cajas][:12]})
    log.info("Foto recortada sin texto: %s -> %s %s", p.name, dst.name, caja)
    return str(dst)
