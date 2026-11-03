"""Diseño del periódico: textos fijos, colores, tipografías y tamaños, editables sin tocar código.

Se guarda en data/diseno.json (solo lo que cambiaste; el resto sale de los valores predeterminados).
Las tipografías se registran con los nombres "de rol" que usa la maqueta (Heavy, Slab, Serif,
FC-*, Narrow), así cambiar una fuente no requiere tocar consolidado.py ni portada.py.
"""
import copy
import json
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .config import ASSETS_DIR, BASE_DIR

DISENO_PATH = BASE_DIR / "data" / "diseno.json"
_A = ASSETS_DIR / "fonts"
_W = Path("C:/Windows/Fonts")

# familia -> archivos por peso (R regular, B negrita, I cursiva, XB extra negrita, XBI extra negrita cursiva)
FAMILIAS = {
    "Alfa Slab One": {"R": _A / "AlfaSlabOne-Regular.ttf"},
    "Zilla Slab": {"R": _A / "ZillaSlab-SemiBold.ttf", "B": _A / "ZillaSlab-Bold.ttf"},
    "PT Serif": {"R": _A / "PT_Serif-Web-Regular.ttf", "B": _A / "PT_Serif-Web-Bold.ttf",
                 "I": _A / "PT_Serif-Web-Italic.ttf"},
    "PT Sans Narrow": {"R": _A / "PT_Sans-Narrow-Web-Regular.ttf", "B": _A / "PT_Sans-Narrow-Web-Bold.ttf"},
    "Fira Sans Condensed": {"R": _A / "FiraSansCondensed-Medium.ttf", "SB": _A / "FiraSansCondensed-SemiBold.ttf",
                            "B": _A / "FiraSansCondensed-Bold.ttf", "XB": _A / "FiraSansCondensed-ExtraBold.ttf",
                            "XBI": _A / "FiraSansCondensed-ExtraBoldItalic.ttf"},
    # de Windows (si la PC no las tiene, se usa la predeterminada del rol)
    "Georgia": {"R": _W / "georgia.ttf", "B": _W / "georgiab.ttf", "I": _W / "georgiai.ttf", "XBI": _W / "georgiaz.ttf"},
    "Cambria": {"B": _W / "cambriab.ttf", "I": _W / "cambriai.ttf", "XBI": _W / "cambriaz.ttf"},
    "Constantia": {"R": _W / "constan.ttf", "B": _W / "constanb.ttf", "I": _W / "constani.ttf"},
    "Arial": {"R": _W / "arial.ttf", "B": _W / "arialbd.ttf", "I": _W / "ariali.ttf", "XBI": _W / "arialbi.ttf"},
    "Arial Narrow": {"R": _W / "ARIALN.TTF", "B": _W / "ARIALNB.TTF", "I": _W / "ARIALNI.TTF", "XBI": _W / "ARIALNBI.TTF"},
    "Arial Black": {"R": _W / "ariblk.ttf"},
    "Impact": {"R": _W / "impact.ttf"},
    "Calibri": {"R": _W / "calibri.ttf", "B": _W / "calibrib.ttf", "I": _W / "calibrii.ttf", "XBI": _W / "calibriz.ttf"},
    "Segoe UI": {"R": _W / "segoeui.ttf", "B": _W / "segoeuib.ttf", "I": _W / "segoeuii.ttf", "XBI": _W / "segoeuiz.ttf"},
    "Corbel": {"R": _W / "corbel.ttf", "B": _W / "corbelb.ttf", "I": _W / "corbeli.ttf", "XBI": _W / "corbelz.ttf"},
}

ROLES = {   # rol -> (nombre visible, familia predeterminada)
    "titular_portada": ("Titulares de portada", "Alfa Slab One"),
    "titular": ("Titulares interiores", "Zilla Slab"),
    "texto": ("Texto de las noticias", "PT Serif"),
    "antetitulo": ("Antetítulos, puntos y etiquetas", "Fira Sans Condensed"),
    "detalles": ("Pies de foto, índice y pies de página", "PT Sans Narrow"),
}

DEFAULT = {
    "textos": {
        "lema": "NOTICIAS DE PUNO Y LA REGIÓN",
        "ciudad": "Puno",
        "cinta": "HOY EN PUNO",
        "banda_1": "{n} NOTICIAS",
        "banda_2": "AL DÍA",
        "franja_2": "Boletín N° {numero}",
        "franja_3": "Edición de las {hora} h",
        "pie_portada_izq": "Altiplano al Día · boletín elaborado a partir del monitoreo de medios de la región",
        "pie_portada_der": "Boletín N° {numero} · {fecha} {hora}",
        "pie_izq": "Altiplano al Día · Boletín N° {numero}",
        "pie_der": "altiplano al día — noticias de Puno y la región",
        "firma_1": "Redacción",
        "firma_2": "Altiplano al Día",
        "indice": "En esta edición",
        "datos": "DATOS CLAVE",
        "credito": "Boletín elaborado por Altiplano al Día a partir del monitoreo de medios de la región.",
    },
    "colores": {
        "principal": "#043F9B",
        "degradado": "#6E9BE0",
        "acento": "#FFEE00",
        "alerta": "#C62828",
        "texto": "#1A1A1A",
        "temas": {"Política": "#043F9B", "Seguridad/Policiales": "#C62828", "Economía": "#2E7D32",
                  "Educación": "#6A1B9A", "Salud": "#00838F", "Sociedad": "#D84315", "Cultura": "#AD1457",
                  "Deportes": "#1565C0", "Medio ambiente": "#2E7D32", "Otros": "#37474F"},
    },
    "fuentes": {rol: fam for rol, (_, fam) in ROLES.items()},
    "tamanos": {
        "texto": 9.0,            # pt del cuerpo de las noticias
        "antetitulo": 11.0,      # pt
        "pie_foto": 7.8,         # pt
        "titulares": 100,        # % sobre el tamaño calculado (interiores)
        "titulares_portada": 100,  # % (portada)
    },
}

TEXTOS_AYUDA = {
    "lema": "Portada · bajo el logo", "ciudad": "Ciudad en la cabecera de cada página",
    "cinta": "Portada · cinta inclinada roja", "banda_1": "Portada · banda (blanco)", "banda_2": "Portada · banda (color acento)",
    "franja_2": "Portada · franja de fecha (2.º)", "franja_3": "Portada · franja de fecha (3.º)",
    "pie_portada_izq": "Portada · pie izquierdo", "pie_portada_der": "Portada · pie derecho",
    "pie_izq": "Páginas interiores · pie izquierdo", "pie_der": "Páginas interiores · pie derecho",
    "firma_1": "Firma de las notas (1.ª línea)", "firma_2": "Firma de las notas (2.ª línea)",
    "indice": "Página 2 · título del índice", "datos": "Título de la caja de datos",
    "credito": "Página 2 · crédito al pie",
}
COLORES_AYUDA = {"principal": "Color principal (cabezal, bandas, enlaces)", "degradado": "Degradado de las bandas",
                 "acento": "Acento (número de página, etiquetas «PÁG.», líneas)",
                 "alerta": "Alerta (cinta HOY EN PUNO, viñetas, ACTUALIZACIÓN)", "texto": "Texto y titulares"}

PRESETS = {
    "Altiplano (predeterminado)": {},
    "Clásico sobrio": {
        "colores": {"principal": "#1B2A41", "degradado": "#4A5D7A", "acento": "#E2B93B", "alerta": "#8B1E1E",
                    "texto": "#161616"},
        "fuentes": {"titular_portada": "Zilla Slab", "titular": "PT Serif", "texto": "PT Serif",
                    "antetitulo": "PT Sans Narrow", "detalles": "PT Sans Narrow"},
    },
    "Moderno": {
        "colores": {"principal": "#0057B8", "degradado": "#3FA9F5", "acento": "#FFD100", "alerta": "#E4002B",
                    "texto": "#101820"},
        "fuentes": {"titular_portada": "Fira Sans Condensed", "titular": "Fira Sans Condensed", "texto": "PT Serif",
                    "antetitulo": "Fira Sans Condensed", "detalles": "Fira Sans Condensed"},
    },
    "Alto contraste": {
        "colores": {"principal": "#111111", "degradado": "#4B4B4B", "acento": "#FFEE00", "alerta": "#D50000",
                    "texto": "#000000"},
        "fuentes": {"titular_portada": "Alfa Slab One", "titular": "Zilla Slab", "texto": "PT Serif",
                    "antetitulo": "Fira Sans Condensed", "detalles": "PT Sans Narrow"},
    },
}


# ------------------------------------------------------------------ carga / guardado
def _mezclar(base, cambios):
    out = copy.deepcopy(base)
    for k, v in (cambios or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _mezclar(out[k], v)
        else:
            out[k] = v
    return out


def cargar() -> dict:
    try:
        guardado = json.loads(DISENO_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        guardado = {}
    return _mezclar(DEFAULT, guardado)


def guardar(d: dict) -> None:
    DISENO_PATH.parent.mkdir(parents=True, exist_ok=True)
    DISENO_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def restablecer() -> dict:
    try:
        DISENO_PATH.unlink()
    except OSError:
        pass
    return cargar()


def con_preset(d: dict, nombre: str) -> dict:
    """Aplica una combinación predefinida de colores y letras (los textos no cambian)."""
    base = _mezclar(DEFAULT, {"textos": d.get("textos", {}), "tamanos": d.get("tamanos", {})})
    return _mezclar(base, PRESETS.get(nombre, {}))


def familias_disponibles() -> list[str]:
    return [f for f, pesos in FAMILIAS.items() if any(Path(p).exists() for p in pesos.values())]


def texto(d: dict, clave: str, **valores) -> str:
    t = d["textos"].get(clave, DEFAULT["textos"].get(clave, ""))
    try:
        return t.format(**valores)
    except (KeyError, IndexError, ValueError):
        return t


def color_tema_hex(d: dict, tema: str) -> str:
    temas = d["colores"]["temas"]
    if tema in temas:
        return temas[tema]
    import unicodedata
    def norm(s):
        return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    for k, v in temas.items():
        if norm(k)[:5] == norm(tema)[:5]:
            return v
    return d["colores"]["principal"]


# ------------------------------------------------------------------ tipografías
# alias usados por la maqueta -> (rol, pesos preferidos en orden)
_ALIAS = {
    "Heavy": ("titular_portada", ["XB", "R", "B"]),
    "Slab": ("titular", ["B", "XB", "R"]),
    "Slab-Semi": ("titular", ["R", "SB", "B"]),
    "Serif": ("texto", ["R", "B"]),
    "Serif-B": ("texto", ["B", "XB", "R"]),
    "Serif-I": ("texto", ["I", "XBI", "R"]),
    "FC-M": ("antetitulo", ["R", "SB", "B"]),
    "FC-SB": ("antetitulo", ["SB", "B", "R"]),
    "FC-B": ("antetitulo", ["B", "XB", "R"]),
    "FC-XB": ("antetitulo", ["XB", "B", "R"]),
    "FC-XBI": ("antetitulo", ["XBI", "I", "XB", "B", "R"]),
    "Narrow": ("detalles", ["R", "B"]),
    "Narrow-B": ("detalles", ["B", "XB", "R"]),
}


def _archivo(familia, pesos):
    archivos = FAMILIAS.get(familia, {})
    for p in pesos:
        f = archivos.get(p)
        if f and Path(f).exists():
            return f
    return None


def aplicar_fuentes(d: dict) -> None:
    """Registra en ReportLab los alias de la maqueta con las familias elegidas en el diseño."""
    for alias, (rol, pesos) in _ALIAS.items():
        fam = d["fuentes"].get(rol) or ROLES[rol][1]
        f = _archivo(fam, pesos) or _archivo(ROLES[rol][1], pesos)
        # ReportLab no reemplaza una fuente ya registrada con el mismo nombre: se quita antes
        pdfmetrics._fonts.pop(alias, None)
        pdfmetrics.registerFont(TTFont(alias, str(f)))
    pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-B", italic="Serif-I", boldItalic="Serif-B")
    pdfmetrics.registerFontFamily("Narrow", normal="Narrow", bold="Narrow-B", italic="Narrow", boldItalic="Narrow-B")
