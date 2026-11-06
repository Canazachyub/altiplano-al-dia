"""Mesa de edición de la vista previa: notas propias, cambio de foto, sección y cupos.

- Nota propia: el equipo escribe (o pega un borrador) una nota que no vino de Facebook, elige la
  sección y la foto; puede ir a un espacio libre concreto y Claude la redacta al largo de ese
  espacio con el estilo del diario.
- Foto desde un archivo, quitar la foto, cambiar la sección de una nota.
- Cupos: cuántas notas tiene cada sección frente a los espacios de la plantilla del día.
"""
import json
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image

from . import consolidado, storage
from .config import BASE_DIR

IMG_PROPIAS = BASE_DIR / "data" / "img_propias"
F = "Segoe UI"
AMBITO = {"LOCAL": "LOCAL", "DISTRITAL": "DISTRITAL", "PROVINCIAL": "REGIONAL", "POLÍTICA": "REGIONAL",
          "POLICIAL": "REGIONAL", "DEPORTES": "REGIONAL"}
TEMA = {"POLÍTICA": "Política", "POLICIAL": "Seguridad/Policiales", "DEPORTES": "Deportes"}


def importar_foto(ruta: str) -> str:
    """Copia la foto elegida a data/img_propias (JPEG de hasta 2000 px) y devuelve la ruta nueva."""
    IMG_PROPIAS.mkdir(parents=True, exist_ok=True)
    im = Image.open(ruta).convert("RGB")
    im.thumbnail((2000, 2000))
    destino = IMG_PROPIAS / f"foto_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    im.save(destino, "JPEG", quality=90)
    return str(destino)


def _nota(gid):
    g = storage.get_grupo(gid)
    return g, (json.loads(g["nota_json"]) if g and g["nota_json"] else {})


def _guardar(gid, nota):
    storage.actualizar_grupo(gid, nota_json=json.dumps(nota, ensure_ascii=False))


def poner_foto(gid, ruta_archivo):
    g, n = _nota(gid)
    n["_IMAGEN"] = importar_foto(ruta_archivo)
    n.pop("_SIN_FOTO", None)
    n.pop("_FOTO_ORIGINAL", None)
    _guardar(gid, n)


def quitar_foto(gid):
    g, n = _nota(gid)
    n.pop("_IMAGEN", None)
    n["_SIN_FOTO"] = True
    _guardar(gid, n)


def poner_seccion(gid, seccion):
    g, n = _nota(gid)
    n["SECCION"] = seccion
    n["_SECCION_MANUAL"] = True
    _guardar(gid, n)


def crear_nota_propia(seccion, ante, titular, lugar, texto, foto=None, pag=None, video=None) -> int:
    """Crea una noticia propia del diario (no viene de Facebook). Devuelve su id.
    video: post de un video de Facebook del que salió la nota; queda unido a ella, y si se deja su
    misma foto (la miniatura), la nota la usa con el crédito del medio y pasa por la limpieza."""
    parrafos = [p.strip() for p in texto.replace("\r", "").split("\n\n") if p.strip()] or [texto.strip()]
    nota = {"ANTETITULO": ante.strip(), "TITULAR": titular.strip(), "BAJADA": "", "LUGAR": lugar.strip(),
            "PIE_FOTO": "", "CUERPO": "\n\n".join(parrafos), "DATOS": [], "SECCION": seccion,
            "_SECCION_MANUAL": True, "_PROPIA": True, "IMPACTO": 6, "_origen": "propia",
            "LINKS": [], "_EDITADO": datetime.now().isoformat(timespec="seconds")}
    foto_video = bool(video is not None and foto and video["imagen"] and Path(foto) == Path(video["imagen"]))
    if foto and not foto_video:
        nota["_IMAGEN"] = importar_foto(foto)
    elif video is not None and not foto:
        nota["_SIN_FOTO"] = True
    if video is not None:
        nota["_VIDEO"] = video["permalink"] or ""
    gid = storage.crear_grupo(AMBITO.get(seccion, "REGIONAL"), TEMA.get(seccion, "Sociedad"), titular.strip())
    storage.actualizar_grupo(gid, nota_json=json.dumps(nota, ensure_ascii=False), estado_pdf="nuevo",
                             en_periodico=1, fecha_hecho=storage.now_iso(), medios="Altiplano al Día",
                             n_fuentes=0, nota_pendiente=0, titular=titular.strip())
    if video is not None:
        storage.video_con_nota(video["id"], gid)
    if pag:
        fijas = consolidado.leer_fijas()
        fijas[gid] = pag
        consolidado.guardar_fijas(fijas)
    return gid


def cupos_y_llenos(mapa) -> list:
    """[(sección, notas puestas, espacios)] según las zonas de la última vista previa."""
    from .maqueta import NOTICIAS
    llenos, total = {}, {}
    for z in mapa.get("zonas", []):
        sec, rol = z.get("seccion"), z.get("rol")
        if not sec or not rol or rol == "aviso" or sec not in NOTICIAS:
            continue
        total[sec] = total.get(sec, 0) + 1
        if z.get("gid"):
            llenos[sec] = llenos.get(sec, 0) + 1
    return [(s, llenos.get(s, 0), total[s]) for s in NOTICIAS if s in total]


class NotaPropia(tk.Toplevel):
    """Formulario para escribir una nota propia (o pegar un borrador) y ponerla en el periódico."""

    def __init__(self, vista, pag=None, seccion=None, rol=None, video=None, al_guardar=None):
        """vista: la vista previa (o la ventana principal, si se abre desde 🎬 Videos).
        video: post de un video de Facebook; su texto llega como borrador y su miniatura como foto."""
        super().__init__(vista)
        from .config import nombre_medio
        from .editor import ajustar_ventana
        from .maqueta import LARGO, NOTICIAS
        self.vista, self.pag, self.rol, self.video, self.al_guardar = vista, pag, rol, video, al_guardar
        self.app = getattr(vista, "app", vista)
        self.title(("Nota desde un video" if video is not None else "Nota propia") + " — Altiplano al Día"
                   + (f" (página {pag})" if pag else ""))
        ajustar_ventana(self, 820, 720)
        self.transient(vista)
        self.foto = video["imagen"] if video is not None and video["imagen"] else None
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        fila = 0
        if video is not None:
            medio = nombre_medio(video["pagina"], self.app.cfg)
            enlace = video["permalink"] or ""
            tk.Label(f, text=f"🎬 Video de {medio}. Míralo y escribe lo que se ve y se dice: el texto de abajo "
                             "es solo lo que el medio puso junto al video. No pongas nada que no esté en el video.",
                     bg="#FFF8E1", fg="#5B4200", font=(F, 9, "bold"), anchor="w", justify="left",
                     wraplength=760, padx=8, pady=6).grid(row=fila, column=0, columnspan=2, sticky="ew")
            fila += 1
            if enlace:
                lk = tk.Label(f, text="▶ Abrir el video en Facebook", fg="#1D4ED8", cursor="hand2",
                              font=(F, 9, "underline"))
                lk.grid(row=fila, column=0, columnspan=2, sticky="w", pady=(2, 6))
                lk.bind("<Button-1>", lambda e: webbrowser.open(enlace))
                fila += 1
        ttk.Label(f, text="Sección:").grid(row=fila, column=0, sticky="w")
        self.sec = ttk.Combobox(f, state="readonly", values=NOTICIAS, width=20)
        self.sec.set(seccion if seccion in NOTICIAS else NOTICIAS[0])
        self.sec.grid(row=fila, column=1, sticky="w", pady=3)
        fila += 1
        self.campos = {}
        for clave, etq in (("ante", "Antetítulo:"), ("titular", "Titular:"), ("lugar", "Lugar (ciudad):")):
            ttk.Label(f, text=etq).grid(row=fila, column=0, sticky="w")
            e = ttk.Entry(f)
            e.grid(row=fila, column=1, sticky="ew", pady=3)
            self.campos[clave] = e
            fila += 1
        largo = ""
        if rol in LARGO:
            mn, mx = LARGO[rol]
            largo = f"   Este espacio pide unos {mn}–{mx} caracteres."
        ttk.Label(f, text="Texto (puede ser un borrador o apuntes; párrafos separados por una línea en blanco)."
                          + largo, foreground="#4B5563").grid(row=fila, column=0, columnspan=2, sticky="w", pady=(8, 2))
        fila += 1
        self.texto = tk.Text(f, wrap="word", font=("Georgia", 11), height=14, undo=True)
        self.texto.grid(row=fila, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(fila, weight=1)
        fila += 1
        self.lbl_cont = ttk.Label(f, text="0 caracteres", foreground="#6B7280")
        self.lbl_cont.grid(row=fila, column=1, sticky="e")
        self.texto.bind("<KeyRelease>", lambda e: self.lbl_cont.configure(
            text=f"{len(self.texto.get('1.0', 'end').strip())} caracteres"))
        fila += 1
        fb = ttk.Frame(f)
        fb.grid(row=fila, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Button(fb, text="🖼 Elegir foto…", command=self._elegir_foto).pack(side="left")
        self.lbl_foto = ttk.Label(fb, text="Sin foto", foreground="#6B7280")
        self.lbl_foto.pack(side="left", padx=8)
        if self.foto:
            self.lbl_foto.configure(text="Miniatura del video (se le quitarán logos y textos)")
            ttk.Button(fb, text="Sin foto", command=self._sin_foto).pack(side="left")
        if video is not None:
            self.texto.insert("1.0", (video["texto"] or "").strip())
            self.lbl_cont.configure(text=f"{len(self.texto.get('1.0', 'end').strip())} caracteres")
        fila += 1
        self.redactar = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Que Claude la redacte con el estilo del diario y al largo del espacio "
                                "(solo con lo que escribiste: no agrega datos)",
                        variable=self.redactar).grid(row=fila, column=0, columnspan=2, sticky="w")
        pie = ttk.Frame(self, padding=(12, 0, 12, 12))
        pie.pack(fill="x")
        ttk.Button(pie, text="＋ Poner en el periódico", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(pie, text="Cancelar", command=self.destroy).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.campos["titular"].focus_set()
        self.grab_set()

    def _sin_foto(self):
        self.foto = None
        self.lbl_foto.configure(text="Sin foto")

    def _elegir_foto(self):
        ruta = filedialog.askopenfilename(parent=self, title="Foto de la nota",
                                          filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if ruta:
            self.foto = ruta
            self.lbl_foto.configure(text=Path(ruta).name)

    def _ok(self):
        titular = self.campos["titular"].get().strip()
        texto = self.texto.get("1.0", "end").strip()
        if not titular or not texto:
            messagebox.showwarning("Nota propia", "Escribe al menos el titular y el texto.", parent=self)
            return
        try:
            gid = crear_nota_propia(self.sec.get(), self.campos["ante"].get(), titular,
                                    self.campos["lugar"].get(), texto, self.foto, self.pag, self.video)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Nota propia", f"No se pudo guardar: {e}", parent=self)
            return
        vista, app, rol, redactar = self.vista, self.app, self.rol or "secundaria", self.redactar.get()
        regenerar = getattr(vista, "generar", lambda: None)
        if self.al_guardar:
            self.al_guardar(gid)
        self.destroy()
        app._refresh_dashboard()
        if not redactar:
            app._aviso("Nota propia añadida al periódico.", "ok")
            regenerar()
            return
        app._aviso("Nota guardada. Claude la está redactando al largo del espacio…", "info", 6000)

        def run():
            from .redaccion import ajustar
            try:
                g, n = _nota(gid)
                n.pop("_EDITADO", None)               # (para que la redacción a medida pueda tocarla)
                _guardar(gid, n)
                ajustar(app._cfg_actual(), gid, rol)
                g, n = _nota(gid)
                n["_EDITADO"] = datetime.now().isoformat(timespec="seconds")
                _guardar(gid, n)
                app.q.put(("aviso_ok", "Nota propia redactada y puesta en el periódico."))
            except Exception as e:  # noqa: BLE001
                app.q.put(("error", f"Nota propia: Claude no pudo redactarla ({e}); quedó tal cual."))
            finally:
                app.en_ui(regenerar)
                app.en_ui(app._refresh_dashboard)

        threading.Thread(target=run, daemon=True).start()
