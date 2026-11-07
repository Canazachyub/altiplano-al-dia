"""Tarjeta para compartir (1200×630): la foto de la nota con la sección, el titular y la marca encima.

Es la imagen que muestran Facebook, WhatsApp y X al compartir el enlace de una nota (og:image), al estilo de
los medios regionales: foto a sangre, cinta de sección, titular grande y pie «Lee más en altiplanoaldia.pe».
Se guarda en caché y solo se rehace si cambia la foto, el titular o la sección.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import BASE_DIR

W, H = 1200, 630
AZUL = (4, 63, 155)
AZUL_OSCURO = (6, 24, 58)
AMARILLO = (255, 222, 0)
FUENTES = BASE_DIR / "assets" / "fonts"
LOGO = BASE_DIR / "assets" / "brand" / "logo-blanco.png"
CACHE = BASE_DIR / "data" / "web_cache" / "compartir"


def _fuente(nombre: str, tam: int):
    try:
        return ImageFont.truetype(str(FUENTES / nombre), tam)
    except OSError:
        return ImageFont.load_default()


def _lineas(d: ImageDraw.ImageDraw, texto: str, fuente, ancho: int) -> list[str]:
    out, linea = [], ""
    for p in texto.split():
        prueba = f"{linea} {p}".strip()
        if d.textlength(prueba, font=fuente) <= ancho or not linea:
            linea = prueba
        else:
            out.append(linea)
            linea = p
    if linea:
        out.append(linea)
    return out


def _titular(d, texto, ancho, max_lineas=3):
    """El tamaño más grande (64 → 40 px) con el que el titular entra en `max_lineas` líneas."""
    for tam in range(64, 39, -4):
        f = _fuente("FiraSansCondensed-Bold.ttf", tam)
        ls = _lineas(d, texto, f, ancho)
        if len(ls) <= max_lineas:
            return f, ls, tam
    f = _fuente("FiraSansCondensed-Bold.ttf", 40)
    ls = _lineas(d, texto, f, ancho)[:max_lineas]
    ls[-1] = ls[-1].rstrip(".,;:") + "…"
    return f, ls, 40


def dibujar(foto: Path, seccion: str, titular: str) -> Image.Image:
    with Image.open(foto) as im:
        base = ImageOps.fit(im.convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.4))

    # degradado azul oscuro de abajo hacia arriba, para que el titular se lea sobre cualquier foto
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    desde = 200
    for y in range(desde, H):
        a = int(245 * ((y - desde) / (H - desde)) ** 1.15)
        dc.line([(0, y), (W, y)], fill=AZUL_OSCURO + (a,))
    img = Image.alpha_composite(base.convert("RGBA"), capa)
    d = ImageDraw.Draw(img)

    # pie de marca: barra azul con el logo y «Lee más en altiplanoaldia.pe»
    pie = 64
    d.rectangle([0, H - pie, W, H], fill=AZUL)
    d.rectangle([0, H - pie - 6, W, H - pie], fill=AMARILLO)
    if LOGO.exists():
        with Image.open(LOGO) as lg:
            lg = lg.convert("RGBA")
            alto = 38
            lg = lg.resize((round(lg.width * alto / lg.height), alto), Image.LANCZOS)
            img.alpha_composite(lg, (40, H - pie + (pie - alto) // 2))
    f_pie = _fuente("FiraSansCondensed-SemiBold.ttf", 28)
    txt = "Lee más en altiplanoaldia.pe"
    d.text((W - 40 - d.textlength(txt, font=f_pie), H - pie + 15), txt, font=f_pie, fill=(255, 255, 255))

    # titular
    margen = 48
    f_t, lineas, tam = _titular(d, titular, W - 2 * margen)
    paso = round(tam * 1.12)
    y = H - pie - 6 - 28 - paso * len(lineas)
    # cinta de sección (amarilla, texto azul, en mayúsculas)
    if seccion:
        f_s = _fuente("FiraSansCondensed-ExtraBold.ttf", 28)
        s = seccion.upper()
        an = d.textlength(s, font=f_s)
        y_s = y - 58
        d.rounded_rectangle([margen, y_s, margen + an + 36, y_s + 44], radius=6, fill=AMARILLO)
        d.text((margen + 18, y_s + 6), s, font=f_s, fill=AZUL)
    for i, ln in enumerate(lineas):
        d.text((margen + 2, y + i * paso + 2), ln, font=f_t, fill=(0, 0, 0, 120))
        d.text((margen, y + i * paso), ln, font=f_t, fill=(255, 255, 255))
    return img.convert("RGB")


def tarjeta(foto: Path, seccion: str, titular: str, gid: int) -> Path | None:
    """Ruta a la tarjeta en caché (la rehace si cambió algo); None si no se pudo."""
    foto = Path(foto)
    if not foto.exists():
        return None
    firma = hashlib.sha1(f"v1|{foto.resolve()}|{foto.stat().st_mtime_ns}|{seccion}|{titular}".encode()).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    dst, marca = CACHE / f"n{gid}.jpg", CACHE / f"n{gid}.txt"
    if dst.exists() and marca.exists() and marca.read_text(encoding="utf-8") == firma:
        return dst
    try:
        dibujar(foto, seccion, titular).save(dst, "JPEG", quality=84, optimize=True, progressive=True)
    except Exception:  # noqa: BLE001
        return None
    marca.write_text(firma, encoding="utf-8")
    return dst
