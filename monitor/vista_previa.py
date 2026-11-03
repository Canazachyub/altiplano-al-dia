"""Vista previa interactiva del periódico, dentro de la app (la mesa de trabajo editorial).

Muestra las páginas del PDF que se va a publicar. Clic en una noticia: se abre su editor (texto,
foto, titular de portada) y al guardar la vista se actualiza sola. Clic derecho: quitarla del
periódico o ponerla en un lugar de la portada. Desde aquí también se publica.
"""
import json
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

import pypdfium2 as pdfium
from PIL import ImageTk

from . import avisos, consolidado, portada, storage
from .gui_diseno import PanelDiseno
from .config import BASE_DIR

F = "Segoe UI"
NAVY = "#16324F"
CARPETA = BASE_DIR / "data" / "previa"
ZOOMS = [0.6, 0.8, 1.0, 1.25, 1.5, 2.0]


class VistaPrevia(tk.Toplevel):
    def __init__(self, app, edicion=None):
        """edicion: número de un periódico ya publicado para verlo y corregirlo (None = el próximo)."""
        super().__init__(app)
        self.app = app
        self.edicion = edicion
        self.title(f"Periódico N° {edicion} (publicado) — Altiplano al Día" if edicion
                   else "Vista previa del periódico — Altiplano al Día")
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
        # diseño (a la derecha, se muestra con el botón 🎨)
        self.panel_diseno = PanelDiseno(cuerpo, self._tras_diseno)
        self._diseno_visible = False
        # página
        centro = tk.Frame(cuerpo, bg="#E9EDF4")
        self._centro = centro
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
        ttk.Button(b, text=(f"💾 Guardar cambios en el N° {self.edicion}" if self.edicion
                            else "📰 Publicar este periódico"), style="Accent.TButton",
                   command=self._publicar).pack(side="right", padx=(4, 12), pady=6)
        ttk.Button(b, text="Abrir en visor PDF", style="Header.TButton",
                   command=lambda: self.ruta and os.startfile(str(self.ruta))).pack(side="right", padx=4)  # noqa: S606
        ttk.Button(b, text="⟳ Actualizar", style="Header.TButton", command=self.generar).pack(side="right", padx=4)
        ttk.Button(b, text="🎨 Diseño", style="Header.TButton", command=self._toggle_diseno).pack(side="right", padx=4)
        ttk.Button(b, text="📢 Avisos", style="Header.TButton", command=self._gestor_avisos).pack(side="right", padx=4)
        ttk.Button(b, text="Ajustar", style="Header.TButton", command=lambda: self._zoom(None)).pack(side="right", padx=4)
        ttk.Button(b, text="+", width=3, style="Header.TButton", command=lambda: self._zoom(+1)).pack(side="right")
        ttk.Button(b, text="−", width=3, style="Header.TButton", command=lambda: self._zoom(-1)).pack(side="right", padx=4)
        ttk.Button(b, text="▶", width=3, style="Header.TButton", command=lambda: self.ir(self.pagina + 1)).pack(side="right", padx=(4, 12))
        self.lbl_pag = tk.Label(b, text="", bg=NAVY, fg="white", font=(F, 10))
        self.lbl_pag.pack(side="right")
        ttk.Button(b, text="◀", width=3, style="Header.TButton", command=lambda: self.ir(self.pagina - 1)).pack(side="right", padx=4)
        h = tk.Frame(self, bg="#E8F0FE")
        h.pack(fill="x")
        tk.Label(h, text="Clic en una noticia: editar texto, foto y titular.   Clic derecho: quitar, mover a otra "
                         "página o a la portada.   Clic en un espacio libre: poner una noticia o un aviso.   "
                         "🎨 Diseño: textos, colores y letras.",
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
        if not self._grupos():
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
                    out = generar_consolidado(cfg, grupos=self._grupos(), destino=destino, registrar=False,
                                              numero=self.edicion, ahora=self._fecha_edicion())
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
        n = len(self._grupos())
        if self.edicion:
            self.lbl_tit.configure(text=f"Periódico publicado N° {self.edicion} · {n} noticias · puedes corregirlo")
        else:
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
        self.cv.delete("hueco")
        alto = self.mapa.get("alto", 842)
        for z in self.mapa.get("zonas", []):
            if z["pag"] == self.pagina + 1 and z.get("lugar") == "hueco":
                x0, y0, x1, y1 = z["rect"]
                X0, Y0, X1, Y1 = (self.ox + x0 * s, self.oy + (alto - y1) * s, self.ox + x1 * s, self.oy + (alto - y0) * s)
                self.cv.create_rectangle(X0 + 3, Y0 + 3, X1 - 3, Y1 - 3, outline="#16A34A", width=2, dash=(5, 4),
                                         fill="#F0FDF4", stipple="gray25", tags="hueco")
                self.cv.create_text((X0 + X1) / 2, (Y0 + Y1) / 2, text="＋ Añadir noticia o aviso",
                                    fill="#15803D", font=(F, max(8, int(11 * s)), "bold"), tags="hueco",
                                    width=max(40, X1 - X0 - 16), justify="center")
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
        zonas = sorted(self.mapa.get("zonas", []), key=lambda z: z.get("lugar") == "hueco")   # noticias primero
        for z in zonas:
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
        if z.get("lugar") == "hueco":
            self.tip.configure(text="Espacio libre: clic para poner una noticia o un aviso")
        elif z.get("lugar") == "aviso":
            a = avisos.por_id(avisos.cargar(), z.get("aviso"))
            self.tip.configure(text=f"Aviso «{(a or {}).get('nombre', '')}»: clic para cambiarlo o quitarlo")
        elif z.get("lugar") == "datos":
            self.tip.configure(text="Datos clave: clic para editarlos")
        else:
            tit = self.mapa.get("titulares", {}).get(str(z["gid"]), "")
            lugar = dict(portada.LUGARES).get(z.get("lugar"), "")
            self.tip.configure(text=(f"Portada · {lugar}:  " if lugar else "") + tit[:90] + "   (clic para editar)")
        if e is not None:
            self.tip.place(x=e.x + 16, y=e.y + 14)

    def _mover(self, e):
        self._resaltar(self._zona_en(e), e)

    def _clic(self, e):
        z = self._zona_en(e)
        if not z:
            return
        if z.get("lugar") in ("hueco", "aviso"):
            self._menu_espacio(z, e)
        elif z.get("lugar") == "datos":
            from .editor import EditorDatos
            EditorDatos(self, z["gids"], on_save=self._tras_cambio)
        else:
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
        if z.get("lugar") in ("hueco", "aviso"):
            self._menu_espacio(z, e)
            return
        if z.get("lugar") == "datos":
            from .editor import EditorDatos
            EditorDatos(self, z["gids"], on_save=self._tras_cambio)
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
        m.add_command(label="📄  Mover a la página…", command=lambda: self._mover_pagina(gid))
        if gid in consolidado.leer_fijas():
            m.add_command(label="↺  Dejar que se ubique sola", command=lambda: self._soltar(gid))
        m.tk_popup(e.x_root, e.y_root)

    # ------------------------------------------------------------------ qué noticias forman este periódico
    def _grupos(self):
        return storage.grupos_de_consolidado(self.edicion) if self.edicion else storage.grupos_para_pdf()

    def _fecha_edicion(self):
        if not self.edicion:
            return None
        c = storage.get_consolidado(self.edicion)
        try:
            return datetime.fromisoformat(c["fecha"])
        except (TypeError, ValueError):
            return None

    def _quitar(self, gid):
        if self.edicion:
            storage.sacar_de_edicion(gid)
            self.app._refresh_dashboard()
            self.app._aviso(f"Noticia quitada del N° {self.edicion} (queda en pendientes como ☐ No va).", "warn")
            self.generar()
            return
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

    def _mover_pagina(self, gid):
        total = len(self.doc) if self.doc else 30
        n = simpledialog.askinteger("Mover a la página", f"¿En qué página va esta noticia? (2 a {total})",
                                    parent=self, minvalue=2, maxvalue=60)
        if not n:
            return
        fijas = consolidado.leer_fijas()
        fijas[gid] = n
        consolidado.guardar_fijas(fijas)
        self.app._aviso(f"La noticia irá en la página {n} (si entra; si no, en la siguiente con espacio).", "ok")
        self.pagina = max(0, n - 1)
        self.generar()

    def _soltar(self, gid):
        fijas = consolidado.leer_fijas()
        fijas.pop(gid, None)
        consolidado.guardar_fijas(fijas)
        self.generar()

    def anadir(self, gid, pag):
        """Pone una noticia en el periódico, en la página pag (desde un espacio libre)."""
        g = storage.get_grupo(gid)
        if self.edicion:
            storage.poner_en_edicion(gid, self.edicion)             # entra en el periódico ya publicado
        elif g["estado_pdf"] not in ("nuevo", "actualizacion"):
            storage.actualizar_grupo(gid, estado_pdf="nuevo")      # ya publicada: vuelve a salir
        if not self.edicion:
            storage.set_en_periodico([gid], True)
        fijas = consolidado.leer_fijas()
        fijas[gid] = pag
        consolidado.guardar_fijas(fijas)
        self.app._refresh_dashboard()
        self.app._aviso(f"Noticia añadida en la página {pag}.", "ok")
        self.pagina = max(0, pag - 1)
        self.generar()

    # ------------------------------------------------------------------ avisos
    def _menu_espacio(self, z, e):
        d = avisos.cargar()
        clave = z.get("clave", "")
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="📰  Poner una noticia aquí", command=lambda: ElegirNoticia(self, z["pag"]))
        sub = tk.Menu(m, tearoff=0)
        for a in d["avisos"]:
            ok = avisos.listo(a)
            sub.add_command(label=("" if ok else "⚠ ") + (a.get("nombre") or a.get("titulo") or "Aviso")
                            + ("" if ok else "  (falta teléfono o imagen)"),
                            command=lambda aid=a["id"]: self._poner_aviso(clave, aid))
        sub.add_separator()
        sub.add_command(label="Crear o editar avisos…", command=self._gestor_avisos)
        m.add_cascade(label="📢  Poner un aviso aquí", menu=sub)
        if z.get("lugar") == "aviso":
            m.add_command(label="✖  Quitar el aviso (dejar en blanco)", command=lambda: self._poner_aviso(clave, ""))
        if clave in d["colocados"]:
            m.add_command(label="↺  Automático (lo decide el programa)", command=lambda: self._poner_aviso(clave, None))
        m.tk_popup(e.x_root, e.y_root)

    def _poner_aviso(self, clave, aid):
        d = avisos.cargar()
        if aid is None:
            d["colocados"].pop(clave, None)
        else:
            a = avisos.por_id(d, aid) if aid else None
            if aid and not avisos.listo(a):
                messagebox.showinfo("Avisos", "A ese aviso le falta el teléfono (o la imagen). Complétalo en "
                                              "📢 Avisos y vuelve a intentarlo.", parent=self)
                self._gestor_avisos()
                return
            d["colocados"][clave] = aid
        avisos.guardar(d)
        self.generar()

    def _gestor_avisos(self):
        from .gui_avisos import GestorAvisos
        GestorAvisos(self, al_guardar=lambda: (self.app._aviso("Avisos guardados. Actualizando…", "ok"),
                                               self.generar()))

    def _toggle_diseno(self):
        if self._diseno_visible:
            self.panel_diseno.pack_forget()
        else:
            self.panel_diseno.pack(side="right", fill="y", before=self._centro)
        self._diseno_visible = not self._diseno_visible
        self.after(80, self._mostrar)

    def _tras_diseno(self, msg):
        self.app._aviso(msg + " Actualizando la vista previa…", "ok")
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
        if self.edicion:
            self._guardar_edicion()
            return
        self._cerrar()
        self.app._publicar()

    def _guardar_edicion(self):
        """Rehace el PDF del periódico publicado con los cambios, con el mismo número."""
        from pathlib import Path
        c = storage.get_consolidado(self.edicion)
        if not messagebox.askyesno("Guardar cambios",
                                   f"Se rehará el periódico N° {self.edicion} con los cambios (mismo número y fecha)."
                                   "\n\n¿Continuar?", parent=self):
            return
        destino = Path(c["path"])
        try:                                    # si el PDF está abierto en otro programa, se guarda una versión nueva
            if destino.exists():
                destino.open("ab").close()
        except OSError:
            destino = destino.with_name(destino.stem + f"_corregido_{datetime.now():%H%M%S}.pdf")
        grupos = self._grupos()
        out = consolidado.generar_consolidado(self.app._cfg_actual(), grupos=grupos, destino=destino,
                                              registrar=False, numero=self.edicion, ahora=self._fecha_edicion())
        storage.actualizar_consolidado(self.edicion, out, len(grupos))
        self.app._aviso(f"Periódico N° {self.edicion} actualizado: {out}", "ok", 7000)
        os.startfile(str(out))  # noqa: S606

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


class ElegirNoticia(tk.Toplevel):
    """Lista de noticias para llenar un espacio libre de la página."""

    FILTROS = ["Que no van (☐ No va)", "Ya publicadas", "Todas"]

    def __init__(self, vista, pag):
        super().__init__(vista)
        self.vista, self.pag = vista, pag
        self.title(f"Añadir una noticia en la página {pag}")
        self.geometry("900x560")
        self.transient(vista)
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Mostrar:").pack(side="left")
        self.filtro = ttk.Combobox(top, state="readonly", values=self.FILTROS, width=24)
        self.filtro.set(self.FILTROS[0])
        self.filtro.pack(side="left", padx=(4, 12))
        self.filtro.bind("<<ComboboxSelected>>", lambda e: self._cargar())
        ttk.Label(top, text="Buscar:").pack(side="left")
        self.q = ttk.Entry(top, width=32)
        self.q.pack(side="left", padx=4)
        self.q.bind("<KeyRelease>", lambda e: self._cargar())
        cols = ("fecha", "titular", "medios", "estado")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("fecha", "Fecha", 90), ("titular", "Titular", 470), ("medios", "Medios", 170),
                        ("estado", "Estado", 110)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=(c == "titular"))
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        pie = ttk.Frame(self, padding=10)
        pie.pack(side="bottom", fill="x")
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=(10, 0))
        self.tree.bind("<Double-1>", lambda e: self._ok())
        ttk.Button(pie, text=f"＋ Añadir en la página {pag}", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(pie, text="Cancelar", command=self.destroy).pack(side="right", padx=6)
        ttk.Label(pie, text="Doble clic para añadir. Si no entra en el espacio, pasa a la página siguiente.",
                  foreground="#6B7280").pack(side="left")
        self._cargar()
        self.grab_set()

    def _cargar(self):
        f = self.filtro.get()
        q = self.q.get().strip().lower()
        with storage._conn() as c:
            filas = c.execute("SELECT * FROM grupos WHERE nota_json IS NOT NULL AND estado_pdf != 'descartado' "
                              "ORDER BY COALESCE(fecha_hecho, actualizado) DESC").fetchall()
        self.tree.delete(*self.tree.get_children())
        for g in filas:
            pendiente = g["estado_pdf"] in ("nuevo", "actualizacion")
            va = (g["en_periodico"] if g["en_periodico"] is not None else 1) == 1
            if self.vista.edicion:
                if not pendiente and g["ultimo_pdf"] == self.vista.edicion:
                    continue                               # ya está en este periódico publicado
            elif pendiente and va:
                continue                                   # ya está en este periódico
            if f == self.FILTROS[0] and not (pendiente and not va):
                continue
            if f == self.FILTROS[1] and pendiente:
                continue
            if q and q not in (g["titular"] or "").lower():
                continue
            estado = "No va" if pendiente else f"Publicada N° {g['ultimo_pdf']}"
            fecha = (g["fecha_hecho"] or g["actualizado"] or "")[:16].replace("T", " ")
            self.tree.insert("", "end", iid=str(g["id"]), values=(fecha[5:], g["titular"], g["medios"], estado))
        if not self.tree.get_children():
            self.tree.insert("", "end", iid="vacio", values=("", "No hay noticias con este filtro. Prueba «Todas».",
                                                             "", ""))

    def _ok(self):
        s = self.tree.selection()
        if not s or not s[0].isdigit():
            return
        gid = int(s[0])
        g = storage.get_grupo(gid)
        if g["estado_pdf"] not in ("nuevo", "actualizacion") and not messagebox.askyesno(
                "Noticia ya publicada", f"Esta noticia ya salió en el N° {g['ultimo_pdf']}. "
                                        "¿Publicarla otra vez en este periódico?", parent=self):
            return
        self.destroy()
        self.vista.anadir(gid, self.pag)
