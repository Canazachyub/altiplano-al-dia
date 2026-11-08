"""Portada del periódico, estilo diario regional impreso.

Bloques: cabezal azul con el logo en blanco, nota destacada (arriba a la derecha), franja de fecha,
banda "Hoy en Puno", nota lateral, nota principal y tres notas abajo. En cada bloque la foto ocupa
todo el espacio y el titular va encima, con borde blanco para que se lea sobre la imagen.

Tipografías: Alfa Slab One (titulares de portada, muy pesada) + Fira Sans Condensed (antetítulos,
puntos clave y etiquetas). Cada tema tiene su color (antetítulo y primera línea del titular).

Qué noticia va en cada lugar se puede fijar a mano en la pestaña Portada (data/portada.json);
lo que no se fija se elige solo, por importancia y por tener foto.
"""
import json
import logging
import re

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics

from .config import BASE_DIR

log = logging.getLogger("monitor")
PORTADA_PATH = BASE_DIR / "data" / "portada.json"
LUGARES = [("principal", "Principal"), ("lateral", "Lateral izquierda"), ("destacada", "Destacada (arriba)"),
           ("abajo1", "Abajo 1"), ("abajo2", "Abajo 2"), ("abajo3", "Abajo 3")]
# ancho aproximado (pt) con que se imprime la foto de cada lugar, para avisar si es chica
ANCHO_LUGAR = {"principal": 390, "lateral": 170, "destacada": 172, "abajo1": 180, "abajo2": 180, "abajo3": 180}

HEAVY, FC_M, FC_SB, FC_B, FC_XB, FC_XBI = "Heavy", "FC-M", "FC-SB", "FC-B", "FC-XB", "FC-XBI"
COLOR_TEMA = {"politica": "#043F9B", "seguridad": "#C62828", "economia": "#2E7D32", "educacion": "#6A1B9A",
              "salud": "#00838F", "sociedad": "#D84315", "cultura": "#AD1457", "deportes": "#1565C0",
              "medio": "#2E7D32", "otros": "#37474F"}


D = None   # diseño activo (lo pone consolidado al generar)


def _d():
    global D
    if D is None:
        from . import diseno
        D = diseno.cargar()
    return D


def _t(clave, **v):
    from . import diseno
    return diseno.texto(_d(), clave, **v)


def color_hex(tema: str) -> str:
    from . import diseno
    return diseno.color_tema_hex(_d(), tema)


def color_tema(tema: str):
    return colors.HexColor(color_hex(tema))


# ------------------------------------------------------------------ lugares fijados a mano
def leer_lugares() -> dict:
    try:
        return {k: int(v) for k, v in json.loads(PORTADA_PATH.read_text(encoding="utf-8")).items() if v}
    except Exception:  # noqa: BLE001
        return {}


def guardar_lugares(d: dict) -> None:
    PORTADA_PATH.parent.mkdir(parents=True, exist_ok=True)
    PORTADA_PATH.write_text(json.dumps({k: v for k, v in d.items() if v}, indent=2), encoding="utf-8")


def calidad_foto(ruta, ancho_pt) -> tuple[int, int, bool]:
    """(ancho_px, alto_px, suficiente). Suficiente = al menos ~110 ppp al tamaño impreso."""
    try:
        with Image.open(ruta) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        return 0, 0, False
    return w, h, w >= ancho_pt * 1.5


def _peso(a):
    """Importancia para la portada: manda el impacto del paso editorial (crimen, corrupción,
    tragedias…); luego que sea tendencia (varios medios), que sea de Puno y que tenga contenido."""
    from .editorial import impacto
    return (impacto(a) * 120 + min(a["n_medios"], 4) * 70 + min(a["palabras"], 400) // 4
            + (60 if a["ambito"] in ("DISTRITAL", "LOCAL", "REGIONAL") else 0))


def elegir(arts) -> dict:
    """{lugar: art} respetando lo fijado a mano; el resto por peso (primero las que tienen foto)."""
    por_id = {a["gid"]: a for a in arts}
    fijados = leer_lugares()
    out, usados = {}, set()
    for lugar, _ in LUGARES:
        gid = fijados.get(lugar)
        if gid in por_id and gid not in usados:
            out[lugar] = por_id[gid]
            usados.add(gid)
    # la foto pesa, pero una noticia de mucho impacto sin foto le gana a una menor con foto
    rank = sorted(arts, key=lambda a: -(_peso(a) + (250 if a["img"] else 0)))
    libres = [a for a in rank if a["gid"] not in usados]
    for lugar, _ in LUGARES:
        if lugar not in out and libres:
            out[lugar] = libres.pop(0)
    return out


# ------------------------------------------------------------------ textos (Claude)
SYS_PORTADA = """Eres el editor de portada de "Altiplano al Día", diario de Puno (Perú).
Para cada noticia escribe textos de portada, fieles a los hechos, sin inventar nada y SIN nombrar
nunca al medio que difundió la información:
- "portada": titular de portada de 3 a 7 palabras, directo y con fuerza (verbo en activo,
  el dato más llamativo). Es lo que hace que alguien compre y lea el diario: empieza por la
  víctima, la cifra o la consecuencia; verbos fuertes y precisos (muere, captura, denuncian,
  paralizan, condenan, exigen); nada genérico ("Realizan…", "Destacan…", "Se pronuncian…").
  Sin signos de exclamación, sin preguntas y sin exagerar los hechos.
- "ante": antetítulo de 5 a 12 palabras que da contexto (quién o dónde).
- "puntos": SOLO para la noticia marcada como principal, 2 frases de 10 a 18 palabras con los
  datos clave; para las demás, lista vacía.
Responde SOLO JSON: {"noticias":[{"id":"n1","portada":"…","ante":"…","puntos":["…","…"]}]}"""


def _ante_natural(a) -> str:
    """Antetítulo con sus mayúsculas originales (a["ante"] viene en MAYÚSCULAS y capitalize()
    dejaría los nombres propios en minúscula: "Consejo regional de puno")."""
    n = a.get("nota") or {}
    for cand in (n.get("ANTETITULO"), n.get("BAJADA") if len((n.get("BAJADA") or "").split()) <= 12 else None,
                 a.get("tema")):
        if cand and cand.strip():
            return cand.strip().rstrip(".")
    return a["ante"].capitalize()


def textos_portada(cfg, arts, principal_gid=None, forzar=False):
    """Titulares de portada. Respeta los escritos a mano; los que faltan los pide a Claude en una
    sola llamada y los guarda en la nota (así la vista previa y el periódico final coinciden)."""
    from . import storage
    out = {a["gid"]: {"portada": a["titular"], "ante": _ante_natural(a), "puntos": []} for a in arts}
    for a in arts:
        n = a["nota"]
        if n.get("PORTADA_TITULAR") and not forzar:
            out[a["gid"]] = {"portada": n["PORTADA_TITULAR"], "ante": n.get("PORTADA_ANTE") or out[a["gid"]]["ante"],
                             "puntos": [x for x in (n.get("PORTADA_PUNTOS") or []) if x]}
    faltan = [a for a in arts if forzar or not a["nota"].get("PORTADA_TITULAR")]
    if not faltan:
        return out
    try:
        from .analisis import claude_json
        from .config import get_api_key
        if not get_api_key("claude"):
            return out
        mapa = {f"n{i + 1}": a for i, a in enumerate(faltan)}
        user = json.dumps({"noticias": [
            {"id": k, "principal": a["gid"] == principal_gid, "titular": a["titular"], "antetitulo": a["ante"],
             "texto": " ".join(a["parrafos"])[:1800]} for k, a in mapa.items()]}, ensure_ascii=False)
        res = claude_json(cfg, SYS_PORTADA, user, f"portada[{len(faltan)}]", 4000)
        for it in res.get("noticias", []):
            a = mapa.get(str(it.get("id")))
            if a and it.get("portada"):
                out[a["gid"]] = {"portada": it["portada"], "ante": it.get("ante") or out[a["gid"]]["ante"],
                                 "puntos": [p for p in it.get("puntos") or [] if p][:3]}
                nota = dict(a["nota"], PORTADA_TITULAR=out[a["gid"]]["portada"],
                            PORTADA_ANTE=out[a["gid"]]["ante"], PORTADA_PUNTOS=out[a["gid"]]["puntos"])
                a["nota"] = nota
                storage.actualizar_grupo(a["gid"], nota_json=nota)
    except Exception as e:  # noqa: BLE001
        log.warning("Portada sin textos de Claude: %s", e)
        AVISOS.append(f"La portada usa los titulares originales: Claude no respondió ({e}).")
    return out


# ------------------------------------------------------------------ primitivas de dibujo
def _elipsis(lineas, font, size, ancho, maxlineas):
    """Deja maxlineas; si sobra texto, la última termina en "…" sin pasarse del ancho."""
    if len(lineas) <= maxlineas:
        return lineas
    out = lineas[:max(1, maxlineas)]
    ult = out[-1].rstrip(" ,;:.-–—") + "…"
    while pdfmetrics.stringWidth(ult, font, size) > ancho and " " in ult.strip():
        ult = ult[:-1].rsplit(" ", 1)[0].rstrip(" ,;:.-–—") + "…"
    out[-1] = ult
    return out


def _partir(texto, font, size, ancho):
    from .consolidado import _partir as partir   # parte con guion las palabras que no entran
    return partir(texto, font, size, ancho)


def _ajustar(texto, font, ancho, alto, maxsize, minsize, maxlineas, interlinea=0.95):
    """Mayor tamaño (de maxsize a minsize) en que el texto entra en el recuadro. Si ni al mínimo
    entra, se corta con "…" (antes se perdía el final del titular sin avisar)."""
    for size in range(int(maxsize), int(minsize) - 1, -1):
        lineas = simpleSplit(texto, font, size, ancho)
        cabe = all(pdfmetrics.stringWidth(ln, font, size) <= ancho + 0.5 for ln in lineas)  # palabras largas
        if cabe and len(lineas) <= maxlineas and len(lineas) * size * interlinea <= alto:
            return size, lineas
    maxl = max(1, min(maxlineas, int(alto / (minsize * interlinea)) if alto > 0 else 1))
    return minsize, _elipsis(_partir(texto, font, minsize, ancho), font, minsize, ancho, maxl)


def _contorno(c, x, y, t, font, size, relleno, borde=colors.white, grosor=None, sombra=True):
    """Texto con borde (y sombra suave), legible encima de las fotos."""
    grosor = grosor if grosor is not None else max(1.2, size * 0.11)
    c.saveState()
    if sombra:
        c.setFillColor(colors.Color(0, 0, 0, alpha=0.30))
        c.setFont(font, size)
        c.drawString(x + size * 0.045, y - size * 0.055, t)
        c.setFillAlpha(1)
    if grosor > 0:
        to = c.beginText(x, y)
        to.setFont(font, size)
        to.setTextRenderMode(1)
        c.setStrokeColor(borde)
        c.setLineWidth(grosor)
        c.setLineJoin(1)
        to.textOut(t)
        to.setTextRenderMode(0)            # el modo 1 persiste en el PDF: se restaura aquí
        c.drawText(to)
    tf = c.beginText(x, y)
    tf.setFont(font, size)
    c.setFillColor(relleno)
    tf.textOut(t)
    c.drawText(tf)
    c.restoreState()


def _velo(c, x, y, w, h, hacia="abajo", a0=0.85, a1=0.0, color=colors.white, pasos=28):
    """Degradado de transparencia (franjas) sobre la foto para que el titular se lea."""
    if w <= 0 or h <= 0:
        return
    c.saveState()
    for i in range(pasos):
        a = a0 + (a1 - a0) * i / (pasos - 1)
        c.setFillColor(color, alpha=max(0.0, a))
        if hacia == "abajo":       # opaco arriba -> transparente abajo
            c.rect(x, y + h - (i + 1) * h / pasos, w, h / pasos + 0.6, stroke=0, fill=1)
        else:                      # opaco a la izquierda -> transparente a la derecha
            c.rect(x + i * w / pasos, y, w / pasos + 0.6, h, stroke=0, fill=1)
    c.restoreState()


def _etiqueta(c, x_der, y, pag, k):
    t = f"‹‹ PÁG. {pag}"
    w = pdfmetrics.stringWidth(t, "P-Etq", 10) + 12
    c.setFillColor(k["AMARILLO"])
    c.rect(x_der - w, y, w, 16, stroke=0, fill=1)
    c.setFillColor(k["AZUL"])
    c.setFont("P-Etq", 10)
    c.drawString(x_der - w + 6, y + 4.5, t)


def _foto(c, a, x, y, w, h, k):
    """La foto llena todo el bloque (recorte al centro). Sin foto: fondo del color del tema + icono."""
    if w <= 2 or h <= 2:
        return
    if a and a["img"]:
        c.drawImage(k["recorte"](a["img"], int(w), int(h)), x, y, w, h)
        return
    c.setFillColor(color_tema(a["tema"] if a else ""))
    c.rect(x, y, w, h, stroke=0, fill=1)
    ico = k["icono"]("tema", a["tema"], True) if a else None
    if ico:
        s = min(w, h) * 0.5
        c.saveState()
        c.setFillAlpha(0.25)
        c.drawImage(ico, x + w - s - 8, y + 8, s, s, mask="auto")
        c.restoreState()


# zonas clicables de la vista previa: [{pag, gid, rect, lugar}] (las llena el render)
ZONAS = None
# problemas de la última portada (p. ej. Claude no respondió): van al mapa .json como "avisos"
AVISOS = []


# gids que tienen página en la edición (los pone el render). Una nota de portada que no entró en las páginas
# interiores no lleva enlace: apuntaría a un destino que no existe y el PDF no se puede guardar.
COLOCADAS = None


def _link(c, a, rect, lugar=None):
    if COLOCADAS is None or a["gid"] in COLOCADAS:
        c.linkRect("", f"g{a['gid']}", rect, relative=0, thickness=0)
    if ZONAS is not None:
        ZONAS.append({"pag": 1, "gid": a["gid"], "rect": [round(v, 1) for v in rect], "lugar": lugar})


def _lineas_balanceadas(texto, font, ancho, maxsize):
    """Parte el titular en 2 líneas parejas y calcula el tamaño para llenar el ancho."""
    palabras = texto.split()
    if len(palabras) < 2:
        size = min(maxsize, ancho / max(1, pdfmetrics.stringWidth(texto, font, 10)) * 10)
        return size, [texto]
    def w10(t):
        return pdfmetrics.stringWidth(t, font, 10)
    mejor = min(((" ".join(palabras[:i]), " ".join(palabras[i:])) for i in range(1, len(palabras))),
                key=lambda ab: max(w10(ab[0]), w10(ab[1])))
    size = min(maxsize, ancho / max(w10(x) for x in mejor) * 10 * 0.98)
    if size < maxsize * 0.62 and len(palabras) >= 6:
        # titular largo: en 3 líneas parejas queda más grande que en 2 apretadas
        tres = min(((" ".join(palabras[:i]), " ".join(palabras[i:j]), " ".join(palabras[j:]))
                    for i in range(1, len(palabras) - 1) for j in range(i + 1, len(palabras))),
                   key=lambda abc: max(w10(x) for x in abc))
        size3 = min(maxsize, ancho / max(w10(x) for x in tres) * 10 * 0.98)
        if size3 > size * 1.12:
            return size3, list(tres)
    return size, list(mejor)


def _cabe(texto, font, size, ancho):
    """Tamaño (como mucho 'size') con que el texto de una línea entra en 'ancho'."""
    w = pdfmetrics.stringWidth(texto, font, size)
    return size if w <= ancho or w <= 0 else size * ancho / w


def _singular(texto, n):
    """1 NOTICIAS -> 1 NOTICIA (el texto de la banda se puede editar: solo se toca esa palabra)."""
    if n != 1:
        return texto
    return re.sub(r"\b(noticia)s\b", r"\1", texto, flags=re.IGNORECASE)


def _panel_sin_destacada(c, x0, y0, x1, y1, ahora, v, k):
    """Con pocas noticias no hay destacada: el hueco lleva la ilustración del altiplano con la fecha."""
    from .config import ASSETS_DIR
    w, h = x1 - x0, y1 - y0
    c.saveState()
    c.setFillColor(k["AZUL"])
    c.rect(x0, y0, w, h, stroke=0, fill=1)
    ilus = ASSETS_DIR / "altiplano_ilustracion.jpg"
    if ilus.exists():
        c.drawImage(k["recorte"](str(ilus), int(w), int(h)), x0, y0, w, h)
        c.setFillColor(k["AZUL"], alpha=0.6)
        c.rect(x0, y0, w, h, stroke=0, fill=1)
        c.setFillAlpha(1)
    c.setFillColor(colors.white)
    ciudad = _t("ciudad", **v)
    c.setFont("P-Banda", _cabe(ciudad, "P-Banda", 26, w - 20))
    c.drawCentredString(x0 + w / 2, y0 + h / 2 + 6, ciudad)
    fecha = k["fecha_larga"](ahora)
    c.setFont("P-EtqM", _cabe(fecha, "P-EtqM", 10, w - 16))
    c.drawCentredString(x0 + w / 2, y0 + h / 2 - 12, fecha)
    c.setFillColor(k["AMARILLO"])
    franja = _t("franja_2", **v)
    c.setFont("P-EtqB", _cabe(franja, "P-EtqB", 10, w - 16))
    c.drawCentredString(x0 + w / 2, y0 + h / 2 - 27, franja)
    c.restoreState()


# ------------------------------------------------------------------ portada
def dibujar(c, cfg, arts_todos, paginas_de, numero, ahora, k):
    AZUL, AMARILLO, NEGRO = k["AZUL"], k["AMARILLO"], colors.HexColor("#111111")
    ROJO = k.get("ROJO", colors.HexColor("#C62828"))
    NEGRO = colors.HexColor(_d()["colores"]["texto"])
    ep = float(_d()["tamanos"].get("titulares_portada", 100)) / 100
    TAM = {k: float(x) for k, x in _d()["tamanos"].items()}
    TA, TP, TB = TAM["portada_antetitulo"], TAM["portada_puntos"], TAM["portada_banda"]
    v = {"numero": numero, "fecha": f"{ahora:%d/%m/%Y}", "hora": f"{ahora:%H:%M}", "n": len(arts_todos)}
    W, H = k["W"], k["H"]
    M = 20
    sel = elegir(arts_todos)
    if not sel:
        return
    if not sel.get("principal"):          # (solo hay notas fijadas en otros lugares) la principal nunca falta
        for lugar in ("lateral", "destacada", "abajo1", "abajo2", "abajo3"):
            if sel.get(lugar):
                sel["principal"] = sel.pop(lugar)
                break
    principal = sel.get("principal")
    tx = textos_portada(cfg, list(sel.values()), principal["gid"] if principal else None)
    pag = lambda a: paginas_de.get(a["gid"], "—")  # noqa: E731
    plano = k["plano"]

    # ---------------- cabezal azul con el logo en blanco
    top = H - 20
    cab_w, cab_h = 374, 124
    c.setFillColor(AZUL)
    c.rect(M, top - cab_h, cab_w, cab_h, stroke=0, fill=1)
    lw = cab_w - 30
    if "dibujar_logo" in k:               # logo vectorial (cae al PNG si falta el SVG)
        lh = lw / k["logo_prop"](True)
        alto = cab_h - 30                 # espacio entre el borde de arriba y el lema
        k["dibujar_logo"](c, M + 12, top - 8 - (alto - lh) / 2 - lh, lw, lh, blanco=True)
    else:
        lh = lw * 297 / 999
        c.drawImage(k["logo_blanco"](), M + 12, top - 8 - lh, lw, lh, mask="auto")
    c.setFillColor(AMARILLO)
    lema = _t("lema", **v)
    c.setFont("P-EtqB", _cabe(lema, "P-EtqB", 11, cab_w - 28))
    c.drawString(M + 14, top - cab_h + 8, lema)

    fy = top - cab_h - 15
    c.setFillColor(NEGRO)
    # franja de fecha: cada parte a continuación de la anterior (una fecha larga como
    # "Miércoles 30 de septiembre de 2026" ya no pisa el separador)
    partes = [k["fecha_larga"](ahora), _t("franja_2", **v), _t("franja_3", **v)]
    sep = 14
    fs = _cabe("".join(partes), "P-EtqM", 10.5, cab_w - sep * (len(partes) - 1))
    c.setFont("P-EtqM", fs)
    xx = M
    for i, p in enumerate(partes):
        if i:
            c.setStrokeColor(NEGRO)
            c.setLineWidth(0.9)
            c.line(xx + sep / 2, fy - 3, xx + sep / 2, fy + 10)
            xx += sep
        c.drawString(xx, fy, p)
        xx += pdfmetrics.stringWidth(p, "P-EtqM", fs)
    c.setStrokeColor(AZUL)
    c.setLineWidth(2.5)
    c.line(M, fy - 7, M + cab_w, fy - 7)

    # ---------------- destacada (arriba a la derecha): foto a todo el bloque, titular encima
    dx0, dx1 = M + cab_w + 12, W - M
    a = sel.get("destacada")
    if a:
        t = tx[a["gid"]]
        c.setFillColor(color_tema(a["tema"]))
        yy = top - 10
        sa, la = _ajustar(plano(t["ante"]), "P-Ante", dx1 - dx0, TA * 2.3, TA, TA * 0.8, 2, 1.05)
        for ln in la:
            c.setFont("P-Ante", sa)
            c.drawString(dx0, yy, ln)
            yy -= sa * 1.09
        fb, ft = fy - 7, yy - 1
        _foto(c, a, dx0, fb, dx1 - dx0, ft - fb, k)
        _velo(c, dx0, fb, dx1 - dx0, ft - fb, "derecha", 0.75, 0.0)
        size, lineas = _ajustar(plano(t["portada"]).upper(), "P-Tit2", (dx1 - dx0) * 0.8, ft - fb - 26, TAM["portada_destacada"] * ep, 8, 5, 1.05)
        y2 = ft - size - 4
        for ln in lineas:
            _contorno(c, dx0 + 5, y2, ln, "P-Tit2", size, NEGRO)
            y2 -= size * 1.05
        _etiqueta(c, dx1, fb + 3, pag(a), k)
        _link(c, a, (dx0, fb, dx1, top), "destacada")
        c.setStrokeColor(NEGRO)
        c.setLineWidth(3)
        c.line(dx0, fb - 0.5, dx1, fb - 0.5)
    else:
        _panel_sin_destacada(c, dx0, fy - 7, dx1, top, ahora, v, k)

    # ---------------- banda "Hoy en Puno"
    by1 = fy - 16
    bh = 50
    by0 = by1 - bh
    c.saveState()
    p = c.beginPath()
    p.roundRect(M, by0, W - 2 * M, bh, 4)
    c.clipPath(p, stroke=0, fill=0)
    c.linearGradient(M, by0, W - M, by0, (AZUL, k["AZUL_CLARO"]), extend=False)
    c.restoreState()
    c.setStrokeColor(colors.white)
    c.setLineWidth(1.2)
    c.roundRect(M + 4, by0 + 4, W - 2 * M - 8, bh - 8, 3, stroke=1, fill=0)
    c.saveState()
    c.translate(M + 16, by0 + 9)
    c.rotate(7)
    c.setFillColor(ROJO)
    c.rect(0, 0, 124, 30, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("P-Banda", 13)
    cinta = _t("cinta", **v)
    c.setFont("P-Banda", _cabe(cinta, "P-Banda", 13, 114))
    c.drawCentredString(62, 10, cinta)
    c.restoreState()
    c.setFont("P-Banda", TB)
    tit = _singular(_t("banda_1", **v), len(arts_todos)) + " "
    c.setFillColor(colors.white)
    c.drawString(M + 160, by0 + 26, tit)
    c.setFillColor(AMARILLO)
    c.drawString(M + 160 + pdfmetrics.stringWidth(tit, "P-Banda", TB), by0 + 26, _t("banda_2", **v))
    xx = M + 160
    for amb in cfg["ambitos"]:
        n = sum(1 for x in arts_todos if x["ambito"] == amb)
        if not n:
            continue
        ico = k["icono"]("ambito", amb, True)
        if ico:
            c.drawImage(ico, xx, by0 + 8, 13, 13, mask="auto")
        c.setFillColor(colors.white)
        c.setFont("P-Ante", 10)
        lab = f"{amb.title()} {n}"
        c.drawString(xx + 15, by0 + 11, lab)
        xx += 15 + pdfmetrics.stringWidth(lab, "P-Ante", 10) + 12

    # ---------------- zona media (con pocas noticias se estira: sin fila de abajo baja hasta el pie,
    # sin nota lateral la principal ocupa todo el ancho)
    hay_abajo = any(sel.get(x) for x in ("abajo1", "abajo2", "abajo3"))
    mz1, mz0 = by0 - 12, (205 if hay_abajo else 34)
    lat_w = 170
    lx0, lx1 = M, M + lat_w
    rx0, rx1 = (lx1 + 14 if sel.get("lateral") else M), W - M
    if sel.get("lateral"):
        c.setStrokeColor(NEGRO)
        c.setLineWidth(0.8)
        c.line(lx1 + 5, mz0, lx1 + 5, mz1)
        c.line(lx1 + 8, mz0, lx1 + 8, mz1)

    a = sel.get("lateral")
    if a:
        t = tx[a["gid"]]
        yy = mz1 - 11
        c.setFillColor(color_tema(a["tema"]))
        sa, la = _ajustar(plano(t["ante"]), "P-Ante", lat_w, TA * 1.09 * 4.6, TA * 1.09, TA * 0.85, 4, 1.1)
        for ln in la:
            c.setFont("P-Ante", sa)
            c.drawString(lx0, yy, ln)
            yy -= sa * 1.19
        yy -= 3
        size, lineas = _ajustar(plano(t["portada"]).upper(), "P-Tit2", lat_w, 170, TAM["portada_lateral"] * ep, 11, 6, 1.0)
        for ln in lineas:
            yy -= size * 0.9
            c.setFillColor(NEGRO)
            c.setFont("P-Tit2", size)
            c.drawString(lx0, yy, ln)
            yy -= size * 0.12
        yy -= 7
        _foto(c, a, lx0, mz0, lat_w, yy - mz0, k)
        _etiqueta(c, lx0 + 66, mz0 + 3, pag(a), k)
        _link(c, a, (lx0, mz0, lx1, mz1), "lateral")

    # principal: antetítulo arriba y la foto ocupa todo el resto, con el titular encima
    a = principal
    if a:
        t = tx[a["gid"]]
        yy = mz1
        size_a, lin_a = _ajustar(plano(t["ante"]).upper(), "P-AnteXB", rx1 - rx0, 36, TA * 1.55, 9, 2, 1.05)
        for ln in lin_a:
            yy -= size_a * 0.92
            c.setFillColor(NEGRO)
            c.setFont("P-AnteXB", size_a)
            c.drawString(rx0, yy, ln)
            yy -= size_a * 0.14
        foto_top = yy - 5
        _foto(c, a, rx0, mz0, rx1 - rx0, foto_top - mz0, k)
        size, lineas = _lineas_balanceadas(plano(t["portada"]).upper(), "P-Tit", rx1 - rx0 - 8, TAM["portada_principal"] * ep)
        alto_tit = len(lineas) * size * 0.98 + 10
        _velo(c, rx0, foto_top - alto_tit * 1.25, rx1 - rx0, alto_tit * 1.25, "abajo", 0.8, 0.0)
        y2 = foto_top - 4
        for i, ln in enumerate(lineas):
            y2 -= size * 0.86
            _contorno(c, rx0 + 4, y2, ln, "P-Tit", size, color_tema(a["tema"]) if i == 0 else NEGRO,
                      grosor=size * 0.1)
            y2 -= size * 0.12
        puntos = [plano(p) for p in t["puntos"]][:2] or [plano(a["nota"].get("BAJADA") or "")]
        py = mz0 + 26
        puntos = [p for p in puntos if p]
        ancho_p = (rx1 - rx0) * 0.62
        # mismo tamaño para todos los puntos: el que entra en el más largo
        fs = min([_ajustar(p, "P-Pun", ancho_p, 60 * TP / 13.5, TP, 8, 4, 1.1)[0] for p in puntos] or [TP])
        for ptxt in reversed(puntos):
            lineas_p = simpleSplit(ptxt, "P-Pun", fs, ancho_p)[:4]
            for j, ln in enumerate(reversed(lineas_p)):
                _contorno(c, rx0 + 24, py, ln, "P-Pun", fs, NEGRO, grosor=3, sombra=False)
                if j == len(lineas_p) - 1:
                    c.setFillColor(ROJO)
                    c.setStrokeColor(colors.white)
                    c.setLineWidth(1.5)
                    c.circle(rx0 + 13, py + 4.5, 6, stroke=1, fill=1)
                py += fs * 1.1
            py += 6
        _etiqueta(c, rx1, mz0 + 3, pag(a), k)
        _link(c, a, (rx0, mz0, rx1, mz1), "principal")

    # ---------------- fila inferior: la foto llena cada tarjeta y el titular va encima, a la izquierda
    abajo = [sel[x] for x in ("abajo1", "abajo2", "abajo3") if sel.get(x)]
    if abajo:
        c.setStrokeColor(AMARILLO)
        c.setLineWidth(4)
        c.line(M, mz0 - 9, W - M, mz0 - 9)
        c.setStrokeColor(AZUL)
        c.setLineWidth(1)
        c.line(M, mz0 - 12.5, W - M, mz0 - 12.5)
        n = len(abajo)
        gap = 12
        cw = (W - 2 * M - gap * (n - 1)) / n
        y1, y0 = mz0 - 21, 34
        for i, a in enumerate(abajo):
            t = tx[a["gid"]]
            x0 = M + i * (cw + gap)
            if i:
                c.setStrokeColor(ROJO)
                c.setLineWidth(0.9)
                c.line(x0 - gap / 2, y0, x0 - gap / 2, y1)
            c.setFillColor(color_tema(a["tema"]))
            sa, ante = _ajustar(plano(t["ante"]), "P-Ante", cw, TA * 0.95 * 2.3, TA * 0.95, TA * 0.75, 2, 1.05)
            c.setFont("P-Ante", sa)
            for j, ln in enumerate(ante):
                c.drawString(x0, y1 - 9 - j * 11, ln)
            ft = y1 - 15 - 11 * max(0, len(ante) - 1)
            _foto(c, a, x0, y0, cw, ft - y0, k)
            _velo(c, x0, y0, cw * 0.8, ft - y0, "derecha", 0.85, 0.0)
            size, lineas = _ajustar(plano(t["portada"]).upper(), "P-Tit2", cw * 0.82, ft - y0 - 24, TAM["portada_abajo"] * ep, 8, 4, 1.05)
            yy = ft - size - 3
            for ln in lineas:
                _contorno(c, x0 + 3, yy, ln, "P-Tit2", size, NEGRO)
                yy -= size * 1.05
            _etiqueta(c, x0 + cw, y0 + 3, pag(a), k)
            _link(c, a, (x0, y0, x0 + cw, y1), f"abajo{i + 1}")
    c.setFont("P-EtqM", 7.5)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(M, 20, _t("pie_portada_izq", **v))
    c.drawRightString(W - M, 20, _t("pie_portada_der", **v))
