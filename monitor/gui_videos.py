"""Pestaña 🎬 Videos: videos y transmisiones de Facebook que esperan el criterio del equipo.

De un video el script solo puede leer el texto que el medio puso al lado (no lo transcribe), así que
no se mandan a la IA. Aquí se ven, se abren en Facebook y se decide:
  ✍ Hacer nota   → se escribe a mano (con la miniatura como foto, sin logos) y va al periódico/web;
  🤖 Que la IA la redacte → solo si el texto del post ya cuenta la noticia completa;
  ✖ Descartar    → no vale nota (programas en vivo, entrevistas sin hecho, promociones…).
"""
import logging
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

from . import storage
from .config import nombre_medio
from .gui_inicio import Tip

F = "Segoe UI"
ESTADO = {"video": "Por revisar", "video_nota": "✔ Con nota", "video_descartado": "✖ Descartado",
          "pendiente": "🤖 A la IA", "procesado": "🤖 Redactado", "error": "Error"}


def _fecha(iso):
    if not iso:
        return ""
    return f"{iso[8:10]}/{iso[5:7]} {iso[11:16]}"


class VideosTab(ttk.Frame):
    def __init__(self, nb, app):
        super().__init__(nb, padding=10)
        self.app, self.nb = app, nb
        self._foto = None
        self._ids_ia = set()            # mandados a la IA desde aquí (para seguir mostrándolos)

        cab = ttk.Frame(self)
        cab.pack(fill="x", pady=(0, 6))
        ttk.Label(cab, text="Videos y transmisiones", font=(F, 12, "bold")).pack(side="left")
        ttk.Label(cab, text="De los videos solo se lee el texto que los acompaña: aquí decides cuáles valen una "
                            "nota. Míralo en Facebook y escríbela a mano.", style="Muted.TLabel").pack(side="left",
                                                                                                     padx=12)
        ttk.Label(cab, text="Mostrar:").pack(side="right", padx=(0, 4))
        self.filtro = ttk.Combobox(cab, state="readonly", width=14, values=["Por revisar", "Todos"])
        self.filtro.set("Por revisar")
        self.filtro.bind("<<ComboboxSelected>>", lambda e: self.recargar())
        self.filtro.pack(side="right", padx=(0, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="▶ Ver en Facebook", command=self._abrir).pack(side="left")
        b = ttk.Button(bar, text="✍ Hacer nota", style="Accent.TButton", command=self._hacer_nota)
        b.pack(side="left", padx=6)
        Tip(b, "Abre el formulario de nota propia con el texto del video como borrador y su miniatura como "
               "foto (se le quitan logos y textos). Claude puede redactarla con el estilo del diario, solo con "
               "lo que escribas.")
        b = ttk.Button(bar, text="🤖 Que la IA la redacte", command=self._a_la_ia)
        b.pack(side="left")
        Tip(b, "Solo cuando el texto del post ya cuenta la noticia completa: se redacta como cualquier otra "
               "publicación y entra al agrupado.")
        ttk.Button(bar, text="✖ Descartar", command=lambda: self._marcar("video_descartado")).pack(side="left",
                                                                                                padx=6)
        ttk.Button(bar, text="↺ Devolver a por revisar", command=lambda: self._marcar("video")).pack(side="left")
        self.lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl.pack(side="right")

        cuerpo = ttk.PanedWindow(self, orient="horizontal")
        cuerpo.pack(fill="both", expand=True)
        izq = ttk.Frame(cuerpo)
        cols = ("medio", "fecha", "texto", "estado")
        self.tree = ttk.Treeview(izq, columns=cols, show="headings", selectmode="extended")
        for c, t, w, st in (("medio", "Medio", 150, False), ("fecha", "Fecha", 90, False),
                            ("texto", "Texto del video", 460, True), ("estado", "Estado", 105, False)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=st, anchor="w")
        sb = ttk.Scrollbar(izq, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("hecho", foreground="#15803D")
        self.tree.tag_configure("fuera", foreground="#9CA3AF")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._detalle())
        self.tree.bind("<Double-1>", lambda e: self._abrir())
        cuerpo.add(izq, weight=3)

        der = ttk.Frame(cuerpo, padding=(10, 0, 0, 0))
        self.img = tk.Label(der, bg="#E5E7EB", text="(sin miniatura)", fg="#6B7280", width=46, height=13)
        self.img.pack(fill="x")
        self.det = tk.Text(der, wrap="word", font=("Georgia", 10), height=12, relief="flat", bg="#FAFAF7",
                           padx=8, pady=6)
        self.det.pack(fill="both", expand=True, pady=(8, 0))
        cuerpo.add(der, weight=2)
        self.recargar()

    # ------------------------------------------------------------------ datos
    def _filas(self):
        todos = self.filtro.get() == "Todos"
        filas = list(storage.videos(todos=todos))
        vistos = {r["id"] for r in filas}
        for pid in list(self._ids_ia):              # los mandados a la IA siguen a la vista un rato
            r = storage.get_post(pid)
            if r and r["id"] not in vistos:
                filas.append(r)
        return filas

    def recargar(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._filas_por_id = {}
        for r in self._filas():
            self._filas_por_id[r["id"]] = r
            txt = " ".join((r["texto"] or "").split())[:160] or "(sin texto)"
            tag = ("hecho",) if r["estado"] in ("video_nota", "procesado") else \
                  ("fuera",) if r["estado"] == "video_descartado" else ()
            self.tree.insert("", "end", iid=r["id"], tags=tag, values=(
                nombre_medio(r["pagina"], self.app.cfg), _fecha(r["publicado"] or r["detectado"]), txt,
                ESTADO.get(r["estado"], r["estado"])))
        quedan = [s for s in sel if self.tree.exists(s)]
        if quedan:
            self.tree.selection_set(quedan)
        self.actualizar_titulo()

    def actualizar_titulo(self):
        n = storage.n_videos_por_revisar()
        self.lbl.configure(text=f"{n} por revisar")
        try:
            self.nb.tab(self, text=f"  🎬 Videos ({n})  " if n else "  🎬 Videos  ")
        except tk.TclError:
            pass

    def _sel(self):
        return [self._filas_por_id[i] for i in self.tree.selection() if i in self._filas_por_id]

    def _detalle(self):
        filas = self._sel()
        self.det.delete("1.0", "end")
        self._foto = None
        self.img.configure(image="", text="(sin miniatura)")
        if not filas:
            return
        r = filas[0]
        self.det.insert("1.0", f"{nombre_medio(r['pagina'], self.app.cfg)} · {_fecha(r['publicado'] or r['detectado'])}"
                               f"\n{r['permalink'] or ''}\n\n{(r['texto'] or '(sin texto)').strip()}")
        if r["imagen"]:
            try:
                im = Image.open(r["imagen"])
                im.thumbnail((380, 240))
                self._foto = ImageTk.PhotoImage(im)
                self.img.configure(image=self._foto, text="", width=im.width, height=im.height)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ acciones
    def _uno(self):
        filas = self._sel()
        if not filas:
            self.app._aviso("Elige un video de la lista.", "warn")
            return None
        return filas[0]

    def _abrir(self):
        r = self._uno()
        if r and r["permalink"]:
            webbrowser.open(r["permalink"])

    def _hacer_nota(self):
        r = self._uno()
        if not r:
            return
        if r["estado"] == "video_nota":
            self.app._aviso("Ese video ya tiene su nota (está en 🗂 Noticias).", "warn")
            return
        from .mesa import NotaPropia
        NotaPropia(self.app, video=r, al_guardar=lambda gid: self.app.after(50, self.recargar))

    def _marcar(self, estado):
        filas = [r for r in self._sel() if r["estado"] in storage.ESTADOS_VIDEO]
        if not filas:
            self.app._aviso("Elige uno o más videos de la lista.", "warn")
            return
        for r in filas:
            if r["estado"] == "video_nota" and estado != "video_nota":
                continue                       # la nota ya existe: se maneja desde 🗂 Noticias
            storage.update_post(r["id"], estado=estado)
        self.recargar()

    def _a_la_ia(self):
        from .analisis import fase2_lock
        filas = [r for r in self._sel() if r["estado"] in ("video", "video_descartado")]
        if not filas:
            self.app._aviso("Elige uno o más videos por revisar.", "warn")
            return
        sin_texto = [r for r in filas if not (r["texto"] or "").strip()]
        if sin_texto:
            self.app._aviso("Hay videos sin texto: la IA no tiene de qué escribir. Haz la nota a mano.", "warn")
            return
        for r in filas:
            storage.update_post(r["id"], estado="pendiente", error=None)
            self._ids_ia.add(r["id"])
        self.recargar()
        if self.app.running or fase2_lock.locked():
            self.app._aviso("Hay una revisión en curso: se redactarán al terminar o en la próxima revisión.", "info")
            return
        cfg, app, ids = self.app._cfg_actual(), self.app, [r["id"] for r in filas]
        app._aviso(f"Redactando {len(ids)} video(s) con la IA…", "info")

        def run():
            from .gui import _reproceso_lock
            from .worker import procesar_post
            try:
                with _reproceso_lock:
                    for pid in ids:
                        procesar_post(pid, cfg, app._emit)
                app.q.put(("aviso_ok", "Listo. Pulsa «⟳ Agrupar noticias» para llevarlas a 🗂 Noticias."))
            except Exception as e:  # noqa: BLE001
                logging.getLogger("monitor").exception("Videos a la IA")
                app.q.put(("error", f"Videos: {e}"))
            finally:
                app.en_ui(self.recargar)

        threading.Thread(target=run, daemon=True).start()
