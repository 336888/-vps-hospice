import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, monologue, survival

PLAN = [
    ("RackNerd", "Los Angeles", "1C1G/20G", 8, .021),
    ("RackNerd", "Seattle", "2C2G/40G", 18, .018),
    ("BuyVM", "Las Vegas", "1C1G/20G", 42, .008),
    ("BuyVM", "Luxembourg", "1C512M/10G", 24, .006),
    ("Hetzner", "Falkenstein", "2C4G/40G", 60, .003),
    ("Hetzner", "Helsinki", "2C4G/40G", 60, .004),
    ("Contabo", "Nuremberg", "4C8G/200G", 80, .042),
    ("GreenCloud", "Hong Kong", "2C2G/30G", 96, .015),
    ("GreenCloud", "Tokyo", "1C1G/20G", 60, .019),
    ("VirMach", "Dallas", "1C1G/15G", 6, .087),
    ("HostBrr", "Falkenstein", "1C1G/20G", 12, .011),
    ("Naranjatech", "Mumbai", "1C512M/10G", 5, .055),
]

BASE = {
    "Hetzner": (2400, 1800, 38000), "BuyVM": (1950, 1450, 31000),
    "HostBrr": (1800, 1200, 28000), "RackNerd": (1650, 950, 22000),
    "GreenCloud": (1500, 880, 19000), "Contabo": (1300, 720, 15500),
    "Naranjatech": (1100, 620, 13000), "VirMach": (950, 480, 9800),
}

OVERSELL = {"VirMach": 4.2, "Contabo": 3.1, "Naranjatech": 2.6, "GreenCloud": 1.2,
            "RackNerd": 1.0, "BuyVM": .3, "HostBrr": .4, "Hetzner": .1}

CERTIFIED = {"Hetzner", "BuyVM", "HostBrr", "RackNerd"}
MONTHS = 24


def _series(provider, decay, start):
    cpu0, disk0, iops0 = BASE.get(provider, (1500, 900, 20000))
    over = OVERSELL.get(provider, .8)
    out = []
    for m in range(MONTHS):
        d = math.exp(-decay * m * (.3 if m in (7, 8, 9, 15, 16) else 1))
        cpu = cpu0 * d * random.uniform(.95, 1.05)
        out.append({
            "ts": start + m * 30 * 86400,
            "cpu_single": int(cpu),
            "cpu_multi": int(cpu * 1.9),
            "disk_read": round(disk0 * d ** 1.4 * random.uniform(.92, 1.08), 1),
            "disk_write": round(disk0 * d ** 1.4 * .85 * random.uniform(.9, 1.1), 1),
            "iops_4k": int(iops0 * d ** 1.6 * random.uniform(.9, 1.1)),
            "ping_avg": round(random.uniform(150, 210), 1),
            "dl_speed": round(random.uniform(680, 1020), 1),
            "cpu_steal": round(max(0, min(12, over * (.6 + m / 40) * random.uniform(.65, 1.35))), 2),
            "load1": round(max(.01, .15 + over * (m / 60) * random.uniform(.5, 1.5)), 2),
            "uptime_days": (m + 1) * 30,
        })
    return out


def run(reset=False):
    db.init()
    if reset:
        with db.tx() as c:
            for t in ("monologues", "leads", "samples", "nodes"):
                c.execute(f"DELETE FROM {t}")

    now = int(time.time())

    for provider, loc, plan, price, decay in PLAN:
        nid = db.add_node(provider, loc, plan, price, now - random.randint(8, 24) * 2592000)
        for s in _series(provider, decay, db.node(nid)["started_at"]):
            if s["ts"] <= now:
                db.add_sample(nid, s, ts=s.pop("ts"))

        rows = db.samples(nid)
        pred = survival.predict(rows)
        db.save_pred(nid, pred.as_dict())
        for i in range(3):
            text, mood, h = monologue.generate(db.node(nid), rows, pred.as_dict(), "en")
            db.add_monologue(nid, text, mood, h, ts=now - i * 86400)
        print(f"{nid:>3} {provider:<12} {loc:<12} {pred.health:>6.1f} {pred.days:>5}")

    for provider, loc, plan, price, epitaph in (
        ("VirMach", "Amsterdam", "1C1G/15G", 5, "The disk died. They said they'd replace it. Nobody came."),
        ("Contabo", "St. Louis", "2C4G/100G", 40, "Oversold to the point where I forgot what I was."),
    ):
        start = now - 800 * 86400
        nid = db.add_node(provider, loc, plan, price, start)
        for s in _series(provider, .09, start)[:20]:
            db.add_sample(nid, s, ts=s.pop("ts"))
        db.bury(nid, epitaph)
        db.add_monologue(nid, epitaph, "dying", ts=now - 2592000)
        print(f"{nid:>3} {provider:<12} {loc:<12}  dead")

    _certify()
    st = db.stats()
    print(f"\n{st['total']} nodes · {st['samples']} samples · {st['monologues']} monologues")



def _certify():
    for n in db.nodes(alive_only=True):
        p = survival.predict(db.samples(n["id"])).as_dict()
        t = survival.tier(p["health"], p["days_left"], p["oversell"])
        if not t:
            continue
        db.set_tier(n["id"], t)
        if n["provider"] in CERTIFIED:
            db.certify(n["id"], f"https://example.com/order?ref=hospice&plan={n['id']}",
                       "sales@example.com")


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
