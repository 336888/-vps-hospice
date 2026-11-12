import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import optimize

DEATH_AT = 0.30
WEIGHTS = {"cpu": 0.40, "disk": 0.35, "iops": 0.25}
MIN_K = 1e-6
MAX_K = 0.20


@dataclass
class Fit:
    key: str
    k: float
    monthly_pct: float
    y0: float
    last: float
    r2: float

    @property
    def total_pct(self):
        if self.y0 <= 0:
            return 0.0
        return round((1 - self.last / self.y0) * 100, 1)


@dataclass
class Result:
    days: int
    death_date: str
    health: float
    confidence: str
    samples: int
    fits: list = field(default_factory=list)
    oversell: float = 0.0
    mining: float = 0.0
    notes: list = field(default_factory=list)

    def as_dict(self):
        return {
            "days_left": self.days,
            "death_date": self.death_date,
            "health": self.health,
            "confidence": self.confidence,
            "samples": self.samples,
            "oversell": self.oversell,
            "mining_risk": self.mining,
            "warnings": self.notes,
            "decay": [
                {
                    "key": f.key,
                    "rate_per_month_pct": f.monthly_pct,
                    "decay_pct": f.total_pct,
                    "initial": int(round(f.y0)) if f.key != "disk" else round(f.y0, 1),
                    "current": int(round(f.last)) if f.key != "disk" else round(f.last, 1),
                    "r_squared": round(f.r2, 3),
                }
                for f in self.fits
            ],
        }


def _model(x, k, y0):
    return y0 * np.exp(-k * x)


def _fit(days, values, key):
    m = np.isfinite(values) & (values > 0)
    if m.sum() < 3:
        return None
    x, y = days[m], values[m]
    if x[-1] - x[0] < 1:
        return None
    try:
        (k, y0), _ = optimize.curve_fit(
            _model, x, y,
            p0=[0.001, float(y[0])],
            bounds=([MIN_K, float(y[0]) * 0.5], [MAX_K, float(y[0]) * 2.0]),
            maxfev=8000,
        )
        pred = _model(x, k, y0)
        ss_res = float(((y - pred) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return Fit(key, float(k), round(float(k) * 30 * 100, 2),
                   float(y0), float(y[-1]), max(0.0, min(1.0, r2)))
    except Exception:
        return None


def oversell_index(samples):
    v = [float(s.get("cpu_steal") or 0) for s in samples]
    v = [x for x in v if x >= 0]
    if not v:
        return 0.0
    avg, sd = float(np.mean(v)), float(np.std(v))
    return round(min(1.0, min(avg / 5.0, 1.0) * 0.7 + min(sd / 3.0, 1.0) * 0.3), 2)


def mining_risk(samples):
    if len(samples) < 4:
        return 0.0
    a, b = samples[0], samples[-1]
    c0, c1 = float(a.get("cpu_single") or 0), float(b.get("cpu_single") or 0)
    if c0 <= 0:
        return 0.0
    drop = max(0.0, 1 - c1 / c0)
    l0, l1 = float(a.get("load1") or 0), float(b.get("load1") or 0)
    rise = max(0.0, l1 - l0)
    if drop > 0.20 and rise > 0.3:
        return round(min(0.95, drop * 1.8 + rise * 0.15), 2)
    return round(max(0.0, min(0.5, drop * 0.4)), 2)


def predict(samples, now=None):
    if not samples:
        return Result(0, "-", 0, "none", 0, notes=["no samples"])

    samples = sorted(samples, key=lambda s: s["ts"])
    now = now or int(samples[-1]["ts"])
    days = np.array([(s["ts"] - samples[0]["ts"]) / 86400.0 for s in samples], float)

    fits = []
    for key, col in (("cpu", "cpu_single"), ("disk", "disk_read"), ("iops", "iops_4k")):
        vals = np.array([float(s.get(col) or 0) for s in samples], float)
        f = _fit(days, vals, key)
        if f:
            fits.append(f)

    if not fits:
        return Result(0, "-", 100, "none", len(samples),
                      notes=["not enough data points"])

    wsum = sum(WEIGHTS.get(f.key, 0) for f in fits)
    k = sum(f.k * WEIGHTS.get(f.key, 0) for f in fits) / wsum if wsum else 0

    cpu = next((f for f in fits if f.key == "cpu"), fits[0])
    health = cpu.last / cpu.y0 if cpu.y0 > 0 else 1.0
    health = max(0.0, min(1.0, health))

    notes = []
    if k <= MIN_K:
        days_left = 3650
        notes.append("decay near zero")
    elif health <= DEATH_AT:
        days_left = 0
        notes.append("below threshold, migrate now")
    else:
        days_left = max(0, min(int(-math.log(DEATH_AT / health) / k), 3650))

    death = time.strftime("%Y-%m-%d", time.localtime(now + days_left * 86400))

    n = len(samples)
    if n >= 24 and all(f.r2 > 0.5 for f in fits):
        conf = "high"
    elif n >= 8:
        conf = "medium"
    else:
        conf = "low"
        notes.append("few samples, prediction may drift")

    om = oversell_index(samples)
    mr = mining_risk(samples)
    if om > 0.6:
        notes.append("heavy oversell")
    if mr > 0.5:
        notes.append("possible neighbor mining")
    disk = next((f for f in fits if f.key == "disk"), None)
    if disk and disk.monthly_pct > 4.0:
        notes.append("disk aging fast, check SSD wear")

    return Result(days_left, death, round(health * 100, 1), conf, n,
                  fits, om, mr, notes)


def badge(health):
    if health >= 81:
        return "g", "bolt"
    if health >= 61:
        return "b", "ok"
    if health >= 41:
        return "y", "warn"
    if health >= 21:
        return "o", "fire"
    return "r", "skull"


def tier(health, days, oversell):
    """Certification tier for a node."""
    if health >= 88 and days >= 1500 and oversell <= 0.25:
        return "platinum"
    if health >= 75 and days >= 900 and oversell <= 0.40:
        return "gold"
    if health >= 60 and days >= 400 and oversell <= 0.60:
        return "silver"
    return None


TIER_RANK = {"platinum": 3, "gold": 2, "silver": 1, None: 0}
