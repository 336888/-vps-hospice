"""
演示数据生成器

生成 12 台机器，每台 24 个月历史（每月 1 个采样点），
数据带真实的衰减曲线，让页面一打开就有内容。

用法：
    python3 -m data.seed          # 生成演示数据
    python3 -m data.seed --reset  # 清空后重新生成
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time

sys.path.insert(0, __file__.rsplit("/data/", 1)[0])

from app import db, monologue, survival


# 12 家真实 LET 商家（数据是模拟的，仅供演示）
NODES = [
    # provider, location, plan, price, 起始健康度, 月衰减率
    ("RackNerd",    "Los Angeles",  "1C1G/20G",   8.0,  100, 0.021),
    ("RackNerd",    "Seattle",      "2C2G/40G",  18.0,  100, 0.018),
    ("BuyVM",       "Las Vegas",    "1C1G/20G",  42.0,  100, 0.008),
    ("BuyVM",       "Luxembourg",   "1C512M/10G", 24.0, 100, 0.006),
    ("Hetzner",     "Falkenstein",  "2C4G/40G",  60.0,  100, 0.003),
    ("Hetzner",     "Helsinki",     "2C4G/40G",  60.0,  100, 0.004),
    ("Contabo",     "Nuremberg",    "4C8G/200G", 80.0,  100, 0.042),
    ("GreenCloud",  "Hong Kong",    "2C2G/30G",  96.0,  100, 0.015),
    ("GreenCloud",  "Tokyo",        "1C1G/20G",  60.0,  100, 0.019),
    ("VirMach",     "Dallas",       "1C1G/15G",   6.0,  100, 0.087),
    ("HostBrr",     "Falkenstein",  "1C1G/20G",  12.0,  100, 0.011),
    ("Naranjatech", "Mumbai",       "1C512M/10G",  5.0, 100, 0.055),
]

# 每台机器的基准性能（不同档次的机器）
BASE = {
    "Hetzner":     {"cpu": 2400, "disk": 1800, "iops": 38000},
    "BuyVM":       {"cpu": 1950, "disk": 1450, "iops": 31000},
    "HostBrr":     {"cpu": 1800, "disk": 1200, "iops": 28000},
    "RackNerd":    {"cpu": 1650, "disk": 950,  "iops": 22000},
    "GreenCloud":  {"cpu": 1500, "disk": 880,  "iops": 19000},
    "Contabo":     {"cpu": 1300, "disk": 720,  "iops": 15500},
    "Naranjatech": {"cpu": 1100, "disk": 620,  "iops": 13000},
    "VirMach":     {"cpu": 950,  "disk": 480,  "iops": 9800},
}

MONTHS = 24


def jitter(v: float, pct: float = 0.06) -> float:
    """加随机波动，模拟真实测量误差"""
    return v * (1 + random.uniform(-pct, pct))


def gen_samples(provider: str, decay_per_month: float, started_at: int) -> list[dict]:
    """生成一台机器的 24 个月历史"""
    base = BASE.get(provider, {"cpu": 1500, "disk": 900, "iops": 20000})
    out = []

    # 超售程度（有的商家很严重）
    oversell_level = {
        "VirMach": 4.2, "Contabo": 3.1, "Naranjatech": 2.6,
        "GreenCloud": 1.2, "RackNerd": 1.0,
        "BuyVM": 0.3, "HostBrr": 0.4, "Hetzner": 0.1,
    }.get(provider, 0.8)

    for m in range(MONTHS):
        ts = started_at + m * 30 * 86400

        # 衰减：指数 + 随机慢化的段（有的机器某几个月很稳）
        segment_slowdown = 1.0
        if m in (7, 8, 9) or m in (15, 16):
            segment_slowdown = 0.3   # 这几个月衰减变慢（可能换了邻居）

        decay = math.exp(-decay_per_month * m * segment_slowdown)

        cpu = jitter(base["cpu"] * decay, 0.05)
        disk = jitter(base["disk"] * (decay ** 1.4), 0.08)     # 磁盘衰减更快
        iops = jitter(base["iops"] * (decay ** 1.6), 0.10)

        # steal 随超售程度和时间增长
        steal = jitter(oversell_level * (0.6 + m / 40), 0.35)
        steal = max(0.0, min(12.0, steal))

        # load 随超售上升
        load = jitter(0.15 + oversell_level * (m / 60), 0.5)
        load = max(0.01, load)

        out.append({
            "ts": ts,
            "cpu_single": int(cpu),
            "cpu_multi": int(cpu * 1.9),
            "disk_read": round(disk, 1),
            "disk_write": round(disk * 0.85, 1),
            "iops_4k": int(iops),
            "ping_avg": round(jitter(180 if "Hong" not in str(base) else 40, 0.15), 1),
            "dl_speed": round(jitter(850, 0.2), 1),
            "cpu_steal": round(steal, 2),
            "load1": round(load, 2),
            "uptime_days": (m + 1) * 30,
        })
    return out


def seed(reset: bool = False) -> None:
    db.init_db()

    if reset:
        with db.tx() as c:
            c.execute("DELETE FROM monologues")
            c.execute("DELETE FROM samples")
            c.execute("DELETE FROM nodes")
        print("已清空旧数据")

    now = int(time.time())
    created = 0

    for provider, location, plan, price, start_health, decay in NODES:
        # 开通时间：随机 8-24 个月前
        months_ago = random.randint(8, 24)
        started_at = now - months_ago * 30 * 86400

        nid = db.add_node(
            provider=provider,
            location=location,
            plan=plan,
            price=price,
            started_at=started_at,
        )

        samples = gen_samples(provider, decay, started_at)
        # 只保留到"现在"为止的采样
        samples = [s for s in samples if s["ts"] <= now]
        for s in samples:
            ts = s.pop("ts")
            db.add_sample(nid, s, ts=ts)

        # 算预测
        all_s = db.node_samples(nid)
        pred = survival.predict(all_s)
        db.update_prediction(nid, pred.to_dict())

        # 生成 3 条历史独白
        for i in range(3):
            offset = i * 86400
            text, mood, dh = monologue.generate(
                db.get_node(nid), all_s, pred.to_dict()
            )
            db.add_monologue(nid, text, mood, dh, ts=now - offset)

        d = pred.to_dict()
        print("  #%-2d %-12s %-14s  健康 %5.1f  剩余 %5d 天  (%s)" % (
            nid, provider, location, d["health"], d["days_left"], d["death_date"]
        ))
        created += 1

    # 制造 2 台已死亡的机器
    dead_specs = [
        ("VirMach", "Amsterdam", "1C1G/15G", 5.0, "硬盘彻底坏了。他们说会换，但没人来。"),
        ("Contabo", "St. Louis", "2C4G/100G", 40.0, "超售到最后，我已经不知道自己是谁了。"),
    ]
    for provider, location, plan, price, epitaph in dead_specs:
        started = now - 800 * 86400
        nid = db.add_node(provider=provider, location=location, plan=plan,
                          price=price, started_at=started)
        samples = gen_samples(provider, 0.09, started)[:20]
        for s in samples:
            ts = s.pop("ts")
            db.add_sample(nid, s, ts=ts)
        db.bury(nid, epitaph)
        db.add_monologue(nid, epitaph, "dying", ts=now - 30 * 86400)
        print("  #%-2d %-12s %-14s  💀 已死亡" % (nid, provider, location))

    st = db.stats()
    print()
    print("完成：%d 台机器 / %d 次采样 / %d 条独白" % (
        st["total"], st["samples"], st["monologues"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清空后重新生成")
    args = ap.parse_args()
    seed(reset=args.reset)
