"""Genera el PDF de cada noticia con ReportLab."""
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from .config import PDF_DIR

FONT, FONT_B = "Helvetica", "Helvetica-Bold"
_win = Path("C:/Windows/Fonts")
if (_win / "arial.ttf").exists():
    pdfmetrics.registerFont(TTFont("Arial", str(_win / "arial.ttf")))
    pdfmetrics.registerFont(TTFont("Arial-Bold", str(_win / "arialbd.ttf")))
    FONT, FONT_B = "Arial", "Arial-Bold"


def _limpio(t: str) -> str:
    """Quita emojis y símbolos que la fuente no tiene, y escapa para Paragraph."""
    t = "".join(ch for ch in (t or "") if ord(ch) < 0x2190 or unicodedata.category(ch).startswith(("L", "N", "P")))
    t = t.replace("\u034f", "")
    return escape(t).replace("\n", "<br/>")


def slugify(t: str, n: int = 50) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^\w\s-]", "", t).strip().lower()
    return re.sub(r"[\s_-]+", "-", t)[:n].strip("-") or "noticia"


def _fecha_legible(iso):
    if not iso:
        return "fecha no disponible"
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def generar_pdf(post: dict, ia: dict) -> Path:
    fecha_iso = post.get("publicado") or post.get("detectado")
    prefijo = (fecha_iso or datetime.now().isoformat())[:16].replace(":", "").replace("T", "_")
    destino = PDF_DIR / post["pagina"] / f"{prefijo}_{slugify(ia.get('TITULAR'))}.pdf"
    destino.parent.mkdir(parents=True, exist_ok=True)

    ss = getSampleStyleSheet()
    st = {
        "meta": ParagraphStyle("meta", parent=ss["Normal"], fontName=FONT, fontSize=9, textColor=colors.grey),
        "tit": ParagraphStyle("tit", parent=ss["Title"], fontName=FONT_B, fontSize=20, leading=24, alignment=0),
        "baj": ParagraphStyle("baj", parent=ss["Normal"], fontName=FONT, fontSize=12.5, leading=17,
                              textColor=colors.HexColor("#333333")),
        "h": ParagraphStyle("h", parent=ss["Heading3"], fontName=FONT_B, fontSize=11,
                            textColor=colors.HexColor("#1f4e79"), spaceBefore=10),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontName=FONT, fontSize=11, leading=15.5,
                               alignment=TA_JUSTIFY, spaceAfter=7),
        "link": ParagraphStyle("link", parent=ss["Normal"], fontName=FONT, fontSize=9.5, leading=13),
    }

    s = []
    s.append(Paragraph(f"Fuente: <b>{escape(post['pagina'])}</b> (Facebook) &nbsp;·&nbsp; "
                       f"Publicado: {_fecha_legible(post.get('publicado'))} &nbsp;·&nbsp; "
                       f"Detectado: {_fecha_legible(post.get('detectado'))}", st["meta"]))
    s.append(Spacer(1, 6))
    s.append(Paragraph(_limpio(ia.get("TITULAR")), st["tit"]))
    s.append(Paragraph(_limpio(ia.get("BAJADA")), st["baj"]))
    s.append(HRFlowable(width="100%", color=colors.HexColor("#1f4e79"), spaceBefore=8, spaceAfter=4))

    s.append(Paragraph("CUERPO", st["h"]))
    for par in re.split(r"\n\s*\n", ia.get("CUERPO") or ""):
        if par.strip():
            s.append(Paragraph(_limpio(par.strip()), st["body"]))

    datos = [d for d in ia.get("DATOS") or [] if str(d).strip()]
    s.append(Paragraph("DATOS O CIFRAS IMPORTANTES", st["h"]))
    if datos:
        s.append(ListFlowable([ListItem(Paragraph(_limpio(str(d)), st["body"])) for d in datos],
                              bulletType="bullet", leftIndent=14))
    else:
        s.append(Paragraph("No se identificaron cifras en la fuente.", st["body"]))

    s.append(Paragraph("LINK DE LA NOTICIA", st["h"]))
    links = ia.get("LINKS") or {}
    for etiqueta, url in (("Facebook", links.get("facebook")), ("Nota completa", links.get("externo"))):
        if url:
            u = escape(url)
            s.append(Paragraph(f"{etiqueta}: <link href='{u}' color='blue'>{u}</link>", st["link"]))
    if not any(links.values()):
        s.append(Paragraph("No disponible.", st["link"]))

    s.append(Spacer(1, 14))
    s.append(Paragraph(f"Redacción asistida por IA ({escape(ia.get('_proveedor', ''))}) a partir de la "
                       "publicación original. Verificar con la fuente antes de difundir.", st["meta"]))

    SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm,
                      title=ia.get("TITULAR", ""), author=post["pagina"]).build(s)
    return destino
