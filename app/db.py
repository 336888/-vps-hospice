"""
SQLite 数据层

设计要点：
- 用 WAL 模式（读写并发不阻塞）
- 所有时间戳用 UNIX 秒（int）
- samples 表是核心，每次探测写一行
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

DB_PATH = os.environ.get("HOSPICE_DB", os.path.join(os.path.dirname(__file__), "..", "data", "hospice.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 参赛机器
CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider     TEXT    NOT NULL,
    location     TEXT    NOT NULL,
    plan         TEXT,
    price        REAL,                    -- 美元/年
    started_at   INTEGER NOT NULL,        -- 开通时间
    ssh_host     TEXT,
    ssh_port     INTEGER DEFAULT 22,
    ssh_user     TEXT    DEFAULT 'root',
    ssh_key      TEXT,
    active       INTEGER DEFAULT 1,
    -- 缓存的最新预测（每次探测后更新）
    hp           REAL    DEFAULT 100,
    days_left    INTEGER,
    death_date   TEXT,
    oversell     REAL    DEFAULT 0,
    mining_risk  REAL    DEFAULT 0,
    last_probe   INTEGER,
    died_at      INTEGER,                 -- 实际死亡时间（迁移/关闭）
    epitaph      TEXT                     -- 用户的"遗言"
);

-- 每次探测的原始结果
CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    -- CPU
    cpu_single  INTEGER,
    cpu_multi   INTEGER,
    -- 磁盘
    disk_read   REAL,                     -- MB/s
    disk_write  REAL,
    iops_4k     INTEGER,
    -- 网络
    ping_avg    REAL,                     -- ms
    dl_speed    REAL,                     -- Mbps
    -- 状态
    cpu_steal   REAL,                     -- %st
    load1       REAL,
    uptime_days INTEGER,
    raw         TEXT,                     -- 完整 YABS 输出（压缩后）
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_samples_node_ts ON samples(node_id, ts);

-- 每日独白
CREATE TABLE IF NOT EXISTS monologues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    mood        TEXT,                     -- 情感标签
    data_hash   TEXT,                     -- 生成时的数据指纹（防重复生成）
    UNIQUE(node_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_mono_node ON monologues(node_id, ts DESC);

-- 全局配置
CREATE TABLE IF NOT EXISTS config (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)


# ============ 查询 ============

def all_nodes(include_dead: bool = True) -> list[dict]:
    sql = "SELECT * FROM nodes"
    if not include_dead:
        sql += " WHERE died_at IS NULL"
    sql += " ORDER BY (died_at IS NOT NULL), days_left DESC"
    with tx() as c:
        return [dict(r) for r in c.execute(sql)]


def get_node(node_id: int) -> dict | None:
    with tx() as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return dict(r) if r else None


def node_samples(node_id: int, limit: int = 500) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM samples WHERE node_id=? ORDER BY ts ASC LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def latest_monologues(limit: int = 30) -> list[dict]:
    """最近的独白（跨所有机器）"""
    with tx() as c:
        rows = c.execute("""
            SELECT m.*, n.provider, n.location
            FROM monologues m JOIN nodes n ON n.id = m.node_id
            ORDER BY m.ts DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def node_monologues(node_id: int, limit: int = 10) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM monologues WHERE node_id=? ORDER BY ts DESC LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def graveyard(limit: int = 50) -> list[dict]:
    with tx() as c:
        rows = c.execute("""
            SELECT n.*,
                   (SELECT MIN(ts) FROM samples WHERE node_id=n.id) AS born,
                   (SELECT COUNT(*) FROM samples WHERE node_id=n.id) AS probe_count
            FROM nodes n WHERE n.died_at IS NOT NULL
            ORDER BY n.died_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ============ 写入 ============

def add_node(provider: str, location: str, plan: str | None = None,
             price: float | None = None, started_at: int | None = None,
             ssh_host: str | None = None, ssh_port: int = 22,
             ssh_user: str = "root", ssh_key: str | None = None) -> int:
    with tx() as c:
        cur = c.execute("""
            INSERT INTO nodes(provider, location, plan, price, started_at,
                              ssh_host, ssh_port, ssh_user, ssh_key)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (provider, location, plan, price,
              started_at or int(time.time()), ssh_host, ssh_port, ssh_user, ssh_key))
        return cur.lastrowid  # type: ignore


def add_sample(node_id: int, data: dict[str, Any], ts: int | None = None) -> int:
    ts = ts or int(time.time())
    with tx() as c:
        cur = c.execute("""
            INSERT INTO samples(node_id, ts, cpu_single, cpu_multi,
                                disk_read, disk_write, iops_4k,
                                ping_avg, dl_speed, cpu_steal, load1,
                                uptime_days, raw)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            node_id, ts,
            data.get("cpu_single"), data.get("cpu_multi"),
            data.get("disk_read"), data.get("disk_write"), data.get("iops_4k"),
            data.get("ping_avg"), data.get("dl_speed"),
            data.get("cpu_steal"), data.get("load1"),
            data.get("uptime_days"),
            json.dumps(data.get("raw")) if data.get("raw") else None,
        ))
        return cur.lastrowid  # type: ignore


def update_prediction(node_id: int, pred: dict) -> None:
    with tx() as c:
        c.execute("""
            UPDATE nodes SET
                days_left=?, death_date=?, hp=?,
                oversell=?, mining_risk=?, last_probe=?
            WHERE id=?
        """, (
            pred["days_left"], pred["death_date"], pred["health"],
            pred["oversell"], pred["mining_risk"], int(time.time()),
            node_id,
        ))


def add_monologue(node_id: int, text: str, mood: str | None = None,
                  data_hash: str | None = None, ts: int | None = None) -> None:
    ts = ts or int(time.time())
    with tx() as c:
        c.execute("""
            INSERT OR REPLACE INTO monologues(node_id, ts, text, mood, data_hash)
            VALUES(?,?,?,?,?)
        """, (node_id, ts, text, mood, data_hash))


def has_monologue_today(node_id: int) -> bool:
    """今天是否已经生成过独白"""
    today0 = int(time.time()) // 86400 * 86400
    with tx() as c:
        r = c.execute(
            "SELECT 1 FROM monologues WHERE node_id=? AND ts>=? LIMIT 1",
            (node_id, today0),
        ).fetchone()
        return r is not None


def bury(node_id: int, epitaph: str | None = None) -> None:
    """标记机器死亡"""
    with tx() as c:
        c.execute(
            "UPDATE nodes SET died_at=?, epitaph=? WHERE id=?",
            (int(time.time()), epitaph, node_id),
        )


def set_config(k: str, v: Any) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO config(k, v) VALUES(?,?)", (k, str(v)))


def get_config(k: str, default: Any = None) -> Any:
    with tx() as c:
        r = c.execute("SELECT v FROM config WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default


def stats() -> dict:
    with tx() as c:
        total = c.execute("SELECT COUNT(*) n FROM nodes").fetchone()["n"]
        alive = c.execute("SELECT COUNT(*) n FROM nodes WHERE died_at IS NULL").fetchone()["n"]
        samples = c.execute("SELECT COUNT(*) n FROM samples").fetchone()["n"]
        monos = c.execute("SELECT COUNT(*) n FROM monologues").fetchone()["n"]
        avg_days = c.execute(
            "SELECT AVG(days_left) v FROM nodes WHERE died_at IS NULL"
        ).fetchone()["v"]
    return {
        "total": total,
        "alive": alive,
        "dead": total - alive,
        "samples": samples,
        "monologues": monos,
        "avg_days_left": round(avg_days or 0),
    }
