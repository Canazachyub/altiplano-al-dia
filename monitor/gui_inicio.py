"""Pestaña Inicio: el flujo de trabajo en 4 pasos, con su estado y el siguiente paso sugerido,
y una lista de comprobación "Antes de empezar" (keys, sesión de Facebook, páginas)."""
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import portada, storage
from .config import PROFILE_DIR, get_api_key

F = "Segoe UI"
BG = "#F3F5F9"
PANEL = "#FFFFFF"
NAVY = "#16324F"
ACCENT = "#2563EB"
MUTED = "#6B7280"
OK = "#1E7B3A"
MAL = "#B3261E"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


class Tip:
    """Ayuda que aparece al pasar el mouse sobre un control."""

    def __init__(self, widget, texto):
        self.w, self.texto, self.top = widget, texto, None
        widget.bind("<Enter>", self._mostrar, add="+")
        widget.bind("<Leave>", self._ocultar, add="+")
        widget.bind("<ButtonPress>", self._ocultar, add="+")

    def _mostrar(self, _e=None):
        if self.top or not self.texto:
            return
        x = self.w.winfo_rootx() + 10
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.top = tk.Toplevel(self.w)
        self.top.wm_overrideredirect(True)
        self.top.attributes("-topmost", True)
        tk.Label(self.top, text=self.texto, bg="#1F2937", fg="white", font=(F, 9), padx=10, pady=6,
                 justify="left", wraplength=340).pack()
        self.top.geometry(f"+{x}+{y}")

    def _ocultar(self, _e=None):
        if self.top:
            self.top.destroy()
            self.top = None


def sesion_facebook_guardada() -> bool:
    return any((PROFILE_DIR / "Default" / sub / "Cookies").exists() for sub in ("Network", ""))


class Paso(tk.Frame):
    def __init__(self, master, numero, titulo, descripcion):
        super().__init__(master, bg=PANEL, highlightthickness=2, highlightbackground="#D9DEE7")
        cab = tk.Frame(self, bg=PANEL)
        cab.pack(fill="x", padx=16, pady=(14, 0))
        self.circulo = tk.Label(cab, text=str(numero), bg="#E4E8F0", fg=NAVY, font=(F, 15, "bold"), width=2)
        self.circulo.pack(side="left")
        tk.Label(cab, text=titulo, bg=PANEL, fg=NAVY, font=(F, 13, "bold")).pack(side="left", padx=10)
        self.sugerido = tk.Label(cab, text="", bg=PANEL, fg=ACCENT, font=(F, 9, "bold"))
        self.sugerido.pack(side="right")
        tk.Label(self, text=descripcion, bg=PANEL, fg=MUTED, font=(F, 9), wraplength=260, justify="left").pack(
            anchor="w", padx=16, pady=(6, 0))
        self.estado = tk.Label(self, text="", bg=PANEL, fg="#111827", font=(F, 11, "bold"), wraplength=260,
                               justify="left")
        self.estado.pack(anchor="w", padx=16, pady=(12, 2))
        self.detalle = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=(F, 9), wraplength=260, justify="left")
        self.detalle.pack(anchor="w", padx=16)
        self.contenido = tk.Frame(self, bg=PANEL)
        self.contenido.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        self.botones = tk.Frame(self, bg=PANEL)
        self.botones.pack(side="bottom", fill="x", padx=16, pady=14)

    def boton(self, texto, cmd, principal=False, ayuda=""):
        b = ttk.Button(self.botones, text=texto, command=cmd, style="Accent.TButton" if principal else "TButton")
        b.pack(fill="x", pady=2)
        if ayuda:
            Tip(b, ayuda)
        return b

    def lista(self, items, vacio=""):
        """items: [(texto, color)]"""
        for w in self.contenido.winfo_children():
            w.destroy()
        if not items and vacio:
            tk.Label(self.contenido, text=vacio, bg=PANEL, fg=MUTED, font=(F, 9, "italic")).pack(anchor="w")
        for texto, color in items:
            tk.Label(self.contenido, text=texto, bg=PANEL, fg=color, font=(F, 9), anchor="w", justify="left",
                     wraplength=270).pack(anchor="w", fill="x", pady=1)

    def resaltar(self, si):
        self.configure(highlightbackground=ACCENT if si else "#D9DEE7")
        self.circulo.configure(bg=ACCENT if si else "#E4E8F0", fg="white" if si else NAVY)
        self.sugerido.configure(text="👉 Siguiente paso" if si else "")


class InicioTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=(20, 14))
        self.app = app
        self.saludo = tk.Label(self, text="", bg=BG, fg=NAVY, font=(F, 18, "bold"))
        self.saludo.pack(anchor="w")
        tk.Label(self, text="Así se arma el periódico: sigue los pasos de izquierda a derecha. "
                            "Pasa el mouse sobre cualquier botón para ver qué hace.",
                 bg=BG, fg=MUTED, font=(F, 10)).pack(anchor="w", pady=(2, 12))

        # ---------------- antes de empezar
        self.check = tk.Frame(self, bg="#FFF8E6", highlightthickness=1, highlightbackground="#F2D27A")
        self.check_items = tk.Frame(self.check, bg="#FFF8E6")

        # ---------------- los 4 pasos
        fila = tk.Frame(self, bg=BG)
        fila.pack(fill="both", expand=True, pady=(8, 0))
        self.pasos = []
        datos = [
            ("Buscar noticias", "El monitor lee las páginas de Facebook, redacta cada noticia con IA y "
                                "agrupa las que hablan del mismo hecho."),
            ("Elegir cuáles van", "Marca ☑ las noticias que irán en el periódico y corrige texto o foto "
                                  "si hace falta."),
            ("Revisar el periódico", "Mira el periódico tal como saldrá. Haz clic en una noticia para "
                                     "corregirla o clic derecho para moverla a la portada."),
            ("Publicar", "Genera el PDF final. Esas noticias quedan como publicadas y no se repiten."),
        ]
        for i, (t, d) in enumerate(datos):
            p = Paso(fila, i + 1, t, d)
            p.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 12, 0))
            fila.columnconfigure(i, weight=1, uniform="pasos")
            self.pasos.append(p)
        fila.rowconfigure(0, weight=1)

        p1, p2, p3, p4 = self.pasos
        self.b_revisar = p1.boton("▶  Revisar ahora", app.check_now, True,
                                  "Busca noticias nuevas en este momento (atajo: F5).")
        self.b_auto = p1.boton("", app._toggle_auto, False,
                               "Prende o apaga la revisión automática cada cierto tiempo.")
        p2.boton("Elegir noticias  →", self._ir_noticias, True,
                 "Abre la lista de noticias pendientes: clic en ☑/☐ para incluir o sacar, doble clic para editar.")
        p3.boton("👁  Ver el periódico", app._vista_previa, True,
                 "Abre la vista previa: puedes corregir cualquier noticia haciendo clic sobre ella.")
        p3.boton("Armar la portada  →", self._ir_portada, False,
                 "Elige qué noticia va en cada lugar de la portada, su foto y su titular.")
        p4.boton("📰  Publicar periódico", app._publicar, True,
                 "Genera el PDF final y marca esas noticias como publicadas.")
        p4.boton("Abrir el último publicado", app._open_last_consolidado, False, "Abre el último PDF generado.")
        self.refrescar()

    # ------------------------------------------------------------------
    def _ir_noticias(self):
        self.app.d_mostrar.set("Pendientes de publicar")
        self.app.d_periodo.set("Todo")
        self.app.nb.select(self.app.tab_dash)
        self.app._refresh_dashboard()

    def _ir_portada(self):
        self.app.nb.select(self.app.portada_tab)

    def _ir_config(self):
        self.app.nb.select(self.app.tab_config)

    def _problemas(self):
        cfg = self.app.cfg
        out = []
        if not get_api_key("claude"):
            out.append(("Falta la API key de Claude (agrupa noticias y arma la portada).", "Pegar key", self._ir_config))
        prov = cfg.get("proveedor_ia", "deepseek")
        if prov != "claude" and not get_api_key(prov):
            out.append((f"Falta la API key de {prov.title()} (redacta cada noticia).", "Pegar key", self._ir_config))
        if not sesion_facebook_guardada():
            out.append(("No hay sesión de Facebook guardada.", "Iniciar sesión", self.app._login))
        if not any(p.get("activa", True) for p in cfg["paginas"]):
            out.append(("No hay páginas activas para revisar.", "Elegir páginas", self._ir_config))
        return out

    def refrescar(self):
        if not self.winfo_exists():
            return
        ahora = datetime.now()
        self.saludo.configure(text=f"Hola · {DIAS[ahora.weekday()]} {ahora.day} de {MESES[ahora.month - 1]}")

        # antes de empezar
        problemas = self._problemas()
        for w in self.check_items.winfo_children():
            w.destroy()
        for w in self.check.winfo_children():
            if w is not self.check_items:
                w.destroy()
        if problemas:
            tk.Label(self.check, text="Antes de empezar", bg="#FFF8E6", fg="#6B4E00",
                     font=(F, 11, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
            self.check_items.pack(fill="x", padx=14, pady=(0, 10))
            for texto, boton, cmd in problemas:
                f = tk.Frame(self.check_items, bg="#FFF8E6")
                f.pack(fill="x", pady=2)
                tk.Label(f, text="✖  " + texto, bg="#FFF8E6", fg=MAL, font=(F, 10)).pack(side="left")
                ttk.Button(f, text=boton, command=cmd).pack(side="left", padx=10)
            self.check.pack(fill="x", pady=(0, 8), before=self.pasos[0].master)
        else:
            self.check.pack_forget()

        app, cfg = self.app, self.app.cfg
        p1, p2, p3, p4 = self.pasos
        # paso 1
        if app.running:
            p1.estado.configure(text="⏳ Revisando ahora…", fg="#8A5300")
        else:
            ult = app.last_check.strftime("%H:%M") if app.last_check else None
            if not ult:
                with storage._conn() as c:
                    r = c.execute("SELECT MAX(detectado) FROM posts").fetchone()[0]
                if r:
                    d = datetime.fromisoformat(r)
                    ult = d.strftime("%H:%M") if d.date() == ahora.date() else d.strftime("%d/%m %H:%M")
            p1.estado.configure(text=f"Última noticia leída: {ult or 'todavía ninguna'}", fg="#111827")
        activas = sum(1 for p in cfg["paginas"] if p.get("activa", True))
        if cfg.get("revision_automatica", True):
            prox = app.next_check.strftime("%H:%M") if app.next_check else "—"
            p1.detalle.configure(text=f"Automática cada {cfg['intervalo_min']} min · próxima {prox} · "
                                      f"{activas} páginas")
            self.b_auto.configure(text="⏸  Pausar la automática")
        else:
            p1.detalle.configure(text=f"Automática en pausa: solo cuando pulses «Revisar ahora» · {activas} páginas")
            self.b_auto.configure(text="▶  Activar la automática")
        self.b_revisar.state(["disabled"] if app.running else ["!disabled"])
        from .config import nombre_medio, page_slug
        p1.lista([(("●  " if pg.get("activa", True) else "○  ") + nombre_medio(page_slug(pg["url"]), cfg),
                   OK if pg.get("activa", True) else MUTED) for pg in cfg["paginas"]])

        # paso 2
        with storage._conn() as c:
            pendientes = c.execute("SELECT COUNT(*) FROM grupos WHERE estado_pdf IN ('nuevo','actualizacion') "
                                   "AND nota_json IS NOT NULL").fetchone()[0]
        van = len(storage.grupos_para_pdf())
        if pendientes:
            p2.estado.configure(text=f"{van} de {pendientes} noticias van al periódico",
                                fg=OK if van else MAL)
            p2.detalle.configure(text="Las que no quieras publicar, márcalas ☐ No va." if van
                                 else "Todas están marcadas ☐ No va: marca al menos una.")
        else:
            p2.estado.configure(text="No hay noticias nuevas todavía", fg=MUTED)
            p2.detalle.configure(text="Llegan en cada revisión (paso 1).")
        with storage._conn() as c:
            ult_pend = c.execute("SELECT titular, COALESCE(en_periodico,1) AS va FROM grupos "
                                 "WHERE estado_pdf IN ('nuevo','actualizacion') AND nota_json IS NOT NULL "
                                 "ORDER BY COALESCE(fecha_hecho, actualizado) DESC LIMIT 9").fetchall()
        p2.lista([(("☑  " if r["va"] else "☐  ") + (r["titular"] or "")[:70], "#111827" if r["va"] else MUTED)
                  for r in ult_pend] + ([("…y más", MUTED)] if pendientes > 9 else []))

        # paso 3
        fijados = len(portada.leer_lugares())
        p3.estado.configure(text=f"{van} noticias listas para revisar" if van else "Nada que revisar todavía",
                            fg="#111827" if van else MUTED)
        p3.detalle.configure(text=(f"Portada: {fijados} lugar(es) elegidos a mano, el resto automático."
                                   if fijados else "Portada automática (por importancia y foto)."))
        self._miniatura_portada(p3)

        # paso 4
        ult = storage.ultimo_consolidado()
        p4.estado.configure(text=f"Próximo: boletín N° {storage.siguiente_consolidado()}",
                            fg="#111827" if van else MUTED)
        if ult:
            try:
                f = datetime.fromisoformat(ult["fecha"]).strftime("%d/%m %H:%M")
            except (TypeError, ValueError):
                f = ult["fecha"]
            p4.detalle.configure(text=f"Último publicado: N° {ult['n']} el {f} ({ult['n_noticias']} noticias)")
        else:
            p4.detalle.configure(text="Todavía no se publicó ningún periódico.")

        p4.lista([], "")
        # siguiente paso sugerido
        if problemas or app.running or not pendientes:
            sug = 0
        elif not van:
            sug = 1
        else:
            sug = 2
        for i, p in enumerate(self.pasos):
            p.resaltar(i == sug)


    def _miniatura_portada(self, paso):
        """Miniatura de la portada: la de la última vista previa o, si no hay, la del último publicado."""
        from pathlib import Path
        from .config import BASE_DIR
        cands = sorted((BASE_DIR / "data" / "previa").glob("vista_previa_*.pdf"), reverse=True)
        ult = storage.ultimo_consolidado()
        ruta, rotulo = (cands[0], "Última vista previa") if cands else (
            (Path(ult["path"]), f"Portada del N° {ult['n']}") if ult and Path(ult["path"]).exists() else (None, ""))
        for w in paso.contenido.winfo_children():
            w.destroy()
        if not ruta:
            tk.Label(paso.contenido, text="(la portada aparecerá aquí)", bg=PANEL, fg=MUTED,
                     font=(F, 9, "italic")).pack(anchor="w")
            return
        try:
            import pypdfium2 as pdfium
            from PIL import ImageTk
            doc = pdfium.PdfDocument(str(ruta))
            im = doc[0].render(scale=0.42).to_pil()
            doc.close()
            self._mini = ImageTk.PhotoImage(im)
        except Exception:  # noqa: BLE001
            return
        tk.Label(paso.contenido, text=rotulo, bg=PANEL, fg=MUTED, font=(F, 8)).pack(anchor="w")
        lbl = tk.Label(paso.contenido, image=self._mini, bg=PANEL, cursor="hand2", bd=1, relief="solid")
        lbl.pack(anchor="w", pady=(2, 0))
        lbl.bind("<Button-1>", lambda e: self.app._vista_previa())
        Tip(lbl, "Clic para abrir la vista previa del periódico.")
