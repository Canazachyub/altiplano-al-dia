"""Busca en Google Imágenes una foto para las notas del día que quedaron SIN foto.

Paso del pipeline (antes de la limpieza con Qwen, así la foto encontrada también pasa por Qwen,
el detector de portadas y el OCR):

  1. Notas del boletín y de la web sin foto publicable (y sin "sin foto" puesto a mano).
  2. Consulta = titular + lugar. Se abre Google Imágenes en Chrome  (perfil propio, data/google_profile;
     solo lectura: no inicia sesión ni hace clic en nada salvo rechazar las cookies no esenciales).
     Solo Google (no Bing). Si Google pide verificación ("no soy un robot") no se intenta saltarla:
     el paso se detiene y las notas quedan para buscarlas en Google desde el Chrome del usuario.
  3. Se bajan las primeras imágenes grandes (sin bancos de fotos con marca de agua) y se descartan
     portadas/afiches (sin_texto.es_pagina), fotos con texto que no se puedan recortar
     (sin_texto.verificada) y las que Qwen-VL ve con rótulos encima.
  3b. La imagen debe venir de una página cuyo título/dirección mencione los nombres propios del
     titular (personas, lugares, siglas): Qwen-VL no reconoce caras y podría poner a otra persona.
  4. Qwen-VL puntúa de 0 a 10 qué tan bien ilustra la nota; se queda la mejor (mínimo PUNTAJE_MIN).
  5. Se pone con mesa.poner_foto y la nota queda marcada _FOTO_GOOGLE (pie: «Foto referencial»).

Cada nota se intenta una vez por día (_GOOGLE_INTENTO) para no repetir búsquedas en cada corrida.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import random
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image

from .config import BASE_DIR

log = logging.getLogger("monitor")

PERFIL = BASE_DIR / "data" / "google_profile"
DIR = BASE_DIR / "data" / "fotos_google"
LADO_MIN = 600                 # px del lado mayor
CANDIDATAS = 8                 # imágenes que se bajan y evalúan por nota
PUNTAJE_MIN = 6                # relevancia mínima (0-10, Qwen-VL) si la página menciona los nombres de la nota
PUNTAJE_SIN_CLAVES = 8         # si el titular no tiene nombres propios, la foto debe encajar muy bien
MAX_NOTAS = 15                 # notas por corrida
EXCLUIR = ("gstatic.com", "google.", "googleusercontent", "shutterstock", "alamy", "gettyimages", "istockphoto",
           "dreamstime", "depositphotos", "123rf", "freepik", "stock.adobe", "adobestock", "pinterest", "pinimg",
           "lookaside", "fbsbx", "fbcdn", "instagram", "tiktok", "x.com", "twimg", "facebook.com",
           "wikipedia.org/static", "logo", "icon", "sprite")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")

RELEVANCIA = ("This photo would illustrate a news story with the headline: \"{t}\" (place: {l}). "
              "Rate from 0 to 10 how well the photograph fits that story: 10 = clearly the same people, place, "
              "event or subject; 5 = a plausible generic photo of the subject or place; 0 = unrelated, a "
              "drawing, a logo, a meme, a screenshot or a graphic. Answer only the number.")


class Bloqueado(Exception):
    """Google pidió verificación: se detiene el paso (no se intenta saltarla)."""


# ------------------------------------------------------------------ qué notas
def _notas_sin_foto() -> list:
    from . import consolidado as C, storage
    hoy = storage.dia_editorial()
    grupos = {g["id"]: g for g in storage.grupos_para_pdf(con_relleno=True)}
    c = storage.consolidado_del_dia(hoy)
    del_boletin = {g["id"] for g in storage.grupos_de_consolidado(c["n"])} if c else set()
    for g in storage.grupos_web():                  # lo que sale hoy en la web (misma regla que web.armar)
        if storage.dia_boletin(g) == hoy or g["id"] in del_boletin:
            grupos.setdefault(g["id"], g)
    out = []
    for g in grupos.values():
        nota = json.loads(g["nota_json"] or "{}")
        if nota.get("_SIN_FOTO") or nota.get("_GOOGLE_INTENTO") == hoy:
            continue
        posts = storage.posts_de_grupo(g["id"])
        if C.foto_publicable(nota, posts, C.imagen_original(g, nota, posts)):
            continue
        out.append((g, nota))
    out.sort(key=lambda x: -(x[1].get("IMPACTO") or 0))
    return out[:MAX_NOTAS]


def consulta(g, nota) -> str:
    t = re.sub(r"[«»\"“”'‘’:;¿?¡!]", " ", nota.get("TITULAR") or g["titular"] or "")
    lugar = (nota.get("LUGAR") or "").strip().rstrip(".")
    if lugar and lugar.lower() not in t.lower():
        t += " " + lugar
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------ Google Imágenes
def _abrir(pw):
    PERFIL.mkdir(parents=True, exist_ok=True)
    kw = dict(user_data_dir=str(PERFIL), headless=False, locale="es-PE",
              viewport={"width": 1280, "height": 900}, args=["--start-minimized"])
    for ch in ("chrome", "msedge"):
        try:
            return pw.chromium.launch_persistent_context(channel=ch, **kw)
        except Exception:  # noqa: BLE001
            continue
    return pw.chromium.launch_persistent_context(**kw)


def _rechazar_cookies(page):
    for txt in ("Rechazar todo", "Reject all"):
        try:
            b = page.get_by_role("button", name=txt)
            if b.count():
                b.first.click(timeout=3000)
                page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            pass


def urls_google(page, q) -> list[tuple]:
    """[(url_imagen, texto)] de Google Imágenes (texto = la propia dirección: Google no da el título)."""
    page.goto("https://www.google.com/search?tbm=isch&hl=es&q=" + urllib.parse.quote(q),
              wait_until="domcontentloaded", timeout=30000)
    _rechazar_cookies(page)
    if "/sorry/" in page.url or page.locator("form#captcha-form, iframe[src*='recaptcha']").count():
        raise Bloqueado("Google pidió verificación")
    page.wait_for_timeout(1500)
    vistos, out = set(), []
    for m in re.finditer(r'\["(https?://[^"]+?)",(\d+),(\d+)\]', page.content()):
        u = m.group(1).encode().decode("unicode_escape", errors="ignore")
        if max(int(m.group(2)), int(m.group(3))) < LADO_MIN or u in vistos:
            continue
        if any(x in u.lower() for x in EXCLUIR):
            continue
        vistos.add(u)
        out.append((u, u))
    return out


def _bajar(url, gid) -> Path | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.google.com/"})
        with urllib.request.urlopen(req, timeout=12) as r:
            datos = r.read(15_000_000)
        im = Image.open(io.BytesIO(datos))
        im.load()
        if max(im.size) < LADO_MIN or getattr(im, "is_animated", False):
            return None
        DIR.mkdir(parents=True, exist_ok=True)
        dst = DIR / f"g{gid}_{hashlib.sha1(url.encode()).hexdigest()[:12]}.jpg"
        im.convert("RGB").save(dst, quality=93)
        return dst
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ evaluar
def _relevancia(ruta, titular, lugar) -> int | None:
    from .fotos_limpias import MODELO_VL, OLLAMA_URL, _jpeg_b64, _post
    try:
        r = _post(OLLAMA_URL + "/api/generate", {
            "model": MODELO_VL, "prompt": RELEVANCIA.format(t=titular, l=lugar or "Puno, Peru"),
            "images": [_jpeg_b64(ruta)], "stream": False, "options": {"temperature": 0},
            "keep_alive": "2m"}, timeout=300)
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"\d+", r.get("response") or "")
    return min(10, int(m.group())) if m else None


GENERICAS = {"peru", "lima", "puno", "juliaca", "gobierno", "regional", "nacional", "municipalidad",
             "provincial", "distrital", "congreso", "fiscalia", "policia", "region", "ciudad", "senor", "sede"}


def _plano(t) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def claves(g, nota) -> list[str]:
    """Nombres propios del titular (personas, lugares, instituciones, siglas): la imagen debe venir de una
    página que los mencione. Así no se pone la foto de otra persona (Qwen-VL no reconoce caras).
    Una palabra cuenta como nombre propio si va con mayúscula a mitad de frase en el titular o el cuerpo."""
    letras = r"[A-Za-zÁÉÍÓÚÑÜáéíóúñüĂăÂâȘșȚțÎî]+"
    titular = nota.get("TITULAR") or g["titular"] or ""
    texto = titular + " " + (nota.get("CUERPO") or "")
    propios = set()
    for m in re.finditer(r"(?<=[a-záéíóúñü,] )(" + letras + ")", texto):
        if m.group(1)[:1].isupper():
            propios.add(m.group(1))
    out = []
    for w in re.findall(letras, titular):
        k = _plano(w)
        if k in GENERICAS or len(w) < 3:
            continue
        if (w in propios and len(w) >= 4) or (w.isupper() and len(w) >= 3):
            out.append(k)
    return list(dict.fromkeys(out))


def elegir(urls, g, nota) -> tuple | None:
    """urls = [(url, texto)]. Devuelve (ruta_final, url, puntaje) de la mejor candidata, o None."""
    from .fotos_limpias import tiene_rotulos
    from .sin_texto import es_pagina, verificada
    titular = nota.get("TITULAR") or g["titular"] or ""
    ks = claves(g, nota)
    minimo = PUNTAJE_MIN if ks else PUNTAJE_SIN_CLAVES
    mejor = None
    bajadas = 0
    for u, texto in urls:
        if bajadas >= CANDIDATAS:
            break
        t = _plano(urllib.parse.unquote(texto))
        if ks and sum(k in t for k in ks) < min(2, len(ks)):
            continue                        # la página no habla de lo mismo
        f = _bajar(u, g["id"])
        if not f:
            continue
        bajadas += 1
        if es_pagina(f) or tiene_rotulos(str(f)):
            continue
        v = verificada(f)
        if not v:
            continue
        p = _relevancia(v, titular, nota.get("LUGAR"))
        if p is None:                       # sin Qwen-VL no se pone nada: no se puede juzgar la foto
            continue
        if p >= minimo and (not mejor or p > mejor[2]):
            mejor = (v, u, p)
            if p >= 9:
                break
    return mejor


# ------------------------------------------------------------------ paso
def paso_buscar_fotos(cfg, emit=lambda *a: None) -> dict:
    from . import mesa, storage
    r = {"buscadas": 0, "puestas": 0, "omitido": ""}
    if not cfg.get("buscar_fotos_google", True):
        r["omitido"] = "desactivado"
        return r
    notas = _notas_sin_foto()
    if not notas:
        return r
    hoy = storage.dia_editorial()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _abrir(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for i, (g, nota) in enumerate(notas, 1):
                q = consulta(g, nota)
                emit("status", f"Buscando foto en Google ({i}/{len(notas)}): {q[:60]}")
                r["buscadas"] += 1
                try:
                    res = elegir(urls_google(page, q), g, nota)
                except Bloqueado:
                    # Solo Google (decisión del usuario, 07/11/2026). Si pide verificación no se salta:
                    # las notas quedan pendientes para buscarlas a mano en Google desde el Chrome del usuario.
                    log.warning("Google pidió verificación: se detiene la búsqueda automática de fotos.")
                    r["omitido"] = "Google pidió verificación"
                    break
                except Exception as e:  # noqa: BLE001
                    log.info("Búsqueda de foto #%s: %s", g["id"], e)
                    res = None
                n = json.loads(storage.get_grupo(g["id"])["nota_json"] or "{}")
                n["_GOOGLE_INTENTO"] = hoy
                if res:
                    ruta, url, p = res
                    mesa._guardar(g["id"], n)
                    mesa.poner_foto(g["id"], str(ruta))
                    n = json.loads(storage.get_grupo(g["id"])["nota_json"])
                    n["_FOTO_GOOGLE"] = {"url": url, "dominio": urllib.parse.urlparse(url).netloc,
                                         "consulta": q, "puntaje": p,
                                         "fecha": datetime.now().isoformat(timespec="seconds")}
                    r["puestas"] += 1
                    log.info("Foto de Google para #%s (%s, puntaje %s)", g["id"], url, p)
                mesa._guardar(g["id"], n)
                time.sleep(random.uniform(3, 6))      # sin ráfagas de búsquedas
        finally:
            ctx.close()
    return r
