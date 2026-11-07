"""Pestaña Portada: qué noticia va en cada lugar de la portada, sus textos y su foto."""
import json
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import portada, storage
from .config import IMG_DIR
from .gui_inicio import enganchar_rueda

F = "Segoe UI"
MUTED = "#6B7280"
AUTO = "(elegir automáticamente)"
THUMB = (236, 132)


def importar_foto(ruta: str, gid: int) -> str:
    """Copia una foto elegida en la PC a data/img/ (JPEG) y devuelve la ruta nueva."""
    with Image.open(ruta) as orig:          # (con with: Windows no deja el archivo bloqueado)
        im = orig.convert("RGB")
    im.thumbnail((2000, 2000))
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    destino = IMG_DIR / f"manual_g{gid}_{datetime.now():%Y%m%d%H%M%S}.jpg"
    im.save(destino, "JPEG", quality=90)
    return str(destino)


def aviso_foto(ruta, ancho_pt):
    """Texto y color del aviso de calidad de una foto para un lugar de la portada."""
    if not ruta:
        return "Sin foto: se usará un fondo de color", MUTED
    w, h, ok = portada.calidad_foto(ruta, ancho_pt)
    if not w:
        return "No se puede abrir la foto", "#B3261E"
    if ok:
        return f"✔ Foto {w}×{h} px", "#2E7D32"
    return f"⚠ Foto pequeña ({w}×{h} px): se verá borrosa, conviene cambiarla", "#B3261E"


class Tarjeta(ttk.LabelFrame):
    def __init__(self, master, tab, lugar, nombre):
        super().__init__(master, text=f"  {nombre}  ", padding=8)
        self.tab, self.lugar = tab, lugar
        self.gid = None
        self.foto = None          # ruta elegida (None = sin foto)
        self.foto_cambiada = False
        self._ph = None
        self.columnconfigure(1, weight=1)

        self.combo = ttk.Combobox(self, state="readonly")
        self.combo.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._cambio_noticia())

        # imagen vacía del tamaño de la miniatura: así el recuadro mide en píxeles (con solo texto, tk mide en letras)
        self._vacio = tk.PhotoImage(width=THUMB[0], height=THUMB[1])
        self.img = tk.Label(self, bg="#E7EEF9", image=self._vacio, text="Sin foto", compound="center", fg=MUTED)
        self.img.grid(row=1, column=0, rowspan=4, sticky="nw", pady=(8, 0))
        bf = ttk.Frame(self)
        bf.grid(row=1, column=1, sticky="nw", padx=(10, 0), pady=(8, 0))
        ttk.Button(bf, text="Cambiar foto…", command=self._archivo).pack(fill="x")
        ttk.Button(bf, text="Otra foto de la noticia ▸", command=self._siguiente).pack(fill="x", pady=3)
        ttk.Button(bf, text="Sin foto", command=lambda: self._usar(None)).pack(fill="x")
        self.calidad = tk.Label(self, text="", fg=MUTED, bg="#F3F5F9", font=(F, 8), wraplength=250, justify="left")
        self.calidad.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 2))

        ttk.Label(self, text="Antetítulo de portada").grid(row=6, column=0, columnspan=2, sticky="w")
        self.ante = ttk.Entry(self)
        self.ante.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Label(self, text="Titular de portada").grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.tit = ttk.Entry(self, font=(F, 10, "bold"))
        self.tit.grid(row=9, column=0, columnspan=2, sticky="ew")
        self.puntos = None
        if lugar == "principal":
            ttk.Label(self, text="Puntos clave (uno por línea)").grid(row=10, column=0, columnspan=2,
                                                                       sticky="w", pady=(4, 0))
            self.puntos = tk.Text(self, height=3, wrap="word", font=(F, 9), relief="solid", borderwidth=1)
            self.puntos.grid(row=11, column=0, columnspan=2, sticky="ew")
        self._marcar_guardado()     # recién creada no tiene cambios (si no, la pestaña nunca se cargaba)

    # ------------------------------------------------------------------
    def cargar(self, gid):
        self.gid = gid
        self.foto_cambiada = False
        for w in (self.ante, self.tit):
            w.delete(0, "end")
        if self.puntos:
            self.puntos.delete("1.0", "end")
        g = storage.get_grupo(gid) if gid is not None else None
        if g is None or not g["nota_json"]:
            self.gid = None
            self.combo.set(AUTO)
            self.foto = None
            self._mostrar()
            self._marcar_guardado()
            return
        self.combo.set(self.tab.etiqueta(gid))
        nota = json.loads(g["nota_json"])
        self.ante.insert(0, nota.get("PORTADA_ANTE") or "")
        self.tit.insert(0, nota.get("PORTADA_TITULAR") or "")
        if self.puntos:
            self.puntos.insert("1.0", "\n".join(nota.get("PORTADA_PUNTOS") or []))
        self.foto = None if nota.get("_SIN_FOTO") else (self._candidatas() or [None])[0]
        if nota.get("_IMAGEN") and Path(nota["_IMAGEN"]).exists() and not nota.get("_SIN_FOTO"):
            self.foto = nota["_IMAGEN"]
        self._mostrar()
        self._marcar_guardado()

    # --- cambios sin guardar
    def _estado(self):
        return (self.gid, self.ante.get().strip(), self.tit.get().strip(),
                self.puntos.get("1.0", "end").strip() if self.puntos else "", self.foto_cambiada)

    def _marcar_guardado(self):
        self._guardado = self._estado()

    def hay_cambios(self):
        return self._estado() != getattr(self, "_guardado", None)

    def _candidatas(self):
        if self.gid is None:
            return []
        g = storage.get_grupo(self.gid)
        if g is None or not g["nota_json"]:
            return []
        nota = json.loads(g["nota_json"])
        out = []
        for c in [nota.get("_IMAGEN")] + [p["imagen"] for p in storage.posts_de_grupo(self.gid)]:
            if c and c not in out and Path(c).exists():
                out.append(c)
        return out

    def _mostrar(self):
        self._ph = None
        if self.foto and Path(self.foto).exists():
            try:
                with Image.open(self.foto) as im:
                    im.thumbnail(THUMB)
                    self._ph = ImageTk.PhotoImage(im)
            except Exception:  # noqa: BLE001   (foto dañada: no debe romper la pestaña)
                self._ph = None
        if self._ph is not None:
            self.img.configure(image=self._ph, text="", compound="none", width=THUMB[0], height=THUMB[1])
        else:
            self._ph = None
            self.img.configure(image=self._vacio, text="Sin foto", compound="center", width=THUMB[0], height=THUMB[1])
        texto, color = aviso_foto(self.foto, portada.ANCHO_LUGAR[self.lugar]) if self.gid else ("", MUTED)
        self.calidad.configure(text=texto, fg=color)

    def _usar(self, ruta):
        if self.gid is None:
            return
        self.foto = ruta
        self.foto_cambiada = True
        self._mostrar()

    def _siguiente(self):
        c = self._candidatas()
        if not c:
            self.tab.app._aviso("Esta noticia no tiene otras fotos. Usa «Cambiar foto…».", "warn")
            return
        i = (c.index(self.foto) + 1) % len(c) if self.foto in c else 0
        self._usar(c[i])

    def _archivo(self):
        if self.gid is None:
            self.tab.app._aviso("Primero elige qué noticia va en este lugar.", "warn")
            return
        ruta = filedialog.askopenfilename(parent=self, title="Elegir foto", filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if ruta:
            try:
                self._usar(importar_foto(ruta, self.gid))
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("Foto", f"No se pudo abrir la imagen:\n{e}", parent=self)

    def _cambio_noticia(self):
        previo = getattr(self, "_guardado", None)
        self.cargar(self.tab.gid_de(self.combo.get()))
        self._guardado = previo          # cambiar la noticia del lugar también es un cambio sin guardar

    def guardar(self):
        if self.gid is None:
            self._marcar_guardado()
            return
        g = storage.get_grupo(self.gid)
        if g is None or not g["nota_json"]:
            return
        nota = json.loads(g["nota_json"])
        for k, v in (("PORTADA_ANTE", self.ante.get().strip()), ("PORTADA_TITULAR", self.tit.get().strip())):
            if v:
                nota[k] = v
            else:
                nota.pop(k, None)
        if self.puntos:
            pts = [x.strip() for x in self.puntos.get("1.0", "end").splitlines() if x.strip()]
            if pts:
                nota["PORTADA_PUNTOS"] = pts
            else:
                nota.pop("PORTADA_PUNTOS", None)
        if self.foto_cambiada:
            nota.pop("_IMAGEN", None)
            nota.pop("_SIN_FOTO", None)
            if self.foto is None:
                nota["_SIN_FOTO"] = True
            else:
                nota["_IMAGEN"] = self.foto
        storage.actualizar_grupo(self.gid, nota_json=nota)
        self.foto_cambiada = False
        self._marcar_guardado()


class PortadaTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.opciones = {}     # etiqueta -> gid

        top = ttk.Frame(self)
        top.pack(fill="x")
        tk.Label(top, text="Portada del próximo periódico", font=(F, 14, "bold"), fg="#16324F",
                 bg="#F3F5F9").pack(side="left")
        # (si la portada no se puede guardar, p. ej. una noticia repetida, no se sigue)
        ttk.Button(top, text="📰 Generar periódico", style="Accent.TButton",
                   command=lambda: self.guardar(silencioso=True) and app._publicar()).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="👁 Vista previa", command=lambda: self.guardar(silencioso=True) and
                   app._vista_previa()).pack(side="right", padx=4)
        ttk.Button(top, text="💾 Guardar portada", command=self.guardar).pack(side="right", padx=4)
        self.b_claude = ttk.Button(top, text="✍ Titulares con Claude", command=self.textos_claude)
        self.b_claude.pack(side="right", padx=4)
        self._claude_en_curso = False
        ttk.Button(top, text="↻ Elegir automáticamente", command=self.automatico).pack(side="right", padx=4)
        self.info = ttk.Label(self, text="", style="Muted.TLabel", wraplength=1100, justify="left")
        self.info.pack(fill="x", pady=(4, 8))
        self.bind("<Configure>", lambda e: self.info.configure(wraplength=max(400, e.width - 30)))

        # las tarjetas van dentro de un área con desplazamiento: en pantallas bajas no entran las dos filas
        cont = ttk.Frame(self)
        cont.pack(fill="both", expand=True)
        self._cv = tk.Canvas(cont, bg="#F3F5F9", highlightthickness=0)
        sb = ttk.Scrollbar(cont, command=self._cv.yview)
        self._cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._cv.pack(side="left", fill="both", expand=True)
        grid = ttk.Frame(self._cv)
        win = self._cv.create_window(0, 0, window=grid, anchor="nw")
        grid.bind("<Configure>", lambda e: self._cv.configure(scrollregion=self._cv.bbox("all")))
        self._cv.bind("<Configure>", lambda e: self._cv.itemconfigure(win, width=e.width))
        self.tarjetas = {}
        for i, (lugar, nombre) in enumerate(portada.LUGARES):
            t = Tarjeta(grid, self, lugar, nombre)
            t.grid(row=i // 3, column=i % 3, sticky="nsew", padx=5, pady=5)
            grid.columnconfigure(i % 3, weight=1, uniform="tarjetas")
            self.tarjetas[lugar] = t
        enganchar_rueda(self._cv, self._cv)

    # ------------------------------------------------------------------
    def etiqueta(self, gid):
        return next((k for k, v in self.opciones.items() if v == gid), AUTO)

    def gid_de(self, etiqueta):
        return self.opciones.get(etiqueta)

    def _articulos(self):
        from .consolidado import _articulo
        return [_articulo(g, self.app._cfg_actual()) for g in storage.grupos_para_pdf(con_relleno=True)]

    def recargar(self):
        arts = self._articulos()
        self.opciones = {f"#{a['gid']} · {a['titular'][:70]}": a["gid"] for a in arts}
        for t in self.tarjetas.values():
            t.combo.configure(values=[AUTO] + list(self.opciones))
        if not arts:
            self.info.configure(text="No hay noticias pendientes marcadas con ☑ en el Dashboard. "
                                     "Cuando lleguen noticias nuevas podrás armar aquí la portada.")
            for t in self.tarjetas.values():
                t.cargar(None)
            return
        sel = portada.elegir(arts)
        fijados = portada.leer_lugares()
        for lugar, t in self.tarjetas.items():
            a = sel.get(lugar)
            t.cargar(a["gid"] if a else None)
        n_fij = sum(1 for lugar in self.tarjetas if fijados.get(lugar) in self.opciones.values())
        self.info.configure(text=f"{len(arts)} noticias van al periódico. Elige qué noticia va en cada lugar "
                                 f"({n_fij} fijadas a mano; el resto se elige solo por importancia y foto). "
                                 "Si dejas vacío un titular de portada, Claude lo escribe al generar.")

    def hay_cambios(self):
        return any(t.hay_cambios() for t in self.tarjetas.values())

    def olvidar_cambios(self):
        for t in self.tarjetas.values():
            t._marcar_guardado()

    def guardar(self, silencioso=False):
        elegidos = [t.gid for t in self.tarjetas.values() if t.gid is not None]
        if len(elegidos) != len(set(elegidos)):
            messagebox.showwarning("Portada", "Una misma noticia está en dos lugares de la portada. Cambia una.",
                                   parent=self)
            return False
        portada.guardar_lugares({lugar: t.gid for lugar, t in self.tarjetas.items()})
        for t in self.tarjetas.values():
            t.guardar()
        if not silencioso:
            self.app._aviso("Portada guardada.", "ok")
        return True

    def automatico(self):
        if self.hay_cambios() and not messagebox.askyesno(
                "Portada", "Se descartarán los cambios que no guardaste y la portada se elegirá sola.\n\n"
                           "¿Continuar?", parent=self):
            return
        portada.guardar_lugares({})
        self.recargar()
        self.app._aviso("Portada armada automáticamente (por importancia y foto).", "ok")

    def textos_claude(self):
        if self._claude_en_curso:
            self.app._aviso("Claude ya está escribiendo los titulares; espera un momento.", "warn")
            return
        if not self.guardar(silencioso=True):
            return
        arts = {a["gid"]: a for a in self._articulos()}
        sel = [arts[t.gid] for t in self.tarjetas.values() if t.gid in arts]
        faltan = [a for a in sel if not a["nota"].get("PORTADA_TITULAR")]
        if not faltan:
            self.app._aviso("Todos los lugares ya tienen titular. Borra los que quieras que Claude reescriba.", "warn")
            return
        principal = self.tarjetas["principal"].gid
        self.app._aviso(f"Claude está escribiendo {len(faltan)} titular(es) de portada…", "info", 8000)
        cfg = self.app._cfg_actual()             # (se lee aquí: el hilo no debe tocar variables de Tk)
        self._claude_en_curso = True
        self.b_claude.configure(text="⏳ Claude escribiendo…")
        self.b_claude.state(["disabled"])

        def listo(msg, tipo):
            self._claude_en_curso = False
            if not self.winfo_exists():
                return
            self.b_claude.configure(text="✍ Titulares con Claude")
            self.b_claude.state(["!disabled"])
            if tipo == "ok" and not self.hay_cambios():
                self.recargar()
            self.app._aviso(msg, tipo, 6000)

        def run():
            try:
                portada.textos_portada(cfg, sel, principal)
                self.app.en_ui(lambda: listo("Titulares de portada listos.", "ok"))
            except Exception as e:  # noqa: BLE001
                msg = f"Claude no pudo escribir los titulares: {e}"
                self.app.en_ui(lambda: listo(msg, "error"))

        threading.Thread(target=run, daemon=True).start()
