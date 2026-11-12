import json
import os
import sqlite3
import time
from contextlib import contextmanager

DB = os.environ.get(
    "HOSPICE_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hospice.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    location    TEXT NOT NULL,
    plan        TEXT,
    price       REAL,
    started_at  INTEGER NOT NULL,
    ssh_host    TEXT,
    ssh_port    INTEGER DEFAULT 22,
    ssh_user    TEXT DEFAULT 'root',
    ssh_key     TEXT,
    hp          REAL DEFAULT 100,
    days_left   INTEGER,
    death_date  TEXT,
    oversell    REAL DEFAULT 0,
    mining_risk REAL DEFAULT 0,
    last_probe  INTEGER,
    died_at     INTEGER,
    epitaph     TEXT,
    certified   INTEGER DEFAULT 0,
    tier        TEXT,
    affiliate   TEXT,
    clicks      INTEGER DEFAULT 0,
    views       INTEGER DEFAULT 0,
    contact     TEXT
);

CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
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
    raw         TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples ON samples(node_id, ts);

CREATE TABLE IF NOT EXISTS monologues (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    text      TEXT NOT NULL,
    mood      TEXT,
    data_hash TEXT,
    UNIQUE (node_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_mono ON monologues(node_id, ts DESC);

CREATE TABLE IF NOT EXISTS leads (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    ts      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT);
"""

EXTRA = [
    ("certified", "INTEGER DEFAULT 0"),
    ("tier", "TEXT"),
    ("affiliate", "TEXT"),
    ("clicks", "INTEGER DEFAULT 0"),
    ("views", "INTEGER DEFAULT 0"),
    ("contact", "TEXT"),
]


def _conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


@contextmanager
def tx():
    c = _conn()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        have = {r[1] for r in c.execute("PRAGMA table_info(nodes)")}
        for col, decl in EXTRA:
            if col not in have:
                c.execute(f"ALTER TABLE nodes ADD COLUMN {col} {decl}")


def _all(sql, args=()):
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args)]


def _one(sql, args=()):
    with tx() as c:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None


def nodes(alive_only=False):
    w = " WHERE died_at IS NULL" if alive_only else ""
    return _all(f"SELECT * FROM nodes{w} ORDER BY (died_at IS NOT NULL), days_left DESC")


def node(nid):
    return _one("SELECT * FROM nodes WHERE id=?", (nid,))


def samples(nid, limit=500):
    return _all("SELECT * FROM samples WHERE node_id=? ORDER BY ts LIMIT ?", (nid, limit))


def monologues(limit=6):
    return _all("""SELECT m.*, n.provider, n.location FROM monologues m
                   JOIN nodes n ON n.id=m.node_id ORDER BY m.ts DESC LIMIT ?""", (limit,))


def node_monologues(nid, limit=10):
    return _all("SELECT * FROM monologues WHERE node_id=? ORDER BY ts DESC LIMIT ?", (nid, limit))


def dead():
    return _all("""SELECT n.*, (SELECT MIN(ts) FROM samples WHERE node_id=n.id) born,
                          (SELECT COUNT(*) FROM samples WHERE node_id=n.id) probes
                   FROM nodes n WHERE n.died_at IS NOT NULL ORDER BY n.died_at DESC""")


def add_node(provider, location, plan=None, price=None, started_at=None,
             ssh_host=None, ssh_port=22, ssh_user="root", ssh_key=None):
    with tx() as c:
        return c.execute("""INSERT INTO nodes(provider,location,plan,price,started_at,
                           ssh_host,ssh_port,ssh_user,ssh_key) VALUES(?,?,?,?,?,?,?,?,?)""",
                         (provider, location, plan, price, started_at or int(time.time()),
                          ssh_host, ssh_port, ssh_user, ssh_key)).lastrowid


def add_sample(nid, d, ts=None):
    with tx() as c:
        return c.execute("""INSERT INTO samples(node_id,ts,cpu_single,cpu_multi,disk_read,
                           disk_write,iops_4k,ping_avg,dl_speed,cpu_steal,load1,uptime_days,raw)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (nid, ts or int(time.time()),
                          d.get("cpu_single"), d.get("cpu_multi"),
                          d.get("disk_read"), d.get("disk_write"), d.get("iops_4k"),
                          d.get("ping_avg"), d.get("dl_speed"), d.get("cpu_steal"),
                          d.get("load1"), d.get("uptime_days"),
                          json.dumps(d["raw"]) if d.get("raw") else None)).lastrowid


def save_pred(nid, p):
    with tx() as c:
        c.execute("""UPDATE nodes SET days_left=?,death_date=?,hp=?,oversell=?,mining_risk=?,
                     last_probe=? WHERE id=?""",
                  (p["days_left"], p["death_date"], p["health"], p["oversell"],
                   p["mining_risk"], int(time.time()), nid))


def add_monologue(nid, text, mood=None, h=None, ts=None):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO monologues(node_id,ts,text,mood,data_hash) VALUES(?,?,?,?,?)",
                  (nid, ts or int(time.time()), text, mood, h))


def spoken_today(nid):
    day0 = int(time.time()) // 86400 * 86400
    with tx() as c:
        return c.execute("SELECT 1 FROM monologues WHERE node_id=? AND ts>=? LIMIT 1",
                         (nid, day0)).fetchone() is not None


def bury(nid, epitaph=None):
    with tx() as c:
        c.execute("UPDATE nodes SET died_at=?, epitaph=? WHERE id=?",
                  (int(time.time()), epitaph, nid))


def set_tier(nid, t):
    with tx() as c:
        c.execute("UPDATE nodes SET tier=? WHERE id=?", (t, nid))


def certify(nid, affiliate=None, contact=None):
    with tx() as c:
        c.execute("UPDATE nodes SET certified=1, affiliate=?, contact=? WHERE id=?",
                  (affiliate, contact, nid))


def certified():
    return _all("""SELECT * FROM nodes WHERE certified=1 AND died_at IS NULL
                   ORDER BY days_left DESC""")


def click(nid):
    with tx() as c:
        c.execute("UPDATE nodes SET clicks=clicks+1 WHERE id=?", (nid,))
        c.execute("INSERT INTO leads(node_id,ts) VALUES(?,?)", (nid, int(time.time())))


def stats():
    with tx() as c:
        total = c.execute("SELECT COUNT(*) n FROM nodes").fetchone()["n"]
        alive = c.execute("SELECT COUNT(*) n FROM nodes WHERE died_at IS NULL").fetchone()["n"]
        sp = c.execute("SELECT COUNT(*) n FROM samples").fetchone()["n"]
        mo = c.execute("SELECT COUNT(*) n FROM monologues").fetchone()["n"]
        avg = c.execute("SELECT AVG(days_left) v FROM nodes WHERE died_at IS NULL").fetchone()["v"]
    return {"total": total, "alive": alive, "dead": total - alive,
            "samples": sp, "monologues": mo, "avg_days_left": round(avg or 0)}
