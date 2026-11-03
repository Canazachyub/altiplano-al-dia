"""Rutas, configuración JSON y API keys (.env)."""
import json
import os
import re
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
    "pdf_consolidado_auto": True,   # boletín consolidado al final de cada ciclo
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


def load_config() -> dict:
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        # admite lista de strings por comodidad
        cfg["paginas"] = [
            p if isinstance(p, dict) else {"url": p, "activa": True}
            for p in cfg["paginas"]
        ]
        return cfg


def save_config(cfg: dict) -> None:
    with _lock:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


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
    load_dotenv(ENV_PATH, override=True)
    return os.getenv(KEY_NAMES[provider], "").strip()


def set_api_key(provider: str, value: str) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")
    set_key(str(ENV_PATH), KEY_NAMES[provider], value.strip(), quote_mode="never")
    os.environ[KEY_NAMES[provider]] = value.strip()
