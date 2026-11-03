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
    estado        TEXT NOT NULL,      -- base | pendiente | procesado | error
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


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(posts)")}
        for col, typ in POSTS_EXTRA.items():
            if col not in cols:
                c.execute(f"ALTER TABLE posts ADD COLUMN {col} {typ}")
        c.execute("CREATE INDEX IF NOT EXISTS ix_grupo ON posts(grupo_id)")
        gcols = {r["name"] for r in c.execute("PRAGMA table_info(grupos)")}
        if "en_periodico" not in gcols:   # 1 = va al periódico, 0 = la excluyó el editor
            c.execute("ALTER TABLE grupos ADD COLUMN en_periodico INTEGER DEFAULT 1")


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


def insert_post(p: dict, estado: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO posts
               (id, text_hash, pagina, permalink, enlace_ext, texto, publicado, detectado, estado, visto)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                p["id"], p["text_hash"], p["pagina"], p.get("permalink"), p.get("enlace_ext"),
                p.get("texto"), p.get("publicado"), now_iso(), estado, 1 if estado == "base" else 0,
            ),
        )


def update_post(post_id: str, **fields) -> None:
    if "ia_json" in fields and not isinstance(fields["ia_json"], str):
        fields["ia_json"] = json.dumps(fields["ia_json"], ensure_ascii=False)
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
    """Noticias capturadas (no 'base') que todavía no pasaron por el cruce."""
    with _conn() as c:
        return c.execute(
            "SELECT * FROM posts WHERE grupo_id IS NULL AND estado != 'base' "
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


def grupos_para_pdf():
    with _conn() as c:
        return c.execute(
            "SELECT * FROM grupos WHERE estado_pdf IN ('nuevo','actualizacion') AND nota_json IS NOT NULL"
            " AND COALESCE(en_periodico, 1)=1"
        ).fetchall()


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
