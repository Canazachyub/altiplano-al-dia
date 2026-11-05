"""Ventana 📢 Avisos: biblioteca de avisos para los espacios libres (texto o imagen), con vista previa."""
import io
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from . import avisos, diseno

F = "Segoe UI"
MUTED = "#6B7280"


def render_aviso(a: dict, w_pt=170, h_pt=200, escala=1.4):
    """Imagen (PIL) del aviso tal como saldrá en el PDF."""
    import pypdfium2 as pdfium
    from reportlab.pdfgen.canvas import Canvas
    d = diseno.cargar()
    diseno.aplicar_fuentes(d)
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(w_pt + 8, h_pt + 8))
    avisos.dibujar(c, a, (4, 4, w_pt + 4, h_pt + 4), d)
    c.save()
    doc = pdfium.PdfDocument(buf.getvalue())
    im = doc[0].render(scale=escala).to_pil()
    doc.close()
    return im


class GestorAvisos(tk.Toplevel):
    def __init__(self, master, al_guardar=None):
        super().__init__(master)
        self.title("📢 Avisos para los espacios libres")
        from .editor import ajustar_ventana
        ajustar_ventana(self, 1040, 680)
        self.minsize(900, 560)
        self.transient(master)
        self.al_guardar = al_guardar
        self.d = avisos.cargar()
        self.sel = None
        self._ph = None

        top = ttk.Frame(self, padding=(12, 10))
        top.pack(fill="x")
        self.auto = tk.BooleanVar(value=self.d.get("auto", True))
        ttk.Checkbutton(top, text="Rellenar solos los espacios libres con los avisos marcados «automático»",
                        variable=self.auto).pack(side="left")
        f = ttk.Frame(top)
        f.pack(side="right")
        ttk.Label(f, text="Número para todos:").pack(side="left")
        self.num_todos = ttk.Entry(f, width=16)
        self.num_todos.pack(side="left", padx=4)
        ttk.Button(f, text="Aplicar a todos", command=self._numero_todos).pack(side="left")

        pie = ttk.Frame(self, padding=12)
        pie.pack(side="bottom", fill="x")
        ttk.Button(pie, text="💾 Guardar y ver en el periódico  (Ctrl+S)", style="Accent.TButton",
                   command=self._guardar).pack(side="right")
        ttk.Button(pie, text="Cerrar  (Esc)", command=self._cerrar).pack(side="right", padx=6)
        self.bind("<Control-s>", lambda e: self._guardar())
        self.bind("<Control-S>", lambda e: self._guardar())
        self.bind("<Escape>", lambda e: self._cerrar())
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        cuerpo = ttk.Frame(self, padding=(12, 0))
        cuerpo.pack(fill="both", expand=True)
        izq = ttk.Frame(cuerpo)
        izq.pack(side="left", fill="y")
        self.lista = tk.Listbox(izq, width=30, height=18, font=(F, 10), activestyle="none", exportselection=False)
        self.lista.pack(fill="y", expand=True)
        self.lista.bind("<<ListboxSelect>>", lambda e: self._elegir())
        bl = ttk.Frame(izq)
        bl.pack(fill="x", pady=6)
        ttk.Button(bl, text="＋ Aviso de texto", command=lambda: self._nuevo("texto")).pack(fill="x")
        ttk.Button(bl, text="＋ Aviso con imagen", command=lambda: self._nuevo("imagen")).pack(fill="x", pady=3)
        ttk.Button(bl, text="Duplicar", command=self._duplicar).pack(fill="x")
        ttk.Button(bl, text="Eliminar", command=self._eliminar).pack(fill="x", pady=3)

        self.form = ttk.Frame(cuerpo, padding=(16, 0))
        self.form.pack(side="left", fill="both", expand=True)
        self.form.columnconfigure(1, weight=1)
        self.campos = {}
        filas = [("nombre", "Nombre (solo para ti)"), ("titulo", "Título"), ("mensaje", "Mensaje"),
                 ("telefono", "Teléfono / WhatsApp")]
        for i, (k, et) in enumerate(filas):
            ttk.Label(self.form, text=et).grid(row=i, column=0, sticky="w", pady=4)
            e = ttk.Entry(self.form, font=(F, 10))
            e.grid(row=i, column=1, sticky="ew", pady=4)
            e.bind("<KeyRelease>", lambda ev: self._vista_diferida())
            self.campos[k] = e
        r = len(filas)
        ttk.Label(self.form, text="Icono").grid(row=r, column=0, sticky="w", pady=4)
        self.icono = ttk.Combobox(self.form, state="readonly", values=list(avisos.ICONOS.values()))
        self.icono.grid(row=r, column=1, sticky="w", pady=4)
        self.icono.bind("<<ComboboxSelected>>", lambda e: self._vista())
        ttk.Label(self.form, text="Imagen").grid(row=r + 1, column=0, sticky="w", pady=4)
        fi = ttk.Frame(self.form)
        fi.grid(row=r + 1, column=1, sticky="w")
        ttk.Button(fi, text="Elegir imagen…", command=self._imagen).pack(side="left")
        self.lbl_img = ttk.Label(fi, text="", foreground=MUTED)
        self.lbl_img.pack(side="left", padx=8)
        self.es_auto = tk.BooleanVar()
        ttk.Checkbutton(self.form, text="Automático: usar este aviso para rellenar espacios libres",
                        variable=self.es_auto).grid(row=r + 2, column=1, sticky="w", pady=6)
        self.aviso_estado = ttk.Label(self.form, text="", foreground="#B3261E")
        self.aviso_estado.grid(row=r + 3, column=1, sticky="w")
        ttk.Label(self.form, text="Así se verá (columna angosta):", foreground=MUTED).grid(
            row=r + 4, column=0, columnspan=2, sticky="w", pady=(12, 2))
        self.prev = tk.Label(self.form, bg="#E9EDF4")
        self.prev.grid(row=r + 5, column=0, columnspan=2, sticky="w")

        self._job = None
        self._refrescar_lista(0)
        self._inicial = self._foto_estado()
        self.grab_set()

    # ------------------------------------------------------------------ cambios sin guardar
    def _foto_estado(self):
        self._volcar()
        return json.dumps([self.d, self.auto.get()], sort_keys=True, ensure_ascii=False)

    def hay_cambios(self):
        return self._foto_estado() != self._inicial

    def _cerrar(self):
        if self.hay_cambios():
            r = messagebox.askyesnocancel("Avisos", "Hiciste cambios en los avisos que no guardaste.\n\n"
                                                    "¿Guardarlos antes de cerrar?", parent=self)
            if r is None:
                return
            if r:
                self._guardar()
                return
        self.destroy()

    # ------------------------------------------------------------------
    def _refrescar_lista(self, idx=None):
        self.lista.delete(0, "end")
        for a in self.d["avisos"]:
            marca = "✔" if avisos.listo(a) else "⚠"
            auto = " · auto" if a.get("auto") else ""
            self.lista.insert("end", f" {marca}  {a.get('nombre') or a.get('titulo')}{auto}")
        if self.d["avisos"]:
            i = idx if idx is not None else 0
            self.lista.selection_clear(0, "end")
            self.lista.selection_set(min(i, len(self.d["avisos"]) - 1))
            self._elegir()

    def _elegir(self):
        s = self.lista.curselection()
        if not s:
            return
        self._volcar()
        self.sel = self.d["avisos"][s[0]]
        a = self.sel
        for k, e in self.campos.items():
            e.delete(0, "end")
            e.insert(0, a.get(k, ""))
        self.icono.set(avisos.ICONOS.get(a.get("icono", ""), "Sin icono"))
        self.es_auto.set(bool(a.get("auto")))
        self.lbl_img.configure(text=(a.get("imagen") or "").split("\\")[-1] or "(ninguna)")
        for k in ("titulo", "mensaje", "telefono"):
            self.campos[k].configure(state="disabled" if a.get("tipo") == "imagen" else "normal")
        self._vista()

    def _volcar(self):
        """Pasa lo del formulario al aviso seleccionado."""
        if not self.sel:
            return
        for k, e in self.campos.items():
            self.sel[k] = e.get().strip()
        inv = {v: k for k, v in avisos.ICONOS.items()}
        self.sel["icono"] = inv.get(self.icono.get(), "")
        self.sel["auto"] = self.es_auto.get()

    def _vista_diferida(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(400, self._vista)

    def _vista(self):
        self._volcar()
        if not self.sel:
            return
        s = self.lista.curselection()
        if s:
            a = self.sel
            texto = f" {'✔' if avisos.listo(a) else '⚠'}  {a.get('nombre') or a.get('titulo')}" + \
                    (" · auto" if a.get("auto") else "")
            if self.lista.get(s[0]) != texto:
                self.lista.delete(s[0])
                self.lista.insert(s[0], texto)
                self.lista.selection_set(s[0])
        if not avisos.listo(self.sel):
            self.aviso_estado.configure(text="⚠ Falta el teléfono (o la imagen): así no se imprime en el periódico.")
        else:
            self.aviso_estado.configure(text="")
        try:
            a = dict(self.sel)
            if a.get("tipo") != "imagen" and not a.get("telefono"):
                a["telefono"] = "999 999 999"
            if a.get("tipo") == "imagen" and not a.get("imagen"):
                self.prev.configure(image="", text="(elige una imagen)", width=30, height=10)
                return
            self._ph = ImageTk.PhotoImage(render_aviso(a))
            self.prev.configure(image=self._ph, text="", width=0, height=0)
        except Exception as e:  # noqa: BLE001
            self.prev.configure(image="", text=f"No se pudo dibujar: {e}")

    def _nuevo(self, tipo):
        self._volcar()
        a = {"id": avisos.nuevo_id(), "nombre": "Nuevo aviso" if tipo == "texto" else "Anuncio con imagen",
             "tipo": tipo, "auto": False, "icono": "telefono" if tipo == "texto" else "",
             "titulo": "TÍTULO DEL AVISO" if tipo == "texto" else "", "mensaje": "Escribe aquí el mensaje",
             "telefono": self.num_todos.get().strip(), "imagen": ""}
        self.d["avisos"].append(a)
        self._refrescar_lista(len(self.d["avisos"]) - 1)
        if tipo == "imagen":
            self._imagen()

    def _duplicar(self):
        if not self.sel:
            return
        self._volcar()
        a = dict(self.sel, id=avisos.nuevo_id(), nombre=(self.sel.get("nombre") or "") + " (copia)")
        self.d["avisos"].append(a)
        self._refrescar_lista(len(self.d["avisos"]) - 1)

    def _eliminar(self):
        if not self.sel or not messagebox.askyesno("Avisos", "¿Eliminar este aviso?", parent=self):
            return
        aid = self.sel["id"]
        self.d["avisos"] = [a for a in self.d["avisos"] if a["id"] != aid]
        self.d["colocados"] = {k: v for k, v in self.d["colocados"].items() if v != aid}
        self.sel = None
        self._refrescar_lista(0)

    def _imagen(self):
        ruta = filedialog.askopenfilename(parent=self, title="Imagen del aviso",
                                          filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if ruta and self.sel:
            try:
                self.sel["imagen"] = avisos.importar_imagen(ruta)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("Avisos", f"No se pudo abrir la imagen:\n{e}", parent=self)
                return
            self.lbl_img.configure(text=self.sel["imagen"].split("\\")[-1])
            self._vista()

    def _numero_todos(self):
        num = self.num_todos.get().strip()
        if not num:
            return
        self._volcar()
        for a in self.d["avisos"]:
            if a.get("tipo") != "imagen":
                a["telefono"] = num
        s = self.lista.curselection()
        self._refrescar_lista(s[0] if s else 0)

    def _guardar(self):
        self._volcar()
        self.d["auto"] = self.auto.get()
        try:
            avisos.guardar(self.d)
        except OSError as e:
            messagebox.showerror("Avisos", f"No se pudieron guardar los avisos:\n{e}", parent=self)
            return
        self.destroy()
        if self.al_guardar:
            self.al_guardar()
