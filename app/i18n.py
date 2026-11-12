import json
import os
from functools import lru_cache

DIR = os.path.join(os.path.dirname(__file__), "locales")
DEFAULT = "en"
SUPPORTED = ("zh", "en")


@lru_cache(maxsize=8)
def load(code):
    with open(os.path.join(DIR, f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


def detect(request):
    for src in (request.query_params.get("lang"), request.cookies.get("lang")):
        if src in SUPPORTED:
            return src
    return DEFAULT


def translator(code):
    table, fallback = load(code if code in SUPPORTED else DEFAULT), load(DEFAULT)

    def t(key, **kw):
        s = table.get(key) or fallback.get(key) or key
        return s.format(**kw) if kw else s

    return t
