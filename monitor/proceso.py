"""Control del proceso: una sola copia de la app y cierre limpio del navegador del monitor."""
import ctypes
import subprocess
import sys

from .config import PROFILE_DIR

_mutex = None
TITULO = "Altiplano al Día — Monitor de noticias"


def unica_instancia() -> bool:
    """True si esta es la única copia abierta (mutex con nombre de Windows)."""
    global _mutex
    if sys.platform != "win32":
        return True
    # use_last_error: ctypes guarda el GetLastError() justo tras la llamada; leerlo después con
    # windll.kernel32.GetLastError() no es fiable (cualquier llamada intermedia lo pisa)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = k32.CreateMutexW(None, False, "AltiplanoAlDia_Monitor_UnaInstancia")
    if not _mutex:
        return True             # no se pudo crear: mejor abrir que bloquear la app
    return ctypes.get_last_error() != 183      # ERROR_ALREADY_EXISTS


def traer_ventana_existente() -> None:
    """Muestra al frente la ventana de la copia que ya está abierta."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITULO)
    if hwnd:
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def cerrar_navegador_monitor() -> int:
    """Cierra los Chrome/Edge que usan el perfil del monitor (fb_profile). Devuelve cuántos cerró.
    No toca el Chrome normal del usuario: solo los procesos con ese perfil en la línea de comandos."""
    if sys.platform != "win32":
        return 0
    perfil = str(PROFILE_DIR).replace("'", "''")
    ps = ("$n=0; Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='msedge.exe' or "
          "Name='chromium.exe'\" | Where-Object { $_.CommandLine -like '*" + perfil + "*' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; $n")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int((r.stdout or "0").strip().splitlines()[-1] or 0)
    except Exception:  # noqa: BLE001
        return 0
