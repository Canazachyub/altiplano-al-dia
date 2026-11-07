"""Sube las notas del día a Google Sheets (panel de redacción de altiplanoaldia.pe/admin).

La hoja la maneja un Apps Script (repositorio del sitio, carpeta apps-script/). Cada publicación reescribe
la hoja «Hoy» y la hoja del día (AAAA-MM-DD), que queda como archivo. Las ediciones que se hagan en el
panel se guardan aparte (hoja «Ediciones») y solo cambian la web: el maquetador nunca las pisa.

Configuración:
  config.json  "sheets_api": URL de la aplicación web de Apps Script (termina en /exec)
  .env         SHEETS_CLAVE=<clave API que muestra el menú Altiplano → Ver clave API>
"""
from __future__ import annotations

import json
import logging
import os

import requests
from dotenv import load_dotenv

from .config import ENV_PATH

log = logging.getLogger("monitor")


def _clave() -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("SHEETS_CLAVE", "").strip()


def configurada(cfg) -> bool:
    return bool((cfg.get("sheets_api") or "").strip() and _clave())


def subir(cfg, notas: list, dia: str) -> str:
    """Manda las notas del día a la hoja. Devuelve un resumen; no lanza errores (la web ya salió)."""
    if not configurada(cfg):
        return "Sheets sin configurar (sheets_api en config.json y SHEETS_CLAVE en .env)."
    campos = ("id", "fecha", "seccion", "antetitulo", "titular", "bajada", "cuerpo", "lugar", "imagen", "slug")
    try:
        fotos = _subir_fotos(cfg, notas)
        j = _post(cfg, {"accion": "publicar", "clave": _clave(), "dia": dia,
                        "notas": [{k: n.get(k) for k in campos} for n in notas]})
    except requests.RequestException as e:
        log.warning("Sheets: sin conexión (%s)", e)
        return f"Sheets: no se pudo subir (sin conexión con Apps Script: {e.__class__.__name__})."
    except Exception as e:  # noqa: BLE001
        log.warning("Sheets: no se pudo subir (%s)", e)
        return f"Sheets: no se pudo subir ({e})."
    return f"Sheets: {j.get('notas')} nota(s) en la hoja {j.get('dia')}; {fotos} foto(s) nueva(s) en Drive."


def _post(cfg, datos: dict, timeout=120) -> dict:
    r = requests.post(cfg["sheets_api"].strip(), data=json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "text/plain;charset=utf-8"}, timeout=timeout)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(j.get("error") or "respuesta inválida")
    return j


def _subir_fotos(cfg, notas) -> int:
    """Sube a Drive las fotos de las notas que todavía no están (se guardan con su nombre de la web)."""
    import base64
    from .web import STAGE
    rutas = {}
    for n in notas:
        rel = n.get("imagen")
        if rel and (STAGE / rel).exists():
            rutas[rel.split("/")[-1]] = STAGE / rel
    if not rutas:
        return 0
    faltan = _post(cfg, {"accion": "fotos_faltan", "clave": _clave(), "nombres": list(rutas)}).get("faltan", [])
    subidas = 0
    for nombre in faltan:
        try:
            _post(cfg, {"accion": "subir_foto", "clave": _clave(), "nombre": nombre, "tipo": "image/jpeg",
                        "b64": base64.b64encode(rutas[nombre].read_bytes()).decode()}, timeout=180)
            subidas += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Drive: no se pudo subir la foto %s (%s)", nombre, e)
    return subidas
