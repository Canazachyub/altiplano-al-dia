"""Paso editorial (Claude como jefe de redacción) antes de maquetar el periódico.

1. UNIR: las notas que cuentan el mismo acontecimiento o la misma historia en curso (p. ej. la
   muerte de un obispo: homenajes, misa, traslado y sucesión) se reescriben como UNA sola nota
   completa, sin repetir datos. Las notas absorbidas quedan fuera del periódico (estado
   'descartado') y se pueden separar otra vez con deshacer_fusion().
2. IMPACTO: cada noticia recibe una nota de 1 a 10 (vidas, crimen, corrupción, ilegalidad,
   tragedias, cuánta gente afecta, cercanía a Puno, cuántos medios la cubren). La portada y el
   orden de las páginas usan ese número.

Se guarda en la nota (nota_json): IMPACTO, _impacto_motivo; en la principal de una fusión
_fusion (ids, posts y nota anterior para deshacer); en las absorbidas _fusionado_en.
"""
import hashlib
import json
import logging

from . import storage
from .ai import ESTILO, RECORDATORIO, ErrorIA, menciona_medio, normalizar_nota
from .analisis import claude_json
from .config import DB_PATH, nombre_medio

log = logging.getLogger("monitor")
ESTADO_PATH = DB_PATH.parent / "editorial.json"
MAX_UNIR = 6          # notas como máximo en una fusión

SYS_EDITOR = """Eres el jefe de redacción de "Altiplano al Día", diario de Puno (Perú). Recibes las
noticias candidatas del próximo periódico (id, ámbito, tema, titular, bajada, inicio del texto,
cuántos medios la cubrieron y fecha). Tu trabajo:

1. "fusiones": grupos de ids que cuentan el MISMO acontecimiento o la MISMA historia en curso y
   que el lector debe recibir como UNA sola nota completa. Ejemplos de fusión: la muerte de un
   obispo y sus homenajes, su misa de cuerpo presente, el traslado de una imagen religiosa para
   su misa y la elección de su sucesor; la visita del papa y su logística, pronósticos y cifras
   de asistentes; un crimen y la captura de sus autores; un motín y sus consecuencias en ese
   mismo penal.
   OJO, el error más grave es publicar DOS VECES lo mismo. Fusiona también:
   - "mismo_hecho": el MISMO suceso contado en varios reportes (del mismo medio o de varios),
     aunque los detalles no coincidan del todo: primero "choque entre dos vehículos" y luego
     "tres vehículos involucrados", un sector nombrado distinto (Ichu / Cusipata), cifras que
     se actualizan. Mismo tipo de hecho + mismo día + misma zona o vía = mismo hecho.
   - "conjunto": hechos DISTINTOS pero del mismo tipo, el mismo día y el mismo lugar, vía o
     institución (dos accidentes en la carretera Puno–Ilave esta mañana; dos denuncias contra
     la misma empresa de agua). Publicados por separado parecen repetidos: van en UNA nota.
   NO fusiones notas que solo comparten tema general: un asesinato en Juliaca y otro en Lima,
   accidentes en provincias o días distintos, elecciones de distritos distintos.
   Revisa con especial atención los "pares_sospechosos" que te indico (mismo antetítulo o
   titulares parecidos): fusiónalos salvo que sean claramente de lugares o días distintos.
   Cada id puede estar en una sola fusión, de 2 a 6 ids.
   "principal": el id cuyo hecho es el más importante o el más reciente del grupo.
   "tipo": "mismo_hecho" o "conjunto".
2. "impacto" para CADA id (también los fusionados), de 1 a 10, pensando en un lector de Puno:
   9-10: muertes violentas, asesinatos, feminicidios, tragedias con víctimas, corrupción o
         malversación de autoridades, crimen organizado, hechos graves en Puno o la región.
   7-8:  delitos e ilegalidades (robos, extorsión, minería ilegal, contrabando, abusos),
         capturas y condenas, accidentes con heridos, conflictos y protestas, decisiones
         que afectan a mucha gente (obras grandes, presupuesto, salud, elecciones).
   5-6:  gestión pública y política con consecuencias, servicios, educación, economía local,
         sucesos nacionales relevantes.
   3-4:  actividades institucionales, cultura, religión y deporte sin conflicto, notas de
         servicio.
   1-2:  farándula, espectáculos, curiosidades, notas internacionales sin relación con el Perú.
   Suma 1 si la cubrieron 3 o más medios (es tendencia) y resta 1 a lo internacional o
   nacional sin efecto en Puno. Nunca más de 10 ni menos de 1.
   "motivo": de 3 a 8 palabras.

Responde SOLO JSON:
{"fusiones":[{"ids":[12,15,19],"principal":15,"tipo":"mismo_hecho","evento":"muerte del obispo de Puno"}],
 "impacto":[{"id":12,"impacto":7,"motivo":"…"}]}"""

SYS_UNIR = ESTILO + """

Recibes varias notas YA REDACTADAS por nuestra redacción que tratan el MISMO acontecimiento o la
misma historia en curso. Escribe UNA sola nota completa y bien organizada que las reemplace:
- No repitas datos: cada hecho, cifra o cita aparece una sola vez.
- El lead cuenta lo más importante y más reciente; después los demás aspectos en orden de
  importancia, uno por párrafo o bloque; al final, qué sigue.
- Si la nota queda de 5 párrafos o más, ordénala en bloques con 1 a 3 intertítulos ("## …").
- TITULAR que resuma la historia completa, no solo una de sus partes: concreto, con el dato
  más fuerte, que invite a leer.
- Si "tipo" es "mismo_hecho" y los reportes difieren en detalles (cuántos vehículos, el nombre
  del sector, cifras), usa la versión más reciente y completa; si la diferencia importa, dilo
  ("en un primer reporte se habló de…"). Es UN solo hecho: no lo cuentes dos veces.
- Si "tipo" es "conjunto" son hechos distintos: el titular los agrupa ("Dos accidentes en la
  vía Puno–Ilave dejan…") y el cuerpo cuenta cada uno por separado, sin mezclar sus datos.
- No agregues nada que no esté en las notas recibidas.

Responde SOLO JSON, sin texto adicional ni bloques de código:
{"ANTETITULO":"…","TITULAR":"…","BAJADA":"…","LUGAR":"…","PIE_FOTO":"…",
 "CUERPO":"párrafos separados por \\n\\n","DATOS":["…"],"ambito":"…","tema":"…"}"""


# ------------------------------------------------------------------ utilidades
def _nota(g) -> dict:
    try:
        n = json.loads(g["nota_json"] or "{}")
        return n if isinstance(n, dict) else {}
    except (TypeError, ValueError):
        return {}


def impacto(nota_o_art) -> int:
    """Impacto 1-10 de una nota (o de un artículo de la maqueta); 0 si aún no se evaluó."""
    n = nota_o_art.get("nota", nota_o_art) if isinstance(nota_o_art, dict) else {}
    try:
        return max(0, min(10, int(n.get("IMPACTO") or 0)))
    except (TypeError, ValueError):
        return 0


def _firma(grupos) -> str:
    base = "|".join(f"{g['id']}:{g['actualizado'] or ''}" for g in sorted(grupos, key=lambda g: g["id"]))
    return hashlib.sha1(base.encode()).hexdigest()


def _leer_estado() -> dict:
    try:
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _guardar_estado(d: dict) -> None:
    ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    ESTADO_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _ficha(g) -> dict:
    n = _nota(g)
    return {"id": g["id"], "ambito": g["ambito"], "tema": g["tema"], "medios": storage.n_medios(g),
            "fecha": (g["fecha_hecho"] or "")[:16], "titular": n.get("TITULAR") or g["titular"],
            "antetitulo": n.get("ANTETITULO") or "", "lugar": n.get("LUGAR") or "",
            "bajada": (n.get("BAJADA") or "")[:240], "texto": (n.get("CUERPO") or "")[:420]}


_VACIAS = set("de la el los las del en y a un una por con para que se al su sus lo es tras ante sobre "
              "este esta entre sin más mas como ser".split())


def _palabras(t):
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return {w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in _VACIAS}


def pares_sospechosos(grupos) -> list:
    """Pares de noticias que probablemente cuentan lo mismo (mismo antetítulo, o titular + inicio muy
    parecidos el mismo día). Se le señalan a Claude para que no se le escapen."""
    fichas = []
    for g in grupos:
        n = _nota(g)
        fichas.append((g["id"], (n.get("ANTETITULO") or "").strip().lower(), (g["fecha_hecho"] or "")[:10],
                       _palabras(f"{n.get('TITULAR', '')} {n.get('BAJADA', '')} {(n.get('CUERPO') or '')[:300]}")))
    pares = []
    for i, (a, ante_a, dia_a, pa) in enumerate(fichas):
        for b, ante_b, dia_b, pb in fichas[i + 1:]:
            parecido = len(pa & pb) / max(1, len(pa | pb))
            if (ante_a and ante_a == ante_b) or (dia_a == dia_b and parecido >= 0.22):
                pares.append([a, b])
    return pares


# ------------------------------------------------------------------ 1. evaluar
def evaluar(cfg, grupos, cancelar=None) -> dict:
    """Una llamada a Claude: fusiones propuestas e impacto de cada noticia."""
    validos = {g["id"] for g in grupos}
    user = json.dumps({"noticias": [_ficha(g) for g in grupos], "pares_sospechosos": pares_sospechosos(grupos)},
                      ensure_ascii=False)
    r = claude_json(cfg, SYS_EDITOR, user, f"editorial[{len(grupos)}]", 5000, esfuerzo="medium",
                    cancelar=cancelar)
    imp = {}
    for x in r.get("impacto") or []:
        try:
            gid, val = int(x.get("id")), int(x.get("impacto"))
        except (TypeError, ValueError, AttributeError):
            continue
        if gid in validos:
            imp[gid] = (max(1, min(10, val)), str(x.get("motivo") or "")[:80])
    fus, usados = [], set()
    for f in r.get("fusiones") or []:
        try:
            ids = [int(i) for i in f.get("ids") or []]
        except (TypeError, ValueError, AttributeError):
            continue
        ids = [i for i in dict.fromkeys(ids) if i in validos and i not in usados][:MAX_UNIR]
        if len(ids) < 2:
            continue
        try:
            principal = int(f.get("principal"))
        except (TypeError, ValueError):
            principal = None
        if principal not in ids:
            principal = max(ids, key=lambda i: imp.get(i, (0,))[0])
        usados.update(ids)
        tipo = "conjunto" if f.get("tipo") == "conjunto" else "mismo_hecho"
        fus.append({"ids": ids, "principal": principal, "evento": str(f.get("evento") or "")[:120], "tipo": tipo})
    return {"impacto": imp, "fusiones": fus}


# ------------------------------------------------------------------ 2. unir
def unir(cfg, ids, principal, cancelar=None, tipo="mismo_hecho") -> bool:
    """Reescribe las notas `ids` como una sola en el grupo `principal`; las demás salen del periódico."""
    grupos = {g["id"]: g for g in (storage.get_grupo(i) for i in ids) if g is not None and g["nota_json"]}
    if principal not in grupos or len(grupos) < 2:
        return False
    orden = sorted(grupos.values(), key=lambda g: g["fecha_hecho"] or "")
    notas = []
    for g in orden:
        n = _nota(g)
        notas.append({"fecha": (g["fecha_hecho"] or "")[:16], "ANTETITULO": n.get("ANTETITULO", ""),
                      "TITULAR": n.get("TITULAR", ""), "BAJADA": n.get("BAJADA", ""), "LUGAR": n.get("LUGAR", ""),
                      "CUERPO": (n.get("CUERPO") or "")[:5000], "DATOS": n.get("DATOS") or []})
    medios_todos = []
    posts_por_grupo = {}
    for gid in grupos:
        ps = storage.posts_de_grupo(gid)
        posts_por_grupo[gid] = [p["id"] for p in ps]
        medios_todos += [nombre_medio(p["pagina"], cfg) for p in ps] + [p["pagina"] for p in ps]
    user = json.dumps({"tipo": tipo, "temas_permitidos": cfg["temas"], "notas": notas}, ensure_ascii=False)
    tarea = f"unir[{','.join(map(str, grupos))}]"

    def pedir(u, t):
        n = normalizar_nota(claude_json(cfg, SYS_UNIR, u, t, 7000, esfuerzo="medium", cancelar=cancelar))
        if not n["TITULAR"] or not n["CUERPO"]:
            raise ErrorIA("Claude devolvió una nota unida sin TITULAR o sin CUERPO")
        return n

    nueva = pedir(user, tarea)
    culpable = menciona_medio(nueva, medios_todos)
    if culpable:
        nueva = pedir(user + RECORDATORIO.format(medio=culpable), tarea + "+reintento")
        if menciona_medio(nueva, medios_todos):
            nueva["_menciona_medio"] = culpable
    if cancelar and cancelar():
        return False

    gp = grupos[principal]
    previa = _nota(gp)
    # foto: la elegida a mano en la principal o, si no tiene, la de cualquiera de las otras
    for k in ("_IMAGEN", "_SIN_FOTO"):
        if previa.get(k):
            nueva[k] = previa[k]
    if not nueva.get("_IMAGEN") and not nueva.get("_SIN_FOTO"):
        for g in orden:
            img = _nota(g).get("_IMAGEN")
            if img:
                nueva["_IMAGEN"] = img
                break
    links, datos_previos = [], []
    for g in orden:
        n = _nota(g)
        links += n.get("LINKS") or []
        datos_previos += [d for d in (n.get("DATOS") or []) if isinstance(d, dict)]
    nueva["LINKS"] = links
    if datos_previos and not any(isinstance(d, dict) for d in nueva.get("DATOS") or []):
        nueva["DATOS"] = [{"dato": d, "fuentes": []} if not isinstance(d, dict) else d
                          for d in nueva.get("DATOS") or []] or datos_previos
    nueva["IMPACTO"] = max(impacto(_nota(g)) for g in orden) or previa.get("IMPACTO")
    nueva["_impacto_motivo"] = previa.get("_impacto_motivo", "")
    nueva["_origen"] = "claude:unida"
    nueva["_fusion"] = {"ids": sorted(grupos), "posts": {str(k): v for k, v in posts_por_grupo.items()},
                        "nota_antes": previa, "fecha": storage.now_iso()}
    medios = sorted({m.strip() for g in orden for m in (g["medios"] or "").split(",") if m.strip()})
    ambito = nueva.get("ambito") if nueva.get("ambito") in cfg["ambitos"] else gp["ambito"]
    tema = nueva.get("tema") if nueva.get("tema") in cfg["temas"] else gp["tema"]
    otros = [gid for gid in grupos if gid != principal]
    with storage._conn() as c:
        for gid in otros:
            c.execute("UPDATE posts SET grupo_id=? WHERE grupo_id=?", (principal, gid))
            n = _nota(grupos[gid])
            n["_fusionado_en"] = principal
            c.execute("UPDATE grupos SET estado_pdf='descartado', en_periodico=0, nota_json=?, actualizado=? "
                      "WHERE id=?", (json.dumps(n, ensure_ascii=False), storage.now_iso(), gid))
        fechas = [g["fecha_hecho"] for g in orden if g["fecha_hecho"]]
        c.execute("UPDATE grupos SET nota_json=?, titular=?, ambito=?, tema=?, medios=?, n_fuentes=?, "
                  "fecha_hecho=?, actualizado=?, en_periodico=1 WHERE id=?",
                  (json.dumps(nueva, ensure_ascii=False), nueva["TITULAR"], ambito, tema, ", ".join(medios),
                   sum(len(v) for v in posts_por_grupo.values()), max(fechas) if fechas else gp["fecha_hecho"],
                   storage.now_iso(), principal))
    log.info("Paso editorial: notas %s unidas en #%s «%s»", sorted(grupos), principal, nueva["TITULAR"])
    return True


def deshacer_fusion(gid) -> bool:
    """Separa otra vez una nota unida: cada noticia vuelve con su texto y sus publicaciones."""
    g = storage.get_grupo(gid)
    if g is None:
        return False
    n = _nota(g)
    f = n.get("_fusion")
    if not f:
        return False
    with storage._conn() as c:
        for k, posts in f.get("posts", {}).items():
            otro = int(k)
            if otro == gid:
                continue
            for pid in posts:
                c.execute("UPDATE posts SET grupo_id=? WHERE id=?", (otro, pid))
            fila = c.execute("SELECT nota_json FROM grupos WHERE id=?", (otro,)).fetchone()
            if fila:
                no = json.loads(fila["nota_json"] or "{}")
                no.pop("_fusionado_en", None)
                c.execute("UPDATE grupos SET estado_pdf='nuevo', en_periodico=1, nota_json=?, actualizado=?, "
                          "n_fuentes=? WHERE id=?",
                          (json.dumps(no, ensure_ascii=False), storage.now_iso(), len(posts), otro))
        antes = f.get("nota_antes") or {}
        antes["_no_unir"] = sorted(int(k) for k in f.get("posts", {}))   # no volver a unirlas solas
        propios = f.get("posts", {}).get(str(gid), [])
        c.execute("UPDATE grupos SET nota_json=?, titular=?, n_fuentes=?, actualizado=? WHERE id=?",
                  (json.dumps(antes, ensure_ascii=False), antes.get("TITULAR") or g["titular"],
                   len(propios) or g["n_fuentes"], storage.now_iso(), gid))
    log.info("Paso editorial: se separó la nota unida #%s", gid)
    return True


# ------------------------------------------------------------------ 3. todo junto
def paso_editorial(cfg, emit=lambda *a: None, cancelar=None, forzar=False) -> dict:
    """Evalúa las noticias pendientes, une las repetidas y guarda el impacto. Solo llama a Claude
    si cambió algo desde la última vez (o con forzar=True)."""
    out = {"unidas": 0, "evaluadas": 0, "omitido": False}
    grupos = list(storage.grupos_para_pdf())
    if not grupos:
        return out
    estado = _leer_estado()
    firma = _firma(grupos)
    if not forzar and estado.get("firma") == firma:
        out["omitido"] = True
        return out
    emit("status", f"Claude: revisión editorial de {len(grupos)} noticias (unir repetidas, ordenar por impacto)…")
    r = evaluar(cfg, grupos, cancelar)
    if cancelar and cancelar():
        return out
    no_unir = set()
    for g in grupos:
        for i in _nota(g).get("_no_unir") or []:
            no_unir.add(frozenset((g["id"], i)))
    # impacto
    with storage._conn() as c:
        for g in grupos:
            if g["id"] not in r["impacto"]:
                continue
            val, motivo = r["impacto"][g["id"]]
            n = _nota(g)
            n["IMPACTO"], n["_impacto_motivo"] = val, motivo
            c.execute("UPDATE grupos SET nota_json=? WHERE id=?", (json.dumps(n, ensure_ascii=False), g["id"]))
            out["evaluadas"] += 1
    # fusiones
    for f in r["fusiones"]:
        if cancelar and cancelar():
            break
        ids = f["ids"]
        if any(frozenset((a, b)) in no_unir for a in ids for b in ids if a != b):
            continue                       # el editor las separó a mano: se respeta
        emit("status", f"Claude: uniendo {len(ids)} notas sobre «{f['evento'] or 'el mismo hecho'}»…")
        try:
            if unir(cfg, ids, f["principal"], cancelar, f.get("tipo", "mismo_hecho")):
                out["unidas"] += len(ids) - 1
                emit("grupo", f["principal"])
        except Exception as e:  # noqa: BLE001
            log.exception("unir %s", ids)
            emit("error", f"No se pudieron unir las notas {ids}: {e}")
    _guardar_estado({"firma": _firma(storage.grupos_para_pdf()), "fecha": storage.now_iso(),
                     "fusiones": r["fusiones"]})
    emit("status", f"Revisión editorial: {out['evaluadas']} noticias ordenadas por impacto, "
                   f"{out['unidas']} repetidas unidas.")
    return out
