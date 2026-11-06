"""Orquesta una revisión: scraping -> detección -> IA -> PDF.
Corre en un hilo aparte; se comunica con la GUI mediante emit(evento, dato)."""
import logging
import random
import re
import threading
from logging.handlers import RotatingFileHandler

import requests
from playwright.sync_api import sync_playwright

from . import storage
from .ai import redactar
from .config import LOG_PATH, get_api_key, page_slug
from .pdfgen import generar_pdf
from .imagenes import descargar_imagen, og_image
from .scraper import Cancelado, NotLoggedIn, navegador_cerrado, open_context, scrape_page

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
# el log rota a los ~2 MB (antes crecía sin límite y la GUI lo lee entero al abrir)
logging.basicConfig(handlers=[RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3,
                                                  encoding="utf-8", delay=True)],
                    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("monitor")

# un solo uso del perfil de navegador a la vez (revisión o login)
browser_lock = threading.Lock()
# botón "Detener": corta la revisión (y la Fase 2) en el próximo punto seguro
detener = threading.Event()


def _es_transitorio(e: Exception) -> bool:
    """Errores que se arreglan solos (sin red, servidor saturado): el post se reintenta luego."""
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return True
    # ai.py lanza RuntimeError("DeepSeek 503: …") / RuntimeError("Claude 529: …")
    return bool(re.match(r"(DeepSeek|Claude) (408|409|425|429|5\d\d):", str(e)))


def procesar_post(post_id: str, cfg: dict, emit) -> str:
    """Redacta y genera el PDF de un post. Devuelve 'ok', 'error', 'sin_key' o 'transitorio'."""
    row = storage.get_post(post_id)
    if not row:
        return "error"
    post = dict(row)
    if not (post.get("texto") or post.get("enlace_ext")):
        storage.update_post(post_id, estado="error", error="Publicación sin texto (solo imagen/video).")
        emit("actualizado", post_id)
        return "error"
    proveedor = cfg.get("proveedor_ia", "deepseek")
    if not get_api_key(proveedor):
        storage.update_post(post_id, estado="pendiente",
                            error=f"Falta la API key de {proveedor}: se procesará al guardarla.")
        emit("error", f"Falta la API key de {proveedor}.")
        emit("actualizado", post_id)
        return "sin_key"
    try:
        emit("status", f"IA ({proveedor}) redactando: {post['pagina']}…")
        ia = redactar(post, cfg)
        pdf = generar_pdf(post, ia)
        storage.update_post(post_id, estado="procesado", titular=ia["TITULAR"], ia_json=ia,
                            pdf_path=str(pdf), error=None)
        log.info("Procesado %s -> %s", post_id, pdf)
        res = "ok"
    except Exception as e:  # noqa: BLE001
        if _es_transitorio(e):
            # sin red o API saturada: queda pendiente y se reintenta en la próxima revisión
            storage.update_post(post_id, estado="pendiente", error=f"Reintentará: {str(e)[:480]}")
            log.warning("Error transitorio procesando %s: %s", post_id, e)
            res = "transitorio"
        else:
            storage.update_post(post_id, estado="error", error=str(e)[:500])
            log.exception("Error procesando %s", post_id)
            res = "error"
        emit("error", f"IA/PDF {post['pagina']}: {e}")
    emit("actualizado", post_id)
    return res


def revisar(cfg: dict, emit) -> None:
    paginas = [p for p in cfg["paginas"] if p.get("activa", True)]
    if not paginas:
        emit("error", "No hay páginas activas.")
        emit("fin", 0)          # sin esto la GUI se queda en «Revisando…» para siempre
        return
    if not browser_lock.acquire(blocking=False):
        emit("error", "El navegador está en uso (¿ventana de login abierta?).")
        emit("fin", 0)
        return
    nuevos = 0
    # detener lo limpia la GUI justo antes de lanzar la revisión: limpiarlo aquí perdía un «Detener» temprano
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
                        if navegador_cerrado(e):
                            log.warning("Navegador cerrado durante la revisión de %s", slug)
                            emit("error", "Se cerró la ventana del navegador: revisión interrumpida. "
                                          "No cierres esa ventana mientras se revisa (usa ■ Detener).")
                            break
                        log.exception("Scraping %s", slug)
                        emit("error", f"{slug}: {e}")
                        continue
                    log.info("%s: %d posts leídos", slug, len(posts))
                    if not posts:
                        log.warning("%s: no se leyó ninguna publicación", slug)
                        emit("error", f"{slug}: no se leyó ninguna publicación (¿página caída o "
                                      "Facebook cambió su diseño?).")
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
                        if estado == "pendiente" and storage.es_video(p):
                            estado = "video"        # no se transcribe: lo decide el equipo (🎬 Videos)
                        if not storage.insert_post(p, estado):
                            continue        # ya estaba (mismo id): no es nuevo
                        if estado in ("pendiente", "video"):
                            try:
                                ruta = descargar_imagen(p.get("imagen_url") or og_image(p.get("enlace_ext")),
                                                        p["id"])
                                if ruta:
                                    storage.update_post(p["id"], imagen=ruta)
                            except Exception:  # noqa: BLE001
                                log.exception("Foto de %s", p["id"])   # sin foto, pero el post sigue
                            nuevos += 1
                            emit("video" if estado == "video" else "nuevo", p["id"])
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

    try:
        fallos_seguidos = 0
        for row in storage.pending_posts():
            if detener.is_set():
                break
            if row["estado"] != "pendiente":
                continue
            res = procesar_post(row["id"], cfg, emit)
            if res == "sin_key":
                break           # los demás quedan pendientes; no repetir el mismo aviso N veces
            fallos_seguidos = fallos_seguidos + 1 if res in ("error", "transitorio") else 0
            if res == "transitorio" or fallos_seguidos >= 3:
                # sin red / API caída: no seguir quemando posts; se reintenta en la próxima revisión
                log.warning("Se pausa la redacción con IA hasta la próxima revisión (%s)", res)
                emit("status", "IA no disponible por ahora: los pendientes se reintentarán "
                               "en la próxima revisión.")
                break

        if cfg.get("fase2_auto", True) and not detener.is_set():
            from .analisis import ciclo_fase2
            ciclo_fase2(cfg, emit, cancelar=detener.is_set)
    except Exception as e:  # noqa: BLE001
        # se registra con traceback (antes solo llegaba el texto a la GUI)
        log.exception("Fallo después del scraping")
        emit("error", f"Procesamiento: {e}")
    if detener.is_set():
        emit("status", f"Revisión detenida por el usuario ({nuevos} publicación(es) nueva(s) guardadas).")
        emit("detenido", None)
    emit("fin", nuevos)
