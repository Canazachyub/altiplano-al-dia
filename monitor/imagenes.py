"""Fotos de las noticias: descarga de la imagen del post (o de la nota enlazada) a data/img/."""
import hashlib
import io
import re

import requests
from bs4 import BeautifulSoup
from PIL import Image

from .config import IMG_DIR

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def og_image(url: str | None) -> str | None:
    """Imagen destacada (og:image) de una nota externa."""
    if not url or re.search(r"(facebook|instagram|youtube|youtu\.be|tiktok|whatsapp)\.", url):
        return None
    try:
        r = requests.get(url, timeout=15, headers=UA)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for prop in ("og:image", "twitter:image"):
            m = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if m and m.get("content", "").startswith("http"):
                return m["content"]
    except Exception:  # noqa: BLE001
        pass
    return None


def descargar_imagen(url: str | None, post_id: str) -> str | None:
    """Baja la imagen, la guarda como JPEG y devuelve la ruta (None si falla o es muy chica)."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=25, headers=UA)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        if im.width < 300 or im.height < 200:
            return None
        im.thumbnail((1600, 1600))
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        destino = IMG_DIR / f"{hashlib.sha1(post_id.encode()).hexdigest()[:16]}.jpg"
        im.save(destino, "JPEG", quality=85)
        return str(destino)
    except Exception:  # noqa: BLE001
        return None
