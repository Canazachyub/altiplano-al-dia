"""Fase 2: cruce de noticias entre medios, clasificación y nota combinada (API de Claude).

Control de gasto:
- Solo se envían las noticias que aún no tienen grupo (las nuevas del ciclo).
- Se comparan contra un resumen de 1 frase de cada grupo de las últimas N horas,
  no contra los textos completos del historial.
- La nota combinada solo se pide para grupos con 2+ fuentes. Si el grupo tiene una sola
  fuente, se reutiliza la nota individual de la Fase 1.
"""
import json
import logging
import re
import threading
import time

import requests

from . import storage
from .ai import ESTILO, RECORDATORIO, fetch_article, menciona_medio
from .config import API_LOG_PATH, get_api_key, nombre_medio

API_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
api_log = logging.getLogger("api")
if not api_log.handlers:
    _h = logging.FileHandler(API_LOG_PATH, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    api_log.addHandler(_h)
    api_log.setLevel(logging.INFO)
    api_log.propagate = False

fase2_lock = threading.Lock()
LOTE = 12  # noticias nuevas por llamada de clasificación


# ------------------------------------------------------------------ llamada a Claude
def claude_json(cfg: dict, system: str, user: str, tarea: str, max_tokens: int = 4000) -> dict:
    key = get_api_key("claude")
    if not key:
        raise RuntimeError("Falta la API key de Claude (pestaña Configuración).")
    model = cfg["modelo_claude"]
    for intento in range(3):
        t0 = time.time()
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=240,
            )
        except requests.RequestException as e:
            api_log.error("%s intento=%d error_red=%s", tarea, intento + 1, e)
            time.sleep(5 * (intento + 1))
            continue
        dt = time.time() - t0
        if r.status_code == 200:
            body = r.json()
            u = body.get("usage", {})
            api_log.info("%s OK modelo=%s in=%s out=%s %.1fs", tarea, model,
                         u.get("input_tokens"), u.get("output_tokens"), dt)
            text = "".join(b.get("text", "") for b in body["content"] if b.get("type") == "text")
            try:
                return _parse_json(text)
            except Exception as e:  # noqa: BLE001
                api_log.error("%s JSON inválido: %s | %s", tarea, e, text[:300])
                raise RuntimeError(f"{tarea}: Claude devolvió JSON inválido") from e
        api_log.error("%s HTTP %s intento=%d %.1fs %s", tarea, r.status_code, intento + 1, dt, r.text[:300])
        if r.status_code in (429, 500, 502, 503, 529):
            time.sleep(10 * (intento + 1))
            continue
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:200]}")
    raise RuntimeError(f"{tarea}: Claude no respondió tras 3 intentos")


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


# ------------------------------------------------------------------ 1. clasificar y agrupar
SYS_CLASIFICAR = """Eres editor de un agregador de noticias de la región Puno (Perú).
Recibes NOTICIAS NUEVAS de varios medios y una lista de GRUPOS RECIENTES (hechos ya registrados).

Para cada noticia nueva decide:
1. "grupo": si trata EXACTAMENTE el mismo hecho concreto que un grupo reciente, pon su número.
   Si trata el mismo hecho que otra noticia nueva sin grupo, dales la MISMA letra ("A", "B"…).
   Si es un hecho distinto, pon una letra propia. Mismo hecho = mismo acontecimiento
   (mismo evento, misma obra, mismo accidente, misma declaración), no solo el mismo tema.
   Titulares distintos pueden ser el mismo hecho.
2. "ambito" (uno):
   DISTRITAL = ocurre en el distrito de Puno (la ciudad de Puno);
   LOCAL = provincia de Puno fuera de la ciudad (Acora, Chucuito, Platería, Paucarcolla, Atuncolla, Capachica, Coata, Huata, Mañazo, Vilque, Tiquillaca, Pichacani, San Antonio, Amantaní);
   REGIONAL = otras provincias de la región Puno (Juliaca/San Román, Azángaro, Ilave/El Collao, Juli, Ayaviri/Melgar, Huancané, Lampa, Carabaya, Sandia, Yunguyo, Moho, Putina) o la región en su conjunto (Gobierno Regional);
   NACIONAL = Perú fuera de Puno o de alcance nacional;
   INTERNACIONAL = fuera del Perú.
   Una institución de la ciudad de Puno (p. ej. la UNA Puno) cuenta como DISTRITAL salvo que el hecho ocurra en otro lugar.
3. "tema" (uno, exactamente de la lista dada).
4. "resumen": una frase de máximo 25 palabras con el hecho (quién, qué, dónde).
5. "es_noticia": false si NO es información periodística: avisos de venta o alquiler,
   publicidad, sorteos, publicaciones sin contenido informativo (solo un enlace o un emoji),
   saludos genéricos. Condolencias, comunicados y convocatorias institucionales SÍ cuentan.

Responde SOLO JSON: {"noticias":[{"id":"n1","grupo":12 | "A","ambito":"...","tema":"...","resumen":"...","es_noticia":true}]}"""


def clasificar_y_agrupar(cfg: dict, emit=lambda *a: None, cancelar=None) -> int:
    nuevos = storage.posts_sin_grupo()
    if not nuevos:
        return 0
    ambitos, temas = cfg["ambitos"], cfg["temas"]
    asignados = 0
    for i in range(0, len(nuevos), LOTE):
        if cancelar and cancelar():
            break
        lote = nuevos[i:i + LOTE]
        recientes = storage.grupos_recientes(cfg["ventana_cruce_horas"])
        emit("status", f"Claude: cruzando {len(lote)} noticias contra {len(recientes)} grupos recientes…")
        mapa = {f"n{j + 1}": p for j, p in enumerate(lote)}
        user = json.dumps({
            "temas_permitidos": temas,
            "grupos_recientes": [{"grupo": g["id"], "resumen": g["resumen"] or g["titular"],
                                  "ambito": g["ambito"]} for g in recientes],
            "noticias_nuevas": [{"id": k, "medio": nombre_medio(p["pagina"], cfg),
                                 "fecha": p["publicado"] or p["detectado"],
                                 "texto": (p["texto"] or "")[:1400]} for k, p in mapa.items()],
        }, ensure_ascii=False)
        res = claude_json(cfg, SYS_CLASIFICAR, user, f"clasificar[{len(lote)}]", 3000)

        letras: dict[str, int] = {}
        ids_recientes = {g["id"] for g in recientes}
        for item in res.get("noticias", []):
            p = mapa.get(str(item.get("id")))
            if not p:
                continue
            ambito = item.get("ambito") if item.get("ambito") in ambitos else "REGIONAL"
            tema = item.get("tema") if item.get("tema") in temas else temas[-1]
            resumen = (item.get("resumen") or "")[:300]
            if item.get("es_noticia") is False:
                # se registra en un grupo descartado: no va al boletín ni al dashboard
                gid = storage.crear_grupo(ambito, tema, resumen)
                storage.asignar_post(p["id"], gid, ambito, tema, resumen)
                storage.actualizar_grupo(gid, estado_pdf="descartado", nota_pendiente=0,
                                         medios=nombre_medio(p["pagina"], cfg), n_fuentes=1,
                                         fecha_hecho=p["publicado"] or p["detectado"])
                api_log.info("descartado (no es noticia): %s %s", p["id"], resumen[:80])
                asignados += 1
                continue
            g = item.get("grupo")
            gid = None
            if isinstance(g, int) or (isinstance(g, str) and g.isdigit()):
                gid = int(g) if int(g) in ids_recientes else None
            if gid is None:
                letra = str(g or f"_{item['id']}")
                if letra not in letras:
                    letras[letra] = storage.crear_grupo(ambito, tema, resumen)
                gid = letras[letra]
            storage.asignar_post(p["id"], gid, ambito, tema, resumen)
            asignados += 1
    return asignados


# ------------------------------------------------------------------ 2. nota combinada
SYS_COMBINAR = ESTILO + """

Recibes varias publicaciones de distintas fuentes sobre el MISMO hecho. Escribe UNA sola nota
propia, más completa que cada fuente por separado, juntando quién, qué, cuándo, dónde, cifras
y declaraciones de todas ellas.

- TITULAR, BAJADA y CUERPO NO nombran a ningún medio (ver la regla PROHIBIDO).
- Si las fuentes dan datos distintos (cifras, nombres, fechas, lugares), NO elijas uno ni
  digas quién dijo qué: en el CUERPO preséntalo de forma neutral ("las cifras preliminares
  varían entre 12 y 15 heridos", "la hora exacta aún no está confirmada") y registra el
  detalle por medio en DISCREPANCIAS.
- DATOS, DISCREPANCIAS y FUENTES son fichas internas de verificación para el editor, no se
  publican: ahí SÍ se indica qué medio aporta cada dato.

Responde SOLO JSON:
{
 "ANTETITULO": "…",
 "TITULAR": "…",
 "BAJADA": "…",
 "LUGAR": "…",
 "PIE_FOTO": "…",
 "CUERPO": "párrafos separados por \\n\\n",
 "DATOS": [{"dato": "…", "fuentes": ["medio", "…"]}],
 "DISCREPANCIAS": [{"aspecto": "…", "versiones": [{"medio": "…", "dice": "…"}]}],
 "FUENTES": [{"medio": "…", "aporte": "qué aportó este medio, 1 línea"}],
 "ambito": "DISTRITAL|LOCAL|REGIONAL|NACIONAL|INTERNACIONAL",
 "tema": "uno de la lista"
}"""


def _nota_desde_fase1(post, cfg) -> dict | None:
    """Grupo de una sola fuente: se adapta la nota individual de la Fase 1 (sin gastar API)."""
    if not post["ia_json"]:
        return None
    ia = json.loads(post["ia_json"])
    medio = nombre_medio(post["pagina"], cfg)
    return {
        "TITULAR": ia.get("TITULAR", ""), "BAJADA": ia.get("BAJADA", ""), "CUERPO": ia.get("CUERPO", ""),
        "ANTETITULO": ia.get("ANTETITULO", ""), "LUGAR": ia.get("LUGAR", ""), "PIE_FOTO": ia.get("PIE_FOTO", ""),
        "DATOS": [{"dato": d, "fuentes": [medio]} for d in ia.get("DATOS", [])],
        "DISCREPANCIAS": [], "FUENTES": [{"medio": medio, "aporte": "fuente única"}],
        "_origen": f"fase1:{ia.get('_proveedor', '')}",
    }


def generar_notas(cfg: dict, emit=lambda *a: None, cancelar=None) -> int:
    hechas = 0
    for g in storage.grupos_con_nota_pendiente():
        if cancelar and cancelar():
            break
        posts = storage.posts_de_grupo(g["id"])
        if not posts:
            continue
        medios = list(dict.fromkeys(nombre_medio(p["pagina"], cfg) for p in posts))
        nota = None
        if len(posts) == 1:
            nota = _nota_desde_fase1(posts[0], cfg)
        if nota is None:
            emit("status", f"Claude: nota combinada del grupo {g['id']} ({len(posts)} fuentes)…")
            fuentes = []
            for p in posts:
                f = {"medio": nombre_medio(p["pagina"], cfg), "fecha": p["publicado"] or p["detectado"],
                     "texto": (p["texto"] or "")[:3500]}
                ext = fetch_article(p["enlace_ext"], 3500)
                if ext:
                    f["nota_enlazada"] = ext
                fuentes.append(f)
            user = json.dumps({"temas_permitidos": cfg["temas"], "fuentes": fuentes}, ensure_ascii=False)
            try:
                tarea = f"combinar[g{g['id']},{len(posts)}f]"
                nota = claude_json(cfg, SYS_COMBINAR, user, tarea, 5000)
                culpable = menciona_medio(nota, medios + [p["pagina"] for p in posts])
                if culpable:  # un solo reintento, con recordatorio
                    api_log.info("%s nombraba al medio %s: se reescribe", tarea, culpable)
                    nota = claude_json(cfg, SYS_COMBINAR, user + RECORDATORIO.format(medio=culpable),
                                       tarea + "+reintento", 5000)
                nota["_origen"] = "claude:combinada"
            except Exception as e:  # noqa: BLE001
                emit("error", f"Nota grupo {g['id']}: {e}")
                continue
        ambito = nota.get("ambito") if nota.get("ambito") in cfg["ambitos"] else g["ambito"]
        tema = nota.get("tema") if nota.get("tema") in cfg["temas"] else g["tema"]
        if g["nota_json"]:
            previa = json.loads(g["nota_json"])
            for k in ("_IMAGEN", "_SIN_FOTO"):
                if previa.get(k):
                    nota[k] = previa[k]
        nota["LINKS"] = [{"medio": nombre_medio(p["pagina"], cfg), "fecha": p["publicado"] or p["detectado"],
                          "facebook": p["permalink"], "externo": p["enlace_ext"]} for p in posts]
        fechas = [p["publicado"] or p["detectado"] for p in posts]
        storage.actualizar_grupo(
            g["id"], nota_json=nota, titular=nota.get("TITULAR") or g["titular"], ambito=ambito, tema=tema,
            n_fuentes=len(posts), medios=", ".join(medios), fecha_hecho=max(fechas),
            nota_pendiente=0, actualizado=storage.now_iso())
        hechas += 1
        emit("grupo", g["id"])
    return hechas


# ------------------------------------------------------------------ ciclo completo
def ciclo_fase2(cfg: dict, emit=lambda *a: None, forzar_pdf: bool = False, cancelar=None) -> dict:
    """Cruce -> clasificación -> notas -> Excel -> PDF consolidado. Devuelve un resumen."""
    from .consolidado import generar_consolidado
    from .excel import exportar_excel

    if not fase2_lock.acquire(blocking=False):
        emit("error", "La Fase 2 ya está corriendo.")
        return {}
    out = {"asignados": 0, "notas": 0, "excel": None, "pdf": None}
    try:
        storage.init_db()
        if get_api_key("claude"):
            out["asignados"] = clasificar_y_agrupar(cfg, emit, cancelar)
            out["notas"] = generar_notas(cfg, emit, cancelar)
        else:
            emit("error", "Fase 2 omitida: falta la API key de Claude.")
        try:
            out["excel"] = str(exportar_excel(cfg))
        except Exception as e:  # noqa: BLE001
            emit("error", f"Excel: {e}")
        if (forzar_pdf or cfg.get("pdf_consolidado_auto", True)) and not (cancelar and cancelar()):
            out["pdf"] = generar_consolidado(cfg)
            if out["pdf"]:
                emit("consolidado", str(out["pdf"]))
    except Exception as e:  # noqa: BLE001
        api_log.exception("ciclo_fase2")
        emit("error", f"Fase 2: {e}")
    finally:
        fase2_lock.release()
    emit("fase2_fin", out)
    return out
