"""Render pass.

1. Time-warp the cast: typing and deliberate reading pauses play at 1x,
   waiting time (clone/install/output scroll) is fast-forwarded and capped,
   and any other idle gap is capped. Scene timestamps are remapped through
   the same warp.
2. If a voice track exists (voice/manifest.json), hold the last frame of any
   scene whose narration clip outlasts it, so clips never overlap.
3. agg -> 1080p mp4 with intro/outro cards; mux the voice clips at their
   scene timestamps.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

IDLE_LIMIT = 3.5        # cap for gaps that are neither typing, pause nor wait
FAST_FACTOR = 6.0       # waits play at this speed...
WAIT_MAX = 6.0          # ...and never longer than this on screen
TYPING_MAX = 6.0        # a single command's typing never takes longer than this on screen:
TYPING_KEEP = 2.0       # ...the first seconds play at 1x, the rest is fast-forwarded
VOICE_MARGIN = 0.8      # silence after a clip before the next scene starts
VOICE_PAUSE_SCALE = 0.4 # reading pauses shrink to this when a voice track drives pacing
INTRO_SECONDS = 3.5
OUTRO_SECONDS = 4.0
CARD_BG = "0x14141c"


def _sh(cmd: list[str]):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _font() -> str:
    from . import config
    f = config.first_existing("fonts.mono") or config.first_existing("fonts.ui")
    if not f:
        raise FileNotFoundError("no card font found: set fonts.mono in your ttywood config "
                                "(Linux: apt install fonts-dejavu-core)")
    return f


# ---- time warp -------------------------------------------------------------

class TimeWarp:
    """Piecewise-linear map from original cast time to output time, built from
    typed intervals: protected (1x), waits (fast-forwarded), other (idle-capped)."""

    def __init__(self, protected: list[list[float]], waits: list[list[float]],
                 fast_factor: float = FAST_FACTOR, wait_max: float = WAIT_MAX,
                 idle_limit: float = IDLE_LIMIT, pause_scale: float = 1.0,
                 fast: list[tuple[float, float, float]] | None = None):
        self.protected = sorted(protected)
        self.pause_scale = pause_scale
        # per-wait scale so a wait shows at 1/fast_factor speed, capped at wait_max
        self.waits = []
        for a, b in sorted(waits):
            length = max(b - a, 1e-6)
            scale = min(1.0 / fast_factor, wait_max / length)
            self.waits.append((a, b, scale))
        self.fast = sorted(fast or [])  # (a, b, scale): long typing, fast-forwarded
        self.idle_limit = idle_limit
        self.pieces: list[tuple[float, float]] = [(0.0, 0.0)]

    def _kind(self, t: float) -> tuple[str, float]:
        for a, b in self.protected:
            if a <= t < b:
                return "protected", self.pause_scale
        for a, b, s in self.waits:
            if a <= t < b:
                return "wait", s
        for a, b, s in self.fast:
            if a <= t < b:
                return "fast", s
        return "other", 1.0

    def _boundaries(self, a: float, b: float) -> list[float]:
        pts = {a, b}
        for x, y in self.protected:
            pts.update(p for p in (x, y) if a < p < b)
        for x, y, _ in self.waits:
            pts.update(p for p in (x, y) if a < p < b)
        for x, y, _ in self.fast:
            pts.update(p for p in (x, y) if a < p < b)
        return sorted(pts)

    def gap(self, a: float, b: float) -> float:
        """Output duration of the original interval [a, b]."""
        out, other = 0.0, 0.0
        pts = self._boundaries(a, b)
        for x, y in zip(pts, pts[1:]):
            kind, scale = self._kind(x)
            if kind == "other":
                other += y - x
            else:
                out += (y - x) * scale
        return out + min(other, self.idle_limit)

    def build(self, event_times: list[float]):
        prev_t, new_t = 0.0, 0.0
        for t in event_times:
            new_t += self.gap(prev_t, t)
            prev_t = t
            self.pieces.append((t, new_t))

    def map(self, t: float) -> float:
        lo, hi = 0, len(self.pieces) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.pieces[mid][0] <= t:
                lo = mid
            else:
                hi = mid - 1
        orig, comp = self.pieces[lo]
        if lo == len(self.pieces) - 1:
            return comp + max(0.0, t - orig)
        return min(comp + max(0.0, t - orig), self.pieces[lo + 1][1])


def warp_typed(typed: list[dict], warp: "TimeWarp") -> list[dict]:
    """The recorder's typed-text log mapped into body time: every character's
    landing time, the Enter, and the step's span, so the compositor can show
    exactly the prefix that has been typed at any frame."""
    out = []
    for e in typed:
        out.append({**e,
                    "start": round(warp.map(e["start"]), 3),
                    "end": round(warp.map(e["end"]), 3),
                    "enter": round(warp.map(e["enter"]), 3) if e.get("enter") is not None else None,
                    "chars": [round(warp.map(c), 3) for c in e.get("chars", [])]})
    return out


def typing_spans(event_times: list[float], protected: list[list[float]],
                 waits: list[list[float]], typing_max: float = TYPING_MAX,
                 keep: float = TYPING_KEEP) -> list[tuple[float, float, float]]:
    """Long stretches of typing (dense cast events outside pauses and waits,
    e.g. a file typed through a heredoc) are boring to watch: keep the first
    `keep` seconds at 1x and fast-forward the rest so the whole command shows
    in `typing_max` seconds. Returns (start, end, scale) intervals."""
    busy = sorted([tuple(p) for p in protected] + [tuple(w) for w in waits])
    inside = lambda t: any(a <= t < b for a, b in busy)
    spans: list[tuple[float, float, int]] = []
    cur_a, cur_b, n = None, None, 0
    for t in event_times:
        if inside(t):
            continue
        # same span only if no pause/wait started in between
        contiguous = cur_b is not None and t - cur_b < 1.0 and not any(
            cur_b < a < t for a, _ in busy)
        if contiguous:
            cur_b, n = t, n + 1
        else:
            if cur_a is not None:
                spans.append((cur_a, cur_b, n))
            cur_a, cur_b, n = t, t, 1
    if cur_a is not None:
        spans.append((cur_a, cur_b, n))
    out = []
    for a, b, n in spans:
        if b - a > typing_max and n >= 25:
            out.append((a + keep, b, (typing_max - keep) / (b - a - keep)))
    return out


def _extend_scenes(new_times: list[float], timeline: list[dict],
                   clips: dict[str, float]) -> list[float]:
    """Hold the last frame at the end of any scene shorter than its voice
    clip. Mutates timeline in place, returns shifted event times."""
    shift, extensions = 0.0, []  # extensions keyed by ORIGINAL (unshifted) time
    for sc in timeline:
        orig_end = sc["end"]
        sc["start"] += shift
        sc["end"] += shift
        need = clips.get(sc["scene"], 0.0) + VOICE_MARGIN
        length = sc["end"] - sc["start"]
        if need > length:
            extra = need - length
            extensions.append((orig_end, extra))
            sc["end"] += extra
            shift += extra
    out = []
    for t in new_times:
        added = sum(extra for at, extra in extensions if t >= at)
        out.append(t + added)
    return out


# ---- cards + mux -----------------------------------------------------------

def _card(path: Path, seconds: float, lines: list[tuple[str, int]]):
    """Solid-color card with centered text lines: [(text, fontsize), ...].
    Drawn with Pillow (ffmpeg's drawtext filter isn't in every build)."""
    from PIL import Image, ImageDraw, ImageFont

    font_path = _font()
    img = Image.new("RGB", (1920, 1080), CARD_BG.replace("0x", "#"))
    draw = ImageDraw.Draw(img)
    n = len(lines)
    for i, (text, size) in enumerate(lines):
        if not text:
            continue
        font = ImageFont.truetype(font_path, size)
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        w, h = x1 - x0, y1 - y0
        draw.text(((1920 - w) / 2 - x0, 540 + (i - (n - 1) / 2) * 120 - h / 2 - y0),
                  text, font=font, fill="white")
    png = path.with_suffix(".png")
    img.save(png)
    _sh(["ffmpeg", "-nostdin", "-y", "-loop", "1", "-framerate", "30", "-i", str(png),
         "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264",
         "-preset", "fast", str(path)])


def _mux_voice(video: Path, out: Path, timeline: list[dict],
               voice_dir: Path, clips: list[dict]):
    delays = {c["scene"]: c["file"] for c in clips}
    inputs, filters, labels = ["-i", str(video)], [], []
    idx = 1
    for sc in timeline:
        if sc["scene"] not in delays:
            continue
        inputs += ["-i", str(voice_dir / delays[sc["scene"]])]
        ms = int(sc["start"] * 1000)
        filters.append(f"[{idx}]adelay={ms}|{ms}[a{idx}]")
        labels.append(f"[a{idx}]")
        idx += 1
    if not labels:
        video.replace(out)
        return
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:"
                   f"dropout_transition=0,apad[a]")
    _sh(["ffmpeg", "-nostdin", "-y", *inputs, "-filter_complex", ";".join(filters),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
         "-b:a", "160k", "-shortest", str(out)])


# ---- main ------------------------------------------------------------------

def render_episode(episode_dir: Path, font_size: int = 20,
                   idle_limit: float = IDLE_LIMIT, fast_factor: float = FAST_FACTOR,
                   wait_max: float = WAIT_MAX, use_voice: bool | None = None,
                   pause_scale: float | None = None, body_only: bool = False) -> Path:
    """body_only: stop after demo_body.mp4 + warp.json + render_meta.json (the
    cinematic compositor draws its own cards and never reads final.mp4)."""
    episode_dir = episode_dir.resolve()
    from .screenplay import bumpers, load_screenplay

    manifest = episode_dir / "voice" / "manifest.json"
    if use_voice is None:
        use_voice = manifest.exists()
    play_path = episode_dir / "screenplay.yaml"
    welcome_text, signoff_text = bumpers(load_screenplay(play_path)) if play_path.exists() \
        else ("", "")
    if pause_scale is None:
        # with a voice track the clip length drives each scene, so the
        # screenplay's reading pauses can be much shorter
        pause_scale = VOICE_PAUSE_SCALE if use_voice else 1.0
    tl = json.loads((episode_dir / "timeline.json").read_text())
    timeline = tl["scenes"] if isinstance(tl, dict) else tl
    protected = tl.get("protected", []) if isinstance(tl, dict) else []
    waits = tl.get("waits", []) if isinstance(tl, dict) else []
    keycasts = tl.get("keys", []) if isinstance(tl, dict) else []
    typed = tl.get("typed", []) if isinstance(tl, dict) else []

    lines = (episode_dir / "demo.cast").read_text().splitlines()
    header = lines[0]
    events = [json.loads(l) for l in lines[1:] if l.strip()]
    if not waits:
        # older recordings: treat any unprotected gap over a second as a wait
        prev = 0.0
        for ev in events:
            if ev[0] - prev > 1.0 and not any(a <= prev < b for a, b in protected):
                waits.append([prev, ev[0]])
            prev = ev[0]

    fast = typing_spans([ev[0] for ev in events], protected, waits)
    warp = TimeWarp(protected, waits, fast_factor, wait_max, idle_limit, pause_scale, fast)
    warp.build([ev[0] for ev in events])
    new_times = [warp.map(ev[0]) for ev in events]
    for sc in timeline:
        sc["start"] = warp.map(sc["start"])
        sc["end"] = warp.map(sc["end"])

    clips: list[dict] = []
    intro_seconds, outro_seconds = INTRO_SECONDS, OUTRO_SECONDS
    if use_voice:
        if not manifest.exists():
            raise FileNotFoundError("no voice track: run `ttywood voice <episode>` first")
        clips = json.loads(manifest.read_text())["clips"]
        durations = {c["scene"]: c["duration"] for c in clips}
        new_times = _extend_scenes(new_times, timeline, durations)
        # the cards stretch to fit the welcome (from 0:00) and the sign-off
        intro_seconds = max(INTRO_SECONDS, durations.get("welcome", 0) + VOICE_MARGIN)
        outro_seconds = max(OUTRO_SECONDS, durations.get("signoff", 0) + VOICE_MARGIN)

    # the video must last until the final scene ends (which may be later than
    # the last terminal event when a voice clip holds the frame)
    tail = max(new_times[-1], max(sc["end"] for sc in timeline)) + 0.5
    last_frame = max(0.5, tail - new_times[-1])
    warped = episode_dir / "demo_idle.cast"
    warped.write_text(header + "\n" + "\n".join(
        json.dumps([round(t, 6)] + ev[1:]) for t, ev in zip(new_times, events)) + "\n")
    # warped pause/wait intervals in body time, for the cinematic compositor
    warp_out = {
        "protected": [[round(warp.map(a), 3), round(warp.map(b), 3)] for a, b in protected],
        "waits": [[round(warp.map(a), 3), round(warp.map(b), 3)] for a, b in waits],
        "keys": [[round(warp.map(a), 3), round(warp.map(b), 3), lbl]
                 for a, b, lbl in keycasts],
        "fast_typing": [[round(warp.map(a), 3), round(warp.map(b), 3)] for a, b, _ in fast],
        "typed": warp_typed(typed, warp),
        "body_seconds": round(tail, 3)}
    web = tl.get("web") if isinstance(tl, dict) else None
    if web and web.get("video"):
        # browser spans are protected (1x), so inside a span: video_t = t0_rel + (orig - span_orig_start)
        warp_out["web"] = {
            "video": web["video"], "t0": web["t0"],
            "spans": [{"orig": [a, b], "warped": [round(warp.map(a), 3), round(warp.map(b), 3)]}
                      for a, b in web["spans"]],
            "actions": [{**act, "start": round(warp.map(act["start"]), 3),
                         "end": round(warp.map(act["end"]), 3),
                         "keys": [round(warp.map(k), 3) for k in act.get("keys", [])],
                         "clicks": [round(warp.map(c), 3) for c in act.get("clicks", [])]}
                        for act in web["actions"]]}
    (episode_dir / "warp.json").write_text(json.dumps(warp_out, indent=2))

    gif = episode_dir / "demo.gif"
    # warping already happened above; stop agg applying its own 5s idle cap
    _sh(["agg", "--font-size", str(font_size), "--fps-cap", "30",
         "--idle-time-limit", "100000", "--last-frame-duration", f"{last_frame:.2f}",
         "--theme", "dracula", str(warped), str(gif)])

    body = episode_dir / "demo_body.mp4"
    # an intermediate: decoded once by the compositor, so encode it quickly
    # and near-losslessly rather than small
    _sh(["ffmpeg", "-nostdin", "-y", "-i", str(gif),
         "-vf", ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=neighbor,"
                 f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color={CARD_BG},fps=30"),
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         str(body)])

    meta = {}
    candidate = episode_dir / "candidate.json"
    if candidate.exists():
        meta = json.loads(candidate.read_text())
    tool = meta.get("tool") or episode_dir.name
    repo = meta.get("repo", "")

    timeline_body = [dict(sc) for sc in timeline]
    if body_only:
        for sc in timeline_body:
            sc["start"], sc["end"] = round(sc["start"] + intro_seconds, 2), round(sc["end"] + intro_seconds, 2)
        (episode_dir / "render_meta.json").write_text(json.dumps(
            {"intro_seconds": intro_seconds, "outro_seconds": outro_seconds,
             "fast_factor": fast_factor, "wait_max": wait_max, "pause_scale": pause_scale,
             "voice": False, "timeline": timeline_body}, indent=2))
        return body

    intro, outro = episode_dir / "intro.mp4", episode_dir / "outro.mp4"
    from . import config
    brand = config.get("brand.name", "") or ""
    _card(intro, intro_seconds, [(brand, 44), (tool, 96), (meta.get("tagline", repo), 36)])
    _card(outro, outro_seconds, [(config.get("cards.final_title", "") or "", 56), (repo, 36), (brand, 40)])

    silent = episode_dir / "final_silent.mp4"
    _sh(["ffmpeg", "-nostdin", "-y", "-i", str(intro), "-i", str(body), "-i", str(outro),
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-pix_fmt", "yuv420p", "-c:v", "libx264",
         "-preset", "fast", str(silent)])

    for sc in timeline:
        sc["start"] = round(sc["start"] + intro_seconds, 2)
        sc["end"] = round(sc["end"] + intro_seconds, 2)
    body_end = round(intro_seconds + tail, 2)  # tail = length of the terminal body
    # welcome over the title card from 0:00, sign-off over the closing card
    timeline = ([{"scene": "welcome", "narration": welcome_text,
                  "start": 0.0, "end": round(intro_seconds, 2)}]
                + timeline
                + [{"scene": "signoff", "narration": signoff_text,
                    "start": body_end, "end": round(body_end + outro_seconds, 2)}])

    final = episode_dir / "final.mp4"
    if clips:
        _mux_voice(silent, final, timeline, episode_dir / "voice", clips)
    else:
        silent.replace(final)

    (episode_dir / "render_meta.json").write_text(json.dumps(
        {"intro_seconds": intro_seconds, "outro_seconds": outro_seconds,
         "fast_factor": fast_factor, "wait_max": wait_max, "pause_scale": pause_scale,
         "voice": bool(clips), "timeline": timeline}, indent=2))
    return final
