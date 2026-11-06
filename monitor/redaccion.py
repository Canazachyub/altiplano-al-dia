"""Redacción a medida: cada nota se reescribe con el largo de su hueco en la maqueta.

Con la plantilla del día se calcula qué nota va en qué hueco (principal, secundaria, abajo,
breve). Si el cuerpo de una nota está fuera del largo de su hueco, Claude la reescribe al largo
justo con el estilo del diario: lead con la ciudad, párrafos de 3 a 5 líneas, intertítulos en
mayúsculas en las notas largas y un titular que entre en el ancho del hueco. Para alargar solo
usa lo que dicen las publicaciones originales: si el material no alcanza, la nota queda corta
(la maqueta lo compensa con la foto) y nunca se inventa nada.
"""
import json
import logging
from datetime import datetime

from . import storage
from .ai import ESTILO, RECORDATORIO, ErrorIA, menciona_medio, normalizar_nota
from .analisis import claude_json
from .config import nombre_medio

log = logging.getLogger("monitor")

# caracteres del titular que entran (2 líneas al tamaño de cada hueco)
TITULAR = {"principal": (40, 62), "secundaria": (35, 55), "abajo": (35, 55), "breve": (28, 48)}
QUE_ES = {"principal": "la nota que abre la página (la más importante)",
          "secundaria": "la segunda nota de la página, en la columna lateral",
          "abajo": "una nota mediana al pie de la página",
          "breve": "un breve en recuadro: solo lo esencial, 1 o 2 párrafos, sin intertítulos"}

SYS_MEDIDA = ESTILO + """

Recibes una nota YA REDACTADA y el material original del que salió (las publicaciones de los
medios). Reescríbela para que ocupe EXACTAMENTE su espacio en la página:
- El CUERPO debe tener entre {min} y {max} caracteres (sin contar los intertítulos). Es {que}.
- Para ALARGAR usa SOLO datos que estén en la nota o en el material original (antecedentes,
  cifras, declaraciones textuales, contexto que la fuente dé). Si el material no alcanza para el
  mínimo, quédate corto: NUNCA inventes ni rellenes con frases vacías.
- Para ACORTAR quita lo secundario y conserva el lead completo.
- Estilo de diario impreso: lead de 1 o 2 oraciones con qué, quién, dónde y cuándo; párrafos de
  2 a 4 oraciones; si el cuerpo pasa de 1200 caracteres, 1 o 2 intertítulos de 1 a 3 palabras
  en mayúsculas en su propio párrafo con "## " (p. ej. "## LO UBICARON", "## LAS CIFRAS").
- TITULAR de {tmin} a {tmax} caracteres (tiene que entrar en dos líneas), fuerte y concreto.
- ANTETITULO en mayúsculas de 4 a 10 palabras que dé contexto y no repita el titular.
- No cambies los hechos ni el sentido; LUGAR igual que antes salvo que esté vacío y la fuente lo diga.

Responde SOLO JSON, sin texto adicional ni bloques de código:
{{"ANTETITULO":"…","TITULAR":"…","BAJADA":"…","LUGAR":"…","PIE_FOTO":"…",
 "CUERPO":"párrafos separados por \\n\\n","DATOS":["…"]}}"""


def largo(nota) -> int:
    return sum(len(p) for p in (nota.get("CUERPO") or "").split("\n\n") if p.strip() and not p.startswith("## "))


def plan(cfg, ahora=None):
    """{gid: (rol, sección de la página, página)} según la plantilla del día."""
    from . import consolidado as C, maqueta
    ahora = ahora or datetime.now()
    arts = [C._articulo(g, cfg) for g in storage.grupos_para_pdf()]
    paginas, _ = maqueta.armar(arts, maqueta.plantilla_del_dia(ahora), C.leer_fijas())
    return {h.art["gid"]: (h.rol, p["seccion"], p["n"]) for p in paginas for h in p["huecos"] if h.art}


def necesita(nota, rol) -> bool:
    from .maqueta import LARGO
    mn, mx = LARGO[rol]
    med = nota.get("_MEDIDA") or {}
    if nota.get("_EDITADO") and nota["_EDITADO"] > (med.get("fecha") or ""):
        return False                     # corregida a mano: no se toca
    if med.get("rol") == rol and med.get("hecho"):
        return False                     # ya se ajustó para este hueco (aunque quedara corta)
    n = largo(nota)
    return n < mn * 0.85 or n > mx * 1.10


def _material(gid, cfg):
    posts = storage.posts_de_grupo(gid)
    medios, fuentes = [], []
    for p in posts:
        medios += [nombre_medio(p["pagina"], cfg), p["pagina"]]
        txt = (p["texto"] or "").strip()
        try:
            ia = json.loads(p["ia_json"] or "{}")
        except (TypeError, ValueError):
            ia = {}
        fuentes.append({"publicacion": txt[:3500], "nota_de_esa_publicacion": (ia.get("CUERPO") or "")[:3500]})
    return fuentes, medios


def ajustar(cfg, gid, rol, cancelar=None) -> bool:
    """Reescribe la nota `gid` al largo de su hueco. True si cambió."""
    from .maqueta import LARGO
    g = storage.get_grupo(gid)
    if not g or not g["nota_json"]:
        return False
    nota = json.loads(g["nota_json"])
    mn, mx = LARGO[rol]
    tmn, tmx = TITULAR[rol]
    fuentes, medios = _material(gid, cfg)
    system = SYS_MEDIDA.format(min=mn, max=mx, que=QUE_ES[rol], tmin=tmn, tmax=tmx)
    user = json.dumps({"nota": {k: nota.get(k) for k in ("ANTETITULO", "TITULAR", "BAJADA", "LUGAR", "PIE_FOTO",
                                                        "CUERPO", "DATOS")},
                       "largo_actual": largo(nota), "material_original": fuentes}, ensure_ascii=False)

    def pedir(u, t):
        n = normalizar_nota(claude_json(cfg, system, u, t, 4000, esfuerzo="low", cancelar=cancelar))
        if not n["TITULAR"] or not n["CUERPO"]:
            raise ErrorIA("Claude devolvió una nota sin TITULAR o sin CUERPO")
        return n

    nueva = pedir(user, f"medida[{gid}:{rol}]")
    culpable = menciona_medio(nueva, medios)
    if culpable:
        nueva = pedir(user + RECORDATORIO.format(medio=culpable), f"medida[{gid}:{rol}]+reintento")
        if menciona_medio(nueva, medios):
            log.warning("Medida #%s: la nota sigue nombrando a %s; se deja la anterior", gid, culpable)
            return False
    if cancelar and cancelar():
        return False
    # se conservan los campos internos (foto elegida, impacto, sección, fusión, enlaces…)
    final = dict(nota)
    for k in ("ANTETITULO", "TITULAR", "BAJADA", "LUGAR", "PIE_FOTO", "CUERPO"):
        if nueva.get(k):
            final[k] = nueva[k]
    if nueva.get("DATOS"):
        final["DATOS"] = nueva["DATOS"]
    final.pop("PORTADA_TITULAR", None)          # los textos de portada se rehacen con el titular nuevo
    final["_MEDIDA"] = {"rol": rol, "hecho": True, "antes": largo(nota), "despues": largo(final),
                        "objetivo": [mn, mx], "fecha": storage.now_iso(),
                        "previa": {k: nota.get(k) for k in ("ANTETITULO", "TITULAR", "BAJADA", "CUERPO", "DATOS")}}
    storage.actualizar_grupo(gid, nota_json=json.dumps(final, ensure_ascii=False))
    return True


def paso_medida(cfg, emit=lambda *a: None, cancelar=None) -> dict:
    """Ajusta al largo de su hueco todas las notas de la próxima edición que lo necesiten."""
    out = {"ajustadas": 0, "revisadas": 0, "errores": 0}
    p = plan(cfg)
    pend = []
    for gid, (rol, _sec, pag) in p.items():
        g = storage.get_grupo(gid)
        if not g or not g["nota_json"]:
            continue
        out["revisadas"] += 1
        if necesita(json.loads(g["nota_json"]), rol):
            pend.append((pag, gid, rol))
    for i, (pag, gid, rol) in enumerate(sorted(pend), 1):
        if cancelar and cancelar():
            break
        emit("status", f"Claude: ajustando notas al largo de su espacio ({i}/{len(pend)}, pág. {pag})…")
        try:
            if ajustar(cfg, gid, rol, cancelar):
                out["ajustadas"] += 1
        except Exception as e:  # noqa: BLE001
            log.exception("Medida de la nota %s", gid)
            out["errores"] += 1
            emit("error", f"Ajuste de largo (#{gid}): {e}")
    if out["ajustadas"]:
        emit("status", f"Notas ajustadas a su espacio en la página: {out['ajustadas']}.")
    return out
