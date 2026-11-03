"""Boletín consolidado por ciclo, maquetado como diario impreso.

Cada página tiene la banda de cabecera (logo, sección, fecha y número de página) y las noticias
repartidas en una grilla de 4 columnas: una zona principal de 3 columnas con las notas grandes y
una columna lateral con las notas cortas. La portada lleva una barra lateral con el logo, la
sección en vertical y el índice "En esta edición". Al final va una ficha de verificación interna
(fuentes, enlaces y datos que difieren entre medios), que no forma parte de lo publicable.
"""
import io
import json
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Frame, Paragraph, Spacer, Table, TableStyle

from . import avisos, diseno, storage
from .config import ASSETS_DIR, BASE_DIR, CONSOLIDADOS_DIR

# ------------------------------------------------------------------ identidad visual
AZUL = colors.HexColor("#043F9B")       # azul del logo
AZUL_CLARO = colors.HexColor("#6E9BE0")
AMARILLO = colors.HexColor("#FFEE00")   # amarillo del logo
NEGRO = colors.HexColor("#1A1A1A")
GRIS = colors.HexColor("#555555")
FILETE = colors.HexColor("#B8B8B8")
ROJO = colors.HexColor("#C62828")

W, H = A4
M = 20                       # margen lateral
NCOL, GUT = 4, 10            # grilla de la página
CW = (W - 2 * M - (NCOL - 1) * GUT) / NCOL
BAND_H = 24
TOP = H - 16                 # borde superior útil
BOTTOM = 26                  # deja sitio al pie
GAP = 16                     # separación entre notas apiladas (con filete al medio)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


D = diseno.cargar()
MAQUETA_PATH = BASE_DIR / "data" / "maqueta.json"


def _esc():
    return float(D["tamanos"].get("titulares", 100)) / 100


def leer_fijas() -> dict:
    try:
        import json as _j
        return {int(k): int(v) for k, v in _j.loads(MAQUETA_PATH.read_text(encoding="utf-8")).get("fijas", {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def guardar_fijas(fijas: dict) -> None:
    MAQUETA_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAQUETA_PATH.write_text(json.dumps({"fijas": {str(k): v for k, v in fijas.items()}}, indent=2), encoding="utf-8")


def _aplicar_diseno():
    """Carga data/diseno.json y aplica colores, letras y textos a toda la maqueta."""
    global D, AZUL, AZUL_CLARO, AMARILLO, NEGRO, ROJO, FIN, CUADRITO
    D = diseno.cargar()
    c = D["colores"]
    AZUL, AZUL_CLARO = colors.HexColor(c["principal"]), colors.HexColor(c["degradado"])
    AMARILLO, NEGRO, ROJO = colors.HexColor(c["acento"]), colors.HexColor(c["texto"]), colors.HexColor(c["alerta"])
    FIN = f'<font name="ZapfDingbats" size="6" color="{c["principal"]}"> n</font>'
    CUADRITO = f'<font name="ZapfDingbats" size="6" color="{c["principal"]}">n</font> '
    from . import portada
    portada.D = D
    diseno.aplicar_fuentes(D)


def _fecha_larga(d: datetime) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def _fecha(iso, fmt="%d/%m/%Y %H:%M"):
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except (TypeError, ValueError):
        return iso or "—"


# ------------------------------------------------------------------ fuentes
def _fuentes():
    diseno.aplicar_fuentes(D)
    return True


def _fuentes_viejo():  # (referencia: nombres de rol que usa la maqueta)
    d = ASSETS_DIR / "fonts"
    for nombre, archivo in (("Slab", "ZillaSlab-Bold.ttf"), ("Slab-Semi", "ZillaSlab-SemiBold.ttf"),
                            ("Serif", "PT_Serif-Web-Regular.ttf"), ("Serif-B", "PT_Serif-Web-Bold.ttf"),
                            ("Serif-I", "PT_Serif-Web-Italic.ttf"),
                            ("Narrow", "PT_Sans-Narrow-Web-Regular.ttf"),
                            ("Narrow-B", "PT_Sans-Narrow-Web-Bold.ttf"),
                            ("Heavy", "AlfaSlabOne-Regular.ttf"),
                            ("FC-M", "FiraSansCondensed-Medium.ttf"), ("FC-SB", "FiraSansCondensed-SemiBold.ttf"),
                            ("FC-B", "FiraSansCondensed-Bold.ttf"), ("FC-XB", "FiraSansCondensed-ExtraBold.ttf"),
                            ("FC-XBI", "FiraSansCondensed-ExtraBoldItalic.ttf")):
        pdfmetrics.registerFont(TTFont(nombre, str(d / archivo)))
    pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-B", italic="Serif-I", boldItalic="Serif-B")
    pdfmetrics.registerFontFamily("Narrow", normal="Narrow", bold="Narrow-B", italic="Narrow", boldItalic="Narrow-B")
    return True


def _st():
    _fuentes()
    t = D["tamanos"]
    tx, an, pf = float(t["texto"]), float(t["antetitulo"]), float(t["pie_foto"])
    return {
        "body": ParagraphStyle("body", fontName="Serif", fontSize=tx, leading=tx * 1.22, alignment=TA_JUSTIFY,
                               hyphenationLang="es", embeddedHyphenation=1, uriWasteReduce=0.3, textColor=NEGRO),
        "inter": ParagraphStyle("inter", fontName="Serif-B", fontSize=tx, leading=tx * 1.22, spaceBefore=3,
                                textColor=NEGRO),
        "kicker": ParagraphStyle("kicker", fontName="FC-B", fontSize=an, leading=an * 1.14, textColor=NEGRO),
        "kicker_s": ParagraphStyle("kicker_s", fontName="FC-B", fontSize=an * 0.84, leading=an * 0.96, textColor=NEGRO),
        "caption": ParagraphStyle("caption", fontName="Narrow", fontSize=pf, leading=pf * 1.15, textColor=colors.HexColor("#333333")),
        "byline": ParagraphStyle("byline", fontName="Serif-I", fontSize=7.5, leading=9, alignment=TA_RIGHT, textColor=NEGRO),
        "idx": ParagraphStyle("idx", fontName="Narrow", fontSize=8, leading=9.4, textColor=NEGRO),
        "idx_h": ParagraphStyle("idx_h", fontName="Narrow-B", fontSize=8, leading=10, textColor=AZUL),
        "credito": ParagraphStyle("credito", fontName="Narrow", fontSize=7, leading=8.4, alignment=TA_CENTER, textColor=GRIS),
        "box_h": ParagraphStyle("box_h", fontName="Narrow-B", fontSize=9, leading=11, textColor=colors.white),
        "box": ParagraphStyle("box", fontName="Narrow", fontSize=8.4, leading=10, textColor=NEGRO),
        "v_h": ParagraphStyle("v_h", fontName="Slab", fontSize=15, leading=18, textColor=AZUL, spaceAfter=4),
        "v": ParagraphStyle("v", fontName="Narrow", fontSize=8.5, leading=10.2, textColor=NEGRO),
        "v_b": ParagraphStyle("v_b", fontName="Narrow-B", fontSize=9.5, leading=11.5, textColor=NEGRO, spaceBefore=6),
    }


# ------------------------------------------------------------------ texto
def _plano(t: str) -> str:
    """Sin emojis ni símbolos que las fuentes no tienen (texto sin marcado)."""
    t = "".join(ch for ch in (t or "") if ord(ch) < 0x2190 or unicodedata.category(ch).startswith(("L", "N", "P")))
    return re.sub(r"\s+", " ", t.replace("͏", "")).strip()


def _x(t: str) -> str:
    return escape(_plano(t))


def _slug(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]+", "-", t).strip("-")


def _icono(tipo: str, nombre: str, blanco: bool = False) -> str | None:
    """Ruta del icono (generado con ChatGPT) de un ámbito o tema; temas nuevos usan 'otros'."""
    d = ASSETS_DIR / "icons"
    suf = "_w" if blanco else ""
    slug = _slug(nombre).split("-")[0] if tipo == "tema" and _slug(nombre).startswith("seguridad") else _slug(nombre)
    for cand in (f"{tipo}_{slug}{suf}.png", f"{tipo}_otros{suf}.png"):
        if (d / cand).exists():
            return str(d / cand)
    return None


def _img_tag(ruta, size, valign=-2):
    return f'<img src="{ruta}" width="{size}" height="{size}" valign="{valign}"/> ' if ruta else ""


FIN = '<font name="ZapfDingbats" size="6" color="#043F9B"> n</font>'   # ■ de cierre
CUADRITO = '<font name="ZapfDingbats" size="6" color="#043F9B">n</font> '


# ------------------------------------------------------------------ artículos
def _articulo(g, cfg) -> dict:
    nota = json.loads(g["nota_json"])
    posts = storage.posts_de_grupo(g["id"])
    img = None
    candidatas = [] if nota.get("_SIN_FOTO") else (
        [nota.get("_IMAGEN")] + [p["imagen"] if "imagen" in p.keys() else None for p in posts])
    for ruta in candidatas:   # _IMAGEN = foto elegida a mano en el editor
        if ruta:
            try:
                with Image.open(ruta) as im:
                    im.verify()
                img = ruta
                break
            except Exception:  # noqa: BLE001
                continue
    parrafos = [x.strip() for x in (nota.get("CUERPO") or "").split("\n\n") if x.strip()]
    ante = _plano(nota.get("ANTETITULO") or "")
    if not ante:
        baj = _plano(nota.get("BAJADA") or "")
        ante = baj if 0 < len(baj.split()) <= 12 else (g["tema"] or "")
    return {
        "gid": g["id"], "ambito": g["ambito"] or "REGIONAL", "tema": g["tema"] or "",
        "ante": ante.upper().rstrip("."), "titular": _plano(nota.get("TITULAR") or g["titular"] or ""),
        "lugar": _plano(nota.get("LUGAR") or "").rstrip("."), "pie": _plano(nota.get("PIE_FOTO") or ""),
        "parrafos": parrafos, "img": img, "act": g["estado_pdf"] == "actualizacion",
        "palabras": sum(len(x.split()) for x in parrafos), "n_medios": storage.n_medios(g),
        "nota": nota, "grupo": g,
    }


def _parrafos(art, st):
    out = []
    primero = True
    for i, par in enumerate(art["parrafos"]):
        if par.startswith("## "):
            out.append(Paragraph(_x(par[3:]).upper(), st["inter"]))
            continue
        txt = _x(par)
        if primero and art["lugar"]:
            txt = f"<b>{_x(art['lugar'])}.</b> {txt}"
        primero = False
        if i == len(art["parrafos"]) - 1:
            txt += FIN
        out.append(Paragraph(txt, st["body"]))
    if not out:
        out.append(Paragraph(_x(art["titular"]) + FIN, st["body"]))
    return out


def _fluir(paras, rects):
    """Reparte los párrafos en los rectángulos (x, y_desde_arriba, ancho, alto) en orden.
    Devuelve (colocados, sobrantes). colocados = [(flowable, x, y, alto)]."""
    puestos, cola = [], list(paras)
    for (x, y0, w, h) in rects:
        y, libre = y0, h
        while cola:
            p = cola[0]
            _, ph = p.wrap(w, libre)
            sa = 2 if p.style.name == "body" else 0
            sb = p.style.spaceBefore if y > y0 else 0
            if ph + sb <= libre + 0.01:
                # un intertítulo no queda solo al pie de una columna
                if p.style.name == "inter" and libre - ph - sb < 3 * 11 and len(cola) > 1:
                    break
                puestos.append((p, x, y + sb, ph))
                y += ph + sb + sa
                libre -= ph + sb + sa
                cola.pop(0)
                continue
            partes = p.split(w, libre - sb) if p.style.name == "body" else []
            if len(partes) == 2:
                _, h0 = partes[0].wrap(w, libre - sb)
                puestos.append((partes[0], x, y + sb, h0))
                cola[0] = partes[1]
            break
    return puestos, cola


def _titular_lineas(texto, ancho, tipo):
    """Elige tamaño y cortes del titular. Devuelve (tamaño, [líneas])."""
    if tipo == "lead":
        palabras = texto.split()
        if len(palabras) >= 4:
            # dos líneas equilibradas, la más larga llena el ancho (estilo portada)
            mejor = None
            for k in range(1, len(palabras)):
                a, b = " ".join(palabras[:k]), " ".join(palabras[k:])
                wa, wb = (pdfmetrics.stringWidth(a, "Slab", 10), pdfmetrics.stringWidth(b, "Slab", 10))
                if mejor is None or max(wa, wb) < mejor[0]:
                    mejor = (max(wa, wb), [a, b])
            size = min(42 * _esc(), ancho / mejor[0] * 10 * 0.99)
            if size >= 24 * _esc():
                return size, mejor[1]
        for size in range(int(34 * _esc()), int(19 * _esc()), -1):
            lineas = simpleSplit(texto, "Slab", size, ancho)
            if len(lineas) <= 3:
                return size, lineas
        return int(20 * _esc()), simpleSplit(texto, "Slab", int(20 * _esc()), ancho)
    rango, maxl = ((int(26 * _esc()), int(15 * _esc())), 3) if tipo == "sec" else \
        ((int(19 * _esc()), int(12 * _esc())), 5)
    for size in range(rango[0], rango[1] - 1, -1):
        lineas = simpleSplit(texto, "Slab", size, ancho)
        if len(lineas) <= (2 if size > rango[0] - 4 else maxl):
            return size, lineas
    return rango[1], simpleSplit(texto, "Slab", rango[1], ancho)


@lru_cache(maxsize=256)
def _recorte(ruta, w, h):
    """Foto recortada al centro con la proporción del hueco."""
    im = Image.open(ruta).convert("RGB")
    r = w / h
    if im.width / im.height > r:
        nw = int(im.height * r)
        im = im.crop(((im.width - nw) // 2, 0, (im.width - nw) // 2 + nw, im.height))
    else:
        nh = int(im.width / r)
        top = max(0, int((im.height - nh) * 0.35))          # sesgo hacia arriba (caras)
        im = im.crop((0, top, im.width, top + nh))
    im.thumbnail((int(w * 3), int(h * 3)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    buf.seek(0)
    return ImageReader(buf)


def _layout(art, ancho, ncols, tipo, st, alto_cuerpo=None, foto_extra=0.0, seccion=None):
    """Arma una nota en un bloque de 'ancho' con 'ncols' columnas de texto.
    Devuelve (alto_total, ops) donde ops dibujan relativo a la esquina superior izquierda.
    Si alto_cuerpo es None, busca el mínimo alto en que entra todo el texto."""
    ops = []
    cw = (ancho - (ncols - 1) * GUT) / ncols
    d = 0.0
    # antetítulo
    ks = st["kicker"] if tipo != "side" else st["kicker_s"]
    marca = f'<font color="{D["colores"]["alerta"]}">ACTUALIZACIÓN · </font>' if art["act"] else ""
    if seccion and art["ambito"] != seccion:
        marca += f'<font color="{D["colores"]["principal"]}">{_x(art["ambito"])} · </font>'
    if art["ante"] or marca:
        from .portada import color_hex
        ico = _img_tag(_icono("tema", art["tema"]), ks.fontSize + 1.5, -2.5)
        k = Paragraph(ico + marca + f'<font color="{color_hex(art["tema"])}">{_x(art["ante"])}</font>', ks)
        _, hk = k.wrap(ancho, 200)
        ops.append(("flow", k, 0, d, hk))
        d += hk + 2
    # titular
    size, lineas = _titular_lineas(art["titular"], ancho, tipo)
    lead = size * (0.98 if tipo == "lead" else 1.02)
    for i, ln in enumerate(lineas):
        color = AZUL if (tipo == "lead" and len(lineas) >= 2 and i == 0) else NEGRO
        ops.append(("text", ln, 0, d + size * 0.82, size, color))
        d += lead
    d += 5 if tipo != "side" else 4
    # foto
    tiene_foto = bool(art["img"])
    if ncols >= 3:
        fx, fw = cw + GUT, ancho - cw - GUT
    else:
        fx, fw = 0, ancho
    fh = 0.0
    cap_ops, cap_h = [], 0.0
    if tiene_foto:
        base = fw * (0.58 if ncols >= 2 else 0.68)
        fh = min(base + foto_extra, fw * (0.80 if ncols >= 2 else 1.0))
        if art["pie"]:
            cap = Paragraph(CUADRITO + _x(art["pie"]), st["caption"])
            _, cap_h = cap.wrap(fw, 100)
            cap_ops.append(("flow", cap, fx, d + fh + 3, cap_h))
            cap_h += 5
        else:
            cap_h = 4
    # firma (primera columna) en notas grandes
    firma_h = 0.0
    firma = None
    if tipo in ("lead", "sec"):
        firma = Paragraph(f"<b>{_x(diseno.texto(D, 'firma_1'))}</b><br/>{_x(diseno.texto(D, 'firma_2'))}",
                          st["byline"])
        _, firma_h = firma.wrap(cw, 50)
        firma_h += 6

    def rects(hb):
        rs = []
        for c in range(ncols):
            x = c * (cw + GUT)
            top = d
            if c == 0 and firma is not None:
                top += firma_h
            if tiene_foto and x >= fx - 0.1:
                top = d + fh + cap_h
            rs.append((x, top, cw, d + hb - top))
        return rs

    def entra(hb):
        _, sobra = _fluir(_parrafos(art, st), rects(hb))
        return not sobra

    minimo = (fh + cap_h + 2 * 11) if tiene_foto else 11
    if alto_cuerpo is None:
        lo, hi = minimo, 3000.0
        if not entra(hi):
            hi = 6000.0
        while hi - lo > 1:
            mid = (lo + hi) / 2
            if entra(mid):
                hi = mid
            else:
                lo = mid
        hb = hi
    else:
        hb = max(alto_cuerpo, minimo)
    puestos, _ = _fluir(_parrafos(art, st), rects(hb))
    if firma is not None:
        ops.append(("flow", firma, 0, d, firma_h - 6))
    if tiene_foto:
        ops.append(("img", art["img"], fx, d, fw, fh))
        ops += cap_ops
    for p, x, y, h in puestos:
        ops.append(("flow", p, x, y, h))
    ops.append(("anchor", art["gid"], 0, 0))
    return d + hb, ops


def _medir(art, ancho, ncols, tipo, st, alto_max=None, seccion=None):
    """Alto natural; si queda espacio hasta alto_max, agranda la foto para ocuparlo."""
    h, ops = _layout(art, ancho, ncols, tipo, st, seccion=seccion)
    if alto_max and art["img"] and alto_max - h > 30:
        lo, hi = 0.0, (alto_max - h) * 2.5
        mejor = (h, ops)
        for _ in range(12):
            mid = (lo + hi) / 2
            h2, ops2 = _layout(art, ancho, ncols, tipo, st, foto_extra=mid, seccion=seccion)
            if h2 <= alto_max:
                lo, mejor = mid, (h2, ops2)
            else:
                hi = mid
        return mejor
    return h, ops


def _dibujar(c, ops, x0, ytop, anclas):
    for op in ops:
        kind = op[0]
        if kind == "flow":
            _, f, x, y, h = op
            f.drawOn(c, x0 + x, ytop - y - h)
        elif kind == "text":
            _, t, x, y, size, color = op
            c.setFont("Slab", size)
            c.setFillColor(color)
            c.drawString(x0 + x, ytop - y, t)
        elif kind == "img":
            _, ruta, x, y, w, h = op
            c.drawImage(_recorte(ruta, int(w), int(h)), x0 + x, ytop - y - h, w, h)
        elif kind == "anchor":
            key = f"g{op[1]}"
            c.bookmarkPage(key, fit="XYZ", left=x0, top=ytop + 10)
            anclas.add(key)


# ------------------------------------------------------------------ caja de datos (relleno)
def _caja_datos(arts, ancho, alto_max, st):
    datos = []
    for a in arts:
        for d in a["nota"].get("DATOS") or []:
            t = d.get("dato") if isinstance(d, dict) else str(d)
            if t and t.strip():
                datos.append(_x(t))
    if not datos:
        return None
    filas = [[Paragraph(_x(diseno.texto(D, "datos")), st["box_h"])]]
    for t in datos:
        filas.append([Paragraph(CUADRITO + t, st["box"])])
    while len(filas) > 2:
        tb = Table(filas, colWidths=[ancho])
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), AZUL),
                                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#EEF3FB")),
                                ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.white),
                                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
        _, h = tb.wrap(ancho, alto_max)
        if h <= alto_max:
            return tb, h
        filas.pop()
    return None


# ------------------------------------------------------------------ paginación
class _Pagina:
    def __init__(self, n, portada=False):
        self.n = n
        self.portada = portada
        self.lateral_izq = portada or n % 2 == 0
        self.top = TOP if portada else TOP - BAND_H - 12
        self.alto = self.top - BOTTOM
        if self.lateral_izq:
            self.x_lat, self.x_main = M, M + CW + GUT
        else:
            self.x_main, self.x_lat = M, M + 3 * (CW + GUT)
        self.w_main = 3 * CW + 2 * GUT
        self.main, self.lat = [], []      # [(art, alto, ops)]
        self.full = None                   # nota a 4 columnas que ocupa toda la página

    def usado(self, pila):
        return sum(h for _, h, _ in pila) + GAP * max(0, len(pila) - 1)

    def libre_main(self):
        return self.alto - self.usado(self.main) - (GAP if self.main else 0)

    def libre_lat(self):
        return self.alto - self.usado(self.lat) - (GAP if self.lat else 0)

    def seccion(self):
        if self.full:
            return self.full[0]["ambito"]
        return self.main[0][0]["ambito"] if self.main else "NOTICIAS"


def _paginar(arts, st, fijas=None):
    """fijas: {gid: página} elegidas en la vista previa ("Mover a la página", "Añadir aquí")."""
    fijas = fijas or {}
    paginas = []
    cola = list(arts)
    while cola:
        n = len(paginas) + 2                      # la 1 es la portada
        pg = _Pagina(n, portada=not paginas)
        paginas.append(pg)
        # las fijadas a esta página van primero; las fijadas a páginas siguientes esperan su turno
        propias = [x for x in cola if fijas.get(x["gid"]) == n]
        luego = [x for x in cola if fijas.get(x["gid"], 0) > n]
        resto = [x for x in cola if x not in propias and x not in luego]
        cola = propias + resto + luego
        if not propias and not resto:             # solo quedan fijadas a páginas más adelante
            cola = luego
        # nota principal de la página
        a = cola.pop(0)
        h, ops = _layout(a, pg.w_main, 3, "lead", st)
        if h <= pg.alto:
            pg.main.append((a, h, ops))
        elif not pg.portada:
            h, ops = _layout(a, W - 2 * M, 4, "lead", st)
            pg.full = (a, h, ops)
            continue
        else:
            pg.main.append((a, h, ops))   # no debería pasar: notas muy largas
            continue
        # resto: corta -> columna lateral; larga -> zona principal
        while True:
            puesto = False
            for i, b in enumerate(cola[:8]):
                if fijas.get(b["gid"], 0) > n:      # fijada a otra página más adelante
                    continue
                opciones = []
                if not pg.portada:
                    hs, ops_s = _layout(b, CW, 1, "side", st, seccion=a["ambito"])
                    if hs <= pg.libre_lat():
                        opciones.append(("lat", hs, ops_s))
                hm, ops_m = _layout(b, pg.w_main, 3, "sec", st, seccion=a["ambito"])
                if hm <= pg.libre_main():
                    opciones.append(("main", hm, ops_m))
                if not opciones:
                    continue
                corta = b["palabras"] < 170 or not b["img"]
                opciones.sort(key=lambda o: 0 if (o[0] == "lat") == corta else 1)
                dest, hh, oo = opciones[0]
                (pg.lat if dest == "lat" else pg.main).append((b, hh, oo))
                cola.pop(i)
                puesto = True
                break
            if not puesto:
                break
    # aprovechar el espacio sobrante: agrandar la foto de la última nota de cada pila
    for pg in paginas:
        for pila, ancho, ncols, libre in ((pg.main, pg.w_main, 3, pg.libre_main), (pg.lat, CW, 1, pg.libre_lat)):
            if pila:
                a, h, ops = pila[-1]
                extra = libre() + GAP
                if extra > 30 and a["img"]:
                    tipo = "side" if ncols == 1 else ("lead" if len(pila) == 1 else "sec")
                    h2, ops2 = _medir(a, ancho, ncols, tipo, st, h + extra, seccion=pg.seccion())
                    pila[-1] = (a, h2, ops2)
    return paginas


# ------------------------------------------------------------------ cabecera, portada y pie
def _logo():
    return ImageReader(str(ASSETS_DIR / "logo.png"))


def _logo_blanco():
    return ImageReader(str(ASSETS_DIR / "logo_blanco.png"))


def _cabecera(c, pg, fecha_txt):
    y = TOP - BAND_H
    logo_w = BAND_H * 1.15 * 999 / 297
    num_w = 26
    secc = pg.seccion()
    impar = pg.n % 2 == 1
    if impar:
        lx, bx0, bx1, nx = M, M + logo_w + 6, W - M - num_w - 3, W - M - num_w
    else:
        nx, bx0, bx1, lx = M, M + num_w + 3, W - M - logo_w - 6, W - M - logo_w
    c.saveState()
    # banda con degradado
    p = c.beginPath()
    p.roundRect(bx0, y, bx1 - bx0, BAND_H, 3)
    c.clipPath(p, stroke=0, fill=0)
    a, b = (AZUL, AZUL_CLARO) if impar else (AZUL_CLARO, AZUL)
    c.linearGradient(bx0, y, bx1, y, (a, b), extend=False)
    c.restoreState()
    # sección
    c.setFillColor(colors.white)
    c.setFont("Slab", 15)
    if impar:
        c.setFillColor(AMARILLO)
        c.drawString(bx0 + 8, y + 7, "/")
        c.setFillColor(colors.white)
        ico = _icono("ambito", secc, blanco=True)
        dx = 0
        if ico:
            c.drawImage(ico, bx0 + 17, y + 3, 18, 18, mask="auto")
            dx = 22
        c.drawString(bx0 + 18 + dx, y + 7, secc)
        c.setFont("Narrow-B", 9)
        c.drawRightString(bx1 - 8, y + 13, diseno.texto(D, "ciudad"))
        c.setFont("Narrow", 8.5)
        c.drawRightString(bx1 - 8, y + 4, fecha_txt)
    else:
        tw = pdfmetrics.stringWidth(secc, "Slab", 15)
        c.drawString(bx1 - 18 - tw, y + 7, secc)
        ico = _icono("ambito", secc, blanco=True)
        if ico:
            c.drawImage(ico, bx1 - 18 - tw - 22, y + 3, 18, 18, mask="auto")
        c.setFillColor(AMARILLO)
        c.drawString(bx1 - 12, y + 7, "/")
        c.setFillColor(colors.white)
        c.setFont("Narrow-B", 9)
        c.drawString(bx0 + 8, y + 13, diseno.texto(D, "ciudad"))
        c.setFont("Narrow", 8.5)
        c.drawString(bx0 + 8, y + 4, fecha_txt)
    # número de página
    c.setFillColor(AMARILLO)
    c.roundRect(nx, y, num_w, BAND_H, 3, stroke=0, fill=1)
    c.setFillColor(AZUL)
    c.setFont("Slab", 15)
    c.drawCentredString(nx + num_w / 2, y + 7, str(pg.n))
    # logo
    c.drawImage(_logo(), lx, y + 1, logo_w, BAND_H * 1.15 - 4, mask="auto", preserveAspectRatio=True)


def _pie(c, numero):
    c.setStrokeColor(FILETE)
    c.setLineWidth(0.5)
    c.line(M, BOTTOM - 8, W - M, BOTTOM - 8)
    c.setFont("Narrow", 7)
    c.setFillColor(GRIS)
    ahora = datetime.now()
    v = {"numero": numero, "fecha": f"{ahora:%d/%m/%Y}", "hora": f"{ahora:%H:%M}"}
    c.drawString(M, BOTTOM - 17, diseno.texto(D, "pie_izq", **v))
    c.drawRightString(W - M, BOTTOM - 17, diseno.texto(D, "pie_der", **v))


def _portada_lateral(c, pg, arts, paginas_de, numero, ahora, st, anclas_links):
    x, w = pg.x_lat, CW
    y = TOP
    # logo
    lh = w * 297 / 999
    c.drawImage(_logo(), x, y - lh, w, lh, mask="auto")
    y -= lh + 10
    c.setFillColor(NEGRO)
    c.setFont("Narrow-B", 10.5)
    c.drawCentredString(x + w / 2, y - 8, diseno.texto(D, "ciudad"))
    c.setFont("Narrow", 9.5)
    c.drawCentredString(x + w / 2, y - 20, _fecha_larga(ahora))
    c.setFont("Narrow-B", 8.5)
    c.setFillColor(AZUL)
    c.drawCentredString(x + w / 2, y - 32, f"BOLETÍN N° {numero}  ·  {ahora:%H:%M} h")
    y -= 42

    # índice
    def indice(size):
        s = ParagraphStyle("i", parent=st["idx"], fontSize=size, leading=size * 1.18)
        filas, alto = [], 0.0
        actual = None
        for a in arts:
            if a["ambito"] != actual:
                actual = a["ambito"]
                hp = Paragraph(_img_tag(_icono("ambito", actual), size + 3, -3) + _x(actual),
                               ParagraphStyle("ih", parent=st["idx_h"], fontSize=size, leading=size + 4))
                _, hh = hp.wrap(w, 100)
                filas.append((hp, hh + 3, None))
                alto += hh + 3
            act = f'<font color="{D["colores"]["alerta"]}"><b>ACT.</b></font> ' if a["act"] else ""
            pgn = paginas_de.get(a["gid"], "")
            p = Paragraph(f'<font name="Narrow-B" color="{D["colores"]["principal"]}">{pgn}</font>  '
                          f'{act}{_x(a["titular"])}', s)
            _, hp_ = p.wrap(w, 200)
            filas.append((p, hp_ + 2.5, a["gid"]))
            alto += hp_ + 2.5
        return filas, alto

    cred = Paragraph(
        f"{_x(diseno.texto(D, 'credito', numero=numero))} "
        f"Generado el {ahora:%d/%m/%Y} a las {ahora:%H:%M}. {len(arts)} noticias "
        f"({sum(1 for a in arts if a['act'])} actualizaciones).", st["credito"])
    _, ch = cred.wrap(w, 100)
    panel_min = 150
    disponible = y - BOTTOM - ch - 20 - 34 - panel_min
    filas, alto = indice(8)
    for size in (7.5, 7, 6.5):
        if alto <= disponible:
            break
        filas, alto = indice(size)
    while alto > disponible and len(filas) > 2:   # recorta y avisa
        f = filas.pop()
        alto -= f[1]
    panel_h = max(panel_min, y - BOTTOM - ch - 20 - 34 - alto)

    # panel azul con la sección en vertical
    c.setFillColor(AZUL)
    c.rect(x, y - panel_h, w, panel_h, stroke=0, fill=1)
    ilus = ASSETS_DIR / "altiplano_ilustracion.jpg"
    if ilus.exists():
        c.drawImage(_recorte(str(ilus), int(w), int(panel_h)), x, y - panel_h, w, panel_h)
        c.saveState()
        c.setFillColor(AZUL, alpha=0.55)
        c.rect(x, y - panel_h, w, panel_h, stroke=0, fill=1)
        c.restoreState()
    secc = (arts[0]["ambito"] if arts else "NOTICIAS")
    size = min(64, (panel_h - 50) / max(1, pdfmetrics.stringWidth(secc, "Slab", 10)) * 10, w * 0.62)
    c.saveState()
    c.translate(x + w / 2 + size * 0.36, y - panel_h + 40)
    c.rotate(90)
    c.setFillColor(colors.white)
    c.setFont("Slab", size)
    c.drawString(0, 0, secc)
    c.restoreState()
    c.setFillColor(AMARILLO)
    c.rect(x, y - panel_h, 26, 24, stroke=0, fill=1)
    c.setFillColor(AZUL)
    c.setFont("Slab", 15)
    c.drawCentredString(x + 13, y - panel_h + 7, str(pg.n))
    y -= panel_h + 12

    # "En esta edición"
    c.setFillColor(AZUL)
    c.setFont("Slab", 12)
    c.drawString(x, y - 10, diseno.texto(D, "indice"))
    c.setStrokeColor(AMARILLO)
    c.setLineWidth(2.5)
    c.line(x, y - 14, x + w, y - 14)
    y -= 22
    for p, hh, gid in filas:
        _, ph = p.wrap(w, 200)
        p.drawOn(c, x, y - ph)
        if gid:
            c.linkRect("", f"g{gid}", (x, y - ph, x + w, y), relative=0, thickness=0)
        y -= hh
    cred.drawOn(c, x, BOTTOM + 2)


def _filete_v(c, x, y0, y1):
    c.setStrokeColor(FILETE)
    c.setLineWidth(0.6)
    c.line(x, y0, x, y1)


def _filete_h(c, x0, x1, y):
    c.setStrokeColor(FILETE)
    c.setLineWidth(0.6)
    c.line(x0, y, x1, y)


# ------------------------------------------------------------------ ficha interna
def _ficha(c, arts, paginas_de, numero, st):
    """Páginas finales de verificación (no publicables): fuentes, enlaces y discrepancias."""
    story = [Paragraph("Ficha de verificación", st["v_h"]),
             Paragraph("Uso interno del equipo editorial, no forma parte de lo publicable. Indica de qué medios "
                       "salió cada nota, sus enlaces y los datos en los que los medios no coinciden.", st["v"]),
             Spacer(1, 6)]
    for a in arts:
        nota, g = a["nota"], a["grupo"]
        story.append(Paragraph(f'Pág. {paginas_de.get(a["gid"], "—")} · {_x(a["titular"])}', st["v_b"]))
        story.append(Paragraph(f'{_x(a["ambito"])} · {_x(a["tema"])} · {_x(g["medios"] or "")} · '
                               f'{_fecha(g["fecha_hecho"])}', st["v"]))
        for l in nota.get("LINKS") or []:
            partes = [f"<b>{_x(l.get('medio', ''))}</b> ({_fecha(l.get('fecha'))})"]
            for etq in ("facebook", "externo"):
                if l.get(etq):
                    partes.append(f"<link href='{escape(l[etq])}' color='blue'>"
                                  f"{'Facebook' if etq == 'facebook' else 'Nota completa'}</link>")
            story.append(Paragraph(" · ".join(partes), st["v"]))
        for d in nota.get("DISCREPANCIAS") or []:
            vers = "; ".join(f"<b>{_x(v.get('medio', ''))}</b>: {_x(v.get('dice', ''))}" for v in d.get("versiones", []))
            story.append(Paragraph(f'<font color="{D["colores"]["alerta"]}"><b>Difieren:</b></font> {_x(d.get("aspecto", ""))} — {vers}',
                                   st["v"]))
    while story:
        c.showPage()
        _pie(c, numero)
        f = Frame(M, BOTTOM, W - 2 * M, TOP - BOTTOM, showBoundary=0)
        antes = len(story)
        f.addFromList(story, c)
        if len(story) == antes:   # algo no entra en una página entera
            story.pop(0)


# ------------------------------------------------------------------ orden y generación
def _ordenar(arts, cfg):
    amb = {a: i for i, a in enumerate(cfg["ambitos"])}

    def peso(a):
        return -(a["n_medios"] * 300 + (150 if a["img"] else 0) + min(a["palabras"], 400))

    return sorted(arts, key=lambda a: (amb.get(a["ambito"], 99), peso(a)))


def _hueco(pag, rect, clave=""):
    from . import portada
    if portada.ZONAS is not None:
        portada.ZONAS.append({"pag": pag, "gid": None, "rect": [round(v, 1) for v in rect], "lugar": "hueco",
                              "clave": clave})


def _zona_aviso(pag, rect, clave, aid):
    from . import portada
    if portada.ZONAS is not None:
        portada.ZONAS.append({"pag": pag, "gid": None, "rect": [round(v, 1) for v in rect], "lugar": "aviso",
                              "clave": clave, "aviso": aid})


def _zona(pag, a, rect):
    from . import portada
    if portada.ZONAS is not None:
        portada.ZONAS.append({"pag": pag, "gid": a["gid"], "rect": [round(v, 1) for v in rect], "lugar": None})


def _render(destino, arts, paginas, numero, ahora, st, cfg):
    paginas_de = {}
    for pg in paginas:
        for a, _, _ in pg.main + pg.lat + ([pg.full] if pg.full else []):
            paginas_de[a["gid"]] = pg.n
    c = Canvas(str(destino), pagesize=A4)
    c.setTitle(f"Altiplano al Día — Boletín N° {numero}")
    c.setAuthor("Altiplano al Día")
    fecha_txt = _fecha_larga(ahora)
    anclas, links = set(), []
    orden_indice = sorted(arts, key=lambda a: (paginas_de.get(a["gid"], 999)))
    from . import portada
    portada.dibujar(c, cfg, arts, paginas_de, numero, ahora, {
        "AZUL": AZUL, "AZUL_CLARO": AZUL_CLARO, "AMARILLO": AMARILLO, "NEGRO": NEGRO, "W": W, "H": H,
        "recorte": _recorte, "icono": _icono, "logo": _logo, "logo_blanco": _logo_blanco, "ROJO": ROJO, "fecha_larga": _fecha_larga, "plano": _plano})
    asignador = avisos.Asignador()
    for pg in paginas:
        c.showPage()
        if pg.portada:
            _portada_lateral(c, pg, orden_indice, paginas_de, numero, ahora, st, links)
            _filete_v(c, pg.x_main - GUT / 2, BOTTOM, TOP)
        else:
            _cabecera(c, pg, fecha_txt)
        _pie(c, numero)
        if pg.full:
            a, h, ops = pg.full
            _dibujar(c, ops, M, pg.top, anclas)
            _zona(pg.n, a, (M, pg.top - h, W - M, pg.top))
            c.addOutlineEntry(a["titular"][:80], f"g{a['gid']}", level=0)
            continue
        for pila, x, ancho in ((pg.main, pg.x_main, pg.w_main), (pg.lat, pg.x_lat, CW)):
            y = pg.top
            for i, (a, h, ops) in enumerate(pila):
                _dibujar(c, ops, x, y, anclas)
                _zona(pg.n, a, (x, y - h, x + ancho, y))
                c.addOutlineEntry(a["titular"][:80], f"g{a['gid']}", level=0)
                y -= h
                if i < len(pila) - 1:
                    _filete_h(c, x, x + ancho, y - GAP / 2)
                    y -= GAP
            # relleno con datos clave si sobra mucho
            libre = y - BOTTOM - (GAP if pila else 0)
            if pila and libre > 90:
                caja = _caja_datos([a for a, _, _ in pila], ancho, libre, st)
                if caja:
                    tb, th = caja
                    tb.drawOn(c, x, y - GAP - th)
                    from . import portada as _p
                    if _p.ZONAS is not None:
                        _p.ZONAS.append({"pag": pg.n, "gid": None, "lugar": "datos",
                                         "gids": [a["gid"] for a, _, _ in pila],
                                         "rect": [round(x, 1), round(y - GAP - th, 1), round(x + ancho, 1),
                                                  round(y - GAP, 1)]})
                    y -= th + GAP
            if not (pg.portada and pila is pg.lat):
                y_hueco = y - (GAP if pila else 0)
                if y_hueco - BOTTOM > 70:
                    clave = f"{pg.n}:{'lat' if pila is pg.lat else 'main'}"
                    alto_h = y_hueco - BOTTOM - 4
                    av, _ = asignador.para(clave, alto_h)
                    if av:
                        # el aviso va abajo; si sobra mucho espacio arriba, queda libre para una noticia
                        alto_av = min(alto_h, 230 if pila is pg.lat else 170)
                        rect_av = (x, BOTTOM + 4, x + ancho, BOTTOM + 4 + alto_av)
                        avisos.dibujar(c, av, rect_av, D)
                        _zona_aviso(pg.n, rect_av, clave, av["id"])
                        if alto_h - alto_av - GAP > 70:
                            _hueco(pg.n, (x, BOTTOM + 4 + alto_av + GAP, x + ancho, y_hueco), clave)
                    else:
                        _hueco(pg.n, (x, BOTTOM + 4, x + ancho, y_hueco), clave)
        if pg.lat and not pg.portada:
            xr = pg.x_main + pg.w_main + GUT / 2 if not pg.lateral_izq else pg.x_main - GUT / 2
            _filete_v(c, xr, BOTTOM, pg.top)
    _ficha(c, orden_indice, paginas_de, numero, st)
    c.save()
    return paginas_de


def generar_consolidado(cfg: dict, grupos=None, destino=None, registrar=True, numero=None, ahora=None):
    """Genera el boletín con los grupos no publicados (nuevos o actualizados). None si no hay.
    grupos/destino/registrar permiten generar una vista previa sin marcar nada como publicado."""
    grupos = list(grupos if grupos is not None else storage.grupos_para_pdf())
    if not grupos:
        return None
    _aplicar_diseno()
    st = _st()
    arts = _ordenar([_articulo(g, cfg) for g in grupos], cfg)
    numero = numero or storage.siguiente_consolidado()
    ahora = ahora or datetime.now()
    paginas = _paginar(arts, st, leer_fijas())
    if destino is None:
        CONSOLIDADOS_DIR.mkdir(parents=True, exist_ok=True)
        destino = CONSOLIDADOS_DIR / f"{ahora:%Y-%m-%d_%H%M}.pdf"
        if destino.exists():
            destino = CONSOLIDADOS_DIR / f"{ahora:%Y-%m-%d_%H%M%S}.pdf"
    from . import portada
    portada.ZONAS = []
    try:
        _render(destino, arts, paginas, numero, ahora, st, cfg)
        # mapa de zonas para la vista previa interactiva (qué noticia hay en cada lugar de cada página)
        mapa = {"ancho": W, "alto": H, "zonas": portada.ZONAS,
                "titulares": {str(a["gid"]): a["titular"] for a in arts}}
        Path(str(destino)).with_suffix(".json").write_text(json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
    finally:
        portada.ZONAS = None
    if registrar:
        fechas = [g["fecha_hecho"] for g in grupos if g["fecha_hecho"]]
        storage.registrar_consolidado(destino, min(fechas) if fechas else None,
                                      max(fechas) if fechas else None, [g["id"] for g in grupos])
    return destino
