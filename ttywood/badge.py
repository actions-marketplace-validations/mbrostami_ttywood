"""The badge: a flat shields-style SVG saying what the last clean-room check
found, plus the shields.io "endpoint" JSON for the day it is hosted.

Two flavours because there is no server yet: `badge.svg` is committed into
the project (works today, updated by the Action on every run), `badge.json`
is the payload a hosted endpoint returns so
https://img.shields.io/endpoint?url=<ours>/badge/<owner>/<repo>.json works
without any further code."""

from __future__ import annotations

import json
from pathlib import Path

from .check import Report

COLORS = {"pass": "#3fb950", "warn": "#d29922", "fail": "#f85149", "blocked": "#9e9e9e"}
LABEL_BG = "#555"
LABEL = "ttywood"


def message(r: Report) -> str:
    what = {"pass": "install ok", "warn": "install ok, friction", "fail": "install broken",
            "blocked": "not checkable here"}[r.verdict]
    n = sum(1 for f in r.findings if f.severity != "env")
    tail = f", {n} finding{'s' if n != 1 else ''}" if n and r.verdict not in ("pass", "blocked") else ""
    return f"{what}{tail} · {r.checked_at[:10] or r.ref_date}"


def endpoint_json(r: Report) -> dict:
    return {"schemaVersion": 1, "label": LABEL, "message": message(r),
            "color": {"pass": "brightgreen", "warn": "yellow", "fail": "red", "blocked": "lightgrey"}[r.verdict],
            "namedLogo": "youtube", "cacheSeconds": 3600}


def _width(text: str) -> int:
    """Rough Verdana-11 width the way shields does it: 6.5 px a char with a
    little extra for wide letters; good enough for a static badge."""
    wide = sum(1 for c in text if c in "mwMW@")
    narrow = sum(1 for c in text if c in "iljtfrI.·,' ")
    return int(round(6.5 * len(text) + 2.5 * wide - 2.0 * narrow)) + 10


def badge_svg(r: Report, label: str = LABEL) -> str:
    msg = message(r)
    lw, mw = _width(label), _width(msg)
    w = lw + mw
    color = COLORS[r.verdict]
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{esc(label)}: {esc(msg)}">
  <title>{esc(label)}: {esc(msg)}</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="20" fill="{LABEL_BG}"/>
    <rect x="{lw}" width="{mw}" height="20" fill="{color}"/>
    <rect width="{w}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{esc(label)}</text>
    <text x="{lw / 2:.1f}" y="14">{esc(label)}</text>
    <text x="{lw + mw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{esc(msg)}</text>
    <text x="{lw + mw / 2:.1f}" y="14">{esc(msg)}</text>
  </g>
</svg>
"""


def write_badge(episode_dir: Path, r: Report) -> tuple[Path, Path]:
    svg, js = episode_dir / "badge.svg", episode_dir / "badge.json"
    svg.write_text(badge_svg(r))
    js.write_text(json.dumps(endpoint_json(r), indent=2) + "\n")
    return svg, js
