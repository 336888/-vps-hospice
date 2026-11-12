import os
import time

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import badge, db, i18n, monologue, prober, survival

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET = os.environ.get("HOSPICE_SECRET", "")

app = FastAPI(title="VPS Hospice")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "templates")), name="static")

db.init()


def _ctx(req, extra=None):
    lang = i18n.detect(req)
    ctx = {"request": req, "t": i18n.translator(lang), "lang": lang}
    return {**ctx, **(extra or {})}


def _date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "-"


def _predict(nid):
    p = survival.predict(db.samples(nid)).as_dict()
    db.save_pred(nid, p)
    return p


def _spark(rows, w=100, h=40):
    vals = [float(r.get("cpu_single") or 0) for r in rows if r.get("cpu_single")]
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals) or 1
    span = (hi - lo) or 1
    pts = [(i / (len(vals) - 1) * w, h - (v - lo) / span * (h - 4) - 2)
           for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return {"line": line, "area": f"0,{h} {line} {w},{h}"}


def _with_tier(rows):
    for n in rows:
        n["tier"] = survival.tier(float(n.get("hp") or 0), int(n.get("days_left") or 0),
                                  float(n.get("oversell") or 0))
    return rows


@app.get("/", response_class=HTMLResponse)
def home(req: Request):
    all_nodes = db.nodes()
    alive = [n for n in all_nodes if not n.get("died_at")]
    alive.sort(key=lambda n: n.get("days_left") or 0, reverse=True)
    cert = sorted(_with_tier(db.certified()),
                  key=lambda n: survival.TIER_RANK.get(n["tier"], 0), reverse=True)
    return tpl.TemplateResponse(req, "index.html", _ctx(req, {
        "alive": alive,
        "certified": cert,
        "stats": db.stats(),
        "monos": db.monologues(6),
    }))


@app.get("/node/{nid}", response_class=HTMLResponse)
def node_page(req: Request, nid: int):
    n = db.node(nid)
    if not n:
        raise HTTPException(404)

    rows = db.samples(nid)
    pred = survival.predict(rows).as_dict()
    lang = i18n.detect(req)

    mono = None
    today = int(time.time()) // 86400 * 86400
    recent = db.node_monologues(nid, 1)
    if recent and recent[0]["ts"] >= today:
        mono = recent[0]
    elif not n.get("died_at"):
        text, mood, h = monologue.generate(n, rows, pred, lang)
        db.add_monologue(nid, text, mood, h)
        mono = {"text": text, "ts": int(time.time())}

    return tpl.TemplateResponse(req, "node.html", _ctx(req, {
        "n": n, "pred": pred, "mono": mono,
        "history": db.node_monologues(nid, 10),
        "spark": _spark(rows),
        "born": _date(n.get("started_at")),
        "alive_days": max(0, int((time.time() - (n.get("started_at") or 0)) // 86400)),
    }))


@app.get("/graveyard", response_class=HTMLResponse)
def graveyard(req: Request):
    rows = db.dead()
    for d in rows:
        d["born_fmt"] = _date(d.get("born") or d.get("started_at"))
        d["died_fmt"] = _date(d.get("died_at"))
        d["lived_days"] = ((d.get("died_at") or 0) - (d.get("born") or 0)) // 86400 if d.get("born") else 0
    return tpl.TemplateResponse(req, "graveyard.html", _ctx(req, {"dead": rows}))


@app.get("/certify", response_class=HTMLResponse)
def certify(req: Request):
    return tpl.TemplateResponse(req, "certify.html", _ctx(req, {"rows": _with_tier(db.certified())}))


@app.get("/compare", response_class=HTMLResponse)
def compare(req: Request):
    ids = [int(x) for x in (req.query_params.get("ids") or "").split(",") if x.isdigit()][:4]
    rows = [n for n in (_with_tier([db.node(i)])[0] if db.node(i) else None for i in ids) if n]
    return tpl.TemplateResponse(req, "compare.html", _ctx(req, {"rows": rows}))


@app.get("/lang/{code}")
def switch(code: str, req: Request):
    r = RedirectResponse(req.headers.get("referer") or "/", status_code=303)
    r.set_cookie("lang", code if code in i18n.SUPPORTED else i18n.DEFAULT,
                 max_age=31536000, samesite="lax")
    return r


@app.get("/badge/{nid}.svg")
def badge_svg(nid: int):
    n = db.node(nid)
    if not n:
        raise HTTPException(404)
    t = survival.tier(float(n.get("hp") or 0), int(n.get("days_left") or 0),
                      float(n.get("oversell") or 0)) if n.get("certified") else None
    return Response(badge.small(t, n.get("hp") or 0), media_type="image/svg+xml",
                    headers={"Cache-Control": "public,max-age=300"})


@app.get("/card/{nid}.svg")
def card_svg(nid: int):
    n = db.node(nid)
    if not n:
        raise HTTPException(404)
    t = survival.tier(float(n.get("hp") or 0), int(n.get("days_left") or 0),
                      float(n.get("oversell") or 0)) if n.get("certified") else None
    return Response(badge.card(n["provider"], n["location"], t, n.get("hp") or 0,
                               n.get("days_left") or 0, n.get("death_date") or "-"),
                    media_type="image/svg+xml", headers={"Cache-Control": "public,max-age=300"})


@app.get("/go/{nid}")
def go(nid: int):
    n = db.node(nid)
    if not n or not n.get("affiliate"):
        raise HTTPException(404)
    db.click(nid)
    return RedirectResponse(n["affiliate"], status_code=302)


@app.get("/api/state")
def api_state():
    return JSONResponse({"stats": db.stats(), "nodes": db.nodes()})


@app.get("/api/node/{nid}")
def api_node(nid: int):
    n = db.node(nid)
    if not n:
        raise HTTPException(404)
    rows = db.samples(nid)
    return JSONResponse({"node": n, "prediction": survival.predict(rows).as_dict(),
                         "samples": len(rows),
                         "monologues": db.node_monologues(nid, 5)})


@app.post("/api/node/{nid}/sample")
async def api_sample(nid: int, req: Request):
    if SECRET and req.headers.get("X-Secret") != SECRET:
        raise HTTPException(403)
    if not db.node(nid):
        raise HTTPException(404)
    db.add_sample(nid, await req.json())
    return {"ok": True, "prediction": _predict(nid)}


@app.post("/api/node/{nid}/monologue")
def api_monologue(nid: int, req: Request):
    n = db.node(nid)
    if not n:
        raise HTTPException(404)
    rows = db.samples(nid)
    text, mood, h = monologue.generate(n, rows, survival.predict(rows).as_dict(), i18n.detect(req))
    db.add_monologue(nid, text, mood, h)
    return {"ok": True, "text": text, "mood": mood}


@app.post("/api/probe/{nid}")
def api_probe(nid: int):
    n = db.node(nid)
    if not n:
        raise HTTPException(404)
    if not n.get("ssh_host"):
        raise HTTPException(400, "no ssh config")
    data = prober.probe(n)
    db.add_sample(nid, data)
    return {"ok": True, "data": data, "prediction": _predict(nid)}


@app.get("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
