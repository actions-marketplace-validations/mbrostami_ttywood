"""Numbered series: plan an arc once, then make one episode at a time.

A one-off episode has to earn its viewer from scratch every time. A series
gives them a reason to come back, so the plan lives in series/<slug>/series.json
and each `start_next` turns the next unmade entry into a normal episode
directory that the usual pipeline records, renders and publishes.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .config import load_prompt, series_root
from .llm import run as llm_run


def series_path(slug: str) -> Path:
    return series_root() / slug / "series.json"


def load(slug: str) -> dict:
    p = series_path(slug)
    if not p.exists():
        raise FileNotFoundError(f"no series {slug!r}; have {[s.name for s in known()]}")
    return json.loads(p.read_text())


def save(data: dict) -> Path:
    p = series_path(data["slug"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return p


def known() -> list[Path]:
    return sorted(d for d in series_root().glob("*") if (d / "series.json").exists())


def plan_series(subject: str, count: int = 6, prefer: str = "", repo: str = "",
                slug: str | None = None, log=print) -> Path:
    """Ask Claude for the arc and write series/<slug>/series.json."""
    from .plan import extract_json
    facts: dict = {}
    if repo:
        from .check import repo_slug
        from .repoinfo import fetch_repo_info, usable_description
        full, url = repo_slug(repo)
        tmp = series_root() / "_facts"
        tmp.mkdir(parents=True, exist_ok=True)
        info = fetch_repo_info(tmp, full)
        facts = {"repo": full, "url": url, "stars": info.get("stars"),
                 "description": usable_description(info.get("description"), full.split("/")[-1])}
    prompt = load_prompt("series.md")
    for key, val in (("SUBJECT", subject), ("COUNT", count),
                     ("FACTS", json.dumps(facts, indent=1) if facts else "{}"),
                     ("PREFER", f"Editor's steer for this series: {prefer}" if prefer else "")):
        prompt = prompt.replace("{{" + key + "}}", str(val))
    log(f"  planning {count} episodes on {subject} (LLM, 1-3 min)")
    output = llm_run(prompt, allowed_tools="WebFetch,WebSearch", input_mode="stdin", timeout=900)
    data = extract_json(output)
    if not data or not data.get("episodes"):
        raise RuntimeError(f"no usable JSON from LLM:\n{output[-1500:]}")
    data["slug"] = slug or data.get("slug") or subject.lower().replace(" ", "-")[:40]
    data["created"] = dt.date.today().isoformat()
    if facts:
        data.setdefault("repo", facts["repo"])
        data.setdefault("url", facts["url"])
    for i, ep in enumerate(data["episodes"], 1):
        ep["n"] = i
        ep.setdefault("episode_dir", None)
        ep.setdefault("video", None)
    out = save(data)
    log(f"  {data['title']}: {len(data['episodes'])} episodes -> {out}")
    return out


def episode_name(data: dict, ep: dict) -> str:
    return f"{data['slug']}-e{ep['n']:02d}"


def next_entry(data: dict) -> dict | None:
    return next((e for e in data["episodes"] if not e.get("episode_dir")), None)


def _series_brief(data: dict, ep: dict) -> str:
    """What the rehearsing agent needs to know about continuity."""
    done = [e for e in data["episodes"] if e["n"] < ep["n"]]
    seen = "\n".join(f"  E{e['n']:02d} {e['title']}: {', '.join(e.get('teaches') or [])}"
                     for e in done) or "  (none - this is the first episode)"
    later = "\n".join(f"  E{e['n']:02d} {e['title']}"
                      for e in data["episodes"] if e["n"] > ep["n"]) or "  (none)"
    return (
        f"This is episode {ep['n']} of {len(data['episodes'])} in the series "
        f"\"{data['title']}\" ({data.get('audience', '')}).\n\n"
        f"Episodes the viewer has already seen:\n{seen}\n\n"
        f"Still to come (do NOT cover these):\n{later}\n\n"
        f"This episode assumes: {ep.get('assumes') or 'nothing beyond the earlier episodes'}\n"
        f"By the end the viewer can: {ep.get('goal', '')}\n"
        f"It must teach, on camera: {', '.join(ep.get('teaches') or [])}\n\n"
        "Do not re-teach what earlier episodes covered - reach the new material fast. "
        "Anything an earlier episode built that this one needs should be restored in one "
        "command, not retyped. The episode still has to stand on its own for someone who "
        "starts here, so the opening makes clear what we are doing and why."
    )


def start_next(slug: str, episodes_root: Path, n: int | None = None, log=print) -> Path:
    """Turn the next unmade entry into an episode directory with candidate.json."""
    data = load(slug)
    ep = (next((e for e in data["episodes"] if e["n"] == n), None) if n
          else next_entry(data))
    if not ep:
        raise RuntimeError(f"series {slug!r}: every episode is made "
                           f"({len(data['episodes'])} of {len(data['episodes'])})")
    name = episode_name(data, ep)
    d = episodes_root / name
    if (d / "candidate.json").exists():
        raise RuntimeError(f"{d} already exists; `tt run {name} --from rehearse` to redo it")
    d.mkdir(parents=True, exist_ok=True)
    candidate = {
        "repo": data.get("repo", ""),
        "url": data.get("url", ""),
        "tool": data.get("tool") or (data.get("repo", "").split("/")[-1] or data["slug"]),
        "tagline": ep["title"],
        "angle": ep.get("angle", ""),
        "structure": ep.get("structure") or "tour",
        "series": {"slug": data["slug"], "title": data["title"], "n": ep["n"],
                   "of": len(data["episodes"]), "episode_title": ep["title"],
                   "brief": _series_brief(data, ep)},
    }
    if data.get("install_hint"):
        candidate["install_hint"] = data["install_hint"]
    (d / "candidate.json").write_text(json.dumps(candidate, indent=2, ensure_ascii=False))
    ep["episode_dir"] = name
    save(data)
    log(f"  {data['title']} E{ep['n']:02d}: {ep['title']} -> {d}")
    return d


def record_video(slug: str, episode_dir_name: str, url: str) -> None:
    """Remember where an episode was published, for the next one's description."""
    data = load(slug)
    for ep in data["episodes"]:
        if ep.get("episode_dir") == episode_dir_name:
            ep["video"] = url
    save(data)


def table(slug: str) -> str:
    data = load(slug)
    rows = [f"{data['title']}  ({data['slug']}, {len(data['episodes'])} episodes)",
            f"  {data.get('audience', '')}", ""]
    for ep in data["episodes"]:
        state = "published" if ep.get("video") else ("made" if ep.get("episode_dir") else "planned")
        rows.append(f"  E{ep['n']:02d}  {state:<9}  {ep['title']}")
        if ep.get("teaches"):
            rows.append(f"          {', '.join(ep['teaches'])[:90]}")
    return "\n".join(rows)
