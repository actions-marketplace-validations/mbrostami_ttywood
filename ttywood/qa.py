"""Quality gate before publishing: hard checks first (cheap, deterministic),
then a Claude review of evenly sampled frames. Writes qa.md; returns pass/fail."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

import subprocess

from .config import load_prompt
from .llm import run as llm_run

MIN_SECONDS, MAX_SECONDS = 45, 20 * 60
BLACK_MEAN = 18.0  # mean pixel value under which a frame counts as black


def _probe(video: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height:format=duration", "-of", "json", str(video)],
                       capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def _sample_frames(video: Path, out_dir: Path, every: float) -> list[Path]:
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("qa_*.jpg"):
        old.unlink()
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video), "-vf",
                    f"fps=1/{every},scale=960:-1", str(out_dir / "qa_%03d.jpg")], check=True)
    return sorted(out_dir.glob("qa_*.jpg"))


def hard_checks(episode_dir: Path) -> list[str]:
    problems: list[str] = []
    video = episode_dir / "final.mp4"
    if not video.exists():
        return ["final.mp4 is missing"]
    info = _probe(video)
    dur = float(info["format"]["duration"])
    kinds = {s["codec_type"] for s in info["streams"]}
    if not MIN_SECONDS <= dur <= MAX_SECONDS:
        problems.append(f"duration {dur:.0f}s outside {MIN_SECONDS}-{MAX_SECONDS}s")
    meta = json.loads((episode_dir / "render_meta.json").read_text())
    if (meta.get("voice") or meta.get("sfx")) and "audio" not in kinds:
        problems.append("expected an audio stream but final.mp4 has none")
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    if (v["width"], v["height"]) not in ((1920, 1080), (1080, 1920)):
        problems.append(f"unexpected resolution {v['width']}x{v['height']}")
    for f in ("narration.md", "narration.srt", "youtube.md"):
        if not (episode_dir / f).exists():
            problems.append(f"{f} missing")
    if not (episode_dir / "thumbnail.png").exists() and not (episode_dir / "thumbnail.jpg").exists():
        problems.append("thumbnail missing (run `ttywood thumbnail`)")
    # black-frame scan every 2 s
    frames = _sample_frames(video, episode_dir / "qa", every=2.0)
    black_run, worst = 0, 0
    for fr in frames:
        mean = float(np.asarray(Image.open(fr).convert("L")).mean())
        black_run = black_run + 1 if mean < BLACK_MEAN else 0
        worst = max(worst, black_run)
    if worst >= 3:
        problems.append(f"{worst * 2}s of near-black frames in a row")
    for old in frames:
        old.unlink()
    return problems


def claude_review(episode_dir: Path) -> tuple[bool, str]:
    frames = _sample_frames(episode_dir / "final.mp4", episode_dir / "qa", every=20.0)
    cand = json.loads((episode_dir / "candidate.json").read_text()) \
        if (episode_dir / "candidate.json").exists() else {}
    template = load_prompt("qa.md")
    narration = (episode_dir / "narration.md").read_text()[:6000] \
        if (episode_dir / "narration.md").exists() else "(none)"
    prompt = (template.replace("{{TOOL}}", cand.get("tool", episode_dir.name))
              .replace("{{FRAMES}}", "\n".join(str(f) for f in frames))
              .replace("{{NARRATION}}", narration))
    report = llm_run(prompt, allowed_tools="Read", timeout=900, cwd=episode_dir)
    ok = bool(re.search(r"Verdict:\s*PASS", report, re.I))
    return ok, report


def run_qa(episode_dir: Path, review: bool = True, log=None) -> tuple[bool, Path]:
    episode_dir = episode_dir.resolve()
    tell = log or (lambda text: None)
    tell("hard checks: duration, streams, resolution, artifacts, black frames")
    problems = hard_checks(episode_dir)
    for p in problems:
        tell(f"hard check failed: {p}")
    if review and not problems:
        tell("LLM reviews a frame every 20 s (2-4 min)")
    lines = ["# QA report", ""]
    if problems:
        lines += ["## Hard checks: FAIL", ""] + [f"- {p}" for p in problems] + [""]
    else:
        lines += ["## Hard checks: PASS", ""]
    ok = not problems
    if review and not problems:
        passed, report = claude_review(episode_dir)
        ok = ok and passed
        lines += ["## Frame review", "", report, ""]
    out = episode_dir / "qa.md"
    out.write_text("\n".join(lines))
    return ok, out
