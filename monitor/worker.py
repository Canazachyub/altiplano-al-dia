"""Orquesta una revisión: scraping -> detección -> IA -> PDF.
Corre en un hilo aparte; se comunica con la GUI mediante emit(evento, dato)."""
import logging
import random
import threading

from playwright.sync_api import sync_playwright

from . import storage
from .ai import redactar
from .config import LOG_PATH, get_api_key, page_slug
from .pdfgen import generar_pdf
from .imagenes import descargar_imagen, og_image
from .scraper import Cancelado, NotLoggedIn, open_context, scrape_page

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("monitor")

# un solo uso del perfil de navegador a la vez (revisión o login)
browser_lock = threading.Lock()
# botón "Detener": corta la revisión (y la Fase 2) en el próximo punto seguro
detener = threading.Event()


def procesar_post(post_id: str, cfg: dict, emit) -> None:
    row = storage.get_post(post_id)
    if not row:
        return
    post = dict(row)
    if not (post.get("texto") or post.get("enlace_ext")):
        storage.update_post(post_id, estado="error", error="Publicación sin texto (solo imagen/video).")
        emit("actualizado", post_id)
        return
    if not get_api_key(cfg["proveedor_ia"]):
        storage.update_post(post_id, estado="pendiente",
                            error=f"Falta la API key de {cfg['proveedor_ia']}: se procesará al guardarla.")
        emit("error", f"Falta la API key de {cfg['proveedor_ia']}.")
        emit("actualizado", post_id)
        return
    try:
        emit("status", f"IA ({cfg['proveedor_ia']}) redactando: {post['pagina']}…")
        ia = redactar(post, cfg)
        pdf = generar_pdf(post, ia)
        storage.update_post(post_id, estado="procesado", titular=ia["TITULAR"], ia_json=ia,
                            pdf_path=str(pdf), error=None)
        log.info("Procesado %s -> %s", post_id, pdf)
    except Exception as e:  # noqa: BLE001
        storage.update_post(post_id, estado="error", error=str(e)[:500])
        log.exception("Error procesando %s", post_id)
        emit("error", f"IA/PDF {post['pagina']}: {e}")
    emit("actualizado", post_id)


def revisar(cfg: dict, emit) -> None:
    paginas = [p for p in cfg["paginas"] if p.get("activa", True)]
    if not paginas:
        emit("error", "No hay páginas activas.")
        return
    if not browser_lock.acquire(blocking=False):
        emit("error", "El navegador está en uso (¿ventana de login abierta?).")
        return
    nuevos = 0
    detener.clear()
    try:
        with sync_playwright() as pw:
            ctx = open_context(pw, cfg.get("navegador_canal"), cfg.get("headless", False))
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                for i, pg in enumerate(paginas):
                    if detener.is_set():
                        break
                    slug = page_slug(pg["url"])
                    emit("status", f"Revisando {slug} ({i + 1}/{len(paginas)})…")
                    primera_vez = not storage.page_has_history(slug)
                    try:
                        posts = scrape_page(page, pg["url"], cfg["posts_por_revision"],
                                            storage.is_known, log=log.info, cancelar=detener.is_set)
                    except Cancelado:
                        break
                    except NotLoggedIn as e:
                        emit("error", str(e))
                        break
                    except Exception as e:  # noqa: BLE001
                        if "has been closed" in str(e) or "Target closed" in str(e):
                            log.warning("Navegador cerrado durante la revisión de %s", slug)
                            emit("error", "Se cerró la ventana del navegador: revisión interrumpida. "
                                          "No cierres esa ventana mientras se revisa (usa ■ Detener).")
                            break
                        log.exception("Scraping %s", slug)
                        emit("error", f"{slug}: {e}")
                        continue
                    log.info("%s: %d posts leídos", slug, len(posts))
                    ya_procesados = 0
                    for p in posts:
                        if storage.is_known(p["id"], p["text_hash"]):
                            continue
                        estado = "pendiente"
                        if primera_vez:
                            # en la primera revisión solo se procesan los N más recientes;
                            # el resto queda como "base" para no gastar IA en lo viejo
                            if ya_procesados >= cfg["procesar_en_primera_revision"]:
                                estado = "base"
                            ya_procesados += 1
                        storage.insert_post(p, estado)
                        if estado == "pendiente":
                            ruta = descargar_imagen(p.get("imagen_url") or og_image(p.get("enlace_ext")), p["id"])
                            if ruta:
                                storage.update_post(p["id"], imagen=ruta)
                        if estado == "pendiente":
                            nuevos += 1
                            emit("nuevo", p["id"])
                    if i < len(paginas) - 1:
                        detener.wait(random.uniform(8, 20))   # pausa entre páginas, cortable
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as e:  # noqa: BLE001
        log.exception("Fallo del navegador")
        emit("error", f"Navegador: {e}")
    finally:
        browser_lock.release()

    pendientes = storage.pending_posts()
    for row in pendientes:
        if detener.is_set():
            break
        if row["estado"] == "pendiente":
            procesar_post(row["id"], cfg, emit)

    if cfg.get("fase2_auto", True) and not detener.is_set():
        from .analisis import ciclo_fase2
        ciclo_fase2(cfg, emit, cancelar=detener.is_set)
    if detener.is_set():
        emit("status", f"Revisión detenida por el usuario ({nuevos} publicación(es) nueva(s) guardadas).")
        emit("detenido", None)
    emit("fin", nuevos)
