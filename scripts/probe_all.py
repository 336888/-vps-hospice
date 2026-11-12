#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, monologue, prober, survival


def main():
    db.init()
    targets = [n for n in db.nodes() if not n.get("died_at") and n.get("ssh_host")]
    if not targets:
        print("no nodes with ssh config")
        return

    ok = fail = 0
    for n in targets:
        label = "#%d %s %s" % (n["id"], n["provider"], n["location"])
        try:
            data = prober.probe(n)
            db.add_sample(n["id"], data)
            s = db.samples(n["id"])
            pred = survival.predict(s)
            db.save_prediction(n["id"], pred.as_dict())
            if not db.has_monologue_today(n["id"]):
                text, mood, dh = monologue.generate(n, s, pred.as_dict(), "zh")
                db.add_monologue(n["id"], text, mood, dh)
            p = pred.as_dict()
            print("ok   %-38s health %5.1f  days %5d" % (label, p["health"], p["days_left"]))
            ok += 1
        except Exception as e:
            print("fail %-38s %s" % (label, str(e)[:60]))
            fail += 1

    print("\ndone: %d ok, %d failed" % (ok, fail))


if __name__ == "__main__":
    main()
