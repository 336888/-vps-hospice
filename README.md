# vps-hospice

Every VPS has a remainder. This measures it.

Benchmark sites tell you how fast a box is today. They don't tell you how many good months are left in it. That is the gap this fills: fit a decay curve to each metric, project it against a 30% usefulness floor, print the date.

Live at [bie.me](https://bie.me).

---

## What it actually computes

Each of CPU single-core, disk read and 4K IOPS gets an exponential fit over its own sample history:

```
y = y0 * exp(-k * t)
```

The three rates are combined 40/35/25. Days remaining falls out of solving `health * exp(-k * t) = 0.30`, capped at ten years because past that the fit is guesswork.

Two derived numbers sit alongside it:

- **oversell** — mean and standard deviation of `%st`, squashed to 0..1. High mean means the host is packed. High deviation means the neighbours are bursty. Both are bad.
- **mining** — CPU score dropped more than 20% while `loadavg` rose above 0.3. That pattern is almost always someone else's workload on your core.

Confidence is `high` at 24+ samples with R² above 0.5 on all three fits, `medium` from 8, `low` below.

## Running it

```
pip install -r requirements.txt
python3 -m data.seed          # 12 nodes, 24 months of synthetic history
python3 -m app.main
```

Two of the seeded nodes are already dead. They show up in `/graveyard` with epitaphs.

## Attaching a real server

```python
from app import db

db.add_node(
    provider="RackNerd",
    location="Los Angeles",
    plan="1C1G/20G",
    price=8.0,
    ssh_host="192.0.2.1",
    ssh_key="/root/.ssh/id_ed25519",
)
```

Then either hit `POST /api/probe/{id}` once, or cron the batch runner:

```
0 3 * * * cd /opt/vps-hospice && .venv/bin/python -m scripts.probe_all
```

The prober runs `uptime`, `top`, `geekbench6` (falling back to `sysbench`), `fio` (falling back to `dd`) and three public pings. Nothing is installed on the target.

If you already collect metrics elsewhere, skip SSH entirely:

```
curl -X POST https://bie.me/api/node/41/sample \
  -H 'Content-Type: application/json' \
  -H 'X-Secret: ...' \
  -d '{"cpu_single":1650,"disk_read":950.5,"iops_4k":22000,
       "cpu_steal":3.4,"load1":0.42,"uptime_days":540}'
```

## The monologue

Each node gets one line a day, generated from its own numbers. With `HOSPICE_LLM_KEY` set it goes through an OpenAI-compatible endpoint; without it, the templates in `monologue.py` pick by mood bucket. Same numbers either way, so the site works offline.

Cached per node per day. Regenerate with `POST /api/node/{id}/monologue`.

## Certification

`/certify` is the vendor-facing page. Three tiers, priced by how long the box is likely to last:

| tier | health | lifespan | oversell | price |
|---|---|---|---|---|
| platinum | ≥ 88 | ≥ 1500d | ≤ 0.25 | $200/qtr |
| gold | ≥ 75 | ≥ 900d | ≤ 0.40 | $120/qtr |
| silver | ≥ 60 | ≥ 400d | ≤ 0.60 | $50/qtr |

Certified nodes get a spot on the front page, an SVG badge they can embed, and a tracked `/go/{id}` link to their order page.

Badges update on their own. If the machine starts dying, so does the badge — which is the point.

```
<a href="https://bie.me/go/41">
  <img src="https://bie.me/badge/41.svg" alt="">
</a>
```

## Config

| variable | default | |
|---|---|---|
| `HOSPICE_DB` | `data/hospice.db` | SQLite path |
| `HOSPICE_LLM_KEY` | empty | blank = template mode |
| `HOSPICE_LLM_BASE` | `https://api.openai.com/v1` | any compatible endpoint |
| `HOSPICE_LLM_MODEL` | `gpt-4o-mini` | |
| `HOSPICE_SECRET` | empty | required on `POST /sample` when set |
| `PORT` | `8000` | |

## Routes

```
GET  /                      arena
GET  /node/{id}             one machine
GET  /graveyard             dead ones
GET  /certify               vendor page
GET  /compare?ids=1,2,3     side by side
GET  /lang/{zh|en}          switch language
GET  /badge/{id}.svg        small badge
GET  /card/{id}.svg         large card
GET  /go/{id}               affiliate redirect, counted
GET  /api/state             everything as JSON
GET  /api/node/{id}         one node as JSON
POST /api/node/{id}/sample  push a sample
POST /api/probe/{id}        SSH probe
```

Default language is English. Chinese is one click away.

## Deploying

```
sudo bash scripts/deploy.sh
```

Drops it in `/opt/vps-hospice`, makes a venv, writes a systemd unit, points nginx at it.

Docker works too:

```
docker build -t vps-hospice .
docker run -d -p 8000:8000 -v $(pwd)/data:/app/data vps-hospice
```

## Layout

```
app/survival.py   decay fit, prediction, tier thresholds
app/db.py         sqlite, schema, migrations
app/prober.py     SSH probing over four metric families
app/monologue.py  LLM call + template fallback
app/badge.py      SVG generation
app/main.py       routes
app/i18n.py       locale loader
app/locales/      zh.json, en.json
app/templates/    jinja2 + base.css
data/seed.py      synthetic dataset for demos
scripts/          cron entry, deploy script
```

## Licence

MIT
