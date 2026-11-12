import json
import os
import re
import subprocess
import tempfile

SSH_TIMEOUT = 60
LONG_TIMEOUT = 900


def _ssh(node):
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


def _run(node, script, timeout=SSH_TIMEOUT):
    p = subprocess.run(_ssh(node) + [script], capture_output=True, text=True,
                       timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "ssh failed")[:300])
    return p.stdout


def basic(node):
    out = _run(node, "uptime; echo ---; cat /proc/loadavg; echo ---; top -bn1 | head -3")
    d = {}
    m = re.search(r"up\s+(\d+)\s+day", out)
    d["uptime_days"] = int(m.group(1)) if m else 0
    parts = out.split("---")
    if len(parts) >= 2:
        try:
            d["load1"] = float(parts[1].split()[0])
        except (IndexError, ValueError):
            pass
    m = re.search(r"([\d.]+)\s*st", out)
    if m:
        d["cpu_steal"] = float(m.group(1))
    return d


def cpu(node):
    out = _run(node, "command -v geekbench6 geekbench5 geekbench 2>/dev/null | head -1 || true")
    binary = out.strip()
    if binary:
        try:
            raw = _run(node, "%s --cpu 2>&1 | tail -40" % binary.split("/")[-1],
                       timeout=LONG_TIMEOUT)
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

    out = _run(node, "command -v sysbench || true")
    if out.strip():
        try:
            raw = _run(node,
                       "sysbench cpu --cpu-max-prime=20000 --time=20 run "
                       "2>&1 | grep 'events per second'", timeout=120)
            m = re.search(r"([\d.]+)", raw)
            if m:
                return {"cpu_single": int(float(m.group(1)) * 40)}
        except Exception:
            pass
    return {}


def disk(node):
    out = _run(node, "command -v fio || true")
    if out.strip():
        try:
            raw = _run(node,
                       "fio --name=seq --filename=/tmp/.fiotest --size=512M --rw=read "
                       "--bs=1M --direct=1 --numjobs=1 --runtime=30 --time_based "
                       "--output-format=json 2>/dev/null", timeout=180)
            j = json.loads(raw)
            d = {"disk_read": round(j["jobs"][0]["read"]["bw"] / 1024, 1)}

            raw2 = _run(node,
                        "fio --name=rnd --filename=/tmp/.fiotest2 --size=256M --rw=randread "
                        "--bs=4k --direct=1 --numjobs=1 --iodepth=32 --runtime=30 "
                        "--time_based --output-format=json 2>/dev/null", timeout=180)
            j2 = json.loads(raw2)
            d["iops_4k"] = int(j2["jobs"][0]["read"]["iops"])
            _run(node, "rm -f /tmp/.fiotest /tmp/.fiotest2")
            return d
        except Exception:
            pass

    try:
        raw = _run(node,
                   "dd if=/dev/zero of=/tmp/.ddtest bs=1M count=512 oflag=direct 2>&1 | tail -1; "
                   "rm -f /tmp/.ddtest", timeout=120)
        m = re.search(r"([\d.]+)\s*([MG])B/s", raw)
        if m:
            v = float(m.group(1))
            if m.group(2) == "G":
                v *= 1024
            return {"disk_write": round(v, 1)}
    except Exception:
        pass
    return {}


def net(node):
    out = _run(node,
               "for h in 1.1.1.1 8.8.8.8 9.9.9.9; do "
               "ping -c 3 -W 3 $h 2>/dev/null | tail -1 | awk -F/ '{print $5}'; done",
               timeout=60)
    vals = [float(x) for x in out.split() if re.match(r"^[\d.]+$", x)]
    if vals:
        return {"ping_avg": round(sum(vals) / len(vals), 1)}
    return {}


def probe(node):
    data = {}
    errors = []
    for name, fn in (("basic", basic), ("cpu", cpu), ("disk", disk), ("net", net)):
        try:
            data.update(fn(node))
        except Exception as e:
            errors.append("%s: %s" % (name, str(e)[:80]))

    if errors:
        data["raw"] = {"errors": errors}
    if not any(k in data for k in ("cpu_single", "disk_read", "disk_write")):
        raise RuntimeError("all metrics failed: " + "; ".join(errors))
    return data
