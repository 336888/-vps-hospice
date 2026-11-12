# VPS Hospice

Every VPS has a remainder. This tracks it.

Most benchmark sites tell you how fast a server is **right now**. Nobody tells you how many days it has left. That's what this does.

```
#42  RackNerd  Los Angeles
     487 days left
     expected to go 2027-03-15

     "今天第 3 次被抢走 CPU。硬盘转了 4 万圈。
      有人在我身体里跑着一个博客，每天 27 个人来看。
      我还剩 487 天，够用。"
```

## What it does

- **Lifespan prediction** — exponential decay fit per metric, solved against a 30% death threshold
- **Oversell index** — mean and variance of CPU steal, normalized 0-1
- **Mining detection** — CPU score drop crossed with load rise
- **Daily monologue** — the machine talks about its own data (LLM or template fallback)
- **Bilingual UI** — Chinese and English, switchable at runtime

## Quick start

```bash
pip install -r requirements.txt
python3 -m data.seed          # 12 demo nodes, 24 months of history
python3 -m app.main           # http://127.0.0.1:8000
```

## Adding real nodes

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

```bash
curl -X POST http://127.0.0.1:8000/api/probe/1
```

The prober SSHs in and runs `uptime`, `top`, `geekbench6` (or `sysbench`), `fio` (or `dd`), and three pings. Results go into `samples`.

Cron it:

```bash
0 3 * * * cd /opt/vps-hospice && .venv/bin/python -m scripts.probe_all
```

## Push samples from your own monitoring

```bash
curl -X POST http://127.0.0.1:8000/api/node/1/sample \
  -H "Content-Type: application/json" \
  -H "X-Secret: $HOSPICE_SECRET" \
  -d '{"cpu_single":1650,"disk_read":950.5,"iops_4k":22000,
       "cpu_steal":3.4,"load1":0.42,"uptime_days":540}'
```

## Config

```bash
cp .env.example .env
```

| Variable | Default | Notes |
|---|---|---|
| `HOSPICE_DB` | `data/hospice.db` | SQLite path |
| `HOSPICE_LLM_KEY` | empty | Leave blank to use template mode |
| `HOSPICE_LLM_BASE` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `HOSPICE_LLM_MODEL` | `gpt-4o-mini` | |
| `HOSPICE_SECRET` | empty | Required for sample POSTs if set |
| `PORT` | `8000` | |

## How the prediction works

Each metric is fitted to `y = y0 * exp(-k * t)`. The three rates are weighted 40/35/25 for CPU/disk/IOPS. Remaining days is solved from `health * exp(-k * t) = 0.30`, capped at 10 years.

It is not linear extrapolation. Linear gives you a negative score eventually, which is nonsense.

Confidence is `high` with 24+ samples and R² > 0.5 on all three fits, `medium` at 8+, `low` below that.

## Monologue

With an LLM key, the prompt gets the real numbers and returns 50-80 characters of first-person text. Without a key, it falls back to templates in `app/monologue.py` — same data, chosen by mood bucket.

Cached per node per day.

## API

| Method | Path | |
|---|---|---|
| GET | `/` | Arena |
| GET | `/node/{id}` | Single machine |
| GET | `/graveyard` | Departed machines |
| GET | `/lang/{zh\|en}` | Switch UI language |
| GET | `/api/state` | All nodes |
| GET | `/api/node/{id}` | One node + prediction |
| POST | `/api/node/{id}/sample` | Push a sample |
| POST | `/api/node/{id}/monologue` | Regenerate monologue |
| POST | `/api/probe/{id}` | Probe over SSH |

## Deploy

```bash
sudo bash scripts/deploy.sh
```

Installs to `/opt/vps-hospice`, creates a venv, sets up systemd and nginx.

Docker:

```bash
docker build -t vps-hospice .
docker run -d -p 8000:8000 -v $(pwd)/data:/app/data vps-hospice
```

## Layout

```
app/
  main.py        routes
  survival.py    decay fit + prediction
  monologue.py   LLM / template text
  prober.py      SSH probing
  db.py          sqlite
  i18n.py        translation loader
  locales/       zh.json, en.json
  templates/     jinja2
data/
  seed.py        demo dataset
scripts/
  probe_all.py   cron entry
  deploy.sh      one-shot install
```

## License

MIT
