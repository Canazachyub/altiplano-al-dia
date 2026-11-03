"""PDF consolidado por ciclo: portada + índice (con páginas) + desarrollo por ámbito y tema."""
import io
import json
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, HRFlowable, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

from . import storage
from .config import CONSOLIDADOS_DIR
from .pdfgen import FONT, FONT_B, _limpio

AZUL = colors.HexColor("#16324F")
AZUL2 = colors.HexColor("#1F4E79")
CLARO = colors.HexColor("#EAF1FB")
ROJO = colors.HexColor("#B3261E")
AMBITO_COLOR = {"DISTRITAL": "#2E7D32", "LOCAL": "#00838F", "REGIONAL": "#1565C0",
                "NACIONAL": "#6A1B9A", "INTERNACIONAL": "#AD1457"}


def _st():
    base = dict(fontName=FONT, fontSize=10.5, leading=14.5)
    return {
        "body": ParagraphStyle("body", alignment=TA_JUSTIFY, spaceAfter=6, **base),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555")),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.5, leading=10.5),
        "cellb": ParagraphStyle("cellb", fontName=FONT_B, fontSize=8.5, leading=10.5),
        "tit": ParagraphStyle("tit", fontName=FONT_B, fontSize=16, leading=20, spaceAfter=4, textColor=colors.black),
        "baj": ParagraphStyle("baj", fontName=FONT, fontSize=11.5, leading=15.5, textColor=colors.HexColor("#333333"), spaceAfter=4),
        "h": ParagraphStyle("h", fontName=FONT_B, fontSize=9.5, leading=12, textColor=AZUL2, spaceBefore=8, spaceAfter=3),
        "tema": ParagraphStyle("tema", fontName=FONT_B, fontSize=12, leading=15, textColor=AZUL2, spaceBefore=10, spaceAfter=6),
        "tag": ParagraphStyle("tag", fontName=FONT_B, fontSize=8.5, leading=11, textColor=colors.white),
        "cov1": ParagraphStyle("cov1", fontName=FONT_B, fontSize=30, leading=36, alignment=TA_CENTER, textColor=AZUL),
        "cov2": ParagraphStyle("cov2", fontName=FONT, fontSize=14, leading=20, alignment=TA_CENTER, textColor=colors.HexColor("#333333")),
        "idx": ParagraphStyle("idx", fontName=FONT_B, fontSize=18, leading=22, textColor=AZUL, spaceAfter=8),
    }


def _fecha(iso, fmt="%d/%m/%Y %H:%M"):
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except (TypeError, ValueError):
        return iso or "—"


class _Doc(BaseDocTemplate):
    def __init__(self, target, numero, **kw):
        super().__init__(target, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                         topMargin=1.8 * cm, bottomMargin=1.8 * cm, **kw)
        self.numero = numero
        self.paginas = {}
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([PageTemplate("p", [frame], onPage=self._pie)])

    def _pie(self, canv, doc):
        if doc.page == 1:
            return
        canv.saveState()
        canv.setFont(FONT, 8)
        canv.setFillColor(colors.grey)
        canv.drawString(2 * cm, 1.1 * cm, f"Altiplano al Día · Boletín N° {self.numero}")
        canv.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Pág. {doc.page}")
        canv.restoreState()

    def afterFlowable(self, f):
        k = getattr(f, "_gkey", None)
        if k:
            self.paginas[k] = self.page


def _banda(texto, color, st):
    t = Table([[Paragraph(escape(texto), ParagraphStyle("b", parent=st["tag"], fontSize=13, leading=16))]],
              colWidths=["100%"])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(color)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _ordenar(grupos, cfg):
    amb = {a: i for i, a in enumerate(cfg["ambitos"])}
    tem = {t: i for i, t in enumerate(cfg["temas"])}
    return sorted(grupos, key=lambda g: (amb.get(g["ambito"], 99), tem.get(g["tema"], 99),
                                         "~" if not g["fecha_hecho"] else "",
                                         -datetime.fromisoformat(g["fecha_hecho"]).timestamp()
                                         if g["fecha_hecho"] else 0))


def _story(grupos, cfg, numero, paginas, st, ahora):
    s = []
    # ---------- portada
    fechas = [g["fecha_hecho"] for g in grupos if g["fecha_hecho"]]
    n_act = sum(1 for g in grupos if g["estado_pdf"] == "actualizacion")
    n_multi = sum(1 for g in grupos if storage.n_medios(g) > 1)
    s += [Spacer(1, 4.5 * cm), Paragraph("ALTIPLANO AL DÍA", st["cov1"]), Spacer(1, 6),
          Paragraph(f"Boletín consolidado N° {numero}", st["cov2"]), Spacer(1, 1.2 * cm),
          HRFlowable(width="60%", color=AZUL, thickness=1.5), Spacer(1, 0.8 * cm),
          Paragraph(f"Generado: {ahora:%d/%m/%Y %H:%M}", st["cov2"]),
          Paragraph(f"Noticias cubiertas: {_fecha(min(fechas)) if fechas else '—'} a "
                    f"{_fecha(max(fechas)) if fechas else '—'}", st["cov2"]),
          Spacer(1, 0.6 * cm),
          Paragraph(f"<b>{len(grupos)}</b> noticias · {len(grupos) - n_act} nuevas · {n_act} actualizaciones · "
                    f"{n_multi} combinadas de varios medios", st["cov2"]),
          Spacer(1, 1 * cm)]
    conteo = [[Paragraph("<b>Ámbito</b>", st["cell"]), Paragraph("<b>Noticias</b>", st["cell"])]]
    for a in cfg["ambitos"]:
        n = sum(1 for g in grupos if g["ambito"] == a)
        if n:
            conteo.append([Paragraph(a.title(), st["cell"]), Paragraph(str(n), st["cell"])])
    t = Table(conteo, colWidths=[6 * cm, 3 * cm], hAlign="CENTER")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), CLARO), ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey)]))
    s += [t, PageBreak()]

    # ---------- índice
    s.append(Paragraph("Índice", st["idx"]))
    filas = [[Paragraph(h, st["cellb"]) for h in ("N°", "Titular", "Medios fuente", "Pág.")]]
    estilos = [("BACKGROUND", (0, 0), (-1, 0), CLARO), ("VALIGN", (0, 0), (-1, -1), "TOP"),
               ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey)]
    amb_actual = tema_actual = None
    for i, g in enumerate(grupos, 1):
        if g["ambito"] != amb_actual:
            amb_actual, tema_actual = g["ambito"], None
            filas.append([Paragraph(f"<font color='white'><b>{escape(amb_actual or '—')}</b></font>", st["cell"]), "", "", ""])
            estilos += [("SPAN", (0, len(filas) - 1), (-1, len(filas) - 1)),
                        ("BACKGROUND", (0, len(filas) - 1), (-1, len(filas) - 1),
                         colors.HexColor(AMBITO_COLOR.get(amb_actual, "#555555")))]
        if g["tema"] != tema_actual:
            tema_actual = g["tema"]
            filas.append([Paragraph(f"<b>{escape(tema_actual or '—')}</b>", st["cell"]), "", "", ""])
            estilos.append(("SPAN", (0, len(filas) - 1), (-1, len(filas) - 1)))
        marca = "<font color='#B3261E'><b>[ACTUALIZACIÓN]</b></font> " if g["estado_pdf"] == "actualizacion" else ""
        multi = f" <font color='#1F4E79'><b>[{storage.n_medios(g)} medios]</b></font>" if storage.n_medios(g) > 1 else ""
        filas.append([
            Paragraph(str(i), st["cell"]),
            Paragraph(f"{marca}<a href='#g{g['id']}' color='black'>{_limpio(g['titular'])}</a>{multi}", st["cell"]),
            Paragraph(escape(g["medios"] or ""), st["cell"]),
            Paragraph(str(paginas.get(f"g{g['id']}", "—")), st["cell"]),
        ])
    idx = Table(filas, colWidths=[1 * cm, 10.2 * cm, 4.6 * cm, 1.2 * cm], repeatRows=1)
    idx.setStyle(TableStyle(estilos))
    s += [idx, Paragraph("[N medios] = nota combinada a partir de N medios distintos.", st["small"]), PageBreak()]

    # ---------- desarrollo
    amb_actual = tema_actual = None
    for g in grupos:
        nota = json.loads(g["nota_json"])
        if g["ambito"] != amb_actual:
            amb_actual, tema_actual = g["ambito"], None
            s += [CondPageBreak(6 * cm), _banda(amb_actual or "—", AMBITO_COLOR.get(amb_actual, "#555555"), st),
                  Spacer(1, 4)]
        if g["tema"] != tema_actual:
            tema_actual = g["tema"]
            s.append(Paragraph(escape(tema_actual or "—"), st["tema"]))

        cab = []
        etiquetas = []
        if g["estado_pdf"] == "actualizacion":
            etiquetas.append(("ACTUALIZACIÓN", ROJO))
        if storage.n_medios(g) > 1:
            etiquetas.append((f"COMBINADA · {storage.n_medios(g)} MEDIOS", AZUL2))
        if etiquetas:
            et = Table([[Paragraph(escape(t), st["tag"]) for t, _ in etiquetas]],
                       colWidths=[None] * len(etiquetas), hAlign="LEFT")
            et.setStyle(TableStyle([("BACKGROUND", (i, 0), (i, 0), c) for i, (_, c) in enumerate(etiquetas)]
                                   + [("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                                      ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            cab.append(et)
            cab.append(Spacer(1, 3))
        tit = Paragraph(f"<a name='g{g['id']}'/>{_limpio(nota.get('TITULAR') or g['titular'])}", st["tit"])
        tit._gkey = f"g{g['id']}"
        cab += [tit, Paragraph(_limpio(nota.get("BAJADA")), st["baj"]),
                Paragraph(f"{escape(g['ambito'] or '')} · {escape(g['tema'] or '')} · {escape(g['medios'] or '')} · "
                          f"{_fecha(g['fecha_hecho'])}", st["small"])]
        s.append(KeepTogether(cab))

        s.append(Paragraph("CUERPO", st["h"]))
        for par in (nota.get("CUERPO") or "").split("\n\n"):
            if par.strip():
                s.append(Paragraph(_limpio(par.strip()), st["body"]))

        datos = nota.get("DATOS") or []
        if datos:
            s.append(Paragraph("DATOS O CIFRAS IMPORTANTES", st["h"]))
            for d in datos:
                if isinstance(d, dict):
                    fu = ", ".join(d.get("fuentes") or [])
                    txt = f"• {_limpio(d.get('dato', ''))}" + (f" <font color='#777777'>({escape(fu)})</font>" if fu else "")
                else:
                    txt = f"• {_limpio(str(d))}"
                s.append(Paragraph(txt, st["body"]))

        disc = nota.get("DISCREPANCIAS") or []
        if disc:
            filas_d = [[Paragraph("<b>Datos que difieren entre medios</b>", st["cell"])]]
            for d in disc:
                vers = "; ".join(f"<b>{escape(v.get('medio', ''))}</b>: {_limpio(v.get('dice', ''))}"
                                 for v in d.get("versiones", []))
                filas_d.append([Paragraph(f"{_limpio(d.get('aspecto', ''))} — {vers}", st["cell"])])
            td = Table(filas_d, colWidths=["100%"])
            td.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF4E5")),
                                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E8A33D"))]))
            s += [Spacer(1, 4), td]

        s.append(Paragraph("FUENTES Y LINKS", st["h"]))
        aportes = {f.get("medio"): f.get("aporte") for f in nota.get("FUENTES") or []}
        for l in nota.get("LINKS") or []:
            partes = [f"<b>{escape(l.get('medio', ''))}</b> ({_fecha(l.get('fecha'))})"]
            if aportes.get(l.get("medio")) and len(nota.get("LINKS")) > 1:
                partes.append(_limpio(aportes[l["medio"]]))
            for etq in ("facebook", "externo"):
                if l.get(etq):
                    u = escape(l[etq])
                    partes.append(f"<link href='{u}' color='blue'>{'Facebook' if etq == 'facebook' else 'Nota completa'}</link>")
            s.append(Paragraph(" · ".join(partes), st["small"]))
        s += [Spacer(1, 6), HRFlowable(width="100%", color=colors.lightgrey), Spacer(1, 6)]
    return s


def generar_consolidado(cfg: dict):
    """Genera el boletín con los grupos no publicados (nuevos o actualizados). None si no hay."""
    grupos = _ordenar(storage.grupos_para_pdf(), cfg)
    if not grupos:
        return None
    numero = storage.siguiente_consolidado()
    ahora = datetime.now()
    st = _st()
    # pasada 1: medir en qué página cae cada noticia
    d1 = _Doc(io.BytesIO(), numero)
    d1.build(_story(grupos, cfg, numero, {}, st, ahora))
    # pasada 2: índice con números de página
    CONSOLIDADOS_DIR.mkdir(parents=True, exist_ok=True)
    destino = CONSOLIDADOS_DIR / f"{ahora:%Y-%m-%d_%H%M}.pdf"
    if destino.exists():
        destino = CONSOLIDADOS_DIR / f"{ahora:%Y-%m-%d_%H%M%S}.pdf"
    d2 = _Doc(str(destino), numero, title=f"Altiplano al Día — Boletín {numero}", author="Altiplano al Día")
    d2.build(_story(grupos, cfg, numero, d1.paginas, st, ahora))
    fechas = [g["fecha_hecho"] for g in grupos if g["fecha_hecho"]]
    storage.registrar_consolidado(destino, min(fechas) if fechas else None,
                                  max(fechas) if fechas else None, [g["id"] for g in grupos])
    return destino
