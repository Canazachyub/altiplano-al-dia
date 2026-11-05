"""Resumen y ampliación de la noticia con DeepSeek o Claude (vía HTTP, sin SDK).

Aquí vive también la llamada HTTP compartida a Claude (`llamar_claude`), que usa la Fase 2:
reintentos con espera creciente ante 429/5xx/cortes de red, caché del prompt de sistema,
control de esfuerzo de razonamiento y mensajes de error claros.
"""
import json
import logging
import logging.handlers
import random
import re
import time
import unicodedata
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .config import API_LOG_PATH, get_api_key, nombre_medio

API_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
api_log = logging.getLogger("api")
if not api_log.handlers:
    _h = logging.handlers.RotatingFileHandler(API_LOG_PATH, maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8", delay=True)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    api_log.addHandler(_h)
    api_log.setLevel(logging.INFO)
    api_log.propagate = False

ESTILO = """Eres el redactor principal de "Altiplano al Día", un diario de noticias de Puno (Perú).
Tu trabajo NO es resumir lo que dijo otro medio: es escribir TU PROPIA nota, con voz propia,
usando como materia prima la información ya publicada.

PROHIBIDO (regla más importante):
- Nombrar o aludir al medio, radio, diario, canal o página de donde sale la información.
  Nada de "según Radio X", "informó Diario Y", "La República publicó", "en su página de
  Facebook", "fuentes periodísticas", "un medio local", "según medios locales", "la
  publicación", "el post", "el video", "la transmisión en vivo", "el enlace".
  Los hechos se cuentan en voz propia, como si nuestro equipo los hubiera reportado.
- Esto vale para TODOS los campos que se publican: ANTETITULO, TITULAR, BAJADA, LUGAR,
  PIE_FOTO y CUERPO (tampoco "Foto: Radio X" en el pie de foto).
- Sí se nombra a las personas, autoridades e instituciones que son PROTAGONISTAS del hecho
  o que dan declaraciones (p. ej. "la UNA Puno anunció…", "el alcalde declaró…"). Lo que no
  se nombra es al medio que difundió la noticia.

ESTRUCTURA (pirámide invertida, que enganche e informe):
- TITULAR: corto y concreto, de 6 a 12 palabras, con sujeto, verbo en activo y el dato más
  fuerte. Verbo en presente o pretérito ("Municipalidad de Puno inicia…", "Policía captura…").
  Sin signos de exclamación, sin preguntas, sin clickbait, sin mayúsculas sostenidas, sin
  punto final y sin "hoy", "ayer" ni "mañana" (el diario se lee después).
- BAJADA: 1 o 2 líneas que completan el titular con el segundo dato clave (dónde, cuánto,
  a quién afecta). No repite el titular.
- CUERPO, en párrafos separados por \\n\\n:
  1. Lead: un primer párrafo de 1 o 2 oraciones que responde qué, quién, dónde y cuándo,
     y que haga querer seguir leyendo.
  2. Desarrollo: los detalles en orden de importancia, cifras, declaraciones entre comillas
     (solo si están en la fuente, textuales y atribuidas a quien las dijo) y antecedentes
     que estén en la fuente.
  3. Cierre: qué sigue, a quién afecta o qué deben saber los lectores (plazos, lugares,
     requisitos), solo si la fuente lo permite. Sin moralejas ni frases de relleno al final.

ESTILO:
- Español estándar del Perú, el de un diario regional serio: claro, preciso y sobrio.
  Oraciones cortas o medianas, una idea por oración.
- Vocabulario cercano; nada de jerga burocrática ni de relleno ("cabe resaltar",
  "es importante mencionar", "en ese sentido", "cabe señalar", "por su parte").
- Párrafos de 2 a 4 oraciones. Tono informativo: no opines, no adjetives de más.
- Nombres de lugares con su grafía correcta (Juliaca, Azángaro, Ilave, Desaguadero,
  Yunguyo, Huancané, Lampa, Ayaviri, Sandia, Macusani). Siglas desarrolladas la primera vez
  si la fuente lo permite (p. ej. "la Dirección Regional de Salud (Diresa) Puno").
- Montos en soles como "S/ 1500"; cifras y unidades tal como vienen en la fuente.

FECHAS Y LUGARES:
- Se te da la fecha de publicación de la fuente. Úsala SOLO para traducir referencias
  relativas de la fuente ("hoy", "ayer", "este lunes", "mañana") a una fecha concreta
  ("el lunes 3 de noviembre"). Si la fuente no dice cuándo ocurrió, no supongas que
  ocurrió el día de la publicación: omite la fecha.
- No pongas el año salvo que sea distinto del año en curso o la fuente lo destaque.
- Horas en formato "10:30 a. m." / "4:00 p. m.".
- Si el lugar exacto no está en la fuente, no lo inventes ni lo deduzcas por el medio.

FORMATO DE DIARIO:
- ANTETITULO: frase corta (máx. 10 palabras) que va encima del titular y aporta contexto o
  un segundo dato (p. ej. "GESTIÓN DEL HOSPITAL PARA LA ATENCIÓN DE URGENCIAS"). No repite el titular.
- LUGAR: ciudad o distrito donde ocurre el hecho, una o dos palabras ("Juliaca", "Puno",
  "Ilave"). Vacío si la fuente no lo dice.
- PIE_FOTO: frase de 5 a 12 palabras que sirva como pie de la foto principal. Resume el hecho
  sin describir detalles visuales que no conoces (no has visto la foto).
- Si el CUERPO tiene 5 o más párrafos, puedes incluir 1 o 2 intertítulos de 1 a 3 palabras,
  cada uno en un párrafo propio que empiece con "## " (p. ej. "## LAS CIFRAS").

RIGOR:
- NO inventes datos, nombres, cargos, cifras, fechas, lugares ni citas que no estén en la fuente.
- Si algo es una denuncia o una versión de parte, preséntalo como tal ("denunció", "según
  la Fiscalía", "presunto"); no lo des por probado.
- Si la fuente es escueta, la nota es breve: es mejor corta y exacta que larga y con supuestos."""

SYSTEM = ESTILO + """

Recibes el texto de una publicación y, si existe, el texto de la nota enlazada.
Si la publicación no es una noticia (saludo, condolencia, aviso), redáctala igual de forma informativa.
Si es solo autopromoción del propio medio (su programación, una transmisión en vivo, su
aniversario, publicidad), redáctala igual pero marca "ES_NOTICIA": false.

Responde SOLO con un objeto JSON válido, sin texto adicional ni bloques de código, con estas claves:
{
  "ANTETITULO": "…",
  "TITULAR": "…",
  "BAJADA": "…",
  "LUGAR": "…",
  "PIE_FOTO": "…",
  "CUERPO": "párrafos separados por \\n\\n",
  "DATOS": ["dato o cifra importante", "..."],
  "ES_NOTICIA": true
}"""

# ------------------------------------------------------------------ detección del medio citado
# Campos que se publican: ahí no puede aparecer el medio de origen.
CAMPOS_PUBLICADOS = ("ANTETITULO", "TITULAR", "BAJADA", "LUGAR", "PIE_FOTO", "CUERPO")

# Palabras que no identifican a un medio por sí solas ("Diario Los Andes" -> "Los Andes").
_GENERICOS = r"(?:diario|radio|radioemisora|emisora|canal|tv|television|televisora|portal|agencia|" \
             r"periodico|semanario|noticias|noticiero|prensa|medio|multimedia|digital|oficial|peru|pe)"
# Palabras que delatan que la página es un medio (y no una institución).
_MEDIO_KW = re.compile(r"\b(?:diario|radio|radioemisora|emisora|canal|tv|television|televisora|portal|"
                       r"agencia|periodico|semanario|noticias|noticiero|prensa|fm|am)\b")
# Núcleos que no sirven para reconocer a un medio ("Puno Noticias" -> "puno" es la ciudad).
_NUCLEOS_VAGOS = {"puno", "juliaca", "peru", "altiplano", "sur", "region", "regional", "andina"}
# Páginas de instituciones (protagonistas legítimas del hecho): nombrarlas no es citar al medio.
_INSTITUCION_KW = re.compile(
    r"\b(?:una|universidad|unap|municipalidad|gobierno|ministerio|direccion|diresa|drep|ugel|"
    r"policia|pnp|hospital|essalud|prefectura|fiscalia|poder judicial|corte|colegio|instituto|"
    r"red de salud|sunat|contraloria|defensoria|jne|onpe|jurado|congreso|ejercito|marina|"
    r"senamhi|indeci|sutran|sunarp|reniec|obispado|prelatura|arzobispado|camara)\b")

# Frases de atribución a un medio (sobre texto ya sin tildes ni mayúsculas).
_ATRIB_ANTES = (r"segun(?: (?:informo|reporto|publico|dio a conocer|difundio|indico|consigno))?|"
                r"de acuerdo (?:con|a)|citado por|citada por|publicado por|publicada por|"
                r"difundido por|difundida por|reportado por|reportada por|informado por|"
                r"informo|reporto|publico|difundio|consigno|recogio|transmitio|"
                r"(?:declaro|dijo|manifesto|indico|senalo|preciso|explico|anuncio|confirmo|aseguro|"
                r"refirio|sostuvo|detallo) a|"
                r"en (?:declaraciones|entrevista|dialogo|conversacion|comunicacion) (?:a|con|para)|"
                r"fuente:?|en (?:los )?microfonos de|en el programa de|en la edicion de")
# Solo con el nombre completo ("a través de Radio Onda Azul", no "a través de los Andes").
_ATRIB_ANTES_FUERTE = r"a traves de|via|mediante"
_ATRIB_DESPUES = (r"informo|informa|reporto|reporta|publico|publica|difundio|difunde|"
                  r"dio a conocer|da a conocer|consigno|conocio|pudo conocer|accedio|tuvo acceso|"
                  r"recogio|transmitio|entrevisto|consulto|revelo|obtuvo")
# Palabras de enlace permitidas entre la frase de atribución y el nombre del medio.
_ENLACE = r"(?:(?:el|la|los|las|del|al|su|sus|un|una|diario|radio|emisora|canal|portal|medio|" \
          r"programa|noticiero|pagina|cuenta|web|televisora|periodico|semanario|agencia|" \
          r"informo|reporto|publico|difundio|dio|a|conocer)\s+){0,3}"
_GENERICAS = re.compile(
    r"(?:pagina|cuenta|fanpage|perfil|muro|transmision|live) (?:oficial )?(?:de|en) "
    r"(?:facebook|instagram|tiktok|youtube)|"
    r"redes sociales del (?:diario|medio|canal|programa|noticiero)|"
    r"la publicacion (?:original|de|del|en)|segun (?:la |esta |una )?publicacion|\bel post\b|"
    r"publicacion (?:de|en) (?:facebook|redes)|"
    r"medios (?:locales|regionales|periodisticos)|\bun medio (?:local|regional|periodistico)|"
    r"(?:segun|de acuerdo (?:con|a)|informaron|reportaron|difundieron) (?:diversos |varios |algunos |los )?"
    r"(?:medios|prensa|fuentes periodisticas)|fuentes periodisticas|prensa local|"
    r"(?:facebook|fb|instagram|tiktok|youtube)\.com|fb\.watch|youtu\.be")


def _plano(s: str) -> str:
    """Minúsculas, sin tildes y con espacios simples (para comparar sin falsos negativos)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s).strip()


def es_institucion(nombre: str) -> bool:
    """True si la página de origen parece una institución (protagonista), no un medio."""
    p = _plano(nombre)
    return bool(_INSTITUCION_KW.search(p)) and not _MEDIO_KW.search(p)


def _variantes(medio: str) -> dict[str, bool]:
    """Formas en que la IA podría escribir el medio -> {variante: es_nombre_completo}.
    Completo, el slug con separadores y el núcleo sin 'Diario/Radio/TV…'."""
    base = _plano(medio)
    out = {base: True, re.sub(r"[._-]+", " ", base).strip(): True}
    nucleo = re.sub(rf"\b{_GENERICOS}\b|\b\d+(?:[.,]\d)?\s*(?:fm|am)\b|\b(?:fm|am)\b", " ", base)
    nucleo = re.sub(r"\s+", " ", nucleo).strip(" .-")
    if nucleo and nucleo != base and nucleo not in _NUCLEOS_VAGOS:
        out.setdefault(nucleo, False)
    # al menos 3 letras que no sean solo artículos
    return {v: c for v, c in out.items() if len(re.sub(r"\b(?:el|la|los|las|de|del)\b|\W", "", v)) >= 3}


def menciona_medio(data: dict, medios: list[str]) -> str | None:
    """Devuelve el medio si la redacción se lo atribuye ("según X", "X informó", "Foto: X"),
    o una etiqueta genérica si alude a la fuente ("en su página de Facebook", "medios locales").

    Solo cuenta la mención en contexto de atribución: un medio cuyo nombre es palabra común
    ("Los Andes", "Sin Fronteras", "La República") no salta por aparecer como lugar o en
    "el presidente de la República informó". Revisa todos los campos que se publican.
    Las páginas de instituciones (p. ej. "UNA Puno") se pueden nombrar como protagonistas."""
    texto = _plano(" ".join(str(data.get(k) or "") for k in CAMPOS_PUBLICADOS))
    if not texto:
        return None
    vistos = set()
    for m in medios:
        if not m or not str(m).strip() or es_institucion(str(m)):
            continue
        for v, completo in sorted(_variantes(str(m)).items(), key=lambda x: -len(x[0])):
            if v in vistos:
                continue
            vistos.add(v)
            n = r"\s+".join(re.escape(t) for t in v.split())
            n = rf"(?<!\w){n}(?!\w)"
            antes = _ATRIB_ANTES + ("|" + _ATRIB_ANTES_FUERTE if completo else "")
            # "X informó", pero no "el presidente de la República informó" ni "Médicos Sin Fronteras"
            despues = (rf"(?<!\bde )(?<!\bdel )(?<!\bmedicos )(?<!\breporteros ){n},?\s+"
                       rf"(?:(?:ya|tambien|asimismo|recientemente|hoy|ayer)\s+)?(?:{_ATRIB_DESPUES})(?!\w)")
            if re.search(rf"(?<!\w)(?:{antes})\s+{_ENLACE}{n}", texto) or re.search(despues, texto) or \
               re.search(rf"(?:foto|fotos|imagen|video|fuente|credito|captura)\s*[:/(-]\s*{_ENLACE}{n}", texto):
                return str(m).strip()
    if _GENERICAS.search(texto):
        return "Facebook / medio"
    return None


RECORDATORIO = ("\n\nATENCIÓN: tu borrador anterior atribuía la información a un medio o a la publicación "
                "de origen ({medio}). Reescribe la nota en voz propia, sin nombrar a ningún medio, radio, "
                "diario, canal ni página, y sin aludir a la publicación, el video ni Facebook, en NINGÚN campo.")


# ------------------------------------------------------------------ utilidades
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
          "octubre", "noviembre", "diciembre")


def fecha_legible(iso: str | None) -> str:
    """'2026-11-03T10:18:00' -> 'martes 3 de noviembre de 2026, 10:18' (la IA calcula 'ayer' bien)."""
    if not iso:
        return "desconocida"
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    txt = f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} de {d.year}"
    return txt + (f", {d:%H:%M}" if (d.hour, d.minute) != (0, 0) else "")


MAX_HTML_BYTES = 3_000_000  # tope de descarga de la nota enlazada


def fetch_article(url: str, max_chars: int = 8000) -> str:
    """Descarga el texto principal de la nota enlazada (best effort)."""
    if not url or re.search(r"(facebook|fb|instagram|youtube|youtu\.be|tiktok|whatsapp|lnk\.bio|wa\.me)\.", url):
        return ""
    try:
        with requests.get(url, timeout=(10, 20), stream=True, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}) as r:
            r.raise_for_status()
            if "html" not in r.headers.get("content-type", "html"):
                return ""
            crudo, t0 = b"", time.time()
            for trozo in r.iter_content(64 * 1024):
                crudo += trozo
                if len(crudo) > MAX_HTML_BYTES or time.time() - t0 > 30:
                    break  # página enorme o servidor lento: se usa lo que llegó
            html = crudo.decode(r.encoding or r.apparent_encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "aside", "header", "form", "noscript"]):
            t.decompose()
        root = soup.find("article") or soup.body or soup
        parrafos = [p.get_text(" ", strip=True) for p in root.find_all("p")]
        texto = "\n".join(dict.fromkeys(p for p in parrafos if len(p) > 40))
        return texto[:max_chars]
    except Exception:
        return ""


class ErrorIA(RuntimeError):
    """Error de la IA con mensaje listo para mostrar en la interfaz."""


class ErrorFormato(ErrorIA):
    """La IA respondió, pero sin un JSON utilizable (vale la pena un reintento)."""


def parse_json(content: str) -> dict:
    """Extrae el objeto JSON de la respuesta: tolera bloques ```json, texto alrededor,
    saltos de línea crudos dentro de cadenas y comas finales. Lanza ErrorIA si no hay JSON."""
    content = (content or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if m and "{" in m.group(1):
        content = m.group(1)
    i, j = content.find("{"), content.rfind("}")
    if i < 0 or j <= i:
        raise ErrorFormato("la IA no devolvió JSON" + (" (respuesta vacía)" if not content else
                                                   " (respuesta incompleta o en texto libre)"))
    bruto = content[i:j + 1]
    try:
        data = json.loads(bruto, strict=False)
    except json.JSONDecodeError:
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", bruto), strict=False)
        except json.JSONDecodeError as e:
            raise ErrorFormato(f"la IA devolvió JSON inválido ({e.msg}, carácter {e.pos})") from e
    if not isinstance(data, dict):
        raise ErrorFormato("la IA devolvió JSON que no es un objeto")
    return data


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "\n\n".join(_texto(x) for x in v if x)
    return str(v).strip()


def normalizar_nota(data: dict) -> dict:
    """Campos de texto siempre como str (nunca 'None'), DATOS siempre lista."""
    for k in CAMPOS_PUBLICADOS:
        data[k] = _texto(data.get(k))
    data["CUERPO"] = re.sub(r"\n{3,}", "\n\n", data["CUERPO"].replace("\\n", "\n"))
    data["TITULAR"] = data["TITULAR"].rstrip(".").strip()
    datos = data.get("DATOS")
    if datos is None or datos == "":
        datos = []
    elif not isinstance(datos, list):
        datos = [datos]
    data["DATOS"] = [d for d in datos if d]
    return data


_parse_json = parse_json  # nombre antiguo


def _esperar(segundos: float, cancelar=None) -> bool:
    """Duerme en tramos cortos; devuelve True si se pidió cancelar."""
    fin = time.time() + segundos
    while time.time() < fin:
        if cancelar and cancelar():
            return True
        time.sleep(min(0.5, max(0.0, fin - time.time())))
    return bool(cancelar and cancelar())


def _espera_reintento(r, intento: int) -> float:
    try:
        ra = float(r.headers.get("retry-after", "")) if r is not None else 0.0
    except ValueError:
        ra = 0.0
    return min(60.0, max(ra, 4.0 * 2 ** intento) + random.uniform(0, 2))


_PATRON_CLAVE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def _sin_clave(texto: str, key: str = "") -> str:
    """Quita cualquier API key (la nuestra o algo con forma de clave) de un texto de error/log."""
    texto = str(texto or "")
    if key:
        texto = texto.replace(key, "***")
    return _PATRON_CLAVE.sub("sk-***", texto)


_REINTENTABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}
# modelos que aceptan output_config.effort
_CON_ESFUERZO = re.compile(r"claude-(?:(?:opus|sonnet|haiku|fable|mythos)-5|opus-4-[5-8]|sonnet-4-6)")


def _error_claude(status: int, body: str, model: str) -> ErrorIA:
    body = _sin_clave(body)
    try:
        msg = json.loads(body).get("error", {}).get("message", "") or body[:200]
    except ValueError:
        msg = body[:200]
    if status == 401:
        return ErrorIA("Claude rechazó la API key (401). Revísala en la pestaña Configuración.")
    if status == 403:
        return ErrorIA(f"Claude: la API key no tiene permiso (403): {msg}")
    if status == 404:
        return ErrorIA(f"Claude: el modelo «{model}» no existe o no está disponible (404). "
                       "Elige otro en Configuración (p. ej. claude-sonnet-5-5).")
    if status == 400 and re.search(r"credit|balance|billing", msg, re.I):
        return ErrorIA("Claude: la cuenta no tiene saldo suficiente (400). Recarga créditos en console.anthropic.com.")
    if status == 413:
        return ErrorIA("Claude: la solicitud es demasiado grande (413).")
    return ErrorIA(f"Claude {status}: {msg}")


def llamar_claude(model: str, system: str, user: str | list, key: str, *, max_tokens: int = 16000,
                  esfuerzo: str | None = "low", tarea: str = "claude", cancelar=None,
                  intentos: int = 4, timeout: int = 300) -> str:
    """POST /v1/messages con reintentos. Devuelve el texto de la respuesta.

    - El prompt de sistema va con cache_control: las llamadas seguidas (varias notas en un
      ciclo) leen ESTILO de la caché a ~10 % del precio.
    - Los modelos actuales razonan por defecto y ese razonamiento cuenta dentro de max_tokens;
      por eso max_tokens es holgado y el esfuerzo se baja a 'low'/'medium' (cuesta menos).
    - Si la respuesta se corta por max_tokens, se reintenta una vez con el doble."""
    if not key:
        raise ErrorIA("Falta la API key de Claude (pestaña Configuración).")
    body = {"model": model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}]}
    if esfuerzo and _CON_ESFUERZO.search(model or ""):
        body["output_config"] = {"effort": esfuerzo}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    ultimo = "sin respuesta"
    ampliado = False
    intento = 0
    while intento < intentos:
        if cancelar and cancelar():
            raise ErrorIA(f"{tarea}: cancelado")
        t0 = time.time()
        r = None
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body,
                              timeout=(15, timeout))
        except requests.RequestException as e:
            ultimo = f"error de red: {type(e).__name__}"
            api_log.error("%s intento=%d %s", tarea, intento + 1, _sin_clave(e, key))
            intento += 1
            if intento < intentos and _esperar(_espera_reintento(None, intento), cancelar):
                raise ErrorIA(f"{tarea}: cancelado") from e
            continue
        dt = time.time() - t0
        if r.status_code == 200:
            try:
                res = r.json()
            except ValueError:
                res = {}
            u = res.get("usage") or {}
            stop = res.get("stop_reason")
            api_log.info("%s OK modelo=%s in=%s out=%s cache_w=%s cache_r=%s stop=%s %.1fs", tarea, model,
                         u.get("input_tokens"), u.get("output_tokens"), u.get("cache_creation_input_tokens"),
                         u.get("cache_read_input_tokens"), stop, dt)
            text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
            if stop == "refusal":
                raise ErrorIA(f"{tarea}: Claude se negó a procesar este contenido (filtro de seguridad).")
            if stop == "max_tokens" and not ampliado:
                ampliado = True
                body["max_tokens"] = min(64000, max_tokens * 2)
                api_log.warning("%s cortada por max_tokens=%d: se reintenta con %d", tarea, max_tokens,
                                body["max_tokens"])
                continue  # no cuenta como intento fallido
            if not text.strip():
                ultimo = f"respuesta vacía (stop={stop})"
                api_log.error("%s %s", tarea, ultimo)
                intento += 1
                continue
            return text
        api_log.error("%s HTTP %s intento=%d %.1fs %s", tarea, r.status_code, intento + 1, dt,
                      _sin_clave(r.text[:300], key))
        if r.status_code == 400 and "output_config" in body and re.search(r"effort|output_config", r.text):
            body.pop("output_config")  # modelo antiguo sin 'effort': se reintenta sin él
            continue
        if r.status_code in _REINTENTABLE:
            ultimo = f"HTTP {r.status_code}" + (" (límite de uso)" if r.status_code == 429 else
                                                 " (servidor saturado)" if r.status_code == 529 else "")
            intento += 1
            if intento < intentos and _esperar(_espera_reintento(r, intento), cancelar):
                raise ErrorIA(f"{tarea}: cancelado")
            continue
        raise _error_claude(r.status_code, _sin_clave(r.text, key), model)
    raise ErrorIA(f"{tarea}: Claude no respondió tras {intentos} intentos ({ultimo}).")


def llamar_deepseek(model: str, system: str, user: str, key: str, *, max_tokens: int = 8000,
                    tarea: str = "deepseek", intentos: int = 4) -> str:
    if not key:
        raise ErrorIA("Falta la API key de DeepSeek (pestaña Configuración).")
    ultimo = "sin respuesta"
    for intento in range(intentos):
        r = None
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "temperature": 0.3, "max_tokens": max_tokens,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=(15, 240),
            )
        except requests.RequestException as e:
            ultimo = f"error de red: {type(e).__name__}"
            api_log.error("%s intento=%d %s", tarea, intento + 1, _sin_clave(e, key))
            _esperar(_espera_reintento(None, intento + 1))
            continue
        if r.status_code == 200:
            try:
                res = r.json()
                ch = res["choices"][0]
                text = ch["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError):
                res, ch, text = {}, {}, ""
            u = res.get("usage") or {}
            api_log.info("%s OK modelo=%s in=%s out=%s cache_r=%s fin=%s", tarea, model, u.get("prompt_tokens"),
                         u.get("completion_tokens"), u.get("prompt_cache_hit_tokens"), ch.get("finish_reason"))
            if text.strip():
                return text
            ultimo = "respuesta vacía"
            continue
        api_log.error("%s HTTP %s intento=%d %s", tarea, r.status_code, intento + 1, _sin_clave(r.text[:300], key))
        if r.status_code == 401:
            raise ErrorIA("DeepSeek rechazó la API key (401). Revísala en la pestaña Configuración.")
        if r.status_code == 402:
            raise ErrorIA("DeepSeek: la cuenta no tiene saldo (402).")
        if r.status_code in _REINTENTABLE:
            ultimo = f"HTTP {r.status_code}"
            _esperar(_espera_reintento(r, intento + 1))
            continue
        raise ErrorIA(f"DeepSeek {r.status_code}: {_sin_clave(r.text[:300], key)}")
    raise ErrorIA(f"DeepSeek no respondió tras {intentos} intentos ({ultimo}).")


def esfuerzo_cfg(cfg: dict, defecto: str) -> str:
    """Nivel de esfuerzo de Claude: cfg['esfuerzo_claude'] si existe, si no el de la tarea."""
    e = str(cfg.get("esfuerzo_claude") or "").strip().lower()
    return e if e in ("low", "medium", "high", "xhigh", "max") else defecto


# ------------------------------------------------------------------ Fase 1: nota individual
def redactar(post: dict, cfg: dict) -> dict:
    """post: fila de la BD (dict). Devuelve el JSON de la IA + LINKS reales."""
    proveedor = cfg["proveedor_ia"]
    if proveedor not in ("claude", "deepseek"):
        raise ErrorIA(f"Proveedor de IA desconocido: {proveedor!r}.")
    key = get_api_key(proveedor)
    if not key:
        raise ErrorIA(f"Falta la API key de {'Claude' if proveedor == 'claude' else 'DeepSeek'} "
                      "(pestaña Configuración).")
    nota = fetch_article(post.get("enlace_ext"))
    medio = nombre_medio(post["pagina"], cfg)
    if es_institucion(medio):
        origen = (f"PÁGINA DE ORIGEN: {medio} (es una institución: puedes nombrarla como protagonista "
                  "del hecho, pero no como fuente: nada de 'publicó en su página')\n")
    else:
        origen = f"MEDIO QUE LO DIFUNDIÓ (solo referencia interna, NO lo menciones): {medio}\n"
    user = (origen +
            f"FECHA DE PUBLICACIÓN: {fecha_legible(post.get('publicado'))}\n\n"
            f"TEXTO DE LA PUBLICACIÓN:\n{(post.get('texto') or '(sin texto)')[:6000]}\n")
    if nota:
        user += f"\nTEXTO DE LA NOTA ENLAZADA:\n{nota}\n"
    tarea = f"redactar[{post.get('id', '?')}]"

    def llamar(u, sufijo=""):
        if proveedor == "claude":
            txt = llamar_claude(cfg["modelo_claude"], SYSTEM, u, key, max_tokens=12000,
                                esfuerzo=esfuerzo_cfg(cfg, "low"), tarea=tarea + sufijo)
        else:
            txt = llamar_deepseek(cfg["modelo_deepseek"], SYSTEM, u, key, tarea=tarea + sufijo)
        try:
            d = normalizar_nota(parse_json(txt))
        except ErrorFormato as e:
            api_log.error("%s%s %s | %s", tarea, sufijo, e, txt[:300])
            raise
        if not d["TITULAR"] or not d["CUERPO"]:
            raise ErrorFormato("la IA devolvió una nota sin TITULAR o sin CUERPO")
        return d

    try:
        data = llamar(user)
    except ErrorFormato:  # JSON roto o nota vacía: un reintento
        data = llamar(user, "+json")
    culpable = menciona_medio(data, [medio, post["pagina"]])
    if culpable:  # un solo reintento, con recordatorio
        api_log.info("%s nombraba al medio %s: se reescribe", tarea, culpable)
        data = llamar(user + RECORDATORIO.format(medio=culpable), "+reintento")
        culpable = menciona_medio(data, [medio, post["pagina"]])
        if culpable:
            api_log.warning("%s sigue nombrando al medio %s tras el reintento", tarea, culpable)
            data["_menciona_medio"] = culpable  # para que el editor lo revise
    es_noticia = data.pop("ES_NOTICIA", True)
    data["_es_noticia"] = es_noticia is not False and str(es_noticia).lower() != "false"
    # los enlaces no se le piden a la IA: se ponen los reales
    data["LINKS"] = {"facebook": post.get("permalink"), "externo": post.get("enlace_ext")}
    data["_proveedor"] = proveedor
    data["_nota_externa_leida"] = bool(nota)
    return data
