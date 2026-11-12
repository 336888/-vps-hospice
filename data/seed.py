import argparse
import math
import random
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, monologue, survival

NODES = [
    ("RackNerd",    "Los Angeles", "1C1G/20G",    8.0, 0.021),
    ("RackNerd",    "Seattle",     "2C2G/40G",   18.0, 0.018),
    ("BuyVM",       "Las Vegas",   "1C1G/20G",   42.0, 0.008),
    ("BuyVM",       "Luxembourg",  "1C512M/10G", 24.0, 0.006),
    ("Hetzner",     "Falkenstein", "2C4G/40G",   60.0, 0.003),
    ("Hetzner",     "Helsinki",    "2C4G/40G",   60.0, 0.004),
    ("Contabo",     "Nuremberg",   "4C8G/200G",  80.0, 0.042),
    ("GreenCloud",  "Hong Kong",   "2C2G/30G",   96.0, 0.015),
    ("GreenCloud",  "Tokyo",       "1C1G/20G",   60.0, 0.019),
    ("VirMach",     "Dallas",      "1C1G/15G",    6.0, 0.087),
    ("HostBrr",     "Falkenstein", "1C1G/20G",   12.0, 0.011),
    ("Naranjatech", "Mumbai",      "1C512M/10G",  5.0, 0.055),
]

BASE = {
    "Hetzner":     (2400, 1800, 38000),
    "BuyVM":       (1950, 1450, 31000),
    "HostBrr":     (1800, 1200, 28000),
    "RackNerd":    (1650, 950, 22000),
    "GreenCloud":  (1500, 880, 19000),
    "Contabo":     (1300, 720, 15500),
    "Naranjatech": (1100, 620, 13000),
    "VirMach":     (950, 480, 9800),
}

OVERSELL = {
    "VirMach": 4.2, "Contabo": 3.1, "Naranjatech": 2.6,
    "GreenCloud": 1.2, "RackNerd": 1.0,
    "BuyVM": 0.3, "HostBrr": 0.4, "Hetzner": 0.1,
}

MONTHS = 24


def j(v, pct=0.06):
    return v * (1 + random.uniform(-pct, pct))


def gen(provider, decay, started_at):
    cpu0, disk0, iops0 = BASE.get(provider, (1500, 900, 20000))
    over = OVERSELL.get(provider, 0.8)
    out = []

    for m in range(MONTHS):
        slow = 0.3 if m in (7, 8, 9, 15, 16) else 1.0
        d = math.exp(-decay * m * slow)

        cpu = j(cpu0 * d, 0.05)
        disk = j(disk0 * (d ** 1.4), 0.08)
        iops = j(iops0 * (d ** 1.6), 0.10)
        steal = max(0.0, min(12.0, j(over * (0.6 + m / 40), 0.35)))
        load = max(0.01, j(0.15 + over * (m / 60), 0.5))

        out.append({
            "ts": started_at + m * 30 * 86400,
            "cpu_single": int(cpu),
            "cpu_multi": int(cpu * 1.9),
            "disk_read": round(disk, 1),
            "disk_write": round(disk * 0.85, 1),
            "iops_4k": int(iops),
            "ping_avg": round(j(180, 0.15), 1),
            "dl_speed": round(j(850, 0.2), 1),
            "cpu_steal": round(steal, 2),
            "load1": round(load, 2),
            "uptime_days": (m + 1) * 30,
        })
    return out


def run(reset=False):
    db.init()

    if reset:
        with db.tx() as c:
            c.execute("DELETE FROM monologues")
            c.execute("DELETE FROM samples")
            c.execute("DELETE FROM nodes")
        print("cleared")

    now = int(time.time())

    for provider, location, plan, price, decay in NODES:
        months_ago = random.randint(8, 24)
        started = now - months_ago * 30 * 86400
        nid = db.add_node(provider=provider, location=location, plan=plan,
                          price=price, started_at=started)

        rows = [s for s in gen(provider, decay, started) if s["ts"] <= now]
        for s in rows:
            ts = s.pop("ts")
            db.add_sample(nid, s, ts=ts)

        s_all = db.samples(nid)
        pred = survival.predict(s_all)
        db.save_prediction(nid, pred.as_dict())

        for i in range(3):
            text, mood, dh = monologue.generate(
                db.node(nid), s_all, pred.as_dict(), "zh")
            db.add_monologue(nid, text, mood, dh, ts=now - i * 86400)

        p = pred.as_dict()
        print("#%-2d %-12s %-12s health %5.1f  days %5d  %s" % (
            nid, provider, location, p["health"], p["days_left"], p["death_date"]))

    dead_specs = [
        ("VirMach", "Amsterdam", "1C1G/15G", 5.0,
         "硬盘彻底坏了。他们说会换，但没人来。"),
        ("Contabo", "St. Louis", "2C4G/100G", 40.0,
         "超售到最后，我已经不知道自己是谁了。"),
    ]
    for provider, location, plan, price, epitaph in dead_specs:
        started = now - 800 * 86400
        nid = db.add_node(provider=provider, location=location, plan=plan,
                          price=price, started_at=started)
        for s in gen(provider, 0.09, started)[:20]:
            ts = s.pop("ts")
            db.add_sample(nid, s, ts=ts)
        db.bury(nid, epitaph)
        db.add_monologue(nid, epitaph, "dying", ts=now - 30 * 86400)
        print("#%-2d %-12s %-12s dead" % (nid, provider, location))

    st = db.stats()
    print()
    print("%d nodes / %d samples / %d monologues" % (
        st["total"], st["samples"], st["monologues"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    run(reset=a.reset)
