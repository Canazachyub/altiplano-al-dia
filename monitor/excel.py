"""Exporta la base SQLite a noticias.xlsx (para revisión manual; la BD sigue siendo la fuente)."""
from datetime import datetime

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import storage
from .config import BASE_DIR, EXCEL_PATH, nombre_medio

AZUL = PatternFill("solid", fgColor="1F4E79")
CLARO = PatternFill("solid", fgColor="EAF1FB")


def _estado(p, descartados=frozenset()) -> str:
    if p["grupo_id"] in descartados:
        return "descartada (no es noticia)"
    if p["consolidado_n"]:
        return f"incluida en PDF N° {p['consolidado_n']}"
    if p["grupo_id"]:
        return "procesada"
    return "nueva"


def _fmt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return iso


def _hoja(ws, headers, filas, anchos, link_cols=(), wrap_cols=()):
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = AZUL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for fila in filas:
        # caracteres de control (vienen a veces en textos de FB) hacen fallar a openpyxl
        ws.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in fila])
    for i, w in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=(c.column in wrap_cols))
            if isinstance(c.value, datetime):
                c.number_format = "dd/mm/yyyy hh:mm"
            if c.column in link_cols and isinstance(c.value, str) and c.value.startswith("http"):
                c.hyperlink = c.value
                c.font = Font(color="0563C1", underline="single")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def exportar_excel(cfg: dict):
    storage.init_db()
    wb = Workbook()
    ws = wb.active
    ws.title = "Noticias"
    posts = storage.all_posts()
    descartados = frozenset(g["id"] for g in storage.grupos_descartados())
    filas = [[
        p["id"], nombre_medio(p["pagina"], cfg), _fmt(p["publicado"]), _fmt(p["detectado"]),
        (p["texto"] or "")[:32000], p["permalink"], p["enlace_ext"], p["ambito"], p["tema"],
        p["grupo_id"], _estado(p, descartados) if p["estado"] != "base" else "base (no procesada)",
        p["titular"], p["resumen"], p["pdf_path"],
    ] for p in posts]
    _hoja(ws, ["ID del post", "Medio", "Publicado", "Capturado", "Texto original", "Enlace Facebook",
               "Enlace externo", "Ámbito", "Tema", "ID grupo", "Estado", "Titular (IA)", "Resumen",
               "PDF individual"],
          filas, [26, 18, 16, 16, 60, 30, 30, 13, 18, 9, 22, 40, 40, 30],
          link_cols=(6, 7), wrap_cols=(5, 12, 13))
    for row in ws.iter_rows(min_row=2):
        row[4].alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row[0].row].height = 60

    wg = wb.create_sheet("Grupos")
    grupos = storage.list_grupos()
    _hoja(wg, ["ID grupo", "Titular", "Ámbito", "Tema", "N° fuentes", "Medios", "Fecha del hecho",
               "Estado PDF", "Último PDF N°"],
          [[g["id"], g["titular"], g["ambito"], g["tema"], g["n_fuentes"], g["medios"],
            _fmt(g["fecha_hecho"]), g["estado_pdf"], g["ultimo_pdf"]] for g in grupos],
          [9, 60, 13, 18, 10, 40, 16, 14, 12], wrap_cols=(2, 6))
    for row in wg.iter_rows(min_row=2):
        if (row[4].value or 0) > 1:
            for c in row:
                c.fill = CLARO

    try:
        wb.save(EXCEL_PATH)
        return EXCEL_PATH
    except PermissionError:
        # el archivo está abierto en Excel: se guarda una copia con fecha
        alt = BASE_DIR / f"noticias_{datetime.now():%Y%m%d_%H%M}.xlsx"
        wb.save(alt)
        return alt
