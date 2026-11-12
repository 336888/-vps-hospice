import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

DEATH = 0.30
W = {"cpu": 0.40, "disk": 0.35, "iops": 0.25}
K_MIN, K_MAX = 1e-6, 0.20
HORIZON = 3650
TIERS = ("platinum", "gold", "silver")
TIER_RANK = dict(zip(TIERS, (3, 2, 1)))


@dataclass
class Fit:
    key: str
    k: float
    monthly: float
    y0: float
    last: float
    r2: float

    @property
    def total(self):
        return round((1 - self.last / self.y0) * 100, 1) if self.y0 else 0.0


@dataclass
class Pred:
    days: int
    death: str
    health: float
    conf: str
    n: int
    fits: list = field(default_factory=list)
    oversell: float = 0.0
    mining: float = 0.0
    notes: list = field(default_factory=list)

    def as_dict(self):
        return {
            "days_left": self.days,
            "death_date": self.death,
            "health": self.health,
            "confidence": self.conf,
            "samples": self.n,
            "oversell": self.oversell,
            "mining_risk": self.mining,
            "warnings": self.notes,
            "decay": [
                {
                    "key": f.key,
                    "rate_per_month_pct": f.monthly,
                    "decay_pct": f.total,
                    "initial": int(round(f.y0)) if f.key != "disk" else round(f.y0, 1),
                    "current": int(round(f.last)) if f.key != "disk" else round(f.last, 1),
                    "r_squared": round(f.r2, 3),
                }
                for f in self.fits
            ],
        }


def _exp(x, k, y0):
    return y0 * np.exp(-k * x)


def _fit(x, y, key):
    y = np.maximum(y, 0)
    m = y > 0
    if m.sum() < 3 or x[m][-1] - x[m][0] < 1:
        return None
    x, y = x[m], y[m]
    try:
        (k, y0), _ = curve_fit(
            _exp, x, y, p0=[1e-3, y[0]],
            bounds=([K_MIN, y[0] * .5], [K_MAX, y[0] * 2]), maxfev=8000,
        )
    except (RuntimeError, ValueError):
        return None
    resid = ((y - _exp(x, k, y0)) ** 2).sum()
    total = ((y - y.mean()) ** 2).sum()
    return Fit(key, float(k), round(float(k) * 3000, 2),
               float(y0), float(y[-1]), float(1 - resid / total) if total else 0.0)


def _oversell(rows):
    v = [float(r.get("cpu_steal") or 0) for r in rows]
    if not v:
        return 0.0
    return round(min(1.0, min(np.mean(v) / 5, 1) * .7 + min(np.std(v) / 3, 1) * .3), 2)


def _mining(rows):
    if len(rows) < 4:
        return 0.0
    c0, c1 = (float(r.get("cpu_single") or 0) for r in (rows[0], rows[-1]))
    if c0 <= 0:
        return 0.0
    drop = max(0.0, 1 - c1 / c0)
    rise = max(0.0, float(rows[-1].get("load1") or 0) - float(rows[0].get("load1") or 0))
    return round(min(0.95, drop * 1.8 + rise * .15), 2) if drop > .2 and rise > .3 \
        else round(min(0.5, drop * .4), 2)


def predict(rows, now=None):
    if not rows:
        return Pred(0, "-", 0, "none", 0)

    rows = sorted(rows, key=lambda r: r["ts"])
    now = now or rows[-1]["ts"]
    x = np.array([(r["ts"] - rows[0]["ts"]) / 86400.0 for r in rows])

    fits = [f for f in (
        _fit(x, np.array([float(r.get(c) or 0) for r in rows]), k)
        for k, c in (("cpu", "cpu_single"), ("disk", "disk_read"), ("iops", "iops_4k"))
    ) if f]

    if not fits:
        return Pred(0, "-", 100, "none", len(rows), notes=["insufficient data"])

    wsum = sum(W[f.key] for f in fits)
    k = sum(f.k * W[f.key] for f in fits) / wsum
    cpu = next((f for f in fits if f.key == "cpu"), fits[0])
    health = min(1.0, max(0.0, cpu.last / cpu.y0 if cpu.y0 else 1.0))

    notes = []
    if k <= K_MIN:
        days = HORIZON
        notes.append("decay near zero")
    elif health <= DEATH:
        days = 0
        notes.append("below threshold, migrate now")
    else:
        days = max(0, min(int(-math.log(DEATH / health) / k), HORIZON))

    n = len(rows)
    conf = "high" if n >= 24 and all(f.r2 > .5 for f in fits) else "medium" if n >= 8 else "low"
    if conf == "low":
        notes.append("few samples")

    om, mr = _oversell(rows), _mining(rows)
    if om > .6:
        notes.append("heavy oversell")
    if mr > .5:
        notes.append("possible neighbor mining")
    disk = next((f for f in fits if f.key == "disk"), None)
    if disk and disk.monthly > 4:
        notes.append("disk aging fast")

    return Pred(days, time.strftime("%Y-%m-%d", time.localtime(now + days * 86400)),
                round(health * 100, 1), conf, n, fits, om, mr, notes)


def tier(health, days, oversell):
    if health >= 88 and days >= 1500 and oversell <= .25:
        return "platinum"
    if health >= 75 and days >= 900 and oversell <= .40:
        return "gold"
    if health >= 60 and days >= 400 and oversell <= .60:
        return "silver"
    return None
