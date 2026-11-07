"""Limpieza de las fotos de Facebook para el boletín.

Las páginas publican sus fotos con rótulos encima (logo del medio, titular, iconos de redes, teléfono,
franjas de color, marcos). Antes de maquetar:

1. Qwen2.5-VL (Ollama, local) mira cada foto y dice si trae rótulos encima. Las limpias se dejan como están.
2. Las que sí traen pasan por Qwen Imagen Studio (Qwen-Image 2.1, local, C:\\PROGRAMACION\\Qwen imagen), que
   borra solo lo sobrepuesto y rellena la escena sin cambiar personas, lugares ni encuadre.

El original nunca se toca. Las versiones limpias van a data/fotos_limpias/ y un índice recuerda qué se hizo con
cada foto, así cada una se procesa una sola vez. En la vista previa se puede volver a la original.
"""
import base64
import hashlib
import io
import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image

from .config import BASE_DIR

log = logging.getLogger("monitor")

DIR = BASE_DIR / "data" / "fotos_limpias"
INDICE = DIR / "indice.json"
STUDIO_DIR = Path(r"C:\PROGRAMACION\Qwen imagen\qwen-imagen-studio")
STUDIO_URL = "http://127.0.0.1:7871"
OLLAMA_URL = "http://127.0.0.1:11434"
MODELO_VL = "qwen2.5vl:7b"
SEMILLA = 1234                       # fija: la misma foto da siempre el mismo resultado

PREGUNTA = ("Look at this news photo. Has someone added graphic elements ON TOP of the photograph, such as "
            "overlaid text, headlines, captions, a media logo or watermark, social media icons, phone numbers, "
            "colored banners or a decorative frame/border? Ignore text that is physically part of the scene "
            "(street signs, shirts, posters on walls). Answer only YES or NO.")
ORDEN = ("Edit image 1: remove ALL overlaid graphics added on top of the photograph: text, headlines, captions, "
         "logos, watermarks, social media icons, phone numbers, colored banners, labels, borders, frames and "
         "decorative dot patterns. Fill those areas naturally with the surrounding scene. Keep the photograph "
         "itself exactly the same: same people, faces, buildings, objects, composition, colors and lighting. "
         "Add nothing new.")

COBERTURA = ("What percentage (0-100) of this image's area is covered by graphics added on top of the photograph "
             "(overlaid text, headline panels, colored banners, logos, watermarks, frames) or is not a photograph "
             "at all (a scanned newspaper page, a text card, a meme)? Answer only the number.")
COBERTURA_OK = 25                    # una foto limpia (o bien limpiada) queda por debajo de esto

_lock = threading.Lock()             # una sola limpieza a la vez (la GPU no da para dos)
_indice_lock = threading.Lock()


# ------------------------------------------------------------------ índice
def _clave(ruta: str) -> str:
    return str(Path(ruta).resolve()).lower()


def _leer() -> dict:
    try:
        return json.loads(INDICE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _anotar(origen: str, **datos) -> None:
    with _indice_lock:
        d = _leer()
        d[_clave(origen)] = {**datos, "fecha": datetime.now().isoformat(timespec="seconds")}
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = INDICE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(INDICE)


def _sumar(origen: str, **datos) -> None:
    with _indice_lock:
        d = _leer()
        d[_clave(origen)] = {**d.get(_clave(origen), {}), **datos}
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = INDICE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(INDICE)


def estado(ruta: str) -> dict:
    return _leer().get(_clave(ruta), {})


def ruta_para(ruta: str | None) -> str | None:
    """La versión limpia de una foto si existe; si no, la original."""
    if not ruta:
        return ruta
    e = estado(ruta)
    if e.get("estado") == "limpia" and e.get("limpia") and Path(e["limpia"]).exists():
        cl, co = e.get("cobertura_limpia"), e.get("cobertura")
        if cl is not None and co is not None and cl > co:
            return ruta                  # la limpieza no sirvió (el panel de texto sigue ahí)
        return e["limpia"]
    return ruta


def descartar(ruta: str) -> None:
    """Olvida lo hecho con una foto (para volver a limpiarla)."""
    with _indice_lock:
        d = _leer()
        d.pop(_clave(ruta), None)
        DIR.mkdir(parents=True, exist_ok=True)
        INDICE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ servicios locales
def _get(url, timeout=5):
    return json.load(urllib.request.urlopen(url, timeout=timeout))


def _post(url, datos, timeout=60):
    req = urllib.request.Request(url, json.dumps(datos).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def studio_listo() -> bool:
    try:
        return _get(STUDIO_URL + "/api/status", 3).get("app") == "qwen-turbo-local"
    except Exception:  # noqa: BLE001
        return False


def iniciar_studio(espera=90) -> bool:
    """Abre Qwen Imagen Studio en segundo plano (sin navegador) si no está abierto."""
    if studio_listo():
        return True
    script = STUDIO_DIR / "scripts" / "start.ps1"
    if not script.exists():
        return False
    import os
    env = dict(os.environ, PSModulePath="")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                      "-NoBrowser"], cwd=str(STUDIO_DIR), env=env, creationflags=0x08000000)  # sin ventana
    fin = time.time() + espera
    while time.time() < fin:
        if studio_listo():
            return True
        time.sleep(2)
    return False


def _jpeg_b64(ruta, lado=768) -> str:
    im = Image.open(ruta).convert("RGB")
    im.thumbnail((lado, lado))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return base64.b64encode(b.getvalue()).decode()


def tiene_rotulos(ruta: str) -> bool | None:
    """True/False según Qwen2.5-VL; None si Ollama no está disponible."""
    try:
        r = _post(OLLAMA_URL + "/api/generate", {
            "model": MODELO_VL, "prompt": PREGUNTA, "images": [_jpeg_b64(ruta)], "stream": False,
            "options": {"temperature": 0}, "keep_alive": "1m"}, timeout=300)
    except Exception as e:  # noqa: BLE001
        log.info("Ollama no disponible para revisar fotos: %s", e)
        return None
    return r.get("response", "").strip().upper().startswith("Y")


def _cobertura_vl(ruta: str) -> int | None:
    try:
        r = _post(OLLAMA_URL + "/api/generate", {
            "model": MODELO_VL, "prompt": COBERTURA, "images": [_jpeg_b64(ruta)], "stream": False,
            "options": {"temperature": 0}, "keep_alive": "1m"}, timeout=300)
    except Exception as e:  # noqa: BLE001
        log.info("Ollama no disponible para medir rótulos: %s", e)
        return None
    import re
    m = re.search(r"\d+", r.get("response", ""))
    return max(0, min(100, int(m.group()))) if m else None


def cobertura(ruta: str) -> int | None:
    """% de la imagen tapado por rótulos (o que no es foto: página de diario, meme). Se guarda en el índice."""
    e = estado(ruta)
    if e.get("estado") == "sin_rotulos":
        return 0
    if e.get("cobertura") is not None:
        return e["cobertura"]
    c = _cobertura_vl(ruta)
    if c is not None:
        _sumar(ruta, cobertura=c)
    return c


def puntaje(ruta: str) -> int:
    """Cuánto rótulo tendría la foto en el boletín: la mejor entre la original y su versión limpia."""
    e = estado(ruta)
    if e.get("estado") == "sin_rotulos":
        return 0
    co = cobertura(ruta)
    co = 100 if co is None else co
    cl = e.get("cobertura_limpia")
    return min(co, cl) if cl is not None else co


def _soltar_vl():
    """Libera la memoria de la GPU que usa Qwen2.5-VL antes de editar imágenes."""
    try:
        _post(OLLAMA_URL + "/api/generate", {"model": MODELO_VL, "keep_alive": 0}, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def _medidas(w, h, lado=1024):
    f = lado / max(w, h)
    return max(256, round(w * f / 32) * 32), max(256, round(h * f / 32) * 32)


def _destino(ruta: str) -> Path:
    return DIR / (hashlib.sha1(_clave(ruta).encode()).hexdigest()[:16] + ".jpg")


def limpiar(ruta: str, cancelar=None, semilla=None) -> str:
    """Borra los rótulos de una foto con Qwen Imagen Studio. Devuelve la ruta de la versión limpia."""
    im = Image.open(ruta).convert("RGB")
    w, h = _medidas(*im.size)
    b = io.BytesIO()
    im.resize((w, h), Image.LANCZOS).save(b, "PNG")
    ref = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    datos = {"prompt": ORDEN, "refs": [ref], "width": w, "height": h, "steps": 6, "count": 1,
             "seed": semilla or SEMILLA}
    for _ in range(90):                                   # si el estudio está ocupado, se espera el turno
        try:
            job = _post(STUDIO_URL + "/api/jobs", datos, 60)
            break
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise RuntimeError(json.loads(e.read() or b"{}").get("error") or str(e)) from e
            time.sleep(4)
    else:
        raise RuntimeError("Qwen Imagen Studio sigue ocupado con otra imagen")
    while job["status"] not in ("completed", "failed", "cancelled", "error"):
        if cancelar and cancelar():
            try:
                _post(f"{STUDIO_URL}/api/jobs/{job['id']}/cancel", {}, 10)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(2)
        job = _get(f"{STUDIO_URL}/api/jobs/{job['id']}", 30)
    if not job.get("images"):
        raise RuntimeError(job.get("message") or "Qwen Imagen Studio no devolvió imagen")
    png = urllib.request.urlopen(STUDIO_URL + job["images"][0]["src"], timeout=60).read()
    DIR.mkdir(parents=True, exist_ok=True)
    destino = _destino(ruta)
    recortar_franjas(Image.open(io.BytesIO(png)).convert("RGB")).save(destino, "JPEG", quality=92)
    return str(destino)


def recortar_franjas(im: Image.Image, umbral=7.0, minimo=0.55) -> Image.Image:
    """Quita las franjas lisas que quedan en los bordes donde había un panel de texto (fondo gris o de un
    solo color). Nunca recorta más del 45 % por lado."""
    from PIL import ImageStat
    g = im.convert("L")
    w, h = g.size

    def lisa(caja):
        return ImageStat.Stat(g.crop(caja)).stddev[0] < umbral

    paso = max(2, h // 100)
    top, bot, izq, der = 0, h, 0, w
    while bot - top > h * minimo and lisa((0, bot - paso, w, bot)):
        bot -= paso
    while bot - top > h * minimo and lisa((0, top, w, top + paso)):
        top += paso
    paso = max(2, w // 100)
    while der - izq > w * minimo and lisa((der - paso, top, der, bot)):
        der -= paso
    while der - izq > w * minimo and lisa((izq, top, izq + paso, bot)):
        izq += paso
    return im if (top, bot, izq, der) == (0, h, 0, w) else im.crop((izq, top, der, bot))


# ------------------------------------------------------------------ elegir la mejor foto
def candidatas(g, nota=None) -> list[str]:
    """Fotos válidas (sin repetir) de todas las publicaciones de la nota."""
    from . import storage
    vistas, out = set(), []
    for p in storage.posts_de_grupo(g["id"]):
        ruta = p["imagen"] if "imagen" in p.keys() else None
        if not ruta or _clave(ruta) in vistas:
            continue
        vistas.add(_clave(ruta))
        try:
            with Image.open(ruta) as im:
                im.verify()
            out.append(ruta)
        except Exception:  # noqa: BLE001
            continue
    return out


def _tiene_rotulos_cache(ruta: str) -> bool | None:
    e = estado(ruta)
    if e.get("estado") in ("sin_rotulos",):
        return False
    if e.get("estado") == "limpia" or e.get("rotulos") is True:
        return True
    t = tiene_rotulos(ruta)
    if t is False:
        _anotar(ruta, estado="sin_rotulos")
    elif t is True:
        _anotar(ruta, rotulos=True)        # sin «estado»: la limpieza la sigue tomando como pendiente
    return t


def elegir_mejor(g, emit=None) -> str | None:
    """Entre las fotos de todos los medios de la nota, guarda en _IMAGEN_IA la mejor: primero las que NO
    traen logos, marcas de agua, titulares ni textos de edición encima (Qwen2.5-VL); a igualdad, la de
    mayor resolución. No toca las notas con foto elegida a mano (_IMAGEN) o marcadas sin foto."""
    from . import storage
    try:
        nota = json.loads(g["nota_json"] or "{}")
    except (TypeError, ValueError):
        return None
    if nota.get("_IMAGEN") or nota.get("_SIN_FOTO"):
        return None
    cands = candidatas(g, nota)
    if len(cands) < 2:
        return None
    puntos = []
    for ruta in cands:
        t = _tiene_rotulos_cache(ruta)
        if t is None:                      # sin Ollama no se puede elegir: se deja como está
            return None
        with Image.open(ruta) as im:
            area = im.size[0] * im.size[1]
        # una foto con una marca de agua leve gana a una con un panel de titular o a una página de diario
        pts = 0 if not t else max(1, puntaje(ruta))
        puntos.append((pts // 10, -area, ruta))
    mejor = min(puntos)[2]
    if nota.get("_IMAGEN_IA") != mejor:
        nota["_IMAGEN_IA"] = mejor
        with storage._conn() as c:
            c.execute("UPDATE grupos SET nota_json=? WHERE id=?", (json.dumps(nota, ensure_ascii=False), g["id"]))
        storage.marcar_web_editada([g["id"]])
        log.info("Foto elegida por la IA para el grupo %s: %s", g["id"], mejor)
    return mejor


def _grupos_objetivo():
    from . import storage
    grupos = {g["id"]: g for g in storage.grupos_para_pdf()}
    for g in storage.grupos_web():
        if storage.estado_web(g) != "en_linea":
            grupos.setdefault(g["id"], g)
    return grupos


def elegir_fotos(grupos, emit=lambda *a: None, cancelar=None) -> int:
    """Corre elegir_mejor en las notas con varias fotos. Devuelve cuántas cambiaron de foto."""
    n = 0
    varias = [g for g in grupos if len(candidatas(g)) > 1]
    for i, g in enumerate(varias, 1):
        if cancelar and cancelar():
            break
        emit("status", f"Eligiendo la mejor foto entre los medios ({i}/{len(varias)})…")
        antes = None
        try:
            antes = json.loads(g["nota_json"] or "{}").get("_IMAGEN_IA")
        except (TypeError, ValueError):
            pass
        nueva = elegir_mejor(g, emit)
        if nueva and nueva != antes:
            n += 1
    return n


# ------------------------------------------------------------------ paso del boletín
def fotos_del_boletin():
    """[(gid, ruta_original, impacto)] de las fotos que usaría el próximo boletín y de las noticias
    marcadas 🌐 que todavía no se subieron a la web (también deben salir sin logos ni textos encima)."""
    from . import storage
    from .consolidado import imagen_original
    from .editorial import impacto
    out = []
    grupos = _grupos_objetivo()
    for g in grupos.values():
        try:
            nota = json.loads(g["nota_json"])
        except (TypeError, ValueError):
            continue
        if nota.get("_FOTO_ORIGINAL"):
            continue
        ruta = imagen_original(g, nota)
        if ruta:
            out.append((g["id"], ruta, impacto(nota)))
    out.sort(key=lambda x: -x[2])                         # primero las de más impacto (portada)
    return out


def fotos_web_sin_revisar() -> int:
    """Fotos de noticias pendientes de subir a la web que aún no pasaron por la limpieza."""
    from . import storage
    pend = {g["id"] for g in storage.grupos_web() if storage.estado_web(g) != "en_linea"}
    return sum(1 for gid, ruta, _ in fotos_del_boletin()
               if gid in pend and estado(ruta).get("estado") in (None, "error"))


def paso_limpieza(cfg, emit, cancelar=None, forzar=False) -> dict:
    """Revisa y limpia las fotos del próximo boletín. Devuelve {"limpias", "sin_rotulos", "errores"}."""
    r = {"limpias": 0, "sin_rotulos": 0, "errores": 0, "omitido": ""}
    if not cfg.get("limpiar_fotos", True):
        r["omitido"] = "desactivado"
        return r
    if not _lock.acquire(blocking=False):
        r["omitido"] = "ya en curso"
        return r
    try:
        # 0) en las notas que juntan varios medios, la IA elige la foto sin logos ni textos encima
        elegir_fotos(list(_grupos_objetivo().values()), emit, cancelar)
        fotos = [f for f in fotos_del_boletin() if forzar or estado(f[1]).get("estado") in (None, "error")]
        if not fotos:
            return r
        # 1) qué fotos traen rótulos (rápido)
        sucias = []
        for i, (gid, ruta, _) in enumerate(fotos, 1):
            if cancelar and cancelar():
                return r
            emit("status", f"Revisando rótulos en las fotos {i}/{len(fotos)}…")
            t = tiene_rotulos(ruta)
            if t is False:
                _anotar(ruta, estado="sin_rotulos")
                r["sin_rotulos"] += 1
            else:
                sucias.append((gid, ruta))
        _soltar_vl()
        if not sucias:
            return r
        if not iniciar_studio():
            emit("error", "Limpieza de fotos: no se pudo abrir Qwen Imagen Studio "
                          f"({STUDIO_DIR}). Las fotos salen como vienen de Facebook.")
            r["omitido"] = "sin estudio"
            return r
        # 2) borrar los rótulos (lento: ~1 min por foto en la GPU)
        for i, (gid, ruta) in enumerate(sucias, 1):
            if cancelar and cancelar():
                break
            emit("status", f"Limpiando fotos con Qwen ({i}/{len(sucias)}): se quitan logos y textos encima…")
            try:
                t0 = time.time()
                previa = _destino(ruta)            # ya limpiada antes (p. ej. se perdió el índice)
                limpia = str(previa) if previa.exists() and not forzar else limpiar(ruta, cancelar)
                co = estado(ruta).get("cobertura")
                _anotar(ruta, estado="limpia", limpia=limpia, gid=gid, segundos=round(time.time() - t0),
                        **({"cobertura": co} if co is not None else {}))
                r["limpias"] += 1
            except Exception as e:  # noqa: BLE001
                log.exception("Limpieza de foto %s", ruta)
                _anotar(ruta, estado="error", error=str(e)[:300])
                r["errores"] += 1
        verificar_limpias([ruta for _, ruta in sucias], emit, cancelar)
        return r
    finally:
        _lock.release()


def verificar_limpias(rutas, emit=lambda *a: None, cancelar=None) -> int:
    """Mide cuánto rótulo quedó en cada foto limpiada. Si la limpieza no sirvió (el panel sigue), ruta_para
    vuelve a la original y la IA elige otra foto de la nota si hay una mejor. Devuelve cuántas fallaron."""
    fallas = 0
    for i, ruta in enumerate(rutas, 1):
        if cancelar and cancelar():
            break
        e = estado(ruta)
        if e.get("estado") != "limpia" or not e.get("limpia") or e.get("cobertura_limpia") is not None:
            continue
        emit("status", f"Comprobando que las fotos quedaron sin rótulos ({i}/{len(rutas)})…")
        cobertura(ruta)
        cl = _cobertura_vl(e["limpia"])
        if cl is None:
            continue
        _sumar(ruta, cobertura_limpia=cl)
        if cl >= COBERTURA_OK:
            fallas += 1
            log.info("La limpieza no quitó los rótulos de %s (queda %s %%)", ruta, cl)
    _soltar_vl()
    return fallas


def en_segundo_plano(cfg, emit, cancelar=None, al_terminar=None) -> bool:
    """Lanza paso_limpieza en un hilo propio (no bloquea el armado del periódico)."""
    if _lock.locked() or not cfg.get("limpiar_fotos", True):
        return False

    def run():
        try:
            res = paso_limpieza(cfg, emit, cancelar)
            if res["limpias"] or res["errores"]:
                emit("aviso_ok", f"Fotos limpias: {res['limpias']} sin rótulos nuevas"
                     + (f" · {res['errores']} no se pudieron limpiar" if res["errores"] else "")
                     + ". La vista previa ya las usa.")
        except Exception as e:  # noqa: BLE001
            log.exception("Limpieza de fotos")
            emit("error", f"Limpieza de fotos: {e}")
        finally:
            if al_terminar:
                al_terminar()

    threading.Thread(target=run, daemon=True).start()
    return True
