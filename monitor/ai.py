"""Resumen y ampliación de la noticia con DeepSeek o Claude (vía HTTP, sin SDK)."""
import json
import re

import requests
from bs4 import BeautifulSoup

from .config import get_api_key

SYSTEM = """Eres un editor de noticias regionales de Puno (Perú). Recibes el texto de una
publicación de Facebook de un medio o institución y, si existe, el texto de la nota
enlazada. Redacta una nota periodística en español neutro.

Reglas:
- NO inventes datos, nombres, cifras, fechas ni citas que no estén en la fuente.
- Si la fuente es escueta, el CUERPO puede ser breve; no rellenes con suposiciones.
- Si la publicación no es una noticia (saludo, condolencia, aviso), redáctala igual de forma informativa.

Responde SOLO con un objeto JSON válido, sin texto adicional, con estas claves:
{
  "TITULAR": "titular informativo, máximo 14 palabras",
  "BAJADA": "1 o 2 líneas que resumen la noticia",
  "CUERPO": "redacción ampliada en párrafos separados por \\n\\n",
  "DATOS": ["dato o cifra importante", "..."]
}"""


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
    for k in ("TITULAR", "BAJADA", "CUERPO"):
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
    user = (f"PÁGINA FUENTE: {post['pagina']}\nFECHA: {post.get('publicado') or 'desconocida'}\n\n"
            f"TEXTO DE LA PUBLICACIÓN:\n{post.get('texto') or '(sin texto)'}\n")
    if nota:
        user += f"\nTEXTO DE LA NOTA ENLAZADA ({post['enlace_ext']}):\n{nota}\n"
    if proveedor == "claude":
        content = _call_claude(cfg["modelo_claude"], user, key)
    else:
        content = _call_deepseek(cfg["modelo_deepseek"], user, key)
    data = _parse_json(content)
    # los enlaces no se le piden a la IA: se ponen los reales
    data["LINKS"] = {"facebook": post.get("permalink"), "externo": post.get("enlace_ext")}
    data["_proveedor"] = proveedor
    data["_nota_externa_leida"] = bool(nota)
    return data
