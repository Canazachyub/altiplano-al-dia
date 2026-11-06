"""Portadas del formato tabloide. Tres diseños, uno por plantilla, para variar entre días:

- "tabloide": cabezal azul, gran foto con el titular gigante DEBAJO, sobre blanco; columna de tres
  llamadas con foto; franja inferior oscura con dos notas más y el índice de secciones.
- "foto": la foto principal a todo el ancho; el titular va en franjas de color sólido ("stickers")
  sobre la foto, sin velos ni difuminados; fila de tres tarjetas y franja amarilla con el índice.
- "mosaico": columna azul a la izquierda con el logo, el índice y dos titulares; foto principal con
  el titular en un bloque amarillo; tres mosaicos con una banda de color sólido para el titular.

Ninguna usa degradados ni transparencias sobre las fotos: el texto va sobre color pleno.
"""
from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics

from . import portada as PO

BLANCO = colors.white
NEGRO = colors.HexColor("#111111")


def _col_sec(a, k):
    from .maqueta import seccion_de
    s = seccion_de(a) or a["ambito"]
    return s, colors.HexColor(k["color_seccion"].get(s, "#37474F"))


def _indice(k):
    """[(sección, "5–9")] de las páginas de noticias."""
    out = {}
    for p in k["paginas"]:
        out.setdefault(p["seccion"], []).append(p["n"])
    return [(s, f"{min(v)}" if len(v) == 1 else f"{min(v)}–{max(v)}") for s, v in out.items()]


def _tag(c, x, y, texto, fondo, color=BLANCO, size=8.5, pad=5):
    w = pdfmetrics.stringWidth(texto, "P-Etq", size) + 2 * pad
    c.setFillColor(fondo)
    c.rect(x, y, w, size + 6, stroke=0, fill=1)
    c.setFillColor(color)
    c.setFont("P-Etq", size)
    c.drawString(x + pad, y + 3.6, texto)
    return w


def _pag(c, x_der, y, n, k, fondo=None):
    t = f"PÁG. {n}"
    w = pdfmetrics.stringWidth(t, "P-Etq", 9) + 10
    c.setFillColor(fondo or k["AMARILLO"])
    c.rect(x_der - w, y, w, 15, stroke=0, fill=1)
    c.setFillColor(k["AZUL"])
    c.setFont("P-Etq", 9)
    c.drawString(x_der - w + 5, y + 4.2, t)


def _lineas(c, lineas, font, size, x, y, color, inter=1.0):
    """Dibuja líneas hacia abajo desde y (borde superior). Devuelve la y final."""
    c.setFillColor(color)
    c.setFont(font, size)
    for ln in lineas:
        y -= size * 0.86
        c.drawString(x, y, ln)
        y -= size * (inter - 0.86)
    return y


def _titulo(texto, font, ancho, alto, maxs, mins, maxl):
    return PO._ajustar(texto, font, ancho, alto, maxs, mins, maxl, 1.0)


def _foto(c, a, x, y, w, h, k):
    PO._foto(c, a, x, y, w, h, k)


def _seleccion(cfg, arts):
    sel = PO.elegir(arts)
    if not sel:
        return [], {}
    if not sel.get("principal"):
        for lugar in ("lateral", "destacada", "abajo1", "abajo2", "abajo3"):
            if sel.get(lugar):
                sel["principal"] = sel.pop(lugar)
                break
    orden = [sel[x] for x in ("principal", "lateral", "destacada", "abajo1", "abajo2", "abajo3") if sel.get(x)]
    tx = PO.textos_portada(cfg, orden, orden[0]["gid"])
    return orden, tx


def _v(numero, ahora, n):
    return {"numero": numero, "fecha": f"{ahora:%d/%m/%Y}", "hora": f"{ahora:%H:%M}", "n": n}


# ------------------------------------------------------------------ 1. tabloide
def _tabloide(c, cfg, orden, tx, pag, numero, ahora, k):
    W, H, M = k["W"], k["H"], k["M"]
    AZUL, AMA = k["AZUL"], k["AMARILLO"]
    plano = k["plano"]
    v = _v(numero, ahora, len(orden))
    # cabezal
    top, ch = H - 18, 92
    c.setFillColor(AZUL)
    c.rect(M, top - ch, W - 2 * M, ch, stroke=0, fill=1)
    lh = ch - 18
    lw = lh * k["logo_prop"](True)
    k["dibujar_logo"](c, M + 12, top - ch + 9, lw, lh, blanco=True)
    c.setFillColor(BLANCO)
    c.setFont("P-EtqB", 13)
    c.drawRightString(W - M - 14, top - 30, k["fecha_larga"](ahora))
    c.setFillColor(AMA)
    c.setFont("P-Etq", 15)
    c.drawRightString(W - M - 14, top - 52, f"BOLETÍN N° {numero}")
    c.setFillColor(BLANCO)
    c.setFont("P-EtqM", 10)
    c.drawRightString(W - M - 14, top - 72, PO._t("lema", **v))
    c.setFillColor(AMA)
    c.rect(M, top - ch - 7, W - 2 * M, 7, stroke=0, fill=1)
    y_top = top - ch - 16
    pie = 118
    # columna izquierda: tres llamadas
    lw_col = 214
    lx = M
    rx0, rx1 = M + lw_col + 16, W - M
    c.setStrokeColor(NEGRO)
    c.setLineWidth(1)
    c.line(rx0 - 8, M + pie, rx0 - 8, y_top)
    llamadas = orden[1:4]
    if llamadas:
        alto_c = (y_top - (M + pie) - 10 * (len(llamadas) - 1)) / len(llamadas)
        y = y_top
        for a in llamadas:
            t = tx[a["gid"]]
            sec, col = _col_sec(a, k)
            _tag(c, lx, y - 15, sec, col)
            fh = alto_c * 0.50
            _foto(c, a, lx, y - 19 - fh, lw_col, fh, k)
            size, lin = _titulo(plano(t["portada"]), "P-Tit2", lw_col, alto_c - fh - 40, 19, 10, 4)
            _lineas(c, lin, "P-Tit2", size, lx, y - 25 - fh, NEGRO, 1.05)
            _pag(c, lx + lw_col, y - alto_c + 2, pag(a), k)
            PO._link(c, a, (lx, y - alto_c, lx + lw_col, y), "lateral")
            y -= alto_c + 10
            if a is not llamadas[-1]:
                c.setStrokeColor(colors.HexColor("#BBBBBB"))
                c.setLineWidth(0.6)
                c.line(lx, y + 5, lx + lw_col, y + 5)
    # principal: antetítulo en franja roja, foto limpia y titular gigante debajo, sobre blanco
    a = orden[0]
    t = tx[a["gid"]]
    sec, col = _col_sec(a, k)
    ante = plano(t["ante"]).upper()
    sa, la = _titulo(ante, "P-AnteXB", rx1 - rx0 - 12, 30, 15, 9, 1)
    c.setFillColor(col)
    c.rect(rx0, y_top - 22, rx1 - rx0, 22, stroke=0, fill=1)
    c.setFillColor(BLANCO)
    c.setFont("P-AnteXB", sa)
    c.drawString(rx0 + 6, y_top - 16, la[0] if la else "")
    size, lineas = PO._lineas_balanceadas(plano(t["portada"]).upper(), "P-Tit", rx1 - rx0, 66)
    alto_tit = len(lineas) * size * 0.98
    puntos = [plano(p) for p in t["puntos"]][:2]
    alto_p = 0
    for p in puntos:
        alto_p += len(simpleSplit(p, "P-Pun", 12.5, rx1 - rx0 - 16)) * 14 + 4
    foto_bot = M + pie + alto_tit + alto_p + 14
    _foto(c, a, rx0, foto_bot, rx1 - rx0, (y_top - 26) - foto_bot, k)
    _pag(c, rx1, foto_bot + 4, pag(a), k)
    y = foto_bot - 4
    for i, ln in enumerate(lineas):
        y -= size * 0.86
        c.setFillColor(col if i == 0 else NEGRO)
        c.setFont("P-Tit", size)
        c.drawString(rx0, y, ln)
        y -= size * 0.12
    y -= 4
    for p in puntos:
        c.setFillColor(k["ROJO"])
        c.rect(rx0, y - 9, 7, 7, stroke=0, fill=1)
        y = _lineas(c, simpleSplit(p, "P-Pun", 12.5, rx1 - rx0 - 16), "P-Pun", 12.5, rx0 + 14, y + 2, NEGRO, 1.12)
        y -= 4
    PO._link(c, a, (rx0, M + pie, rx1, y_top), "principal")
    # franja inferior oscura: dos notas más + índice
    _franja_inferior(c, orden[4:6], tx, pag, k, M, M, W - 2 * M, pie - 8)


def _franja_inferior(c, notas, tx, pag, k, x, y, w, h):
    plano = k["plano"]
    c.setFillColor(colors.HexColor("#0B1F44"))
    c.rect(x, y, w, h, stroke=0, fill=1)
    ind_w = 210
    xx = x + 10
    ancho_n = (w - ind_w - 20 - 10 * max(0, len(notas) - 1)) / max(1, len(notas)) if notas else 0
    for a in notas:
        t = tx[a["gid"]]
        sec, col = _col_sec(a, k)
        fw = min(110, ancho_n * 0.42)
        _foto(c, a, xx, y + 10, fw, h - 20, k)
        _tag(c, xx + fw + 8, y + h - 24, sec, col)
        size, lin = _titulo(plano(t["portada"]), "P-Tit2", ancho_n - fw - 8, h - 50, 17, 9, 3)
        _lineas(c, lin, "P-Tit2", size, xx + fw + 8, y + h - 30, BLANCO, 1.05)
        _pag(c, xx + ancho_n, y + 10, pag(a), k)
        PO._link(c, a, (xx, y, xx + ancho_n, y + h), "abajo")
        xx += ancho_n + 10
    # índice de secciones
    ix = x + w - ind_w
    c.setFillColor(k["AMARILLO"])
    c.rect(ix, y, ind_w, h, stroke=0, fill=1)
    c.setFillColor(k["AZUL"])
    c.setFont("P-Banda", 13)
    c.drawString(ix + 10, y + h - 20, "EN ESTA EDICIÓN")
    filas = _indice(k)
    paso = (h - 30) / max(1, (len(filas) + 1) // 2)
    for i, (s, r) in enumerate(filas):
        cx = ix + 10 + (i % 2) * (ind_w / 2 - 4)
        cy = y + h - 36 - (i // 2) * paso
        c.setFont("P-EtqB", 9.5)
        c.setFillColor(NEGRO)
        c.drawString(cx, cy, s.capitalize())
        c.setFillColor(k["AZUL"])
        c.setFont("P-Etq", 9.5)
        c.drawRightString(cx + ind_w / 2 - 16, cy, r)


# ------------------------------------------------------------------ 2. foto
def _sticker(c, lineas, font, size, x, y_base, fondos, textos, pad=6):
    """Titular en franjas sólidas, una por línea, desde y_base hacia arriba (la última línea abajo)."""
    alto = size * 1.12
    for i, ln in enumerate(reversed(lineas)):
        j = len(lineas) - 1 - i
        w = pdfmetrics.stringWidth(ln, font, size) + 2 * pad
        yy = y_base + i * (alto + 3)
        c.setFillColor(fondos[j % len(fondos)])
        c.rect(x, yy, w, alto, stroke=0, fill=1)
        c.setFillColor(textos[j % len(textos)])
        c.setFont(font, size)
        c.drawString(x + pad, yy + size * 0.2, ln)
    return y_base + len(lineas) * (alto + 3)


def _foto_portada(c, cfg, orden, tx, pag, numero, ahora, k):
    W, H, M = k["W"], k["H"], k["M"]
    AZUL, AMA = k["AZUL"], k["AMARILLO"]
    plano = k["plano"]
    v = _v(numero, ahora, len(orden))
    # cabezal claro y centrado
    top = H - 16
    lh = 62
    lw = lh * k["logo_prop"](False)
    k["dibujar_logo"](c, (W - lw) / 2, top - lh, lw, lh)
    c.setStrokeColor(AZUL)
    c.setLineWidth(2.2)
    c.line(M, top - lh - 8, W - M, top - lh - 8)
    c.setLineWidth(0.7)
    c.line(M, top - lh - 12, W - M, top - lh - 12)
    c.setFont("P-EtqB", 10)
    c.setFillColor(NEGRO)
    c.drawString(M, top - lh - 26, k["fecha_larga"](ahora).upper())
    c.drawCentredString(W / 2, top - lh - 26, PO._t("lema", **v))
    c.setFillColor(AZUL)
    c.drawRightString(W - M, top - lh - 26, f"BOLETÍN N° {numero}")
    y_top = top - lh - 34
    # foto principal a todo el ancho con el titular en franjas sólidas
    a = orden[0]
    t = tx[a["gid"]]
    sec, col = _col_sec(a, k)
    fb = 300
    _foto(c, a, M, fb, W - 2 * M, y_top - fb, k)
    size, lineas = PO._ajustar(plano(t["portada"]).upper(), "P-Tit", (W - 2 * M) * 0.80, 170, 50, 20, 3, 1.1)
    y = _sticker(c, lineas, "P-Tit", size, M + 14, fb + 16, [AMA, NEGRO, AMA], [NEGRO, BLANCO, NEGRO])
    ante = plano(t["ante"]).upper()
    sa, la = PO._ajustar(ante, "P-AnteXB", (W - 2 * M) * 0.7, 20, 12, 8, 1, 1.0)
    _tag(c, M + 14, y + 4, la[0] if la else sec, col, size=sa)
    _pag(c, W - M - 6, fb + 8, pag(a), k)
    PO._link(c, a, (M, fb, W - M, y_top), "principal")
    # tarjetas: foto arriba, sección y titular debajo
    tarjetas = orden[1:4]
    franja = 64
    y1, y0 = fb - 12, M + franja + 10
    if tarjetas:
        n = len(tarjetas)
        gap = 14
        cw = (W - 2 * M - gap * (n - 1)) / n
        for i, a in enumerate(tarjetas):
            t = tx[a["gid"]]
            sec, col = _col_sec(a, k)
            x = M + i * (cw + gap)
            fh = (y1 - y0) * 0.52
            _foto(c, a, x, y1 - fh, cw, fh, k)
            c.setFillColor(col)
            c.rect(x, y1 - fh - 4, cw, 4, stroke=0, fill=1)
            c.setFillColor(col)
            c.setFont("P-Etq", 9)
            c.drawString(x, y1 - fh - 17, sec)
            size, lin = _titulo(plano(t["portada"]), "P-Tit2", cw, y1 - fh - 22 - y0 - 18, 21, 10, 4)
            _lineas(c, lin, "P-Tit2", size, x, y1 - fh - 22, NEGRO, 1.05)
            _pag(c, x + cw, y0, pag(a), k)
            PO._link(c, a, (x, y0, x + cw, y1), "abajo")
    # franja amarilla con más titulares e índice
    c.setFillColor(AMA)
    c.rect(M, M, W - 2 * M, franja, stroke=0, fill=1)
    c.setFillColor(AZUL)
    c.rect(M, M, 96, franja, stroke=0, fill=1)
    c.setFillColor(AMA)
    c.setFont("P-Banda", 15)
    c.drawCentredString(M + 48, M + franja / 2 + 3, "TAMBIÉN")
    c.setFont("P-EtqB", 8.5)
    c.drawCentredString(M + 48, M + franja / 2 - 10, "EN ESTA EDICIÓN")
    otras = orden[4:6]
    xx = M + 108
    ancho_o = (W - 2 * M - 108 - 230) / max(1, len(otras)) if otras else 0
    for a in otras:
        t = tx[a["gid"]]
        size, lin = _titulo(plano(t["portada"]), "P-Tit2", ancho_o - 46, franja - 12, 14, 8, 3)
        _lineas(c, lin, "P-Tit2", size, xx, M + franja - 8, NEGRO, 1.04)
        c.setFillColor(AZUL)
        c.setFont("P-Etq", 11)
        c.drawRightString(xx + ancho_o - 10, M + 8, f"{pag(a)}")
        PO._link(c, a, (xx, M, xx + ancho_o, M + franja), "abajo")
        xx += ancho_o
    ix = W - M - 226
    filas = _indice(k)
    ncol = 3
    paso_y = (franja - 14) / max(1, -(-len(filas) // ncol))
    for i, (s, r) in enumerate(filas):
        cx = ix + (i % ncol) * 75
        cy = M + franja - 15 - (i // ncol) * paso_y
        nombre = s.capitalize() if len(s) <= 10 else s.capitalize()[:8] + "."
        c.setFont("P-EtqB", 8)
        c.setFillColor(NEGRO)
        c.drawString(cx, cy, nombre)
        c.setFillColor(AZUL)
        c.setFont("P-Etq", 8)
        c.drawRightString(cx + 70, cy, r)


# ------------------------------------------------------------------ 3. mosaico
def _mosaico(c, cfg, orden, tx, pag, numero, ahora, k):
    W, H, M = k["W"], k["H"], k["M"]
    AZUL, AMA = k["AZUL"], k["AMARILLO"]
    plano = k["plano"]
    v = _v(numero, ahora, len(orden))
    # columna azul con el logo, la fecha, el índice y dos titulares
    cw = 168
    c.setFillColor(AZUL)
    c.rect(M, M, cw, H - 2 * M + 4, stroke=0, fill=1)
    lw = cw - 20
    lh = lw / k["logo_prop"](True)
    top = H - M + 4
    k["dibujar_logo"](c, M + 10, top - 12 - lh, lw, lh, blanco=True)
    y = top - 22 - lh
    c.setFillColor(AMA)
    c.rect(M + 10, y - 2, lw, 3, stroke=0, fill=1)
    c.setFillColor(BLANCO)
    c.setFont("P-EtqB", 10)
    c.drawString(M + 10, y - 17, k["fecha_larga"](ahora))
    c.setFillColor(AMA)
    c.setFont("P-Etq", 13)
    c.drawString(M + 10, y - 34, f"BOLETÍN N° {numero}")
    c.setFillColor(BLANCO)
    lema = simpleSplit(PO._t("lema", **v), "P-EtqM", 8.5, lw)
    yy = y - 48
    for ln in lema:
        c.setFont("P-EtqM", 8.5)
        c.drawString(M + 10, yy, ln)
        yy -= 10
    yy -= 10
    c.setFillColor(AMA)
    c.setFont("P-Banda", 13)
    c.drawString(M + 10, yy, "EN ESTA EDICIÓN")
    yy -= 8
    for s, r in _indice(k):
        yy -= 15
        c.setFillColor(BLANCO)
        c.setFont("P-EtqB", 10)
        c.drawString(M + 10, yy, s.capitalize())
        c.setFillColor(AMA)
        c.setFont("P-Etq", 10)
        c.drawRightString(M + 10 + lw, yy, r)
    yy -= 22
    for a in orden[4:6]:
        t = tx[a["gid"]]
        sec, col = _col_sec(a, k)
        if yy < M + 90:
            break
        _tag(c, M + 10, yy - 13, sec, col)
        size, lin = _titulo(plano(t["portada"]), "P-Tit2", lw, yy - M - 50, 16, 9, 5)
        y2 = _lineas(c, lin, "P-Tit2", size, M + 10, yy - 18, BLANCO, 1.06)
        _pag(c, M + 10 + lw, y2 - 18, pag(a), k)
        PO._link(c, a, (M, y2 - 20, M + cw, yy), "abajo")
        yy = y2 - 32
    # zona derecha
    x0, x1 = M + cw + 14, W - M
    y_top = H - M + 4
    a = orden[0]
    t = tx[a["gid"]]
    sec, col = _col_sec(a, k)
    ante = plano(t["ante"]).upper()
    sa, la = PO._ajustar(ante, "P-AnteXB", x1 - x0, 20, 14, 8, 1, 1.0)
    c.setFillColor(col)
    c.setFont("P-AnteXB", sa)
    c.drawString(x0, y_top - sa, la[0] if la else sec)
    size, lineas = PO._lineas_balanceadas(plano(t["portada"]).upper(), "P-Tit", x1 - x0 - 24, 58)
    bloque = len(lineas) * size * 0.98 + 22
    mos_h = 196
    fb = M + mos_h + 14 + bloque
    _foto(c, a, x0, fb, x1 - x0, (y_top - sa - 8) - fb, k)
    c.setFillColor(AMA)
    c.rect(x0, fb - bloque, x1 - x0, bloque, stroke=0, fill=1)
    c.setFillColor(col)
    c.rect(x0, fb - bloque, 8, bloque, stroke=0, fill=1)
    y = fb - 10
    for ln in lineas:
        y -= size * 0.86
        c.setFillColor(NEGRO)
        c.setFont("P-Tit", size)
        c.drawString(x0 + 16, y, ln)
        y -= size * 0.12
    _pag(c, x1 - 4, fb + 6, pag(a), k, fondo=BLANCO)
    PO._link(c, a, (x0, fb - bloque, x1, y_top), "principal")
    # mosaicos: foto entera con banda sólida del color de la sección para el titular
    tiles = orden[1:4]
    if tiles:
        n = len(tiles)
        gap = 10
        tw = (x1 - x0 - gap * (n - 1)) / n
        for i, a in enumerate(tiles):
            t = tx[a["gid"]]
            sec, col = _col_sec(a, k)
            x = x0 + i * (tw + gap)
            _foto(c, a, x, M, tw, mos_h, k)
            size, lin = _titulo(plano(t["portada"]), "P-Tit2", tw - 16, 70, 17, 9, 4)
            bh = len(lin) * size * 1.05 + 24
            c.setFillColor(col)
            c.rect(x, M, tw, bh, stroke=0, fill=1)
            c.setFillColor(BLANCO)
            c.setFont("P-Etq", 8)
            c.drawString(x + 8, M + bh - 12, sec)
            _lineas(c, lin, "P-Tit2", size, x + 8, M + bh - 15, BLANCO, 1.05)
            _pag(c, x + tw, M + mos_h - 17, pag(a), k)
            PO._link(c, a, (x, M, x + tw, M + mos_h), "abajo")


DISENOS = {"tabloide": _tabloide, "foto": _foto_portada, "mosaico": _mosaico}


def dibujar(c, cfg, arts, paginas_de, numero, ahora, diseno, k):
    orden, tx = _seleccion(cfg, arts)
    if not orden:
        return
    pag = lambda a: paginas_de.get(a["gid"], "—")  # noqa: E731
    DISENOS.get(diseno, _tabloide)(c, cfg, orden, tx, pag, numero, ahora, k)
