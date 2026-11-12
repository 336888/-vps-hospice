#!/usr/bin/env python3
"""批量探测所有存活机器（供 cron 调用）"""
import sys, os, time, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, survival, monologue
from app import prober


def main():
    db.init_db()
    nodes = [n for n in db.all_nodes() if not n.get("died_at") and n.get("ssh_host")]
    if not nodes:
        print("没有配置 SSH 的存活机器")
        return

    print("开始探测 %d 台机器" % len(nodes))
    ok = fail = 0

    for n in nodes:
        nid = n["id"]
        label = "#%d %s %s" % (nid, n["provider"], n["location"])
        try:
            data = prober.probe(n)
            db.add_sample(nid, data)
            samples = db.node_samples(nid)
            pred = survival.predict(samples)
            db.update_prediction(nid, pred.to_dict())

            # 今天还没生成独白就生成
            if not db.has_monologue_today(nid):
                text, mood, dh = monologue.generate(n, samples, pred.to_dict())
                db.add_monologue(nid, text, mood, dh)

            d = pred.to_dict()
            print("  ✓ %-40s 健康 %5.1f  剩余 %5d 天" % (label, d["health"], d["days_left"]))
            ok += 1
        except Exception as e:
            print("  ✗ %-40s %s" % (label, str(e)[:70]))
            fail += 1

    print()
    print("完成：成功 %d / 失败 %d" % (ok, fail))


if __name__ == "__main__":
    main()
