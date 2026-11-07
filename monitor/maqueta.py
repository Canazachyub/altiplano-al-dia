"""Maquetador por plantillas (formato tabloide cuadrado, 28 × 28 cm).

La edición sigue la estructura de un diario regional de 19 páginas:

    1 portada · 2-3 LOCAL · 4 DISTRITAL · 5-9 POLÍTICA · 10-11 POLICIAL · 12-14 PROVINCIAL ·
    15 DEPORTES · 16-17 CLASIFICADOS · 18 ENTRETENIMIENTO · 19 PUPILETRAS

Cada página de noticias usa un ESQUEMA: huecos sobre una grilla de 6 columnas (principal,
secundaria, abajo, breve, aviso). Hay 3 PLANTILLAS que reparten los esquemas de distinta manera
y se alternan por día, para que el diario no se vea igual todos los días.

Las noticias se reparten así: cada sección recibe sus notas por impacto (la más fuerte abre la
página), lo que sobra de una sección llena huecos libres de otras (con su etiqueta de sección) y
los huecos que quedan vacíos llevan un aviso. Cada nota se ajusta a su hueco: la foto crece o se
achica y, si el texto no entra, se acorta al final (la redacción a medida evita llegar a eso).
"""
import json
import random
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Frame, Paragraph, Spacer

from . import avisos, consolidado as C, diseno, portada as PO
from .config import BASE_DIR

# ------------------------------------------------------------------ página
W = H = 792.0                 # 11 × 11 pulgadas (≈ 28 × 28 cm), como el diario de referencia
M = 30                        # margen
NC = 6                        # columnas
GUT = C.GUT                   # medianil (el mismo que usa el armado de notas)
CW = (W - 2 * M - (NC - 1) * GUT) / NC
CAB_Y = H - 20                # borde superior de la cabecera
CAB_H = 34
TOP = CAB_Y - CAB_H - 12      # borde superior del área de noticias
BOTTOM = 32
AH = TOP - BOTTOM             # alto útil
VG = 16                       # separación vertical entre notas (con filete al medio)

NOTICIAS = ["LOCAL", "DISTRITAL", "POLÍTICA", "POLICIAL", "PROVINCIAL", "DEPORTES"]
SERVICIO = ["CLASIFICADOS", "ENTRETENIMIENTO", "PUPILETRAS"]
COLOR_SECCION = {"LOCAL": "#043F9B", "DISTRITAL": "#2E7D32", "POLÍTICA": "#5E2CA5", "POLICIAL": "#C62828",
                 "PROVINCIAL": "#00796B", "DEPORTES": "#E65100", "CLASIFICADOS": "#F2B705",
                 "ENTRETENIMIENTO": "#AD1457", "PUPILETRAS": "#283593", "NACIONAL": "#37474F",
                 "INTERNACIONAL": "#37474F"}

# ------------------------------------------------------------------ esquemas de página
# hueco: (rol, columna inicial, columnas, fila desde, fila hasta)  — filas en fracción del alto útil
ESQUEMAS = {
    # apertura de 4 columnas y nota lateral a todo el alto; abajo, dos notas
    "apertura_lateral": [("principal", 0, 4, 0, .60), ("secundaria", 4, 2, 0, 1),
                         ("abajo", 0, 2, .60, 1), ("abajo", 2, 2, .60, 1)],
    # apertura a todo el ancho y tres notas abajo
    "apertura_ancha": [("principal", 0, 6, 0, .56), ("abajo", 0, 2, .56, 1), ("abajo", 2, 2, .56, 1),
                       ("abajo", 4, 2, .56, 1)],
    # la página policial de cinco notas
    "cinco_notas": [("principal", 0, 4, 0, .57), ("secundaria", 4, 2, 0, .57),
                    ("abajo", 0, 2, .57, 1), ("abajo", 2, 2, .57, 1), ("abajo", 4, 2, .57, 1)],
    # apertura de alto completo, secundaria y breve a la derecha
    "apertura_breve": [("principal", 0, 4, 0, 1), ("secundaria", 4, 2, 0, .62), ("breve", 4, 2, .62, 1)],
    # dos notas y una franja de avisos abajo (donde el diario de referencia pone edictos)
    "dos_y_aviso": [("principal", 0, 4, 0, .74), ("secundaria", 4, 2, 0, .74), ("aviso", 0, 6, .74, 1)],
    # una nota especial a toda la página con aviso abajo
    "especial": [("principal", 0, 6, 0, .80), ("aviso", 0, 6, .80, 1)],
    # nota lateral a la izquierda, apertura al centro-derecha y nota abajo
    "lateral_izquierda": [("secundaria", 0, 2, 0, 1), ("principal", 2, 4, 0, .62), ("abajo", 2, 4, .62, 1)],
    # tres columnas dobles a todo el alto
    "tres_columnas": [("principal", 0, 2, 0, 1), ("secundaria", 2, 2, 0, 1), ("secundaria", 4, 2, 0, 1)],
}

# ------------------------------------------------------------------ plantillas
# (sección, esquema) para las páginas 2 a 15; 16-19 son de servicio en las tres
PLANTILLAS = [
    {"nombre": "Clásica", "portada": "tabloide", "paginas": [
        ("LOCAL", "lateral_izquierda"), ("LOCAL", "apertura_breve"), ("DISTRITAL", "apertura_breve"),
        ("POLÍTICA", "dos_y_aviso"), ("POLÍTICA", "dos_y_aviso"), ("POLÍTICA", "apertura_lateral"),
        ("POLÍTICA", "especial"), ("POLÍTICA", "apertura_breve"), ("POLICIAL", "apertura_breve"),
        ("POLICIAL", "cinco_notas"), ("PROVINCIAL", "apertura_ancha"), ("PROVINCIAL", "apertura_breve"),
        ("PROVINCIAL", "especial"), ("DEPORTES", "especial")]},
    {"nombre": "Dinámica", "portada": "foto", "paginas": [
        ("LOCAL", "apertura_ancha"), ("LOCAL", "apertura_lateral"), ("DISTRITAL", "lateral_izquierda"),
        ("POLÍTICA", "apertura_breve"), ("POLÍTICA", "dos_y_aviso"), ("POLÍTICA", "tres_columnas"),
        ("POLÍTICA", "especial"), ("POLÍTICA", "apertura_lateral"), ("POLICIAL", "cinco_notas"),
        ("POLICIAL", "apertura_breve"), ("PROVINCIAL", "apertura_lateral"), ("PROVINCIAL", "dos_y_aviso"),
        ("PROVINCIAL", "apertura_ancha"), ("DEPORTES", "dos_y_aviso")]},
    {"nombre": "Impacto", "portada": "mosaico", "paginas": [
        ("LOCAL", "cinco_notas"), ("LOCAL", "dos_y_aviso"), ("DISTRITAL", "apertura_ancha"),
        ("POLÍTICA", "apertura_lateral"), ("POLÍTICA", "especial"), ("POLÍTICA", "apertura_breve"),
        ("POLÍTICA", "lateral_izquierda"), ("POLÍTICA", "dos_y_aviso"), ("POLICIAL", "apertura_ancha"),
        ("POLICIAL", "cinco_notas"), ("PROVINCIAL", "apertura_breve"), ("PROVINCIAL", "tres_columnas"),
        ("PROVINCIAL", "dos_y_aviso"), ("DEPORTES", "apertura_breve")]},
]
PAGINAS_SERVICIO = [("CLASIFICADOS", "clasificados"), ("CLASIFICADOS", "guia"),
                    ("ENTRETENIMIENTO", "entretenimiento"), ("PUPILETRAS", "pupiletras")]
ELECCION_PATH = BASE_DIR / "data" / "plantilla.json"
RANGO = {"principal": 0, "secundaria": 1, "abajo": 2, "breve": 3}
# largo que debe tener una nota según su hueco (caracteres del cuerpo; lo usa la redacción a medida)
LARGO = {"principal": (1300, 2000), "secundaria": (650, 1000), "abajo": (450, 750), "breve": (180, 380)}


RELLENO_MAX = 0.5   # como mucho la mitad de los huecos con notas del día anterior; nunca todo el boletín


def plantilla_del_dia(ahora: datetime) -> dict:
    """La plantilla elegida a mano en la vista previa, o una distinta cada día (rotan las tres)."""
    try:
        d = json.loads(ELECCION_PATH.read_text(encoding="utf-8"))
        if d.get("fecha") == f"{ahora:%Y-%m-%d}" and 0 <= int(d.get("indice", -1)) < len(PLANTILLAS):
            return PLANTILLAS[int(d["indice"])]
    except Exception:  # noqa: BLE001
        pass
    return PLANTILLAS[ahora.toordinal() % len(PLANTILLAS)]


def elegir_plantilla(indice: int, ahora: datetime | None = None) -> None:
    ahora = ahora or datetime.now()
    ELECCION_PATH.parent.mkdir(parents=True, exist_ok=True)
    ELECCION_PATH.write_text(json.dumps({"fecha": f"{ahora:%Y-%m-%d}", "indice": indice}), encoding="utf-8")


def cupos(plantilla: dict) -> dict:
    """Huecos de noticia por sección: {sección: {rol: n}}."""
    out = {s: {} for s in NOTICIAS}
    for sec, esq in plantilla["paginas"]:
        for rol, *_ in ESQUEMAS[esq]:
            if rol != "aviso":
                out[sec][rol] = out[sec].get(rol, 0) + 1
    return out


# ------------------------------------------------------------------ sección de cada nota
def seccion_de(a) -> str | None:
    """Sección del diario de una nota (None = nacional/internacional sin sección propia: comodín)."""
    s = (a["nota"].get("SECCION") or "").strip().upper().replace("POLITICA", "POLÍTICA")
    if s in NOTICIAS:
        return s
    if s in ("NACIONAL", "INTERNACIONAL"):      # sin página propia: llena huecos libres
        return None
    tema = (a.get("tema") or "").lower()
    if tema.startswith("seguridad"):
        return "POLICIAL"
    if tema.startswith("deporte"):
        return "DEPORTES"
    if tema.startswith("pol"):
        return "POLÍTICA"
    return {"LOCAL": "LOCAL", "DISTRITAL": "DISTRITAL", "REGIONAL": "PROVINCIAL"}.get(a["ambito"])


def _peso(a):
    return PO._peso(a) + (150 if a["img"] else 0)


# ------------------------------------------------------------------ reparto
class Hueco:
    def __init__(self, pag, idx, seccion, rol, c0, nc, r0, r1):
        self.pag, self.idx, self.seccion, self.rol = pag, idx, seccion, rol
        self.nc = nc
        self.x0 = M + c0 * (CW + GUT)
        self.x1 = self.x0 + nc * CW + (nc - 1) * GUT
        self.top = TOP - r0 * AH - (VG / 2 if r0 > 0 else 0)
        self.bot = TOP - r1 * AH + (VG / 2 if r1 < 1 else 0)
        self.c0, self.r0, self.r1 = c0, r0, r1
        self.art = None
        self.clave = f"{pag}:{idx}"

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.top - self.bot


MIN_PAGINAS_NOTICIAS = 6     # el boletín nunca baja de 6 páginas de noticias


def _vacios(paginas):
    return [h for p in paginas for h in p["huecos"] if h.rol != "aviso" and h.art is None]


def ajustar(arts, plantilla, fijas=None):
    """La plantilla del día ajustada a las notas que hay: se quitan las páginas que quedarían casi
    vacías (sus notas pasan a los huecos libres de las otras), hasta que no sobren huecos o quitar
    otra página deje notas afuera. Así no salen páginas llenas de avisos de relleno."""
    paginas_pl = list(plantilla["paginas"])
    fijas = fijas or {}
    while len(paginas_pl) > MIN_PAGINAS_NOTICIAS:
        pl = dict(plantilla, paginas=paginas_pl)
        pags, sobr = _armar_base(arts, pl, fijas)
        vac = _vacios(pags)
        if not vac:
            break
        fijadas = set(fijas.values())
        candidatas = []
        for i, pg in enumerate(pags[:len(paginas_pl)]):
            if pg["n"] in fijadas:
                continue
            notas = sum(1 for h in pg["huecos"] if h.art is not None)
            propios_vacios = sum(1 for h in pg["huecos"] if h.rol != "aviso" and h.art is None)
            if notas <= len(vac) - propios_vacios:          # sus notas caben en los huecos de las demás
                candidatas.append((notas, -propios_vacios, i))
        if not candidatas:
            break
        mejor = None
        for _, _, i in sorted(candidatas):
            prueba = paginas_pl[:i] + paginas_pl[i + 1:]
            _, sobr2 = _armar_base(arts, dict(plantilla, paginas=prueba), fijas)
            if len(sobr2) <= len(sobr):
                mejor = prueba
                break
        if mejor is None:
            break
        paginas_pl = mejor
    return dict(plantilla, paginas=paginas_pl)


def armar(arts, plantilla, fijas=None):
    """Reparte las notas en los huecos de la plantilla ajustada a lo que hay (ver ajustar)."""
    return _armar_base(arts, ajustar(arts, plantilla, fijas), fijas)


def _armar_base(arts, plantilla, fijas=None):
    """Reparte las notas en los huecos. Devuelve (paginas, sobrantes):
    paginas = [{"n", "seccion", "esquema", "huecos": [Hueco]}]; sobrantes = notas que no entraron."""
    fijas = fijas or {}
    paginas = []
    for i, (sec, esq) in enumerate(plantilla["paginas"]):
        n = i + 2
        paginas.append({"n": n, "seccion": sec, "esquema": esq, "tipo": "noticias",
                        "huecos": [Hueco(n, k, sec, *h) for k, h in enumerate(ESQUEMAS[esq])]})
    for j, (sec, tipo) in enumerate(PAGINAS_SERVICIO):
        paginas.append({"n": len(plantilla["paginas"]) + 2 + j, "seccion": sec, "tipo": tipo, "huecos": []})
    huecos = [h for p in paginas for h in p["huecos"] if h.rol != "aviso"]
    # notas del día anterior que no salieron en papel: solo llenan lo que dejan libre las de hoy
    hoy = max((a.get("dia") or "" for a in arts), default="")
    relleno = sorted([a for a in arts if (a.get("dia") or hoy) < hoy], key=lambda a: -_peso(a))
    libres = sorted([a for a in arts if a not in relleno], key=lambda a: -_peso(a))
    tope = int(len(huecos) * RELLENO_MAX)

    def poner(h, a):
        h.art = a
        (libres if a in libres else relleno).remove(a)

    # 1) las fijadas a mano a una página ("Mover a la página…")
    for a in list(libres) + list(relleno):
        pag = fijas.get(a["gid"])
        if pag:
            cand = [h for h in huecos if h.pag == pag and h.art is None]
            if cand:
                poner(min(cand, key=lambda h: RANGO[h.rol]), a)
    def repartir(lista, maximo=None):
        puestas = 0
        # 2) cada sección con sus notas: primero aperturas (con foto si se puede), al final los breves
        for sec in NOTICIAS:
            propias = [a for a in lista if seccion_de(a) == sec]
            hs = sorted([h for h in huecos if h.seccion == sec and h.art is None],
                        key=lambda h: (RANGO[h.rol], h.pag))
            for h in hs:
                if not propias or (maximo is not None and puestas >= maximo):
                    break
                if h.rol == "breve":
                    a = min(propias, key=lambda x: (x["palabras"], _peso(x)))
                elif h.rol in ("principal", "secundaria"):
                    a = next((x for x in propias[:3] if x["img"]), propias[0])
                else:
                    a = propias[0]
                propias.remove(a)
                poner(h, a)
                puestas += 1
        # 3) huecos libres: las notas sin sección propia (nacionales) y lo que sobró de otras secciones
        for h in sorted([h for h in huecos if h.art is None], key=lambda h: (RANGO[h.rol], h.pag)):
            if not lista or (maximo is not None and puestas >= maximo):
                break
            a = next((x for x in lista[:3] if x["img"]), lista[0]) if h.rol in ("principal", "secundaria") \
                else (min(lista[:6], key=lambda x: x["palabras"]) if h.rol == "breve" else lista[0])
            poner(h, a)
            puestas += 1

    repartir(libres)                                   # las de hoy, todas las que entren
    ya = sum(1 for h in huecos if h.art is not None and h.art in arts and (h.art.get("dia") or hoy) < hoy)
    repartir(relleno, max(0, tope - ya))               # lo que quede vacío, con lo de ayer
    return paginas, libres


# ------------------------------------------------------------------ ajuste de una nota a su hueco
def _oraciones(t):
    return [x for x in re.split(r"(?<=[.!?])\s+", t) if x]


def _acortar(a):
    """La misma nota con un poco menos de texto (sale el último párrafo o la última oración)."""
    ps = list(a["parrafos"])
    while ps and ps[-1].startswith("## "):
        ps.pop()
    if len(ps) > 1:
        ps.pop()
        while ps and ps[-1].startswith("## "):
            ps.pop()
    elif ps:
        o = _oraciones(ps[0])
        if len(o) <= 1:
            return None
        ps[0] = " ".join(o[:-1])
    else:
        return None
    return dict(a, parrafos=ps, _med={}, _recortada=True)


def _tipo(rol):
    return {"principal": "lead", "secundaria": "sec", "abajo": "sec", "breve": "side"}[rol]


def encajar(art, h: Hueco, st, seccion_pag):
    """(alto, ops, nota_usada) de la nota dentro del hueco."""
    ncols = h.nc
    tipo = _tipo(h.rol)
    a = dict(art, _med={})
    if h.rol == "breve":
        a["img"] = None
    sec_nota = seccion_de(art)
    # etiqueta de sección cuando la nota no es de esta página (o es nacional / internacional)
    etiqueta = sec_nota if sec_nota and sec_nota != seccion_pag else (
        art["ambito"] if art["ambito"] in ("NACIONAL", "INTERNACIONAL") else None)
    a["ambito"] = etiqueta or seccion_pag
    marca = seccion_pag if etiqueta else None
    for _ in range(40):
        alto, ops = C._layout(a, h.w, ncols, tipo, st, seccion=marca)
        if alto <= h.h + 0.5:
            if a["img"] and h.h - alto > 20:
                alto, ops = C._medir(a, h.w, ncols, tipo, st, h.h, seccion=marca)
            return alto, ops, a
        if a["img"]:          # primero se prueba con la foto más baja
            alto2, ops2 = C._layout(a, h.w, ncols, tipo, st, foto_extra=-h.w, seccion=marca)
            if alto2 <= h.h + 0.5:
                return alto2, ops2, a
        b = _acortar(a)
        if b is None:
            if a["img"]:
                a = dict(a, img=None, _med={})
                continue
            break
        a = b
    return alto, ops, a


# ------------------------------------------------------------------ cabecera y pie
def _cabecera(c, n, seccion, fecha_txt, D):
    col = colors.HexColor(COLOR_SECCION.get(seccion, D["colores"]["principal"]))
    oscuro = seccion != "CLASIFICADOS"
    y = CAB_Y - CAB_H
    impar = n % 2 == 1
    logo_h = CAB_H
    logo_w = logo_h * C._logo_prop()
    num_w = 34
    if impar:
        lx, bx0, bx1, nx = M, M + logo_w + 8, W - M - num_w - 4, W - M - num_w
    else:
        nx, bx0, bx1, lx = M, M + num_w + 4, W - M - logo_w - 8, W - M - logo_w
    c.setFillColor(col)
    c.rect(bx0, y, bx1 - bx0, CAB_H, stroke=0, fill=1)
    # pestaña con la sección, en grande
    c.setFillColor(colors.white if oscuro else colors.HexColor("#1A1A1A"))
    size = 21
    tw = pdfmetrics.stringWidth(seccion, "P-Banda", size)
    if impar:
        c.setFont("P-Banda", size)
        c.drawString(bx0 + 12, y + 9, seccion)
        c.setFont("Narrow-B", 9.5)
        c.drawRightString(bx1 - 10, y + 19, diseno.texto(C.D, "ciudad"))
        c.setFont("Narrow", 9)
        c.drawRightString(bx1 - 10, y + 8, fecha_txt)
        # diagonal amarilla tras el nombre de la sección
        c.setFillColor(C.AMARILLO)
        p = c.beginPath()
        x = bx0 + 24 + tw
        p.moveTo(x, y)
        p.lineTo(x + 8, y + CAB_H)
        p.lineTo(x + 14, y + CAB_H)
        p.lineTo(x + 6, y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    else:
        c.setFont("P-Banda", size)
        c.drawRightString(bx1 - 12, y + 9, seccion)
        c.setFont("Narrow-B", 9.5)
        c.drawString(bx0 + 10, y + 19, diseno.texto(C.D, "ciudad"))
        c.setFont("Narrow", 9)
        c.drawString(bx0 + 10, y + 8, fecha_txt)
        c.setFillColor(C.AMARILLO)
        p = c.beginPath()
        x = bx1 - 24 - tw
        p.moveTo(x, y)
        p.lineTo(x - 8, y + CAB_H)
        p.lineTo(x - 14, y + CAB_H)
        p.lineTo(x - 6, y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(C.AMARILLO)
    c.rect(nx, y, num_w, CAB_H, stroke=0, fill=1)
    c.setFillColor(C.AZUL)
    c.setFont("P-Banda", 19)
    c.drawCentredString(nx + num_w / 2, y + 10, str(n))
    C._dibujar_logo(c, lx, y, logo_w, logo_h)
    c.setStrokeColor(col)
    c.setLineWidth(1.6)
    c.line(M, y - 5, W - M, y - 5)


def _pie(c, numero, ahora):
    c.setStrokeColor(C.FILETE)
    c.setLineWidth(0.5)
    c.line(M, BOTTOM - 10, W - M, BOTTOM - 10)
    c.setFont("Narrow", 7.5)
    c.setFillColor(C.GRIS)
    v = {"numero": numero, "fecha": f"{ahora:%d/%m/%Y}", "hora": f"{ahora:%H:%M}"}
    c.drawString(M, BOTTOM - 20, diseno.texto(C.D, "pie_izq", **v))
    c.drawRightString(W - M, BOTTOM - 20, diseno.texto(C.D, "pie_der", **v))


# ------------------------------------------------------------------ dibujo de una página de noticias
def _breve_fondo(c, h, col):
    c.setFillColor(colors.HexColor("#F3F1EA"))
    c.rect(h.x0, h.bot, h.w, h.h, stroke=0, fill=1)
    c.setFillColor(col)
    c.rect(h.x0, h.top - 18, 62, 18, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("P-Banda", 11)
    c.drawString(h.x0 + 8, h.top - 13.5, "BREVE")


def _filetes(c, pagina):
    c.setStrokeColor(C.FILETE)
    c.setLineWidth(0.6)
    for h in pagina["huecos"]:
        if h.r0 > 0:
            y = TOP - h.r0 * AH
            c.line(h.x0, y, h.x1, y)
        if h.c0 > 0:
            c.line(h.x0 - GUT / 2, h.bot, h.x0 - GUT / 2, h.top)


def _pagina_noticias(c, pagina, st, asignador, anclas, D, numero, ahora):
    from . import portada
    sec = pagina["seccion"]
    col = colors.HexColor(COLOR_SECCION[sec])
    _filetes(c, pagina)
    for h in pagina["huecos"]:
        if h.art is None:
            _hueco_libre(c, h, pagina, asignador, D)
            continue
        if h.rol == "breve":
            _breve_fondo(c, h, col)
            x0, ytop, w = h.x0 + 8, h.top - 24, h.w - 16
            hh = Hueco(h.pag, h.idx, sec, "breve", h.c0, h.nc, h.r0, h.r1)
            hh.x0, hh.x1, hh.top, hh.bot = x0, x0 + w, ytop, h.bot + 8
            alto, ops, usada = encajar(h.art, hh, st, sec)
        else:
            x0, ytop = h.x0, h.top
            alto, ops, usada = encajar(h.art, h, st, sec)
        h.usada = usada
        C._dibujar(c, ops, x0, ytop, anclas)
        portada.ZONAS.append({"pag": pagina["n"], "gid": h.art["gid"], "lugar": None, "rol": h.rol,
                              "clave": h.clave, "seccion": sec, "rect": [round(h.x0, 1), round(h.bot, 1), round(h.x1, 1),
                                                         round(h.top, 1)]})
        c.addOutlineEntry(C._corto(h.art["titular"]), f"g{h.art['gid']}", level=1)
        # lo que sobra al pie del hueco: datos clave de la nota
        libre = (ytop - alto) - h.bot
        if h.rol != "breve" and libre > 70:
            caja = C._caja_datos([h.art], h.w, libre - 10, st)
            if caja:
                tb, th = caja
                tb.drawOn(c, h.x0, ytop - alto - 10 - th)


def _hueco_libre(c, h, pagina, asignador, D):
    """Hueco sin nota: un aviso (y queda marcado en la vista previa para añadir una nota ahí)."""
    from . import portada
    # una sola "Anúnciate aquí" por página; los demás huecos rotan entre los otros avisos
    av, _ = asignador.para(h.clave, h.h, h.w, grande_ok=not pagina.get("_grande"))
    if av and av.get("grande"):
        pagina["_grande"] = True
    if av:
        avisos.dibujar(c, av, (h.x0, h.bot, h.x1, h.top), D)
        portada.ZONAS.append({"pag": pagina["n"], "gid": None, "lugar": "aviso", "clave": h.clave,
                              "aviso": av["id"], "rol": h.rol, "seccion": pagina["seccion"],
                              "rect": [round(h.x0, 1), round(h.bot, 1), round(h.x1, 1), round(h.top, 1)]})
    else:
        portada.ZONAS.append({"pag": pagina["n"], "gid": None, "lugar": "hueco", "clave": h.clave,
                              "rol": h.rol, "seccion": pagina["seccion"],
                              "rect": [round(h.x0, 1), round(h.bot, 1), round(h.x1, 1), round(h.top, 1)]})


# ------------------------------------------------------------------ ficha de verificación (A4)
def _ficha(c, arts, paginas_de, numero, st):
    from reportlab.lib.pagesizes import A4
    story = [Paragraph("Ficha de verificación", st["v_h"]),
             Paragraph("Uso interno del equipo editorial, no forma parte de lo publicable.", st["v"]), Spacer(1, 6)]
    for a in sorted(arts, key=lambda a: paginas_de.get(a["gid"], 999)):
        nota, g = a["nota"], a["grupo"]
        story.append(Paragraph(f'Pág. {paginas_de.get(a["gid"], "— (no entró)")} · {C._x(a["titular"])}',
                               st["v_b"]))
        story.append(Paragraph(f'{C._x(a["ambito"])} · {C._x(a["tema"])} · {C._x(g["medios"] or "")} · '
                               f'{C._fecha(g["fecha_hecho"])}', st["v"]))
        for l in nota.get("LINKS") or []:
            partes = [f"<b>{C._x(l.get('medio', ''))}</b> ({C._fecha(l.get('fecha'))})"]
            for etq in ("facebook", "externo"):
                if l.get(etq):
                    partes.append(f"<link href='{C.escape(l[etq])}' color='blue'>"
                                  f"{'Facebook' if etq == 'facebook' else 'Nota completa'}</link>")
            story.append(Paragraph(" · ".join(partes), st["v"]))
    while story:
        c.setPageSize(A4)
        f = Frame(20, 30, A4[0] - 40, A4[1] - 50, showBoundary=0)
        antes = len(story)
        f.addFromList(story, c)
        if len(story) == antes:
            story.pop(0)
        c.showPage()


# ------------------------------------------------------------------ edición completa
def render(destino, arts, numero, ahora, st, cfg, fijas=None):
    """Dibuja la edición completa. Devuelve paginas_de {gid: página}."""
    from . import portada, portadas, servicio
    plantilla = plantilla_del_dia(ahora)
    paginas, sobrantes = armar(arts, plantilla, fijas)
    paginas_de = {h.art["gid"]: p["n"] for p in paginas for h in p["huecos"] if h.art}
    if sobrantes:
        portada.AVISOS.append(f"{len(sobrantes)} noticia(s) no entraron en la plantilla «{plantilla['nombre']}»: "
                              + "; ".join(C._corto(a["titular"], 60) for a in sobrantes[:6]))
    vacios = [(p["seccion"], h.rol) for p in paginas for h in p["huecos"] if h.art is None and h.rol != "aviso"]
    if vacios:
        por_sec = {}
        for s, _ in vacios:
            por_sec[s] = por_sec.get(s, 0) + 1
        portada.AVISOS.append("Faltan notas para completar la edición: "
                              + ", ".join(f"{s.capitalize()} {n}" for s, n in por_sec.items())
                              + ". Puedes añadir notas propias desde la vista previa.")
    D = C.D
    c = Canvas(str(destino), pagesize=(W, H))
    c.setTitle(f"Altiplano al Día — Boletín N° {numero}")
    c.setAuthor("Altiplano al Día")
    fecha_txt = C._fecha_larga(ahora)
    anclas = set()
    c.bookmarkPage("p1")
    c.addOutlineEntry("Portada", "p1", level=0)
    k = {"AZUL": C.AZUL, "AZUL_CLARO": C.AZUL_CLARO, "AMARILLO": C.AMARILLO, "NEGRO": C.NEGRO, "ROJO": C.ROJO,
         "W": W, "H": H, "M": M, "recorte": C._recorte, "icono": C._icono, "dibujar_logo": C._dibujar_logo,
         "logo_prop": C._logo_prop, "fecha_larga": C._fecha_larga, "plano": C._plano,
         "paginas": paginas, "color_seccion": COLOR_SECCION}
    portadas.dibujar(c, cfg, arts, paginas_de, numero, ahora, plantilla["portada"], k)
    asignador = avisos.Asignador()
    rnd = random.Random(ahora.toordinal() * 1000 + int(numero or 0))
    for p in paginas:
        c.showPage()
        c.setPageSize((W, H))
        _cabecera(c, p["n"], p["seccion"], fecha_txt, D)
        _pie(c, numero, ahora)
        c.bookmarkPage(f"p{p['n']}")
        c.addOutlineEntry(f"Pág. {p['n']} · {p['seccion'].capitalize()}", f"p{p['n']}", level=0)
        area = (M, BOTTOM, W - M, TOP)
        if p["tipo"] == "noticias":
            _pagina_noticias(c, p, st, asignador, anclas, D, numero, ahora)
        else:
            servicio.dibujar(c, p["tipo"], area, k, arts=arts, rnd=rnd, numero=numero, ahora=ahora)
    c.save()
    from reportlab.lib.pagesizes import A4
    C.guardar_ficha(destino, lambda cf: _ficha(cf, arts, paginas_de, numero, st), A4, numero)
    return paginas_de
