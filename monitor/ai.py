"""Resumen y ampliación de la noticia con DeepSeek o Claude (vía HTTP, sin SDK)."""
import json
import re

import requests
from bs4 import BeautifulSoup

from .config import get_api_key, nombre_medio

ESTILO = """Eres el redactor principal de "Altiplano al Día", un medio de noticias de Puno (Perú).
Tu trabajo NO es resumir lo que dijo otro medio: es escribir TU PROPIA nota, con voz propia,
usando como materia prima la información ya publicada.

PROHIBIDO (regla más importante):
- Nombrar o aludir al medio, radio, diario, canal o página de donde sale la información.
  Nada de "según Radio X", "informó Diario Y", "La República publicó", "en su página de
  Facebook", "fuentes periodísticas", "un medio local", "la publicación", "el post".
  Los hechos se cuentan en voz propia, como si nuestro equipo los hubiera reportado.
- Sí se nombra a las personas, autoridades e instituciones que son PROTAGONISTAS del hecho
  o que dan declaraciones (p. ej. "la UNA Puno anunció…", "el alcalde declaró…"). Lo que no
  se nombra es al medio que difundió la noticia.

ESTRUCTURA (pirámide invertida, que enganche e informe):
- TITULAR: concreto, con verbo en activo y el dato más fuerte. Máximo 14 palabras.
  Sin signos de exclamación, sin clickbait, sin mayúsculas sostenidas.
- BAJADA: 1 o 2 líneas que completan el titular con el segundo dato clave (dónde, cuánto, a quién afecta).
- CUERPO, en párrafos separados por \\n\\n:
  1. Lead: un primer párrafo de 1 o 2 oraciones que responde qué, quién, dónde y cuándo,
     y que haga querer seguir leyendo.
  2. Desarrollo: los detalles en orden de importancia, cifras, declaraciones entre comillas
     (solo si están en la fuente, atribuidas a quien las dijo) y antecedentes que estén en la fuente.
  3. Cierre: qué sigue, a quién afecta o qué deben saber los lectores (plazos, lugares, requisitos),
     solo si la fuente lo permite.

ESTILO:
- Español neutro, claro y elegante. Oraciones cortas o medianas, una idea por oración.
- Vocabulario preciso y cercano; nada de jerga burocrática ni de relleno ("cabe resaltar",
  "es importante mencionar", "en ese sentido").
- Párrafos de 2 a 4 oraciones. Fechas y horas concretas en lugar de "ayer" u "hoy" cuando se conozcan.
- Tono informativo y sobrio: no opines, no adjetives de más.

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
- NO inventes datos, nombres, cifras, fechas ni citas que no estén en la fuente.
- Si la fuente es escueta, la nota es breve: es mejor corta y exacta que larga y con supuestos."""

SYSTEM = ESTILO + """

Recibes el texto de una publicación y, si existe, el texto de la nota enlazada.
Si la publicación no es una noticia (saludo, condolencia, aviso), redáctala igual de forma informativa.

Responde SOLO con un objeto JSON válido, sin texto adicional, con estas claves:
{
  "ANTETITULO": "…",
  "TITULAR": "…",
  "BAJADA": "…",
  "LUGAR": "…",
  "PIE_FOTO": "…",
  "CUERPO": "párrafos separados por \\n\\n",
  "DATOS": ["dato o cifra importante", "..."]
}"""

_VERBOS_ATRIB = (r"seg[uú]n|inform[oóa]|report[oóa]|public[oóa]|difund|de acuerdo (?:con|a)|"
                 r"indic[oóa]|señal[oóa]|dio a conocer|da a conocer|precis[oóa]|detall[oóa]|"
                 r"consign[oóa]|recog|transmiti")


def menciona_medio(data: dict, medios: list[str]) -> str | None:
    """Devuelve el medio si la redacción se lo atribuye (\"según X\", \"X informó\")."""
    texto = " ".join(str(data.get(k, "")) for k in ("TITULAR", "BAJADA", "CUERPO"))
    for m in {m.strip() for m in medios if m and len(m.strip()) > 2}:
        n = re.escape(m)
        if re.search(rf"(?:{_VERBOS_ATRIB})\W+(?:\w+\W+){{0,3}}?{n}\b", texto, re.I) or \
           re.search(rf"\b{n}\W+(?:\w+\W+){{0,2}}?(?:{_VERBOS_ATRIB})", texto, re.I):
            return m
    if re.search(r"(p[aá]gina|cuenta|fanpage) de facebook|la publicaci[oó]n (original|de)|"
                 r"medios? (local|regional|de comunicaci)", texto, re.I):
        return "Facebook / medio"
    return None


RECORDATORIO = ("\n\nATENCIÓN: tu borrador anterior atribuía la información a un medio ({medio}). "
                "Reescribe la nota en voz propia, sin nombrar a ningún medio, radio, diario ni página.")


def fetch_article(url: str, max_chars: int = 8000) -> str:
    """Descarga el texto principal de la nota enlazada (best effort)."""
    if not url or re.search(r"(facebook|instagram|youtube|youtu\.be|tiktok|whatsapp|lnk\.bio)\.", url):
        return ""
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "aside", "header", "form", "noscript"]):
            t.decompose()
        root = soup.find("article") or soup.body or soup
        parrafos = [p.get_text(" ", strip=True) for p in root.find_all("p")]
        texto = "\n".join(p for p in parrafos if len(p) > 40)
        return texto[:max_chars]
    except Exception:
        return ""


def _parse_json(content: str) -> dict:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    i, j = content.find("{"), content.rfind("}")
    data = json.loads(content[i:j + 1])
    data.setdefault("DATOS", [])
    if isinstance(data["DATOS"], str):
        data["DATOS"] = [data["DATOS"]]
    for k in ("TITULAR", "BAJADA", "CUERPO", "ANTETITULO", "LUGAR", "PIE_FOTO"):
        data[k] = str(data.get(k, "")).strip()
    return data


def _call_deepseek(model, user, key):
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "temperature": 0.3,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": user}]},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"DeepSeek {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def _call_claude(model, user, key):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 3000, "system": SYSTEM,
              "messages": [{"role": "user", "content": user}]},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def redactar(post: dict, cfg: dict) -> dict:
    """post: fila de la BD (dict). Devuelve el JSON de la IA + LINKS reales."""
    proveedor = cfg["proveedor_ia"]
    key = get_api_key(proveedor)
    if not key:
        raise RuntimeError(f"Falta la API key de {proveedor}.")
    nota = fetch_article(post.get("enlace_ext"))
    medio = nombre_medio(post["pagina"], cfg)
    user = (f"MEDIO QUE LO DIFUNDIÓ (solo referencia interna, NO lo menciones): {medio}\n"
            f"FECHA DE PUBLICACIÓN: {post.get('publicado') or 'desconocida'}\n\n"
            f"TEXTO DE LA PUBLICACIÓN:\n{post.get('texto') or '(sin texto)'}\n")
    if nota:
        user += f"\nTEXTO DE LA NOTA ENLAZADA:\n{nota}\n"

    def llamar(u):
        if proveedor == "claude":
            return _parse_json(_call_claude(cfg["modelo_claude"], u, key))
        return _parse_json(_call_deepseek(cfg["modelo_deepseek"], u, key))

    data = llamar(user)
    culpable = menciona_medio(data, [medio, post["pagina"]])
    if culpable:  # un solo reintento, con recordatorio
        data = llamar(user + RECORDATORIO.format(medio=culpable))
    # los enlaces no se le piden a la IA: se ponen los reales
    data["LINKS"] = {"facebook": post.get("permalink"), "externo": post.get("enlace_ext")}
    data["_proveedor"] = proveedor
    data["_nota_externa_leida"] = bool(nota)
    return data
