"""Avisos para los espacios libres del periódico: "reporta tu noticia", "anuncia con nosotros",
o la imagen de un anuncio. Se guardan en data/avisos.json; las imágenes en data/avisos/.

Cada espacio libre se identifica por página y columna ("8:lat", "10:main"). Un aviso puede
colocarse a mano en un espacio, o los avisos marcados como "auto" rellenan solos los espacios
que queden libres (rotando entre ellos)."""
import copy
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics

from .config import ASSETS_DIR, BASE_DIR

AVISOS_PATH = BASE_DIR / "data" / "avisos.json"
IMG_AVISOS = BASE_DIR / "data" / "avisos"
ICONOS = {"telefono": "Teléfono", "chat": "Mensaje", "megafono": "Megáfono", "camara": "Cámara", "": "Sin icono"}

WHATSAPP = "+51 982 591 289"   # número público del periódico (denuncias, publicidad, noticias)
GRANDE = 150                   # alto (pt) desde el que un espacio libre se ofrece como "Anúnciate aquí"

DEFAULT = {
    "auto": True,             # rellenar solos los espacios libres con los avisos marcados "auto"
    "colocados": {},          # "pag:col" -> id de aviso (o "" para dejar el espacio vacío a propósito)
    "borrados": [],           # avisos de fábrica que el usuario eliminó (no se vuelven a agregar)
    "avisos": [
        {"id": "anunciate", "nombre": "Anúnciate aquí (espacios grandes)", "tipo": "texto", "auto": True,
         "icono": "megafono", "titulo": "¡VENDE MÁS! ANÚNCIATE AQUÍ",
         "mensaje": "Este espacio puede ser tuyo. Publicidad, avisos y denuncias al WhatsApp",
         "telefono": WHATSAPP, "imagen": "", "grande": True},
        {"id": "reporta", "nombre": "Reporta tu noticia", "tipo": "texto", "auto": True, "icono": "telefono",
         "titulo": "¿TIENES UNA NOTICIA?", "mensaje": "Repórtala o envíanos tu foto o video al WhatsApp",
         "telefono": WHATSAPP, "imagen": ""},
        {"id": "anuncia", "nombre": "Anuncia con nosotros", "tipo": "texto", "auto": True, "icono": "megafono",
         "titulo": "¡VENDE MÁS! ANÚNCIATE AQUÍ", "mensaje": "Llega a miles de lectores de Puno y la región. WhatsApp",
         "telefono": WHATSAPP, "imagen": ""},
        {"id": "denuncia", "nombre": "Denuncia", "tipo": "texto", "auto": True, "icono": "chat",
         "titulo": "¡DENÚNCIALO!", "mensaje": "¿Abusos, cobros indebidos u obras abandonadas? Escríbenos al WhatsApp",
         "telefono": WHATSAPP, "imagen": ""},
        {"id": "foto", "nombre": "Envía tu foto", "tipo": "texto", "auto": False, "icono": "camara",
         "titulo": "¿FUISTE TESTIGO?", "mensaje": "Envíanos tu foto y la publicamos con tu nombre. WhatsApp",
         "telefono": WHATSAPP, "imagen": ""},
    ],
}


def cargar() -> dict:
    try:
        d = json.loads(AVISOS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    out = copy.deepcopy(DEFAULT)
    out.update({k: v for k, v in d.items() if k in out})
    # avisos de fábrica nuevos (p. ej. "Anúnciate aquí") se agregan a una lista ya guardada
    ids = {a["id"] for a in out["avisos"]}
    for i, a in enumerate(DEFAULT["avisos"]):
        if a["id"] not in ids and a["id"] not in out["borrados"]:
            out["avisos"].insert(min(i, len(out["avisos"])), copy.deepcopy(a))
    # sin número propio, los avisos de texto usan el WhatsApp del periódico
    for a in out["avisos"]:
        if a.get("tipo") != "imagen" and not (a.get("telefono") or "").strip():
            a["telefono"] = WHATSAPP
    return out


def guardar(d: dict) -> None:
    AVISOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AVISOS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def nuevo_id() -> str:
    return uuid.uuid4().hex[:8]


def importar_imagen(ruta: str) -> str:
    IMG_AVISOS.mkdir(parents=True, exist_ok=True)
    im = Image.open(ruta)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    im.thumbnail((2000, 2000))
    destino = IMG_AVISOS / f"aviso_{datetime.now():%Y%m%d%H%M%S}.png"
    im.save(destino)
    return str(destino)


CONTACTO_SIN_TEL = "Altiplano al Día"     # sin número: se invita a escribir a la página de Facebook


def listo(a: dict) -> bool:
    """Un aviso se puede imprimir si tiene lo mínimo (imagen, o título si es de texto: sin teléfono
    sale con la invitación a escribir por Facebook)."""
    if a.get("tipo") == "imagen":
        return bool(a.get("imagen")) and Path(a["imagen"]).exists()
    return bool((a.get("titulo") or "").strip())


def contacto(a: dict) -> tuple[str, str]:
    """(mensaje, línea grande de contacto). Sin teléfono no se inventa un número."""
    msg = (a.get("mensaje") or "").strip()
    tel = (a.get("telefono") or "").strip()
    if tel:
        return msg, tel
    msg = re.sub(r"\s*(Ll[aá]manos al|Escr[ií]benos al|al WhatsApp|al)\s*:?$", "", msg, flags=re.I).rstrip(" .:")
    return (msg + (". " if msg and not msg.endswith("?") else " ") + "Escríbenos por Facebook:").strip(), \
        CONTACTO_SIN_TEL


def por_id(d: dict, aid: str):
    return next((a for a in d["avisos"] if a["id"] == aid), None)


class Asignador:
    """Decide qué aviso va en cada espacio libre durante una maquetación."""

    def __init__(self):
        self.d = cargar()
        listos = [a for a in self.d["avisos"] if a.get("auto") and listo(a)]
        self.grandes = [a for a in listos if a.get("grande")]
        self.rotacion = [a for a in listos if not a.get("grande")] or self.grandes
        self.i = self.j = 0

    def para(self, clave: str, alto: float, ancho: float | None = None, grande_ok: bool = True):
        """ancho (opcional): en un espacio ancho (zona principal) entra un aviso apaisado más bajo."""
        if clave in self.d["colocados"]:
            a = por_id(self.d, self.d["colocados"][clave])
            return (a, True) if a and listo(a) else (None, True)
        minimo = 60 if (ancho or 0) >= 300 else 90
        if not self.d.get("auto") or not self.rotacion or alto < minimo:
            return None, False
        if self.grandes and alto >= GRANDE and grande_ok:      # espacio grande: "Anúnciate aquí" con el WhatsApp
            a = self.grandes[self.j % len(self.grandes)]
            self.j += 1
            return a, False
        a = self.rotacion[self.i % len(self.rotacion)]
        self.i += 1
        return a, False


# ------------------------------------------------------------------ dibujo en el PDF
def _icono(nombre, blanco=True):
    if not nombre:
        return None
    f = ASSETS_DIR / "icons" / f"aviso_{nombre}{'_w' if blanco else ''}.png"
    return str(f) if f.exists() else None


def _ajustar(texto, font, ancho, maxsize, minsize, maxlineas):
    s = maxsize
    while s >= minsize:
        lineas = simpleSplit(texto, font, s, ancho)
        if len(lineas) <= maxlineas and all(pdfmetrics.stringWidth(x, font, s) <= ancho + 0.5 for x in lineas):
            return s, lineas
        s -= 0.5
    return minsize, simpleSplit(texto, font, minsize, ancho)[:maxlineas]


def _dibujar_apaisado(c, a, x0, y0, w, h, ico, acento):
    pad = 14
    x = x0 + pad
    s_ico = min(h - 2 * pad, 40) if ico and h > 50 else 0
    if s_ico:
        c.drawImage(ico, x, y0 + (h - s_ico) / 2, s_ico, s_ico, mask="auto")
        x += s_ico + 10
    sp, lp = _ajustar(a.get("telefono") or "", "P-Banda", w * 0.34, min(26, h * 0.42), 9, 1)
    wp = max([pdfmetrics.stringWidth(x_, "P-Banda", sp) for x_ in lp] or [0])
    ancho = max(40, x0 + w - pad - wp - 14 - x)
    st, lt = _ajustar((a.get("titulo") or "").upper(), "P-Banda", ancho, min(18, h * 0.3), 8, 2)
    sm, lm = _ajustar(a.get("mensaje") or "", "P-Ante", ancho, 10, 7, 2)
    total = len(lt) * st * 1.05 + 4 + len(lm) * sm * 1.2
    if total > h - 16:                       # no entra: se achica todo junto
        f = (h - 16) / total
        st, sm = st * f, sm * f
        lt = simpleSplit((a.get("titulo") or "").upper(), "P-Banda", st, ancho)[:2]
        lm = simpleSplit(a.get("mensaje") or "", "P-Ante", sm, ancho)[:2]
        total = len(lt) * st * 1.05 + 4 + len(lm) * sm * 1.2
    y = y0 + h / 2 + total / 2
    c.setFillColor(colors.white)
    for ln in lt:
        y -= st
        c.setFont("P-Banda", st)
        c.drawString(x, y, ln)
        y -= st * 0.05
    y -= 4
    for ln in lm:
        y -= sm
        c.setFont("P-Ante", sm)
        c.drawString(x, y, ln)
        y -= sm * 0.2
    c.setFillColor(acento)
    for ln in lp:
        c.setFont("P-Banda", sp)
        c.drawRightString(x0 + w - pad, y0 + h / 2 - sp * 0.35, ln)


def dibujar(c, a: dict, rect, D: dict):
    """Dibuja el aviso a dentro de rect=(x0, y0, x1, y1) (puntos PDF)."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    principal = colors.HexColor(D["colores"]["principal"])
    acento = colors.HexColor(D["colores"]["acento"])
    if a.get("tipo") == "imagen":
        im = ImageReader(a["imagen"])
        iw, ih = im.getSize()
        esc = min((w - 4) / iw, (h - 14) / ih)
        dw, dh = iw * esc, ih * esc
        c.drawImage(im, x0 + (w - dw) / 2, y0 + (h - 12 - dh) / 2, dw, dh, mask="auto")
        c.setFont("Narrow", 6)
        c.setFillColor(colors.HexColor("#888888"))
        c.drawCentredString(x0 + w / 2, y1 - 8, "PUBLICIDAD")
        return
    # aviso de texto: recuadro del color principal, título, mensaje y número grande
    a = dict(a)
    a["mensaje"], a["telefono"] = contacto(a)
    a["titulo"] = re.sub(r"([!?])\s+(?=\S)", "\\1\n", (a.get("titulo") or "").strip())   # "¡VENDE MÁS!" en su línea
    c.saveState()
    c.setFillColor(principal)
    c.roundRect(x0, y0, w, h, 6, stroke=0, fill=1)
    c.setStrokeColor(acento)
    c.setLineWidth(1.4)
    c.roundRect(x0 + 4, y0 + 4, w - 8, h - 8, 4, stroke=1, fill=0)
    pad = 10
    ancho = w - 2 * pad
    ico = _icono(a.get("icono", ""))
    if w >= 2.6 * h:              # espacio apaisado (zona principal): icono | título y mensaje | número
        _dibujar_apaisado(c, a, x0, y0, w, h, ico, acento)
        c.restoreState()
        return
    s_ico = min(ancho * 0.32, h * 0.22, 46) if ico and h > 120 else 0
    st, lt = _ajustar((a.get("titulo") or "").upper(), "P-Banda", ancho, min(22, ancho / 5.5), 8, 3)
    sm, lm = _ajustar(a.get("mensaje") or "", "P-Ante", ancho, 11, 7, 4)
    sp, lp = _ajustar(a.get("telefono") or "", "P-Banda", ancho, min(30, ancho / 4.2), 9, 1)
    bloques = [s_ico + (6 if s_ico else 0), len(lt) * st * 1.05 + 6, len(lm) * sm * 1.2 + 6, sp * 1.1]
    total = sum(bloques)
    if total > h - 2 * pad:                     # no entra: se achica todo junto
        f = (h - 2 * pad) / total
        st, sm, sp, s_ico = st * f, sm * f, sp * f, s_ico * f
        lt = simpleSplit((a.get("titulo") or "").upper(), "P-Banda", st, ancho)[:3]
        lm = simpleSplit(a.get("mensaje") or "", "P-Ante", sm, ancho)[:4]
        total = (s_ico + (6 if s_ico else 0)) + len(lt) * st * 1.05 + 6 + len(lm) * sm * 1.2 + 6 + sp * 1.1
    y = y0 + h / 2 + total / 2
    cx = x0 + w / 2
    if s_ico:
        c.drawImage(ico, cx - s_ico / 2, y - s_ico, s_ico, s_ico, mask="auto")
        y -= s_ico + 6
    c.setFillColor(colors.white)
    for ln in lt:
        y -= st
        c.setFont("P-Banda", st)
        c.drawCentredString(cx, y, ln)
        y -= st * 0.05
    y -= 6
    for ln in lm:
        y -= sm
        c.setFont("P-Ante", sm)
        c.drawCentredString(cx, y, ln)
        y -= sm * 0.2
    y -= 6
    c.setFillColor(acento)
    for ln in lp:
        y -= sp
        c.setFont("P-Banda", sp)
        c.drawCentredString(cx, y, ln)
    c.restoreState()
