"""Which command opens the video. From the screenplay when the rehearsal
chose one (meta.cold_open), else decided once by claude -p from the notes
and the list of commands, cached as cold_open.json (edit it to override,
delete it to regenerate)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import load_prompt
from .plan import extract_json
from .screenplay import LANGUAGES, ColdOpen, Screenplay, clean_text


def _commands(play: Screenplay) -> list[tuple[str, str]]:
    out = []
    for sc in play.scenes:
        for c in sc.commands:
            if c.type:
                out.append((sc.id, c.type))
    return out


def _validate(choice: ColdOpen, play: Screenplay) -> ColdOpen | None:
    """The chosen command must exist in the chosen scene (exact, else a
    unique substring match so a lightly paraphrased answer still resolves)."""
    cmds = [t for s, t in _commands(play) if s == choice.scene]
    if not cmds:
        return None
    if choice.command in cmds:
        return choice
    hits = [t for t in cmds if choice.command.strip() in t or t in choice.command]
    if len(hits) == 1:
        return choice.model_copy(update={"command": hits[0]})
    return None


def load_cold_open(episode_dir: Path, play: Screenplay,
                   outputs: dict[tuple[str, str], str] | None = None, log=None) -> ColdOpen | None:
    """outputs: what each (scene, command) printed in the recording; when
    given, Claude chooses on what the viewer will actually see."""
    tell = log or (lambda m: None)
    if play.meta.cold_open:
        v = _validate(play.meta.cold_open, play)
        if v:
            return v
        tell("screenplay cold_open does not match a typed command; asking Claude")
    cache = episode_dir / "cold_open.json"
    if cache.exists():
        v = _validate(ColdOpen.model_validate(json.loads(cache.read_text())), play)
        if v:
            return v
    notes = episode_dir / "notes.md"
    cmds = _commands(play)
    if not cmds:
        return None
    outputs = outputs or {}
    parts = []
    for s, t in cmds:
        parts.append(f"### scene {s}\n$ {t}")
        if (s, t) in outputs:
            parts.append("output on screen:\n```\n" + (outputs[(s, t)] or "(nothing new)") + "\n```")
    listing = "\n".join(parts)
    template = load_prompt("cold_open.md")
    prompt = (template.replace("{{TOOL}}", play.meta.tool)
              .replace("{{LANGUAGE}}", LANGUAGES.get(play.meta.lang, "English"))
              .replace("{{NOTES}}", notes.read_text()[:12000] if notes.exists() else "(none)")
              .replace("{{COMMANDS}}", listing))
    tell("asking Claude which command's output should open the video")
    p = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        tell(f"claude cold_open failed: {p.stderr[-300:]}")
        return None
    data = extract_json(p.stdout)
    if not data:
        tell("no JSON in the cold_open answer; falling back to the heuristic")
        return None
    try:
        choice = ColdOpen.model_validate(data)
    except Exception:
        return None
    choice.hook = clean_text(choice.hook)
    v = _validate(choice, play)
    if v is None:
        tell(f"cold_open answer names an unknown command ({choice.command!r}); falling back")
        return None
    cache.write_text(json.dumps(v.model_dump(), indent=2, ensure_ascii=False))
    return v
