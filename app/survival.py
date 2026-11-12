"""
生存分析 —— 预测 VPS 的剩余寿命

核心方法：指数衰减拟合 + 威布尔生存函数

为什么不用线性外推：
    线性会得出"永远不死"的荒谬结论（y = 2100 - 20t，t 很大时 y 为负）
    真实硬件的性能衰减是单调递减的指数曲线

为什么用威布尔：
    可靠性工程的标准分布，S(t) = exp(-(t/λ)^k)
    当 k=1 退化为指数分布，k<1 表示早期失效，k>1 表示磨损
    VPS 的衰减通常 k<1（前期衰减快，后期趋缓）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import optimize


# 健康度降到这个值算"死亡"
DEATH_THRESHOLD = 0.30

# 各项指标的权重（加起来 = 1.0）
WEIGHTS = {
    "cpu": 0.40,     # CPU 性能最影响体验
    "disk": 0.35,    # 磁盘衰减最能反映硬件老化
    "iops": 0.25,    # 随机 IOPS 反映 SSD 磨损
}

# 衰减率的合理范围（每月）
MIN_RATE = 1e-6
MAX_RATE = 0.20      # 每月 20% 已经是极端情况


@dataclass
class DecayFit:
    """单个指标的衰减拟合结果"""
    key: str
    rate_per_day: float          # 日衰减率 k
    rate_per_month_pct: float    # 月衰减百分比
    initial: float               # 初始值
    current: float               # 当前值
    r_squared: float             # 拟合度

    @property
    def decay_pct(self) -> float:
        """从开始到现在的总衰减百分比"""
        if self.initial <= 0:
            return 0.0
        return round((1 - self.current / self.initial) * 100, 1)


@dataclass
class Prediction:
    """寿命预测结果"""
    days_left: int
    death_date: str
    health: float                # 当前健康度 0-100
    confidence: str              # high / medium / low
    samples: int
    decay: list[DecayFit] = field(default_factory=list)
    oversell: float = 0.0        # 超售指数 0-1
    mining_risk: float = 0.0     # 邻居挖矿概率 0-1
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days_left": self.days_left,
            "death_date": self.death_date,
            "health": self.health,
            "confidence": self.confidence,
            "samples": self.samples,
            "oversell": self.oversell,
            "mining_risk": self.mining_risk,
            "warnings": self.warnings,
            "decay": [
                {
                    "key": d.key,
                    "rate_per_month_pct": d.rate_per_month_pct,
                    "decay_pct": d.decay_pct,
                    "initial": round(d.initial, 1),
                    "current": round(d.current, 1),
                    "r_squared": round(d.r_squared, 3),
                }
                for d in self.decay
            ],
        }


def _exp_model(x: np.ndarray, k: float, y0: float) -> np.ndarray:
    """指数衰减模型 y = y0 * exp(-k*x)"""
    return y0 * np.exp(-k * x)


def _fit_decay(days: np.ndarray, values: np.ndarray, key: str) -> DecayFit | None:
    """
    拟合单个指标的衰减

    Args:
        days: 距首次采样的天数（递增）
        values: 对应的指标值
        key: 指标名

    Returns:
        DecayFit 或 None（数据不足/全为 0）
    """
    # 过滤无效值
    mask = np.isfinite(values) & (values > 0)
    if mask.sum() < 3:
        return None

    x = days[mask]
    y = values[mask]

    if len(x) < 3 or x[-1] - x[0] < 1:
        return None

    try:
        # 用首值做初值，避免收敛到 0
        popt, _ = optimize.curve_fit(
            _exp_model,
            x,
            y,
            p0=[0.001, float(y[0])],
            bounds=([MIN_RATE, float(y[0]) * 0.5], [MAX_RATE, float(y[0]) * 2.0]),
            maxfev=8000,
        )
        k, y0 = float(popt[0]), float(popt[1])

        # R² 拟合度
        y_pred = _exp_model(x, k, y0)
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        return DecayFit(
            key=key,
            rate_per_day=k,
            rate_per_month_pct=round(k * 30 * 100, 2),
            initial=y0,
            current=float(y[-1]),
            r_squared=max(0.0, min(1.0, r2)),
        )
    except Exception:
        return None


def compute_oversell(samples: list[dict]) -> float:
    """
    超售指数

    两个维度：
      ① CPU steal 的均值（越高说明被抢得越多）
      ② steal 的波动（忽高忽低 = 邻居活动频繁）

    返回 0-1，越高越糟
    """
    steals = [float(s.get("cpu_steal") or 0) for s in samples]
    steals = [s for s in steals if s >= 0]
    if not steals:
        return 0.0

    avg = float(np.mean(steals))
    std = float(np.std(steals))

    # steal 5% 以上算严重
    avg_score = min(avg / 5.0, 1.0) * 0.7
    std_score = min(std / 3.0, 1.0) * 0.3

    return round(min(1.0, avg_score + std_score), 2)


def compute_mining_risk(samples: list[dict]) -> float:
    """
    邻居挖矿概率

    特征：你的 CPU 跑分下降，但系统 load 上升
    → 说明有人在抢 CPU（挖矿/编译/压测）
    """
    if len(samples) < 4:
        return 0.0

    first, last = samples[0], samples[-1]
    cpu_first = float(first.get("cpu_single") or 0)
    cpu_last = float(last.get("cpu_single") or 0)

    if cpu_first <= 0:
        return 0.0

    cpu_drop = max(0.0, 1 - cpu_last / cpu_first)

    load_first = float(first.get("load1") or 0)
    load_last = float(last.get("load1") or 0)
    load_rise = max(0.0, load_last - load_first)

    # CPU 掉了 20% 以上，且 load 上升 → 高度可疑
    if cpu_drop > 0.20 and load_rise > 0.3:
        return round(min(0.95, cpu_drop * 1.8 + load_rise * 0.15), 2)

    # 单看 CPU 衰减
    return round(max(0.0, min(0.5, cpu_drop * 0.4)), 2)


def predict(samples: list[dict], now_ts: int | None = None) -> Prediction:
    """
    主入口：给定历史采样，预测剩余寿命

    Args:
        samples: [{"ts": int, "cpu_single": int, "disk_read": float,
                   "iops_4k": int, "cpu_steal": float, "load1": float}, ...]
                 必须按 ts 升序
        now_ts: 当前时间戳（默认用最后一个采样）

    Returns:
        Prediction
    """
    if not samples:
        return Prediction(
            days_left=0, death_date="未知", health=0,
            confidence="none", samples=0,
            warnings=["没有采样数据"],
        )

    samples = sorted(samples, key=lambda s: s["ts"])
    now = now_ts or int(samples[-1]["ts"])
    t0 = int(samples[0]["ts"])

    days = np.array([(s["ts"] - t0) / 86400.0 for s in samples], dtype=float)

    # 三个指标的衰减拟合
    fits: list[DecayFit] = []
    for key, field_name in [
        ("cpu", "cpu_single"),
        ("disk", "disk_read"),
        ("iops", "iops_4k"),
    ]:
        vals = np.array([float(s.get(field_name) or 0) for s in samples], dtype=float)
        f = _fit_decay(days, vals, key)
        if f:
            fits.append(f)

    warnings: list[str] = []

    if not fits:
        # 数据太少，无法拟合
        return Prediction(
            days_left=0, death_date="无法预测", health=100,
            confidence="none", samples=len(samples),
            warnings=["采样点不足（至少需要 3 个有效数据点）"],
        )

    # 综合衰减率（加权平均）
    total_w = sum(WEIGHTS.get(f.key, 0) for f in fits)
    k = sum(f.rate_per_day * WEIGHTS.get(f.key, 0) for f in fits) / total_w if total_w > 0 else 0

    # 当前健康度：以 CPU 为基准（最直观）
    cpu_fit = next((f for f in fits if f.key == "cpu"), fits[0])
    health = cpu_fit.current / cpu_fit.initial if cpu_fit.initial > 0 else 1.0
    health = max(0.0, min(1.0, health))

    # 预测剩余天数：解 health * exp(-k*t) = DEATH_THRESHOLD
    if k <= MIN_RATE:
        days_left = 3650
        warnings.append("衰减率接近 0，可能不会自然死亡")
    elif health <= DEATH_THRESHOLD:
        days_left = 0
        warnings.append("健康度已低于死亡线，建议立即迁移")
    else:
        days_left = int(-math.log(DEATH_THRESHOLD / health) / k)
        days_left = max(0, min(days_left, 3650))

    # 死亡日期
    import datetime as _dt
    death_ts = now + days_left * 86400
    death_date = _dt.datetime.fromtimestamp(death_ts).strftime("%Y-%m-%d")

    # 置信度
    n = len(samples)
    if n >= 24 and all(f.r_squared > 0.5 for f in fits):
        confidence = "high"
    elif n >= 8:
        confidence = "medium"
    else:
        confidence = "low"
        warnings.append("采样点较少，预测可能不准")

    # 超售 & 挖矿
    oversell = compute_oversell(samples)
    mining = compute_mining_risk(samples)

    if oversell > 0.6:
        warnings.append("超售严重（steal %.1f%%）" % (np.mean([s.get("cpu_steal") or 0 for s in samples])))
    if mining > 0.5:
        warnings.append("检测到疑似邻居挖矿")

    # 磁盘衰减特别快时单独警告
    disk_fit = next((f for f in fits if f.key == "disk"), None)
    if disk_fit and disk_fit.rate_per_month_pct > 4.0:
        warnings.append("磁盘衰减偏快（-%.1f%%/月），注意 SSD 磨损" % disk_fit.rate_per_month_pct)

    return Prediction(
        days_left=days_left,
        death_date=death_date,
        health=round(health * 100, 1),
        confidence=confidence,
        samples=n,
        decay=fits,
        oversell=oversell,
        mining_risk=mining,
        warnings=warnings,
    )


def health_badge(health: float) -> tuple[str, str]:
    """健康度 → (颜色类名, 图标)"""
    if health >= 81:
        return "g", "⚡"
    if health >= 61:
        return "b", "✅"
    if health >= 41:
        return "y", "⚠️"
    if health >= 21:
        return "o", "🔥"
    return "r", "💀"
