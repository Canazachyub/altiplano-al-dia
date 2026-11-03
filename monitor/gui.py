"""Interfaz Tkinter: Bandeja (Fase 1), Dashboard (Fase 2) y Configuración."""
import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

from . import storage
from .analisis import ciclo_fase2, fase2_lock
from .config import (BASE_DIR, EXCEL_PATH, PROFILE_DIR, get_api_key, load_config, nombre_medio,
                     normalize_page_url, page_slug, save_config, set_api_key)
from .editor import EditorNota
from .gui_portada import PortadaTab
from .proceso import cerrar_navegador_monitor
from .scraper import interactive_login
from .worker import browser_lock, detener, procesar_post, revisar

try:
    from plyer import notification
except Exception:  # noqa: BLE001
    notification = None

# ------------------------------------------------------------------ paleta
BG = "#F3F5F9"
PANEL = "#FFFFFF"
NAVY = "#16324F"
ACCENT = "#2563EB"
MUTED = "#6B7280"
BORDER = "#D9DEE7"
NEW = "#FFF3B0"
MULTI = "#E3EDFF"
ERR = "#B3261E"
AMBITO_COLOR = {"TODOS": "#334155", "DISTRITAL": "#2E7D32", "LOCAL": "#00838F", "REGIONAL": "#1565C0",
                "NACIONAL": "#6A1B9A", "INTERNACIONAL": "#AD1457"}
F = "Segoe UI"


def _sin_marcas(cuerpo) -> str:
    """Intertítulos '## X' -> 'X' en mayúsculas, para el visor de texto."""
    import re as _re
    return _re.sub(r"(?m)^## (.+)$", lambda m: m.group(1).upper(), cuerpo or "")


def _fecha_corta(iso):
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%d/%m %H:%M")


class RichText(tk.Text):
    """Text de solo lectura con estilos y enlaces clicables."""

    def __init__(self, master, **kw):
        super().__init__(master, wrap="word", font=(F, 10), padx=14, pady=12, relief="flat",
                         bg=PANEL, highlightthickness=1, highlightbackground=BORDER, cursor="arrow", **kw)
        self.tag_configure("h1", font=(F, 15, "bold"), foreground="#111827", spacing3=4)
        self.tag_configure("baj", font=(F, 11), foreground="#374151", spacing3=6)
        self.tag_configure("h2", font=(F, 9, "bold"), foreground=ACCENT, spacing1=10, spacing3=3)
        self.tag_configure("meta", font=(F, 9), foreground=MUTED)
        self.tag_configure("warn", background="#FFF4E5", foreground="#7A4B00", lmargin1=6, lmargin2=6)
        self.tag_configure("badge_upd", background=ERR, foreground="white", font=(F, 8, "bold"))
        self.tag_configure("badge_multi", background=ACCENT, foreground="white", font=(F, 8, "bold"))
        self.tag_configure("src", font=(F, 9, "bold"), foreground=NAVY, spacing1=8)
        self.tag_configure("orig", font=(F, 9), foreground="#1F2937", lmargin1=8, lmargin2=8)
        self._n = 0
        self.configure(state="disabled")

    def clear(self):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self._n = 0

    def add(self, text, *tags):
        self.insert("end", text, tags)

    def link(self, label, url):
        if not url:
            return
        self._n += 1
        tag = f"lnk{self._n}"
        self.tag_configure(tag, foreground=ACCENT, underline=True)
        self.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
        self.tag_bind(tag, "<Enter>", lambda e: self.configure(cursor="hand2"))
        self.tag_bind(tag, "<Leave>", lambda e: self.configure(cursor="arrow"))
        self.insert("end", label, (tag,))

    def done(self):
        self.configure(state="disabled")
        self.yview_moveto(0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Altiplano al Día — Monitor de noticias")
        self.geometry("1360x820")
        self.minsize(1100, 680)
        self.configure(bg=BG)
        storage.init_db()
        self.cfg = load_config()
        self.q = queue.Queue()
        self.running = False
        self.next_check = None
        self.last_check = None
        self.last_error = ""
        self.nuevos_ids = set()
        self.grupos_nuevos = set()
        self.amb_sel = "TODOS"

        self._style()
        self._build_header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self._build_bandeja()
        self._build_dashboard()
        self.portada_tab = PortadaTab(self.nb, self)
        self.nb.add(self.portada_tab, text="  Portada  ")
        self._build_config()
        self._build_statusbar()
        self.nb.bind("<<NotebookTabChanged>>", self._tab_cambiada)

        self._load_pages()
        self._load_posts()
        self._refresh_dashboard()
        self._schedule(initial=True)
        self.after(400, self._poll_queue)
        self.after(1000, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self._cerrando = False

    # ================================================================== cierre
    def _cerrar(self):
        """Al cerrar la ventana: corta la revisión, cierra el navegador del monitor y termina el proceso."""
        if self._cerrando:
            return
        if self.running or fase2_lock.locked():
            if not messagebox.askyesno("Cerrar", "Hay una revisión en curso. Se detendrá y se cerrará el "
                                                 "navegador del monitor. ¿Cerrar la aplicación?"):
                return
        self._cerrando = True
        detener.set()
        self.next_check = None
        self.title("Cerrando…")
        self._set_busy(True, "Cerrando: deteniendo la revisión y el navegador…")
        self._esperar_cierre(0)

    def _esperar_cierre(self, intentos):
        # hasta ~8 s para que el hilo de revisión cierre el navegador por sí mismo
        if (browser_lock.locked() or fase2_lock.locked()) and intentos < 16:
            self.after(500, lambda: self._esperar_cierre(intentos + 1))
            return
        cerrar_navegador_monitor()          # por si quedó alguno abierto
        try:
            self.destroy()
        finally:
            os._exit(0)                      # termina también los hilos que sigan vivos

    # ================================================================== estilo
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".", font=(F, 10), background=BG)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG)
        s.configure("Muted.TLabel", foreground=MUTED, background=BG)
        s.configure("TLabelframe", background=BG, bordercolor=BORDER)
        s.configure("TLabelframe.Label", background=BG, foreground=NAVY, font=(F, 10, "bold"))
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        s.configure("TNotebook.Tab", padding=(18, 7), font=(F, 10, "bold"), background="#E4E8F0",
                    foreground=MUTED, borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", NAVY)])
        s.configure("Inner.TNotebook.Tab", padding=(12, 4), font=(F, 9, "bold"))
        s.configure("Treeview", rowheight=28, font=(F, 10), background=PANEL, fieldbackground=PANEL,
                    bordercolor=BORDER)
        s.configure("Treeview.Heading", font=(F, 9, "bold"), background="#E9EDF4", foreground=NAVY,
                    relief="flat", padding=(6, 5))
        s.map("Treeview", background=[("selected", "#C7DBFF")], foreground=[("selected", "#0B1B33")])
        s.configure("TButton", padding=(10, 5))
        s.configure("Accent.TButton", background=ACCENT, foreground="white", font=(F, 10, "bold"),
                    bordercolor=ACCENT, padding=(14, 6))
        s.map("Accent.TButton", background=[("disabled", "#93A9D6"), ("active", "#1D4ED8")])
        s.configure("Header.TButton", background="#24476E", foreground="white", bordercolor="#24476E",
                    padding=(12, 6))
        s.map("Header.TButton", background=[("active", "#2D5888"), ("disabled", "#3A5272")])
        s.configure("Danger.TButton", background="#C62828", foreground="white", bordercolor="#C62828",
                    font=(F, 10, "bold"), padding=(12, 6))
        s.map("Danger.TButton", background=[("active", "#A61B1B"), ("disabled", "#D98A8A")])
        s.configure("TCheckbutton", background=BG)
        s.configure("TRadiobutton", background=BG)

    # ================================================================== cabecera
    def _build_header(self):
        h = tk.Frame(self, bg=NAVY, height=64)
        h.pack(fill="x")
        left = tk.Frame(h, bg=NAVY)
        left.pack(side="left", padx=16, pady=8)
        tk.Label(left, text="ALTIPLANO AL DÍA", bg=NAVY, fg="white", font=(F, 17, "bold")).pack(anchor="w")
        tk.Label(left, text="Monitor de noticias de Facebook · Puno", bg=NAVY, fg="#9FB3CC",
                 font=(F, 9)).pack(anchor="w")
        right = tk.Frame(h, bg=NAVY)
        right.pack(side="right", padx=14)
        self.chip = tk.Label(right, text="● En espera", bg=NAVY, fg="#9FE2B0", font=(F, 9, "bold"))
        self.chip.pack(side="left", padx=(0, 14))
        self.btn_fase2 = ttk.Button(right, text="⟳ Analizar y cruzar", style="Header.TButton",
                                    command=lambda: self._run_fase2(False))
        self.btn_fase2.pack(side="left", padx=4)
        self.btn_auto = ttk.Button(right, text="", style="Header.TButton", command=self._toggle_auto)
        self.btn_auto.pack(side="left", padx=4)
        self.btn_now = ttk.Button(right, text="▶  Revisar ahora", style="Accent.TButton", command=self.check_now)
        self.btn_now.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(right, text="■  Detener", style="Danger.TButton", command=self._detener)

        # franja visible mientras hay una operación en curso
        self.busy_bar = tk.Frame(self, bg="#FFF4CC", highlightthickness=1, highlightbackground="#F2D27A")
        self.busy_txt = tk.Label(self.busy_bar, text="", bg="#FFF4CC", fg="#6B4E00", font=(F, 10, "bold"))
        self.busy_txt.pack(side="left", padx=(14, 10), pady=6)
        self.busy_prog = ttk.Progressbar(self.busy_bar, mode="indeterminate", length=220)
        self.busy_prog.pack(side="left", pady=6)
        ttk.Button(self.busy_bar, text="■ Detener", style="Danger.TButton",
                   command=self._detener).pack(side="right", padx=10, pady=4)
        tk.Label(self.busy_bar, text="Puedes seguir revisando y editando noticias mientras tanto; "
                                     "la vista previa y el periódico esperan a que termine.",
                 bg="#FFF4CC", fg="#6B4E00", font=(F, 9)).pack(side="left", padx=12)
        self._toast = None

    def _build_statusbar(self):
        bar = tk.Frame(self, bg="#E4E8F0", height=26)
        bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value="Listo.")
        self.sched = tk.StringVar()
        tk.Label(bar, textvariable=self.status, bg="#E4E8F0", fg="#1F2937", anchor="w",
                 font=(F, 9)).pack(side="left", fill="x", expand=True, padx=10, pady=3)
        tk.Label(bar, textvariable=self.sched, bg="#E4E8F0", fg=MUTED, font=(F, 9)).pack(side="right", padx=10)

    # ================================================================== pestaña Bandeja
    def _build_bandeja(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  Bandeja  ")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Buscar:").pack(side="left")
        self.q_text = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.q_text, width=32)
        e.pack(side="left", padx=(4, 12))
        self.q_text.trace_add("write", lambda *a: self._load_posts())
        ttk.Label(bar, text="Medio:").pack(side="left")
        self.q_medio = ttk.Combobox(bar, state="readonly", width=22)
        self.q_medio.pack(side="left", padx=(4, 12))
        self.q_medio.bind("<<ComboboxSelected>>", lambda e: self._load_posts())
        ttk.Label(bar, text="Estado:").pack(side="left")
        self.q_estado = ttk.Combobox(bar, state="readonly", width=12,
                                     values=["Todos", "procesado", "pendiente", "error"])
        self.q_estado.set("Todos")
        self.q_estado.pack(side="left", padx=4)
        self.q_estado.bind("<<ComboboxSelected>>", lambda e: self._load_posts())
        self.lbl_bandeja = ttk.Label(bar, style="Muted.TLabel")
        self.lbl_bandeja.pack(side="right")

        pw = ttk.PanedWindow(tab, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        pw.add(left, weight=3)
        cols = ("pagina", "fecha", "titular", "estado")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("pagina", "Medio", 140), ("fecha", "Fecha", 95), ("titular", "Titular", 460),
                        ("estado", "Estado", 85)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=(c == "titular"), anchor="w")
        self.tree.tag_configure("nuevo", background=NEW, font=(F, 10, "bold"))
        self.tree.tag_configure("error", foreground=ERR)
        self.tree.tag_configure("odd", background="#F8FAFD")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_post)
        self.tree.bind("<Double-1>", lambda e: self._open_pdf())

        right = ttk.Frame(pw, padding=(10, 0, 0, 0))
        pw.add(right, weight=2)
        self.detail = RichText(right)
        self.detail.pack(fill="both", expand=True)
        db = ttk.Frame(right)
        db.pack(fill="x", pady=(8, 0))
        ttk.Button(db, text="Abrir en Facebook", command=lambda: self._open_link("permalink")).pack(side="left")
        ttk.Button(db, text="Nota externa", command=lambda: self._open_link("enlace_ext")).pack(side="left", padx=4)
        ttk.Button(db, text="Abrir PDF", command=self._open_pdf).pack(side="left")
        ttk.Button(db, text="Reprocesar IA", command=self._reprocess).pack(side="right")

    # ================================================================== pestaña Dashboard
    def _build_dashboard(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  Dashboard  ")

        # tarjetas por ámbito
        self.cards_frame = ttk.Frame(tab)
        self.cards_frame.pack(fill="x")
        self.cards = {}
        for i, amb in enumerate(["TODOS"] + self.cfg["ambitos"]):
            card = tk.Frame(self.cards_frame, bg=PANEL, highlightthickness=1, highlightbackground=BORDER,
                            cursor="hand2")
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            self.cards_frame.columnconfigure(i, weight=1)
            stripe = tk.Frame(card, bg=AMBITO_COLOR.get(amb, MUTED), height=4)
            stripe.pack(fill="x")
            num = tk.Label(card, text="0", bg=PANEL, fg=AMBITO_COLOR.get(amb, MUTED), font=(F, 22, "bold"))
            num.pack(anchor="w", padx=12, pady=(4, 0))
            lab = tk.Label(card, text=amb.title(), bg=PANEL, fg="#374151", font=(F, 9, "bold"))
            lab.pack(anchor="w", padx=12)
            sub = tk.Label(card, text="", bg=PANEL, fg=MUTED, font=(F, 8))
            sub.pack(anchor="w", padx=12, pady=(0, 8))
            for w in (card, num, lab, sub):
                w.bind("<Button-1>", lambda e, a=amb: self._select_ambito(a))
            self.cards[amb] = (card, num, lab, sub)

        # filtros y acciones
        fb = ttk.Frame(tab)
        fb.pack(fill="x", pady=(10, 4))
        ttk.Label(fb, text="Periodo:").pack(side="left")
        self.d_periodo = ttk.Combobox(fb, state="readonly", width=14,
                                      values=["Hoy", "Últimas 48 h", "Últimos 7 días", "Todo"])
        self.d_periodo.set("Últimas 48 h")
        self.d_periodo.pack(side="left", padx=(4, 12))
        ttk.Label(fb, text="Tema:").pack(side="left")
        self.d_tema = ttk.Combobox(fb, state="readonly", width=20, values=["Todos"] + self.cfg["temas"])
        self.d_tema.set("Todos")
        self.d_tema.pack(side="left", padx=(4, 12))
        ttk.Label(fb, text="Mostrar:").pack(side="left")
        self.d_mostrar = ttk.Combobox(fb, state="readonly", width=22,
                                      values=["Pendientes de publicar", "Todas"])
        self.d_mostrar.set("Pendientes de publicar")
        self.d_mostrar.pack(side="left", padx=(4, 12))
        self.d_mostrar.bind("<<ComboboxSelected>>", lambda e: self._refresh_dashboard())
        self.d_multi = tk.BooleanVar(value=False)
        ttk.Checkbutton(fb, text="Solo combinadas (2+ medios)", variable=self.d_multi,
                        command=self._refresh_dashboard).pack(side="left")
        for cb in (self.d_periodo, self.d_tema):
            cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_dashboard())
        ttk.Button(fb, text="Abrir Excel", command=self._open_excel).pack(side="right")
        ttk.Button(fb, text="Último boletín", command=self._open_last_consolidado).pack(side="right", padx=4)
        ttk.Button(fb, text="📰 Generar periódico", style="Accent.TButton",
                   command=self._publicar).pack(side="right", padx=(4, 0))
        ttk.Button(fb, text="👁 Vista previa", command=self._vista_previa).pack(side="right")

        # barra del editor
        eb = ttk.Frame(tab)
        eb.pack(fill="x", pady=(0, 4))
        ttk.Label(eb, text="Periódico:", style="Muted.TLabel").pack(side="left")
        ttk.Button(eb, text="✎ Editar nota", command=self._editar).pack(side="left", padx=(6, 0))
        ttk.Button(eb, text="☑ Incluir / ☐ Excluir", command=self._toggle_incluir).pack(side="left", padx=4)
        ttk.Button(eb, text="Incluir todas", command=lambda: self._incluir_todas(True)).pack(side="left")
        ttk.Button(eb, text="Excluir todas", command=lambda: self._incluir_todas(False)).pack(side="left", padx=4)
        self.lbl_va = ttk.Label(eb, text="", style="Muted.TLabel")
        self.lbl_va.pack(side="left", padx=10)
        ttk.Label(eb, text="Clic en ☑ para incluir o sacar · doble clic para editar texto y foto",
                  style="Muted.TLabel").pack(side="right")

        # chips por tema
        self.chips = ttk.Frame(tab)
        self.chips.pack(fill="x", pady=(2, 8))

        pw = ttk.PanedWindow(tab, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        pw.add(left, weight=3)
        cols = ("va", "marca", "hora", "titular", "medios", "n", "tema", "pdf")
        self.gtree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, t, w, anc in (("va", "¿Va?", 74, "center"), ("marca", "", 28, "center"), ("hora", "Hora", 78, "w"),
                             ("titular", "Titular", 300, "w"), ("medios", "Medios", 150, "w"),
                             ("n", "Fuentes", 56, "center"), ("tema", "Tema", 115, "w"),
                             ("pdf", "Boletín", 72, "w")):
            self.gtree.heading(c, text=t)
            self.gtree.column(c, width=w, stretch=(c == "titular"), anchor=anc)
        self.gtree.tag_configure("multi", background=MULTI, font=(F, 10, "bold"))
        self.gtree.tag_configure("nuevo", background=NEW)
        sb = ttk.Scrollbar(left, command=self.gtree.yview)
        self.gtree.configure(yscrollcommand=sb.set)
        self.gtree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.gtree.bind("<<TreeviewSelect>>", self._on_select_grupo)
        self.gtree.bind("<Button-1>", self._click_va, add="+")
        self.gtree.bind("<Double-1>", lambda e: self._editar())
        self.gtree.tag_configure("fuera", foreground="#9CA3AF")
        self.gtree.tag_configure("publicada", foreground="#6B7280")

        right = ttk.Frame(pw, padding=(10, 0, 0, 0))
        pw.add(right, weight=2)
        inner = ttk.Notebook(right, style="Inner.TNotebook")
        inner.pack(fill="both", expand=True)
        self.g_nota = RichText(inner)
        self.g_orig = RichText(inner)
        inner.add(self.g_nota, text="Nota combinada")
        inner.add(self.g_orig, text="Textos originales")

    # ================================================================== pestaña Configuración
    def _build_config(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="  Configuración  ")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=1)

        # --- IA
        ia = ttk.LabelFrame(tab, text="Inteligencia artificial", padding=10)
        ia.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(ia, text="Notas individuales (Fase 1) con:").grid(row=0, column=0, sticky="w")
        self.prov = tk.StringVar(value=self.cfg["proveedor_ia"])
        ttk.Radiobutton(ia, text="DeepSeek", variable=self.prov, value="deepseek",
                        command=self._save_provider).grid(row=0, column=1, padx=6)
        ttk.Radiobutton(ia, text="Claude", variable=self.prov, value="claude",
                        command=self._save_provider).grid(row=0, column=2, padx=6)
        ttk.Label(ia, text="El cruce y la nota combinada (Fase 2) usan siempre Claude.",
                  style="Muted.TLabel").grid(row=0, column=3, columnspan=3, sticky="w", padx=12)
        self.k_ds = self._key_row(ia, 1, "API key DeepSeek", get_api_key("deepseek"))
        self.k_cl = self._key_row(ia, 2, "API key Claude", get_api_key("claude"))
        ttk.Label(ia, text="Modelo Claude:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.m_cl = ttk.Entry(ia, width=28)
        self.m_cl.insert(0, self.cfg["modelo_claude"])
        self.m_cl.grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(ia, text="Guardar", style="Accent.TButton", command=self._save_keys).grid(
            row=3, column=4, sticky="e", pady=(6, 0))

        # --- páginas
        pf = ttk.LabelFrame(tab, text="Páginas monitoreadas", padding=10)
        pf.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        tab.rowconfigure(1, weight=1)
        self.pages_lb = tk.Listbox(pf, height=10, activestyle="none", exportselection=False, font=(F, 10),
                                   relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                   selectbackground="#C7DBFF", selectforeground="black")
        self.pages_lb.pack(fill="both", expand=True)
        self.page_entry = ttk.Entry(pf)
        self.page_entry.pack(fill="x", pady=(8, 4))
        bf = ttk.Frame(pf)
        bf.pack(fill="x")
        ttk.Button(bf, text="Agregar", command=self._add_page).pack(side="left")
        ttk.Button(bf, text="Quitar", command=self._remove_page).pack(side="left", padx=4)
        ttk.Button(bf, text="Activar / Pausar", command=self._toggle_page).pack(side="left")
        ttk.Label(pf, text="● activa   ○ pausada", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        # --- revisión y fase 2
        rv = ttk.LabelFrame(tab, text="Revisión y procesamiento", padding=10)
        rv.grid(row=1, column=1, sticky="nsew", padx=4)
        self.interval = tk.IntVar(value=self.cfg["intervalo_min"])
        self.n_posts = tk.IntVar(value=self.cfg["posts_por_revision"])
        self.ventana = tk.IntVar(value=self.cfg["ventana_cruce_horas"])
        for r, (txt, var, a, b) in enumerate((("Revisar cada (min)", self.interval, 5, 720),
                                             ("Publicaciones por página", self.n_posts, 1, 30),
                                             ("Ventana de cruce (horas)", self.ventana, 6, 168))):
            ttk.Label(rv, text=txt).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Spinbox(rv, from_=a, to=b, width=6, textvariable=var).grid(row=r, column=1, sticky="w", padx=6)
        self.f2_auto = tk.BooleanVar(value=self.cfg["fase2_auto"])
        self.pdf_auto = tk.BooleanVar(value=self.cfg["pdf_consolidado_auto"])
        ttk.Checkbutton(rv, text="Cruzar y clasificar con Claude en cada revisión",
                        variable=self.f2_auto).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Checkbutton(rv, text="Generar boletín PDF consolidado en cada ciclo",
                        variable=self.pdf_auto).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Button(rv, text="Aplicar", style="Accent.TButton", command=self._apply_settings).grid(
            row=5, column=0, sticky="w", pady=(12, 4))
        ttk.Separator(rv).grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Button(rv, text="Iniciar sesión en Facebook", command=self._login).grid(
            row=7, column=0, columnspan=2, sticky="w")
        ttk.Label(rv, text="Abre el navegador del monitor; inicia sesión y ciérralo.",
                  style="Muted.TLabel", wraplength=260).grid(row=8, column=0, columnspan=2, sticky="w", pady=4)

        # --- temas
        tf = ttk.LabelFrame(tab, text="Temas (clasificación)", padding=10)
        tf.grid(row=1, column=2, sticky="nsew", padx=(8, 0))
        self.temas_lb = tk.Listbox(tf, height=10, activestyle="none", exportselection=False, font=(F, 10),
                                   relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                   selectbackground="#C7DBFF", selectforeground="black")
        self.temas_lb.pack(fill="both", expand=True)
        for t in self.cfg["temas"]:
            self.temas_lb.insert("end", t)
        self.tema_entry = ttk.Entry(tf)
        self.tema_entry.pack(fill="x", pady=(8, 4))
        bt = ttk.Frame(tf)
        bt.pack(fill="x")
        ttk.Button(bt, text="Agregar", command=self._add_tema).pack(side="left")
        ttk.Button(bt, text="Quitar", command=self._remove_tema).pack(side="left", padx=4)
        ttk.Button(bt, text="↑", width=3, command=lambda: self._move_tema(-1)).pack(side="left")
        ttk.Button(bt, text="↓", width=3, command=lambda: self._move_tema(1)).pack(side="left", padx=4)
        ttk.Label(tf, text="El orden se usa en el boletín. El último es el comodín (\"Otros\").",
                  style="Muted.TLabel", wraplength=260).pack(anchor="w", pady=(6, 0))

    def _key_row(self, parent, row, label, value):
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="w", pady=3)
        e = ttk.Entry(parent, width=60, show="•")
        e.insert(0, value)
        e.grid(row=row, column=1, columnspan=3, sticky="w", pady=3)
        v = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="ver", variable=v,
                        command=lambda: e.configure(show="" if v.get() else "•")).grid(row=row, column=4, sticky="w")
        return e

    # ================================================================== configuración: acciones
    def _load_pages(self):
        self.pages_lb.delete(0, "end")
        for p in self.cfg["paginas"]:
            slug = page_slug(p["url"])
            act = p.get("activa", True)
            self.pages_lb.insert("end", f"{'●' if act else '○'}  {nombre_medio(slug, self.cfg)}  ({slug})")
            self.pages_lb.itemconfig("end", fg="#111827" if act else "#9CA3AF")
        self.q_medio.configure(values=["Todos"] + [page_slug(p["url"]) for p in self.cfg["paginas"]])
        if not self.q_medio.get():
            self.q_medio.set("Todos")

    def _add_page(self):
        txt = self.page_entry.get().strip()
        if not txt:
            return
        url = normalize_page_url(txt)
        if any(page_slug(p["url"]) == page_slug(url) for p in self.cfg["paginas"]):
            messagebox.showinfo("Páginas", "Esa página ya está en la lista.")
            return
        self.cfg["paginas"].append({"url": url, "activa": True})
        save_config(self.cfg)
        self.page_entry.delete(0, "end")
        self._load_pages()

    def _sel(self, lb):
        s = lb.curselection()
        return s[0] if s else None

    def _remove_page(self):
        i = self._sel(self.pages_lb)
        if i is None:
            return
        if messagebox.askyesno("Páginas", f"¿Quitar {page_slug(self.cfg['paginas'][i]['url'])}?"):
            del self.cfg["paginas"][i]
            save_config(self.cfg)
            self._load_pages()

    def _toggle_page(self):
        i = self._sel(self.pages_lb)
        if i is None:
            return
        p = self.cfg["paginas"][i]
        p["activa"] = not p.get("activa", True)
        save_config(self.cfg)
        self._load_pages()
        self.pages_lb.selection_set(i)

    def _save_temas(self):
        self.cfg["temas"] = list(self.temas_lb.get(0, "end"))
        save_config(self.cfg)
        self.d_tema.configure(values=["Todos"] + self.cfg["temas"])

    def _add_tema(self):
        t = self.tema_entry.get().strip()
        if t and t not in self.temas_lb.get(0, "end"):
            # se inserta antes del comodín final
            self.temas_lb.insert(max(0, self.temas_lb.size() - 1), t)
            self.tema_entry.delete(0, "end")
            self._save_temas()

    def _remove_tema(self):
        i = self._sel(self.temas_lb)
        if i is not None and self.temas_lb.size() > 1:
            self.temas_lb.delete(i)
            self._save_temas()

    def _move_tema(self, d):
        i = self._sel(self.temas_lb)
        if i is None or not 0 <= i + d < self.temas_lb.size():
            return
        t = self.temas_lb.get(i)
        self.temas_lb.delete(i)
        self.temas_lb.insert(i + d, t)
        self.temas_lb.selection_set(i + d)
        self._save_temas()

    def _save_keys(self):
        set_api_key("deepseek", self.k_ds.get())
        set_api_key("claude", self.k_cl.get())
        self.cfg["modelo_claude"] = self.m_cl.get().strip() or self.cfg["modelo_claude"]
        save_config(self.cfg)
        self.status.set("API keys guardadas en .env")

    def _save_provider(self):
        self.cfg["proveedor_ia"] = self.prov.get()
        save_config(self.cfg)
        self.status.set(f"Notas individuales con: {self.prov.get()}")

    def _apply_settings(self):
        try:
            self.cfg["intervalo_min"] = max(5, int(self.interval.get()))
            self.cfg["posts_por_revision"] = max(1, int(self.n_posts.get()))
            self.cfg["ventana_cruce_horas"] = max(6, int(self.ventana.get()))
        except (tk.TclError, ValueError):
            messagebox.showwarning("Configuración", "Revisa los números.")
            return
        self.cfg["fase2_auto"] = self.f2_auto.get()
        self.cfg["pdf_consolidado_auto"] = self.pdf_auto.get()
        save_config(self.cfg)
        self._schedule()
        self.status.set("Configuración guardada.")

    # ================================================================== revisión
    def _schedule(self, initial=False):
        if initial and not PROFILE_DIR.exists():
            self.next_check = None
            self.status.set("Primer uso: Configuración → 'Iniciar sesión en Facebook', luego 'Revisar ahora'.")
            return
        self._pintar_auto()
        if not self.cfg.get("revision_automatica", True):
            self.next_check = None
            return
        mins = self.cfg["intervalo_min"]
        self.next_check = datetime.now() + (timedelta(seconds=5) if initial else timedelta(minutes=mins))

    def _pintar_auto(self):
        if self.cfg.get("revision_automatica", True):
            self.btn_auto.configure(text=f"⏸  Pausar automático ({self.cfg['intervalo_min']} min)")
        else:
            self.btn_auto.configure(text="▶  Reanudar automático")

    def _toggle_auto(self):
        activo = not self.cfg.get("revision_automatica", True)
        self.cfg["revision_automatica"] = activo
        save_config(self.cfg)
        if activo:
            self.next_check = datetime.now() + timedelta(minutes=self.cfg["intervalo_min"])
            self._aviso(f"Revisión automática activada: cada {self.cfg['intervalo_min']} min "
                        f"(próxima a las {self.next_check:%H:%M}).", "ok")
        else:
            self.next_check = None
            self._aviso("Revisión automática pausada. Solo se revisará al pulsar «Revisar ahora».", "warn")
        self._pintar_auto()

    def _detener(self):
        if not self.running and not fase2_lock.locked():
            self._aviso("No hay ninguna revisión en curso.", "info")
            return
        detener.set()
        self.busy_txt.configure(text="⏳ Deteniendo… se cierra el navegador en unos segundos")
        self.btn_stop.state(["disabled"])
        self._aviso("Deteniendo la revisión. Lo que ya se leyó queda guardado.", "warn")

    def _set_busy(self, busy, texto=None):
        self.running = busy
        for b in (self.btn_now, self.btn_fase2):
            b.state(["disabled"] if busy else ["!disabled"])
        self.chip.configure(text=f"● {texto or ('Trabajando…' if busy else 'En espera')}",
                            fg="#FFD27A" if busy else "#9FE2B0")
        if busy:
            self.btn_stop.state(["!disabled"])
            self.btn_stop.pack(side="left", padx=4)
            self.busy_txt.configure(text=f"⏳ {texto or 'Trabajando…'}")
            self.busy_bar.pack(fill="x", after=self.chip.master.master, padx=12, pady=(8, 0))
            self.busy_prog.start(12)
        else:
            self.btn_stop.pack_forget()
            self.busy_prog.stop()
            self.busy_bar.pack_forget()

    def _aviso(self, texto, tipo="ok", ms=4500):
        """Mensaje flotante abajo a la derecha (y en la barra de estado)."""
        colores = {"ok": ("#E7F6EC", "#1E6B34", "✔"), "warn": ("#FFF4E5", "#8A5300", "⚠"),
                   "error": ("#FDECEC", "#B3261E", "✖"), "info": ("#E8F0FE", "#1D4ED8", "ℹ")}
        bg, fg, ico = colores.get(tipo, colores["info"])
        self.status.set(texto)
        if self._toast is not None:
            self._toast.destroy()
        t = tk.Label(self, text=f" {ico}  {texto} ", bg=bg, fg=fg, font=(F, 10, "bold"), padx=12, pady=8,
                     highlightthickness=1, highlightbackground=fg, wraplength=520, justify="left")
        t.place(relx=1.0, rely=1.0, x=-18, y=-36, anchor="se")
        self._toast = t
        self.after(ms, lambda: t.destroy() if self._toast is t else None)

    def _cfg_actual(self):
        cfg = load_config()
        cfg["proveedor_ia"] = self.prov.get()
        return cfg

    def _emit(self, ev, d=None):
        self.q.put((ev, d))

    def check_now(self):
        if self.running:
            self._aviso("Ya hay una revisión en curso (franja amarilla). Puedes detenerla con «■ Detener».", "warn")
            return
        detener.clear()
        self._set_busy(True, "Revisando Facebook…")
        self.last_error = ""
        self.status.set("Iniciando revisión…")
        threading.Thread(target=self._run_check, args=(self._cfg_actual(),), daemon=True).start()

    def _run_check(self, cfg):
        try:
            revisar(cfg, self._emit)
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", str(e)))
            self.q.put(("fin", 0))

    def _run_fase2(self, forzar_pdf):
        if self.running or fase2_lock.locked():
            return
        cfg = self._cfg_actual()
        if not forzar_pdf:
            cfg["pdf_consolidado_auto"] = False  # "Analizar" no genera boletín
        self._set_busy(True, "Analizando con Claude…" if not forzar_pdf else "Generando boletín…")
        self.last_error = ""

        def run():
            try:
                detener.clear()
                ciclo_fase2(cfg, self._emit, forzar_pdf=forzar_pdf, cancelar=detener.is_set)
            finally:
                self.q.put(("fase2_manual_fin", None))

        threading.Thread(target=run, daemon=True).start()

    def _login(self):
        if self.running or browser_lock.locked():
            messagebox.showwarning("Facebook", "Espera a que termine la revisión en curso.")
            return

        def run():
            with browser_lock:
                interactive_login(self.cfg.get("navegador_canal"), log=lambda m: self.q.put(("status", m)))
            self.q.put(("status", "Ventana de login cerrada. Sesión guardada en fb_profile/."))

        threading.Thread(target=run, daemon=True).start()

    def _tick(self):
        if self.next_check and not self.running and datetime.now() >= self.next_check:
            self.check_now()
        if self.running:
            nxt = "en curso…"
        elif not self.cfg.get("revision_automatica", True):
            nxt = "automático pausado (solo manual)"
        else:
            nxt = self.next_check.strftime("%H:%M:%S") if self.next_check else "—"
        last = self.last_check.strftime("%d/%m %H:%M") if self.last_check else "—"
        err = f"   ⚠ {self.last_error[:90]}" if self.last_error else ""
        self.sched.set(f"Última revisión: {last}   ·   Próxima: {nxt}{err}")
        self.after(1000, self._tick)

    def _poll_queue(self):
        try:
            while True:
                ev, d = self.q.get_nowait()
                if ev == "status":
                    self.status.set(d)
                    if self.running:
                        self.busy_txt.configure(text=f"⏳ {d}")
                elif ev == "error":
                    self.last_error = d
                    self.status.set(f"Error: {d}")
                elif ev == "nuevo":
                    self.nuevos_ids.add(d)
                    self._load_posts()
                elif ev == "actualizado":
                    self._load_posts()
                    self._notify_post(d)
                elif ev == "grupo":
                    self.grupos_nuevos.add(d)
                    self._refresh_dashboard()
                elif ev == "consolidado":
                    self._notify("Boletín consolidado listo", os.path.basename(d))
                    self.status.set(f"Boletín generado: {d}")
                elif ev == "fase2_fin":
                    self._refresh_dashboard()
                    self._load_posts()
                elif ev == "detenido":
                    self._aviso("Revisión detenida. Lo leído hasta ese momento quedó guardado.", "warn")
                elif ev == "fase2_manual_fin":
                    self._set_busy(False)
                    if not self.last_error:
                        self.status.set("Análisis terminado.")
                elif ev == "fin":
                    self._set_busy(False)
                    self.last_check = datetime.now()
                    self._schedule()
                    self._load_posts()
                    self._refresh_dashboard()
                    self.status.set(f"Revisión {'detenida' if detener.is_set() else 'terminada'}: "
                                    f"{d} publicación(es) nueva(s)."
                                    + (f"  Último error: {self.last_error}" if self.last_error else ""))
        except queue.Empty:
            pass
        self.after(400, self._poll_queue)

    def _notify(self, title, msg):
        self.bell()
        if notification:
            try:
                notification.notify(title=title, message=msg[:200], app_name="Altiplano al Día", timeout=8)
            except Exception:  # noqa: BLE001
                pass

    def _notify_post(self, post_id):
        row = storage.get_post(post_id)
        if row and row["estado"] == "procesado":
            self._notify(f"Nueva noticia · {nombre_medio(row['pagina'], self.cfg)}", row["titular"] or "")

    # ================================================================== bandeja: datos
    def _load_posts(self):
        if not hasattr(self, "tree"):
            return
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        txt = self.q_text.get().lower().strip()
        medio = self.q_medio.get()
        estado = self.q_estado.get()
        n = 0
        for r in storage.list_posts():
            if medio not in ("", "Todos") and r["pagina"] != medio:
                continue
            if estado != "Todos" and r["estado"] != estado:
                continue
            titular = r["titular"] or (r["texto"] or "").strip().split("\n")[0][:110]
            if txt and txt not in (titular + " " + (r["texto"] or "")).lower():
                continue
            tags = ["odd"] if n % 2 else []
            if r["id"] in self.nuevos_ids:
                tags = ["nuevo"]
            if r["estado"] == "error":
                tags.append("error")
            self.tree.insert("", "end", iid=r["id"], tags=tags, values=(
                nombre_medio(r["pagina"], self.cfg), _fecha_corta(r["publicado"] or r["detectado"]),
                titular, r["estado"]))
            n += 1
        self.lbl_bandeja.configure(text=f"{n} publicaciones")
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])

    def _current(self):
        s = self.tree.selection()
        return storage.get_post(s[0]) if s else None

    def _on_select_post(self, _e=None):
        r = self._current()
        if not r:
            return
        if r["id"] in self.nuevos_ids:
            self.nuevos_ids.discard(r["id"])
            self.tree.item(r["id"], tags=[])
        t = self.detail
        t.clear()
        ia = json.loads(r["ia_json"]) if r["ia_json"] else {}
        t.add(f"{nombre_medio(r['pagina'], self.cfg)}  ·  publicado {r['publicado'] or '—'}  ·  "
              f"capturado {r['detectado']}\n", "meta")
        if r["ambito"]:
            t.add(f"{r['ambito']} · {r['tema']} · grupo #{r['grupo_id']}\n", "meta")
        t.add("\n")
        if ia:
            t.add(ia.get("TITULAR", "") + "\n", "h1")
            t.add(ia.get("BAJADA", "") + "\n", "baj")
            t.add("CUERPO\n", "h2")
            t.add(_sin_marcas(ia.get("CUERPO")) + "\n")
            if ia.get("DATOS"):
                t.add("DATOS O CIFRAS\n", "h2")
                for d in ia["DATOS"]:
                    t.add(f"  •  {d}\n")
        if r["error"]:
            t.add(f"\n⚠ {r['error']}\n", "warn")
        t.add("ENLACES\n", "h2")
        t.link("Ver en Facebook", r["permalink"])
        if r["enlace_ext"]:
            t.add("   ·   ")
            t.link("Nota externa", r["enlace_ext"])
        t.add("\n")
        t.add("TEXTO ORIGINAL\n", "h2")
        t.add((r["texto"] or "") + "\n", "orig")
        t.done()

    def _open_link(self, campo):
        r = self._current()
        if r and r[campo]:
            webbrowser.open(r[campo])

    def _open_pdf(self):
        r = self._current()
        if r and r["pdf_path"] and os.path.exists(r["pdf_path"]):
            os.startfile(r["pdf_path"])  # noqa: S606
        elif r:
            messagebox.showinfo("PDF", "Esta publicación todavía no tiene PDF.")

    def _reprocess(self):
        r = self._current()
        if not r:
            return
        storage.update_post(r["id"], estado="pendiente", error=None)
        self.status.set("Reprocesando con IA…")
        threading.Thread(target=procesar_post, args=(r["id"], self._cfg_actual(), self._emit), daemon=True).start()

    # ================================================================== dashboard: datos
    def _desde(self):
        p = self.d_periodo.get()
        now = datetime.now()
        if p == "Hoy":
            return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if p == "Últimas 48 h":
            return (now - timedelta(hours=48)).isoformat()
        if p == "Últimos 7 días":
            return (now - timedelta(days=7)).isoformat()
        return None

    def _select_ambito(self, amb):
        self.amb_sel = amb
        self._refresh_dashboard()

    def _refresh_dashboard(self):
        if not hasattr(self, "gtree"):
            return
        grupos = storage.list_grupos(self._desde())
        tema = self.d_tema.get()
        solo_multi = self.d_multi.get()
        base = [g for g in grupos if (not solo_multi or storage.n_medios(g) > 1)]

        # tarjetas: conteo por ámbito (respeta tema y "solo combinadas")
        con_tema = [g for g in base if tema == "Todos" or g["tema"] == tema]
        for amb, (card, num, lab, sub) in self.cards.items():
            items = con_tema if amb == "TODOS" else [g for g in con_tema if g["ambito"] == amb]
            multi = sum(1 for g in items if storage.n_medios(g) > 1)
            num.configure(text=str(len(items)))
            sub.configure(text=f"{multi} combinadas" if multi else " ")
            sel = amb == self.amb_sel
            color = AMBITO_COLOR.get(amb, MUTED)
            bg = color if sel else PANEL
            for w in (card, num, lab, sub):
                w.configure(bg=bg)
            num.configure(fg="white" if sel else color)
            lab.configure(fg="white" if sel else "#374151")
            sub.configure(fg="#E5E7EB" if sel else MUTED)
            card.configure(highlightbackground=color if sel else BORDER)

        # chips por tema (respeta ámbito)
        en_amb = [g for g in base if self.amb_sel == "TODOS" or g["ambito"] == self.amb_sel]
        for w in self.chips.winfo_children():
            w.destroy()
        ttk.Label(self.chips, text="Por tema:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        for t in self.cfg["temas"]:
            n = sum(1 for g in en_amb if g["tema"] == t)
            if not n:
                continue
            activo = tema == t
            chip = tk.Label(self.chips, text=f"{t}  {n}", font=(F, 9, "bold" if activo else "normal"),
                            bg=ACCENT if activo else "#E4E8F0", fg="white" if activo else "#374151",
                            padx=10, pady=3, cursor="hand2")
            chip.pack(side="left", padx=3)
            chip.bind("<Button-1>", lambda e, tt=t: self._chip(tt))
        if tema != "Todos":
            x = tk.Label(self.chips, text="✕ quitar filtro", fg=ACCENT, bg=BG, cursor="hand2", font=(F, 9))
            x.pack(side="left", padx=8)
            x.bind("<Button-1>", lambda e: self._chip("Todos"))

        # tabla
        sel = self.gtree.selection()
        self.gtree.delete(*self.gtree.get_children())
        for g in en_amb:
            if tema != "Todos" and g["tema"] != tema:
                continue
            multi = storage.n_medios(g) > 1
            tags = ["multi"] if multi else []
            if g["id"] in self.grupos_nuevos:
                tags.append("nuevo")
            pdf = {"publicado": f"N° {g['ultimo_pdf']}", "actualizacion": "Actualizar",
                   "nuevo": "Pendiente"}.get(g["estado_pdf"], g["estado_pdf"])
            pendiente = g["estado_pdf"] in ("nuevo", "actualizacion")
            if self.d_mostrar.get() != "Todas" and not pendiente:
                continue
            va_si = (g["en_periodico"] if g["en_periodico"] is not None else 1) == 1
            if pendiente and not va_si:
                tags.append("fuera")
            if not pendiente:
                tags.append("publicada")
            va = ("☑ Va" if va_si else "☐ No va") if pendiente else "Publicada"
            self.gtree.insert("", "end", iid=str(g["id"]), tags=tags, values=(
                va, "◆" if multi else "", _fecha_corta(g["fecha_hecho"]), g["titular"], g["medios"],
                g["n_fuentes"], g["tema"], pdf))
        if sel and self.gtree.exists(sel[0]):
            self.gtree.selection_set(sel[0])
        listos = storage.grupos_para_pdf()
        self.lbl_va.configure(text=f"{len(listos)} noticia(s) irán en el próximo periódico")
        if not self.gtree.get_children() and self.d_mostrar.get() != "Todas":
            self.gtree.insert("", "end", iid="vacio", values=(
                "", "", "", "No hay noticias pendientes con estos filtros. Las ya publicadas se ven con "
                "«Mostrar: Todas»; llegan noticias nuevas en cada revisión.", "", "", "", ""))

    def _chip(self, tema):
        self.d_tema.set(tema if self.d_tema.get() != tema else "Todos")
        self._refresh_dashboard()

    def _on_select_grupo(self, _e=None):
        s = self.gtree.selection()
        if not s or not s[0].isdigit():
            return
        gid = int(s[0])
        self.grupos_nuevos.discard(gid)
        g = storage.get_grupo(gid)
        if not g or not g["nota_json"]:
            return
        nota = json.loads(g["nota_json"])
        posts = storage.posts_de_grupo(gid)

        t = self.g_nota
        t.clear()
        if g["estado_pdf"] == "actualizacion":
            t.add(" ACTUALIZACIÓN ", "badge_upd")
            t.add("  ")
        if storage.n_medios(g) > 1:
            t.add(f" ◆ COMBINADA · {storage.n_medios(g)} MEDIOS ", "badge_multi")
        t.add("\n")
        t.add(f"{g['ambito']} · {g['tema']} · {_fecha_corta(g['fecha_hecho'])} · grupo #{gid}\n", "meta")
        t.add((nota.get("TITULAR") or "") + "\n", "h1")
        t.add((nota.get("BAJADA") or "") + "\n", "baj")
        t.add("CUERPO\n", "h2")
        t.add(_sin_marcas(nota.get("CUERPO")) + "\n")
        if nota.get("DATOS"):
            t.add("DATOS O CIFRAS IMPORTANTES\n", "h2")
            for d in nota["DATOS"]:
                if isinstance(d, dict):
                    t.add(f"  •  {d.get('dato', '')}")
                    if d.get("fuentes"):
                        t.add(f"   ({', '.join(d['fuentes'])})", "meta")
                    t.add("\n")
                else:
                    t.add(f"  •  {d}\n")
        if nota.get("DISCREPANCIAS"):
            t.add("DATOS QUE DIFIEREN ENTRE MEDIOS\n", "h2")
            for d in nota["DISCREPANCIAS"]:
                vers = "; ".join(f"{v.get('medio')}: {v.get('dice')}" for v in d.get("versiones", []))
                t.add(f" {d.get('aspecto', '')} — {vers} \n", "warn")
        t.add("FUENTES Y LINKS\n", "h2")
        aportes = {f.get("medio"): f.get("aporte") for f in nota.get("FUENTES") or []}
        for l in nota.get("LINKS") or []:
            t.add(f"{l.get('medio')}  ", "src")
            t.add(f"{_fecha_corta(l.get('fecha'))}  ", "meta")
            t.link("Facebook", l.get("facebook"))
            if l.get("externo"):
                t.add("  ·  ")
                t.link("Nota completa", l.get("externo"))
            t.add("\n")
            if aportes.get(l.get("medio")) and len(nota.get("LINKS")) > 1:
                t.add(f"   {aportes[l['medio']]}\n", "meta")
        t.done()

        o = self.g_orig
        o.clear()
        for p in posts:
            o.add(f"{nombre_medio(p['pagina'], self.cfg)}  ·  {p['publicado'] or p['detectado']}\n", "src")
            o.link("Abrir en Facebook", p["permalink"])
            o.add("\n")
            o.add((p["texto"] or "") + "\n\n", "orig")
        o.done()

    # ================================================================== periódico: selección y edición
    def _gid_sel(self):
        s = self.gtree.selection()
        return int(s[0]) if s and s[0].isdigit() else None

    def _click_va(self, e):
        if self.gtree.identify_region(e.x, e.y) != "cell" or self.gtree.identify_column(e.x) != "#1":
            return
        fila = self.gtree.identify_row(e.y)
        if fila and fila.isdigit():
            self.after(10, lambda: self._cambiar_va(int(fila)))

    def _toggle_incluir(self):
        gid = self._gid_sel()
        if gid is None:
            self._aviso("Selecciona una noticia en la tabla.", "warn")
            return
        self._cambiar_va(gid)

    def _cambiar_va(self, gid):
        g = storage.get_grupo(gid)
        titular = (g["titular"] or "")[:60]
        if g["estado_pdf"] not in ("nuevo", "actualizacion"):
            if messagebox.askyesno("Noticia ya publicada",
                                   f"«{titular}» ya salió en el boletín N° {g['ultimo_pdf']}.\n\n"
                                   "¿Quieres ponerla otra vez en el próximo periódico?"):
                storage.actualizar_grupo(gid, estado_pdf="nuevo")
                storage.set_en_periodico([gid], True)
                self._aviso(f"Volverá a salir: {titular}", "ok")
            self._refresh_dashboard()
            return
        va = (g["en_periodico"] if g["en_periodico"] is not None else 1) == 1
        storage.set_en_periodico([gid], not va)
        self._aviso(("Excluida del periódico: " if va else "Incluida en el periódico: ") + titular,
                    "warn" if va else "ok")
        self._refresh_dashboard()

    def _incluir_todas(self, valor):
        """Aplica a las noticias pendientes que se ven ahora en la tabla (respeta los filtros)."""
        ids = []
        for i in self.gtree.get_children():
            if i.isdigit() and self.gtree.set(i, "va") != "Publicada":
                ids.append(int(i))
        if not ids:
            self._aviso("No hay noticias pendientes en la tabla (las publicadas no cambian).", "warn")
            return
        storage.set_en_periodico(ids, valor)
        self._aviso(f"{len(ids)} noticia(s) {'incluidas' if valor else 'excluidas'}.", "ok" if valor else "warn")
        self._refresh_dashboard()

    def _tab_cambiada(self, _e=None):
        if self.nb.select() == str(self.portada_tab):
            self.portada_tab.recargar()

    def _editar(self):
        gid = self._gid_sel()
        if gid is None:
            self._aviso("Selecciona una noticia en la tabla para editarla.", "warn")
            return
        EditorNota(self, gid, self._cfg_actual(),
                   on_save=lambda: (self._refresh_dashboard(), self._on_select_grupo()))

    def _generar_pdf(self, previa: bool):
        from .consolidado import generar_consolidado
        if self.running or fase2_lock.locked():
            self._aviso("Hay una revisión o un análisis en curso (ver la franja amarilla). "
                        "Espera a que termine para armar el periódico.", "warn", 6000)
            return
        n = len(storage.grupos_para_pdf())
        if not n:
            messagebox.showinfo("Periódico", "No hay noticias marcadas (☑) pendientes de publicar.")
            return
        if not previa and not messagebox.askyesno(
                "Generar periódico",
                f"Se generará el boletín N° {storage.siguiente_consolidado()} con {n} noticia(s).\n"
                "Esas noticias quedarán marcadas como publicadas. ¿Continuar?"):
            return
        cfg = self._cfg_actual()
        self._set_busy(True, "Armando la vista previa…" if previa else "Generando el periódico…")
        self.last_error = ""

        def run():
            try:
                with fase2_lock:
                    if previa:
                        out = generar_consolidado(cfg, destino=BASE_DIR / "data" / "vista_previa.pdf",
                                                  registrar=False)
                    else:
                        out = generar_consolidado(cfg)
                if out:
                    os.startfile(str(out))  # noqa: S606
                    self.q.put(("status", ("Vista previa lista: " if previa else "Periódico generado: ") + str(out)))
            except PermissionError:
                self.q.put(("error", "Cierra la vista previa anterior en el visor de PDF y vuelve a intentar."))
            except Exception as e:  # noqa: BLE001
                self.q.put(("error", f"Periódico: {e}"))
            finally:
                self.q.put(("fase2_fin", None))
                self.q.put(("fase2_manual_fin", None))

        threading.Thread(target=run, daemon=True).start()

    def _vista_previa(self):
        self._generar_pdf(True)

    def _publicar(self):
        self._generar_pdf(False)

    def _open_last_consolidado(self):
        c = storage.ultimo_consolidado()
        if c and os.path.exists(c["path"]):
            os.startfile(c["path"])  # noqa: S606
        else:
            messagebox.showinfo("Boletín", "Todavía no se generó ningún boletín.")

    def _open_excel(self):
        if EXCEL_PATH.exists():
            os.startfile(str(EXCEL_PATH))  # noqa: S606
        else:
            messagebox.showinfo("Excel", "Todavía no existe noticias.xlsx (se crea al analizar).")


def main():
    App().mainloop()
