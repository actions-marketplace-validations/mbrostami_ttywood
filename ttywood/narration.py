"""Turn the rendered timeline into a read-along narration script + subtitles."""

from __future__ import annotations

import json
from pathlib import Path


def _mmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _srt_ts(t: float) -> str:
    ms = int(round((t - int(t)) * 1000))
    s = int(t)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d},{ms:03d}"


def write_narration(episode_dir: Path) -> tuple[Path, Path]:
    episode_dir = episode_dir.resolve()
    meta = json.loads((episode_dir / "render_meta.json").read_text())
    timeline = meta["timeline"]
    # narration text comes from the screenplay, timestamps from the render;
    # the recording's copy of the text may be stale
    play_path = episode_dir / "screenplay.yaml"
    if play_path.exists():
        from .screenplay import load_screenplay
        by_id = {s.id: s.narration for s in load_screenplay(play_path).scenes}
        for sc in timeline:
            sc["narration"] = by_id.get(sc["scene"], sc["narration"])

    from .screenplay import clean_text
    for sc in timeline:
        sc["narration"] = clean_text(sc["narration"])

    md = ["# Narration script", "",
          "Read each block when the video reaches its timestamp.", ""]
    srt: list[str] = []
    for i, sc in enumerate(timeline):
        md += [f"## [{_mmss(sc['start'])}] {sc['scene']}", "", sc["narration"], ""]
        end = timeline[i + 1]["start"] if i + 1 < len(timeline) else sc["end"]
        srt += [str(i + 1), f"{_srt_ts(sc['start'])} --> {_srt_ts(end)}",
                sc["narration"], ""]

    md_path = episode_dir / "narration.md"
    srt_path = episode_dir / "narration.srt"
    md_path.write_text("\n".join(md))
    srt_path.write_text("\n".join(srt))
    return md_path, srt_path
