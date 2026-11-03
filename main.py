import sys
import traceback
from pathlib import Path

LOG = Path(__file__).resolve().parent / "data" / "error.log"


def _log_exc(exc_type, exc, tb):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        traceback.print_exception(exc_type, exc, tb, file=f)
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_exc

import threading  # noqa: E402

threading.excepthook = lambda a: _log_exc(a.exc_type, a.exc_value, a.exc_traceback)

from monitor.proceso import cerrar_navegador_monitor, traer_ventana_existente, unica_instancia  # noqa: E402

if __name__ == "__main__":
    if not unica_instancia():
        # ya hay una copia abierta: se muestra esa y no se abre otra (evita revisiones duplicadas)
        traer_ventana_existente()
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Altiplano al Día", "La aplicación ya está abierta. Se mostró la ventana existente.")
        r.destroy()
        sys.exit(0)
    cerrar_navegador_monitor()      # restos de una sesión anterior que se cerró mal

    from monitor.gui import App

    app = App()
    app.report_callback_exception = _log_exc
    app.mainloop()
