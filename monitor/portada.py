"""Portada del boletín al estilo de un diario: cabezal con logo, nota destacada arriba a la derecha,
franja de fecha, banda "Hoy en esta edición", nota principal con titular gigante, nota lateral y
tres notas abajo. Cada nota lleva su etiqueta amarilla con la página donde se desarrolla."""
import json
import logging

from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics

log = logging.getLogger("monitor")

SYS_PORTADA = """Eres el editor de portada de "Altiplano al Día", diario de Puno (Perú).
Para cada noticia escribe textos de portada, fieles a los hechos, sin inventar nada y SIN nombrar
nunca al medio que difundió la información:
- "portada": titular de portada de 3 a 7 palabras, directo y con fuerza (verbo en activo,
  el dato más llamativo). Sin signos de exclamación ni sensacionalismo.
- "ante": antetítulo de 5 a 12 palabras que da contexto (quién o dónde).
- "puntos": SOLO para la noticia marcada como principal, 2 frases de 10 a 18 palabras con los
  datos clave; para las demás, lista vacía.
Responde SOLO JSON: {"noticias":[{"id":"n1","portada":"…","ante":"…","puntos":["…","…"]}]}"""


def textos_portada(cfg, arts):
    """Una sola llamada a Claude para los titulares de portada. Si falla, usa los de la nota."""
    out = {a["gid"]: {"portada": a["titular"], "ante": a["ante"].capitalize(), "puntos": []} for a in arts}
    principal_gid = arts[0]["gid"] if arts else None
    # textos de portada escritos o corregidos a mano en el editor: se respetan
    for a in arts:
        n = a["nota"]
        if n.get("PORTADA_TITULAR"):
            out[a["gid"]] = {"portada": n["PORTADA_TITULAR"], "ante": n.get("PORTADA_ANTE") or out[a["gid"]]["ante"],
                             "puntos": [x for x in (n.get("PORTADA_PUNTOS") or []) if x]}
    arts = [a for a in arts if not a["nota"].get("PORTADA_TITULAR")]
    if not arts:
        return out
    try:
        from .analisis import claude_json
        from .config import get_api_key
        if not get_api_key("claude"):
            return out
        mapa = {f"n{i + 1}": a for i, a in enumerate(arts)}
        user = json.dumps({"noticias": [
            {"id": k, "principal": a["gid"] == principal_gid, "titular": a["titular"], "antetitulo": a["ante"],
             "texto": " ".join(a["parrafos"])[:1800]} for i, (k, a) in enumerate(mapa.items())]},
            ensure_ascii=False)
        res = claude_json(cfg, SYS_PORTADA, user, f"portada[{len(arts)}]", 4000)
        for it in res.get("noticias", []):
            a = mapa.get(str(it.get("id")))
            if a and it.get("portada"):
                out[a["gid"]] = {"portada": it["portada"], "ante": it.get("ante") or out[a["gid"]]["ante"],
                                 "puntos": [p for p in it.get("puntos") or [] if p][:3]}
                nota = dict(a["nota"], PORTADA_TITULAR=out[a["gid"]]["portada"],
                            PORTADA_ANTE=out[a["gid"]]["ante"], PORTADA_PUNTOS=out[a["gid"]]["puntos"])
                from . import storage
                storage.actualizar_grupo(a["gid"], nota_json=nota)
    except Exception as e:  # noqa: BLE001
        log.warning("Portada sin textos de Claude: %s", e)
    return out


def elegir(arts):
    """Principal, lateral, destacada (arriba) y tres de abajo: primero las de más peso con foto."""
    con_foto = [a for a in arts if a["img"]]
    sin_foto = [a for a in arts if not a["img"]]
    rank = sorted(con_foto, key=_peso, reverse=True) + sorted(sin_foto, key=_peso, reverse=True)
    return rank[:6]


def _peso(a):
    return a["n_medios"] * 300 + min(a["palabras"], 400) + (100 if a["ambito"] in ("DISTRITAL", "LOCAL", "REGIONAL") else 0)


# ------------------------------------------------------------------ dibujo
def _ajustar(texto, font, ancho, alto, maxsize, minsize, maxlineas, interlinea=0.92):
    for size in range(int(maxsize), int(minsize) - 1, -1):
        lineas = simpleSplit(texto, font, size, ancho)
        if len(lineas) <= maxlineas and len(lineas) * size * interlinea <= alto:
            return size, lineas
    return minsize, simpleSplit(texto, font, minsize, ancho)[:maxlineas]


def _contorno(c, x, y, t, font, size, relleno, borde=colors.white, grosor=None, sombra=True):
    """Texto con borde blanco (y sombra suave), legible sobre fotos, como en los diarios."""
    grosor = grosor if grosor is not None else max(1.2, size * 0.09)
    c.saveState()
    if sombra:
        c.setFillColor(colors.Color(0, 0, 0, alpha=0.28))
        c.setFont(font, size)
        c.drawString(x + size * 0.04, y - size * 0.05, t)
        c.setFillAlpha(1)
    to = c.beginText(x, y)
    to.setFont(font, size)
    to.setTextRenderMode(1)
    c.setStrokeColor(borde)
    c.setLineWidth(grosor)
    c.setLineJoin(1)
    if grosor > 0:
        to.textOut(t)
        to.setTextRenderMode(0)             # el modo 1 persiste en el PDF: se restaura aquí
        c.drawText(to)
    relleno_txt = c.beginText(x, y)
    relleno_txt.setFont(font, size)
    relleno_txt.setTextRenderMode(0)        # vuelve a modo relleno (el modo 1 persiste)
    c.setFillColor(relleno)
    relleno_txt.textOut(t)
    c.drawText(relleno_txt)
    c.restoreState()


def _etiqueta(c, x_der, y, pag, k):
    """Etiqueta amarilla '<< PÁG. N' alineada a la derecha en x_der."""
    t = f"‹‹ PÁG. {pag}"
    c.setFont("Narrow-B", 9.5)
    w = pdfmetrics.stringWidth(t, "Narrow-B", 9.5) + 12
    c.setFillColor(k["AMARILLO"])
    c.rect(x_der - w, y, w, 15, stroke=0, fill=1)
    c.setFillColor(k["AZUL"])
    c.drawString(x_der - w + 6, y + 4, t)


def _foto(c, a, x, y, w, h, k):
    if a and a["img"]:
        c.drawImage(k["recorte"](a["img"], int(w), int(h)), x, y, w, h)
    else:
        c.setFillColor(colors.HexColor("#E7EEF9"))
        c.rect(x, y, w, h, stroke=0, fill=1)
        ico = k["icono"]("tema", a["tema"]) if a else None
        if ico:
            s = min(w, h) * 0.45
            c.drawImage(ico, x + (w - s) / 2, y + (h - s) / 2, s, s, mask="auto")


def _link(c, a, rect):
    c.linkRect("", f"g{a['gid']}", rect, relative=0, thickness=0)


def dibujar(c, cfg, arts_todos, paginas_de, numero, ahora, k):
    """k: dict con colores y helpers de consolidado (AZUL, AMARILLO, NEGRO, recorte, icono,
    logo, fecha_larga, x)."""
    AZUL, AMARILLO, NEGRO = k["AZUL"], k["AMARILLO"], k["NEGRO"]
    W, H = k["W"], k["H"]
    M = 20
    sel = elegir(arts_todos)
    if not sel:
        return
    tx = textos_portada(cfg, sel)
    pag = lambda a: paginas_de.get(a["gid"], "—")  # noqa: E731
    principal = sel[0]
    lateral = sel[1] if len(sel) > 1 else None
    destacada = sel[2] if len(sel) > 2 else None
    abajo = sel[3:6]

    # ---------------- cabezal
    top = H - 22
    cab_w, cab_h = 372, 118
    c.setFillColor(colors.white)
    c.rect(M, top - cab_h, cab_w, cab_h, stroke=0, fill=1)
    lw = cab_w - 40
    lh = lw * 297 / 999
    c.drawImage(k["logo"](), M + 4, top - 4 - lh, lw, lh, mask="auto")
    c.setFillColor(AZUL)
    c.setFont("Slab", 11)
    c.drawString(M + 6, top - cab_h + 6, "Noticias de Puno y la región")
    c.setStrokeColor(AZUL)
    c.setLineWidth(3)
    c.line(M, top - cab_h, M + cab_w, top - cab_h)

    # franja de fecha
    fy = top - cab_h - 16
    c.setFillColor(NEGRO)
    c.setFont("Narrow", 10.5)
    partes = [k["fecha_larga"](ahora), f"Boletín N° {numero}", f"Edición de las {ahora:%H:%M} h"]
    xs = [M, M + 150, M + 252]
    for i, (p, xx) in enumerate(zip(partes, xs)):
        c.drawString(xx + (6 if i else 0), fy, p)
        if i:
            c.setLineWidth(0.8)
            c.setStrokeColor(NEGRO)
            c.line(xx, fy - 3, xx, fy + 10)
    c.setStrokeColor(AZUL)
    c.setLineWidth(2)
    c.line(M, fy - 8, M + cab_w, fy - 8)

    # nota destacada (arriba a la derecha)
    dx0, dx1 = M + cab_w + 12, W - M
    if destacada:
        t = tx[destacada["gid"]]
        c.setFillColor(AZUL)
        ante = simpleSplit(k["plano"](t["ante"]), "Narrow-B", 10.5, dx1 - dx0)[:2]
        yy = top - 9
        for ln in ante:
            c.setFont("Narrow-B", 10.5)
            c.drawString(dx0, yy, ln)
            yy -= 11.5
        fb = fy - 8           # base de la caja de foto
        ftop = yy - 2
        _foto(c, destacada, dx0, fb, dx1 - dx0, ftop - fb, k)
        size, lineas = _ajustar(k["plano"](t["portada"]).upper(), "Slab", (dx1 - dx0) * 0.62,
                                ftop - fb - 26, 19, 11, 5, 1.0)
        y2 = ftop - size * 0.95 - 4
        for ln in lineas:
            _contorno(c, dx0 + 5, y2, ln, "Slab", size, NEGRO)
            y2 -= size * 1.0
        _etiqueta(c, dx1, fb + 2, pag(destacada), k)
        _link(c, destacada, (dx0, fb, dx1, top))
        c.setStrokeColor(NEGRO)
        c.setLineWidth(3)
        c.line(dx0, fy - 8, dx1, fy - 8)

    # ---------------- banda "en esta edición"
    by1 = fy - 18
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
    # cinta amarilla inclinada
    c.saveState()
    c.translate(M + 18, by0 + 10)
    c.rotate(7)
    c.setFillColor(AMARILLO)
    c.rect(0, 0, 118, 28, stroke=0, fill=1)
    c.setFillColor(AZUL)
    c.setFont("Slab", 13)
    c.drawCentredString(59, 9, "HOY EN PUNO")
    c.restoreState()
    n_tot = len(arts_todos)
    c.setFillColor(colors.white)
    c.setFont("Slab", 19)
    titulo = f"{n_tot} NOTICIAS "
    c.drawString(M + 160, by0 + 27, titulo)
    c.setFillColor(AMARILLO)
    c.drawString(M + 160 + pdfmetrics.stringWidth(titulo, "Slab", 19), by0 + 27, "AL DÍA")
    # conteo por ámbito con iconos
    xx = M + 160
    for amb in cfg["ambitos"]:
        n = sum(1 for a in arts_todos if a["ambito"] == amb)
        if not n:
            continue
        ico = k["icono"]("ambito", amb, True)
        if ico:
            c.drawImage(ico, xx, by0 + 8, 13, 13, mask="auto")
        c.setFillColor(colors.white)
        c.setFont("Narrow-B", 9.5)
        lab = f"{amb.title()} {n}"
        c.drawString(xx + 15, by0 + 11, lab)
        xx += 15 + pdfmetrics.stringWidth(lab, "Narrow-B", 9.5) + 12

    # ---------------- zona media
    mz1 = by0 - 12
    mz0 = 205
    lat_w = 168
    lx0, lx1 = M, M + lat_w
    rx0, rx1 = lx1 + 14, W - M
    c.setStrokeColor(NEGRO)
    c.setLineWidth(0.8)
    c.line(lx1 + 5, mz0, lx1 + 5, mz1)
    c.line(lx1 + 8, mz0, lx1 + 8, mz1)

    if lateral:
        t = tx[lateral["gid"]]
        yy = mz1 - 10
        c.setFillColor(AZUL)
        for ln in simpleSplit(k["plano"](t["ante"]), "Narrow-B", 11.5, lat_w)[:4]:
            c.setFont("Narrow-B", 11.5)
            c.drawString(lx0, yy, ln)
            yy -= 12.5
        yy -= 4
        size, lineas = _ajustar(k["plano"](t["portada"]).upper(), "Slab", lat_w, 150, 30, 15, 5, 0.95)
        for ln in lineas:
            yy -= size * 0.88
            c.setFillColor(NEGRO)
            c.setFont("Slab", size)
            c.drawString(lx0, yy, ln)
            yy -= size * 0.1
        yy -= 8
        _foto(c, lateral, lx0, mz0, lat_w, yy - mz0, k)
        _etiqueta(c, lx0 + 62, mz0 + 2, pag(lateral), k)
        _link(c, lateral, (lx0, mz0, lx1, mz1))

    # principal
    t = tx[principal["gid"]]
    yy = mz1 - 4
    ante = k["plano"](t["ante"]).upper()
    size_a, lin_a = _ajustar(ante, "Narrow-B", rx1 - rx0, 34, 16, 10, 2, 1.0)
    for ln in lin_a:
        yy -= size_a * 0.9
        _contorno(c, rx0, yy, ln, "Narrow-B", size_a, NEGRO, grosor=0, sombra=False)
        yy -= size_a * 0.15
    yy -= 4
    tit = k["plano"](t["portada"]).upper()
    palabras = tit.split()
    # dos líneas equilibradas: la 1.ª en azul, la 2.ª en negro, a todo el ancho
    if len(palabras) >= 2:
        mejor = min(((" ".join(palabras[:i]), " ".join(palabras[i:])) for i in range(1, len(palabras))),
                    key=lambda ab: max(pdfmetrics.stringWidth(ab[0], "Slab", 10), pdfmetrics.stringWidth(ab[1], "Slab", 10)))
        ancho10 = max(pdfmetrics.stringWidth(x, "Slab", 10) for x in mejor)
        size = min(70, (rx1 - rx0) / ancho10 * 10 * 0.98)
        lineas = list(mejor)
    else:
        size, lineas = _ajustar(tit, "Slab", rx1 - rx0, 140, 70, 24, 2)
    for i, ln in enumerate(lineas):
        yy -= size * 0.82
        _contorno(c, rx0, yy, ln, "Slab", size, AZUL if i == 0 else NEGRO)
        yy -= size * 0.12
    foto_top = yy - 4
    _foto(c, principal, rx0, mz0, rx1 - rx0, foto_top - mz0, k)
    # puntos clave sobre la foto
    puntos = [k["plano"](p) for p in t["puntos"]][:2] or [k["plano"](principal["nota"].get("BAJADA") or "")]
    py = mz0 + 26
    bloques = []
    for ptxt in reversed([p for p in puntos if p]):
        lineas_p = simpleSplit(ptxt, "Narrow-B", 13, (rx1 - rx0) * 0.62)[:3]
        bloques.append(lineas_p)
    for lineas_p in bloques:
        for j, ln in enumerate(reversed(lineas_p)):
            _contorno(c, rx0 + 22, py, ln, "Narrow-B", 13, NEGRO, grosor=2.6, sombra=False)
            if j == len(lineas_p) - 1:
                c.setFillColor(AMARILLO)
                c.setStrokeColor(AZUL)
                c.setLineWidth(1.5)
                c.circle(rx0 + 12, py + 4.5, 5.5, stroke=1, fill=1)
            py += 14.5
        py += 6
    _etiqueta(c, rx1, mz0 + 2, pag(principal), k)
    _link(c, principal, (rx0, mz0, rx1, mz1))

    # ---------------- fila inferior
    c.setStrokeColor(AMARILLO)
    c.setLineWidth(4)
    c.line(M, mz0 - 9, W - M, mz0 - 9)
    c.setStrokeColor(AZUL)
    c.setLineWidth(1)
    c.line(M, mz0 - 12.5, W - M, mz0 - 12.5)
    if abajo:
        n = len(abajo)
        gap = 12
        cw = (W - 2 * M - gap * (n - 1)) / n
        y1, y0 = mz0 - 22, 34
        for i, a in enumerate(abajo):
            t = tx[a["gid"]]
            x0 = M + i * (cw + gap)
            if i:
                c.setStrokeColor(AZUL)
                c.setLineWidth(0.8)
                c.line(x0 - gap / 2, y0, x0 - gap / 2, y1)
            c.setFillColor(AZUL)
            c.setFont("Narrow-B", 9.5)
            ante = simpleSplit(k["plano"](t["ante"]), "Narrow-B", 9.5, cw)[:2]
            for j, ln in enumerate(ante):
                c.drawString(x0, y1 - 9 - j * 10.5, ln)
            ft = y1 - 15 - 10.5 * max(0, len(ante) - 1)
            _foto(c, a, x0 + cw * 0.42, y0, cw * 0.58, ft - y0, k)
            size, lineas = _ajustar(k["plano"](t["portada"]).upper(), "Slab", cw * 0.92, ft - y0 - 22, 17, 10, 4, 1.0)
            yy = ft - size * 0.95
            for ln in lineas:
                _contorno(c, x0, yy, ln, "Slab", size, NEGRO)
                yy -= size
            _etiqueta(c, x0 + cw, y0 + 2, pag(a), k)
            _link(c, a, (x0, y0, x0 + cw, y1))
    # pie
    c.setFont("Narrow", 7)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(M, 20, "Altiplano al Día · boletín elaborado a partir del monitoreo de medios de la región")
    c.drawRightString(W - M, 20, f"Boletín N° {numero} · {ahora:%d/%m/%Y %H:%M}")
