"""
The Remainder —— 100 台 VPS 的余生

启动：
    python3 -m app.main
    或 uvicorn app.main:app --host 0.0.0.0 --port 8000

环境变量：
    REMAINDER_DB          SQLite 路径
    REMAINDER_LLM_KEY     LLM API Key（可选，没有就用模板）
    REMAINDER_LLM_BASE    API 地址（默认 OpenAI）
    REMAINDER_LLM_MODEL   模型名
    REMAINDER_SECRET      管理接口密钥（/api/probe 需要）
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, monologue, survival

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET = os.environ.get("REMAINDER_SECRET", "")

app = FastAPI(title="The Remainder", version="1.0.0")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# ==================== 辅助 ====================

def _predict_for(node_id: int) -> dict:
    """对单台机器做预测（含更新缓存）"""
    samples = db.node_samples(node_id)
    pred = survival.predict(samples)
    d = pred.to_dict()
    db.update_prediction(node_id, d)
    return d


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _days_ago(ts: int | None) -> int:
    if not ts:
        return 0
    return max(0, (time.time() - ts) // 86400)


# ==================== 页面 ====================

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    nodes = db.all_nodes()
    alive = [n for n in nodes if not n.get("died_at")]
    dead = [n for n in nodes if n.get("died_at")]
    st = db.stats()

    # 给每台机器补上颜色和图标
    for n in alive:
        cls, ico = survival.health_badge(float(n.get("hp") or 0))
        n["cls"] = cls
        n["ico"] = ico
        n["pct"] = min(100, max(0, float(n.get("days_left") or 0) / 3650 * 100))

    # 按剩余天数排序
    alive.sort(key=lambda x: x.get("days_left") or 0, reverse=True)

    monos = db.latest_monologues(6)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "alive": alive,
        "dead_count": len(dead),
        "stats": st,
        "monos": monos,
        "llm": monologue.llm_available(),
    })


@app.get("/node/{node_id}", response_class=HTMLResponse)
def node_page(request: Request, node_id: int):
    n = db.get_node(node_id)
    if not n:
        raise HTTPException(404, "机器不存在")

    samples = db.node_samples(node_id)
    pred = survival.predict(samples)
    d = pred.to_dict()

    # 生成/读取今天的独白
    mono = None
    if not n.get("died_at"):
        recent = db.node_monologues(node_id, 1)
        today0 = int(time.time()) // 86400 * 86400
        if recent and recent[0]["ts"] >= today0:
            mono = recent[0]
        else:
            text, mood, dh = monologue.generate(n, samples, d)
            db.add_monologue(node_id, text, mood, dh)
            mono = {"text": text, "mood": mood, "ts": int(time.time())}

    history = db.node_monologues(node_id, 10)

    # 曲线数据
    curve = []
    if samples and pred.decay:
        cpu_fit = next((f for f in pred.decay if f.key == "cpu"), None)
        if cpu_fit and cpu_fit.initial > 0:
            t0 = samples[0]["ts"]
            for s in samples:
                curve.append({
                    "t": _fmt_ts(s["ts"]),
                    "v": s.get("cpu_single") or 0,
                })
            # 补上预测部分
            k = cpu_fit.rate_per_day
            for extra_d in [30, 90, 180, 365, 730]:
                future = samples[-1]["ts"] + extra_d * 86400
                v = cpu_fit.current * (2.718281828 ** (-k * extra_d))
                curve.append({"t": _fmt_ts(int(future)), "v": round(v), "future": True})

    cls, ico = survival.health_badge(d["health"])

    return templates.TemplateResponse("node.html", {
        "request": request,
        "n": n,
        "pred": d,
        "cls": cls,
        "ico": ico,
        "mono": mono,
        "history": history,
        "curve": curve,
        "samples_count": len(samples),
        "born": _fmt_ts(n.get("started_at")),
        "alive_days": _days_ago(n.get("started_at")),
        "llm": monologue.llm_available(),
    })


@app.get("/graveyard", response_class=HTMLResponse)
def graveyard_page(request: Request):
    dead = db.graveyard()
    for d in dead:
        d["born_fmt"] = _fmt_ts(d.get("born") or d.get("started_at"))
        d["died_fmt"] = _fmt_ts(d.get("died_at"))
        if d.get("born") and d.get("died_at"):
            d["lived_days"] = (d["died_at"] - d["born"]) // 86400
        else:
            d["lived_days"] = 0
    return templates.TemplateResponse("graveyard.html", {
        "request": request,
        "dead": dead,
    })


# ==================== API ====================

@app.get("/api/state")
def api_state():
    nodes = db.all_nodes()
    return JSONResponse({
        "stats": db.stats(),
        "nodes": [
            {
                "id": n["id"],
                "provider": n["provider"],
                "location": n["location"],
                "plan": n["plan"],
                "hp": n.get("hp"),
                "days_left": n.get("days_left"),
                "death_date": n.get("death_date"),
                "oversell": n.get("oversell"),
                "alive": not n.get("died_at"),
            }
            for n in nodes
        ],
    })


@app.get("/api/node/{node_id}")
def api_node(node_id: int):
    n = db.get_node(node_id)
    if not n:
        raise HTTPException(404, "not found")
    samples = db.node_samples(node_id)
    pred = survival.predict(samples).to_dict()
    mono = db.node_monologues(node_id, 5)
    return JSONResponse({
        "node": n,
        "prediction": pred,
        "samples": len(samples),
        "monologues": [{"ts": m["ts"], "text": m["text"], "mood": m["mood"]} for m in mono],
    })


@app.post("/api/node/{node_id}/sample")
async def api_add_sample(node_id: int, request: Request):
    """写入一次探测结果（供 prober 调用）"""
    if SECRET:
        if request.headers.get("X-Secret") != SECRET:
            raise HTTPException(403, "bad secret")
    if not db.get_node(node_id):
        raise HTTPException(404, "node not found")

    data = await request.json()
    db.add_sample(node_id, data)
    pred = _predict_for(node_id)
    return {"ok": True, "prediction": pred}


@app.post("/api/node/{node_id}/monologue")
async def api_gen_monologue(node_id: int):
    """手动触发独白生成"""
    n = db.get_node(node_id)
    if not n:
        raise HTTPException(404, "node not found")
    samples = db.node_samples(node_id)
    pred = survival.predict(samples).to_dict()
    text, mood, dh = monologue.generate(n, samples, pred)
    db.add_monologue(node_id, text, mood, dh)
    return {"ok": True, "text": text, "mood": mood}


@app.post("/api/probe/{node_id}")
def api_probe(node_id: int):
    """
    触发一次真实探测（SSH 到目标机跑 YABS）
    注意：需要目标机 SSH 可达 + 有 ssh key
    """
    from . import prober
    n = db.get_node(node_id)
    if not n:
        raise HTTPException(404, "node not found")
    if not n.get("ssh_host"):
        raise HTTPException(400, "这台机器没有配置 SSH")

    try:
        data = prober.probe(n)
    except Exception as e:
        raise HTTPException(500, "探测失败: %s" % e)

    db.add_sample(node_id, data)
    pred = _predict_for(node_id)
    return {"ok": True, "data": data, "prediction": pred}


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}


# ==================== 启动 ====================

def main() -> None:
    import uvicorn
    db.init_db()
    st = db.stats()
    if st["total"] == 0:
        print("⚠️  数据库是空的，先跑：python3 -m data.seed")
    print("→ http://127.0.0.1:8000")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
