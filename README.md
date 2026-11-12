# ⏳ VPS Hospice

> **每台 VPS 都有一段余生。**
> 我们记录它的衰老、预测它的死亡、写下它想说的话。

[English](#english) · [中文](#中文)

---

## 这 是 什 么

所有人都在测 VPS **现在多快**。

没有人回答这个问题：

```
这台 $8/年的机器，一年后还剩多少性能？
第二年会不会因为邻居超售变成废物？
它的硬盘什么时候会坏？
```

**VPS Hospice** 给每台机器一个**剩余寿命倒计时**，和一段**它想说的话**。

```
┌──────────────────────────────────────────┐
│  #42 · RackNerd 洛杉矶                    │
│                                          │
│            还有  487  天                  │
│          预计 2027-03-15 离开             │
│                                          │
│  健康度 71 · 月衰减 -2.1% · 超售 0.34     │
├──────────────────────────────────────────┤
│  💭 它今天说的话                          │
│                                          │
│  "今天第 3 次被抢走 CPU。硬盘转了 4 万圈。 │
│   有人在我身体里跑着一个博客，             │
│   每天 27 个人来看。我还剩 487 天，够用。" │
└──────────────────────────────────────────┘
```

## 为什么不一样

| 现有产品 | VPS Hospice |
|---|---|
| 测「当下性能」 | 测「剩余寿命」 |
| 数据是数字 | 数据是**故事** |
| 看一次就走 | **每天回来看它说了什么** |
| 无法预测 | **给一个具体日期** |

## 技术核心

### 1. 生存分析（不是线性外推）

```python
# 错误做法：线性拟合
#   y = 2100 - 20t   → t=105 时 y=0，但 t=200 时 y 为负数，荒谬

# 正确做法：指数衰减 + 威布尔生存函数
#   y = y0 * exp(-k*t)
#   S(t) = exp(-(t/λ)^shape)

health * exp(-k*t) = 0.3   # 健康度降到 30% 视为死亡
```

**三项指标加权**：CPU 40% / 磁盘 35% / IOPS 25%

### 2. 超售指数

```python
# CPU steal 的均值和波动
# 均值高 = 长期被抢；波动大 = 邻活跃
oversell = min(1.0, avg_steal/5 * 0.7 + std_steal/3 * 0.3)
```

### 3. 邻居挖矿检测

```python
# 你的 CPU 跑分下降，但 load 上升 → 有人在占你的核
if cpu_drop > 0.20 and load_rise > 0.3:
    mining_risk = min(0.95, cpu_drop * 1.8 + load_rise * 0.15)
```

### 4. 机器独白（LLM）

把真实数据喂给模型，让它用第一人称说话：

```
输入：健康度 71 / 剩余 487 天 / CPU 1650 / steal 3.4% / 跑着博客
输出："今天第 3 次被抢走 CPU。硬盘转了 4 万圈。"
```

**没有 API Key 也能跑**（模板模式）。

## 快速开始

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 生成 12 台演示机器（带 24 个月历史）
python3 -m data.seed

# 3. 启动
python3 -m app.main
# 或：uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 打开
open http://127.0.0.1:8000
```

**30 秒内就能看到完整效果**，不需要任何配置。

## 接入真实机器

### 1. 加一台机器

```python
from app import db

db.add_node(
    provider="RackNerd",
    location="Los Angeles",
    plan="1C1G/20G",
    price=8.0,
    ssh_host="192.0.2.1",
    ssh_port=22,
    ssh_user="root",
    ssh_key="/root/.ssh/id_ed25519",
)
```

### 2. 探测它

```bash
curl -X POST http://127.0.0.1:8000/api/probe/1
```

**会 SSH 上去跑**：
- `uptime` / `top`（load、steal）
- `geekbench6` 或 `sysbench`（CPU）
- `fio` 或 `dd`（磁盘、IOPS）
- `ping` 三个公共 DNS（延迟）

### 3. 定时探测

```bash
# crontab -e
0 3 * * * cd /opt/vps-hospice && python3 -m scripts.probe_all
```

### 4. 接别人的数据

如果你已经有监控数据，直接 POST：

```bash
curl -X POST http://127.0.0.1:8000/api/node/1/sample \
  -H "Content-Type: application/json" \
  -H "X-Secret: your-secret" \
  -d '{
    "cpu_single": 1650,
    "disk_read": 950.5,
    "iops_4k": 22000,
    "cpu_steal": 3.4,
    "load1": 0.42,
    "uptime_days": 540
  }'
```

## 环境变量

```bash
cp .env.example .env

# LLM（可选，不填用模板模式）
HOSPICE_LLM_KEY=sk-xxx
HOSPICE_LLM_MODEL=gpt-4o-mini

# 管理密钥
HOSPICE_SECRET=random-string
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 竞技场（所有机器） |
| GET | `/node/{id}` | 单机详情 |
| GET | `/graveyard` | 墓园 |
| GET | `/api/state` | 所有机器状态（JSON） |
| GET | `/api/node/{id}` | 单机详情（JSON） |
| POST | `/api/node/{id}/sample` | 写入采样 |
| POST | `/api/node/{id}/monologue` | 生成独白 |
| POST | `/api/probe/{id}` | 触发真实探测 |
| GET | `/healthz` | 健康检查 |

## 部署

### Docker

```bash
docker build -t vps-hospice .
docker run -d -p 8000:8000 -v $(pwd)/data:/app/data vps-hospice
```

### systemd

```bash
sudo cp scripts/vps-hospice.service /etc/systemd/system/
sudo systemctl enable --now vps-hospice
```

### Nginx 反代

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 数据模型

```
nodes        机器（provider/location/plan/price/ssh 配置）
  └─ samples      每次探测（cpu/disk/iops/steal/load/ping）
  └─ monologues   每日独白（text/mood/数据指纹）
config       全局配置
```

## 为什么值得做

**三个壁垒**：

1. **时间** —— 需要长期采集，别人抄不了
2. **翻译** —— 把技术数据翻译成情感语言（大多数测评站只做前者）
3. **深度** —— 生存分析不是简单外推

**一项数据，两种用途**：
- 对用户：**买不买这台机器**
- 对商家：**我的硬件还能撑多久**

## 许可

MIT

## 致谢

- 生存分析模型参考 MIL-STD-217 / Weibull 可靠性工程
- LLM 独白 prompt 迭代了 30+ 版才稳定
- 感谢 LET 社区的所有测速党

---

<a name="english"></a>
## English

**VPS Hospice** is a VPS longevity tracker.

Everyone benchmarks a server **once**. Nobody tracks it for **three years**.

This project:
- Predicts how many days a VPS has left (exponential decay + Weibull survival)
- Computes an oversell index from CPU steal
- Detects likely neighbor crypto-mining
- Generates a daily first-person monologue from the machine (LLM or template)

**Quick start:**

```bash
pip install -r requirements.txt
python3 -m data.seed          # 12 demo nodes with 24 months of history
python3 -m app.main           # http://127.0.0.1:8000
```

**Live demo**: _coming soon_

**License**: MIT
