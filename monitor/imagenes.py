"""Fotos de las noticias: descarga de la imagen del post (o de la nota enlazada) a data/img/."""
import hashlib
import io
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from .config import IMG_DIR

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

MAX_IMG_BYTES = 15 * 1024 * 1024    # una foto de noticia nunca pesa tanto: corta descargas absurdas
MAX_HTML_BYTES = 3 * 1024 * 1024    # para buscar og:image basta el <head>
MAX_PIXELES = 60_000_000            # "bombas" de descompresión: se rechazan antes de decodificar


def _bajar(url: str, limite: int, timeout: int) -> tuple[bytes, requests.Response]:
    """GET en streaming que se corta al pasar `limite` bytes (ValueError)."""
    with requests.get(url, timeout=timeout, headers=UA, stream=True) as r:
        r.raise_for_status()
        largo = r.headers.get("Content-Length")
        if largo and largo.isdigit() and int(largo) > limite:
            raise ValueError(f"archivo demasiado grande ({largo} bytes)")
        buf = bytearray()
        for trozo in r.iter_content(64 * 1024):
            buf.extend(trozo)
            if len(buf) > limite:
                raise ValueError("archivo demasiado grande")
        return bytes(buf), r


def og_image(url: str | None) -> str | None:
    """Imagen destacada (og:image) de una nota externa."""
    if not url or not url.startswith(("http://", "https://")) \
            or re.search(r"(facebook|instagram|youtube|youtu\.be|tiktok|whatsapp)\.", url):
        return None
    try:
        datos, r = _bajar(url, MAX_HTML_BYTES, 15)
        if "html" not in (r.headers.get("Content-Type") or "text/html").lower():
            return None
        soup = BeautifulSoup(datos, "html.parser")     # bytes: BS detecta el charset del <meta>
        for prop in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image"):
            m = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            contenido = (m.get("content") or "").strip() if m else ""
            if contenido:
                # algunas webs ponen la ruta relativa ("/img/foto.jpg" o "//cdn…")
                absoluta = urljoin(r.url, contenido)
                if absoluta.startswith(("http://", "https://")):
                    return absoluta
    except Exception:  # noqa: BLE001
        pass
    return None


def descargar_imagen(url: str | None, post_id: str) -> str | None:
    """Baja la imagen, la guarda como JPEG y devuelve la ruta (None si falla o es muy chica)."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        datos, r = _bajar(url, MAX_IMG_BYTES, 25)
        tipo = (r.headers.get("Content-Type") or "").lower()
        if tipo and not tipo.startswith(("image/", "application/octet-stream", "binary/")):
            return None            # p. ej. una página HTML de error en vez de la foto
        im = Image.open(io.BytesIO(datos))     # solo lee la cabecera
        if im.width * im.height > MAX_PIXELES:
            return None
        try:
            im = ImageOps.exif_transpose(im)   # fotos de celular: respeta la orientación
        except Exception:  # noqa: BLE001
            pass
        im = im.convert("RGB")
        if im.width < 300 or im.height < 200:
            return None
        im.thumbnail((1600, 1600))
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        # nombre = hash del id: nunca trae caracteres raros ni rutas
        destino = IMG_DIR / f"{hashlib.sha1(post_id.encode()).hexdigest()[:16]}.jpg"
        tmp = destino.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=85)
        os.replace(tmp, destino)               # sin JPEG a medio escribir si algo falla
        return str(destino)
    except Exception:  # noqa: BLE001
        return None
