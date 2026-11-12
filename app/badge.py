T = {
    "platinum": ("PLATINUM", "#e8eef7", "#b8c6db", "#1a2540"),
    "gold": ("GOLD", "#ffe9a8", "#f5b942", "#241c0a"),
    "silver": ("SILVER", "#e6ecf5", "#9aabc4", "#1a2030"),
    None: ("TRACKED", "#8fa6c8", "#5b6fa8", "#121828"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif"


def _w(s, size=11):
    return int(len(str(s)) * size * .62)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def small(tier=None, health=0):
    label, c1, c2, bg = T.get(tier) or T[None]
    left, right = "VPS HOSPICE", f"{label} {int(health)}/100"
    lw, rw, h = _w(left) + 18, _w(right) + 18, 20
    w = lw + rw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
<defs><linearGradient id="a" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>
<clipPath id="r"><rect width="{w}" height="{h}" rx="3"/></clipPath></defs>
<g clip-path="url(#r)"><rect width="{lw}" height="{h}" fill="#2a3145"/>
<rect x="{lw}" width="{rw}" height="{h}" fill="{bg}"/><circle cx="{lw-7}" cy="{h/2}" r="2.4" fill="{c1}"/></g>
<g font-family="{FONT}" font-size="10" font-weight="600" fill="#fff">
<text x="{lw/2+3}" y="{h/2+3.5}" text-anchor="middle">{_esc(left)}</text>
<text x="{lw+rw/2}" y="{h/2+3.5}" text-anchor="middle" fill="{c1}">{_esc(right)}</text></g></svg>"""


def card(provider, location, tier, health, days, end):
    label, c1, c2, _ = T.get(tier) or T[None]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="96">
<defs><linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#121828"/><stop offset="1" stop-color="#0d1220"/></linearGradient>
<linearGradient id="c" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs>
<rect width="300" height="96" rx="10" fill="url(#b)"/><rect width="3" height="96" fill="url(#c)"/>
<g font-family="{FONT}">
<text x="18" y="28" fill="#6b7a99" font-size="9" font-weight="700" letter-spacing="1.4">VPS HOSPICE</text>
<text x="18" y="50" fill="{c1}" font-size="14" font-weight="800">{_esc(label)}</text>
<text x="18" y="70" fill="#5a6b8a" font-size="10">{_esc(provider)} &middot; {_esc(location)}</text>
<text x="282" y="34" fill="#fff" font-size="20" font-weight="800" text-anchor="end">{int(health)}</text>
<text x="282" y="48" fill="#4a5568" font-size="8" text-anchor="end">HEALTH</text>
<text x="282" y="72" fill="#8fa6c8" font-size="12" font-weight="600" text-anchor="end">{int(days)} days</text>
<text x="282" y="86" fill="#3f4a5c" font-size="8" text-anchor="end">{_esc(end)}</text></g></svg>"""
