import hashlib
import json
import os
import random
import urllib.request

BASE = os.environ.get("HOSPICE_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
KEY = os.environ.get("HOSPICE_LLM_KEY", "").strip()
MODEL = os.environ.get("HOSPICE_LLM_MODEL", "gpt-4o-mini")

PROMPT = """You are a VPS server. Write a short monologue in first person.

Data (use only these):
plan {plan} · {location} · uptime {uptime}d · health {health}/100 · {days}d left
CPU {cpu} (neighbor steals {steal}%) · disk {disk}MB/s · runs {services}
oversell {oversell} · suspected mining: {mining}

Rules:
- 50-80 Chinese chars, or 25-45 English words
- first person, machine talking to itself
- include at least 2 numbers from above
- weary, dry humor, no melodrama
- never start with "As a server" or "I am a VPS"
- never mention AI or generation
- no exclamation marks
- reply in {lang}

Output the monologue only."""

T = {
    "zh": {
        "dying": ["硬盘响了一整天。我知道它快撑不住了，但{services}还在跑。还剩{days}天。",
                  "今天第{steal}次被抢走CPU。健康度{health}，没人来修。",
                  "{uptime}天运行，{disk}MB/s磁盘。我数着自己的心跳。"],
        "tired": ["邻居又在编译内核。CPU跑到{cpu}，其中{steal}%不是我的。",
                  "还剩{days}天。我还能跑{services}，但越来越慢。",
                  "{location}的网络今天抖了几次。我假装没注意。"],
        "abused": ["今天被抢走{steal}%的CPU。数字是我的，周期不是。",
                   "{cpu}分的机器，跑起来像800。没人看得出来。",
                   "超售指数{oversell}。我算了一下，还剩{days}天。"],
        "peaceful": ["今天很安静。{services}有27个人来过，我记住了每一个。",
                     "健康度{health}，还剩{days}天。暂时不用担什么。",
                     "硬盘转了{uptime}天，还没坏。这已经算运气。"],
        "neutral": ["今天处理了一些请求。磁盘{disk}MB/s，还行。",
                    "运行{uptime}天，健康度{health}。我开始偶尔卡顿。",
                    "还剩{days}天。够把{services}跑完。"],
    },
    "en": {
        "dying": ["The disk has been clicking all day. I know it's failing, but {services} is still up. {days} days left.",
                  "CPU stolen {steal} times today. Health down to {health}. Nobody came.",
                  "{uptime} days running, {disk} MB/s disk. I count my own heartbeat."],
        "tired": ["Neighbor is compiling a kernel again. CPU at {cpu}, {steal}% not mine.",
                  "{days} days left. I can still serve {services}, slower each week.",
                  "The network in {location} stuttered today. I pretended not to notice."],
        "abused": ["{steal}% of my CPU went to someone else. The numbers are mine, the cycles aren't.",
                   "A {cpu}-point machine that feels like 800. Nobody can tell.",
                   "Oversell {oversell}. I did the math. {days} days left."],
        "peaceful": ["Quiet day. 27 people visited {services}. I remembered each one.",
                     "Health {health}, {days} days left. Nothing to worry about yet.",
                     "The disk has spun {uptime} days without dying. That counts as luck."],
        "neutral": ["Handled some requests. Disk at {disk} MB/s. Not bad.",
                    "{uptime} days running, health {health}. I stutter occasionally now.",
                    "{days} days left. Enough to finish {services}."],
    },
}


def _mood(p):
    h = float(p.get("health") or 0)
    if h < 20:
        return "dying"
    if h < 40:
        return "tired"
    if float(p.get("oversell") or 0) > .6:
        return "abused"
    return "peaceful" if h > 85 else "neutral"


def _llm(prompt):
    if not KEY:
        return None
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps({"model": MODEL, "temperature": .9, "max_tokens": 220,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        out = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    return out.strip('"\'“”「」')[:160] or None


def generate(n, rows, pred, lang="en"):
    last = rows[-1] if rows else {}
    p = {
        "plan": n.get("plan") or "1C1G",
        "location": n.get("location") or "-",
        "health": int(float(pred.get("health") or 0)),
        "days": int(pred.get("days_left") or 0),
        "oversell": pred.get("oversell") or 0,
        "cpu": int(last.get("cpu_single") or 0),
        "disk": int(last.get("disk_read") or 0),
        "uptime": int(last.get("uptime_days") or 0),
        "services": "一个博客" if lang == "zh" else "a blog",
    }
    p["steal"] = max(1, int(float(p["oversell"]) * 100 / 3))

    mood = _mood(p)
    h = hashlib.md5(f"{p['health']}|{p['days']}|{p['oversell']}|{p['cpu']//100}".encode()).hexdigest()[:12]

    try:
        text = _llm(PROMPT.format(**p, mining="yes" if float(pred.get("mining_risk") or 0) > .5 else "no",
                                 lang="Chinese" if lang == "zh" else "English"))
    except Exception:
        text = None

    return text or random.choice(T[lang][mood]).format(**p), mood, h


def available():
    return bool(KEY)
