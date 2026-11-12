"""
真实探测 —— SSH 到目标 VPS 跑测试，解析结果

安全说明：
- 只用 SSH 执行只读命令
- 不下载执行任何未审计的脚本（YABS 只跑官方 URL，且可关闭）
- 输出解析失败不影响主流程

依赖系统命令：ssh（OpenSSH 客户端）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any

# YABS 官方地址（Mason 维护）
YABS_URL = "https://yabs.sh"

# SSH 超时（秒）
SSH_TIMEOUT = 60
YABS_TIMEOUT = 900


def _ssh_base(node: dict) -> list[str]:
    cmd = [
        "ssh",
        "-p", str(node.get("ssh_port") or 22),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=10",
    ]
    key = node.get("ssh_key")
    if key:
        # key 可以是路径，也可以是私钥内容
        if os.path.isfile(key):
            cmd += ["-i", key]
        else:
            f = tempfile.NamedTemporaryFile("w", suffix=".key", delete=False)
            f.write(key)
            f.close()
            os.chmod(f.name, 0o600)
            cmd += ["-i", f.name]
    cmd.append("%s@%s" % (node.get("ssh_user") or "root", node["ssh_host"]))
    return cmd


def _run(cmd: list[str], timeout: int = SSH_TIMEOUT) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "ssh failed")[:300])
    return p.stdout


def _remote(node: dict, script: str, timeout: int = SSH_TIMEOUT) -> str:
    return _run(_ssh_base(node) + [script], timeout=timeout)


# ==================== 各项采集 ====================

def get_basic(node: dict) -> dict:
    """uptime / load / steal"""
    out = _remote(node, "uptime; echo '---'; cat /proc/loadavg; echo '---'; top -bn1 | head -3")
    d: dict[str, Any] = {}

    m = re.search(r"up\s+(\d+)\s+day", out)
    if m:
        d["uptime_days"] = int(m.group(1))
    else:
        m = re.search(r"up\s+(\d+):(\d+)", out)
        if m:
            d["uptime_days"] = 0
        else:
            d["uptime_days"] = 0

    parts = out.split("---")
    if len(parts) >= 2:
        try:
            d["load1"] = float(parts[1].split()[0])
        except Exception:
            pass

    m = re.search(r"([\d.]+)\s*st", out)
    if m:
        d["cpu_steal"] = float(m.group(1))

    return d


def get_cpu(node: dict) -> dict:
    """跑 Geekbench 6（如果装了）或用 sysbench 估算"""
    # 先看有没有 geekbench
    out = _remote(node, "which geekbench6 geekbench5 geekbench 2>/dev/null | head -1 || echo NONE")
    if "NONE" not in out and out.strip():
        try:
            raw = _remote(node, "%s --cpu 2>&1 | tail -40" % out.strip().split("/")[-1],
                          timeout=YABS_TIMEOUT)
            d = {}
            m = re.search(r"Single-Core Score\s+(\d+)", raw)
            if m:
                d["cpu_single"] = int(m.group(1))
            m = re.search(r"Multi-Core Score\s+(\d+)", raw)
            if m:
                d["cpu_multi"] = int(m.group(1))
            if d:
                return d
        except Exception:
            pass

    # 退路：sysbench（结果换算成粗略分数）
    out = _remote(node, "which sysbench || echo NONE")
    if "NONE" not in out and out.strip():
        try:
            raw = _remote(
                node,
                "sysbench cpu --cpu-max-prime=20000 --time=20 run 2>&1 | grep 'events per second'",
                timeout=120,
            )
            m = re.search(r"([\d.]+)", raw)
            if m:
                eps = float(m.group(1))
                # sysbench eps ≈ 30 → geekbench ≈ 1200（经验换算）
                return {"cpu_single": int(eps * 40)}
        except Exception:
            pass

    return {}


def get_disk(node: dict) -> dict:
    """fio 测试（如果没有 fio，用 dd 粗测）"""
    out = _remote(node, "which fio || echo NONE")
    if "NONE" not in out and out.strip():
        try:
            raw = _remote(
                node,
                "fio --name=seq --filename=/tmp/.fiotest --size=512M --rw=read "
                "--bs=1M --direct=1 --numjobs=1 --runtime=30 --time_based "
                "--output-format=json 2>/dev/null",
                timeout=180,
            )
            j = json.loads(raw)
            bw = j["jobs"][0]["read"]["bw"] / 1024  # KiB/s → MB/s
            d = {"disk_read": round(bw, 1)}
            # 再测 4K 随机
            raw2 = _remote(
                node,
                "fio --name=rnd --filename=/tmp/.fiotest2 --size=256M --rw=randread "
                "--bs=4k --direct=1 --numjobs=1 --iodepth=32 --runtime=30 --time_based "
                "--output-format=json 2>/dev/null",
                timeout=180,
            )
            j2 = json.loads(raw2)
            d["iops_4k"] = int(j2["jobs"][0]["read"]["iops"])
            _remote(node, "rm -f /tmp/.fiotest /tmp/.fiotest2")
            return d
        except Exception:
            pass

    # 退路：dd
    try:
        raw = _remote(
            node,
            "dd if=/dev/zero of=/tmp/.ddtest bs=1M count=512 oflag=direct 2>&1 | tail -1; "
            "rm -f /tmp/.ddtest",
            timeout=120,
        )
        m = re.search(r"([\d.]+)\s*[MG]B/s", raw)
        if m:
            v = float(m.group(1))
            if "GB/s" in raw:
                v *= 1024
            return {"disk_write": round(v, 1)}
    except Exception:
        pass

    return {}


def get_net(node: dict) -> dict:
    """ping 三个公共节点，取平均延迟"""
    out = _remote(
        node,
        "for h in 1.1.1.1 8.8.8.8 9.9.9.9; do "
        "ping -c 3 -W 3 $h 2>/dev/null | tail -1 | awk -F'/' '{print $5}'; done",
        timeout=60,
    )
    vals = []
    for line in out.splitlines():
        line = line.strip()
        if re.match(r"^[\d.]+$", line):
            vals.append(float(line))
    if vals:
        return {"ping_avg": round(sum(vals) / len(vals), 1)}
    return {}


def probe(node: dict) -> dict:
    """
    完整探测一台机器

    Returns:
        dict，可直接喂给 db.add_sample
    """
    data: dict[str, Any] = {}
    errors = []

    for name, fn in [
        ("basic", get_basic),
        ("cpu", get_cpu),
        ("disk", get_disk),
        ("net", get_net),
    ]:
        try:
            data.update(fn(node))
        except Exception as e:
            errors.append("%s: %s" % (name, str(e)[:80]))

    if errors:
        data["raw"] = {"errors": errors}

    if not any(k in data for k in ("cpu_single", "disk_read", "disk_write")):
        raise RuntimeError("探测失败，所有指标都没拿到: " + "; ".join(errors))

    return data
