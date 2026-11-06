"""Fase 2: cruce de noticias entre medios, clasificación y nota combinada (API de Claude).

Control de gasto:
- Solo se envían las noticias que aún no tienen grupo (las nuevas del ciclo).
- Se comparan contra un resumen de 1 frase de cada grupo de las últimas N horas,
  no contra los textos completos del historial. Esa lista se toma UNA vez por ciclo y va
  en un bloque cacheado: los lotes siguientes la leen de la caché a ~10 % del precio.
- La nota combinada solo se pide para grupos con 2+ medios distintos. Si el grupo tiene una
  sola fuente (o varias publicaciones del mismo medio), se reutiliza la nota de la Fase 1.
"""
import json
import re
import threading

from . import storage
from .ai import (ESTILO, RECORDATORIO, ErrorFormato, ErrorIA, api_log, esfuerzo_cfg, fecha_legible,
                 fetch_article, llamar_claude, menciona_medio, normalizar_nota, parse_json)
from .config import get_api_key, nombre_medio

fase2_lock = threading.Lock()
LOTE = 12  # noticias nuevas por llamada de clasificación
MAX_GRUPOS_RECIENTES = 150  # tope de grupos contra los que se compara (resumen de 1 frase c/u)
MAX_FUENTES_COMBINADA = 8  # publicaciones que se envían para una nota combinada
PRESUPUESTO_FUENTES = 28000  # caracteres totales de texto de fuentes por nota combinada

# errores que no se arreglan reintentando con el siguiente lote o grupo
_FATAL = re.compile(r"API key|saldo|no existe|cancelado|permiso")


def _parse_json(text: str) -> dict:  # compatibilidad
    return parse_json(text)


# ------------------------------------------------------------------ llamada a Claude
def claude_json(cfg: dict, system: str, user, tarea: str, max_tokens: int = 4000, *,
                esfuerzo: str = "low", cancelar=None) -> dict:
    """Llama a Claude y devuelve el JSON de la respuesta (con un reintento si viene roto).

    `max_tokens` es la salida visible esperada; se suma margen porque los modelos actuales
    razonan antes de responder y ese razonamiento también cuenta en el límite.
    `user` puede ser texto o una lista de bloques de contenido (para cachear una parte)."""
    key = get_api_key("claude")
    if not key:
        raise ErrorIA("Falta la API key de Claude (pestaña Configuración).")
    model = cfg["modelo_claude"]
    tope = max(max_tokens * 2, 12000)
    for intento in (1, 2):
        sufijo = "" if intento == 1 else "+json"
        text = llamar_claude(model, system, user, key, max_tokens=tope, esfuerzo=esfuerzo_cfg(cfg, esfuerzo),
                             tarea=tarea + sufijo, cancelar=cancelar)
        try:
            return parse_json(text)
        except ErrorFormato as e:
            api_log.error("%s%s %s | %s", tarea, sufijo, e, text[:300])
            if intento == 2:
                raise ErrorIA(f"{tarea}: Claude devolvió JSON inválido dos veces") from e
    raise AssertionError("inalcanzable")


def _gid(valor, validos: set) -> int | None:
    """Número de grupo existente a partir de 12, "12" o "g12"; None si no es uno válido."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)) and int(valor) in validos:
        return int(valor)
    m = re.fullmatch(r"\s*(?:grupo\s*|g)?(\d+)\s*", str(valor or ""), re.I)
    if m and int(m.group(1)) in validos:
        return int(m.group(1))
    return None


# ------------------------------------------------------------------ 1. clasificar y agrupar
SYS_CLASIFICAR = """Eres editor de un agregador de noticias de la región Puno (Perú).
Recibes NOTICIAS NUEVAS de varios medios y la lista de GRUPOS existentes (hechos ya registrados:
"grupos_recientes" y "grupos_creados_en_este_ciclo").

Para cada noticia nueva decide:
1. "grupo": si trata EXACTAMENTE el mismo hecho concreto que un grupo existente, pon su número
   (solo números que aparezcan en esas listas).
   Si trata el mismo hecho que otra noticia nueva sin grupo, dales la MISMA letra ("A", "B"…).
   Si es un hecho distinto, pon una letra propia. Mismo hecho = mismo acontecimiento
   (mismo evento, misma obra, mismo accidente, misma declaración), no solo el mismo tema
   ni la misma institución. Titulares distintos pueden ser el mismo hecho; una novedad del
   mismo caso (p. ej. la captura del autor de un robo ya informado) también lo es.
2. "ambito" (uno):
   DISTRITAL = ocurre en el distrito de Puno (la ciudad de Puno);
   LOCAL = provincia de Puno fuera de la ciudad (Acora, Chucuito, Platería, Paucarcolla, Atuncolla, Capachica, Coata, Huata, Mañazo, Vilque, Tiquillaca, Pichacani, San Antonio, Amantaní);
   REGIONAL = otras provincias de la región Puno (Juliaca/San Román, Azángaro, Ilave/El Collao, Juli, Ayaviri/Melgar, Huancané, Lampa, Carabaya, Sandia, Yunguyo, Moho, Putina) o la región en su conjunto (Gobierno Regional);
   NACIONAL = Perú fuera de Puno o de alcance nacional;
   INTERNACIONAL = fuera del Perú.
   Una institución de la ciudad de Puno (p. ej. la UNA Puno) cuenta como DISTRITAL salvo que el hecho ocurra en otro lugar.
   El ámbito lo da el lugar del hecho, no la sede del medio que lo difunde.
3. "tema" (uno, exactamente de la lista dada).
4. "resumen": una frase de máximo 25 palabras con el hecho (quién, qué, dónde), sin nombrar al medio.
5. "es_noticia": false si NO es información periodística: avisos de venta o alquiler,
   publicidad, sorteos, autopromoción del medio (su programación, transmisiones en vivo sin
   contenido descrito, portadas de su edición), publicaciones sin contenido informativo
   (solo un enlace o un emoji), saludos genéricos. Condolencias, comunicados y convocatorias
   institucionales SÍ cuentan.

Incluye TODAS las noticias nuevas, cada una con su "id".
Responde SOLO JSON: {"noticias":[{"id":"n1","grupo":12 | "A","ambito":"...","tema":"...","resumen":"...","es_noticia":true}]}"""


def _ficha_grupo(g) -> dict:
    return {"grupo": g["id"], "resumen": (g["resumen"] or g["titular"] or "")[:220], "ambito": g["ambito"]}


def clasificar_y_agrupar(cfg: dict, emit=lambda *a: None, cancelar=None) -> int:
    nuevos = storage.posts_sin_grupo()
    if not nuevos:
        return 0
    ambitos, temas = cfg["ambitos"], cfg["temas"]
    asignados = 0
    # foto fija de los grupos recientes para todo el ciclo (orden estable -> bloque cacheable)
    recientes = sorted(storage.grupos_recientes(cfg["ventana_cruce_horas"], MAX_GRUPOS_RECIENTES),
                       key=lambda g: g["id"])
    fijo = json.dumps({"temas_permitidos": temas, "grupos_recientes": [_ficha_grupo(g) for g in recientes]},
                      ensure_ascii=False)
    cachear = len(nuevos) > LOTE  # con un solo lote, escribir en caché costaría más
    validos = {g["id"] for g in recientes}
    creados: dict[int, dict] = {}  # grupos creados en este ciclo (para los lotes siguientes)
    for i in range(0, len(nuevos), LOTE):
        if cancelar and cancelar():
            break
        lote = nuevos[i:i + LOTE]
        emit("status", f"Claude: cruzando {len(lote)} noticias contra {len(validos)} grupos…")
        mapa = {f"n{j + 1}": p for j, p in enumerate(lote)}
        variable = json.dumps({
            "grupos_creados_en_este_ciclo": list(creados.values()),
            "noticias_nuevas": [{"id": k, "medio": nombre_medio(p["pagina"], cfg),
                                 "fecha": fecha_legible(p["publicado"] or p["detectado"]),
                                 "texto": (p["texto"] or "")[:1400]} for k, p in mapa.items()],
        }, ensure_ascii=False)
        bloque_fijo = {"type": "text", "text": fijo}
        if cachear:
            bloque_fijo["cache_control"] = {"type": "ephemeral"}
        user = [bloque_fijo, {"type": "text", "text": variable}]
        try:
            res = claude_json(cfg, SYS_CLASIFICAR, user, f"clasificar[{len(lote)}]", 3000,
                              esfuerzo="low", cancelar=cancelar)
        except Exception as e:  # noqa: BLE001
            if cancelar and cancelar():
                break
            emit("error", f"Clasificación: {e}")
            if _FATAL.search(str(e)):
                break
            continue  # este lote queda sin grupo y se reintenta en el próximo ciclo

        letras: dict[str, int] = {}
        items = res.get("noticias") if isinstance(res, dict) else None
        vistos = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            k = str(item.get("id"))
            p = mapa.get(k)
            if not p or k in vistos:
                continue
            vistos.add(k)
            ambito = item.get("ambito") if item.get("ambito") in ambitos else "REGIONAL"
            tema = item.get("tema") if item.get("tema") in temas else temas[-1]
            resumen = str(item.get("resumen") or "")[:300] or (p["texto"] or "")[:200]
            if item.get("es_noticia") is False or str(item.get("es_noticia")).lower() == "false":
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
            gid = _gid(g, validos)
            if gid is None:
                letra = str(g).strip().upper() if g is not None and str(g).strip() else f"_{k}"
                if letra not in letras:
                    letras[letra] = storage.crear_grupo(ambito, tema, resumen)
                    creados[letras[letra]] = {"grupo": letras[letra], "resumen": resumen[:220], "ambito": ambito}
                    validos.add(letras[letra])
                gid = letras[letra]
            storage.asignar_post(p["id"], gid, ambito, tema, resumen)
            asignados += 1
        if len(vistos) < len(mapa):
            api_log.warning("clasificar: %d de %d noticias sin respuesta; se reintentan en el próximo ciclo",
                            len(mapa) - len(vistos), len(mapa))
    return asignados


# ------------------------------------------------------------------ 2. nota combinada
SYS_COMBINAR = ESTILO + """

Recibes varias publicaciones de distintas fuentes sobre el MISMO hecho. Escribe UNA sola nota
propia, más completa que cada fuente por separado, juntando quién, qué, cuándo, dónde, cifras
y declaraciones de todas ellas. Cada fuente trae su fecha de publicación: úsala para fechar
el hecho (ver FECHAS Y LUGARES) y, si las fuentes son de días distintos, cuenta primero lo
más reciente.

- ANTETITULO, TITULAR, BAJADA, LUGAR, PIE_FOTO y CUERPO NO nombran a ningún medio (ver la
  regla PROHIBIDO).
- Si las fuentes dan datos distintos (cifras, nombres, fechas, lugares), NO elijas uno ni
  digas quién dijo qué: en el CUERPO preséntalo de forma neutral ("las cifras preliminares
  varían entre 12 y 15 heridos", "la hora exacta aún no está confirmada") y registra el
  detalle por medio en DISCREPANCIAS.
- DATOS, DISCREPANCIAS y FUENTES son fichas internas de verificación para el editor, no se
  publican: ahí SÍ se indica qué medio aporta cada dato.

Responde SOLO JSON, sin texto adicional ni bloques de código:
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
    try:
        ia = json.loads(post["ia_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(ia, dict) or not (ia.get("TITULAR") and ia.get("CUERPO")):
        return None
    medio = nombre_medio(post["pagina"], cfg)
    datos = ia.get("DATOS") or []
    nota = {
        "TITULAR": ia.get("TITULAR", ""), "BAJADA": ia.get("BAJADA", ""), "CUERPO": ia.get("CUERPO", ""),
        "ANTETITULO": ia.get("ANTETITULO", ""), "LUGAR": ia.get("LUGAR", ""), "PIE_FOTO": ia.get("PIE_FOTO", ""),
        "DATOS": [d if isinstance(d, dict) else {"dato": d, "fuentes": [medio]}
                  for d in (datos if isinstance(datos, list) else [datos])],
        "DISCREPANCIAS": [], "FUENTES": [{"medio": medio, "aporte": "fuente única"}],
        "_origen": f"fase1:{ia.get('_proveedor', '')}",
    }
    if ia.get("_menciona_medio"):
        nota["_menciona_medio"] = ia["_menciona_medio"]
    return nota


def _mejor_post(posts):
    """La publicación con más texto (y con nota de Fase 1) de un grupo de un solo medio."""
    con_nota = [p for p in posts if p["ia_json"]]
    return max(con_nota or posts, key=lambda p: len(p["texto"] or ""))


def _fuentes_combinada(posts, cfg, cancelar=None) -> list[dict]:
    """Textos de las fuentes, recortados para no pasarse del presupuesto de contexto."""
    elegidos = sorted(posts, key=lambda p: len(p["texto"] or ""), reverse=True)[:MAX_FUENTES_COMBINADA]
    elegidos.sort(key=lambda p: p["publicado"] or p["detectado"] or "")
    por_fuente = max(1200, min(3500, PRESUPUESTO_FUENTES // max(1, len(elegidos)) // 2))
    fuentes = []
    for p in elegidos:
        if cancelar and cancelar():
            break
        f = {"medio": nombre_medio(p["pagina"], cfg), "fecha": fecha_legible(p["publicado"] or p["detectado"]),
             "texto": (p["texto"] or "")[:por_fuente]}
        ext = fetch_article(p["enlace_ext"], por_fuente)
        if ext:
            f["nota_enlazada"] = ext
        fuentes.append(f)
    return fuentes


def generar_notas(cfg: dict, emit=lambda *a: None, cancelar=None) -> int:
    hechas = 0
    for g in storage.grupos_con_nota_pendiente():
        if cancelar and cancelar():
            break
        try:
            if _generar_nota_grupo(g, cfg, emit, cancelar):
                hechas += 1
        except Exception as e:  # noqa: BLE001  (el grupo queda pendiente para el próximo ciclo)
            if cancelar and cancelar():
                break
            api_log.exception("nota grupo %s", g["id"])
            emit("error", f"Nota grupo {g['id']}: {e}")
            if _FATAL.search(str(e)):
                break
    return hechas


def _generar_nota_grupo(g, cfg, emit, cancelar) -> bool:
    posts = storage.posts_de_grupo(g["id"])
    if not posts:
        return False
    medios = list(dict.fromkeys(nombre_medio(p["pagina"], cfg) for p in posts))
    nota = None
    if len(medios) == 1:  # una fuente, o varias publicaciones del mismo medio
        nota = _nota_desde_fase1(_mejor_post(posts), cfg)
    if nota is None:
        emit("status", f"Claude: nota combinada del grupo {g['id']} ({len(posts)} fuentes)…")
        fuentes = _fuentes_combinada(posts, cfg, cancelar)
        if cancelar and cancelar():
            return False
        user = json.dumps({"temas_permitidos": cfg["temas"], "fuentes": fuentes}, ensure_ascii=False)
        tarea = f"combinar[g{g['id']},{len(posts)}f]"
        todos = medios + [p["pagina"] for p in posts]

        def pedir(u, t):
            n = normalizar_nota(claude_json(cfg, SYS_COMBINAR, u, t, 6000, esfuerzo="medium", cancelar=cancelar))
            if not n["TITULAR"] or not n["CUERPO"]:
                n = normalizar_nota(claude_json(cfg, SYS_COMBINAR, u, t + "+vacia", 6000, esfuerzo="medium",
                                                cancelar=cancelar))
                if not n["TITULAR"] or not n["CUERPO"]:
                    raise ErrorIA("Claude devolvió una nota sin TITULAR o sin CUERPO")
            return n

        nota = pedir(user, tarea)
        culpable = menciona_medio(nota, todos)
        if culpable:  # un solo reintento, con recordatorio
            api_log.info("%s nombraba al medio %s: se reescribe", tarea, culpable)
            nota = pedir(user + RECORDATORIO.format(medio=culpable), tarea + "+reintento")
            culpable = menciona_medio(nota, todos)
            if culpable:
                api_log.warning("%s sigue nombrando al medio %s tras el reintento", tarea, culpable)
                nota["_menciona_medio"] = culpable  # para que el editor lo revise
        nota["_origen"] = "claude:combinada"
    ambito = nota.get("ambito") if nota.get("ambito") in cfg["ambitos"] else g["ambito"]
    tema = nota.get("tema") if nota.get("tema") in cfg["temas"] else g["tema"]
    if g["nota_json"]:
        try:
            previa = json.loads(g["nota_json"])
        except (TypeError, ValueError):
            previa = {}
        for k in ("_IMAGEN", "_SIN_FOTO"):
            if isinstance(previa, dict) and previa.get(k):
                nota[k] = previa[k]
    nota["LINKS"] = [{"medio": nombre_medio(p["pagina"], cfg), "fecha": p["publicado"] or p["detectado"],
                      "facebook": p["permalink"], "externo": p["enlace_ext"]} for p in posts]
    fechas = [f for f in (p["publicado"] or p["detectado"] for p in posts) if f]
    storage.actualizar_grupo(
        g["id"], nota_json=nota, titular=nota.get("TITULAR") or g["titular"], ambito=ambito, tema=tema,
        n_fuentes=len(posts), medios=", ".join(medios), fecha_hecho=max(fechas) if fechas else g["fecha_hecho"],
        nota_pendiente=0, actualizado=storage.now_iso())
    emit("grupo", g["id"])
    return True


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
            # cada paso por separado: si falla la clasificación, igual se redactan los grupos pendientes
            try:
                out["asignados"] = clasificar_y_agrupar(cfg, emit, cancelar)
            except Exception as e:  # noqa: BLE001
                api_log.exception("clasificar_y_agrupar")
                emit("error", f"Clasificación: {e}")
            if not (cancelar and cancelar()):
                try:
                    out["notas"] = generar_notas(cfg, emit, cancelar)
                except Exception as e:  # noqa: BLE001
                    api_log.exception("generar_notas")
                    emit("error", f"Notas: {e}")
            if not (cancelar and cancelar()):
                # jefe de redacción: une las notas del mismo acontecimiento y ordena por impacto
                try:
                    from .editorial import paso_editorial
                    out["editorial"] = paso_editorial(cfg, emit, cancelar)
                except Exception as e:  # noqa: BLE001
                    api_log.exception("paso_editorial")
                    emit("error", f"Revisión editorial: {e}")
        else:
            emit("error", "Fase 2 omitida: falta la API key de Claude (pestaña Configuración).")
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
