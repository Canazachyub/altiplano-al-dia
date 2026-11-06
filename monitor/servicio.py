"""Páginas de servicio de la edición: clasificados, guía útil, entretenimiento y pupiletras.

Todo es contenido propio y verificable: los clasificados salen de data/clasificados.json (los que
cargue el equipo; si no hay, la página invita a publicar por WhatsApp), los juegos se generan cada
día (sudokus con solución única, laberinto y pupiletras con palabras de las noticias de la edición)
y las soluciones van en la página siguiente o en la misma, al pie.
"""
import json
import re
import unicodedata

from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit

from . import avisos
from .config import BASE_DIR

CLASIFICADOS_PATH = BASE_DIR / "data" / "clasificados.json"
CATEGORIAS = ["INMUEBLES", "VEHÍCULOS", "EMPLEOS", "SERVICIOS", "ALQUILERES", "VARIOS"]
NEGRO = colors.HexColor("#111111")
GRIS = colors.HexColor("#666666")
_SOL = {}            # soluciones de los juegos de esta edición (para imprimirlas en pupiletras)


def leer_clasificados() -> list:
    try:
        d = json.loads(CLASIFICADOS_PATH.read_text(encoding="utf-8"))
        return [x for x in d if isinstance(x, dict) and (x.get("texto") or "").strip()]
    except Exception:  # noqa: BLE001
        return []


def _whatsapp():
    d = avisos.cargar()
    for a in d["avisos"]:
        if (a.get("telefono") or "").strip():
            return a["telefono"].strip()
    return avisos.WHATSAPP


def _titulo_bloque(c, x, y, w, texto, color, size=14):
    c.setFillColor(color)
    c.rect(x, y - size - 8, w, size + 8, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("P-Banda", size)
    c.drawString(x + 8, y - size - 2.5, texto)
    return y - size - 14


# ------------------------------------------------------------------ clasificados
def _clasificados(c, area, k):
    x0, y0, x1, y1 = area
    w = x1 - x0
    tel = _whatsapp()
    AZUL, AMA = k["AZUL"], k["AMARILLO"]
    # cabeza: llamado a publicar
    hh = 92
    c.setFillColor(AMA)
    c.rect(x0, y1 - hh, w, hh, stroke=0, fill=1)
    c.setFillColor(AZUL)
    c.setFont("P-Banda", 34)
    c.drawString(x0 + 18, y1 - 44, "CLASIFICADOS")
    c.setFont("P-EtqB", 13)
    c.setFillColor(NEGRO)
    c.drawString(x0 + 20, y1 - 66, "Compra, vende, alquila u ofrece empleo. Publica tu aviso por WhatsApp:")
    c.setFillColor(AZUL)
    c.roundRect(x1 - 250, y1 - hh + 18, 232, 52, 6, stroke=0, fill=1)
    c.setFillColor(AMA)
    c.setFont("P-Banda", 25)
    c.drawCentredString(x1 - 134, y1 - hh + 37, tel)
    c.setFillColor(colors.white)
    c.setFont("P-EtqB", 9)
    c.drawCentredString(x1 - 134, y1 - hh + 24, "WHATSAPP · LLAMADAS")
    datos = leer_clasificados()
    # grilla de categorías (3 × 2)
    gy1 = y1 - hh - 14
    gw = (w - 2 * 14) / 3
    gh = (gy1 - y0 - 14) / 2
    for i, cat in enumerate(CATEGORIAS):
        cx = x0 + (i % 3) * (gw + 14)
        cy1 = gy1 - (i // 3) * (gh + 14)
        y = _titulo_bloque(c, cx, cy1, gw, cat, AZUL, 13)
        items = [d for d in datos if (d.get("categoria") or "").upper() == cat]
        c.setStrokeColor(colors.HexColor("#CCCCCC"))
        c.setLineWidth(0.6)
        c.rect(cx, cy1 - gh, gw, gh, stroke=1, fill=0)
        if items:
            for it in items:
                lineas = simpleSplit(it["texto"].strip(), "Serif", 9, gw - 16)
                need = len(lineas) * 10.5 + 16
                if y - need < cy1 - gh + 6:
                    break
                for ln in lineas:
                    c.setFont("Serif", 9)
                    c.setFillColor(NEGRO)
                    c.drawString(cx + 8, y - 9, ln)
                    y -= 10.5
                if it.get("telefono"):
                    c.setFont("Narrow-B", 9.5)
                    c.setFillColor(AZUL)
                    c.drawString(cx + 8, y - 10, f"Tel. {it['telefono']}")
                    y -= 12
                c.setStrokeColor(colors.HexColor("#DDDDDD"))
                c.line(cx + 8, y - 3, cx + gw - 8, y - 3)
                y -= 8
        else:
            # espacio libre: invitación a publicar en esta categoría
            cyc = cy1 - gh / 2 - 6
            c.setFillColor(colors.HexColor("#F6F6F6"))
            c.rect(cx + 1, cy1 - gh + 1, gw - 2, gh - 30, stroke=0, fill=1)
            c.setFillColor(GRIS)
            c.setFont("P-EtqB", 12)
            c.drawCentredString(cx + gw / 2, cyc + 10, "PUBLICA AQUÍ TU AVISO")
            c.setFont("P-EtqM", 10)
            c.drawCentredString(cx + gw / 2, cyc - 6, cat.capitalize())
            c.setFillColor(AZUL)
            c.setFont("P-Etq", 15)
            c.drawCentredString(cx + gw / 2, cyc - 28, tel)


def _guia(c, area, k, D):
    """Segunda página de clasificados: guía de emergencias y avisos grandes de la casa."""
    x0, y0, x1, y1 = area
    AZUL = k["AZUL"]
    gw = 250
    y = _titulo_bloque(c, x0, y1, gw, "GUÍA DE EMERGENCIAS", k["ROJO"], 14)
    numeros = [("Policía Nacional", "105"), ("Bomberos", "116"), ("SAMU (emergencias médicas)", "106"),
               ("Línea 100 (violencia familiar)", "100"), ("Salud (MINSA)", "113")]
    for nombre, num in numeros:
        c.setFillColor(colors.HexColor("#F4F4F4"))
        c.rect(x0, y - 34, gw, 30, stroke=0, fill=1)
        c.setFillColor(NEGRO)
        c.setFont("P-EtqB", 11)
        c.drawString(x0 + 10, y - 23, nombre)
        c.setFillColor(k["ROJO"])
        c.setFont("P-Banda", 18)
        c.drawRightString(x0 + gw - 10, y - 25, num)
        y -= 36
    y -= 10
    y = _titulo_bloque(c, x0, y, gw, "ESCRÍBENOS", AZUL, 14)
    tel = _whatsapp()
    for txt in ("¿Tienes una denuncia o una noticia?", "¿Quieres publicar un aviso?",
                "Envíanos fotos, videos y datos por WhatsApp:"):
        c.setFillColor(NEGRO)
        c.setFont("Serif", 10.5)
        c.drawString(x0 + 4, y - 12, txt)
        y -= 16
    c.setFillColor(AZUL)
    c.setFont("P-Banda", 24)
    c.drawString(x0 + 4, y - 30, tel)
    # a la derecha: avisos grandes de la casa (los que tengan número), uno debajo de otro
    ax0 = x0 + gw + 18
    lista = [a for a in avisos.cargar()["avisos"] if avisos.listo(a) and a.get("tipo") == "texto"]
    if lista:
        n = min(3, len(lista))
        ah = (y1 - y0 - 12 * (n - 1)) / n
        for i, a in enumerate(lista[:n]):
            ytop = y1 - i * (ah + 12)
            avisos.dibujar(c, a, (ax0, ytop - ah, x1, ytop), D)


# ------------------------------------------------------------------ sudoku
def _sudoku_completo(rnd):
    base = 3
    lado = 9

    def patron(r, c):
        return (base * (r % base) + r // base + c) % lado

    filas = [g * base + r for g in rnd.sample(range(base), base) for r in rnd.sample(range(base), base)]
    cols = [g * base + c for g in rnd.sample(range(base), base) for c in rnd.sample(range(base), base)]
    nums = rnd.sample(range(1, 10), 9)
    return [[nums[patron(r, c)] for c in cols] for r in filas]


def _soluciones(tab, limite=2):
    cnt = [0]

    def ok(r, c, v):
        if any(tab[r][j] == v for j in range(9)) or any(tab[i][c] == v for i in range(9)):
            return False
        br, bc = r // 3 * 3, c // 3 * 3
        return all(tab[br + i][bc + j] != v for i in range(3) for j in range(3))

    def resolver():
        for r in range(9):
            for c in range(9):
                if tab[r][c] == 0:
                    for v in range(1, 10):
                        if ok(r, c, v):
                            tab[r][c] = v
                            resolver()
                            tab[r][c] = 0
                            if cnt[0] >= limite:
                                return
                    return
        cnt[0] += 1

    resolver()
    return cnt[0]


def sudoku(rnd, pistas=30):
    sol = _sudoku_completo(rnd)
    tab = [fila[:] for fila in sol]
    celdas = [(r, c) for r in range(9) for c in range(9)]
    rnd.shuffle(celdas)
    quedan = 81
    for r, c in celdas:
        if quedan <= pistas:
            break
        v = tab[r][c]
        tab[r][c] = 0
        prueba = [f[:] for f in tab]
        if _soluciones(prueba) != 1:
            tab[r][c] = v
        else:
            quedan -= 1
    return tab, sol


def _dibujar_sudoku(c, x, y_top, lado, tab, k, chico=False):
    cel = lado / 9
    c.setFillColor(colors.white)
    c.rect(x, y_top - lado, lado, lado, stroke=0, fill=1)
    for i in range(10):
        c.setStrokeColor(NEGRO)
        c.setLineWidth(1.8 if i % 3 == 0 else 0.5)
        c.line(x + i * cel, y_top, x + i * cel, y_top - lado)
        c.line(x, y_top - i * cel, x + lado, y_top - i * cel)
    size = cel * 0.58
    for r in range(9):
        for col in range(9):
            v = tab[r][col]
            if v:
                c.setFillColor(k["AZUL"] if not chico else NEGRO)
                c.setFont("P-Etq", size)
                c.drawCentredString(x + col * cel + cel / 2, y_top - r * cel - cel / 2 - size * 0.35, str(v))


# ------------------------------------------------------------------ laberinto
def laberinto(rnd, n=16):
    """Paredes de un laberinto perfecto (backtracking). paredes[(r,c)] = {"N","S","E","O"}."""
    paredes = {(r, c): {"N", "S", "E", "O"} for r in range(n) for c in range(n)}
    visto = {(0, 0)}
    pila = [(0, 0)]
    dirs = {"N": (-1, 0, "S"), "S": (1, 0, "N"), "E": (0, 1, "O"), "O": (0, -1, "E")}
    while pila:
        r, c = pila[-1]
        opciones = [(d, r + dr, c + dc, op) for d, (dr, dc, op) in dirs.items()
                    if 0 <= r + dr < n and 0 <= c + dc < n and (r + dr, c + dc) not in visto]
        if not opciones:
            pila.pop()
            continue
        d, nr, nc, op = rnd.choice(opciones)
        paredes[(r, c)].discard(d)
        paredes[(nr, nc)].discard(op)
        visto.add((nr, nc))
        pila.append((nr, nc))
    paredes[(0, 0)].discard("O")
    paredes[(n - 1, n - 1)].discard("E")
    return paredes, n


def _dibujar_laberinto(c, x, y_top, lado, lab, k):
    paredes, n = lab
    cel = lado / n
    c.setStrokeColor(NEGRO)
    c.setLineWidth(1.6)
    c.setLineCap(1)
    for (r, col), ps in paredes.items():
        xx, yy = x + col * cel, y_top - r * cel
        if "N" in ps:
            c.line(xx, yy, xx + cel, yy)
        if "S" in ps:
            c.line(xx, yy - cel, xx + cel, yy - cel)
        if "O" in ps:
            c.line(xx, yy, xx, yy - cel)
        if "E" in ps:
            c.line(xx + cel, yy, xx + cel, yy - cel)
    c.setFillColor(k["ROJO"])
    c.circle(x - 8, y_top - cel / 2, 4, stroke=0, fill=1)
    c.setFillColor(k["AZUL"])
    c.circle(x + lado + 8, y_top - lado + cel / 2, 4, stroke=0, fill=1)


def _entretenimiento(c, area, k, rnd):
    x0, y0, x1, y1 = area
    w = x1 - x0
    col_w = (w - 24) / 2
    s1, sol1 = sudoku(rnd, 34)
    s2, sol2 = sudoku(rnd, 27)
    _SOL["sudoku_facil"], _SOL["sudoku_dificil"] = sol1, sol2
    lado = min(col_w - 20, (y1 - y0) / 2 - 60)
    # sudoku fácil
    y = _titulo_bloque(c, x0, y1, col_w, "SUDOKU · NIVEL FÁCIL", k["AZUL"], 14)
    c.setFillColor(GRIS)
    c.setFont("Serif-I", 9.5)
    c.drawString(x0, y - 4, "Completa la grilla: cada fila, columna y cuadro de 3 × 3 lleva los números del 1 al 9.")
    _dibujar_sudoku(c, x0 + (col_w - lado) / 2, y - 14, lado, s1, k)
    # sudoku difícil
    xr = x0 + col_w + 24
    y = _titulo_bloque(c, xr, y1, col_w, "SUDOKU · NIVEL DIFÍCIL", k["ROJO"], 14)
    c.setFillColor(GRIS)
    c.setFont("Serif-I", 9.5)
    c.drawString(xr, y - 4, "Las soluciones de hoy están en la página siguiente.")
    _dibujar_sudoku(c, xr + (col_w - lado) / 2, y - 14, lado, s2, k)
    # laberinto
    ym = y1 - lado - 70
    y = _titulo_bloque(c, x0, ym, col_w, "LABERINTO", colors.HexColor("#AD1457"), 14)
    lad = min(col_w - 40, y - y0 - 20)
    c.setFillColor(GRIS)
    c.setFont("Serif-I", 9.5)
    c.drawString(x0, y - 4, "Entra por el punto rojo y encuentra la salida azul.")
    _dibujar_laberinto(c, x0 + (col_w - lad) / 2, y - 16, lad, laberinto(rnd, 16), k)
    # aviso de la casa
    lista = [a for a in avisos.cargar()["avisos"] if avisos.listo(a) and a.get("tipo") == "texto"]
    if lista:
        a = lista[rnd.randrange(len(lista))]
        avisos.dibujar(c, a, (xr, y0, x1, ym - 4), k["D"])


# ------------------------------------------------------------------ pupiletras
_VACIAS = set("PARA DESDE ENTRE SOBRE ANTE TRAS CONTRA SEGUN DURANTE MEDIANTE HASTA COMO PERO TAMBIEN "
              "ESTE ESTA ESTOS ESTAS ESOS ESAS AQUEL SIDO HABER TIENE TIENEN SERA SERAN FUE FUERON "
              "PUEDE PUEDEN DONDE CUANDO TODO TODOS TODAS NUEVO NUEVA NUEVOS NUEVAS".split())


def _palabras_de(arts, rnd, n=14):
    cuenta = {}
    for a in arts:
        for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", a["titular"]):
            p = unicodedata.normalize("NFKD", w.upper()).encode("ascii", "ignore").decode()
            p = p.replace("N~", "N")
            if 5 <= len(p) <= 11 and p not in _VACIAS:
                cuenta[p] = cuenta.get(p, 0) + (2 if w[0].isupper() else 1)
    orden = sorted(cuenta, key=lambda p: (-cuenta[p], p))
    elegidas = orden[:n * 2]
    rnd.shuffle(elegidas)
    return sorted(elegidas[:n], key=len, reverse=True)


def pupiletras(palabras, rnd, n=15):
    tab = [[""] * n for _ in range(n)]
    dirs = [(0, 1), (1, 0), (1, 1), (-1, 1), (0, -1), (1, -1)]
    puestas = []
    for p in palabras:
        for _ in range(300):
            dr, dc = rnd.choice(dirs)
            r, c = rnd.randrange(n), rnd.randrange(n)
            rr, cc = r + dr * (len(p) - 1), c + dc * (len(p) - 1)
            if not (0 <= rr < n and 0 <= cc < n):
                continue
            if all(tab[r + dr * i][c + dc * i] in ("", p[i]) for i in range(len(p))):
                for i in range(len(p)):
                    tab[r + dr * i][c + dc * i] = p[i]
                puestas.append(p)
                break
    letras = "AEIOUAEIOSRNLTCDMPBGVHQ"
    for r in range(n):
        for c in range(n):
            if not tab[r][c]:
                tab[r][c] = rnd.choice(letras)
    return tab, puestas


def _pupiletras(c, area, k, rnd, arts):
    x0, y0, x1, y1 = area
    w = x1 - x0
    pal = _palabras_de(arts, rnd)
    tab, puestas = pupiletras(pal, rnd)
    n = len(tab)
    lado = min(w * 0.6, y1 - y0 - 50)
    y = _titulo_bloque(c, x0, y1, lado, "PUPILETRAS DE LA EDICIÓN", k["AZUL"], 14)
    c.setFillColor(GRIS)
    c.setFont("Serif-I", 9.5)
    c.drawString(x0, y - 4, "Encuentra las palabras de las noticias de hoy: en horizontal, vertical y diagonal.")
    cel = lado / n
    gy = y - 14
    c.setFillColor(colors.HexColor("#FFFBE0"))
    c.rect(x0, gy - lado, lado, lado, stroke=0, fill=1)
    c.setStrokeColor(k["AZUL"])
    c.setLineWidth(1.4)
    c.rect(x0, gy - lado, lado, lado, stroke=1, fill=0)
    for r in range(n):
        for col in range(n):
            c.setFillColor(NEGRO)
            c.setFont("P-Etq", cel * 0.55)
            c.drawCentredString(x0 + col * cel + cel / 2, gy - r * cel - cel * 0.68, tab[r][col])
    # lista de palabras
    lx = x0 + lado + 22
    lw = x1 - lx
    y = _titulo_bloque(c, lx, y1, lw, "BUSCA", k["ROJO"], 14)
    for p in puestas:
        c.setStrokeColor(k["AZUL"])
        c.setLineWidth(1)
        c.rect(lx + 4, y - 14, 9, 9, stroke=1, fill=0)
        c.setFillColor(NEGRO)
        c.setFont("P-EtqB", 12)
        c.drawString(lx + 20, y - 14, p)
        y -= 19
    # soluciones de los sudokus de la página anterior
    y -= 14
    for clave, nombre in (("sudoku_facil", "Solución sudoku fácil"), ("sudoku_dificil", "Solución sudoku difícil")):
        sol = _SOL.get(clave)
        if not sol:
            continue
        lado_s = min(lw - 20, 130)
        if y - lado_s - 20 < y0:
            break
        c.setFillColor(GRIS)
        c.setFont("P-EtqB", 9)
        c.drawString(lx, y - 10, nombre.upper())
        _dibujar_sudoku(c, lx, y - 16, lado_s, sol, k, chico=True)
        y -= lado_s + 30


def dibujar(c, tipo, area, k, arts=None, rnd=None, numero=None, ahora=None):
    from .consolidado import D
    k = dict(k, D=D)
    if tipo == "clasificados":
        _clasificados(c, area, k)
    elif tipo == "guia":
        _guia(c, area, k, D)
    elif tipo == "entretenimiento":
        _entretenimiento(c, area, k, rnd)
    elif tipo == "pupiletras":
        _pupiletras(c, area, k, rnd, arts or [])
