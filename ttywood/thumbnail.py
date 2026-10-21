"""Thumbnail: a real frame from the episode (the rough-edge scene when there is
one) under big text, 1280x720, saved as thumbnail.png (< 2 MB for YouTube)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .cinema import ACCENT, BG, BRAND, FONT_MONO, FONT_UI, _font, wrap

TW, TH = 1280, 720


def _big_text(episode_dir: Path, fallback: str) -> str:
    yt = episode_dir / "youtube.md"
    if yt.exists():
        m = re.search(r"##\s*Thumbnail.*?\n(.*?)(?:\n##|\Z)", yt.read_text(), re.S | re.I)
        if m:
            for line in m.group(1).splitlines():
                raw = line.strip().replace("**", "")
                if not raw or raw.lower().startswith(("visual", "suggest", "-")):
                    continue
                # "Big text: KILL YOUR NVM" / "Big-text copy: ..." -> after the first colon
                t = raw.split(":", 1)[1] if ":" in raw and "text" in raw.split(":", 1)[0].lower() else raw
                t = t.strip(" *:\"'")
                if 3 <= len(t) <= 32:
                    return t.upper()
    return fallback.upper()


def _frame_at(video: Path, t: float) -> Image.Image:
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True, check=True)
    from io import BytesIO
    return Image.open(BytesIO(p.stdout)).convert("RGB")


def make_thumbnail(episode_dir: Path) -> Path:
    episode_dir = episode_dir.resolve()
    meta = json.loads((episode_dir / "render_meta.json").read_text())
    cand = json.loads((episode_dir / "candidate.json").read_text()) \
        if (episode_dir / "candidate.json").exists() else {}
    tool = cand.get("tool") or episode_dir.name
    scenes = [s for s in meta["timeline"] if s["scene"] not in ("welcome", "signoff")]
    pick = next((s for s in scenes if "rough" in s["scene"] or "edge" in s["scene"]), None) \
        or (scenes[len(scenes) // 2] if scenes else None)
    # sample late in the scene so the output is on screen; skip the chapter card
    t = (pick["start"] + max(3.0, (pick["end"] - pick["start"]) * 0.7)) if pick else 10.0

    frame = _frame_at(episode_dir / "final.mp4", t).resize((TW, TH), Image.LANCZOS)
    # darken + blur the right side a little so the text reads
    frame = frame.filter(ImageFilter.GaussianBlur(1.2))
    overlay = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for x in range(TW):
        a = int(min(200, max(0, (x - TW * 0.42) / (TW * 0.58) * 230)))
        d.line([(x, 0), (x, TH)], fill=(BG[0], BG[1], BG[2], a))
    img = Image.alpha_composite(frame.convert("RGBA"), overlay)
    d = ImageDraw.Draw(img)

    big = _big_text(episode_dir, tool)
    for size in (120, 104, 92, 80, 68, 56):
        f_big = _font(FONT_UI, size)
        lines = wrap(d, big, f_big, 620)
        # a single long word cannot wrap: shrink until it fits the right side
        if len(lines) <= 3 and max(d.textlength(l, font=f_big) for l in lines) <= TW - 640:
            break
    x0 = 620
    y = TH // 2 - len(lines) * int(size * 1.05) // 2 - 20
    for l in lines:
        # thick shadow for legibility on any frame
        for dx, dy in ((4, 4), (-2, 2), (2, -2)):
            d.text((x0 + dx, y + dy), l, font=f_big, fill=(0, 0, 0, 200))
        d.text((x0, y), l, font=f_big, fill=(255, 255, 255, 255))
        y += int(size * 1.05)
    f_tool = _font(FONT_MONO, 44)
    d.rounded_rectangle((x0 - 14, y + 16, x0 + d.textlength(tool, font=f_tool) + 24, y + 80),
                        radius=12, fill=ACCENT + (255,))
    d.text((x0 + 4, y + 22), tool, font=f_tool, fill=(10, 10, 16, 255))
    if BRAND:
        d.text((40, TH - 70), BRAND, font=_font(FONT_MONO, 30), fill=ACCENT + (255,))

    out = episode_dir / "thumbnail.png"
    img.convert("RGB").save(out, optimize=True)
    if out.stat().st_size > 2_000_000:
        img.convert("RGB").save(out.with_suffix(".jpg"), quality=88)
        out = out.with_suffix(".jpg")
    return out
