#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, monologue, prober, survival


def main():
    db.init()
    targets = [n for n in db.nodes(alive_only=True) if n.get("ssh_host")]
    ok = fail = 0
    for n in targets:
        try:
            db.add_sample(n["id"], prober.probe(n))
            rows = db.samples(n["id"])
            pred = survival.predict(rows)
            db.save_pred(n["id"], pred.as_dict())
            if not db.spoken_today(n["id"]):
                text, mood, h = monologue.generate(n, rows, pred.as_dict(), "en")
                db.add_monologue(n["id"], text, mood, h)
            print(f"ok   {n['provider']:<12} {n['location']:<14} {pred.health:>6.1f} {pred.days:>5}")
            ok += 1
        except Exception as e:
            print(f"fail {n['provider']:<12} {n['location']:<14} {str(e)[:60]}")
            fail += 1
    print(f"\n{ok} ok, {fail} failed")


if __name__ == "__main__":
    main()
