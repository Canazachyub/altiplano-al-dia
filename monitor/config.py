"""Rutas, configuración JSON y API keys (.env)."""
import copy
import json
import os
import re
import shutil
import threading
from pathlib import Path

from dotenv import load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "data" / "posts.db"
PDF_DIR = BASE_DIR / "pdfs"
PROFILE_DIR = BASE_DIR / "fb_profile"
LOG_PATH = BASE_DIR / "data" / "monitor.log"
API_LOG_PATH = BASE_DIR / "data" / "api.log"
EXCEL_PATH = BASE_DIR / "noticias.xlsx"
CONSOLIDADOS_DIR = PDF_DIR / "consolidados"
IMG_DIR = BASE_DIR / "data" / "img"
ASSETS_DIR = BASE_DIR / "assets"

DEFAULTS = {
    "paginas": [],
    "intervalo_min": 30,
    "proveedor_ia": "deepseek",
    "modelo_deepseek": "deepseek-chat",
    "modelo_claude": "claude-sonnet-5-5",
    "posts_por_revision": 8,
    "procesar_en_primera_revision": 3,
    "navegador_canal": "chrome",
    "headless": False,
    # --- Fase 2
    "fase2_auto": True,             # cruce + clasificación con Claude al final de cada revisión
    "revision_automatica": True,    # False = solo se revisa al pulsar "Revisar ahora"
    "pdf_consolidado_auto": False,  # el periódico se genera a mano tras revisar (Dashboard)
    "limpiar_fotos": True,          # quitar logos y textos sobrepuestos de las fotos (Qwen local)
    "ventana_cruce_horas": 48,
    "ambitos": ["DISTRITAL", "LOCAL", "REGIONAL", "NACIONAL", "INTERNACIONAL"],
    "temas": ["Política", "Deportes", "Economía", "Educación", "Salud", "Seguridad/Policiales",
              "Sociedad", "Cultura", "Medio ambiente", "Otros"],
    "nombres_medios": {},
}


def nombre_medio(slug: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return cfg.get("nombres_medios", {}).get(slug, slug)

_lock = threading.Lock()


CONFIG_BAK = CONFIG_PATH.with_name("config.json.bak")


def _leer_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} no contiene un objeto JSON")
    return data


def load_config() -> dict:
    with _lock:
        # deepcopy: si no, las listas/dicts de DEFAULTS se comparten y se modifican entre llamadas
        cfg = copy.deepcopy(DEFAULTS)
        if not CONFIG_PATH.exists() and CONFIG_BAK.exists():
            cfg.update(_leer_json(CONFIG_BAK))       # se cortó justo al guardar
        elif CONFIG_PATH.exists():
            try:
                cfg.update(_leer_json(CONFIG_PATH))
            except (ValueError, OSError):
                # config.json dañado (p. ej. se cortó la luz al guardar): se usa la copia anterior.
                # Si tampoco hay copia, se lanza el error: arrancar con la config vacía y guardarla
                # borraría la lista de páginas del usuario.
                if not CONFIG_BAK.exists():
                    raise
                cfg.update(_leer_json(CONFIG_BAK))
        # admite lista de strings por comodidad
        cfg["paginas"] = [
            p if isinstance(p, dict) else {"url": p, "activa": True}
            for p in cfg["paginas"]
        ]
        return cfg


def save_config(cfg: dict) -> None:
    """Guarda de forma atómica (archivo temporal + reemplazo) y deja la versión anterior en .bak."""
    with _lock:
        texto = json.dumps(cfg, ensure_ascii=False, indent=2)
        tmp = CONFIG_PATH.with_name("config.json.tmp")
        tmp.write_text(texto, encoding="utf-8")
        if CONFIG_PATH.exists():
            try:
                _leer_json(CONFIG_PATH)                 # solo se respalda si la actual es válida
                shutil.copyfile(CONFIG_PATH, CONFIG_BAK)
            except (ValueError, OSError):
                pass
        try:
            os.replace(tmp, CONFIG_PATH)
        except PermissionError:
            # Windows: otro programa (antivirus, editor) tiene el archivo abierto un instante
            CONFIG_PATH.write_text(texto, encoding="utf-8")
            tmp.unlink(missing_ok=True)


def page_slug(url: str) -> str:
    """https://www.facebook.com/unapunooficial -> unapunooficial"""
    path = re.sub(r"^https?://(www\.|m\.)?facebook\.com/", "", url.strip()).strip("/")
    path = path.split("?")[0].split("/")[0]
    return re.sub(r"[^\w.-]", "_", path) or "pagina"


def normalize_page_url(text: str) -> str:
    text = text.strip()
    if not text.startswith("http"):
        text = "https://www.facebook.com/" + text.lstrip("/")
    return "https://www.facebook.com/" + page_slug(text)


# ---------- API keys ----------
KEY_NAMES = {"deepseek": "DEEPSEEK_API_KEY", "claude": "ANTHROPIC_API_KEY"}


def get_api_key(provider: str) -> str:
    """'' si no hay key (o si el proveedor no existe: antes lanzaba KeyError y cortaba la revisión)."""
    nombre = KEY_NAMES.get((provider or "").strip().lower())
    if not nombre:
        return ""
    load_dotenv(ENV_PATH, override=True)
    return os.getenv(nombre, "").strip()


def set_api_key(provider: str, value: str) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")
    set_key(str(ENV_PATH), KEY_NAMES[provider], value.strip(), quote_mode="never")
    os.environ[KEY_NAMES[provider]] = value.strip()
