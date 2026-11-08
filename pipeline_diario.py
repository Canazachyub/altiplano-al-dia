"""Pipeline completo sin intervención (para el Programador de tareas de Windows).

1. Revisa Facebook (todas las páginas activas).
2. Fase 2: agrupa, redacta, une repetidas con lo ya publicado (hoy y ayer), ajusta largos.
3. Fotos: a las notas sin foto se les busca una en Google Imágenes (--sin-google lo salta);
   la IA elige la mejor y quita rótulos / marcas de agua.
4. Boletín del día: si ya salió uno hoy lo actualiza (mismo N°); si no, genera el del día.
   Lo de hoy va primero; los huecos se llenan con notas de ayer que no salieron en papel.
   La ficha de verificación queda en un PDF aparte y NO se publica.
5. Web: todas las noticias del día + el boletín → altiplanoaldia.pe (git push del sitio).

Uso:  .venv\\Scripts\\python.exe pipeline_diario.py
Registro: data\\pipeline_diario.log
"""
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
SITIO = Path(r"E:\Altiplano al dia")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(BASE / "data" / "pipeline_diario.log", encoding="utf-8"),
                              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("pipeline")


def emit(ev, d=None):
    if ev in ("status", "error", "fin", "consolidado", "fase2_fin"):
        log.info("[%s] %s", ev, d)


def cerrar_gui() -> bool:
    """Cierra el maquetador si está abierto (usa el mismo navegador y la misma base)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'pythonw%'\" | "
          "Where-Object { $_.CommandLine -match 'main.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'x' }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return "x" in r.stdout


def abrir_gui():
    subprocess.Popen([str(BASE / ".venv" / "Scripts" / "pythonw.exe"), "main.py"], cwd=str(BASE),
                     creationflags=0x00000008)  # DETACHED_PROCESS


def main():
    log.info("===== Pipeline diario %s =====", datetime.now().strftime("%d/%m/%Y %H:%M"))
    cerrar_gui()
    try:
        from monitor import config, storage
        from monitor.worker import revisar
        from monitor.analisis import ciclo_fase2, fase2_lock
        from monitor import consolidado, web
        from monitor.fotos_limpias import paso_limpieza

        cfg = config.load_config()
        storage.init_db()
        log.info("Día editorial: %s", storage.dia_editorial())

        if "--sin-facebook" not in sys.argv:   # --sin-facebook: solo fotos, boletín y web
            # 1) Facebook
            try:
                revisar(cfg, emit)
            except Exception:  # noqa: BLE001
                log.exception("Revisión de Facebook")

            # 2) Fase 2 (sin PDF ni limpieza en segundo plano: se hacen abajo, en orden)
            ciclo_fase2(dict(cfg, pdf_consolidado_auto=False, limpiar_fotos=False), emit)

        # 3a) Notas sin foto: se busca una en Google Imágenes (después pasa por Qwen y el OCR)
        if "--sin-google" not in sys.argv:
            try:
                from monitor.buscar_foto import paso_buscar_fotos
                log.info("Fotos de Google: %s", paso_buscar_fotos(cfg, emit))
            except Exception:  # noqa: BLE001
                log.exception("Búsqueda de fotos en Google")

        # 3) Fotos (si la revisión dejó una limpieza en segundo plano, se espera a que termine)
        from monitor.fotos_limpias import _lock as fotos_lock
        with fotos_lock:
            pass
        try:
            log.info("Fotos: %s", paso_limpieza(cfg, emit))
        except Exception:  # noqa: BLE001
            log.exception("Limpieza de fotos")

        # 4) Boletín del día
        with fase2_lock:
            if storage.consolidado_del_dia():
                res = consolidado.actualizar_boletin(cfg, emit)
                log.info("Boletín actualizado: %s", res)
            else:
                out = consolidado.generar_consolidado(cfg)
                c = storage.consolidado_del_dia()
                log.info("Boletín nuevo: %s (N° %s, %s notas)", out, c and c["n"], c and c["n_noticias"])
                if c:
                    storage.set_en_web([g["id"] for g in storage.grupos_de_consolidado(c["n"])], True)
            storage.set_en_web([g["id"] for g in storage.todas_del_dia()], True)
            storage.set_en_web([g["id"] for g in storage.grupos_para_pdf()], True)   # desde las 3 p. m. de ayer

        # 5) Web (se arma en la carpeta local; todavía NO se sube)
        log.info(web.publicar(cfg, avisar=lambda t: log.info("web: %s", t), subir=False))

        # 6) Control de calidad: si hay errores no se publica nada (regla)
        from monitor import revision
        inf = {"boletin": revision.revisar_boletin(cfg), "web": revision.revisar_web(SITIO / "public", cfg)}
        revision.guardar(inf)
        for parte, r in inf.items():
            for e in r["errores"]:
                log.error("REVISIÓN %s: %s", parte, e)
            for a in r["avisos"]:
                log.warning("revisión %s: %s", parte, a)
        if any(r["errores"] for r in inf.values()):
            log.error("La revisión encontró errores: NO se sube la web. Ver data/revision/ultima.json")
            return
        log.info("Web: %s", web.subir_sitio(
            cfg, f"Web y boletín actualizados {datetime.now():%d/%m/%Y %H:%M} (automático)"))

        # 7) Notas del día a Google Sheets (panel /admin y archivo por días). Si falla, la web ya salió.
        import json as _json
        from monitor import hoja
        notas_web = _json.loads((web.STAGE / "data" / "noticias.json").read_text(encoding="utf-8"))
        log.info(hoja.subir(cfg, notas_web, storage.dia_editorial()))
    except Exception:  # noqa: BLE001
        log.exception("Pipeline diario")
    finally:
        abrir_gui()                      # el maquetador queda abierto al terminar
        log.info("===== Fin del pipeline %s =====", datetime.now().strftime("%H:%M"))


if __name__ == "__main__":
    main()
