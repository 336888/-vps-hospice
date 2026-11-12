TIERS = {
    "platinum": {"label": "PLATINUM", "c1": "#e8eef7", "c2": "#b8c6db", "bg": "#1a2540", "dot": "#e8f4ff"},
    "gold":     {"label": "GOLD",     "c1": "#ffe9a8", "c2": "#f5b942", "bg": "#241c0a", "dot": "#ffd97a"},
    "silver":   {"label": "SILVER",   "c1": "#e6ecf5", "c2": "#9aabc4", "bg": "#1a2030", "dot": "#c8d6e8"},
    "none":     {"label": "TRACKED",  "c1": "#8fa6c8", "c2": "#5b6fa8", "bg": "#121828", "dot": "#6b7a99"},
}

STYLE = "flat"      # flat | for-the-badge
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _text_width(s, size=11):
    return int(len(str(s)) * size * 0.62)


def badge_svg(tier="none", health=0, days=0, style="flat"):
    t = TIERS.get(tier) or TIERS["none"]
    label = "VPS HOSPICE"
    value = "%s %d/100" % (t["label"], int(health))

    pad = 9
    lw = _text_width(label) + pad * 2
    vw = _text_width(value) + pad * 2
    w = lw + vw
    h = 20 if style != "for-the-badge" else 28
    baseline = h / 2 + 4

    return """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" role="img" aria-label="{label}: {value}">
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{c1}"/>
    <stop offset="1" stop-color="{c2}"/>
  </linearGradient>
  <linearGradient id="l" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#3a4356"/>
    <stop offset="1" stop-color="#2a3145"/>
  </linearGradient>
  <clipPath id="r"><rect width="{w}" height="{h}" rx="4" ry="4"/></clipPath>
</defs>
<g clip-path="url(#r)">
  <rect width="{lw}" height="{h}" fill="url(#l)"/>
  <rect x="{lw}" width="{vw}" height="{h}" fill="{bg}"/>
  <rect x="{lw}" width="{vw}" height="{h}" fill="url(#g)" opacity=".15"/>
  <circle cx="{dotx}" cy="{doty}" r="3" fill="{dot}"/>
</g>
<g fill="#fff" font-family="{font}" font-size="{fs}" font-weight="600">
  <text x="{lx}" y="{baseline}" text-anchor="middle">{label}</text>
  <text x="{vx}" y="{baseline}" text-anchor="middle" fill="{c1}">{value}</text>
</g>
</svg>""".format(
        w=w, h=h, lw=lw, vw=vw,
        c1=t["c1"], c2=t["c2"], bg=t["bg"], dot=t["dot"],
        label=_esc(label), value=_esc(value),
        dotx=lw - 7, doty=h / 2,
        lx=lw / 2 + 3, vx=lw + vw / 2,
        baseline=baseline, font=FONT,
        fs=10 if style != "for-the-badge" else 12,
    )


def card_svg(provider, location, tier, health, days, death_date):
    """Larger embeddable card for vendor websites."""
    t = TIERS.get(tier) or TIERS["none"]
    w, h = 300, 96
    return """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#121828"/>
    <stop offset="1" stop-color="#0d1220"/>
  </linearGradient>
  <linearGradient id="ac" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{c1}"/>
    <stop offset="1" stop-color="{c2}"/>
  </linearGradient>
</defs>
<rect width="{w}" height="{h}" rx="12" fill="url(#bg)" stroke="#1e2739"/>
<rect x="0" y="0" width="4" height="{h}" rx="2" fill="url(#ac)"/>
<text x="20" y="30" fill="#8fa6c8" font-family="{font}" font-size="10.5" font-weight="700" letter-spacing="1.2">VPS HOSPICE</text>
<text x="20" y="52" fill="{c1}" font-family="{font}" font-size="15" font-weight="900">{tier}</text>
<text x="20" y="74" fill="#6b7a99" font-family="{font}" font-size="11">{provider} · {location}</text>
<text x="{rx}" y="34" fill="#fff" font-family="{font}" font-size="22" font-weight="900" text-anchor="end">{health}</text>
<text x="{rx}" y="49" fill="#5a6b8a" font-family="{font}" font-size="9.5" text-anchor="end">HEALTH</text>
<text x="{rx}" y="72" fill="#8fa6c8" font-family="{font}" font-size="13" font-weight="700" text-anchor="end">{days} days</text>
<text x="{rx}" y="87" fill="#4a5568" font-family="{font}" font-size="9" text-anchor="end">est. {death_date}</text>
</svg>""".format(
        w=w, h=h, font=FONT,
        c1=t["c1"], c2=t["c2"],
        tier=_esc(t["label"]),
        provider=_esc(provider), location=_esc(location),
        health=int(health), days=int(days), death_date=_esc(death_date),
        rx=w - 20,
    )
