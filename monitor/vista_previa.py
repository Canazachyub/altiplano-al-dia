"""Vista previa interactiva del periódico, dentro de la app.

Muestra las páginas del PDF que se va a publicar. Clic en una noticia: se abre su editor (texto,
foto, titular de portada) y al guardar la vista se actualiza sola. Clic derecho: quitarla del
periódico o ponerla en un lugar de la portada. Desde aquí también se publica.
"""
import json
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import pypdfium2 as pdfium
from PIL import ImageTk

from . import portada, storage
from .config import BASE_DIR

F = "Segoe UI"
NAVY = "#16324F"
CARPETA = BASE_DIR / "data" / "previa"
ZOOMS = [0.6, 0.8, 1.0, 1.25, 1.5, 2.0]


class VistaPrevia(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Vista previa del periódico — Altiplano al Día")
        self.geometry("1280x900")
        self.minsize(900, 600)
        self.configure(bg="#E9EDF4")
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.doc = None
        self.ruta = None
        self.mapa = {"zonas": [], "titulares": {}}
        self.pagina = 0
        self.zoom = None            # None = ajustar a la altura
        self._img = None
        self._thumbs = []
        self._zona_actual = None
        self._generando = False
        self.ox = self.oy = 0

        self._barra()
        cuerpo = tk.Frame(self, bg="#E9EDF4")
        cuerpo.pack(fill="both", expand=True)
        # miniaturas
        izq = tk.Frame(cuerpo, bg="#DDE3EC", width=170)
        izq.pack(side="left", fill="y")
        izq.pack_propagate(False)
        self.lista = tk.Canvas(izq, bg="#DDE3EC", highlightthickness=0)
        sb = ttk.Scrollbar(izq, command=self.lista.yview)
        self.lista.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lista.pack(fill="both", expand=True)
        self.lista.bind("<MouseWheel>", lambda e: self.lista.yview_scroll(-e.delta // 120, "units"))
        # página
        centro = tk.Frame(cuerpo, bg="#E9EDF4")
        centro.pack(side="left", fill="both", expand=True)
        self.cv = tk.Canvas(centro, bg="#9AA5B4", highlightthickness=0, cursor="arrow")
        vs = ttk.Scrollbar(centro, orient="vertical", command=self.cv.yview)
        hs = ttk.Scrollbar(centro, orient="horizontal", command=self.cv.xview)
        self.cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Motion>", self._mover)
        self.cv.bind("<Leave>", lambda e: self._resaltar(None))
        self.cv.bind("<Button-1>", self._clic)
        self.cv.bind("<Button-3>", self._menu)
        self.cv.bind("<MouseWheel>", self._rueda)
        self.cv.bind("<Configure>", lambda e: self.zoom is None and self._mostrar())
        self.bind("<Left>", lambda e: self.ir(self.pagina - 1))
        self.bind("<Right>", lambda e: self.ir(self.pagina + 1))
        self.bind("<Prior>", lambda e: self.ir(self.pagina - 1))
        self.bind("<Next>", lambda e: self.ir(self.pagina + 1))
        self.protocol("WM_DELETE_WINDOW", self._cerrar)

        self.aviso = tk.Label(self.cv, text="", bg="#FFF4CC", fg="#6B4E00", font=(F, 12, "bold"), padx=18, pady=10,
                              highlightthickness=1, highlightbackground="#F2D27A")
        self.tip = tk.Label(self.cv, text="", bg="#16324F", fg="white", font=(F, 9, "bold"), padx=8, pady=4)
        self.generar()

    # ------------------------------------------------------------------ barra superior
    def _barra(self):
        b = tk.Frame(self, bg=NAVY)
        b.pack(fill="x")
        self.lbl_tit = tk.Label(b, text="Vista previa", bg=NAVY, fg="white", font=(F, 13, "bold"))
        self.lbl_tit.pack(side="left", padx=14, pady=8)
        ttk.Button(b, text="📰 Publicar este periódico", style="Accent.TButton",
                   command=self._publicar).pack(side="right", padx=(4, 12), pady=6)
        ttk.Button(b, text="Abrir en visor PDF", style="Header.TButton",
                   command=lambda: self.ruta and os.startfile(str(self.ruta))).pack(side="right", padx=4)  # noqa: S606
        ttk.Button(b, text="⟳ Actualizar", style="Header.TButton", command=self.generar).pack(side="right", padx=4)
        ttk.Button(b, text="Ajustar", style="Header.TButton", command=lambda: self._zoom(None)).pack(side="right", padx=4)
        ttk.Button(b, text="+", width=3, style="Header.TButton", command=lambda: self._zoom(+1)).pack(side="right")
        ttk.Button(b, text="−", width=3, style="Header.TButton", command=lambda: self._zoom(-1)).pack(side="right", padx=4)
        ttk.Button(b, text="▶", width=3, style="Header.TButton", command=lambda: self.ir(self.pagina + 1)).pack(side="right", padx=(4, 12))
        self.lbl_pag = tk.Label(b, text="", bg=NAVY, fg="white", font=(F, 10))
        self.lbl_pag.pack(side="right")
        ttk.Button(b, text="◀", width=3, style="Header.TButton", command=lambda: self.ir(self.pagina - 1)).pack(side="right", padx=4)
        h = tk.Frame(self, bg="#E8F0FE")
        h.pack(fill="x")
        tk.Label(h, text="Pasa el mouse sobre una noticia para verla marcada.   Clic: editar texto, foto y titular "
                         "de portada.   Clic derecho: quitarla del periódico o ponerla en la portada.",
                 bg="#E8F0FE", fg="#1D4ED8", font=(F, 9)).pack(side="left", padx=14, pady=4)

    # ------------------------------------------------------------------ generar y cargar
    def generar(self):
        if self._generando:
            return
        if self.app.running:
            self._mensaje("Hay una revisión en curso: la vista previa se actualizará al terminar…")
            self.after(3000, self.generar)
            return
        from .analisis import fase2_lock
        if not storage.grupos_para_pdf():
            self._mensaje("No hay noticias marcadas (☑) para el periódico.")
            return
        self._generando = True
        self._mensaje("⏳ Armando la vista previa…")
        CARPETA.mkdir(parents=True, exist_ok=True)
        destino = CARPETA / f"vista_previa_{datetime.now():%Y%m%d_%H%M%S}.pdf"
        cfg = self.app._cfg_actual()

        def run():
            from .consolidado import generar_consolidado
            try:
                with fase2_lock:
                    out = generar_consolidado(cfg, destino=destino, registrar=False)
                self.after(0, lambda: self._cargar(out))
            except Exception as e:  # noqa: BLE001
                msg = f"No se pudo armar la vista previa: {e}"
                self.after(0, lambda: self._mensaje(msg, error=True))
            finally:
                self._generando = False

        threading.Thread(target=run, daemon=True).start()

    def _cargar(self, ruta):
        if not self.winfo_exists():
            return
        if self.doc is not None:
            self.doc.close()
        viejo = self.ruta
        self.ruta = ruta
        self.doc = pdfium.PdfDocument(str(ruta))
        try:
            self.mapa = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.mapa = {"zonas": [], "titulares": {}}
        if viejo and viejo != ruta:      # borra la versión anterior (y su mapa)
            for f in (viejo, viejo.with_suffix(".json")):
                try:
                    f.unlink()
                except OSError:
                    pass
        n = len(storage.grupos_para_pdf())
        self.lbl_tit.configure(text=f"Vista previa · boletín N° {storage.siguiente_consolidado()} · {n} noticias")
        self.pagina = min(self.pagina, len(self.doc) - 1)
        self._miniaturas()
        self._mensaje(None)
        self._mostrar()

    def _miniaturas(self):
        self.lista.delete("all")
        self._thumbs = []
        y = 10
        for i in range(len(self.doc)):
            im = self.doc[i].render(scale=0.2).to_pil()
            ph = ImageTk.PhotoImage(im)
            self._thumbs.append(ph)
            borde = "#2563EB" if i == self.pagina else "#DDE3EC"
            self.lista.create_rectangle(12, y - 3, 12 + im.width + 6, y + im.height + 3, outline=borde, width=3,
                                        tags=(f"t{i}", "marco"))
            self.lista.create_image(15, y, image=ph, anchor="nw", tags=(f"t{i}",))
            self.lista.create_text(15 + im.width / 2, y + im.height + 12, text=str(i + 1), font=(F, 9, "bold"),
                                   fill="#374151", tags=(f"t{i}",))
            self.lista.tag_bind(f"t{i}", "<Button-1>", lambda e, k=i: self.ir(k))
            y += im.height + 30
        self.lista.configure(scrollregion=(0, 0, 160, y))

    # ------------------------------------------------------------------ dibujo de la página
    def _escala(self):
        if self.zoom is not None:
            return self.zoom
        alto = max(200, self.cv.winfo_height() - 24)
        return alto / self.mapa.get("alto", 842)

    def _mostrar(self):
        if self.doc is None:
            return
        s = self._escala()
        im = self.doc[self.pagina].render(scale=s).to_pil()
        self._img = ImageTk.PhotoImage(im)
        self.cv.delete("pagina", "zona")
        cw = max(self.cv.winfo_width(), im.width + 24)
        self.ox = (cw - im.width) / 2
        self.oy = 12
        self.cv.create_rectangle(self.ox + 4, self.oy + 4, self.ox + im.width + 4, self.oy + im.height + 4,
                                 fill="#6B7684", outline="", tags="pagina")
        self.cv.create_image(self.ox, self.oy, image=self._img, anchor="nw", tags="pagina")
        self.cv.configure(scrollregion=(0, 0, cw, im.height + 24))
        self.lbl_pag.configure(text=f"  Página {self.pagina + 1} de {len(self.doc)}  ")
        self.lista.itemconfigure("marco", outline="#DDE3EC")
        for it in self.lista.find_withtag(f"t{self.pagina}"):
            if "marco" in self.lista.gettags(it):
                self.lista.itemconfigure(it, outline="#2563EB")
        self._zona_actual = None

    def ir(self, i):
        if self.doc is None:
            return
        i = max(0, min(len(self.doc) - 1, i))
        if i != self.pagina:
            self.pagina = i
            self._mostrar()
            self.cv.yview_moveto(0)

    def _zoom(self, paso):
        if paso is None:
            self.zoom = None
        else:
            actual = self._escala()
            if paso > 0:
                self.zoom = next((z for z in ZOOMS if z > actual + 0.01), ZOOMS[-1])
            else:
                self.zoom = next((z for z in reversed(ZOOMS) if z < actual - 0.01), ZOOMS[0])
        self._mostrar()

    def _rueda(self, e):
        if e.state & 0x4:                    # Ctrl + rueda = zoom
            self._zoom(+1 if e.delta > 0 else -1)
        elif self.zoom is None:              # página ajustada: la rueda cambia de página
            self.ir(self.pagina + (1 if e.delta < 0 else -1))
        else:
            self.cv.yview_scroll(-e.delta // 120, "units")

    # ------------------------------------------------------------------ zonas (noticias)
    def _zona_en(self, e):
        if self.doc is None:
            return None
        s = self._escala()
        x = (self.cv.canvasx(e.x) - self.ox) / s
        y = self.mapa.get("alto", 842) - (self.cv.canvasy(e.y) - self.oy) / s
        for z in self.mapa.get("zonas", []):
            if z["pag"] == self.pagina + 1:
                x0, y0, x1, y1 = z["rect"]
                if x0 <= x <= x1 and y0 <= y <= y1:
                    return z
        return None

    def _resaltar(self, z, e=None):
        if z is self._zona_actual and z is not None and e is not None:
            self.tip.place(x=e.x + 16, y=e.y + 14)
            return
        self._zona_actual = z
        self.cv.delete("zona")
        if z is None:
            self.cv.configure(cursor="arrow")
            self.tip.place_forget()
            return
        s = self._escala()
        alto = self.mapa.get("alto", 842)
        x0, y0, x1, y1 = z["rect"]
        self.cv.create_rectangle(self.ox + x0 * s - 3, self.oy + (alto - y1) * s - 3,
                                 self.ox + x1 * s + 3, self.oy + (alto - y0) * s + 3,
                                 outline="#2563EB", width=3, dash=(6, 3), tags="zona")
        self.cv.configure(cursor="hand2")
        tit = self.mapa.get("titulares", {}).get(str(z["gid"]), "")
        lugar = dict(portada.LUGARES).get(z.get("lugar"), "")
        self.tip.configure(text=(f"Portada · {lugar}:  " if lugar else "") + tit[:90] + "   (clic para editar)")
        if e is not None:
            self.tip.place(x=e.x + 16, y=e.y + 14)

    def _mover(self, e):
        self._resaltar(self._zona_en(e), e)

    def _clic(self, e):
        z = self._zona_en(e)
        if z:
            self._editar(z["gid"])

    def _editar(self, gid):
        from .editor import EditorNota
        EditorNota(self, gid, self.app._cfg_actual(), on_save=self._tras_cambio)

    def _tras_cambio(self):
        self.app._refresh_dashboard()
        self.app._aviso("Cambios guardados. Actualizando la vista previa…", "ok")
        self.generar()

    def _menu(self, e):
        z = self._zona_en(e)
        if not z:
            return
        gid = z["gid"]
        tit = self.mapa.get("titulares", {}).get(str(gid), "")[:60]
        m = tk.Menu(self, tearoff=0)
        m.add_command(label=f"✎  Editar «{tit}»", command=lambda: self._editar(gid))
        m.add_command(label="✖  Quitar del periódico", command=lambda: self._quitar(gid))
        sub = tk.Menu(m, tearoff=0)
        for lugar, nombre in portada.LUGARES:
            sub.add_command(label=nombre, command=lambda lg=lugar: self._a_portada(gid, lg))
        m.add_cascade(label="★  Poner en la portada como", menu=sub)
        m.tk_popup(e.x_root, e.y_root)

    def _quitar(self, gid):
        storage.set_en_periodico([gid], False)
        lugares = {k: v for k, v in portada.leer_lugares().items() if v != gid}
        portada.guardar_lugares(lugares)
        self.app._refresh_dashboard()
        self.app._aviso("Noticia quitada del periódico (puedes volver a incluirla en el Dashboard).", "warn")
        self.generar()

    def _a_portada(self, gid, lugar):
        lugares = {k: v for k, v in portada.leer_lugares().items() if v != gid}
        lugares[lugar] = gid
        portada.guardar_lugares(lugares)
        self.app._aviso(f"Noticia puesta en la portada: {dict(portada.LUGARES)[lugar]}.", "ok")
        self.generar()

    # ------------------------------------------------------------------ otros
    def _mensaje(self, texto, error=False):
        if not texto:
            self.aviso.place_forget()
            return
        self.aviso.configure(text=texto, bg="#FDECEC" if error else "#FFF4CC",
                             fg="#B3261E" if error else "#6B4E00")
        self.aviso.place(relx=0.5, y=40, anchor="n")
        self.aviso.lift()

    def _publicar(self):
        if self._generando:
            self.app._aviso("Espera a que termine de actualizarse la vista previa.", "warn")
            return
        self._cerrar()
        self.app._publicar()

    def _cerrar(self):
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        for f in CARPETA.glob("vista_previa_*"):
            try:
                f.unlink()
            except OSError:
                pass
        self.app._ventana_previa = None
        self.destroy()

    def traer(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self.generar()
