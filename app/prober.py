import json
import os
import re
import subprocess
import tempfile

SHORT, LONG = 60, 900


def _ssh(n):
    cmd = ["ssh", "-p", str(n.get("ssh_port") or 22),
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
           "-o", "ServerAliveInterval=10"]
    key = n.get("ssh_key")
    if key:
        if not os.path.isfile(key):
            f = tempfile.NamedTemporaryFile("w", suffix=".key", delete=False)
            f.write(key)
            f.close()
            os.chmod(f.name, 0o600)
            key = f.name
        cmd += ["-i", key]
    return cmd + [f"{n.get('ssh_user') or 'root'}@{n['ssh_host']}"]


def _run(n, script, timeout=SHORT):
    p = subprocess.run(_ssh(n) + [script], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "ssh failed")[:300])
    return p.stdout


def basic(n):
    out = _run(n, "uptime; echo ---; cat /proc/loadavg; echo ---; top -bn1 | head -3")
    d = {}
    m = re.search(r"up\s+(\d+)\s+day", out)
    d["uptime_days"] = int(m.group(1)) if m else 0
    parts = out.split("---")
    if len(parts) > 1:
        d["load1"] = float(parts[1].split()[0])
    m = re.search(r"([\d.]+)\s*st", out)
    if m:
        d["cpu_steal"] = float(m.group(1))
    return d


def cpu(n):
    path = _run(n, "command -v geekbench6 geekbench5 geekbench 2>/dev/null | head -1 || true").strip()
    if path:
        raw = _run(n, f"{os.path.basename(path)} --cpu 2>&1 | tail -40", LONG)
        d = {}
        for k, pat in (("cpu_single", r"Single-Core Score\s+(\d+)"),
                       ("cpu_multi", r"Multi-Core Score\s+(\d+)")):
            m = re.search(pat, raw)
            if m:
                d[k] = int(m.group(1))
        if d:
            return d
    if _run(n, "command -v sysbench || true").strip():
        raw = _run(n, "sysbench cpu --cpu-max-prime=20000 --time=20 run 2>&1 "
                      "| grep 'events per second'", 120)
        m = re.search(r"([\d.]+)", raw)
        if m:
            return {"cpu_single": int(float(m.group(1)) * 40)}
    return {}


def disk(n):
    if _run(n, "command -v fio || true").strip():
        try:
            a = json.loads(_run(n, "fio --name=s --filename=/tmp/.f1 --size=512M --rw=read "
                                   "--bs=1M --direct=1 --runtime=30 --time_based "
                                   "--output-format=json 2>/dev/null", 180))
            b = json.loads(_run(n, "fio --name=r --filename=/tmp/.f2 --size=256M --rw=randread "
                                   "--bs=4k --direct=1 --iodepth=32 --runtime=30 --time_based "
                                   "--output-format=json 2>/dev/null", 180))
            _run(n, "rm -f /tmp/.f1 /tmp/.f2")
            return {"disk_read": round(a["jobs"][0]["read"]["bw"] / 1024, 1),
                    "iops_4k": int(b["jobs"][0]["read"]["iops"])}
        except (ValueError, KeyError, RuntimeError):
            pass
    out = _run(n, "dd if=/dev/zero of=/tmp/.d bs=1M count=512 oflag=direct 2>&1 | tail -1; "
                  "rm -f /tmp/.d", 120)
    m = re.search(r"([\d.]+)\s*([MG])B/s", out)
    return {"disk_write": round(float(m.group(1)) * (1024 if m.group(2) == "G" else 1), 1)} if m else {}


def net(n):
    out = _run(n, "for h in 1.1.1.1 8.8.8.8 9.9.9.9; do ping -c 3 -W 3 $h 2>/dev/null "
                  "| tail -1 | awk -F/ '{print $5}'; done")
    v = [float(x) for x in out.split() if re.fullmatch(r"[\d.]+", x)]
    return {"ping_avg": round(sum(v) / len(v), 1)} if v else {}


def probe(n):
    data, errs = {}, []
    for name, fn in (("basic", basic), ("cpu", cpu), ("disk", disk), ("net", net)):
        try:
            data.update(fn(n))
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
            errs.append(f"{name}: {str(e)[:80]}")
    if errs:
        data["raw"] = {"errors": errs}
    if not {"cpu_single", "disk_read", "disk_write"} & data.keys():
        raise RuntimeError("; ".join(errs))
    return data
