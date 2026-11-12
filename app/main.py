import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, i18n, monologue, survival

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET = os.environ.get("HOSPICE_SECRET", "")

app = FastAPI(title="VPS Hospice", version="1.1.0")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


@app.on_event("startup")
def _boot():
    db.init()


def _ctx(request: Request, extra: Optional[dict] = None) -> dict:
    lang = i18n.detect(request)
    ctx = {
        "request": request,
        "t": i18n.translator(lang),
        "lang": lang,
        "locales": i18n.all_locales(),
    }
    if extra:
        ctx.update(extra)
    return ctx


def _fmt_date(ts):
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _days_since(ts):
    if not ts:
        return 0
    return max(0, int((time.time() - ts) // 86400))


def _refresh(node_id: int) -> dict:
    s = db.samples(node_id)
    p = survival.predict(s).as_dict()
    db.save_prediction(node_id, p)
    return p


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    all_nodes = db.nodes()
    alive = [n for n in all_nodes if not n.get("died_at")]
    dead = [n for n in all_nodes if n.get("died_at")]

    for n in alive:
        cls, icon = survival.badge(float(n.get("hp") or 0))
        n["cls"] = cls
        n["icon"] = icon
        n["pct"] = min(100, max(0, float(n.get("days_left") or 0) / 3650 * 100))
    alive.sort(key=lambda x: x.get("days_left") or 0, reverse=True)

    return templates.TemplateResponse(request, "index.html", _ctx(request, {
        "alive": alive,
        "dead_count": len(dead),
        "stats": db.stats(),
        "monos": db.recent_monologues(6),
        "llm": monologue.available(),
    }))


@app.get("/node/{node_id}", response_class=HTMLResponse)
def node_page(request: Request, node_id: int):
    n = db.node(node_id)
    if not n:
        raise HTTPException(404, "not found")

    lang = i18n.detect(request)
    s = db.samples(node_id)
    pred = survival.predict(s).as_dict()

    mono = None
    if not n.get("died_at"):
        today0 = int(time.time()) // 86400 * 86400
        recent = db.node_monologues(node_id, 1)
        if recent and recent[0]["ts"] >= today0:
            mono = recent[0]
        else:
            text, mood, dh = monologue.generate(n, s, pred, lang)
            db.add_monologue(node_id, text, mood, dh)
            mono = {"text": text, "mood": mood, "ts": int(time.time())}

    cls, icon = survival.badge(pred["health"])

    return templates.TemplateResponse(request, "node.html", _ctx(request, {
        "n": n,
        "pred": pred,
        "cls": cls,
        "icon": icon,
        "mono": mono,
        "history": db.node_monologues(node_id, 10),
        "samples_count": len(s),
        "born": _fmt_date(n.get("started_at")),
        "alive_days": _days_since(n.get("started_at")),
        "llm": monologue.available(),
    }))


@app.get("/graveyard", response_class=HTMLResponse)
def graveyard_page(request: Request):
    dead = db.graveyard()
    for d in dead:
        d["born_fmt"] = _fmt_date(d.get("born") or d.get("started_at"))
        d["died_fmt"] = _fmt_date(d.get("died_at"))
        if d.get("born") and d.get("died_at"):
            d["lived_days"] = (d["died_at"] - d["born"]) // 86400
        else:
            d["lived_days"] = 0
    return templates.TemplateResponse(request, "graveyard.html", _ctx(request, {"dead": dead}))


@app.get("/lang/{code}")
def switch_lang(code: str, request: Request):
    back = request.headers.get("referer") or "/"
    if code not in i18n.all_locales():
        code = i18n.DEFAULT
    r = RedirectResponse(back, status_code=303)
    r.set_cookie("lang", code, max_age=86400 * 365, samesite="lax")
    return r


@app.get("/api/state")
def api_state():
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
            for n in db.nodes()
        ],
    })


@app.get("/api/node/{node_id}")
def api_node(node_id: int):
    n = db.node(node_id)
    if not n:
        raise HTTPException(404, "not found")
    s = db.samples(node_id)
    return JSONResponse({
        "node": n,
        "prediction": survival.predict(s).as_dict(),
        "samples": len(s),
        "monologues": [
            {"ts": m["ts"], "text": m["text"], "mood": m["mood"]}
            for m in db.node_monologues(node_id, 5)
        ],
    })


@app.post("/api/node/{node_id}/sample")
async def api_sample(node_id: int, request: Request):
    if SECRET and request.headers.get("X-Secret") != SECRET:
        raise HTTPException(403, "bad secret")
    if not db.node(node_id):
        raise HTTPException(404, "node not found")
    data = await request.json()
    db.add_sample(node_id, data)
    return {"ok": True, "prediction": _refresh(node_id)}


@app.post("/api/node/{node_id}/monologue")
def api_monologue(node_id: int, request: Request):
    n = db.node(node_id)
    if not n:
        raise HTTPException(404, "node not found")
    s = db.samples(node_id)
    pred = survival.predict(s).as_dict()
    text, mood, dh = monologue.generate(n, s, pred, i18n.detect(request))
    db.add_monologue(node_id, text, mood, dh)
    return {"ok": True, "text": text, "mood": mood}


@app.post("/api/probe/{node_id}")
def api_probe(node_id: int):
    from . import prober
    n = db.node(node_id)
    if not n:
        raise HTTPException(404, "node not found")
    if not n.get("ssh_host"):
        raise HTTPException(400, "no ssh config")
    try:
        data = prober.probe(n)
    except Exception as e:
        raise HTTPException(500, "probe failed: %s" % e)
    db.add_sample(node_id, data)
    return {"ok": True, "data": data, "prediction": _refresh(node_id)}


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}


def main():
    import uvicorn
    db.init()
    if db.stats()["total"] == 0:
        print("empty db, run: python3 -m data.seed")
    print("http://127.0.0.1:%s" % os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
