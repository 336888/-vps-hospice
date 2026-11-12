import json
import os
from functools import lru_cache

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locales")
DEFAULT = "zh"
SUPPORTED = ("zh", "en")


@lru_cache(maxsize=8)
def load(code: str) -> dict:
    if code not in SUPPORTED:
        code = DEFAULT
    path = os.path.join(LOCALE_DIR, code + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def detect(request) -> str:
    q = request.query_params.get("lang")
    if q in SUPPORTED:
        return q
    c = request.cookies.get("lang")
    if c in SUPPORTED:
        return c
    al = (request.headers.get("accept-language") or "").lower()
    if al.startswith("en"):
        return "en"
    return DEFAULT


def translator(code: str):
    table = load(code)
    fallback = load(DEFAULT)

    def t(key: str, **kw):
        s = table.get(key) or fallback.get(key) or key
        if kw:
            try:
                return s.format(**kw)
            except (KeyError, IndexError, ValueError):
                return s
        return s

    return t


def all_locales() -> list:
    return list(SUPPORTED)
