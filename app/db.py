import json
import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "HOSPICE_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "hospice.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,
    location    TEXT    NOT NULL,
    plan        TEXT,
    price       REAL,
    started_at  INTEGER NOT NULL,
    ssh_host    TEXT,
    ssh_port    INTEGER DEFAULT 22,
    ssh_user    TEXT    DEFAULT 'root',
    ssh_key     TEXT,
    hp          REAL    DEFAULT 100,
    days_left   INTEGER,
    death_date  TEXT,
    oversell    REAL    DEFAULT 0,
    mining_risk REAL    DEFAULT 0,
    last_probe  INTEGER,
    died_at     INTEGER,
    epitaph     TEXT,
    certified   INTEGER DEFAULT 0,
    tier        TEXT,
    affiliate   TEXT,
    clicks      INTEGER DEFAULT 0,
    views       INTEGER DEFAULT 0,
    contact     TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    ip        TEXT,
    ua        TEXT,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_leads_node ON leads(node_id, ts DESC);

CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    cpu_single  INTEGER,
    cpu_multi   INTEGER,
    disk_read   REAL,
    disk_write  REAL,
    iops_4k     INTEGER,
    ping_avg    REAL,
    dl_speed    REAL,
    cpu_steal   REAL,
    load1       REAL,
    uptime_days INTEGER,
    raw         TEXT,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_samples_node_ts ON samples(node_id, ts);

CREATE TABLE IF NOT EXISTS monologues (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    text      TEXT    NOT NULL,
    mood      TEXT,
    data_hash TEXT,
    UNIQUE (node_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_mono_node ON monologues(node_id, ts DESC);

CREATE TABLE IF NOT EXISTS config (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


@contextmanager
def tx():
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


MIGRATIONS = [
    ("nodes", "certified", "INTEGER DEFAULT 0"),
    ("nodes", "tier", "TEXT"),
    ("nodes", "affiliate", "TEXT"),
    ("nodes", "clicks", "INTEGER DEFAULT 0"),
    ("nodes", "views", "INTEGER DEFAULT 0"),
    ("nodes", "contact", "TEXT"),
    ("nodes", "note", "TEXT"),
]


def _migrate(c):
    for table, col, decl in MIGRATIONS:
        cols = {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
        if col not in cols:
            c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        _migrate(c)


def nodes(include_dead=True):
    sql = "SELECT * FROM nodes"
    if not include_dead:
        sql += " WHERE died_at IS NULL"
    sql += " ORDER BY (died_at IS NOT NULL), days_left DESC"
    with tx() as c:
        return [dict(r) for r in c.execute(sql)]


def node(node_id):
    with tx() as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return dict(r) if r else None


def samples(node_id, limit=500):
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM samples WHERE node_id=? ORDER BY ts ASC LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_monologues(limit=6):
    with tx() as c:
        rows = c.execute("""
            SELECT m.*, n.provider, n.location
            FROM monologues m JOIN nodes n ON n.id = m.node_id
            ORDER BY m.ts DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def node_monologues(node_id, limit=10):
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM monologues WHERE node_id=? ORDER BY ts DESC LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def graveyard(limit=50):
    with tx() as c:
        rows = c.execute("""
            SELECT n.*,
                   (SELECT MIN(ts) FROM samples WHERE node_id=n.id) AS born,
                   (SELECT COUNT(*) FROM samples WHERE node_id=n.id)   AS probe_count
            FROM nodes n WHERE n.died_at IS NOT NULL
            ORDER BY n.died_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def add_node(provider, location, plan=None, price=None, started_at=None,
             ssh_host=None, ssh_port=22, ssh_user="root", ssh_key=None):
    with tx() as c:
        cur = c.execute("""
            INSERT INTO nodes(provider, location, plan, price, started_at,
                              ssh_host, ssh_port, ssh_user, ssh_key)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (provider, location, plan, price, started_at or int(time.time()),
              ssh_host, ssh_port, ssh_user, ssh_key))
        return cur.lastrowid


def add_sample(node_id, data, ts=None):
    with tx() as c:
        cur = c.execute("""
            INSERT INTO samples(node_id, ts, cpu_single, cpu_multi, disk_read,
                                disk_write, iops_4k, ping_avg, dl_speed,
                                cpu_steal, load1, uptime_days, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            node_id, ts or int(time.time()),
            data.get("cpu_single"), data.get("cpu_multi"),
            data.get("disk_read"), data.get("disk_write"), data.get("iops_4k"),
            data.get("ping_avg"), data.get("dl_speed"),
            data.get("cpu_steal"), data.get("load1"), data.get("uptime_days"),
            json.dumps(data["raw"]) if data.get("raw") else None,
        ))
        return cur.lastrowid


def save_prediction(node_id, p):
    with tx() as c:
        c.execute("""
            UPDATE nodes SET days_left=?, death_date=?, hp=?,
                             oversell=?, mining_risk=?, last_probe=?
            WHERE id=?
        """, (p["days_left"], p["death_date"], p["health"],
              p["oversell"], p["mining_risk"], int(time.time()), node_id))


def add_monologue(node_id, text, mood=None, data_hash=None, ts=None):
    with tx() as c:
        c.execute("""
            INSERT OR REPLACE INTO monologues(node_id, ts, text, mood, data_hash)
            VALUES (?,?,?,?,?)
        """, (node_id, ts or int(time.time()), text, mood, data_hash))


def has_monologue_today(node_id):
    day0 = int(time.time()) // 86400 * 86400
    with tx() as c:
        r = c.execute(
            "SELECT 1 FROM monologues WHERE node_id=? AND ts>=? LIMIT 1",
            (node_id, day0),
        ).fetchone()
        return r is not None


def bury(node_id, epitaph=None):
    with tx() as c:
        c.execute("UPDATE nodes SET died_at=?, epitaph=? WHERE id=?",
                  (int(time.time()), epitaph, node_id))


def stats():
    with tx() as c:
        total = c.execute("SELECT COUNT(*) n FROM nodes").fetchone()["n"]
        alive = c.execute("SELECT COUNT(*) n FROM nodes WHERE died_at IS NULL").fetchone()["n"]
        sp = c.execute("SELECT COUNT(*) n FROM samples").fetchone()["n"]
        mo = c.execute("SELECT COUNT(*) n FROM monologues").fetchone()["n"]
        avg = c.execute("SELECT AVG(days_left) v FROM nodes WHERE died_at IS NULL").fetchone()["v"]
    return {
        "total": total,
        "alive": alive,
        "dead": total - alive,
        "samples": sp,
        "monologues": mo,
        "avg_days_left": round(avg or 0),
    }


def certify(node_id, enabled=True, affiliate=None, contact=None):
    with tx() as c:
        c.execute(
            "UPDATE nodes SET certified=?, affiliate=?, contact=? WHERE id=?",
            (1 if enabled else 0, affiliate, contact, node_id),
        )


def set_tier(node_id, tier_name):
    with tx() as c:
        c.execute("UPDATE nodes SET tier=? WHERE id=?", (tier_name, node_id))


def click(node_id):
    with tx() as c:
        c.execute("UPDATE nodes SET clicks=clicks+1 WHERE id=?", (node_id,))
        c.execute("INSERT INTO leads(node_id, ts) VALUES(?,?)",
                  (node_id, int(time.time())))


def view(node_id):
    with tx() as c:
        c.execute("UPDATE nodes SET views=views+1 WHERE id=?", (node_id,))


def certified_nodes():
    with tx() as c:
        rows = c.execute("""
            SELECT * FROM nodes
            WHERE certified=1 AND died_at IS NULL
            ORDER BY days_left DESC
        """).fetchall()
        return [dict(r) for r in rows]


def click_stats(node_id, days=30):
    since = int(time.time()) - days * 86400
    with tx() as c:
        n = c.execute(
            "SELECT COUNT(*) v FROM leads WHERE node_id=? AND ts>=?",
            (node_id, since),
        ).fetchone()["v"]
    return n
