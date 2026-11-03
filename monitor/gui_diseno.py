"""Panel 🎨 Diseño de la vista previa: textos fijos, colores, tipografías y tamaños del periódico."""
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from . import diseno

F = "Segoe UI"
MUTED = "#6B7280"


class _Scroll(tk.Frame):
    """Marco con barra de desplazamiento vertical."""

    def __init__(self, master):
        super().__init__(master, bg="#FFFFFF")
        self.cv = tk.Canvas(self, bg="#FFFFFF", highlightthickness=0)
        sb = ttk.Scrollbar(self, command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.dentro = tk.Frame(self.cv, bg="#FFFFFF")
        self._win = self.cv.create_window(0, 0, window=self.dentro, anchor="nw")
        self.dentro.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self._win, width=e.width))
        for w in (self.cv, self.dentro):
            w.bind("<Enter>", lambda e: self.cv.bind_all("<MouseWheel>", self._rueda))
            w.bind("<Leave>", lambda e: self.cv.unbind_all("<MouseWheel>"))

    def _rueda(self, e):
        self.cv.yview_scroll(-e.delta // 120, "units")


class PanelDiseno(tk.Frame):
    def __init__(self, master, al_aplicar):
        super().__init__(master, bg="#FFFFFF", width=360, highlightthickness=1, highlightbackground="#D9DEE7")
        self.pack_propagate(False)
        self.al_aplicar = al_aplicar
        self.d = diseno.cargar()

        cab = tk.Frame(self, bg="#16324F")
        cab.pack(fill="x")
        tk.Label(cab, text="🎨 Diseño del periódico", bg="#16324F", fg="white",
                 font=(F, 11, "bold")).pack(side="left", padx=10, pady=7)

        pre = tk.Frame(self, bg="#FFFFFF")
        pre.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(pre, text="Combinación lista:", bg="#FFFFFF", font=(F, 9, "bold")).pack(anchor="w")
        fila = tk.Frame(pre, bg="#FFFFFF")
        fila.pack(fill="x")
        self.preset = ttk.Combobox(fila, state="readonly", values=list(diseno.PRESETS))
        self.preset.set("Altiplano (predeterminado)")
        self.preset.pack(side="left", fill="x", expand=True)
        ttk.Button(fila, text="Usar", command=self._usar_preset).pack(side="left", padx=(6, 0))
        tk.Label(pre, text="Cambia colores y letras de una vez (tus textos se mantienen).", bg="#FFFFFF",
                 fg=MUTED, font=(F, 8)).pack(anchor="w")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=4)
        self.t_textos, self.t_colores, self.t_letras = _Scroll(nb), _Scroll(nb), _Scroll(nb)
        self.t_portada = _Scroll(nb)
        nb.add(self.t_textos, text="Textos")
        nb.add(self.t_colores, text="Colores")
        nb.add(self.t_letras, text="Letras")
        nb.add(self.t_portada, text="Portada")

        pie = tk.Frame(self, bg="#FFFFFF")
        pie.pack(fill="x", padx=10, pady=8)
        ttk.Button(pie, text="✔ Aplicar y ver", style="Accent.TButton", command=self._aplicar).pack(fill="x")
        ttk.Button(pie, text="Volver al diseño original", command=self._restablecer).pack(fill="x", pady=(4, 0))
        self._llenar()

    # ------------------------------------------------------------------
    def _llenar(self):
        for t in (self.t_textos, self.t_colores, self.t_letras, self.t_portada):
            for w in t.dentro.winfo_children():
                w.destroy()
        d = self.d
        # textos
        tk.Label(self.t_textos.dentro, text="Puedes usar {numero} {fecha} {hora} {n}", bg="#FFFFFF", fg=MUTED,
                 font=(F, 8)).pack(anchor="w", padx=8, pady=(6, 2))
        self.e_textos = {}
        for clave, ayuda in diseno.TEXTOS_AYUDA.items():
            tk.Label(self.t_textos.dentro, text=ayuda, bg="#FFFFFF", fg="#374151", font=(F, 8, "bold")).pack(
                anchor="w", padx=8, pady=(6, 0))
            e = ttk.Entry(self.t_textos.dentro)
            e.insert(0, d["textos"].get(clave, ""))
            e.pack(fill="x", padx=8)
            self.e_textos[clave] = e
        # colores
        self.colores = {k: v for k, v in d["colores"].items() if k != "temas"}
        self.temas = dict(d["colores"]["temas"])
        tk.Label(self.t_colores.dentro, text="Clic en un color para cambiarlo", bg="#FFFFFF", fg=MUTED,
                 font=(F, 8)).pack(anchor="w", padx=8, pady=(6, 2))
        for clave, ayuda in diseno.COLORES_AYUDA.items():
            self._fila_color(self.t_colores.dentro, ayuda, self.colores, clave)
        tk.Label(self.t_colores.dentro, text="Color de cada tema", bg="#FFFFFF", fg="#16324F",
                 font=(F, 9, "bold")).pack(anchor="w", padx=8, pady=(10, 2))
        for tema in self.temas:
            self._fila_color(self.t_colores.dentro, tema, self.temas, tema)
        # letras
        fams = diseno.familias_disponibles()
        self.c_fuentes = {}
        tk.Label(self.t_letras.dentro, text="Tipografía de cada parte", bg="#FFFFFF", fg="#16324F",
                 font=(F, 9, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        for rol, (nombre, _) in diseno.ROLES.items():
            tk.Label(self.t_letras.dentro, text=nombre, bg="#FFFFFF", font=(F, 8, "bold"), fg="#374151").pack(
                anchor="w", padx=8, pady=(6, 0))
            cb = ttk.Combobox(self.t_letras.dentro, state="readonly", values=fams)
            cb.set(d["fuentes"].get(rol, diseno.ROLES[rol][1]))
            cb.pack(fill="x", padx=8)
            self.c_fuentes[rol] = cb
        tk.Label(self.t_letras.dentro, text="Tamaños", bg="#FFFFFF", fg="#16324F",
                 font=(F, 9, "bold")).pack(anchor="w", padx=8, pady=(12, 2))
        self.s_tam = {}
        for clave, nombre, de, a, paso in (("texto", "Texto de las noticias (pt)", 7, 13, 0.5),
                                           ("antetitulo", "Antetítulos (pt)", 8, 16, 0.5),
                                           ("pie_foto", "Pies de foto (pt)", 6, 11, 0.2),
                                           ("titulares", "Titulares interiores (%)", 70, 140, 5)):
            f = tk.Frame(self.t_letras.dentro, bg="#FFFFFF")
            f.pack(fill="x", padx=8, pady=2)
            tk.Label(f, text=nombre, bg="#FFFFFF", font=(F, 9)).pack(side="left")
            sp = ttk.Spinbox(f, from_=de, to=a, increment=paso, width=7)
            sp.set(d["tamanos"].get(clave))
            sp.pack(side="right")
            self.s_tam[clave] = sp
        # portada: letras y tamaños de cada parte
        P = self.t_portada.dentro
        tk.Label(P, text="Tipografía de cada parte de la portada", bg="#FFFFFF", fg="#16324F",
                 font=(F, 9, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        for rol, (nombre, _) in diseno.ROLES_PORTADA.items():
            tk.Label(P, text=nombre, bg="#FFFFFF", font=(F, 8, "bold"), fg="#374151").pack(anchor="w", padx=8, pady=(6, 0))
            cb = ttk.Combobox(P, state="readonly", values=fams)
            cb.set(d["fuentes"].get(rol, diseno.ROLES_PORTADA[rol][1]))
            cb.pack(fill="x", padx=8)
            self.c_fuentes[rol] = cb
        tk.Label(P, text="Tamaños de letra de la portada", bg="#FFFFFF", fg="#16324F",
                 font=(F, 9, "bold")).pack(anchor="w", padx=8, pady=(12, 2))
        tk.Label(P, text="Los titulares se achican solos si no entran; este es su tamaño máximo.",
                 bg="#FFFFFF", fg=MUTED, font=(F, 8), wraplength=300, justify="left").pack(anchor="w", padx=8)
        for clave, nombre, de, a, paso in diseno.TAMANOS_PORTADA:
            f = tk.Frame(P, bg="#FFFFFF")
            f.pack(fill="x", padx=8, pady=2)
            tk.Label(f, text=nombre, bg="#FFFFFF", font=(F, 9)).pack(side="left")
            sp = ttk.Spinbox(f, from_=de, to=a, increment=paso, width=7)
            sp.set(d["tamanos"].get(clave))
            sp.pack(side="right")
            self.s_tam[clave] = sp

    def _fila_color(self, padre, etiqueta, destino, clave):
        f = tk.Frame(padre, bg="#FFFFFF")
        f.pack(fill="x", padx=8, pady=2)
        muestra = tk.Label(f, text="      ", bg=destino[clave], relief="solid", bd=1, cursor="hand2")
        muestra.pack(side="left")
        lbl = tk.Label(f, text=f"{etiqueta}   {destino[clave]}", bg="#FFFFFF", font=(F, 9), cursor="hand2",
                       anchor="w", justify="left", wraplength=260)
        lbl.pack(side="left", padx=6, fill="x")

        def elegir(_e=None):
            _, hexa = colorchooser.askcolor(destino[clave], parent=self, title=etiqueta)
            if hexa:
                destino[clave] = hexa.upper()
                muestra.configure(bg=hexa)
                lbl.configure(text=f"{etiqueta}   {hexa.upper()}")
        for w in (muestra, lbl):
            w.bind("<Button-1>", elegir)

    def _leer(self):
        d = diseno.cargar()
        for clave, e in self.e_textos.items():
            d["textos"][clave] = e.get()
        d["colores"].update(self.colores)
        d["colores"]["temas"] = dict(self.temas)
        for rol, cb in self.c_fuentes.items():
            d["fuentes"][rol] = cb.get()
        for clave, sp in self.s_tam.items():
            try:
                d["tamanos"][clave] = float(sp.get())
            except ValueError:
                pass
        return d

    def _aplicar(self):
        self.d = self._leer()
        diseno.guardar(self.d)
        self.al_aplicar("Diseño aplicado.")

    def _usar_preset(self):
        self.d = diseno.con_preset(self._leer(), self.preset.get())
        self._llenar()
        self._aplicar()

    def _restablecer(self):
        if messagebox.askyesno("Diseño", "¿Volver a los textos, colores y letras originales?", parent=self):
            self.d = diseno.restablecer()
            self._llenar()
            self.al_aplicar("Diseño original restablecido.")
