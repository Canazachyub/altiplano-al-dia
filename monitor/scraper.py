"""Scraping de páginas públicas de Facebook con Playwright (perfil persistente).

Lo que se observó al explorar el DOM (nov-2026):
- Cada publicación del feed es un  div[aria-posinset="N"].
- El feed se virtualiza: al hacer scroll, los posts que quedan arriba se vacían.
  Por eso se extrae post por post mientras se avanza, no al final.
- Texto: [data-ad-rendering-role="story_message"]  (truncado con "Ver más").
- Tarjeta de enlace externo: roles "title"/"description"/"meta"; el href pasa por
  l.facebook.com/l.php?u=<url real>.
- La fecha/permalink está en un <a> cuyo texto viene ofuscado (caracteres U+034F) y
  cuyo href real (/posts/pfbid..., /reel/..., /videos/...) a veces solo aparece al
  pasar el mouse. Por eso se hace hover sobre ese enlace antes de leerlo.
- El "pfbid" de /posts/ es estable entre visitas: sirve como ID del post.
"""
import random
import re
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

try:  # existe desde Playwright 1.39; se importa del módulo interno porque sync_api no lo exporta
    from playwright._impl._errors import TargetClosedError
except ImportError:  # pragma: no cover
    TargetClosedError = None

from .config import PROFILE_DIR, page_slug
from .storage import text_hash


class NotLoggedIn(Exception):
    pass


def navegador_cerrado(e: BaseException) -> bool:
    """True si el error es porque se cerró la ventana / el navegador (no tiene sentido seguir)."""
    if TargetClosedError is not None and isinstance(e, TargetClosedError):
        return True
    msg = str(e)
    return isinstance(e, PlaywrightError) and any(
        t in msg for t in ("has been closed", "Target closed", "Browser closed", "Connection closed"))


MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

FIND_TIMESTAMP_JS = r"""
(p) => {
  // El enlace de fecha está en la cabecera: después del nombre de la página y antes del
  // texto. Así se evita tomar la fecha de un comentario o el enlace de una foto.
  const msg = p.querySelector('[data-ad-rendering-role="story_message"]');
  const prof = p.querySelector('[data-ad-rendering-role="profile_name"]');
  const F = Node.DOCUMENT_POSITION_FOLLOWING, P = Node.DOCUMENT_POSITION_PRECEDING;
  const cab = [...p.querySelectorAll('a[role="link"]')].filter(a =>
      (!prof || (!prof.contains(a) && (prof.compareDocumentPosition(a) & F))) &&
      (!msg || (msg.compareDocumentPosition(a) & P)) &&
      !/\/photo|\/stories\/|\/videos\/.*\/photo/.test(a.getAttribute('href') || '') &&
      !/ver historia/i.test(a.getAttribute('aria-label') || ''));
  return cab.find(a => /\/posts\/|story_fbid|permalink|\/reel\/|\/videos\//.test(a.getAttribute('href') || ''))
      || cab.find(a => /\d{4}/.test(a.getAttribute('aria-label') || ''))
      || cab.find(a => a.innerText.includes('͏'))
      || cab[0] || null;
}
"""

EXTRACT_JS = r"""
(p) => {
  const q = (r) => p.querySelector(`[data-ad-rendering-role="${r}"]`);
  const msg = q('story_message');
  const hrefs = [...p.querySelectorAll('a[href]')].map(a => a.href);
  let ext = null;
  for (const h of hrefs) {
    try {
      const u = new URL(h);
      if (u.pathname === '/l.php' && u.searchParams.get('u')) { ext = u.searchParams.get('u'); break; }
      if (u.protocol.startsWith('http') && !/(^|\.)(facebook\.com|fbcdn\.net|fb\.me|fb\.watch)$/.test(u.hostname)) { ext = h; break; }
    } catch (e) {}
  }
  const perma = hrefs.filter(h => /\/posts\/|permalink\.php|story_fbid|\/videos\/|\/reel\/|\/watch\/?\?v=|\/photo/.test(h));
  // solo fechas anteriores al texto (las posteriores son de comentarios)
  const fechas = [...p.querySelectorAll('[aria-label]')]
      .filter(e => !msg || (msg.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_PRECEDING))
      .map(e => e.getAttribute('aria-label'))
      .filter(t => /\d{1,2} de [a-záéíóú]+ de \d{4}/i.test(t || ''));
  // foto principal: la imagen de fbcdn más grande del post (no avatares ni emojis)
  let img = null, area = 0;
  for (const im of p.querySelectorAll('img')) {
    const r = im.getBoundingClientRect();
    const a = r.width * r.height;
    if (/fbcdn|scontent/.test(im.src) && r.width >= 180 && r.height >= 120 && a > area) { img = im.src; area = a; }
  }
  return {
    img,
    texto: msg ? msg.innerText : '',
    ext, perma, fechas,
    titulo_card: (q('title') || {}).innerText || '',
    desc_card: (q('description') || {}).innerText || '',
    autor: (q('profile_name') || {}).innerText || '',
  };
}
"""


# ------------------------------------------------------------------ utilidades
def canonical(url: str):
    """Devuelve (id_canonico, permalink_limpio) o (None, None)."""
    if not url:
        return None, None
    u = urlparse(url)
    qs = parse_qs(u.query)
    path = u.path
    if m := re.search(r"/posts/(pfbid\w+|\d+)", path):
        return f"post:{m.group(1)}", f"https://www.facebook.com{path.rstrip('/')}"
    if "permalink.php" in path and "story_fbid" in qs:
        sid, pid = qs["story_fbid"][0], qs.get("id", [""])[0]
        return f"post:{sid}", f"https://www.facebook.com/permalink.php?story_fbid={sid}&id={pid}"
    if m := re.search(r"/(videos|reel)/(\d+)", path):
        return f"{m.group(1)}:{m.group(2)}", f"https://www.facebook.com/{m.group(1)}/{m.group(2)}/"
    if "/watch" in path and "v" in qs:
        return f"videos:{qs['v'][0]}", f"https://www.facebook.com/watch/?v={qs['v'][0]}"
    if "/photo" in path and "fbid" in qs:
        return f"photo:{qs['fbid'][0]}", f"https://www.facebook.com/photo/?fbid={qs['fbid'][0]}"
    return None, None


def parse_fecha(texto: str):
    """'lunes, 2 de noviembre de 2026 a las 5:38 pm' -> '2026-11-02T17:38:00'"""
    if not texto:
        return None
    m = re.search(
        r"(\d{1,2}) de ([a-záéíóú]+) de (\d{4})(?:\D+(\d{1,2}):(\d{2})\s*([ap])?\.?\s*m?)?",
        texto, re.I,
    )
    if not m or m.group(2).lower() not in MESES:
        return None
    d, mes, y = int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3))
    hh, mm = int(m.group(4) or 0), int(m.group(5) or 0)
    if m.group(6):
        ap = m.group(6).lower()
        if ap == "p" and hh < 12:
            hh += 12
        if ap == "a" and hh == 12:
            hh = 0
    return f"{y:04d}-{mes:02d}-{d:02d}T{hh:02d}:{mm:02d}:00"


def parse_relativo(texto: str):
    """'11 h', '25 min', '3 d' -> fecha ISO aproximada (fallback si no hubo tooltip)."""
    m = re.fullmatch(r"\s*(\d+)\s*(min|h|d|sem)\s*", texto or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    delta = {"min": timedelta(minutes=n), "h": timedelta(hours=n),
             "d": timedelta(days=n), "sem": timedelta(weeks=n)}[u]
    return (datetime.now() - delta).replace(second=0, microsecond=0).isoformat()


def pausa(a=1.2, b=3.0):
    time.sleep(random.uniform(a, b))


# ------------------------------------------------------------------ navegador
def open_context(pw, canal="chrome", headless=False):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        locale="es-PE",
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    # Chrome instalado -> Edge (viene con Windows) -> Chromium de Playwright
    primer_error = None
    for ch in dict.fromkeys(c for c in (canal, "chrome", "msedge") if c):
        try:
            return pw.chromium.launch_persistent_context(channel=ch, **kwargs)
        except Exception as e:  # noqa: BLE001
            primer_error = primer_error or e
            continue
    try:
        return pw.chromium.launch_persistent_context(**kwargs)
    except Exception as e:  # noqa: BLE001
        # el error útil suele ser el del primer navegador (p. ej. «perfil en uso»), no el del
        # Chromium de Playwright, que casi nunca está instalado
        if primer_error is not None:
            raise RuntimeError(f"No se pudo abrir el navegador: {primer_error}") from e
        raise


def is_login_wall(page) -> bool:
    if "/login" in page.url or "checkpoint" in page.url:
        return True
    return page.locator('input[name="email"]').count() > 0 and page.locator('input[name="pass"]').count() > 0


def interactive_login(canal="chrome", minutos=15, log=print):
    """Abre la ventana del perfil para que el usuario inicie sesión a mano.
    Termina cuando el usuario cierra la ventana (o se agota el tiempo)."""
    with sync_playwright() as pw:
        ctx = open_context(pw, canal, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        log("Inicia sesión en la ventana de Chrome que se abrió y luego ciérrala.")
        try:
            page.wait_for_event("close", timeout=minutos * 60 * 1000)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass


# ------------------------------------------------------------------ extracción
def _expand_and_hover(page, h):
    """Expande 'Ver más' y pasa el mouse por la fecha para que FB rellene el permalink."""
    try:
        h.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        return None
    for btn in h.query_selector_all('[data-ad-rendering-role="story_message"] [role="button"]'):
        try:
            if btn.inner_text().strip() in ("Ver más", "See more"):
                try:
                    btn.click(timeout=1500)
                except Exception:
                    btn.evaluate("(b) => b.click()")  # si algo lo tapa, clic por JS
                page.wait_for_timeout(600)
                break
        except Exception:
            pass
    tooltip, aria = None, None
    try:
        ts = h.evaluate_handle(FIND_TIMESTAMP_JS).as_element()
        if ts:
            aria = ts.get_attribute("aria-label")
            ts.hover(timeout=3000)
            for intento in range(16):  # el tooltip con la fecha completa tarda en aparecer
                page.wait_for_timeout(250)
                tooltip = page.evaluate(
                    "() => [...document.querySelectorAll('[role=tooltip]')]"
                    ".map(e => e.innerText).find(t => /\\d{4}/.test(t)) || null")
                al = ts.get_attribute("aria-label") or ""
                if not tooltip and re.search(r"\d{4}", al):
                    tooltip = al
                if tooltip:
                    break
                if intento == 7:  # re-hover: a veces el primero solo hidrata el enlace
                    page.mouse.move(5, 5)
                    ts.hover(timeout=3000)
            aria = ts.get_attribute("aria-label") or aria
            href = ts.get_attribute("href")
            page.mouse.move(5, 5)
            page.wait_for_timeout(200)
            return {"href": href and page.evaluate("(h) => new URL(h, location.href).href", href),
                    "tooltip": tooltip, "aria": aria}
    except Exception:
        pass
    return {"href": None, "tooltip": tooltip, "aria": aria}


class Cancelado(Exception):
    """El usuario pulsó Detener."""


def scrape_page(page, url, max_posts=8, is_known=None, log=print, max_scrolls=15, cancelar=None):
    """Recorre el feed de una página y devuelve una lista de posts (orden del feed).
    cancelar(): si devuelve True, se corta la lectura (Cancelado)."""
    slug = page_slug(url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    pausa(3, 5)
    if is_login_wall(page):
        raise NotLoggedIn("Facebook pide iniciar sesión (usa el botón 'Iniciar sesión en Facebook').")

    posts, vistos, conocidos_seguidos = [], set(), 0
    vueltas_sin_nada = 0   # scrolls seguidos sin ningún post nuevo en pantalla
    for _ in range(max_scrolls):
        if cancelar and cancelar():
            raise Cancelado()
        n_vistos = len(vistos)
        for h in page.query_selector_all("div[aria-posinset]"):
            if cancelar and cancelar():
                raise Cancelado()
            try:
                k = h.get_attribute("aria-posinset")
                if k in vistos:
                    continue
                if not h.query_selector('[data-ad-rendering-role="profile_name"]'):
                    continue  # aún sin renderizar o ya virtualizado
                vistos.add(k)
                ts = _expand_and_hover(page, h) or {}
                data = h.evaluate(EXTRACT_JS)
            except PlaywrightError as e:
                # un post que FB quitó del DOM (virtualizado) no debe tirar todo lo leído de la página
                if navegador_cerrado(e):
                    raise
                log(f"  [{slug}] post omitido: {str(e).splitlines()[0][:120]}")
                continue

            post_id, permalink = canonical(ts.get("href"))
            if not post_id:
                # preferir enlaces al post antes que a una foto suelta
                cands = sorted(data["perma"], key=lambda u: 1 if "/photo" in u else 0)
                for cand in cands:
                    post_id, permalink = canonical(cand)
                    if post_id:
                        break
            texto = re.sub(r"\s*…?\s*(Ver más|See more)\s*$", "", (data["texto"] or "")).strip()
            if data["titulo_card"] and data["titulo_card"] not in texto:
                texto += f"\n\n[Tarjeta de enlace] {data['titulo_card']}\n{data['desc_card']}".rstrip()
            thash = text_hash(slug, texto or (permalink or ""))
            if not texto and not permalink:
                continue
            post = {
                "id": post_id or f"hash:{thash[:20]}",
                "text_hash": thash,
                "pagina": slug,
                "permalink": permalink,
                "enlace_ext": data["ext"],
                "imagen_url": data.get("img"),
                "texto": texto,
                "publicado": parse_fecha(ts.get("tooltip") or "")
                or next((f for f in (parse_fecha(x) for x in data["fechas"]) if f), None)
                or parse_fecha(ts.get("aria") or "") or parse_relativo(ts.get("aria") or ""),
            }
            posts.append(post)
            log(f"  [{slug}] #{k} {post['id']}  {texto[:60]!r}")

            if is_known and is_known(post["id"], thash):
                conocidos_seguidos += 1
            else:
                conocidos_seguidos = 0
            if len(posts) >= max_posts or conocidos_seguidos >= 3:
                return posts
            pausa(0.6, 1.5)
        vueltas_sin_nada = vueltas_sin_nada + 1 if len(vistos) == n_vistos else 0
        if vueltas_sin_nada >= 5:
            # fin del feed, o FB cambió el DOM: no tiene sentido seguir bajando
            if not vistos:
                log(f"  [{slug}] no se encontró ninguna publicación (¿cambió el DOM de Facebook "
                    "o la página no está disponible?)")
            break
        page.mouse.wheel(0, random.randint(600, 1100))
        pausa(1.5, 3.2)
    return posts
