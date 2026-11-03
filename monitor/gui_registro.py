"""Panel "Registro de actividad": lo que hace el programa, en vivo y con barra de desplazamiento."""
import logging
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from .config import LOG_PATH

F = "Segoe UI"
MAX_LINEAS = 3000
COLORES = {"INFO": "#374151", "DETALLE": "#9CA3AF", "AVISO": "#B45309", "ERROR": "#B3261E", "PASO": "#1D4ED8",
           "OK": "#1E7B3A"}


class ColaHandler(logging.Handler):
    """Manda cada registro de logging a la cola de la interfaz (los hilos no tocan Tk directamente)."""

    def __init__(self, q):
        super().__init__(level=logging.INFO)
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", (record.levelname, record.created, record.getMessage())))
        except Exception:  # noqa: BLE001
            pass


class PanelRegistro(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D9DEE7")
        self.app = app
        cab = tk.Frame(self, bg="#E9EDF4")
        cab.pack(fill="x")
        tk.Label(cab, text="📋 Registro de actividad", bg="#E9EDF4", fg="#16324F",
                 font=(F, 10, "bold")).pack(side="left", padx=10, pady=4)
        self.detalle = tk.BooleanVar(value=False)
        ttk.Checkbutton(cab, text="Mostrar detalle técnico", variable=self.detalle,
                        command=self._recargar).pack(side="left", padx=12)
        self.seguir = tk.BooleanVar(value=True)
        ttk.Checkbutton(cab, text="Seguir lo último", variable=self.seguir).pack(side="left")
        ttk.Button(cab, text="Abrir archivo", command=lambda: LOG_PATH.exists() and os.startfile(str(LOG_PATH))  # noqa: S606
                   ).pack(side="right", padx=(4, 8), pady=2)
        ttk.Button(cab, text="Copiar", command=self._copiar).pack(side="right", padx=4, pady=2)
        ttk.Button(cab, text="Limpiar vista", command=self._limpiar).pack(side="right", padx=4, pady=2)

        cuerpo = tk.Frame(self, bg="#FFFFFF")
        cuerpo.pack(fill="both", expand=True)
        self.txt = tk.Text(cuerpo, height=7, wrap="none", font=("Consolas", 9), bg="#FFFFFF", fg="#374151",
                           relief="flat", padx=8, pady=4, state="disabled", cursor="arrow")
        vs = ttk.Scrollbar(cuerpo, orient="vertical", command=self.txt.yview)
        hs = ttk.Scrollbar(cuerpo, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.txt.pack(fill="both", expand=True)
        for k, c in COLORES.items():
            self.txt.tag_configure(k, foreground=c)
        self.txt.tag_configure("hora", foreground="#9CA3AF")
        self.txt.tag_configure("ERROR", foreground=COLORES["ERROR"], font=("Consolas", 9, "bold"))
        self.lineas = []          # (tipo, hora, texto) para poder refiltrar
        self._historial()

    # ------------------------------------------------------------------
    @staticmethod
    def _tipo(nivel, texto):
        if nivel in ("ERROR", "CRITICAL"):
            return "ERROR"
        if nivel == "WARNING":
            return "AVISO"
        t = texto.lstrip()
        if t.startswith("[") or t.startswith("Traceback") or t.startswith("File ") or "#" in t[:25]:
            return "DETALLE"
        return "INFO"

    def _historial(self):
        """Carga las últimas líneas del archivo de log para poder ver lo anterior."""
        if not LOG_PATH.exists():
            return
        try:
            with LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()[-600:]
        except OSError:
            return
        for ln in lineas:
            ln = ln.rstrip("\n")
            partes = ln.split(" ", 3)
            if len(partes) == 4 and partes[2] in ("INFO", "WARNING", "ERROR", "CRITICAL"):
                hora = partes[1][:8]
                self.lineas.append((self._tipo(partes[2], partes[3]), hora, partes[3]))
            elif ln.strip():
                self.lineas.append(("DETALLE", "", ln))
        self.lineas.append(("PASO", datetime.now().strftime("%H:%M:%S"), "— app abierta —"))
        self._recargar()

    def agregar(self, tipo, texto, hora=None):
        hora = hora or datetime.now().strftime("%H:%M:%S")
        self.lineas.append((tipo, hora, texto))
        if len(self.lineas) > MAX_LINEAS:
            self.lineas = self.lineas[-MAX_LINEAS:]
        if tipo == "DETALLE" and not self.detalle.get():
            return
        self._escribir([(tipo, hora, texto)])

    def desde_logging(self, nivel, creado, texto):
        hora = datetime.fromtimestamp(creado).strftime("%H:%M:%S")
        self.agregar(self._tipo(nivel, texto), texto, hora)

    def _escribir(self, items):
        self.txt.configure(state="normal")
        for tipo, hora, texto in items:
            if hora:
                self.txt.insert("end", f"{hora}  ", "hora")
            self.txt.insert("end", texto + "\n", tipo)
        exceso = int(self.txt.index("end-1c").split(".")[0]) - MAX_LINEAS
        if exceso > 0:
            self.txt.delete("1.0", f"{exceso + 1}.0")
        self.txt.configure(state="disabled")
        if self.seguir.get():
            self.txt.see("end")

    def _recargar(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        ver = [x for x in self.lineas if self.detalle.get() or x[0] != "DETALLE"]
        self._escribir(ver[-1500:])

    def _limpiar(self):
        self.lineas = []
        self._recargar()

    def _copiar(self):
        self.clipboard_clear()
        self.clipboard_append(self.txt.get("1.0", "end"))
        self.app._aviso("Registro copiado al portapapeles.", "ok")
