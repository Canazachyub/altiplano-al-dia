"""Control de calidad ANTES de publicar el boletín y la web (regla: nada sale sin pasar esta revisión).

revisar_boletin(cfg, pdf)  y  revisar_web(carpeta)  devuelven un informe:
    {"errores": [...], "avisos": [...], "corregido": [...], "hojas": [rutas de imágenes para mirar]}
Un ERROR bloquea la publicación (pipeline_diario no hace git push). Un AVISO se informa.

Qué se revisa
  Boletín: huecos de noticia vacíos, páginas sin notas, notas repetidas, fotos con texto (OCR),
           caras cortadas por el recorte, fotos de baja resolución para su hueco, notas grandes sin
           foto, PDF generado y sin la ficha interna. Se arma una hoja con todas las páginas.
  Web:     cada foto publicada sin texto (OCR), foco de encuadre, titulares/slugs repetidos, notas
           del boletín presentes en la web, notas sin cuerpo. Se arma una hoja de contacto de fotos.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .config import BASE_DIR

log = logging.getLogger("monitor")
SALIDA = BASE_DIR / "data" / "revision"
DPI_MIN = 110            # resolución mínima de una foto impresa en su hueco (px por pulgada)


def _norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


def _informe():
    return {"errores": [], "avisos": [], "corregido": [], "hojas": []}


# ------------------------------------------------------------------ boletín
def revisar_boletin(cfg, pdf=None, ahora=None) -> dict:
    from . import consolidado as C, encuadre, maqueta as M, storage
    from .sin_texto import textos
    r = _informe()
    c = storage.consolidado_del_dia()
    if not c:
        r["errores"].append("No hay boletín del día.")
        return r
    pdf = Path(pdf or c["path"])
    ahora = ahora or datetime.fromisoformat(c["fecha"][:19])
    if not pdf.exists():
        r["errores"].append(f"No existe el PDF {pdf}.")
        return r
    grupos = {g["id"]: g for g in storage.grupos_de_consolidado(c["n"])}
    for g in storage.grupos_para_pdf(con_relleno=True):
        grupos.setdefault(g["id"], g)
    arts = [C._articulo(g, cfg) for g in grupos.values()]
    paginas, sobr = M.armar(arts, M.plantilla_del_dia(ahora), C.leer_fijas())

    vac = M._vacios(paginas)
    if vac:
        r["avisos"].append(f"{len(vac)} hueco(s) de noticia vacío(s) (salen con aviso): "
                           + ", ".join(f"pág. {h.pag} {h.rol}" for h in vac[:8]))
    for p in paginas:
        if p["huecos"] and not any(h.art for h in p["huecos"]):
            r["errores"].append(f"La página {p['n']} ({p['seccion']}) no tiene ninguna nota.")
    vistos = {}
    for p in paginas:
        for h in p["huecos"]:
            a = h.art
            if not a:
                continue
            clave = _norm(a["titular"])
            if clave in vistos:
                r["errores"].append(f"Nota repetida: «{a['titular']}» (págs. {vistos[clave]} y {p['n']}).")
            vistos[clave] = p["n"]
            if h.rol in ("principal", "secundaria") and not a["img"]:
                r["avisos"].append(f"Pág. {p['n']}: la {h.rol} «{a['titular'][:60]}» va sin foto.")
            if not a["img"]:
                continue
            if not Path(a["img"]).exists():
                r["errores"].append(f"Pág. {p['n']}: falta el archivo de la foto de «{a['titular'][:60]}».")
                continue
            if textos(a["img"]):
                r["errores"].append(f"Pág. {p['n']}: la foto de «{a['titular'][:60]}» tiene texto.")
            # caras dentro del recorte
            with Image.open(a["img"]) as im:
                W, Hh = im.size
            fw = h.w
            fh = max(40.0, h.h * (0.55 if h.rol in ("principal", "secundaria") else 0.45))
            x0, y0, x1, y1 = encuadre.caja_recorte(W, Hh, fw / fh, encuadre.foco(a["img"]))
            for cx0, cy0, cx1, cy1 in encuadre.caras(a["img"]):
                if (cx1 - cx0) * (cy1 - cy0) < 0.01:
                    continue
                if cx0 * W < x0 - 2 or cx1 * W > x1 + 2 or cy0 * Hh < y0 - 2 or cy1 * Hh > y1 + 2:
                    r["avisos"].append(f"Pág. {p['n']}: el recorte de «{a['titular'][:50]}» puede cortar una cara.")
                    break
            dpi = (x1 - x0) / (fw / 72)
            if dpi < DPI_MIN:
                r["avisos"].append(f"Pág. {p['n']}: la foto de «{a['titular'][:50]}» tiene poca resolución "
                                   f"({round(dpi)} ppp).")
    if sobr:
        r["avisos"].append(f"{len(sobr)} nota(s) no entraron en el papel (van solo a la web).")
    ficha = pdf.with_name(f"{pdf.stem}_ficha.pdf")
    if not ficha.exists():
        r["avisos"].append("No se encontró la ficha de verificación aparte (el boletín no debe llevarla).")
    r["hojas"].append(str(_hoja_pdf(pdf)))
    return r


def _hoja_pdf(pdf: Path) -> Path:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    ims = [doc[i].render(scale=0.5).to_pil().convert("RGB") for i in range(len(doc))]
    doc.close()
    w, h = ims[0].size
    cols = 5
    filas = (len(ims) + cols - 1) // cols
    hoja = Image.new("RGB", (cols * w, filas * h), "#777")
    for i, im in enumerate(ims):
        hoja.paste(im, ((i % cols) * w, (i // cols) * h))
    SALIDA.mkdir(parents=True, exist_ok=True)
    dst = SALIDA / "boletin_paginas.jpg"
    hoja.save(dst, quality=80)
    return dst


# ------------------------------------------------------------------ web
def revisar_web(carpeta, cfg=None) -> dict:
    """carpeta = la carpeta 'public' del sitio (con data/noticias.json e img/)."""
    from . import storage
    from .sin_texto import textos
    r = _informe()
    carpeta = Path(carpeta)
    try:
        notas = json.loads((carpeta / "data" / "noticias.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        r["errores"].append(f"No se pudo leer noticias.json: {e}")
        return r
    if not notas:
        r["errores"].append("La web quedaría sin noticias.")
        return r
    tit, slugs, fotos = {}, set(), []
    for n in notas:
        k = _norm(n.get("titular"))
        if k in tit:
            r["errores"].append(f"Titular repetido en la web: «{n['titular']}» (#{tit[k]} y #{n['id']}).")
        tit[k] = n["id"]
        if n.get("slug") in slugs:
            r["errores"].append(f"Dirección repetida: {n['slug']}")
        slugs.add(n.get("slug"))
        if not n.get("cuerpo"):
            r["errores"].append(f"Nota sin texto: «{n.get('titular')}».")
        if n.get("imagen"):
            f = carpeta / n["imagen"]
            if not f.exists():
                r["errores"].append(f"Falta la foto de «{n['titular'][:60]}» ({n['imagen']}).")
                continue
            if textos(f):
                r["errores"].append(f"La foto de «{n['titular'][:60]}» tiene texto.")
            if not n.get("foco"):
                r["avisos"].append(f"La foto de «{n['titular'][:60]}» no tiene foco de encuadre.")
            fotos.append((n["id"], f))
    c = storage.consolidado_del_dia()
    if c:
        en_web = {n["id"] for n in notas}
        faltan = [g for g in storage.grupos_de_consolidado(c["n"]) if g["id"] not in en_web]
        for g in faltan:
            r["errores"].append(f"La nota del boletín «{g['titular']}» no está en la web.")
    sin = sum(1 for n in notas if not n.get("imagen"))
    r["avisos"].append(f"Web: {len(notas)} notas, {len(notas) - sin} con foto, {sin} sin foto.")
    if fotos:
        r["hojas"].append(str(_hoja_fotos(fotos)))
    return r


def _hoja_fotos(fotos) -> Path:
    T, cols = 220, 8
    filas = (len(fotos) + cols - 1) // cols
    hoja = Image.new("RGB", (cols * T, filas * (T + 16)), "white")
    d = ImageDraw.Draw(hoja)
    for i, (gid, f) in enumerate(fotos):
        with Image.open(f) as im:
            im = im.convert("RGB")
            im.thumbnail((T, T))
            x, y = (i % cols) * T, (i // cols) * (T + 16)
            hoja.paste(im, (x, y))
        d.text((x + 2, y + T + 2), f"#{gid}", fill="black")
    SALIDA.mkdir(parents=True, exist_ok=True)
    dst = SALIDA / "web_fotos.jpg"
    hoja.save(dst, quality=82)
    return dst


def guardar(informes: dict) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    dst = SALIDA / "ultima.json"
    dst.write_text(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), **informes},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return dst
