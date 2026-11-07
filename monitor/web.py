"""Publica en la web las noticias marcadas 🌐 Web.

Cada nota sale con el mismo titular, texto, foto (la versión limpia) y pie que lleva en el periódico,
porque se arma con la misma función que usa el boletín (consolidado._articulo).

Pasos de publicar():
  1. Arma en data/web/ los archivos del sitio:
       data/noticias.json   notas marcadas para la web (de todos los días)
       data/boletines.json  boletines PDF generados (los últimos 30)
       img/noticias/        fotos de las notas, achicadas para la web
       boletines/           PDF de cada boletín y la imagen de su portada
       rss.xml, sitemap.xml, robots.txt (si hay dirección pública configurada)
  2. Los copia a la carpeta del sitio en esta PC (vista local), si está configurada.
  3. Los sube a GitHub en un solo commit (GitHub Pages actualiza el sitio), si hay repositorio y token.
"""
import base64
import hashlib
import json
import logging
import re
import shutil
import threading
import unicodedata
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from PIL import Image

from . import storage
from .config import BASE_DIR, get_github_token

STAGE = BASE_DIR / "data" / "web"
# carpetas que administra el maquetador (lo demás del sitio no se toca)
GESTIONADAS = ("data/noticias.json", "data/boletines.json", "img/noticias/", "img/compartir/", "boletines/",
               "rss.xml", "sitemap.xml", "robots.txt")
MAX_BOLETINES = 30
FOTO_MAX = 1280

TEMAS = {"Política": "politica", "Seguridad/Policiales": "seguridad", "Economía": "economia",
         "Educación": "educacion", "Salud": "salud", "Sociedad": "sociedad", "Cultura": "cultura",
         "Deportes": "deportes", "Medio ambiente": "ambiente", "Otros": "otros"}

_lock = threading.Lock()


class ErrorWeb(Exception):
    """Error con un mensaje listo para mostrar al usuario."""


def configurada(cfg) -> bool:
    return bool((cfg.get("web_carpeta") or "").strip() or ((cfg.get("web_repo") or "").strip() and get_github_token()))


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:80] or "nota"


# ------------------------------------------------------------------ 1. armar los archivos
def _foto(origen: str, gid: int) -> str | None:
    """Copia la foto achicada (máx. 1280 px, JPG) a img/noticias/n<gid>.jpg; reutiliza la ya hecha."""
    src = Path(origen)
    if not src.exists():
        return None
    huella = hashlib.sha1(f"{src.resolve()}|{src.stat().st_mtime_ns}".encode()).hexdigest()[:8]
    rel = f"img/noticias/n{gid}-{huella}.jpg"
    dst = STAGE / rel
    previa = BASE_DIR / "data" / "web_cache" / f"n{gid}.jpg"   # se conserva entre publicaciones
    marca = previa.with_suffix(".txt")
    firma = f"{src.resolve()}|{src.stat().st_mtime_ns}|ocr5"
    hecho = marca.read_text(encoding="utf-8") if marca.exists() else ""
    if hecho == "RECHAZADA|" + firma:
        return None
    if not (previa.exists() and hecho == firma):
        previa.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((FOTO_MAX, FOTO_MAX))
                im.save(previa, "JPEG", quality=82, optimize=True, progressive=True)
        except Exception:  # noqa: BLE001
            return None
        # último control: la copia exacta que se sube no puede tener ningún texto
        try:
            from .sin_texto import textos
            restos = textos(previa)
        except Exception:  # noqa: BLE001
            restos = ["sin verificar"]
        if restos:
            previa.unlink(missing_ok=True)
            marca.write_text("RECHAZADA|" + firma, encoding="utf-8")
            logging.getLogger("monitor").info("Web: foto de la nota %s no se publica (texto: %s)", gid,
                                              [r[4] if isinstance(r, tuple) else r for r in restos][:5])
            return None
        marca.write_text(firma, encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(previa, dst)
    return rel


def _foco(ruta):
    try:
        from .encuadre import css, foco
        return css(foco(ruta))
    except Exception:  # noqa: BLE001
        return None


def _compartir(origen: str | None, tema: str, titular: str, gid: int) -> str | None:
    """Tarjeta de 1200x630 con la foto, la seccion y el titular para Facebook/WhatsApp (og:image)."""
    if not origen:
        return None
    from .tarjeta_web import tarjeta
    hecha = tarjeta(Path(origen), (tema or "").split("/")[0], titular, gid)
    if not hecha:
        return None
    huella = hashlib.sha1(f"{Path(origen).resolve()}|{Path(origen).stat().st_mtime_ns}|{titular}".encode()).hexdigest()[:8]
    rel = f"img/compartir/n{gid}-{huella}.jpg"
    (STAGE / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(hecha, STAGE / rel)
    return rel


def _impacto(nota) -> int:
    from .editorial import impacto
    return impacto(nota)


def _nota(g, cfg, usados: set) -> dict | None:
    from .consolidado import _articulo, _plano
    try:
        art = _articulo(g, cfg)
    except Exception:  # noqa: BLE001
        return None
    nota = art["nota"]
    titular = art["titular"]
    if not titular or not art["parrafos"]:
        return None
    s = (g["web_slug"] if "web_slug" in g.keys() else None) or slug(titular)
    if s in usados:
        s = f"{s}-{g['id']}"
    usados.add(s)
    datos = []
    for d in nota.get("DATOS") or []:
        t = d.get("dato", "") if isinstance(d, dict) else str(d)
        if t.strip():
            datos.append(_plano(t))
    credito = ""
    m = re.search(r"Foto:\s*([^.]+)\.?\s*$", art["pie"] or "")
    if m:
        credito = m.group(1).strip()
    edicion = g["ultimo_pdf"] if g["estado_pdf"] == "publicado" and g["ultimo_pdf"] else None
    return {
        "id": g["id"], "slug": s, "titular": titular, "bajada": _plano(nota.get("BAJADA") or ""),
        "antetitulo": _plano(nota.get("ANTETITULO") or "").rstrip(".") or (g["tema"] or ""), "lugar": art["lugar"],
        "cuerpo": [_plano(p) for p in art["parrafos"]], "datos": datos,
        "tema": g["tema"] or "Otros", "seccion": TEMAS.get(g["tema"] or "", "otros"),
        "ambito": g["ambito"] or "REGIONAL", "fecha": g["fecha_hecho"] or g["actualizado"],
        "imagen": (foto := _foto(art["img"], g["id"]) if art["img"] else None),
        # dónde están las caras: la web recorta las tarjetas sin cortar cabezas (object-position)
        "foco": _foco(art["img"]) if foto else None,
        # En la web no se cita a otros medios: el pie de la foto es el titular de la nota.
        "pie": titular + (" (Foto referencial)" if foto and art.get("referencial") else ""), "credito": "",
        "medios": [], "fuentes": 1, "enlaces": [],
        "boletin": edicion, "edicion": edicion,
        "impacto": _impacto(nota), "fijada": storage.web_fijada(g),
        "imagen_compartir": _compartir(art["img"], g["tema"], titular, g["id"]),
    }


def _boletines(dia=None) -> list:
    """Boletines PDF para la web (dia 'AAAA-MM-DD' = solo los de ese día)."""
    out = []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        pdfium = None
    for c in storage.list_consolidados()[:MAX_BOLETINES]:
        if dia and storage.dia_editorial(datetime.fromisoformat((c["fecha"] or "1970-01-01")[:19])) != dia:
            continue
        src = Path(c["path"] or "")
        if not src.exists():
            continue
        n = c["n"]
        rel = f"boletines/boletin-{n}.pdf"
        (STAGE / "boletines").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, STAGE / rel)
        info = {"numero": n, "fecha": c["fecha"], "hora": (c["fecha"] or "")[11:16] or None,
                "noticias": c["n_noticias"], "paginas": None, "archivo": rel, "portada": None,
                "kb": round(src.stat().st_size / 1024)}
        if pdfium:
            try:
                doc = pdfium.PdfDocument(str(src))
                info["paginas"] = len(doc)
                portada = f"boletines/boletin-{n}-portada.jpg"
                doc[0].render(scale=1.4).to_pil().convert("RGB").save(STAGE / portada, "JPEG", quality=80)
                info["portada"] = portada
                doc.close()
            except Exception:  # noqa: BLE001
                pass
        out.append(info)
    return out


def _rfc822(iso):
    try:
        return format_datetime(datetime.fromisoformat(iso).astimezone())
    except Exception:  # noqa: BLE001
        return ""


def _extras(notas, url):
    url = url.rstrip("/")
    items = []
    for n in notas[:50]:
        link = f"{url}/nota/{n['slug']}"
        enc = f'<enclosure url="{url}/{n["imagen"]}" type="image/jpeg" length="0"/>' if n["imagen"] else ""
        items.append(f"<item><title>{escape(n['titular'])}</title><link>{link}</link><guid isPermaLink=\"true\">"
                     f"{link}</guid><description>{escape(n['bajada'])}</description><category>{escape(n['tema'])}"
                     f"</category><pubDate>{_rfc822(n['fecha'] or '')}</pubDate>{enc}</item>")
    (STAGE / "rss.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Altiplano al Día</title>'
        f"<link>{url}/</link><description>Noticias de Puno y la región</description><language>es-PE</language>"
        f"{''.join(items)}</channel></rss>\n", encoding="utf-8")
    rutas = ["", "lo-ultimo", "clima", "ediciones", "denuncia", "boletin", "nosotros"]
    rutas += [f"seccion/{v}" for v in sorted(set(TEMAS.values()))] + [f"nota/{n['slug']}" for n in notas]
    urls = "".join(f"<url><loc>{url}/{r}</loc></url>" for r in rutas)
    (STAGE / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
                                       f'"http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n', encoding="utf-8")
    (STAGE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {url}/sitemap.xml\n", encoding="utf-8")


def armar(cfg):
    """Arma data/web/. Devuelve (notas, boletines, gids)."""
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "data").mkdir(parents=True)
    usados, notas, gids = set(), [], []
    hoy = storage.dia_editorial()
    solo_hoy = cfg.get("web_solo_hoy", True)        # como el boletín: en la web solo lo del día
    c = storage.consolidado_del_dia(hoy)
    del_boletin = {g["id"] for g in storage.grupos_de_consolidado(c["n"])} if c else set()
    for g in storage.grupos_web():
        # lo del día = lo del boletín de hoy (desde las 3 p. m. de ayer) y lo que salió impreso en él
        if solo_hoy and storage.dia_boletin(g) != hoy and g["id"] not in del_boletin:
            continue
        n = _nota(g, cfg, usados)
        if n:
            notas.append(n)
            gids.append(g["id"])
    bols = _boletines(hoy if solo_hoy else None)
    (STAGE / "data" / "noticias.json").write_text(json.dumps(notas, ensure_ascii=False, indent=1), encoding="utf-8")
    (STAGE / "data" / "boletines.json").write_text(json.dumps(bols, ensure_ascii=False, indent=1), encoding="utf-8")
    if (cfg.get("web_url") or "").strip():
        _extras(notas, cfg["web_url"].strip())
    return notas, bols, gids


def _archivos():
    """{ruta relativa con '/': Path} de todo lo armado."""
    return {p.relative_to(STAGE).as_posix(): p for p in STAGE.rglob("*") if p.is_file()}


def _gestionada(rel: str) -> bool:
    return any(rel == g or (g.endswith("/") and rel.startswith(g)) for g in GESTIONADAS)


# ------------------------------------------------------------------ 2. carpeta local
def a_carpeta(destino: str) -> int:
    base = Path(destino)
    if not base.is_dir():
        raise ErrorWeb(f"No existe la carpeta del sitio: {destino}")
    locales = _archivos()
    cambios = 0
    for rel, src in locales.items():
        dst = base / rel
        if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.read_bytes() == src.read_bytes():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        cambios += 1
    for g in GESTIONADAS:                     # quita lo que ya no va (solo en lo que administra el maquetador)
        if g.endswith("/") and (base / g).is_dir():
            for p in (base / g).rglob("*"):
                if p.is_file() and p.relative_to(base).as_posix() not in locales:
                    p.unlink()
                    cambios += 1
    return cambios


# ------------------------------------------------------------------ 3. GitHub
def _gh(metodo, ruta, token, **kw):
    try:
        r = requests.request(metodo, f"https://api.github.com{ruta}", timeout=90, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}, **kw)
    except requests.RequestException as e:
        raise ErrorWeb(f"Sin conexión con GitHub ({e.__class__.__name__}). Revisa internet.") from e
    if r.status_code == 401:
        raise ErrorWeb("GitHub rechazó el token: revísalo en ⚙ Configuración → Web.")
    if r.status_code == 403:
        raise ErrorWeb("El token de GitHub no tiene permiso de escritura en el repositorio.")
    if r.status_code == 404:
        raise ErrorWeb("No se encontró el repositorio o la rama en GitHub (revisa ⚙ Configuración → Web).")
    if r.status_code == 409:
        raise ErrorWeb("El repositorio de GitHub está vacío: primero hay que subirle el sitio.")
    if r.status_code >= 300:
        raise ErrorWeb(f"GitHub respondió {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def _sha_git(datos: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(datos) + datos).hexdigest()


def a_github(repo, rama, ruta, token, mensaje, avisar=lambda t: None) -> int:
    repo, rama, ruta = repo.strip().strip("/"), (rama or "main").strip(), (ruta or "").strip().strip("/")
    pref = f"{ruta}/" if ruta else ""
    ref = _gh("GET", f"/repos/{repo}/git/ref/heads/{rama}", token)
    padre = ref["object"]["sha"]
    arbol_base = _gh("GET", f"/repos/{repo}/git/commits/{padre}", token)["tree"]["sha"]
    remoto = {e["path"]: e["sha"] for e in _gh("GET", f"/repos/{repo}/git/trees/{arbol_base}?recursive=1", token)
              .get("tree", []) if e["type"] == "blob"}
    entradas = []
    locales = _archivos()
    pendientes = [(rel, p) for rel, p in locales.items() if remoto.get(pref + rel) != _sha_git(p.read_bytes())]
    for i, (rel, p) in enumerate(pendientes, 1):
        avisar(f"Subiendo a la web {i}/{len(pendientes)}: {rel}")
        blob = _gh("POST", f"/repos/{repo}/git/blobs", token,
                   json={"content": base64.b64encode(p.read_bytes()).decode(), "encoding": "base64"})
        entradas.append({"path": pref + rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    for path in remoto:
        rel = path[len(pref):] if path.startswith(pref) else None
        if rel and _gestionada(rel) and rel not in locales:
            entradas.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
    if not entradas:
        return 0
    arbol = _gh("POST", f"/repos/{repo}/git/trees", token, json={"base_tree": arbol_base, "tree": entradas})
    commit = _gh("POST", f"/repos/{repo}/git/commits", token,
                 json={"message": mensaje, "tree": arbol["sha"], "parents": [padre]})
    _gh("PATCH", f"/repos/{repo}/git/refs/heads/{rama}", token, json={"sha": commit["sha"]})
    return len(entradas)


# ------------------------------------------------------------------ todo junto
def publicar(cfg, avisar=lambda t: None) -> str:
    """Arma y publica. Devuelve un resumen para mostrar; lanza ErrorWeb con un mensaje claro si falla."""
    if not configurada(cfg):
        raise ErrorWeb("Falta configurar la web: ve a ⚙ Configuración → Web (carpeta del sitio o GitHub).")
    if not _lock.acquire(blocking=False):
        raise ErrorWeb("Ya se está subiendo la web. Espera a que termine.")
    try:
        avisar("Armando la web con las noticias marcadas 🌐…")
        notas, bols, gids = armar(cfg)
        partes = []
        carpeta = (cfg.get("web_carpeta") or "").strip()
        if carpeta:
            n = a_carpeta(carpeta)
            partes.append(f"carpeta local ({n} cambio(s))")
        repo, token = (cfg.get("web_repo") or "").strip(), get_github_token()
        if repo and token:
            n = a_github(repo, cfg.get("web_rama"), cfg.get("web_ruta"), token,
                         f"Web: {len(notas)} noticias · {datetime.now():%d/%m/%Y %H:%M}", avisar)
            partes.append(f"GitHub ({n} archivo(s))" if n else "GitHub (sin cambios)")
        storage.registrar_subida_web(gids, {n["id"]: n["slug"] for n in notas})
        return f"Web actualizada: {len(notas)} noticia(s) en línea, {len(bols)} boletín(es) PDF → " + ", ".join(partes)
    finally:
        _lock.release()
