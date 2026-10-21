"""Verdict data for the closing cards: from the screenplay when the rehearsal
wrote one, else derived once from notes.md via claude -p and cached as
verdict.json (edit that file to tweak the cards, delete it to regenerate)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .config import load_prompt
from .screenplay import LANGUAGES, Screenplay, Verdict


def load_verdict(episode_dir: Path, play: Screenplay) -> Verdict | None:
    if play.verdict:
        return play.verdict
    cache = episode_dir / "verdict.json"
    if cache.exists():
        return Verdict.model_validate(json.loads(cache.read_text()))
    notes = episode_dir / "notes.md"
    if not notes.exists():
        return None
    template = load_prompt("verdict.md")
    prompt = (template.replace("{{TOOL}}", play.meta.tool)
              .replace("{{LANGUAGE}}", LANGUAGES.get(play.meta.lang, "English"))
              .replace("{{NOTES}}", notes.read_text()[:12000]))
    p = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"claude verdict failed: {p.stderr[-1200:]}")
    m = re.search(r"\{.*\}", p.stdout, re.S)
    if not m:
        raise RuntimeError(f"no JSON in verdict output:\n{p.stdout[-1200:]}")
    v = Verdict.model_validate(json.loads(m.group(0)))
    cache.write_text(json.dumps(v.model_dump(), indent=2, ensure_ascii=False))
    return v
