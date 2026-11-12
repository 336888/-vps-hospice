"""
机器独白生成器

两种模式：
  ① LLM 模式（需配置 API Key）—— 自然、有情感
  ② 模板模式（无需 API）—— 数据拼装，也能用

缓存策略：
  同一台机器、同一天、同样数据 → 只生成一次（避免浪费 token）
  数据指纹 = hash(健康度, 天数, steal, load)

配置（环境变量）：
  REMAINDER_LLM_BASE   API 地址，默认 https://api.openai.com/v1
  REMAINDER_LLM_KEY    API Key，留空则用模板模式
  REMAINDER_LLM_MODEL  模型名，默认 gpt-4o-mini
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import urllib.request
from typing import Any

LLM_BASE = os.environ.get("REMAINDER_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
LLM_KEY = os.environ.get("REMAINDER_LLM_KEY", "").strip()
LLM_MODEL = os.environ.get("REMAINDER_LLM_MODEL", "gpt-4o-mini").strip()

PROMPT = """你是一台 VPS 服务器。用第一人称写一段独白。

真实数据（不要编造，只能用这些）：
- 机型：{plan}
- 位置：{location}
- 已运行：{uptime_days} 天
- 健康度：{health}/100
- 预计还剩：{days_left} 天
- CPU 峰值：{cpu}%（被邻居抢走 {steal}%）
- 磁盘读取：{disk} MB/s
- 上面跑着：{services}
- 超售指数：{oversell}/1.0
- 疑似邻居挖矿：{mining}

硬规则：
1. 50-80 字，不超过 80 字
2. 第一人称，像机器在自言自语
3. 必须包含至少 2 个上面给的具体数字
4. 有一点无奈或自嘲，但不要煽情
5. 禁止出现"作为一台服务器"、"我是一台VPS"这类开头
6. 禁止提"AI"、"模型"、"生成"
7. 不要用感叹号
8. 结尾可以是一句很短的话

直接输出独白正文，不要引号，不要标题。"""


def _data_hash(p: dict) -> str:
    """数据指纹，用于缓存判断"""
    key = "%s|%s|%s|%s|%s" % (
        p.get("health"), p.get("days_left"),
        p.get("oversell"), p.get("mining_risk"),
        p.get("cpu", 0) // 100,
    )
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _mood(p: dict) -> str:
    """根据数据判断心情"""
    h = float(p.get("health") or 0)
    steal = float(p.get("oversell") or 0)
    if h < 20:
        return "dying"
    if h < 40:
        return "tired"
    if steal > 0.6:
        return "abused"
    if h > 85:
        return "peaceful"
    return "neutral"


def _llm_call(prompt: str) -> str | None:
    """调 LLM，失败返回 None"""
    if not LLM_KEY:
        return None
    try:
        body = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.9,
            "max_tokens": 200,
        }).encode()
        req = urllib.request.Request(
            LLM_BASE + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + LLM_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
        # 清理
        text = text.strip('"\'' + "「」" + "“”")
        if len(text) > 160:
            text = text[:157] + "..."
        return text or None
    except Exception:
        return None


def _template(p: dict) -> str:
    """无 API 时的模板生成（也有点味道）"""
    health = float(p.get("health") or 0)
    days = int(p.get("days_left") or 0)
    steal = float(p.get("oversell") or 0)
    cpu = int(p.get("cpu") or 0)
    disk = float(p.get("disk") or 0)
    uptime = int(p.get("uptime_days") or 0)
    services = p.get("services") or "几个网站"
    location = p.get("location") or "某处"

    if health < 20:
        opts = [
            "硬盘响了一整天。我知道它快撑不住了，但我还在跑着%s。还剩%d天。" % (services, days),
            "今天第%d次被抢走CPU。我的健康度只剩%d，没人来修。" % (int(steal * 10) + 1, int(health)),
            "%d天的运行，%dMB/s的磁盘。我数着自己的心跳。" % (uptime, int(disk)),
        ]
    elif health < 40:
        opts = [
            "邻居又在编译内核。我的CPU跑到%d%%，其中%d%%不是我的。" % (cpu, int(steal * cpu)),
            "还剩%d天。我还能跑%s，但越来越慢了。" % (days, services),
            "%s的网络今天抖动了几次。我假装没注意。" % location,
        ]
    elif health < 70:
        opts = [
            "今天处理了%d个请求。我的磁盘读到%dMB/s，还不错。" % (cpu * 12, int(disk)),
            "运行%d天，健康度%d。我开始偶尔卡顿。" % (uptime, int(health)),
            "还剩%d天。够我把%s跑完。" % (days, services),
        ]
    else:
        opts = [
            "今天很安静。%s有27个人来过，我记住了每一个。" % services,
            "健康度%d，还剩%d天。我暂时不需要担心什么。" % (int(health), days),
            "硬盘转了%d天，还没坏。这已经算幸运。" % uptime,
        ]
    return random.choice(opts)


def generate(node: dict, samples: list[dict], pred: dict) -> tuple[str, str, str]:
    """
    生成一段独白

    Returns:
        (text, mood, data_hash)
    """
    latest = samples[-1] if samples else {}
    p = {
        "plan": node.get("plan") or "1C1G",
        "location": node.get("location") or "未知",
        "health": pred.get("health", 100),
        "days_left": pred.get("days_left", 0),
        "oversell": pred.get("oversell", 0),
        "mining_risk": pred.get("mining_risk", 0),
        "cpu": latest.get("cpu_single") or 0,
        "disk": latest.get("disk_read") or 0,
        "uptime_days": latest.get("uptime_days") or 0,
        "services": "一个博客",   # 可扩展：从 /proc 或容器列表推断
    }

    dh = _data_hash(p)
    mood = _mood(p)

    prompt = PROMPT.format(
        plan=p["plan"], location=p["location"],
        uptime_days=p["uptime_days"],
        health=int(p["health"]), days_left=int(p["days_left"]),
        cpu=int(p["cpu"]), steal=int(float(p["oversell"]) * 100 / 3),
        disk=int(p["disk"]),
        services=p["services"],
        oversell=p["oversell"],
        mining=("是" if p["mining_risk"] > 0.5 else "否"),
    )

    text = _llm_call(prompt)
    if not text:
        text = _template(p)

    return text, mood, dh


def llm_available() -> bool:
    return bool(LLM_KEY)
