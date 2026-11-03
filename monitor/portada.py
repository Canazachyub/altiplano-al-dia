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
    return a["n_medios"] * 300 + min(a["palabras"], 400) + (100 if a["ambito"] in ("DISTRITAL", "LOCAL", "REGIONAL") else 0)


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
    rank = sorted(arts, key=lambda a: (0 if a["img"] else 1, -_peso(a)))
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
  el dato más llamativo). Sin signos de exclamación ni sensacionalismo.
- "ante": antetítulo de 5 a 12 palabras que da contexto (quién o dónde).
- "puntos": SOLO para la noticia marcada como principal, 2 frases de 10 a 18 palabras con los
  datos clave; para las demás, lista vacía.
Responde SOLO JSON: {"noticias":[{"id":"n1","portada":"…","ante":"…","puntos":["…","…"]}]}"""


def textos_portada(cfg, arts, principal_gid=None, forzar=False):
    """Titulares de portada. Respeta los escritos a mano; los que faltan los pide a Claude en una
    sola llamada y los guarda en la nota (así la vista previa y el periódico final coinciden)."""
    from . import storage
    out = {a["gid"]: {"portada": a["titular"], "ante": a["ante"].capitalize(), "puntos": []} for a in arts}
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
    return out


# ------------------------------------------------------------------ primitivas de dibujo
def _ajustar(texto, font, ancho, alto, maxsize, minsize, maxlineas, interlinea=0.95):
    for size in range(int(maxsize), int(minsize) - 1, -1):
        lineas = simpleSplit(texto, font, size, ancho)
        cabe = all(pdfmetrics.stringWidth(ln, font, size) <= ancho + 0.5 for ln in lineas)  # palabras largas
        if cabe and len(lineas) <= maxlineas and len(lineas) * size * interlinea <= alto:
            return size, lineas
    return minsize, simpleSplit(texto, font, minsize, ancho)[:maxlineas]


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


def _link(c, a, rect, lugar=None):
    c.linkRect("", f"g{a['gid']}", rect, relative=0, thickness=0)
    if ZONAS is not None:
        ZONAS.append({"pag": 1, "gid": a["gid"], "rect": [round(v, 1) for v in rect], "lugar": lugar})


def _lineas_balanceadas(texto, font, ancho, maxsize):
    """Parte el titular en 2 líneas parejas y calcula el tamaño para llenar el ancho."""
    palabras = texto.split()
    if len(palabras) < 2:
        size = min(maxsize, ancho / max(1, pdfmetrics.stringWidth(texto, font, 10)) * 10)
        return size, [texto]
    mejor = min(((" ".join(palabras[:i]), " ".join(palabras[i:])) for i in range(1, len(palabras))),
                key=lambda ab: max(pdfmetrics.stringWidth(ab[0], font, 10), pdfmetrics.stringWidth(ab[1], font, 10)))
    ancho10 = max(pdfmetrics.stringWidth(x, font, 10) for x in mejor)
    return min(maxsize, ancho / ancho10 * 10 * 0.98), list(mejor)


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
    lh = lw * 297 / 999
    c.drawImage(k["logo_blanco"](), M + 12, top - 8 - lh, lw, lh, mask="auto")
    c.setFillColor(AMARILLO)
    c.setFont("P-EtqB", 11)
    c.drawString(M + 14, top - cab_h + 8, _t("lema", **v))

    fy = top - cab_h - 15
    c.setFillColor(NEGRO)
    c.setFont("P-EtqM", 10.5)
    partes = [k["fecha_larga"](ahora), _t("franja_2", **v), _t("franja_3", **v)]
    for i, (p, xx) in enumerate(zip(partes, (M, M + 154, M + 258))):
        c.drawString(xx + (7 if i else 0), fy, p)
        if i:
            c.setStrokeColor(NEGRO)
            c.setLineWidth(0.9)
            c.line(xx, fy - 3, xx, fy + 10)
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
        for ln in simpleSplit(plano(t["ante"]), "P-Ante", TA, dx1 - dx0)[:2]:
            c.setFont("P-Ante", TA)
            c.drawString(dx0, yy, ln)
            yy -= 12
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
    c.drawCentredString(62, 10, _t("cinta", **v))
    c.restoreState()
    c.setFont("P-Banda", TB)
    tit = _t("banda_1", **v) + " "
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

    # ---------------- zona media
    mz1, mz0 = by0 - 12, 205
    lat_w = 170
    lx0, lx1 = M, M + lat_w
    rx0, rx1 = lx1 + 14, W - M
    c.setStrokeColor(NEGRO)
    c.setLineWidth(0.8)
    c.line(lx1 + 5, mz0, lx1 + 5, mz1)
    c.line(lx1 + 8, mz0, lx1 + 8, mz1)

    a = sel.get("lateral")
    if a:
        t = tx[a["gid"]]
        yy = mz1 - 11
        c.setFillColor(color_tema(a["tema"]))
        for ln in simpleSplit(plano(t["ante"]), "P-Ante", TA * 1.09, lat_w)[:4]:
            c.setFont("P-Ante", TA * 1.09)
            c.drawString(lx0, yy, ln)
            yy -= 13
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
    c.setStrokeColor(AMARILLO)
    c.setLineWidth(4)
    c.line(M, mz0 - 9, W - M, mz0 - 9)
    c.setStrokeColor(AZUL)
    c.setLineWidth(1)
    c.line(M, mz0 - 12.5, W - M, mz0 - 12.5)
    abajo = [sel[x] for x in ("abajo1", "abajo2", "abajo3") if sel.get(x)]
    if abajo:
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
            c.setFont("P-Ante", TA * 0.95)
            ante = simpleSplit(plano(t["ante"]), "P-Ante", TA * 0.95, cw)[:2]
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
