"""Shorts cut: one scene as a 1080x1920 vertical video (<= 60 s).

Layout: hook/title strip on top, the terminal cropped to its left ~70 columns
(so text stays readable at phone size) or the browser frame, then the
narration beats as large captions below. Same key sounds as the cinematic cut.
"""

from __future__ import annotations

import bisect
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .cinema import (ACCENT, BG, BRAND, FADE_SEC, FONT_MONO, FONT_UI, FPS, MUTED, BodyReader,
                     WebSource, _blend, _dim, _fade, _font, _in_any, humanize, plan_beats,
                     read_time, sentences, wrap, write_sfx_track)
from .screenplay import load_screenplay

SW, SH = 1080, 1920
MAX_SECONDS = 58.0
# Layout A, designed around what YouTube paints over a Short: its title,
# channel row and description cover the bottom ~470 px, the like/comment
# column the right ~200 px. Everything readable lives in SAFE.
SAFE_X0, SAFE_Y0, SAFE_X1, SAFE_Y1 = 80, 200, 1000, 1440
HEADER_H = 150       # episode title (up to two lines) + the small GitHub row
CHIP_H = 132         # the command chip under the header (up to two lines)
CAPTION_H = 210      # the caption panel under the terminal
# the GitHub mark from a Nerd Font (fonts.icons in config), so no logo file
# has to ship with the repo; left out when no Nerd Font is installed
GITHUB_GLYPH = "\uf09b"
TOP_H = 320          # title strip of the legacy crop layout
TERM_COLS = 72       # left columns of the wide take shown (legacy crop path)
CAPTION_TOP = 1180   # captions live below the terminal area
# a Short recorded for the format: a narrow terminal fills the phone frame,
# so nothing is cropped and the text is readable without zooming
SHORT_COLS, SHORT_ROWS = 62, 30
SHORT_VIEW = 0.66    # fraction of the recorded rows shown (the active band)
SHORT_TARGET = 15.0  # aim for a 10-15 s Short: the payoff, not the whole scene
SHORT_MIN = 9.0      # never cut below this
SHORT_FONT = 26      # agg font size for the vertical body


def _nerd_font(size: int):
    from PIL import ImageFont
    from . import config
    for path in config.get("fonts.icons", []) or []:
        if Path(path).expanduser().exists():
            try:
                return ImageFont.truetype(str(Path(path).expanduser()), size)
            except OSError:
                pass
    return None


def _header_image(tool: str, title: str, repo: str) -> Image.Image:
    """The episode title, and under it a small GitHub mark with owner/repo so
    a viewer landing mid-scroll sees this is an open-source project."""
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    width = SAFE_X1 - SAFE_X0
    for size in (54, 48, 42):
        f_title = _font(FONT_UI, size)
        lines = wrap(d, f"{tool}: {title}", f_title, width)
        if len(lines) <= 2:
            break
    y = SAFE_Y0
    for l in lines[:2]:
        d.text(((SW - d.textlength(l, font=f_title)) / 2, y), l, font=f_title,
               fill=(245, 246, 250, 255))
        y += int(size * 1.18)
    if repo:
        f_repo = _font(FONT_UI, 30)
        gf = _nerd_font(32)
        gw = (d.textlength(GITHUB_GLYPH, font=gf) + 12) if gf else 0
        rw = d.textlength(repo, font=f_repo)
        x = (SW - (gw + rw)) / 2
        if gf:
            d.text((x, y + 4), GITHUB_GLYPH, font=gf, fill=MUTED + (255,))
            x += gw
        d.text((x, y + 8), repo, font=f_repo, fill=MUTED + (255,))
    return img


def _title_image(tool: str, title: str) -> Image.Image:
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_small = _font(FONT_MONO, 34)
    f_big = _font(FONT_UI, 64)
    if BRAND:
        d.text(((SW - d.textlength(BRAND, font=f_small)) / 2, 60), BRAND, font=f_small,
               fill=ACCENT + (255,))
    lines = wrap(d, f"{tool}: {title}", f_big, 980)
    y = 112
    for l in lines[:2]:
        d.text(((SW - d.textlength(l, font=f_big)) / 2, y), l, font=f_big, fill=(250, 250, 255, 255))
        y += 72
    return img


def _caption_image(text: str, top: int = CAPTION_TOP, panel: bool = False) -> Image.Image:
    """The narration beat. In layout A it sits in a panel between the terminal
    and the bottom of the safe area, never in YouTube's own UI band."""
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    max_w = (SAFE_X1 - SAFE_X0) - 80 if panel else 940
    room = (SAFE_Y1 - top - 30) if panel else (SH - top - 120)
    for size in (48, 44, 40, 36, 32):
        font = _font(FONT_UI, size)
        lines = wrap(d, text, font, max_w)
        line_h = int(size * 1.28)
        if len(lines) * line_h <= room:
            break
    if panel:
        h = min(SAFE_Y1 - top, len(lines) * line_h + 48)
        d.rounded_rectangle((SAFE_X0, top, SAFE_X1, top + h), radius=22, fill=(22, 24, 34, 235))
        x0, y = SAFE_X0 + 48, top + 24
        d.rounded_rectangle((SAFE_X0 + 18, y + 4, SAFE_X0 + 26, top + h - 20), radius=4,
                            fill=ACCENT + (255,))
    else:
        x0, y = 90, top + 40
        d.rounded_rectangle((x0 - 30, y + 6, x0 - 20, y + len(lines) * line_h - 6), radius=4,
                            fill=ACCENT + (255,))
    for l in lines:
        d.text((x0, y), l, font=font, fill=(240, 240, 246, 255))
        y += line_h
    return img


def _keycap_chip(label: str, top: int) -> Image.Image:
    """Keys as caps, in the chip's place. Nothing a keypress does reaches the
    screen as text, so on a phone this is the only way to see what was hit."""
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    caps = [c for c in label.split("  ") if c]
    inner = (SAFE_X1 - SAFE_X0) - 48
    for size in (40, 35, 30, 26):
        font = _font(FONT_MONO, size)
        pad, gap = int(size * 0.55), int(size * 0.4)
        widths = [d.textlength(c, font=font) + pad * 2 for c in caps]
        if sum(widths) + gap * max(0, len(caps) - 1) <= inner:
            break
    cap_h = int(size * 1.8)
    h = cap_h + 34
    d.rounded_rectangle((SAFE_X0, top, SAFE_X1, top + h), radius=18,
                        fill=(20, 22, 32, 240), outline=ACCENT + (110,), width=2)
    x = SAFE_X0 + (SAFE_X1 - SAFE_X0 - (sum(widths) + gap * max(0, len(caps) - 1))) / 2
    for cap, w in zip(caps, widths):
        y = top + 17
        d.rounded_rectangle((x, y, x + w, y + cap_h), radius=10, fill=(32, 35, 50, 255),
                            outline=(130, 142, 190, 210), width=2)
        d.rounded_rectangle((x + 3, y + cap_h - 8, x + w - 3, y + cap_h - 4), radius=3,
                            fill=(74, 82, 116, 210))
        d.text((x + pad, y + (cap_h - font.size) / 2 - 3), cap, font=font,
               fill=(240, 243, 250, 255))
        x += w + gap
    return img


def _chip_image(text: str, top: int) -> Image.Image:
    """The command being typed, as a chip under the header (layout A)."""
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text = " ".join(l.strip() for l in text.split("\n"))
    inner = (SAFE_X1 - SAFE_X0) - 56
    for size in (34, 30, 27, 24):
        font = _font(FONT_MONO, size)
        lines = wrap(d, "$ " + text, font, inner)
        if len(lines) <= 2:
            break
    lines = lines[:2]
    line_h = int(size * 1.35)
    h = 34 + line_h * len(lines)
    d.rounded_rectangle((SAFE_X0, top, SAFE_X1, top + h), radius=18, fill=(20, 22, 32, 240),
                        outline=ACCENT + (110,), width=2)
    y = top + 16
    for l in lines:
        d.text((SAFE_X0 + 28, y), l, font=font, fill=(255, 208, 120, 255))
        y += line_h
    cw = d.textlength(lines[-1], font=font)
    d.rectangle((SAFE_X0 + 30 + cw, y - line_h + 4, SAFE_X0 + 30 + cw + size * 0.6,
                 y - line_h + 4 + size * 1.05), fill=ACCENT + (255,))
    return img


def _bar_image(text: str) -> Image.Image:
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for size in (34, 30, 26, 22):
        font = _font(FONT_MONO, size)
        if d.textlength(text, font=font) + size <= 960:
            break
    tw = d.textlength(text, font=font)
    bw, bh = int(tw + 60 + size), int(size * 1.4 + 28)
    x0, y0 = (SW - bw) // 2, TOP_H - bh - 10
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=14, fill=(8, 8, 14, 220),
                        outline=ACCENT + (120,), width=2)
    d.text((x0 + 30, y0 + 14), text, font=font, fill=(245, 245, 250, 255))
    d.rectangle((x0 + 34 + tw, y0 + 16, x0 + 34 + tw + size * 0.6, y0 + 16 + size * 1.1),
                fill=ACCENT + (255,))
    return img


def _place(frame: np.ndarray, crop: tuple[int, int, int, int],
           slot: tuple[int, int] | None = None, max_w: int = SW) -> np.ndarray:
    """Crop (x, y, w, h) from a source frame and fit it to the terminal slot
    (top, height) within max_w; the default slot is the legacy fixed band."""
    y0_slot, slot_h = slot or (TOP_H + 10, CAPTION_TOP - TOP_H - 20)
    x, y, w, h = crop
    piece = frame[y:y + h, x:x + w]
    s = min(max_w / w, slot_h / h)
    size = (int(w * s), int(h * s))
    canvas = np.empty((SH, SW, 3), np.uint8)
    canvas[:] = BG
    small = np.asarray(Image.fromarray(piece).resize(size, Image.BILINEAR))
    x0 = (SW - size[0]) // 2
    y0 = y0_slot
    canvas[y0:y0 + size[1], x0:x0 + size[0]] = small
    return canvas


SETUP_WORDS = ("clone", "install", "setup", "download", "binary", "build", "welcome", "intro",
               "compile", "prereq", "launch")
HOOK_WORDS = ("rough", "edge", "gotcha", "break", "fail", "quirk", "surprise", "trap", "footgun",
              "catch", "wow", "magic", "pivot")


def pick_scene(scenes: list[dict], wanted: str | None,
               command_scenes: set[str] | None = None) -> dict:
    """The Short must show the tool in use, never the install. Preference:
    explicit choice (flag or meta.short_scene) -> a hook scene (rough edge,
    surprise) -> the longest scene that isn't setup -> whatever is left.
    When `command_scenes` is given, only scenes that run a typed terminal
    command are eligible (the Short is a terminal recording, so a web-only or
    narration-only scene shows nothing)."""
    if wanted:
        for s in scenes:
            if s["scene"] == wanted:
                return s
        raise ValueError(f"no scene {wanted!r}; have {[s['scene'] for s in scenes]}")
    pool0 = [s for s in scenes if command_scenes is None or s["scene"] in command_scenes]
    pool0 = pool0 or scenes
    usage = [s for s in pool0 if not any(w in s["scene"].lower() for w in SETUP_WORDS)]
    for s in usage:
        if any(w in s["scene"].lower() for w in HOOK_WORDS):
            return s
    pool = usage or pool0
    return max(pool, key=lambda s: s["end"] - s["start"])


def _command_scenes(play) -> set[str]:
    """Scene ids that run at least one typed terminal command (as opposed to
    web-only or narration-only scenes)."""
    return {sc.id for sc in play.scenes
            if any(getattr(c, "type", None) for c in sc.commands)}


def short_scene_id(episode_dir: Path, scene_id: str | None = None) -> str:
    """Which scene the Short shows: an explicit --scene, else meta.short_scene
    when it names a scene that actually runs a command, else the pick_scene
    heuristic. A short_scene that points at a web-only or narration-only scene
    (nothing for a terminal recording to show) is ignored -- the Short is a
    terminal capture, so it needs a scene with a typed command."""
    play = load_screenplay(episode_dir / "screenplay.yaml")
    cmd_scenes = _command_scenes(play)
    if scene_id:
        return scene_id
    meta_path = episode_dir / "render_meta.json"
    scenes = json.loads(meta_path.read_text()).get("body_timeline", []) if meta_path.exists() else []
    if not scenes:
        scenes = [{"scene": sc.id, "start": 0.0, "end": 1.0} for sc in play.scenes]
    wanted = play.meta.short_scene
    if wanted and wanted in cmd_scenes:
        return wanted
    return pick_scene(scenes, None, cmd_scenes)["scene"]


_SPLIT_FLIP = {"right": "down", "left": "up", "down": "down", "up": "up"}
_FOCUS_FLIP = {"left": "up", "right": "down", "up": "up", "down": "down"}


def _stack_panes(play) -> None:
    """Side-by-side panes are wrong for a phone: at {SHORT_COLS} columns each
    half is ~31 wide, every line wraps, and the second pane's few lines sit at
    the top of the screen where the active-band crop cuts them off — the
    viewer sees one busy pane and one empty box. Stacked panes keep both full
    width and put the second one at the bottom, inside the band.""".replace(
        "{SHORT_COLS}", str(SHORT_COLS))
    for scene in play.scenes:
        for cmd in scene.commands or []:
            if not cmd.zellij:
                continue
            head, *rest = cmd.zellij.split()
            flip = {"new-pane": _SPLIT_FLIP, "move-focus": _FOCUS_FLIP}.get(head)
            if flip:
                cmd.zellij = " ".join([head] + [flip.get(w, w) for w in rest])


def record_short(episode_dir: Path, scene_id: str | None = None,
                 cols: int = SHORT_COLS, rows: int = SHORT_ROWS, on_progress=None) -> Path:
    """Record a take made for the vertical format: the same screenplay up to
    and including the Short's scene, replayed in a narrow terminal so the
    phone frame shows whole lines instead of a crop of a 120-column session.
    Writes short.cast + short_timeline.json beside the main take."""
    from .record import record_episode
    episode_dir = episode_dir.resolve()
    play = load_screenplay(episode_dir / "screenplay.yaml")
    target = short_scene_id(episode_dir, scene_id)
    ids = [sc.id for sc in play.scenes]
    if target not in ids:
        raise ValueError(f"no scene {target!r}; have {ids}")
    trimmed = play.model_copy(deep=True)
    trimmed.scenes = play.scenes[:ids.index(target) + 1]
    trimmed.meta.terminal.cols, trimmed.meta.terminal.rows = cols, rows
    _stack_panes(trimmed)
    return record_episode(episode_dir, play=trimmed, cast_name="short.cast",
                          timeline_name="short_timeline.json", tag="short",
                          on_progress=on_progress)


def render_short_body(episode_dir: Path, font_size: int = SHORT_FONT) -> tuple[Path, dict]:
    """Time-warp short.cast (pauses at 1x, waits fast-forwarded) and render it
    to short_body.mp4 with agg. Returns the body path and the warp data."""
    from .render import FAST_FACTOR, IDLE_LIMIT, WAIT_MAX, TimeWarp, typing_spans, warp_typed, _sh
    episode_dir = episode_dir.resolve()
    tl = json.loads((episode_dir / "short_timeline.json").read_text())
    timeline, protected, waits = tl["scenes"], tl.get("protected", []), tl.get("waits", [])
    lines = (episode_dir / "short.cast").read_text().splitlines()
    header, events = lines[0], [json.loads(l) for l in lines[1:] if l.strip()]
    fast = typing_spans([ev[0] for ev in events], protected, waits)
    warp = TimeWarp(protected, waits, FAST_FACTOR, WAIT_MAX, IDLE_LIMIT, 1.0, fast)
    warp.build([ev[0] for ev in events])
    new_times = [warp.map(ev[0]) for ev in events]
    scenes = [{**sc, "start": warp.map(sc["start"]), "end": warp.map(sc["end"])} for sc in timeline]
    warped = episode_dir / "short_idle.cast"
    warped.write_text(header + "\n" + "\n".join(
        json.dumps([round(t, 6)] + ev[1:]) for t, ev in zip(new_times, events)) + "\n")
    gif = episode_dir / "short_body.gif"
    _sh(["agg", "--font-size", str(font_size), "--fps-cap", "30", "--idle-time-limit", "100000",
         "--last-frame-duration", "1.0", "--theme", "dracula", str(warped), str(gif)])
    body = episode_dir / "short_body.mp4"
    _sh(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(gif), "-vf", "fps=30",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast", str(body)])
    data = {"scenes": scenes,
            "protected": [[round(warp.map(a), 3), round(warp.map(b), 3)] for a, b in protected],
            "waits": [[round(warp.map(a), 3), round(warp.map(b), 3)] for a, b in waits],
            "fast_typing": [[round(warp.map(a), 3), round(warp.map(b), 3)] for a, b, _ in fast],
            "keys": [[round(warp.map(a), 3), round(warp.map(b), 3), lbl]
                     for a, b, lbl in tl.get("keys", [])],
            "typed": warp_typed(tl.get("typed", []), warp)}
    (episode_dir / "short_warp.json").write_text(json.dumps(data, indent=2))
    return body, data


def render_short(episode_dir: Path, scene_id: str | None = None, sfx: bool = True,
                 native: bool | None = None) -> Path:
    """Vertical cut of one scene. `native` uses the dedicated narrow-terminal
    take (short.cast, from `record_short`), where the whole terminal fits the
    phone frame; without it the wide take is cropped, which is what the older
    Shorts did. Default: the native take when it exists."""
    episode_dir = episode_dir.resolve()
    play = load_screenplay(episode_dir / "screenplay.yaml")
    if native is None:
        native = (episode_dir / "short.cast").exists()
    narr = {s.id: s.narration for s in play.scenes}

    wide = False
    if native:
        if not (episode_dir / "short_body.mp4").exists() or \
                (episode_dir / "short.cast").stat().st_mtime > (episode_dir / "short_body.mp4").stat().st_mtime:
            render_short_body(episode_dir)
        warp = json.loads((episode_dir / "short_warp.json").read_text())
        wanted = short_scene_id(episode_dir, scene_id)
        matches = [s_ for s_ in warp["scenes"] if s_["scene"] == wanted]
        if not matches:
            raise ValueError(f"scene {wanted!r} is not in the Short take; re-run `ttywood shorts --record`")
        sc = matches[-1]
        cols, rows = SHORT_COLS, SHORT_ROWS
        body = BodyReader(episode_dir / "short_body.mp4")
        bw, bh = Image.open(episode_dir / "short_body.gif").size
        # the shell scrolls, so the action is always in the lower rows: show
        # that band full width instead of letterboxing the whole screen
        keep = int(bh * SHORT_VIEW)
        term_crop = (0, bh - keep, bw, keep)
        cast_for_cursor = episode_dir / "short_idle.cast"
        web = None
        # layout A: header, command chip, terminal, caption, all inside the
        # safe area (YouTube paints its own UI over the rest of the frame)
        chip_top = SAFE_Y0 + HEADER_H + 22
        term_top = chip_top + CHIP_H + 20
        term_room = (SAFE_Y1 - CAPTION_H - 16) - term_top
        fit = min((SAFE_X1 - SAFE_X0) / bw, term_room / bh)
        term_slot = (term_top, int(bh * fit))
        caption_top = term_top + int(bh * fit) + 16
    else:
        meta = json.loads((episode_dir / "render_meta.json").read_text())
        warp = json.loads((episode_dir / "warp.json").read_text())
        body_scenes = meta.get("body_timeline") or []
        if not body_scenes:
            raise RuntimeError("run `ttywood cinema` first (it records body-relative scene times)")
        sc = pick_scene(body_scenes, short_scene_id(episode_dir, scene_id))
        cols, rows = play.meta.terminal.cols, play.meta.terminal.rows
        gw, gh = Image.open(episode_dir / "demo.gif").size
        scale = min(1920 / gw, 1080 / gh)
        offx, offy = (1920 - gw * scale) / 2, (1080 - gh * scale) / 2
        cell_w = gw * scale / cols
        # A full-screen TUI draws to every column: its status bar, its pane
        # borders and its right-hand pane are the subject, so keeping the left
        # 72 columns cuts the demo in half. Fit the whole width instead, and
        # use the same layout as the native take.
        from .record import _host_for
        wide = _host_for(play) == "tmux"
        term_crop = ((int(offx), int(offy), int(gw * scale), int(gh * scale)) if wide else
                     (int(offx), int(offy), int(TERM_COLS * cell_w), int(gh * scale)))
        body = BodyReader(episode_dir / "demo_body.mp4")
        cast_for_cursor = episode_dir / "demo_idle.cast"
        web = WebSource(episode_dir, warp["web"]) if warp.get("web") else None
        if wide:  # layout A, sized to the full-width terminal
            chip_top = SAFE_Y0 + HEADER_H + 22
            term_top = chip_top + CHIP_H + 20
            term_room = (SAFE_Y1 - CAPTION_H - 16) - term_top
            cw, ch = term_crop[2], term_crop[3]
            fit = min((SAFE_X1 - SAFE_X0) / cw, term_room / ch)
            term_slot = (term_top, int(ch * fit))
            caption_top = term_top + int(ch * fit) + 16
        else:
            term_slot, caption_top, chip_top = None, CAPTION_TOP, TOP_H
    text = narr.get(sc["scene"], "")

    from .cinema import _keycast_lookup, _typed_lookup
    keycast = _keycast_lookup(warp)
    typed_at = _typed_lookup(warp) if warp.get("typed") else None
    bar_typed_idx = -1
    keylabels = {lbl for _a, _b, lbl in warp.get("keys", [])}
    typing = lambda t: not _in_any(t, warp["protected"]) and not _in_any(t, warp["waits"])
    waiting = lambda t: _in_any(t, warp["waits"])

    scene_len = max(sc["end"] - sc["start"], 1.0 / FPS)
    if native and scene_len > SHORT_TARGET:
        # keep the end of the scene (where the result lands) and start at the
        # last command that still leaves a watchable clip
        from .cinema import command_anchors
        anchors = [a for a in command_anchors(warp, sc["start"], sc["end"])
                   if sc["end"] - a >= SHORT_MIN]
        new_start = max(anchors[-1] if anchors else sc["end"] - SHORT_TARGET,
                        sc["end"] - SHORT_TARGET)
        sc = {**sc, "start": new_start}
        scene_len = sc["end"] - sc["start"]
    total = min(MAX_SECONDS, max(scene_len, sum(read_time(b) for b in sentences(text))))
    if native:
        total = min(total, SHORT_TARGET + 4)
    from .cinema import command_anchors
    beats = plan_beats(text, total, command_anchors(warp, sc["start"], sc["start"] + scene_len),
                       make_image=lambda t: _caption_image(t, caption_top, panel=native))
    if native:
        title = _header_image(play.meta.tool, humanize(sc["scene"]), play.meta.repo)
    else:
        title = _title_image(play.meta.tool, humanize(sc["scene"]))

    # command text for the bar, same reconstruction as cinema (a long command
    # wraps in the terminal, and the wrapped row carries no prompt)
    from .cinema import cursor_track, _PROMPT_RE, WEB_PROMPT, BAR_LINGER
    cblocks: list[str] = []
    typed_commands = [c.type for s_ in play.scenes for c in s_.commands if c.type]
    ctimes, _, clines = cursor_track(cast_for_cursor, cols, rows,
                                     blocks=cblocks, known=typed_commands)
    cursor_at = lambda t: max(bisect.bisect_right(ctimes, t) - 1, 0)

    out_path = episode_dir / "short.mp4"
    video_only = episode_dir / "short_video.mp4"
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{SW}x{SH}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
         "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(video_only)],
        stdin=subprocess.PIPE)
    write = lambda fr: proc.stdin.write(np.ascontiguousarray(fr).tobytes())  # type: ignore[union-attr]

    n_frames = int(round(total * FPS))
    bar_text, bar_until, bar_shown_at = "", -1.0, 0.0
    bar_cache: dict[str, Image.Image] = {}
    last_web = None
    sfx_events: list[tuple[float, str]] = []
    if sfx:
        lo, hi = bisect.bisect_left(ctimes, sc["start"]), bisect.bisect_left(ctimes, sc["start"] + total)
        last = -1.0
        for idx in range(lo, hi):
            tb = ctimes[idx]
            if typing(tb) and tb - last > 0.03:
                sfx_events.append((tb - sc["start"], "tick"))
                last = tb
        for a, b in warp["waits"]:
            if sc["start"] <= a < sc["start"] + total:
                sfx_events.append((a - sc["start"], "enter"))
    for k in range(n_frames):
        tau = k / FPS
        live = tau < scene_len
        t_body = sc["start"] + min(tau, scene_len - 1.0 / FPS)
        web_frame = web.frame(t_body) if web else None
        if web_frame is not None:
            last_web = web_frame
        elif last_web is not None and not (typing(t_body) or waiting(t_body)):
            web_frame = last_web
        else:
            last_web = None
        if web_frame is not None:
            fr = _place(web_frame, (0, 0, web_frame.shape[1], web_frame.shape[0]))
            lbl = web.label(t_body) if web else None
            if lbl:
                if not bar_text.endswith(lbl):
                    bar_shown_at = tau
                bar_text, bar_until = WEB_PROMPT + lbl, tau + BAR_LINGER
        else:
            fr = _place(body.get(int(round(t_body * FPS))), term_crop, term_slot,
                        max_w=(SAFE_X1 - SAFE_X0) if native else SW)
            keys_now = keycast(t_body)
            if keys_now:
                if bar_text != keys_now:
                    bar_shown_at, bar_text = tau, keys_now
                bar_until = tau + BAR_LINGER
            elif live and typed_at:
                # the recorder's typed log: exactly what has been typed so far
                hit = typed_at(t_body)
                if hit:
                    ti, prefix, hold = hit
                    if bar_typed_idx != ti:
                        bar_shown_at, bar_typed_idx = tau, ti
                    # the Short's bar shows the command without the prompt
                    flat = " ".join(l.strip() for l in prefix.split("\n") if l.strip())
                    if flat:
                        bar_text = flat
                        bar_until = tau + min(BAR_LINGER, max(hold - t_body, 0.0))
            elif live and (typing(t_body) or waiting(t_body)):
                idx = cursor_at(t_body)
                block = cblocks[idx] if cblocks else (clines[idx] if clines else "")
                m = _PROMPT_RE.match(block.split("\n")[0] if block else "")
                # same as the episode bar: the last keystroke lands in the
                # protected beat before Enter, so gating on typing() alone
                # cut the final character off the chip
                full = " ".join(l.strip() for l in
                                [m.group(2)] + block.split("\n")[1:]) if m and m.group(2) else ""
                extends = bool(bar_text) and full.startswith(bar_text)
                if (typing(t_body) or extends) and m and m.group(2):
                    if not bar_text:
                        bar_shown_at = tau
                    # the Short's bar shows the command without the prompt
                    bar_text = " ".join(l.strip() for l in
                                        [m.group(2)] + block.split("\n")[1:])
                bar_until = tau + BAR_LINGER
        fr = _blend(fr, title, 1.0)
        if bar_text and tau < bar_until:
            if bar_text not in bar_cache:
                bar_cache[bar_text] = (
                    _keycap_chip(bar_text, chip_top if (native or wide) else TOP_H)
                    if bar_text in keylabels else
                    _chip_image(bar_text, chip_top) if (native or wide) else _bar_image(bar_text))
            a = min(1.0, (tau - bar_shown_at) / FADE_SEC, (bar_until - tau) / FADE_SEC)
            fr = _blend(fr, bar_cache[bar_text], max(a, 0.0))
        elif bar_text and tau >= bar_until:
            bar_text = ""
        for b in beats:
            if b.start <= tau < b.end:
                k0 = int((tau - b.start) * FPS)
                nb = int((b.end - b.start) * FPS)
                fr = _blend(fr, b.img, _fade(k0, max(nb, 1)))
                break
        write(_dim(fr, _fade(k, n_frames)))
    proc.stdin.close()  # type: ignore[union-attr]
    proc.wait()
    body.close()
    if web:
        web.close()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed while writing the short")

    if sfx and sfx_events:
        sfx_path = episode_dir / "short_sfx.wav"
        write_sfx_track(sfx_path, sfx_events, total)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video_only), "-i", str(sfx_path),
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                        "-shortest", str(out_path)], check=True)
        video_only.unlink()
    else:
        video_only.replace(out_path)
    return out_path
