import hashlib
import json
import os
import random
import urllib.request

BASE = os.environ.get("HOSPICE_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
KEY = os.environ.get("HOSPICE_LLM_KEY", "").strip()
MODEL = os.environ.get("HOSPICE_LLM_MODEL", "gpt-4o-mini").strip()

PROMPT = """You are a VPS server. Write a short monologue in first person.

Real data (do not invent, only use these):
- Plan: {plan}
- Location: {location}
- Uptime: {uptime} days
- Health: {health}/100
- Estimated days left: {days}
- CPU score: {cpu} (neighbor stealing {steal}% of the time)
- Disk read: {disk} MB/s
- Running: {services}
- Oversell index: {oversell}/1.0
- Suspected neighbor mining: {mining}

Rules:
1. 50-80 characters if Chinese, 25-45 words if English
2. First person, like a machine talking to itself
3. Must include at least 2 of the specific numbers above
4. A little weary or dry humor, but no melodrama
5. Never start with "As a server" or "I am a VPS"
6. Never mention AI, model, or generation
7. No exclamation marks
8. Reply in {lang}

Output only the monologue text, no quotes, no title."""

TEMPLATES = {
    "zh": {
        "dying": [
            "硬盘响了一整天。我知道它快撑不住了，但我还在跑着{services}。还剩{days}天。",
            "今天第{steal}次被抢走CPU。健康度只剩{health}，没人来修。",
            "{uptime}天的运行，{disk}MB/s的磁盘。我数着自己的心跳。",
        ],
        "tired": [
            "邻居又在编译内核。CPU跑到{cpu}，其中{steal}%不是我的。",
            "还剩{days}天。我还能跑{services}，但越来越慢了。",
            "{location}的网络今天抖动了几次。我假装没注意。",
        ],
        "abused": [
            "今天被抢走了{steal}%的CPU时间。我的数字还在，但不是我自己的。",
            "{cpu}分的机器，跑起来像800分。没人看得出来。",
            "超售指数{oversell}。我算了一下，我还剩{days}天。",
        ],
        "peaceful": [
            "今天很安静。{services}有27个人来过，我记住了每一个。",
            "健康度{health}，还剩{days}天。我暂时不需要担心什么。",
            "硬盘转了{uptime}天，还没坏。这已经算幸运。",
        ],
        "neutral": [
            "今天处理了一些请求。磁盘读到{disk}MB/s，还不错。",
            "运行{uptime}天，健康度{health}。我开始偶尔卡顿。",
            "还剩{days}天。够我把{services}跑完。",
        ],
    },
    "en": {
        "dying": [
            "The disk has been clicking all day. I know it is failing, but {services} is still running. {days} days left.",
            "CPU stolen {steal} times today. Health down to {health}. Nobody came to fix it.",
            "{uptime} days uptime, {disk} MB/s disk. I count my own heartbeat.",
        ],
        "tired": [
            "The neighbor is compiling a kernel again. CPU at {cpu}, {steal}% of it not mine.",
            "{days} days left. I can still serve {services}, but slower each week.",
            "The network in {location} stuttered a few times today. I pretended not to notice.",
        ],
        "abused": [
            "{steal}% of my CPU time was taken today. The numbers are mine but not the cycles.",
            "A {cpu}-point machine that feels like 800. Nobody can tell.",
            "Oversell index {oversell}. I did the math. {days} days left.",
        ],
        "peaceful": [
            "Quiet day. 27 people visited {services}. I remembered each one.",
            "Health {health}, {days} days left. Nothing to worry about for now.",
            "The disk has spun for {uptime} days without dying. That counts as lucky.",
        ],
        "neutral": [
            "Handled some requests today. Disk read at {disk} MB/s. Not bad.",
            "{uptime} days running, health {health}. I stutter occasionally now.",
            "{days} days left. Enough to finish {services}.",
        ],
    },
}


def _hash(p):
    raw = "%s|%s|%s|%s|%s" % (p.get("health"), p.get("days_left"),
                              p.get("oversell"), p.get("mining_risk"),
                              int(p.get("cpu") or 0) // 100)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _mood(p):
    h = float(p.get("health") or 0)
    if h < 20:
        return "dying"
    if h < 40:
        return "tired"
    if float(p.get("oversell") or 0) > 0.6:
        return "abused"
    if h > 85:
        return "peaceful"
    return "neutral"


def _call_llm(prompt):
    if not KEY:
        return None
    try:
        body = json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.9,
            "max_tokens": 220,
        }).encode()
        req = urllib.request.Request(
            BASE + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + KEY},
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
        text = (data["choices"][0]["message"]["content"] or "").strip()
        text = text.strip('"\'“”「」')
        return text[:160] if text else None
    except Exception:
        return None


def _from_template(p, lang):
    table = TEMPLATES.get(lang) or TEMPLATES["zh"]
    pool = table.get(_mood(p)) or table["neutral"]
    return random.choice(pool).format(**p)


def generate(node, samples, pred, lang="zh"):
    latest = samples[-1] if samples else {}
    p = {
        "plan": node.get("plan") or "1C1G",
        "location": node.get("location") or "-",
        "health": pred.get("health", 100),
        "days": pred.get("days_left", 0),
        "days_left": pred.get("days_left", 0),
        "oversell": pred.get("oversell", 0),
        "mining_risk": pred.get("mining_risk", 0),
        "cpu": latest.get("cpu_single") or 0,
        "disk": int(latest.get("disk_read") or 0),
        "uptime": latest.get("uptime_days") or 0,
        "services": "一个博客" if lang == "zh" else "a blog",
    }
    p["steal"] = max(1, int(float(p["oversell"]) * 100 / 3))
    p["health"] = int(float(p["health"]))
    p["days"] = int(p["days"])

    dh = _hash(p)
    mood = _mood(p)

    text = _call_llm(PROMPT.format(
        plan=p["plan"], location=p["location"], uptime=p["uptime"],
        health=p["health"], days=p["days"], cpu=int(p["cpu"]),
        steal=p["steal"], disk=p["disk"], services=p["services"],
        oversell=p["oversell"],
        mining="yes" if float(p["mining_risk"]) > 0.5 else "no",
        lang="Chinese" if lang == "zh" else "English",
    ))
    if not text:
        text = _from_template(p, lang)

    return text, mood, dh


def available():
    return bool(KEY)
