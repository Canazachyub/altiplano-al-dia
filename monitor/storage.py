"""Base SQLite (fuente principal): publicaciones, grupos de noticias y consolidados."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,   -- id canónico (pfbid, id numérico) o hash del texto
    text_hash     TEXT,
    pagina        TEXT NOT NULL,
    permalink     TEXT,
    enlace_ext    TEXT,
    texto         TEXT,
    publicado     TEXT,               -- ISO si se pudo leer, si no NULL
    detectado     TEXT NOT NULL,
    estado        TEXT NOT NULL,      -- base | pendiente | procesado | error | video (por revisar)
                                      -- | video_nota (se hizo su nota) | video_descartado
    titular       TEXT,
    ia_json       TEXT,
    pdf_path      TEXT,
    error         TEXT,
    visto         INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_hash ON posts(text_hash);
CREATE INDEX IF NOT EXISTS ix_pagina ON posts(pagina);

-- Fase 2: un grupo = un mismo hecho publicado por uno o varios medios
CREATE TABLE IF NOT EXISTS grupos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    creado        TEXT NOT NULL,
    actualizado   TEXT NOT NULL,
    ambito        TEXT,
    tema          TEXT,
    titular       TEXT,
    resumen       TEXT,               -- 1 frase, se usa para comparar con noticias nuevas
    nota_json     TEXT,               -- nota combinada (JSON)
    n_fuentes     INTEGER DEFAULT 0,
    medios        TEXT,               -- "La República, Diario Los Andes"
    fecha_hecho   TEXT,               -- publicación más reciente del grupo
    estado_pdf    TEXT DEFAULT 'nuevo',  -- nuevo | actualizacion | publicado
    ultimo_pdf    INTEGER,
    nota_pendiente INTEGER DEFAULT 1  -- 1 = hay que (re)generar la nota combinada
);
CREATE TABLE IF NOT EXISTS consolidados (
    n             INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha         TEXT NOT NULL,
    path          TEXT NOT NULL,
    desde         TEXT,
    hasta         TEXT,
    n_noticias    INTEGER
);
"""

# columnas añadidas a posts en la Fase 2 (migración sobre bases de la Fase 1)
POSTS_EXTRA = {"ambito": "TEXT", "tema": "TEXT", "grupo_id": "INTEGER", "resumen": "TEXT",
               "consolidado_n": "INTEGER", "imagen": "TEXT"}


class _Conexion(sqlite3.Connection):
    """Conexión que, usada con «with», confirma (o deshace) Y ADEMÁS se cierra.
    El «with» de sqlite3 normal solo hace commit y deja la conexión abierta."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout = espera ante «database is locked» (hilo de revisión + GUI escriben a la vez)
    c = sqlite3.connect(DB_PATH, timeout=30, factory=_Conexion)
    c.row_factory = sqlite3.Row
    return c


def _add_column(c, tabla, col, typ):
    """ALTER TABLE idempotente (si otro hilo ya la agregó, no falla)."""
    try:
        c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {typ}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def init_db():
    with _conn() as c:
        try:
            # WAL: la GUI puede leer mientras el hilo de revisión escribe (menos «database is locked»).
            # Es persistente en el archivo; si el disco no lo admite, se sigue en modo normal.
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(posts)")}
        for col, typ in POSTS_EXTRA.items():
            if col not in cols:
                _add_column(c, "posts", col, typ)
        gcols = {r["name"] for r in c.execute("PRAGMA table_info(grupos)")}
        if "en_periodico" not in gcols:   # 1 = va al periódico, 0 = la excluyó el editor
            _add_column(c, "grupos", "en_periodico", "INTEGER DEFAULT 1")
        # Web: en_web 1 = va a la web; web_estado '' (nunca subida) | 'publicada' | 'actualizar' (se editó
        # después de subirla); web_slug fija la dirección de la nota aunque luego cambie el titular.
        for col, typ in (("en_web", "INTEGER DEFAULT 0"), ("web_estado", "TEXT DEFAULT ''"),
                         ("web_publicado", "TEXT"), ("web_slug", "TEXT"),
                         ("web_fijada", "INTEGER DEFAULT 0")):
            if col not in gcols:
                _add_column(c, "grupos", col, typ)
        # índices de las columnas usadas en WHERE / ORDER BY (idempotentes)
        c.execute("CREATE INDEX IF NOT EXISTS ix_grupo ON posts(grupo_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_estado ON posts(estado)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_g_estado_pdf ON grupos(estado_pdf)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_g_ultimo_pdf ON grupos(ultimo_pdf)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_g_nota_pend ON grupos(nota_pendiente)")
        # videos y transmisiones que aún esperaban a la IA: pasan a la bandeja de videos por revisar
        c.execute("UPDATE posts SET estado='video', error=NULL WHERE estado IN ('pendiente','error') "
                  "AND grupo_id IS NULL AND " + _SQL_VIDEO)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def text_hash(pagina: str, texto: str) -> str:
    norm = re.sub(r"\s+", " ", (texto or "").lower()).strip()[:300]
    return hashlib.sha1(f"{pagina}|{norm}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ posts (Fase 1)
def is_known(post_id: str, thash: str) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM posts WHERE id=? OR (text_hash=? AND text_hash IS NOT NULL) LIMIT 1",
            (post_id, thash),
        ).fetchone()
        return row is not None


def page_has_history(pagina: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM posts WHERE pagina=? LIMIT 1", (pagina,)).fetchone() is not None


def insert_post(p: dict, estado: str) -> bool:
    """Inserta el post; devuelve False si ya existía (mismo id)."""
    with _conn() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO posts
               (id, text_hash, pagina, permalink, enlace_ext, texto, publicado, detectado, estado, visto)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                p["id"], p["text_hash"], p["pagina"], p.get("permalink"), p.get("enlace_ext"),
                p.get("texto"), p.get("publicado"), now_iso(), estado, 1 if estado == "base" else 0,
            ),
        )
        return cur.rowcount == 1


# ------------------------------------------------------------------ videos y transmisiones
# De un video solo se lee el texto que lo acompaña (no se transcribe): no van a la IA sino a la
# pestaña 🎬 Videos, donde el equipo decide si vale una nota y la escribe a mano.
_RE_VIDEO = re.compile(r"facebook\.com/(?:[^?#]*/)?(?:videos|reel|watch)\b|fb\.watch/", re.I)
_SQL_VIDEO = ("(id LIKE 'videos:%' OR id LIKE 'reel:%' OR permalink LIKE '%/videos/%' "
              "OR permalink LIKE '%/reel/%' OR permalink LIKE '%/watch%')")
ESTADOS_VIDEO = ("video", "video_nota", "video_descartado")


def es_video(p) -> bool:
    p = dict(p)
    pid = str(p.get("id") or "")
    return pid.startswith(("videos:", "reel:")) or bool(_RE_VIDEO.search(p.get("permalink") or ""))


def videos(todos: bool = False, limite: int = 300):
    """Videos por revisar (o también los ya resueltos), del más nuevo al más viejo."""
    est = ESTADOS_VIDEO if todos else ("video",)
    with _conn() as c:
        return c.execute(f"SELECT * FROM posts WHERE estado IN ({','.join('?' * len(est))}) "
                         "ORDER BY COALESCE(publicado, detectado) DESC LIMIT ?", (*est, limite)).fetchall()


def n_videos_por_revisar() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM posts WHERE estado='video'").fetchone()[0]


def video_con_nota(post_id: str, gid: int) -> None:
    """El video queda unido a la nota hecha a mano (su foto y su medio sirven para el crédito)."""
    with _conn() as c:
        c.execute("UPDATE posts SET estado='video_nota', grupo_id=? WHERE id=?", (gid, post_id))


def update_post(post_id: str, **fields) -> None:
    if "ia_json" in fields and not isinstance(fields["ia_json"], str):
        fields["ia_json"] = json.dumps(fields["ia_json"], ensure_ascii=False)
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE posts SET {cols} WHERE id=?", (*fields.values(), post_id))


def get_post(post_id: str):
    with _conn() as c:
        return c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def pending_posts():
    with _conn() as c:
        return c.execute("SELECT * FROM posts WHERE estado IN ('pendiente','error') ORDER BY detectado").fetchall()


def list_posts(limit: int = 500, incluir_base: bool = False):
    q = "SELECT * FROM posts"
    if not incluir_base:
        q += " WHERE estado != 'base'"
    q += " ORDER BY COALESCE(publicado, detectado) DESC LIMIT ?"
    with _conn() as c:
        return c.execute(q, (limit,)).fetchall()


def all_posts():
    with _conn() as c:
        return c.execute("SELECT * FROM posts ORDER BY COALESCE(publicado, detectado) DESC").fetchall()


# ------------------------------------------------------------------ grupos (Fase 2)
def posts_sin_grupo():
    """Noticias capturadas (no 'base' ni videos) que todavía no pasaron por el cruce."""
    with _conn() as c:
        return c.execute(
            "SELECT * FROM posts WHERE grupo_id IS NULL AND estado NOT IN ('base','video','video_nota',"
            "'video_descartado') "
            "AND COALESCE(texto,'') != '' ORDER BY COALESCE(publicado, detectado)"
        ).fetchall()


def grupos_recientes(horas: int, limite: int = 80):
    desde = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
    with _conn() as c:
        return c.execute(
            "SELECT * FROM grupos WHERE estado_pdf != 'descartado' AND (actualizado >= ? "
            "OR COALESCE(fecha_hecho,'') >= ?) ORDER BY actualizado DESC LIMIT ?", (desde, desde, limite)
        ).fetchall()


def crear_grupo(ambito, tema, resumen) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO grupos (creado, actualizado, ambito, tema, resumen, titular) VALUES (?,?,?,?,?,?)",
            (now_iso(), now_iso(), ambito, tema, resumen, resumen),
        )
        return cur.lastrowid


def asignar_post(post_id, grupo_id, ambito, tema, resumen):
    with _conn() as c:
        c.execute("UPDATE posts SET grupo_id=?, ambito=?, tema=?, resumen=? WHERE id=?",
                  (grupo_id, ambito, tema, resumen, post_id))
        g = c.execute("SELECT estado_pdf, ultimo_pdf FROM grupos WHERE id=?", (grupo_id,)).fetchone()
        # si el grupo ya salió en un consolidado, la nueva fuente lo convierte en "actualización"
        nuevo_estado = "actualizacion" if g and g["ultimo_pdf"] else "nuevo"
        c.execute("UPDATE grupos SET nota_pendiente=1, actualizado=?, estado_pdf=? WHERE id=?",
                  (now_iso(), nuevo_estado, grupo_id))


def n_medios(g) -> int:
    """Medios distintos de un grupo (2 posts del mismo medio no hacen una nota 'combinada')."""
    return len({m for m in (g["medios"] or "").split(", ") if m})


def get_grupo(gid):
    with _conn() as c:
        return c.execute("SELECT * FROM grupos WHERE id=?", (gid,)).fetchone()


def posts_de_grupo(gid):
    with _conn() as c:
        return c.execute("SELECT * FROM posts WHERE grupo_id=? ORDER BY COALESCE(publicado, detectado)",
                         (gid,)).fetchall()


def grupos_con_nota_pendiente():
    with _conn() as c:
        return c.execute("SELECT * FROM grupos WHERE nota_pendiente=1").fetchall()


def actualizar_grupo(gid, **fields):
    if "nota_json" in fields and not isinstance(fields["nota_json"], str):
        fields["nota_json"] = json.dumps(fields["nota_json"], ensure_ascii=False)
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE grupos SET {cols} WHERE id=?", (*fields.values(), gid))


def list_grupos(desde_iso: str | None = None):
    q = "SELECT * FROM grupos WHERE nota_json IS NOT NULL"
    args = ()
    if desde_iso:
        q += " AND COALESCE(fecha_hecho, actualizado) >= ?"
        args = (desde_iso,)
    q += " ORDER BY COALESCE(fecha_hecho, actualizado) DESC"
    with _conn() as c:
        return c.execute(q, args).fetchall()


def grupos_descartados():
    with _conn() as c:
        return c.execute("SELECT id FROM grupos WHERE estado_pdf='descartado'").fetchall()


def grupos_para_pdf(dia: str | None = None, con_relleno: bool = False):
    """Noticias que van al próximo boletín: solo las del día (fecha del hecho = hoy, hora local).
    dia: 'AAAA-MM-DD' para otro día; '' = sin filtro de fecha.
    con_relleno: suma las notas del día anterior que no entraron en el papel; el maquetador las usa
    solo para llenar los huecos que queden después de poner todas las de hoy."""
    if dia is None:
        dia = dia_editorial()
    if con_relleno and dia:
        hoy = list(grupos_para_pdf(dia))
        ids = {g["id"] for g in hoy}
        return hoy + [g for g in relleno_de(dia) if g["id"] not in ids]
    q = ("SELECT * FROM grupos WHERE estado_pdf IN ('nuevo','actualizacion') AND nota_json IS NOT NULL"
         " AND COALESCE(en_periodico, 1)=1")
    args = ()
    if dia:
        # las del día (con la madrugada siguiente: el boletín se cierra tarde) y las que se pusieron a mano
        # en una página desde la vista previa
        fijas = _fijas()
        ayer = (datetime.strptime(dia, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        q += (f" AND ({_SQL_DIA} OR {_SQL_TARDE}"
              + (f" OR id IN ({','.join('?' * len(fijas))})" if fijas else "") + ")")
        args = (*_args_dia(dia), ayer, f"{HORA_ARRASTRE:02d}", *fijas)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def relleno_de(dia: str):
    """Notas del día anterior a `dia` que nunca salieron en el papel (solo fueron a la web)."""
    ayer = (datetime.strptime(dia, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    with _conn() as c:
        return c.execute("SELECT * FROM grupos WHERE estado_pdf='nuevo' AND nota_json IS NOT NULL "
                         f"AND ultimo_pdf IS NULL AND COALESCE(en_periodico, 1)=1 AND {_SQL_DIA}",
                         _args_dia(ayer)).fetchall()


def _fijas() -> list:
    try:
        d = json.loads((DB_PATH.parent / "maqueta.json").read_text(encoding="utf-8"))
        return [int(k) for k in d.get("fijas", {})]
    except Exception:  # noqa: BLE001
        return []


def pendientes_de_otros_dias() -> int:
    """Noticias sin publicar de días anteriores (no entran al boletín de hoy)."""
    return len(grupos_para_pdf("")) - len(grupos_para_pdf())


def registrar_consolidado(path, desde, hasta, gids) -> int:
    with _conn() as c:
        cur = c.execute("INSERT INTO consolidados (fecha, path, desde, hasta, n_noticias) VALUES (?,?,?,?,?)",
                        (now_iso(), str(path), desde, hasta, len(gids)))
        n = cur.lastrowid
        for gid in gids:
            c.execute("UPDATE grupos SET estado_pdf='publicado', ultimo_pdf=? WHERE id=?", (n, gid))
            c.execute("UPDATE posts SET consolidado_n=? WHERE grupo_id=? AND consolidado_n IS NULL", (n, gid))
        return n


def siguiente_consolidado() -> int:
    with _conn() as c:
        r = c.execute("SELECT COALESCE(MAX(n),0)+1 AS n FROM consolidados").fetchone()
        return r["n"]


def ultimo_consolidado():
    with _conn() as c:
        return c.execute("SELECT * FROM consolidados ORDER BY n DESC LIMIT 1").fetchone()


def set_en_periodico(gids, valor: bool) -> None:
    with _conn() as c:
        c.executemany("UPDATE grupos SET en_periodico=? WHERE id=?", [(1 if valor else 0, g) for g in gids])


def guardar_nota(gid, nota: dict) -> None:
    """Guarda la nota editada a mano (texto, foto, portada)."""
    actualizar_grupo(gid, nota_json=nota, titular=nota.get("TITULAR") or None, actualizado=now_iso())
    marcar_web_editada([gid])


# ------------------------------------------------------------------ día de trabajo
HORA_CIERRE = 5     # hasta las 5 a. m. se sigue trabajando el boletín del día anterior
HORA_ARRASTRE = 15  # lo publicado desde las 3 p. m. del día anterior también es del boletín de hoy


def dia_editorial(ahora: datetime | None = None) -> str:
    """Día del boletín en curso ('AAAA-MM-DD'). Pasada la medianoche y hasta HORA_CIERRE sigue siendo el
    día anterior: antes, a las 00:05 el maquetador ya buscaba las noticias «de mañana» y no encontraba ninguna
    (no unía repetidas, no limpiaba fotos, no armaba el boletín)."""
    return ((ahora or datetime.now()) - timedelta(hours=HORA_CIERRE)).strftime("%Y-%m-%d")


_SQL_DIA = ("(substr(COALESCE(fecha_hecho, actualizado), 1, 10) = ? OR "
            "(substr(COALESCE(fecha_hecho, actualizado), 1, 10) = ? AND "
            "substr(COALESCE(fecha_hecho, actualizado), 12, 2) < ?))")


def _args_dia(dia: str) -> tuple:
    sig = (datetime.strptime(dia, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return dia, sig, f"{HORA_CIERRE:02d}"


_SQL_TARDE = ("(substr(COALESCE(fecha_hecho, actualizado), 1, 10) = ? AND "
              "substr(COALESCE(fecha_hecho, actualizado), 12, 2) >= ?)")


def dia_boletin(g) -> str:
    """Boletín al que corresponde una noticia: como dia_de, pero lo publicado desde HORA_ARRASTRE
    (3 p. m.) cuenta ya para el boletín del día siguiente."""
    f = g["fecha_hecho"] or g["actualizado"] or ""
    try:
        d = datetime.fromisoformat(f[:19])
    except ValueError:
        return f[:10]
    if d.hour >= HORA_ARRASTRE:
        return (d + timedelta(days=1)).strftime("%Y-%m-%d")
    # De madrugada (antes de HORA_CIERRE) también es del día del calendario: ya pasó la hora de arrastre de
    # ayer, así que va al boletín de hoy. Con dia_editorial() caía en el boletín de AYER, que ya había salido,
    # y la nota no salía en ningún lado.
    return d.strftime("%Y-%m-%d")


def dia_de(g) -> str:
    """Día de trabajo de una noticia ('AAAA-MM-DD'): el de la fecha del hecho (o de la última actualización).
    Lo publicado de madrugada (antes de HORA_CIERRE) cuenta para el día anterior."""
    f = g["fecha_hecho"] or g["actualizado"] or ""
    try:
        return dia_editorial(datetime.fromisoformat(f[:19]))
    except ValueError:
        return f[:10]


def consolidado_del_dia(dia: str | None = None):
    """El último boletín publicado para el día editorial `dia` (hoy por defecto), o None."""
    dia = dia or dia_editorial()
    for c in list_consolidados():
        try:
            if dia_editorial(datetime.fromisoformat(c["fecha"][:19])) == dia:
                return c
        except (TypeError, ValueError):
            continue
    return None


def publicadas_del_dia(dia: str | None = None):
    """Noticias del día que ya salieron en un boletín (para unir con ellas lo que llegue después)."""
    dia = dia or dia_editorial()
    with _conn() as c:
        return c.execute(f"SELECT * FROM grupos WHERE estado_pdf='publicado' AND nota_json IS NOT NULL AND {_SQL_DIA}",
                         _args_dia(dia)).fetchall()


def todas_del_dia(dia: str | None = None):
    """Todas las noticias válidas del día (publicadas o no): lo que va a la web."""
    dia = dia or dia_editorial()
    with _conn() as c:
        return c.execute(f"SELECT * FROM grupos WHERE estado_pdf != 'descartado' AND nota_json IS NOT NULL "
                         f"AND {_SQL_DIA}", _args_dia(dia)).fetchall()


def dias_con_noticias(limite: int = 60):
    """[(día, total, al periódico, a la web)] de los días con noticias, del más reciente al más antiguo."""
    with _conn() as c:
        return c.execute(
            "SELECT substr(COALESCE(fecha_hecho, actualizado), 1, 10) AS dia, COUNT(*) AS n, "
            "SUM(CASE WHEN COALESCE(en_periodico,1)=1 THEN 1 ELSE 0 END) AS np, "
            "SUM(CASE WHEN COALESCE(en_web,0)=1 THEN 1 ELSE 0 END) AS nw "
            "FROM grupos WHERE nota_json IS NOT NULL AND estado_pdf != 'descartado' "
            "GROUP BY dia ORDER BY dia DESC LIMIT ?", (limite,)).fetchall()


def grupos_del_dia(dia: str | None):
    """Noticias de un día ('AAAA-MM-DD'); None = todas."""
    q = "SELECT * FROM grupos WHERE nota_json IS NOT NULL AND estado_pdf != 'descartado'"
    args = ()
    if dia:
        # de las 5 a. m. del día a las 5 a. m. del siguiente (como dia_de)
        dia, sig, hh = _args_dia(dia)
        q += (" AND ((substr(COALESCE(fecha_hecho, actualizado), 1, 10) = ? AND "
              "substr(COALESCE(fecha_hecho, actualizado), 12, 2) >= ?) OR "
              "(substr(COALESCE(fecha_hecho, actualizado), 1, 10) = ? AND "
              "substr(COALESCE(fecha_hecho, actualizado), 12, 2) < ?))")
        args = (dia, hh, sig, hh)
    q += " ORDER BY COALESCE(fecha_hecho, actualizado) DESC"
    with _conn() as c:
        return c.execute(q, args).fetchall()


def boletin_del_dia(dia: str):
    """Último boletín generado ese día (por su fecha de generación), o None."""
    with _conn() as c:
        return c.execute("SELECT * FROM consolidados WHERE substr(fecha, 1, 10)=? ORDER BY n DESC LIMIT 1",
                         (dia,)).fetchone()


# ------------------------------------------------------------------ web
def en_web(g) -> bool:
    return bool("en_web" in g.keys() and g["en_web"])


def estado_web(g) -> str:
    """'no' | 'por_subir' | 'en_linea' | 'actualizar' | 'por_quitar' (estaba en línea y se desmarcó)."""
    est = (g["web_estado"] if "web_estado" in g.keys() else "") or ""
    if not en_web(g):
        return "por_quitar" if est in ("publicada", "actualizar") else "no"
    if est == "publicada":
        return "en_linea"
    if est == "actualizar":
        return "actualizar"
    return "por_subir"


def set_en_web(gids, valor: bool) -> None:
    with _conn() as c:
        c.executemany("UPDATE grupos SET en_web=? WHERE id=?", [(1 if valor else 0, g) for g in gids])


def set_web_fijada(gid, valor: bool) -> None:
    """📌 Fija (o suelta) una noticia arriba de la portada de la web; queda por subir."""
    with _conn() as c:
        c.execute("UPDATE grupos SET web_fijada=? WHERE id=?", (1 if valor else 0, gid))
    marcar_web_editada([gid])


def web_fijada(g) -> bool:
    return bool("web_fijada" in g.keys() and g["web_fijada"])


def marcar_web_editada(gids) -> None:
    """Una nota ya subida que se edita queda «por actualizar» en la web."""
    with _conn() as c:
        c.executemany("UPDATE grupos SET web_estado='actualizar' WHERE id=? AND web_estado='publicada'",
                      [(g,) for g in gids])


def grupos_web():
    """Noticias marcadas para la web (de todos los días)."""
    with _conn() as c:
        return c.execute("SELECT * FROM grupos WHERE COALESCE(en_web,0)=1 AND nota_json IS NOT NULL "
                         "AND estado_pdf != 'descartado' ORDER BY COALESCE(fecha_hecho, actualizado) DESC").fetchall()


def cambios_web() -> int:
    """Cuántas noticias tienen cambios sin subir (nuevas, editadas o desmarcadas)."""
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM grupos WHERE nota_json IS NOT NULL AND ("
            "(COALESCE(en_web,0)=1 AND COALESCE(web_estado,'') != 'publicada') OR "
            "(COALESCE(en_web,0)=0 AND web_estado IN ('publicada','actualizar')))").fetchone()[0]


def registrar_subida_web(subidas, slugs: dict) -> None:
    """Tras subir: las marcadas quedan 'publicada'; las desmarcadas vuelven a ''."""
    ahora = now_iso()
    with _conn() as c:
        for gid in subidas:
            c.execute("UPDATE grupos SET web_estado='publicada', web_publicado=COALESCE(web_publicado, ?), "
                      "web_slug=? WHERE id=?", (ahora, slugs.get(gid), gid))
        c.execute("UPDATE grupos SET web_estado='' WHERE COALESCE(en_web,0)=0 AND web_estado IN ('publicada','actualizar')")


def list_consolidados():
    with _conn() as c:
        return c.execute("SELECT * FROM consolidados ORDER BY n DESC").fetchall()


def get_consolidado(n):
    with _conn() as c:
        return c.execute("SELECT * FROM consolidados WHERE n=?", (n,)).fetchone()


def grupos_de_consolidado(n):
    """Noticias que forman la edición publicada N° n (para verla o corregirla)."""
    with _conn() as c:
        return c.execute("SELECT * FROM grupos WHERE ultimo_pdf=? AND nota_json IS NOT NULL "
                         "AND estado_pdf != 'descartado'", (n,)).fetchall()


def poner_en_edicion(gid, n):
    with _conn() as c:
        c.execute("UPDATE grupos SET ultimo_pdf=?, estado_pdf='publicado', en_periodico=1 WHERE id=?", (n, gid))


def sacar_de_edicion(gid):
    """La noticia deja la edición publicada y vuelve a pendientes (marcada ☐ No va)."""
    with _conn() as c:
        c.execute("UPDATE grupos SET ultimo_pdf=NULL, estado_pdf='nuevo', en_periodico=0 WHERE id=?", (gid,))


def actualizar_consolidado(n, path, n_noticias):
    with _conn() as c:
        c.execute("UPDATE consolidados SET path=?, n_noticias=? WHERE n=?", (str(path), n_noticias, n))
