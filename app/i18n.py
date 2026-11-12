import json
import os
from functools import lru_cache

DIR = os.path.join(os.path.dirname(__file__), "locales")
DEFAULT = "en"
SUPPORTED = ("en", "zh", "zh-TW", "ja", "ru", "de")

ALIASES = {
    "zh-cn": "zh", "zh-hans": "zh", "zh-sg": "zh", "zh-my": "zh",
    "zh-tw": "zh-TW", "zh-hk": "zh-TW", "zh-mo": "zh-TW", "zh-hant": "zh-TW",
    "ja-jp": "ja", "ru-ru": "ru", "de-de": "de", "de-at": "de", "de-ch": "de",
    "en-us": "en", "en-gb": "en", "en-au": "en", "en-ca": "en",
}


@lru_cache(maxsize=16)
def load(code):
    path = os.path.join(DIR, f"{code}.json")
    if not os.path.exists(path):
        path = os.path.join(DIR, f"{DEFAULT}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse(header):
    out = []
    for part in (header or "").split(","):
        bits = part.strip().split(";")
        tag = bits[0].strip().lower()
        if not tag or tag == "*":
            continue
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        out.append((q, tag))
    return [t for q, t in sorted(out, key=lambda x: -x[0]) if q > 0]


def match(tag):
    if tag in SUPPORTED:
        return tag
    if tag in ALIASES:
        return ALIASES[tag]
    base = tag.split("-")[0]
    if base in SUPPORTED:
        return base
    return ALIASES.get(base)


def detect(request):
    for src in (request.query_params.get("lang"), request.cookies.get("lang")):
        if src in SUPPORTED:
            return src
        if src:
            hit = match(src.lower())
            if hit:
                return hit
    for tag in parse(request.headers.get("accept-language")):
        hit = match(tag)
        if hit:
            return hit
    return DEFAULT


def translator(code):
    table, fallback = load(code if code in SUPPORTED else DEFAULT), load(DEFAULT)

    def t(key, **kw):
        s = table.get(key) or fallback.get(key) or key
        return s.format(**kw) if kw else s

    return t
