"""Editor de una nota antes de maquetar el periódico: textos, foto, textos de portada y si va o no."""
import json
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import storage
from .config import nombre_medio

F = "Segoe UI"
MUTED = "#6B7280"
THUMB = (150, 100)
PREVIEW = (330, 220)


class EditorNota(tk.Toplevel):
    """Ventana de edición. on_save() se llama después de guardar."""

    def __init__(self, master, gid: int, cfg: dict, on_save=None):
        super().__init__(master)
        self.gid, self.cfg, self.on_save = gid, cfg, on_save
        self._guardando = False
        self._inicial = None
        g = storage.get_grupo(gid)
        if g is None or not g["nota_json"]:
            self.withdraw()
            messagebox.showwarning("Editar nota", "Esta noticia ya no existe o todavía no tiene nota redactada.",
                                   parent=master)
            self.after_idle(self.destroy)
            return
        self.nota = json.loads(g["nota_json"])
        self.posts = storage.posts_de_grupo(gid)
        self.title(f"Editar nota · grupo #{gid}")
        ajustar_ventana(self, 1180, 800)
        self.minsize(820, 520)
        self.configure(bg="#F3F5F9")
        self.transient(master)
        self._imgs = []          # referencias para que Tk no borre las miniaturas
        self.foto = None if self.nota.get("_SIN_FOTO") else self._foto_actual()

        # barra de botones primero, abajo: siempre visible aunque la pantalla sea chica
        pie = ttk.Frame(self, padding=(12, 6, 12, 10))
        pie.pack(side="bottom", fill="x")
        ttk.Button(pie, text="💾 Guardar  (Ctrl+S)", style="Accent.TButton", command=self._guardar).pack(side="right")
        ttk.Button(pie, text="Cancelar  (Esc)", command=self._cancelar).pack(side="right", padx=6)
        ttk.Label(pie, text="Los cambios se ven en el periódico al guardar.", foreground=MUTED).pack(side="left")
        cab = ttk.Frame(self, padding=(12, 8, 12, 0))
        cab.pack(side="top", fill="x")
        ttk.Button(cab, text="💾 Guardar", style="Accent.TButton", command=self._guardar).pack(side="right")
        medio = self.nota.get("_menciona_medio")
        if medio:
            # la IA dejó el nombre del medio de origen en el texto: hay que sacarlo antes de publicar
            tk.Label(cab, text=f"⚠ El texto todavía nombra al medio de origen «{medio}». Quítalo del titular, "
                               "la bajada y el cuerpo antes de publicar.", bg="#FDECEC", fg="#B3261E",
                     font=(F, 10, "bold"), padx=10, pady=4, wraplength=760, justify="left").pack(side="left")
        self.bind("<Control-s>", lambda e: self._guardar())
        self.bind("<Control-S>", lambda e: self._guardar())
        self.bind("<Escape>", lambda e: self._cancelar())
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        cuerpo = ttk.Frame(self, padding=12)
        cuerpo.pack(fill="both", expand=True)
        cuerpo.columnconfigure(0, weight=3)
        cuerpo.columnconfigure(1, weight=2)
        cuerpo.rowconfigure(0, weight=1)

        # ---------------- textos
        izq = ttk.Frame(cuerpo)
        izq.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        izq.columnconfigure(1, weight=1)
        medios = ", ".join(dict.fromkeys(nombre_medio(p["pagina"], cfg) for p in self.posts))
        ttk.Label(izq, text=f"{g['ambito']} · {g['tema']} · fuentes: {medios}",
                  foreground=MUTED).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.campos = {}
        fila = 1
        for clave, etiqueta in (("ANTETITULO", "Antetítulo"), ("TITULAR", "Titular"),
                                ("LUGAR", "Lugar (ciudad)"), ("PIE_FOTO", "Pie de foto")):
            ttk.Label(izq, text=etiqueta).grid(row=fila, column=0, sticky="w", pady=3)
            e = ttk.Entry(izq, font=(F, 11 if clave == "TITULAR" else 10))
            e.insert(0, self.nota.get(clave) or "")
            e.grid(row=fila, column=1, sticky="ew", pady=3)
            self.campos[clave] = e
            fila += 1
        ttk.Label(izq, text="Bajada").grid(row=fila, column=0, sticky="nw", pady=3)
        self.bajada = self._texto(izq, 3, self.nota.get("BAJADA"))
        self.bajada.grid(row=fila, column=1, sticky="ew", pady=3)
        fila += 1
        ttk.Label(izq, text="Cuerpo").grid(row=fila, column=0, sticky="nw", pady=3)
        marco = ttk.Frame(izq)
        marco.grid(row=fila, column=1, sticky="nsew", pady=3)
        izq.rowconfigure(fila, weight=1)
        self.cuerpo = self._texto(marco, 9, self.nota.get("CUERPO"))
        sb = ttk.Scrollbar(marco, command=self.cuerpo.yview)
        self.cuerpo.configure(yscrollcommand=sb.set)
        self.cuerpo.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        fila += 1
        ttk.Label(izq, text="Deja una línea en blanco entre párrafos. Un párrafo que empieza con «## » "
                            "es un intertítulo.", foreground=MUTED).grid(row=fila, column=1, sticky="w")
        fila += 1
        ttk.Label(izq, text="Datos clave\n(uno por línea)").grid(row=fila, column=0, sticky="nw", pady=3)
        self.datos = self._texto(izq, 4, datos_a_texto(self.nota))
        self.datos.grid(row=fila, column=1, sticky="ew", pady=3)
        fila += 1

        port = ttk.LabelFrame(izq, text="  Si sale en portada  ", padding=8)
        port.grid(row=fila, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        port.columnconfigure(1, weight=1)
        ttk.Label(port, text="Titular de portada").grid(row=0, column=0, sticky="w")
        self.p_tit = ttk.Entry(port, font=(F, 10, "bold"))
        self.p_tit.insert(0, self.nota.get("PORTADA_TITULAR") or "")
        self.p_tit.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(port, text="Antetítulo de portada").grid(row=1, column=0, sticky="w")
        self.p_ante = ttk.Entry(port)
        self.p_ante.insert(0, self.nota.get("PORTADA_ANTE") or "")
        self.p_ante.grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(port, text="Puntos clave\n(uno por línea)").grid(row=2, column=0, sticky="nw")
        self.p_puntos = self._texto(port, 2, "\n".join(self.nota.get("PORTADA_PUNTOS") or []))
        self.p_puntos.grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Label(port, text="Si lo dejas vacío, Claude lo escribe al generar la vista previa.",
                  foreground=MUTED).grid(row=3, column=1, sticky="w")

        # ---------------- foto
        der = ttk.Frame(cuerpo)
        der.grid(row=0, column=1, sticky="nsew")
        ttk.Label(der, text="Foto de la nota", font=(F, 11, "bold")).pack(anchor="w")
        self.lbl_foto = tk.Label(der, bg="#E7EEF9", width=PREVIEW[0], height=PREVIEW[1])
        self.lbl_foto.pack(anchor="w", pady=(4, 6))
        self.lbl_calidad = tk.Label(der, text="", font=(F, 9, "bold"), bg="#F3F5F9", wraplength=330, justify="left")
        self.lbl_calidad.pack(anchor="w", pady=(0, 6))
        bf = ttk.Frame(der)
        bf.pack(anchor="w")
        ttk.Button(bf, text="Elegir archivo…", command=self._elegir_archivo).pack(side="left")
        ttk.Button(bf, text="Sin foto", command=lambda: self._usar(None)).pack(side="left", padx=6)
        ttk.Label(der, text="Fotos disponibles (clic para usar):", foreground=MUTED).pack(anchor="w", pady=(12, 4))
        self.galeria = ttk.Frame(der)
        self.galeria.pack(anchor="w", fill="x")
        self._llenar_galeria()

        self.incluir = tk.BooleanVar(value=bool(g["en_periodico"] if "en_periodico" in g.keys() and
                                                g["en_periodico"] is not None else 1))
        ttk.Checkbutton(der, text="Incluir esta noticia en el periódico", variable=self.incluir).pack(
            anchor="w", pady=(16, 0))

        self._mostrar_foto()
        self._inicial = self._estado()
        self.campos["TITULAR"].focus_set()
        self.grab_set()

    # ------------------------------------------------------------------ cambios sin guardar
    def _estado(self):
        return ([e.get() for e in self.campos.values()],
                [t.get("1.0", "end") for t in (self.bajada, self.cuerpo, self.datos, self.p_puntos)],
                self.p_tit.get(), self.p_ante.get(), self.foto, self.incluir.get())

    def hay_cambios(self):
        return self._inicial is not None and self._estado() != self._inicial

    def _cancelar(self):
        if self.hay_cambios() and not messagebox.askyesno(
                "Cambios sin guardar", "Hiciste cambios en esta nota que no guardaste.\n\n"
                                       "¿Cerrar y perder los cambios?", icon="warning", parent=self):
            return
        self.destroy()

    # ------------------------------------------------------------------ util
    def _texto(self, parent, alto, valor):
        t = tk.Text(parent, height=alto, wrap="word", font=(F, 10), relief="solid", borderwidth=1,
                    padx=6, pady=4, undo=True)
        t.insert("1.0", valor or "")
        return t

    def _foto_actual(self):
        cands = [self.nota.get("_IMAGEN")] + [p["imagen"] for p in self.posts if "imagen" in p.keys()]
        return next((c for c in cands if c and Path(c).exists()), None)

    def _candidatas(self):
        vistas, out = set(), []
        for c in [self.nota.get("_IMAGEN")] + [p["imagen"] for p in self.posts if "imagen" in p.keys()]:
            if c and c not in vistas and Path(c).exists():
                vistas.add(c)
                out.append(c)
        return out

    def _miniatura(self, ruta, size):
        """PhotoImage de la foto, o None si no se puede abrir (archivo dañado o borrado)."""
        try:
            with Image.open(ruta) as im:          # (con with: el archivo no queda bloqueado en Windows)
                im.thumbnail(size)
                ph = ImageTk.PhotoImage(im)
        except Exception:  # noqa: BLE001
            return None
        self._imgs.append(ph)
        return ph

    def _llenar_galeria(self):
        for w in self.galeria.winfo_children():
            w.destroy()
        cands = self._candidatas()
        if not cands:
            ttk.Label(self.galeria, text="(los posts de esta noticia no tienen foto)", foreground=MUTED).pack(anchor="w")
        for i, ruta in enumerate(cands):
            ph = self._miniatura(ruta, THUMB)
            b = tk.Label(self.galeria, image=ph or "", text="" if ph else "(foto dañada)", cursor="hand2", bd=2,
                         relief="solid" if ruta == self.foto else "flat")
            b.grid(row=i // 2, column=i % 2, padx=3, pady=3)
            b.bind("<Button-1>", lambda e, r=ruta: self._usar(r))

    def _mostrar_foto(self):
        from .gui_portada import aviso_foto
        texto, color = aviso_foto(self.foto, 300)
        self.lbl_calidad.configure(text=texto, fg=color)
        ph = self._miniatura(self.foto, PREVIEW) if self.foto and Path(self.foto).exists() else None
        if ph:
            self.lbl_foto.configure(image=ph, text="", width=0, height=0)
        else:
            self.lbl_foto.configure(image="", text="Sin foto", font=(F, 12), fg=MUTED,
                                    width=36, height=11)

    def _usar(self, ruta):
        self.foto = ruta
        self._imgs = []          # las miniaturas viejas se vuelven a crear (no se acumulan en memoria)
        self._mostrar_foto()
        self._llenar_galeria()

    def _elegir_archivo(self):
        ruta = filedialog.askopenfilename(parent=self, title="Elegir foto",
                                          filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if not ruta:
            return
        try:
            from .gui_portada import importar_foto
            destino = importar_foto(ruta, self.gid)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Foto", f"No se pudo abrir la imagen:\n{e}", parent=self)
            return
        self._usar(str(destino))

    def _guardar(self):
        if self._guardando:                   # (Ctrl+S repetido o doble clic)
            return
        n = dict(self.nota)
        for clave, e in self.campos.items():
            n[clave] = e.get().strip()
        n["BAJADA"] = self.bajada.get("1.0", "end").strip()
        n["CUERPO"] = self.cuerpo.get("1.0", "end").strip()
        n["DATOS"] = texto_a_datos(self.nota, self.datos.get("1.0", "end"))
        if not n["TITULAR"] or not n["CUERPO"]:
            falta = "el titular" if not n["TITULAR"] else "el cuerpo"
            messagebox.showwarning("Editar nota", f"Falta {falta}: el titular y el cuerpo no pueden quedar vacíos.",
                                   parent=self)
            (self.campos["TITULAR"] if not n["TITULAR"] else self.cuerpo).focus_set()
            return
        tit, ante = self.p_tit.get().strip(), self.p_ante.get().strip()
        puntos = [x.strip() for x in self.p_puntos.get("1.0", "end").splitlines() if x.strip()]
        for k, v in (("PORTADA_TITULAR", tit), ("PORTADA_ANTE", ante), ("PORTADA_PUNTOS", puntos)):
            if v:
                n[k] = v
            else:
                n.pop(k, None)
        n.pop("_IMAGEN", None)
        n.pop("_SIN_FOTO", None)
        if self.foto is None:
            n["_SIN_FOTO"] = True
        else:
            n["_IMAGEN"] = self.foto
        n["_EDITADO"] = datetime.now().isoformat(timespec="seconds")
        medio = (n.get("_menciona_medio") or "").strip().lower()
        if medio and medio not in " ".join(str(n.get(k) or "") for k in
                                           ("ANTETITULO", "TITULAR", "BAJADA", "CUERPO", "PIE_FOTO")).lower():
            n.pop("_menciona_medio", None)          # ya se corrigió: se quita la advertencia
        self._guardando = True
        try:
            storage.guardar_nota(self.gid, n)
            storage.set_en_periodico([self.gid], self.incluir.get())
        except Exception as e:  # noqa: BLE001
            self._guardando = False
            messagebox.showerror("Editar nota", f"No se pudo guardar la nota (vuelve a intentar en unos "
                                                f"segundos):\n{e}", parent=self)
            return
        self.destroy()
        if self.on_save:
            self.on_save()


def datos_a_texto(nota: dict) -> str:
    out = []
    for d in nota.get("DATOS") or []:
        out.append(d.get("dato", "") if isinstance(d, dict) else str(d))
    return "\n".join(x for x in out if x.strip())


def texto_a_datos(nota: dict, texto: str) -> list:
    """Líneas -> DATOS, conservando los medios de cada dato que no cambió (para la ficha interna)."""
    previos = {}
    for d in nota.get("DATOS") or []:
        if isinstance(d, dict):
            previos[d.get("dato", "").strip()] = d.get("fuentes", [])
    lineas = [x.strip(" •-\t") for x in texto.splitlines() if x.strip(" •-\t")]
    if previos or any(isinstance(d, dict) for d in nota.get("DATOS") or []):
        return [{"dato": x, "fuentes": previos.get(x, [])} for x in lineas]
    return lineas


class EditorDatos(tk.Toplevel):
    """Edita la caja «Datos clave» de una página: los datos de cada noticia que la forman."""

    def __init__(self, master, gids, on_save=None):
        super().__init__(master)
        self.title("Editar datos clave")
        ajustar_ventana(self, 780, 640)
        self.transient(master)
        self.on_save = on_save
        self._guardando = False
        pie = ttk.Frame(self, padding=12)
        pie.pack(side="bottom", fill="x")
        ttk.Button(pie, text="💾 Guardar  (Ctrl+S)", style="Accent.TButton", command=self._guardar).pack(side="right")
        ttk.Button(pie, text="Cancelar  (Esc)", command=self._cancelar).pack(side="right", padx=6)
        self.bind("<Control-s>", lambda e: self._guardar())
        self.bind("<Control-S>", lambda e: self._guardar())
        self.bind("<Escape>", lambda e: self._cancelar())
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        ttk.Label(self, text="Un dato por línea. Si borras todos los datos de las noticias, la caja no se muestra.",
                  foreground=MUTED, padding=(12, 10, 12, 4)).pack(anchor="w")
        cuerpo = ttk.Frame(self, padding=(12, 0))
        cuerpo.pack(fill="both", expand=True)
        self.campos = {}
        for gid in gids:
            g = storage.get_grupo(gid)
            if g is None or not g["nota_json"]:
                continue
            nota = json.loads(g["nota_json"])
            ttk.Label(cuerpo, text=(g["titular"] or "")[:100], font=(F, 10, "bold")).pack(anchor="w", pady=(10, 2))
            t = tk.Text(cuerpo, height=5, wrap="word", font=(F, 10), relief="solid", borderwidth=1, padx=6, pady=4)
            t.insert("1.0", datos_a_texto(nota))
            t.pack(fill="both", expand=True)
            self.campos[gid] = (t, nota)
        self._inicial = self._estado()
        self.grab_set()

    def _estado(self):
        return [t.get("1.0", "end") for t, _ in self.campos.values()]

    def hay_cambios(self):
        return self._estado() != self._inicial

    def _cancelar(self):
        if self.hay_cambios() and not messagebox.askyesno(
                "Cambios sin guardar", "Hiciste cambios en los datos clave que no guardaste.\n\n"
                                       "¿Cerrar y perder los cambios?", icon="warning", parent=self):
            return
        self.destroy()

    def _guardar(self):
        if self._guardando:
            return
        self._guardando = True
        try:
            for gid, (t, nota) in self.campos.items():
                nota["DATOS"] = texto_a_datos(nota, t.get("1.0", "end"))
                storage.guardar_nota(gid, nota)
        except Exception as e:  # noqa: BLE001
            self._guardando = False
            messagebox.showerror("Datos clave", f"No se pudo guardar:\n{e}", parent=self)
            return
        self.destroy()
        if self.on_save:
            self.on_save()


def ajustar_ventana(win, ancho, alto):
    """Tamaño que entra en la pantalla (deja sitio a la barra de tareas) y centrado."""
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = min(ancho, sw - 40), min(alto, sh - 90)
    win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
