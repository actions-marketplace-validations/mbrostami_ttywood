"""Speechless "cinematic" render: the narration becomes on-screen captions,
each scene opens with a chapter card, and a camera follows the typing.

Input: demo_body.mp4 + demo_idle.cast + warp.json + render_meta.json from
the normal render (time-warped terminal video). Output: final.mp4, and
render_meta.json rewritten with the new scene timestamps.

Compositing is done frame-by-frame in numpy/Pillow (a 5-minute episode takes
a couple of minutes); frames stream in and out through ffmpeg pipes so memory
stays flat.
"""

from __future__ import annotations

import bisect
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyte
from PIL import Image, ImageDraw, ImageFont

from . import config
from .screenplay import clean_text, load_screenplay
from .verdict import load_verdict

W, H, FPS = 1920, 1080, 30
BG = (20, 20, 28)
ACCENT = (124, 156, 255)
MUTED = (150, 155, 175)

BAR_H = 100              # strip above the terminal reserved for the pinned command bar
CAPTION_H = 130          # strip below the terminal reserved for captions (never covers output)
TERM_FONT_SIZE = 22      # agg font for the body so the terminal fills the width above the strip
READ_WPM = 165           # caption pacing: a comfortable silent-reading speed
BEAT_MIN, BEAT_MAX = 3.0, 9.0   # a caption is never on screen for less than 3 s
SCENE_CARD_SEC = 3.2            # floors; cards stay longer when there is more to read
INTRO_SEC, OUTRO_SEC = 5.0, 4.5
FADE_SEC = 0.35

ZOOM_TYPING = 1.55       # camera zoom while a command is being typed
CAMERA_EASE = 0.085      # per-frame easing toward the target (0..1)
CURSOR_LEAD = 0.5        # keep the cursor this fraction across the frame (0.5 = centered)

# first installed font from the fonts.ui / fonts.mono lists in config
FONT_UI = config.first_existing("fonts.ui") or ""
FONT_MONO = config.first_existing("fonts.mono") or FONT_UI


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (path, FONT_UI, FONT_MONO):
        if candidate:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    try:  # Pillow >= 10.1 ships a scalable default
        return ImageFont.load_default(size)
    except TypeError:  # pragma: no cover
        return ImageFont.load_default()


# ---- text helpers ----------------------------------------------------------

_ACRONYMS = {"http", "https", "cli", "tui", "json", "yaml", "toml", "csv", "api", "ci", "cd",
             "url", "ssh", "tls", "css", "html", "sql", "gpu", "cpu", "ram", "io", "ui", "ux",
             "npm", "pip", "git", "k8s", "aws", "gcp", "oauth", "jwt", "rest", "grpc"}


def humanize(scene_id: str) -> str:
    words = re.sub(r"[-_]+", " ", scene_id).strip().split()
    out = [w.upper() if w.lower() in _ACRONYMS else w for w in words]
    if out and out[0].lower() not in _ACRONYMS:
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", clean_text(text))
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # very long sentence: split at a comma near the middle
        while len(p.split()) > 26:
            words = p.split()
            mid = len(words) // 2
            cut = None
            for off in range(0, 8):
                for i in (mid + off, mid - off):
                    if 6 <= i < len(words) - 6 and words[i - 1].endswith(","):
                        cut = i
                        break
                if cut:
                    break
            if not cut:
                cut = mid
            out.append(" ".join(words[:cut]).rstrip(",;") + " …")
            p = "… " + " ".join(words[cut:])
        out.append(p)
    # merge fragments that are too short to be a beat on their own
    merged: list[str] = []
    for s in out:
        if merged and len(s.split()) < 5:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return merged


def read_time(text: str) -> float:
    return max(BEAT_MIN, len(text.split()) / READ_WPM * 60 + 0.6)


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# ---- overlays (rendered once, cached) -----------------------------------------

def caption_image(text: str) -> Image.Image:
    """Caption drawn inside the reserved strip under the terminal: accent bar
    plus up to three wrapped lines, vertically centred in the strip."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for size in (34, 30, 26):
        font = _font(FONT_UI, size)
        lines = wrap(d, text, font, 1620)
        line_h = int(size * 1.3)
        if len(lines) * line_h <= CAPTION_H - 24:
            break
    text_w = max(d.textlength(l, font=font) for l in lines)
    block_h = len(lines) * line_h
    x0 = int((W - text_w) / 2)
    y0 = H - CAPTION_H + (CAPTION_H - block_h) // 2
    d.rounded_rectangle((x0 - 26, y0 + 4, x0 - 18, y0 + block_h - 4), radius=4,
                        fill=ACCENT + (255,))
    y = y0
    for l in lines:
        d.text((x0, y), l, font=font, fill=(240, 240, 246, 255))
        y += line_h
    return img


TERM_AREA_H = H - BAR_H - CAPTION_H  # the terminal lives between the two strips


def letterbox(frame: np.ndarray, content: tuple[int, int, int, int]) -> np.ndarray:
    """Crop the terminal content rect (x, y, w, h) out of the body frame and fit
    it between the command-bar strip and the caption strip."""
    x, y, w, h = content
    crop = frame[y:y + h, x:x + w]
    s = min(W / w, TERM_AREA_H / h)
    size = (int(w * s), int(h * s))
    canvas = np.empty((H, W, 3), np.uint8)
    canvas[:] = BG
    small = np.asarray(Image.fromarray(crop).resize(size, Image.BILINEAR))
    x0 = (W - size[0]) // 2
    y0 = BAR_H + (TERM_AREA_H - size[1]) // 2
    canvas[y0:y0 + size[1], x0:x0 + size[0]] = small
    return canvas


def logo_window(height: int = 240) -> Image.Image | None:
    """The configured logo (brand.logo, cropped to brand.logo_crop), for the
    title/closing cards and the watermark. None when there is no logo."""
    path = config.path("brand.logo")
    if not path or not path.is_file():
        return None
    win = Image.open(path).convert("RGB")
    crop = config.get("brand.logo_crop") or []
    if len(crop) == 4:
        win = win.crop(tuple(crop))
    return win.resize((max(1, int(win.width * height / win.height)), height), Image.LANCZOS)


def card_seconds(*texts: str, floor: float) -> float:
    """How long a card stays up: long enough to read everything on it."""
    words = sum(len(t.split()) for t in texts if t)
    return max(floor, words / READ_WPM * 60 + 1.6)


def card_image(kicker: str, title: str, subtitle: str = "", footer: str = "",
               title_size: int = 92, logo: Image.Image | None = None,
               y_offset: int = 0) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_kicker = _font(FONT_MONO, 30)
    f_title = _font(FONT_UI, title_size)
    f_sub = _font(FONT_UI, 40)
    f_foot = _font(FONT_MONO, 26)
    top = 380 + y_offset
    if logo is not None:
        # logo above the rule; everything else shifts down a little
        img.paste(logo, ((W - logo.width) // 2, 118 + y_offset))
        top = 118 + y_offset + logo.height + 40
    # subtle accent rule
    d.rectangle((W // 2 - 60, top, W // 2 + 60, top + 6), fill=ACCENT)
    y = top + 40
    if kicker:
        d.text(((W - d.textlength(kicker, font=f_kicker)) / 2, y), kicker, font=f_kicker, fill=ACCENT)
        y += 60
    title_lines = wrap(d, title, f_title, 1500)
    for l in title_lines:
        d.text(((W - d.textlength(l, font=f_title)) / 2, y), l, font=f_title, fill=(250, 250, 255))
        y += int(title_size * 1.15)
    if subtitle:
        y += 18
        for l in wrap(d, subtitle, f_sub, 1300):
            d.text(((W - d.textlength(l, font=f_sub)) / 2, y), l, font=f_sub, fill=MUTED)
            y += 52
    if footer:
        d.text(((W - d.textlength(footer, font=f_foot)) / 2, H - 90), footer, font=f_foot, fill=MUTED)
    return img


# ---- cursor tracking ----------------------------------------------------------

def _pane_raw(row: str, cursor_x: int) -> str:
    """The focused pane's segment of a screen row, NOT stripped: whether the
    text fills the pane to its last column is how a terminal wrap is told
    apart from a line that simply ended."""
    start = 0
    for i, ch in enumerate(row):
        if ch in "│┃║":  # box-drawing borders only; a plain "|" is a shell pipe
            if start <= cursor_x < i:
                return row[start:i]
            start = i + 1
    return row[start:]


def _pane_segment(row: str, cursor_x: int) -> str:
    """With split panes one screen row holds several panes separated by box
    borders; return only the segment the cursor is in."""
    return _pane_raw(row, cursor_x).rstrip()


_STRING_OPENER = re.compile(r"\x1b[P_^X]")   # DCS, APC, PM, SOS
_STRING_CLOSER = re.compile(r"\x1b\\|\x07")


def _drop_string_sequences(data: str, open_state: bool) -> tuple[str, bool]:
    """pyte has no parser for DCS/APC/PM/SOS, so it prints their payload as
    text: zellij's `ESC P 26661nCgkK... ESC \\` landed in the middle of a
    half-typed command and the bar showed it for a frame. The real terminal
    swallows them, so we do too. A sequence can straddle two cast events,
    hence the carried `open_state`."""
    out, i = [], 0
    while i < len(data):
        if open_state:
            end = _STRING_CLOSER.search(data, i)
            if not end:
                return "".join(out), True
            i, open_state = end.end(), False
            continue
        start = _STRING_OPENER.search(data, i)
        if not start:
            out.append(data[i:])
            break
        out.append(data[i:start.start()])
        i, open_state = start.end(), True
    return "".join(out), open_state


def cursor_track(cast_path: Path, cols: int, rows: int, screens: list | None = None,
                 blocks: list | None = None, known: list[str] | None = None
                 ) -> tuple[list[float], list[tuple[int, int]], list[str]]:
    """Replay the (warped) cast through a VT emulator to know, at every event,
    where the cursor is and what its line says: (times, [(col,row)], [line]).
    Pass a list as `screens` to also collect every event's full screen, and
    one as `blocks` to collect the command block being typed (prompt line
    plus heredoc/continuation lines) as one string per event."""
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    times, pos, lines = [], [], []
    in_string = False
    for line in cast_path.read_text().splitlines()[1:]:
        if not line.strip():
            continue
        t, kind, data = json.loads(line)
        if kind != "o":
            continue
        data, in_string = _drop_string_sequences(data, in_string)
        try:
            stream.feed(data)
        except Exception:
            pass
        times.append(t)
        pos.append((screen.cursor.x, screen.cursor.y))
        lines.append(_pane_segment(screen.display[screen.cursor.y], screen.cursor.x))
        if screens is not None:
            screens.append(tuple(r.rstrip() for r in screen.display))
        if blocks is not None:
            blocks.append("\n".join(command_block(screen.display, screen.cursor.y,
                                                  screen.cursor.x, known=known)))
    return times, pos, lines


BAR_LINGER = 1.4  # seconds the command bar stays after a command's output finished
TYPED_MARK = "\x00typed\x00"  # bar_text prefix: "<prompt>\x00typed\x00<text>" comes from the typed log


def _typed_bar(bar_text: str) -> Image.Image:
    prompt, _, text = bar_text[len(TYPED_MARK):].partition(TYPED_SEP)
    return typed_bar_image(prompt, text)


TYPED_SEP = "\x01"
KEYCAST_HOLD = 1.8  # seconds a keypress stays on the bar after the last key


def _typed_lookup(warp: dict):
    """t -> (index, prefix, hold_until) for the `type:` step whose bar belongs
    on screen at body time t, or None.

    Ground truth from the recorder: warp["typed"] holds every typed step's
    exact text and the body time each character landed (see
    TyperEngine.typed, record.py, render.warp_typed). The bar shows exactly
    the characters that have landed so far, holds while the command runs
    (through the wait that follows its Enter) and lingers BAR_LINGER after
    that; a later step simply replaces it. Nothing is read off the screen,
    so a multiplexer repaint, a wrapped row or a REPL clock can never put
    the wrong text on the bar."""
    entries = warp.get("typed") or []
    waits = warp.get("waits") or []
    spans: list[tuple[float, float, int]] = []
    for i, e in enumerate(entries):
        anchor = e["enter"] if e.get("enter") is not None else e["end"]
        wait_end = next((b for a, b in waits if a >= anchor - 0.3), anchor)
        spans.append((e["start"], max(wait_end, anchor) + BAR_LINGER, i))

    def at(t: float):
        cur = None
        for start, hold, i in spans:
            if start > t + 0.05:
                break
            if t <= hold:
                cur = (i, hold)
        if cur is None:
            return None
        i, hold = cur
        e = entries[i]
        n = bisect.bisect_right(e["chars"], t)
        return i, e["text"][:n], hold
    return at


def _keycast_lookup(warp: dict):
    """t -> the key label to show, or None. A `keys:` step prints nothing on
    screen, so in a keybinding episode the bar has nothing to read; these
    spans come from the typer engine, which knows what it pressed and when."""
    spans = [(a, b, lbl) for a, b, lbl in warp.get("keys", [])]

    def at(t: float) -> str | None:
        for a, b, lbl in spans:
            if a - 0.25 <= t <= b + KEYCAST_HOLD:
                return lbl
        return None
    return at
_PROMPT_RE = re.compile(r"^(.*?(?:\$ |› |> |>>> ))(.*)$")  # bash, browser, nu/fish, python
WEB_PROMPT = "browser › "


class WebSource:
    """Browser video (Playwright webm) aligned to warped body time via warp.json."""

    def __init__(self, episode_dir: Path, web: dict):
        self.reader = BodyReader(episode_dir / web["video"])
        self.t0 = web["t0"]
        self.spans = web["spans"]
        self.actions = web["actions"]

    def video_time(self, t_warped: float) -> float | None:
        for sp in self.spans:
            a, b = sp["warped"]
            if a <= t_warped < b:
                return sp["orig"][0] + (t_warped - a) - self.t0
        return None

    def frame(self, t_warped: float) -> np.ndarray | None:
        tv = self.video_time(t_warped)
        if tv is None:
            return None
        # before the first painted frame (page still loading): hold frame 0
        return self.reader.get(max(0, int(round(tv * FPS))))

    def label(self, t_warped: float) -> str | None:
        current = None
        for act in self.actions:
            if act["start"] <= t_warped and act.get("action") != "wait":
                current = act
        if current and t_warped < current["end"] + BAR_LINGER and current.get("label"):
            return current["label"]
        return None

    def close(self):
        self.reader.close()


def _known_command(fragment: str, known: list[str] | None) -> str | None:
    """The screenplay command this typed fragment belongs to, if exactly one
    matches. The screen is repainted per keystroke by the multiplexer, so a
    wrapped row can hold stale text; the screenplay is the truth."""
    if not known or not fragment:
        return None
    hits = [k for k in known if k.startswith(fragment)]
    return hits[0] if len(set(hits)) == 1 else None


def command_block(rows: tuple[str, ...] | list[str], cursor_y: int, cursor_x: int,
                  known: list[str] | None = None) -> list[str]:
    """The lines of the command being typed: the prompt line plus any
    continuation lines (`> ` heredoc / multi-line input) down to the cursor
    row. Only the focused pane's segment of each row.

    A command longer than the terminal is wrapped by the terminal itself onto
    the next row with no marker; those rows are joined back into one logical
    line, so the bar keeps growing with the typing instead of freezing at the
    first row."""
    if not rows or not (0 <= cursor_y < len(rows)):
        return []
    raw = lambda i: _pane_raw(rows[i], cursor_x)
    seg = lambda i: raw(i).rstrip()
    # walk up: a row belongs to the command if it continues the one above,
    # either as a `> ` continuation or as a terminal wrap (the row above is
    # full to its last column, so the text ran over)
    top = cursor_y
    for _ in range(MAX_WRAP_ROWS):
        if top <= 0:
            break
        cur_is_prompt = _PROMPT_RE.match(seg(top)) and not _CONT_RE.match(seg(top))
        if cur_is_prompt:
            break
        above = raw(top - 1)
        above_full = bool(above) and len(above) == len(above.rstrip())
        wrapped = above_full and bool(seg(top)) and not seg(top).startswith(" ")
        if above_full and seg(top) and not wrapped and known:
            # the break fell on a word boundary, so the row starts with a
            # space; the screenplay tells whether it is still the command
            pm = _PROMPT_RE.match(above.rstrip())
            if pm and _known_command(pm.group(2), known):
                wrapped = True
        if not (_CONT_RE.match(seg(top)) or wrapped):
            break
        top -= 1
    # only a block that starts at a prompt is a command; otherwise the cursor
    # sits in output and a full-width line above is just a long output line
    if top != cursor_y and not _PROMPT_RE.match(seg(top)):
        return [seg(cursor_y)]
    # a wrapped command: rebuild its text from the screenplay when possible,
    # using the cursor to know how much of it has been typed so far
    if known and top < cursor_y and _PROMPT_RE.match(seg(top)):
        m = _PROMPT_RE.match(seg(top))
        prompt, first = m.group(1), m.group(2)
        width = len(raw(top))
        typed = len(first) + sum(width for _ in range(top + 1, cursor_y)) + cursor_x
        full = _known_command(first, known)
        if full:
            return [prompt + full[:max(typed, len(first))]]

    # join the wrapped rows back together; `> ` rows stay separate lines
    block: list[str] = []
    for i in range(top, cursor_y + 1):
        text = seg(i)
        if i > top and not _CONT_RE.match(text) and block:
            prev = raw(i - 1)
            prev_full = bool(prev) and len(prev) == len(prev.rstrip())
            if prev_full and text and not text.startswith(" "):
                # a wrapped row can still carry stale characters at its right
                # edge from whatever the screen held before; the command text
                # itself never has a run of three spaces
                block[-1] += re.split(r" {3,}", text, maxsplit=1)[0]
                continue
        block.append(text)
    return block


_CONT_RE = re.compile(r"^(?:> |\.\.\. |\s*[│┃║]?\s*> )")   # bash PS2 / python continuation
_TOKEN_RE = re.compile(
    r"""(?P<str>"(?:\\.|[^"\\])*"|'[^']*')|(?P<var>\$\{[^}]*\}|\$\w+)|"""
    r"""(?P<op>\|\||&&|\||;|>>|>|<|2>&1|2>)|(?P<flag>--?[A-Za-z][\w-]*)|(?P<ws>\s+)|(?P<word>\S+)""")

C_PROMPT = (140, 150, 175, 255)
C_CMD = (255, 208, 120, 255)      # the program name
C_FLAG = (150, 190, 255, 255)
C_STR = (160, 230, 160, 255)
C_VAR = (255, 160, 130, 255)
C_OP = (170, 175, 195, 255)
C_TEXT = (245, 245, 250, 255)
C_CONT = (110, 118, 140, 255)


def _tokens(cmd: str) -> list[tuple[str, tuple]]:
    """(text, color) pairs; the first word and every word after an operator
    is a program name."""
    out, expect_cmd = [], True
    for m in _TOKEN_RE.finditer(cmd):
        kind = m.lastgroup
        text = m.group(0)
        if kind == "ws":
            out.append((text, C_TEXT))
            continue
        if kind == "op":
            out.append((text, C_OP))
            expect_cmd = True
            continue
        if kind == "str":
            color = C_STR
        elif kind == "var":
            color = C_VAR
        elif kind == "flag":
            color = C_FLAG
        elif expect_cmd and "=" not in text:
            color = C_CMD
        else:
            color = C_TEXT
        if kind == "word" and "=" in text and expect_cmd:
            color = C_VAR  # FOO=bar prefix assignment
        else:
            expect_cmd = False if kind != "word" or "=" not in text else expect_cmd
        out.append((text, color))
    return out


def _wrap_tokens(d: ImageDraw.ImageDraw, tokens: list[tuple[str, tuple]], font,
                 max_w: float) -> list[list[tuple[str, tuple]]]:
    """Greedy wrap that prefers breaking before a flag or after an operator."""
    # a token wider than the panel (a long quoted prompt is one string token)
    # is broken at its spaces first, so it wraps instead of running off the edge
    pieces: list[tuple[str, tuple]] = []
    for text, color in tokens:
        if d.textlength(text, font=font) <= max_w or " " not in text.strip():
            pieces.append((text, color))
            continue
        for part in re.findall(r"\S+\s*|\s+", text):
            pieces.append((part, color))
    lines: list[list[tuple[str, tuple]]] = [[]]
    width = 0.0
    for text, color in pieces:
        w = d.textlength(text, font=font)
        if width + w > max_w and lines[-1]:
            # drop trailing whitespace on the finished line
            while lines[-1] and lines[-1][-1][0].isspace():
                lines[-1].pop()
            lines.append([])
            width = 0.0
            if text.isspace():
                continue
        lines[-1].append((text, color))
        width += w
    return [l for l in lines if l]


def keycast_bar_image(label: str) -> Image.Image:
    """Keys drawn as keycaps where the command bar goes. A keypress leaves no
    text on screen, so this is the only record of what was pressed; drawing
    caps rather than a command line makes clear nobody typed a word."""
    caps = [c for c in label.split("  ") if c]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(FONT_MONO, 40)
    pad_x, gap, cap_pad, cap_h = 34, 16, 22, 74
    widths = [d.textlength(c, font=font) + cap_pad * 2 for c in caps]
    box_w = int(sum(widths) + gap * max(0, len(caps) - 1) + pad_x * 2)
    box_h = cap_h + 28
    x0, y0 = (W - box_w) // 2, (BAR_H - box_h) // 2
    d.rounded_rectangle((x0 + 4, y0 + 6, x0 + box_w + 4, y0 + box_h + 6), radius=18,
                        fill=(0, 0, 0, 90))
    d.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=18,
                        fill=(10, 10, 18, 232), outline=(80, 90, 130, 140), width=2)
    d.rounded_rectangle((x0, y0 + 10, x0 + 6, y0 + box_h - 10), radius=3, fill=ACCENT + (255,))
    x = x0 + pad_x
    for cap, w in zip(caps, widths):
        top = y0 + (box_h - cap_h) // 2
        d.rounded_rectangle((x, top, x + w, top + cap_h), radius=10,
                            fill=(30, 33, 46, 255), outline=(120, 132, 176, 200), width=2)
        d.rounded_rectangle((x + 3, top + cap_h - 9, x + w - 3, top + cap_h - 4), radius=3,
                            fill=(70, 78, 110, 200))   # the cap's lower edge
        d.text((x + cap_pad, top + (cap_h - font.size) / 2 - 4), cap, font=font, fill=(240, 243, 250, 255))
        x += w + gap
    return img


def command_bar_image(block: str) -> Image.Image:
    """Legacy path (recordings without a typed log): the command block as
    scraped from the screen, prompt line plus `> ` continuation lines."""
    raw_lines = block.split("\n")
    # REPLs draw a right-aligned clock/status on the prompt line
    raw_lines = [re.split(r" {3,}", l.rstrip(), maxsplit=1)[0] for l in raw_lines]
    m = _PROMPT_RE.match(raw_lines[0])
    prompt, first = (m.group(1), m.group(2)) if m else ("", raw_lines[0])
    conts = []
    for l in raw_lines[1:]:
        cm = _CONT_RE.match(l)
        conts.append(l[cm.end():] if cm else l)
    return _draw_bar(prompt, first, conts, heredoc="<<" in first)


def typed_bar_image(prompt: str, text: str) -> Image.Image:
    """The bar for a typed step: the prompt the recorder was at, and exactly
    the text typed so far. Every line after the first is a continuation
    (heredoc body, a multi-line command); it is shown verbatim, spaces and
    all, with no guessing about wraps or continuation markers."""
    lines = text.split("\n")
    return _draw_bar(prompt, lines[0], lines[1:], heredoc=len(lines) > 1)


def _draw_bar(prompt: str, first: str, conts: list[str], heredoc: bool) -> Image.Image:
    """The command being typed, pinned across the top: prompt dimmed, program
    name, flags, strings and variables coloured, block cursor at the end.
    Fits on one line when it can; otherwise wraps at flags/pipes onto up to
    MAX_BAR_LINES lines and the panel grows down over the terminal's top rows.
    A multi-line input (heredoc) shows every line typed so far."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    max_w = 1700
    # one line if it fits at a big size, else wrap at a readable size
    for size in (44, 38, 34):
        font = _font(FONT_MONO, size)
        if not conts and d.textlength(prompt + first, font=font) + size <= max_w:
            break
    else:
        size = 30
        font = _font(FONT_MONO, size)
    # a long cwd in the prompt must not squeeze the command: elide the middle
    # of the path until the prompt takes at most 40% of the panel
    while prompt and d.textlength(prompt, font=font) > max_w * 0.4:
        head, sep, tail = prompt.partition(" ")
        body = tail if sep else head
        if "…" in body:
            i = body.index("…")
            body = body[: max(0, i - 2)] + "…" + body[i + 3:] if len(body) > 6 else body[-4:]
        else:
            mid = len(body) // 2
            body = body[: mid - 1] + "…" + body[mid + 2:]
        prompt = (head + " " + body) if sep else body
        if len(prompt) <= 6:
            break
    prompt_w = d.textlength(prompt, font=font)
    indent = min(prompt_w, d.textlength("    ", font=font) * 2)
    rendered: list[tuple[float, list[tuple[str, tuple]]]] = []  # (x offset, tokens)
    first_lines = _wrap_tokens(d, _tokens(first), font, max_w - prompt_w)
    for i, l in enumerate(first_lines or [[]]):
        rendered.append((0.0 if i == 0 else indent, l))
    for c in conts:
        toks = [(c, C_TEXT)] if heredoc else _tokens(c)
        for i, l in enumerate(_wrap_tokens(d, toks, font, max_w - indent) or [[]]):
            rendered.append((indent, l))
    if len(rendered) > MAX_BAR_LINES:
        hidden = len(rendered) - (MAX_BAR_LINES - 1)
        rendered = rendered[:MAX_BAR_LINES - 3] + [(indent, [(f"… {hidden} more lines …", C_CONT)])] \
            + rendered[-2:]
    line_h = int(size * 1.32)
    pad_x, pad_y = 34, 14
    content_w = max((x + (prompt_w if i == 0 else 0) + sum(d.textlength(t, font=font) for t, _ in l))
                    for i, (x, l) in enumerate(rendered))
    content_w = max(content_w, prompt_w + size)
    box_w = min(int(content_w + pad_x * 2 + size), W - 40)  # room for the cursor
    box_h = int(len(rendered) * line_h + pad_y * 2)
    x0 = (W - box_w) // 2
    y0 = (BAR_H - box_h) // 2 if box_h <= BAR_H - 16 else 12
    # shadow, panel, accent edge
    d.rounded_rectangle((x0 + 4, y0 + 6, x0 + box_w + 4, y0 + box_h + 6), radius=18,
                        fill=(0, 0, 0, 90))
    d.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=18,
                        fill=(10, 10, 18, 232), outline=(80, 90, 130, 140), width=2)
    d.rounded_rectangle((x0, y0 + 10, x0 + 6, y0 + box_h - 10), radius=3, fill=ACCENT + (255,))
    y = y0 + pad_y
    for i, (xoff, toks) in enumerate(rendered):
        x = x0 + pad_x + xoff
        if i == 0 and prompt:
            d.text((x, y), prompt, font=font, fill=C_PROMPT)
            x += prompt_w
        elif i > 0 and xoff:
            d.text((x - d.textlength("› ", font=font), y), "› ", font=font, fill=C_CONT)
        for text, color in toks:
            d.text((x, y), text, font=font, fill=color)
            x += d.textlength(text, font=font)
        if i == len(rendered) - 1:
            d.rectangle((x + 4, y + 2, x + 4 + size * 0.6, y + size * 1.15), fill=ACCENT + (255,))
        y += line_h
    return img


MAX_BAR_LINES = 7
MAX_WRAP_ROWS = 6   # how far up command_block follows terminal wrapping


# ---- the compositor -------------------------------------------------------------

@dataclass
class Camera:
    z: float = 1.0
    cx: float = W / 2
    cy: float = H / 2

    def step(self, tz: float, tx: float, ty: float):
        self.z += (tz - self.z) * CAMERA_EASE
        self.cx += (tx - self.cx) * CAMERA_EASE
        self.cy += (ty - self.cy) * CAMERA_EASE

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if abs(self.z - 1.0) < 0.004:
            return frame
        cw, ch = W / self.z, H / self.z
        x0 = min(max(self.cx - cw / 2, 0), W - cw)
        y0 = min(max(self.cy - ch / 2, 0), H - ch)
        crop = frame[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
        return np.asarray(Image.fromarray(crop).resize((W, H), Image.BILINEAR))


def _in_any(t: float, intervals: list[list[float]]) -> bool:
    return any(a <= t < b for a, b in intervals)


# ---- verdict cards --------------------------------------------------------------

GOOD, BAD = (74, 222, 128), (248, 113, 113)
ITEM_SEC = 2.6  # each bullet gets at least this long after it appears


# channel branding, all from config (brand.*); an empty brand.name means none
BRAND = config.get("brand.name", "") or ""
SLOGAN = config.get("brand.slogan", "") or ""
AUTHOR = config.get("brand.author", "") or ""
_WM: dict[str, np.ndarray | None] = {}


def brand_text(d: ImageDraw.ImageDraw, xy: tuple[float, float], font, fill=(242, 242, 247)) -> None:
    """The channel name, its first brand.accent_chars characters in the accent colour."""
    n = int(config.get("brand.accent_chars", 0) or 0)
    head, tail = BRAND[:n], BRAND[n:]
    x, y = xy
    if head:
        d.text((x, y), head, font=font, fill=ACCENT)
        x += d.textlength(head, font=font)
    d.text((x, y), tail, font=font, fill=fill)


def wordmark(height: int = 54, gap: int = 14) -> Image.Image | None:
    """Logo + channel name as one image (used for the final card)."""
    win = logo_window(height)
    if win is None and not BRAND:
        return None
    f = _font(FONT_MONO, int(height * 0.72))
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tw = int(probe.textlength(BRAND, font=f)) if BRAND else 0
    ww = win.width + gap if win is not None else 0
    img = Image.new("RGB", (max(1, ww + tw), height), BG)
    if win is not None:
        img.paste(win, (0, 0))
    d = ImageDraw.Draw(img)
    y = (height - int(height * 0.72)) // 2 - int(height * 0.1)
    if BRAND:
        brand_text(d, (ww, y), f)
    return img


def watermark(frame: np.ndarray) -> np.ndarray:
    """Small channel mark (logo + name), bottom-left, on every frame except
    the welcome and the final card. Nothing without a brand."""
    if "img" not in _WM:
        # stacked: icon with the name under it, narrow enough to stay clear of
        # the centred captions (they never come closer than ~150 px to the edge)
        win = logo_window(44)
        if win is None or not BRAND:
            _WM["img"] = None
        else:
            f = _font(FONT_MONO, 20)
            probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            tw = int(probe.textlength(BRAND, font=f))
            wimg = Image.new("RGB", (max(win.width, tw), win.height + 26), BG)
            wimg.paste(win, ((wimg.width - win.width) // 2, 0))
            d = ImageDraw.Draw(wimg)
            brand_text(d, ((wimg.width - tw) // 2, win.height + 2), f)
            _WM["img"] = np.asarray(wimg)
    wm = _WM["img"]
    if wm is None:
        return frame
    h, w = wm.shape[:2]
    y0, x0 = H - h - 24, 30
    out = frame.copy()
    out[y0:y0 + h, x0:x0 + w] = (out[y0:y0 + h, x0:x0 + w].astype(np.float32) * 0.25
                                 + wm.astype(np.float32) * 0.75).astype(np.uint8)
    return out


def welcome_card() -> Image.Image:
    """Channel-only opener: logo, wordmark, slogan."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    logo = logo_window(260)
    if logo is not None:
        img.paste(logo, ((W - logo.width) // 2, 250))
    f_name = _font(FONT_MONO, 120)
    brand_text(d, ((W - d.textlength(BRAND, font=f_name)) / 2, 540), f_name)
    f_slog = _font(FONT_UI, 40)
    d.text(((W - d.textlength(SLOGAN, font=f_slog)) / 2, 700), SLOGAN, font=f_slog, fill=MUTED)
    return img


def intro_card(tool: str, subtitle: str, url: str, stars: int | None, kicker: str) -> Image.Image:
    img = card_image(kicker, tool, subtitle, title_size=104)
    d = ImageDraw.Draw(img)
    f_url = _font(FONT_MONO, 30)
    line = url.replace("https://", "")  # stars go in the description, not on the card
    d.text(((W - d.textlength(line, font=f_url)) / 2, H - 150), line, font=f_url, fill=MUTED)
    return img


def credits_card(repo: str, contributors: list[dict], avatars: list,
                 total: int | None = None) -> Image.Image:
    n = len(contributors)
    line = f"top {n} contributor{'s' if n != 1 else ''} to {repo}"
    if total and total > n:
        line += ", and 100+ more" if total > 100 else f", and {total - n} more"
    img = card_image("credits", "Built by", line, title_size=72)
    if n:
        gap, size = 380, 160
        x = W // 2 - (n - 1) * gap // 2
        f_login = _font(FONT_MONO, 32)
        d = ImageDraw.Draw(img)
        for c, av in zip(contributors, avatars):
            cx = x
            if av is not None:
                img.paste(av, (cx - size // 2, 640), av)
            else:
                d.ellipse((cx - size // 2, 640, cx + size // 2, 640 + size), fill=(40, 40, 56))
            login = "@" + c["login"]
            d.text((cx - d.textlength(login, font=f_login) / 2, 640 + size + 24), login,
                   font=f_login, fill=(240, 240, 246))
            x += gap
    return img


FINAL_TITLE = config.get("cards.final_title", "") or ""
FINAL_SUBTITLE = config.get("cards.final_subtitle", "") or ""


def final_card(repo_url: str = "") -> Image.Image:
    mark = wordmark(150, gap=36)
    img = card_image("", FINAL_TITLE, FINAL_SUBTITLE or (repo_url.replace("https://", "") if not mark else ""),
                     title_size=72, logo=mark, y_offset=150 if mark is not None else 0)
    if AUTHOR:
        d = ImageDraw.Draw(img)
        f = _font(FONT_MONO, 24)
        d.text((W - d.textlength(AUTHOR, font=f) - 40, H - 60), AUTHOR, font=f, fill=(90, 95, 120))
    return img


def _verdict_card(kind: str, v, tool: str, visible: int) -> Image.Image:
    """kind: 'fit' | 'proscons' | 'bottom'. `visible` = how many items have
    appeared so far (bullets fade in one by one)."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_kicker, f_title = _font(FONT_MONO, 30), _font(FONT_UI, 72)
    f_item, f_small = _font(FONT_UI, 42), _font(FONT_UI, 34)

    def kicker(text):
        d.text(((W - d.textlength(text, font=f_kicker)) / 2, 96), text, font=f_kicker, fill=ACCENT)

    def title(text, y=140):
        d.text(((W - d.textlength(text, font=f_title)) / 2, y), text, font=f_title, fill=(250, 250, 255))

    if kind == "fit":
        kicker(f"the verdict · {tool} · 1/2")
        title("Where you'd use it")
        f_item = _font(FONT_UI, 46)
        y = 330
        for i, item in enumerate(v.use_cases):
            if i >= visible:
                break
            d.rounded_rectangle((500, y + 16, 518, y + 34), radius=3, fill=ACCENT)
            for l in wrap(d, item, f_item, 940):
                d.text((552, y), l, font=f_item, fill=(240, 240, 246))
                y += 58
            y += 30
    elif kind == "proscons":
        kicker(f"the verdict · {tool} · 2/3")
        title("Pros and cons")
        cols = ((130, "pros", GOOD, "+", v.pros), (1010, "cons", BAD, "−", v.cons))
        for x, head, color, mark, items in cols:
            d.text((x + 40, 290), head, font=_font(FONT_MONO, 34), fill=color)
            d.rectangle((x + 40, 336, x + 780, 339), fill=color)
            y = 372
            for i, item in enumerate(items):
                # pros and cons appear alternately: pro1, con1, pro2, ...
                order = i * 2 + (0 if head == "pros" else 1)
                if order >= visible:
                    break
                d.text((x + 40, y), mark, font=_font(FONT_UI, 44), fill=color)
                for l in wrap(d, item, f_item, 690):
                    d.text((x + 96, y), l, font=f_item, fill=(240, 240, 246))
                    y += 54
                y += 26
    else:
        kicker(f"the verdict · {tool} · 2/2")
        title("Bottom line", 150)
        f_big = _font(FONT_UI, 60)
        lines = wrap(d, v.bottom_line, f_big, 1400)
        y = H // 2 - len(lines) * 40
        for l in lines:
            d.text(((W - d.textlength(l, font=f_big)) / 2, y), l, font=f_big, fill=(250, 250, 255))
            y += 78
        if visible >= 2:
            d.text(((W - d.textlength(tool, font=f_kicker)) / 2, y + 40), tool, font=f_kicker, fill=MUTED)
    return img


def render_verdict(v, tool: str, narration: str, write, t_out: float,
                   sfx_events: list, kicker_time: float) -> tuple[float, list[dict]]:
    """Two animated cards (use cases, bottom line). Bullets appear one by one
    at reading pace. No captions here: the spoken explanation ends with the
    terminal demo, the cards carry the verdict on their own. Returns the new
    output time and timeline entries (one per card, for chapters)."""
    plan = [("fit", len(v.use_cases), "Where you'd use it"),
            ("bottom", 2, "Bottom line")]
    # per-card durations: each item needs reading time, plus a settle at the end
    def item_secs(kind, i):
        items = v.use_cases if kind == "fit" else [v.bottom_line, tool]
        return max(ITEM_SEC, read_time(items[i]) if i < len(items) else ITEM_SEC)
    beats: list = []
    entries, tau_global = [], 0.0
    cache: dict[tuple, Image.Image] = {}
    for kind, n_items, label in plan:
        start_out = t_out
        steps = [item_secs(kind, i) for i in range(n_items)]
        dur = sum(steps) + 1.6
        n_frames = int(dur * FPS)
        # item i becomes visible at the cumulative time of the previous items
        appear = [sum(steps[:i]) for i in range(n_items)]
        for k in range(n_frames):
            tau = k / FPS
            visible = sum(1 for a in appear if tau >= a)
            key = (kind, visible)
            if key not in cache:
                cache[key] = _verdict_card(kind, v, tool, visible)
            fr = np.asarray(cache[key])
            # soft fade-in of the newest item: blend previous state for 0.35 s
            if visible and tau - appear[visible - 1] < FADE_SEC:
                prev = (kind, visible - 1)
                if prev not in cache:
                    cache[prev] = _verdict_card(kind, v, tool, visible - 1)
                a = (tau - appear[visible - 1]) / FADE_SEC
                fr = (np.asarray(cache[prev]).astype(np.float32) * (1 - a)
                      + fr.astype(np.float32) * a).astype(np.uint8)
            for b in beats:
                if b.start <= tau_global + tau < b.end:
                    k0 = int((tau_global + tau - b.start) * FPS)
                    nb = int((b.end - b.start) * FPS)
                    fr = _blend(fr, b.img, _fade(k0, max(nb, 1)))
                    break
            write(_dim(watermark(fr), _fade(k, n_frames)))
        for a in appear[1:]:
            sfx_events.append((start_out + a, "space"))  # soft thock as each item lands
        t_out += n_frames / FPS
        tau_global += n_frames / FPS
        entries.append({"scene": f"verdict-{kind}", "narration": label,
                        "start": round(start_out, 2), "end": round(t_out, 2)})
    return t_out, entries


def _key_events(ctimes: list[float], clines: list[str], typing, warp: dict,
                start: float, end: float, out_start: float) -> list[tuple[float, str]]:
    """Key sound events for the body interval [start, end), mapped to output
    time. Every cast event during a typing interval is one echoed keystroke;
    the start of a wait is the Enter."""
    events: list[tuple[float, str]] = []
    if warp.get("typed"):
        # the recorder's own log: one tick per character at the moment it
        # landed, the Enter where it was pressed
        enters = []
        for e in warp["typed"]:
            for c, ch in zip(e["chars"], e["text"]):
                if start <= c < end:
                    events.append((out_start + c - start, "space" if ch in " \n" else "tick"))
            if e.get("enter") is not None and start <= e["enter"] < end:
                events.append((out_start + e["enter"] - start, "enter"))
                enters.append(e["enter"])
        for a, _b in warp["waits"]:  # waits after a keys: step still end in an Enter
            if start <= a < end and not any(abs(a - x) < 0.3 for x in enters):
                events.append((out_start + a - start, "enter"))
        return sorted(events)
    lo = bisect.bisect_left(ctimes, start)
    hi = bisect.bisect_left(ctimes, end)
    last_tick = -1.0
    for idx in range(lo, hi):
        tb = ctimes[idx]
        if typing(tb) and tb - last_tick > 0.03:
            # the character just echoed is the last char of the cursor line
            line = clines[idx]
            kind = "space" if line.endswith(" ") or (
                idx > 0 and len(line) == len(clines[idx - 1])) else "tick"
            events.append((out_start + tb - start, kind))
            last_tick = tb
    for a, _b in warp["waits"]:
        if start <= a < end:
            events.append((out_start + a - start, "enter"))
    return events


COLD_OPEN_SEC = 7.0  # the payoff clip shown before any card
MUSIC_GAIN = 0.22    # the bed under the key sounds (bed.wav peaks at 0.7)


def locate_command(scenes: list[dict], scene_id: str, command: str,
                   ctimes: list[float], clines: list[str], warp: dict
                   ) -> tuple[float, float, float] | None:
    """(typed_at, enter, wait_end) of a command in the recording: the first
    moment inside its scene the cursor line contains the typed text (REPL
    prompts may carry a clock on the same row, long commands wrap so the
    tail is enough), then the next wait interval is its execution."""
    sc = next((s for s in scenes if s["scene"] == scene_id), None)
    if sc is None:
        return None
    s, e = sc["start"], sc["end"]
    needle = " ".join(command.split())
    tail = needle[-30:]
    lo, hi = bisect.bisect_left(ctimes, s), bisect.bisect_left(ctimes, e)
    for i in range(lo, hi):
        line = " ".join(clines[i].split())
        if needle in line or (len(needle) > 30 and tail in line):
            typed_at = ctimes[i]
            enter = next((a for a, _b in warp["waits"] if a >= typed_at - 0.05), typed_at)
            wait_end = next((b for a, b in warp["waits"] if a >= typed_at - 0.05), enter)
            return typed_at, enter, wait_end
    return None


def command_outputs(play, scenes: list[dict], ctimes: list[float], clines: list[str],
                    screens: list[tuple[str, ...]], warp: dict, max_lines: int = 14
                    ) -> dict[tuple[str, str], str]:
    """What each typed command put on screen: the rows that are new after
    its wait compared with the screen when it was typed."""
    def screen_at(t: float) -> tuple[str, ...]:
        i = bisect.bisect_right(ctimes, t) - 1
        return screens[i] if 0 <= i < len(screens) else ()

    out: dict[tuple[str, str], str] = {}
    for sc in play.scenes:
        for c in sc.commands:
            if not c.type:
                continue
            loc = locate_command(scenes, sc.id, c.type, ctimes, clines, warp)
            if loc is None:
                continue
            typed_at, _enter, wait_end = loc
            before = set(screen_at(typed_at))
            after = screen_at(wait_end + 0.6)
            rows = [r for r in after if r.strip() and r not in before and c.type not in r]
            out[(sc.id, c.type)] = "\n".join(rows[:max_lines]) + (
                f"\n... ({len(rows) - max_lines} more rows)" if len(rows) > max_lines else "")
    return out


def cold_open_for_command(scenes: list[dict], scene_id: str, command: str,
                          ctimes: list[float], clines: list[str], warp: dict
                          ) -> tuple[float, float, str, float] | None:
    """Window around a chosen command: opens on its typing tail, holds the
    output long enough to read it."""
    loc = locate_command(scenes, scene_id, command, ctimes, clines, warp)
    if loc is None:
        return None
    sc = next(s for s in scenes if s["scene"] == scene_id)
    s, e = sc["start"], sc["end"]
    _typed_at, enter, wait_end = loc
    start = max(s, enter - 1.5)
    length = min(10.0, max(COLD_OPEN_SEC, wait_end - start + 3.0))
    # the window may outlast the scene: the renderer then holds the last frame
    return start, start + length, sc["scene"], min(e, start + length)


def cold_open_window(scenes: list[dict], short_scene: str | None, ctimes: list[float],
                     screens: list[tuple[str, ...]], warp: dict
                     ) -> tuple[float, float, str] | None:
    """Fallback when nobody chose a command: the COLD_OPEN_SEC window of the
    scene the Short would show around the command with the biggest output
    (the wait whose screen changed on the most rows; zellij repaints
    everything on every keystroke, so bytes and event counts say nothing)."""
    if not scenes:
        return None
    from .shorts import pick_scene
    try:
        sc = pick_scene(scenes, short_scene)
    except ValueError:
        sc = pick_scene(scenes, None)
    s, e = sc["start"], sc["end"]
    length = min(COLD_OPEN_SEC, e - s)
    if length < 2.0:
        return None
    lead = min(1.5, length / 3)

    def screen_at(t: float) -> tuple[str, ...] | None:
        i = bisect.bisect_right(ctimes, t) - 1
        return screens[i] if 0 <= i < len(screens) else None

    best, best_rows = None, -1
    for a, b in warp["waits"]:
        if not (s <= a < e):
            continue
        before, after = screen_at(a - 0.05), screen_at(b + 0.5)
        if before is None or after is None:
            continue
        # rows that are new, not just scrolled up: set difference of the lines
        old = set(before)
        changed = sum(1 for y in after if y.strip() and y not in old)
        if changed > best_rows:
            best, best_rows = a, changed
    if best is None:
        best = s + lead
    start = min(max(s, best - lead), max(s, e - length))
    return start, min(e, start + length), sc["scene"]


def command_anchors(warp: dict, start: float, end: float) -> list[float]:
    """Scene-relative times at which each command's typing begins: the moment
    a protected pause (the pre_pause) ends inside the scene. Web actions count
    too (their spans are protected, so their start is an anchor as well)."""
    anchors = set()
    for a, b in warp["protected"]:
        if start <= b < end:
            anchors.add(round(b - start, 3))
    for sp in (warp.get("web") or {}).get("spans", []):
        a, _ = sp["warped"]
        if start <= a < end:
            anchors.add(round(a - start, 3))
    return sorted(anchors)


def _fade(k: int, n: int) -> float:
    f = int(FADE_SEC * FPS)
    return min(1.0, (k + 1) / f, (n - k) / f)


_OVERLAYS: dict[int, tuple] = {}  # id(overlay) -> (overlay, bbox, premultiplied rgb, alpha)


def _overlay_parts(overlay: Image.Image) -> tuple | None:
    """An overlay is a full-frame RGBA image that is transparent almost
    everywhere (a bar strip, a caption strip). Blending the whole frame in
    float32 cost ~17 ms per overlay per frame; only the opaque bounding box
    matters. Cached per image object (the callers cache the images)."""
    key = id(overlay)
    hit = _OVERLAYS.get(key)
    if hit is not None and hit[0] is overlay:
        return hit
    ov = np.asarray(overlay)
    if ov.ndim != 3 or ov.shape[2] != 4:
        ov = np.asarray(overlay.convert("RGBA"))
    ys, xs = np.nonzero(ov[:, :, 3])
    if len(ys) == 0:
        parts = (overlay, None, None, None)
    else:
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        box = ov[y0:y1, x0:x1].astype(np.float32)
        a = box[:, :, 3:4] / 255.0
        parts = (overlay, (y0, y1, x0, x1), box[:, :, :3] * a, a)
    if len(_OVERLAYS) > 64:
        _OVERLAYS.clear()
    _OVERLAYS[key] = parts
    return parts


def _blend(base: np.ndarray, overlay: Image.Image, alpha: float) -> np.ndarray:
    """base with overlay composited at `alpha`; the base is never modified."""
    if alpha <= 0:
        return base
    _, bbox, rgb_pre, a = _overlay_parts(overlay)
    if bbox is None:
        return base
    y0, y1, x0, x1 = bbox
    out = base.copy()
    region = out[y0:y1, x0:x1].astype(np.float32)
    region *= 1.0 - a * alpha
    region += rgb_pre * alpha
    out[y0:y1, x0:x1] = region.astype(np.uint8)
    return out


def _dim(frame: np.ndarray, f: float) -> np.ndarray:
    return frame if f >= 0.999 else (frame.astype(np.float32) * f).astype(np.uint8)


# ---- sound design (synthesized, no samples needed) -----------------------------

SR = 48000


def _decay(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau * SR))


def _lowpass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """One-pole low-pass; takes the fizz out of noise transients."""
    a = math.exp(-2 * math.pi * cutoff_hz / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = a * acc + (1 - a) * v
        y[i] = acc
    return y


def _thock(rng: np.random.Generator, deep: bool = False) -> np.ndarray:
    """A muted 'brown switch' keypress: a damped low body resonance under a
    soft, low-passed transient. No high-frequency click."""
    n = int(0.055 * SR)
    t = np.arange(n) / SR
    f = rng.uniform(95, 130) if deep else rng.uniform(150, 215)
    body = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * f * 2.4 * t)) * _decay(n, 0.011)
    transient = _lowpass(rng.normal(0, 1, n), 900) * _decay(n, 0.0025) * 2.2
    s = body * 0.55 + transient
    s *= rng.uniform(0.65, 1.0) * (1.35 if deep else 1.0)
    return s.astype(np.float32)


def _load_samples(root: Path) -> list[np.ndarray]:
    """Optional real recordings: key*.wav in paths.sfx (mono/stereo, any rate)."""
    out = []
    for p in sorted(root.glob("key*.wav")):
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(p), "-f", "f32le", "-ac", "1",
                            "-ar", str(SR), "-"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            s = np.frombuffer(r.stdout, np.float32)
            peak = float(np.max(np.abs(s))) or 1.0
            out.append((s / peak).astype(np.float32))
    return out


MIN_KEY_GAP = 0.07     # no two key sounds closer than this (a fast human, ~14 keys/s)
BURST_WINDOW = 0.5     # ...and when more keys than a human could press land in
BURST_KEYS = 7         # this window, the typing is fast-forwarded: go silent


def thin_key_events(events: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Key sounds a viewer can bear. Typing that is fast-forwarded in the
    render (a heredoc, a long command) arrives as dozens of keystrokes per
    second; playing a thock for each is a machine gun. Keep Enter always,
    keep normal typing, and inside an inhuman burst keep only its first key
    or two: silence reads as "sped up", a rattle reads as broken audio."""
    events = sorted(events)
    times = [t for t, _ in events]
    out: list[tuple[float, str]] = []
    last_key = -1.0
    for i, (t, kind) in enumerate(events):
        if kind == "enter":
            out.append((t, kind))
            continue
        if t - last_key < MIN_KEY_GAP:
            continue
        lo = bisect.bisect_left(times, t - BURST_WINDOW)
        if i - lo >= BURST_KEYS:
            continue
        out.append((t, kind))
        last_key = t
    return out


def write_sfx_track(path: Path, events: list[tuple[float, str]], duration: float):
    """events: (time_seconds, kind) with kind in tick|space|enter.
    Uses key*.wav from paths.sfx if set, else synthesized brown-switch thocks."""
    rng = np.random.default_rng(7)
    sfx_dir = config.path("paths.sfx")
    samples = _load_samples(sfx_dir) if sfx_dir else []
    buf = np.zeros(int((duration + 1.0) * SR), np.float32)
    for t, kind in thin_key_events(events):
        deep = kind in ("space", "enter")
        if samples:
            s = samples[rng.integers(len(samples))] * rng.uniform(0.6, 1.0) * (1.3 if deep else 1.0)
            if deep:  # pitch the sample down a little for the big keys
                s = np.interp(np.arange(0, len(s), 0.85), np.arange(len(s)), s).astype(np.float32)
        else:
            s = _thock(rng, deep=deep)
        i = int(t * SR)
        j = min(i + len(s), len(buf))
        buf[i:j] += s[: j - i]
    peak = float(np.max(np.abs(buf))) or 1.0
    buf *= min(1.0, 0.28 / peak)  # a quiet background layer, roughly -11 dBFS peaks
    pcm = (buf * 32767).astype(np.int16)
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _video_size(path: Path) -> tuple[int, int]:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    w, h = p.stdout.strip().split(",")[:2]
    return int(w), int(h)


class BodyReader:
    """Sequential frame access to a video by output frame index (at FPS)."""

    def __init__(self, path: Path):
        self.w, self.h = _video_size(path)
        self.proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-vf", f"fps={FPS}", "-"], stdout=subprocess.PIPE)
        self.idx = -1
        self.frame: np.ndarray | None = None
        self._buf: bytes | None = None
        self.eof = False

    def get(self, i: int) -> np.ndarray:
        """The frame at index i. A terminal sits still most of the time, so
        the same picture is returned as the SAME array object while nothing
        changed: callers key their caches on identity and skip the work."""
        n = self.w * self.h * 3
        while self.idx < i and not self.eof:
            buf = self.proc.stdout.read(n)  # type: ignore[union-attr]
            if len(buf) < n:
                self.eof = True
                break
            self.idx += 1
            if self._buf is not None and buf == self._buf:
                continue  # identical picture: keep the array (and its identity)
            self._buf = buf
            self.frame = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
        assert self.frame is not None
        return self.frame

    def close(self):
        self.proc.kill()


@dataclass
class Beat:
    text: str
    start: float  # scene-relative seconds
    end: float
    img: Image.Image = field(repr=False)


def plan_beats(narration: str, total: float, anchors: list[float] | None = None,
               make_image=None) -> list[Beat]:
    """Spread narration sentences over `total` seconds. With `anchors` (scene-
    relative times when each command starts being typed) the beats are grouped
    per command so captions track what is on screen instead of drifting."""
    make_image = make_image or caption_image
    parts = sentences(narration)
    if not parts:
        return []
    weights = [max(1, len(p.split())) for p in parts]

    def spread(items: list[tuple[str, int]], start: float, end: float) -> list[Beat]:
        span = max(end - start, BEAT_MIN)
        tot = sum(w for _, w in items)
        out, t = [], start
        for p, w in items:
            d = max(BEAT_MIN, span * w / tot)
            out.append(Beat(p, t, t + d, make_image(p)))
            t += d
        return out

    anchors = sorted(a for a in (anchors or []) if 0 <= a < total)
    if len(anchors) >= 2 and len(parts) >= len(anchors):
        # group beats onto commands proportionally, in order
        groups: list[list[tuple[str, int]]] = [[] for _ in anchors]
        for i, item in enumerate(zip(parts, weights)):
            groups[min(len(anchors) - 1, i * len(anchors) // len(parts))].append(item)
        bounds = anchors + [total]
        beats: list[Beat] = []
        for g, (a, b) in zip(groups, zip(bounds, bounds[1:])):
            beats += spread(g, a, b)
        # pull the first beat to the scene start so there's never a silent opening
        if beats:
            beats[0].start = 0.0
        # never overlap: each beat ends where the next starts
        for prev, nxt in zip(beats, beats[1:]):
            prev.end = min(prev.end, nxt.start) if nxt.start > prev.start else prev.end
        return beats
    return spread(list(zip(parts, weights)), 0.0, total)


def render_cinematic(episode_dir: Path, music: Path | None = None,
                     zoom: bool = False, sfx: bool = True, cold_open: bool = False,
                     on_progress=None) -> Path:
    """zoom=False (default): steady full terminal + pinned command bar.
    zoom=True: the camera follows the cursor while typing instead.
    sfx: synthesized key ticks / Enter thud / done chime at the real times.
    music: a bed to loop under it all; the synthesized lo-fi bed when None.
    cold_open: open on the tool in use before the welcome card (off by default).
    on_progress(seconds_written, estimated_total, label) every output second."""
    episode_dir = episode_dir.resolve()
    if music is None:
        from .music import ensure_bed
        music = ensure_bed()
    meta = json.loads((episode_dir / "render_meta.json").read_text())
    warp = json.loads((episode_dir / "warp.json").read_text())
    play = load_screenplay(episode_dir / "screenplay.yaml")
    cand = json.loads((episode_dir / "candidate.json").read_text()) \
        if (episode_dir / "candidate.json").exists() else {}
    intro_off = meta["intro_seconds"]
    scenes = [dict(s, start=s["start"] - intro_off, end=s["end"] - intro_off)
              for s in meta["timeline"] if s["scene"] not in ("welcome", "signoff")]
    narr = {s.id: s.narration for s in play.scenes}

    # cursor geometry: gif cell grid -> body pixel coordinates
    cols, rows = play.meta.terminal.cols, play.meta.terminal.rows
    gw, gh = Image.open(episode_dir / "demo.gif").size
    scale = min(W / gw, H / gh)
    offx, offy = (W - gw * scale) / 2, (H - gh * scale) / 2
    cell_w, cell_h = gw * scale / cols, gh * scale / rows
    content = (int(offx), int(offy), int(gw * scale), int(gh * scale))
    screens: list = []
    cblocks: list[str] = []  # the command block being typed, per event (for the bar)

    def steady(blocks: list[str]) -> list[str]:
        """The multiplexer repaints per keystroke, so consecutive events can
        alternate between the wrapped and unwrapped view of the same command.
        Keep the longest text seen for a command while it is still being
        typed, so the bar only ever grows."""
        out: list[str] = []
        best = ""
        for b in blocks:
            if best and (b.startswith(best) or best.startswith(b)):
                best = b if len(b) > len(best) else best
            else:
                best = b
            out.append(best)
        return out

    typed_commands = [c.type for sc in play.scenes for c in sc.commands if c.type]
    ctimes, cpos, clines = cursor_track(episode_dir / "demo_idle.cast", cols, rows,
                                        screens if cold_open else None, cblocks,
                                        known=typed_commands)
    cblocks = steady(cblocks)

    def cursor_at(t: float) -> int:
        return max(bisect.bisect_right(ctimes, t) - 1, 0)

    def cursor_px(t: float) -> tuple[float, float]:
        x, y = cpos[cursor_at(t)] if cpos else (0, 0)
        return offx + (x + 0.5) * cell_w, offy + (y + 0.5) * cell_h

    typing = lambda t: not _in_any(t, warp["protected"]) and not _in_any(t, warp["waits"])
    waiting = lambda t: _in_any(t, warp["waits"])
    keycast = _keycast_lookup(warp)
    keylabels = {lbl for _a, _b, lbl in warp.get("keys", [])}
    typed_at = _typed_lookup(warp) if warp.get("typed") else None
    prompt_cache: dict[int, str] = {}

    def typed_prompt(i: int) -> str:
        """The prompt a typed step started on: the cursor line just before
        its first keystroke (one stable read, nothing typed yet)."""
        if i not in prompt_cache:
            e = warp["typed"][i]
            line = clines[cursor_at(e["start"] - 0.01)] if clines else ""
            m = _PROMPT_RE.match(line)
            prompt_cache[i] = m.group(1) if m and not m.group(2).strip() else ""
        return prompt_cache[i]
    bar_cache: dict[str, Image.Image] = {}
    web = WebSource(episode_dir, warp["web"]) if warp.get("web") else None

    body = BodyReader(episode_dir / "demo_body.mp4")
    final = episode_dir / "final.mp4"
    video_only = episode_dir / "final_video.mp4"
    out = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
         "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", str(video_only)],
        stdin=subprocess.PIPE)
    sfx_events: list[tuple[float, str]] = []  # (output time, tick|enter|done)
    # rough total for the progress bar: body + cards + verdict + credits + outro
    est_total = sum(max(s["end"] - s["start"], 1.0) for s in scenes) + 12 * len(scenes) + 75
    frames_out = [0]
    current = ["cold open"]

    written: list = [None, b""]  # the last array written and its bytes

    def write(fr):
        # a card held for seconds, a still terminal: the same array comes
        # back frame after frame, so serialise it once
        if fr is not written[0]:
            written[0], written[1] = fr, np.ascontiguousarray(fr).tobytes()
        out.stdin.write(written[1])  # type: ignore[union-attr]
        frames_out[0] += 1
        if on_progress and frames_out[0] % FPS == 0:
            on_progress(frames_out[0] / FPS, est_total, current[0])

    tool = play.meta.tool
    tagline = play.meta.tagline or cand.get("tagline", "")
    repo = play.meta.repo or cand.get("repo", "")
    structure = cand.get("structure", "tour")
    shape = {"bug-hunt": "bug hunt", "versus": "versus", "speedrun": "5-minute speedrun"}.get(structure, "")
    kicker = " · ".join(k for k in (BRAND, shape) if k)

    # the repo's own one-liner is the subtitle when it reads well, else Claude's tagline
    from .repoinfo import avatar, fetch_repo_info, usable_description
    rinfo = fetch_repo_info(episode_dir, repo) if repo else {}
    subtitle = usable_description(rinfo.get("description"), tool) or tagline
    repo_url = rinfo.get("html_url") or (f"https://github.com/{repo}" if repo else "")

    # 0) cold open: the tool doing its thing, before any card. The first ten
    #    seconds decide whether anyone stays, and nobody stays for a logo.
    timeline_out: list[dict] = []
    t_out = 0.0
    cold, hook_text = None, ""
    if cold_open:
        from .coldopen import load_cold_open
        outputs = command_outputs(play, scenes, ctimes, clines, screens, warp)
        choice = load_cold_open(episode_dir, play, outputs,
                                log=lambda m: on_progress and on_progress(0, 1, m))
        if choice:
            cold = cold_open_for_command(scenes, choice.scene, choice.command,
                                         ctimes, clines, warp)
            hook_text = choice.hook
        if cold is None:
            fb = cold_open_window(scenes, play.meta.short_scene, ctimes, screens, warp)
            cold = (*fb, fb[1]) if fb else None
    screens.clear()
    if cold and web:
        # a browser step that starts inside the window is the payoff (a page,
        # a video playing): open on it instead of on the command before it
        for sp in web.spans:
            a, b = sp["warped"]
            if cold[0] <= a < cold[1]:
                cold = (a, min(b, a + 10.0), cold[2], min(b, a + 10.0))
                break
    if cold:
        c_start, c_end, c_scene, c_freeze = cold
        hook = [hook_text] if hook_text else sentences(narr.get(c_scene, ""))
        hook_img = caption_image(hook[0]) if hook else None
        n = int((c_end - c_start) * FPS)
        bar_text, bar_until, bar_shown_at = "", -1.0, 0.0
        bar_ran, ran_bars = False, set()  # the bar's command has executed; commands that did
        bar_typed_idx = -1  # which typed step the bar is showing (typed-log path)
        # the body reader is forward-only: the cold open gets its own, so the
        # main pass still starts at frame 0
        cold_body = BodyReader(episode_dir / "demo_body.mp4")
        for k in range(n):
            tau = k / FPS
            t_body = min(c_start + tau, c_freeze - 1.0 / FPS)  # hold the last frame past the scene
            fr = letterbox(cold_body.get(int(round(t_body * FPS))), content)
            web_frame = web.frame(t_body) if web else None
            if web_frame is not None:
                fr = letterbox(web_frame, (0, 0, web_frame.shape[1], web_frame.shape[0]))
            hit = typed_at(t_body) if typed_at else None
            if web_frame is not None:
                bar_text = ""  # full-frame browser: no terminal command bar over it
            elif typed_at:
                if hit:
                    ti, prefix, hold = hit
                    shown = TYPED_MARK + typed_prompt(ti) + TYPED_SEP + prefix
                    if bar_typed_idx != ti:
                        bar_shown_at, bar_typed_idx = tau, ti
                    bar_text = shown
                    bar_until = tau + min(BAR_LINGER, max(hold - t_body, 0.0))
            elif typing(t_body) or waiting(t_body):
                idx = cursor_at(t_body)
                line = clines[idx] if clines else ""
                block = cblocks[idx] if cblocks else line
                m = _PROMPT_RE.match(block.split("\n")[0] if block else "")
                extends = bool(bar_text) and block.startswith(bar_text)
                # zellij repaints between keystrokes with the cursor parked on
                # an earlier prompt row, so the block is briefly a command that
                # already ran: never let that replace the one being typed
                stale = (not extends and (block in ran_bars
                         or (bool(bar_text) and not bar_ran
                             and bool(m) and len(m.group(2)) > 1)))
                if (typing(t_body) or extends) and m and m.group(2) and not stale:
                    if not bar_text or not extends:
                        bar_shown_at, bar_ran = tau, False
                    bar_text = block
                if waiting(t_body) and bar_text:
                    bar_ran = True
                    ran_bars.add(bar_text)
                bar_until = tau + BAR_LINGER
            if bar_text and tau < bar_until:
                if bar_text not in bar_cache:
                    bar_cache[bar_text] = (
                        keycast_bar_image(bar_text) if bar_text in keylabels
                        else _typed_bar(bar_text) if bar_text.startswith(TYPED_MARK)
                        else command_bar_image(bar_text))
                a = min(1.0, (tau - bar_shown_at) / FADE_SEC, (bar_until - tau) / FADE_SEC)
                fr = _blend(fr, bar_cache[bar_text], max(a, 0.0))
            if hook_img is not None:
                fr = _blend(fr, hook_img, _fade(k, n))
            write(_dim(watermark(fr), _fade(k, n)))
        cold_body.close()
        if sfx:
            sfx_events += _key_events(ctimes, clines, typing, warp, c_start, c_freeze, t_out)
        t_out += n / FPS
        timeline_out.append({"scene": "cold-open", "narration": hook[0] if hook else "",
                             "start": 0.0, "end": round(t_out, 2)})

    # 1) welcome: channel only (skipped without a brand).  2) intro: the tool,
    #    its one-liner, the repo URL
    current[0] = "welcome + intro cards"
    welcome_start = t_out
    welcome_seconds = card_seconds(BRAND, SLOGAN, floor=4.0) if BRAND else 0.0
    if BRAND:
        welcome = np.asarray(welcome_card())
        n = int(welcome_seconds * FPS)
        for k in range(n):
            write(_dim(welcome, _fade(k, n)))
    t_out += welcome_seconds
    intro_seconds = card_seconds(kicker, tool, subtitle, repo_url, floor=INTRO_SEC)
    intro = watermark(np.asarray(intro_card(tool, subtitle, repo_url, rinfo.get("stars"), kicker)))
    n = int(intro_seconds * FPS)
    for k in range(n):
        write(_dim(intro, _fade(k, n)))
    t_out += intro_seconds

    cam = Camera()
    # YouTube only shows chapters when the first one starts at 00:00
    timeline_out += ([{"scene": "welcome", "narration": SLOGAN, "start": round(welcome_start, 2),
                       "end": round(welcome_start + welcome_seconds, 2)}] if BRAND else []) + [
                     {"scene": "intro", "narration": f"{tool}: {subtitle}",
                      "start": round(welcome_start + welcome_seconds, 2), "end": round(t_out, 2)}]
    # a command-less final scene (the spoken verdict) is shown as designed
    # verdict cards instead of a frozen terminal
    verdict = load_verdict(episode_dir, play)
    verdict_narration = ""
    by_id = {s.id: s for s in play.scenes}
    if verdict is not None and scenes:
        last = by_id.get(scenes[-1]["scene"])
        if last is not None and not last.commands:
            verdict_narration = narr.get(last.id, "")
            scenes = scenes[:-1]
    n_scenes = len(scenes) + (1 if verdict is not None else 0)  # the verdict is a scene too
    base_src: np.ndarray | None = None  # the body frame the cached base was made from
    base_img: np.ndarray | None = None  # ...letterboxed and watermarked
    last_src: np.ndarray | None = None  # what the last written frame was made of...
    last_key: tuple | None = None       # ...and with which overlays
    last_out: np.ndarray | None = None
    for si, sc in enumerate(scenes):
        title = humanize(sc["scene"])
        current[0] = f"scene {si + 1}/{n_scenes} {sc['scene']}"
        text = narr.get(sc["scene"], sc.get("narration", ""))
        beats_src = sentences(text)
        takeaway = beats_src[0] if beats_src else ""
        sub = takeaway if len(takeaway) <= 170 else takeaway[:150].rsplit(" ", 1)[0] + " …"
        card = watermark(np.asarray(card_image(f"scene {si + 1} of {n_scenes}", title, sub,
                                               title_size=84)))
        card_sec = card_seconds(title, sub, floor=SCENE_CARD_SEC)
        n = int(card_sec * FPS)
        for k in range(n):
            write(_dim(card, _fade(k, n)))
        t_out += card_sec

        scene_len = max(sc["end"] - sc["start"], 1.0 / FPS)
        if si == len(scenes) - 1:
            # the recorder types `exit` right after the last scene; keep the
            # held outro frame clear of that first keystroke
            scene_len = max(scene_len - 0.6, 1.0 / FPS)
        total = max(scene_len, sum(read_time(b) for b in beats_src))
        # a command "starts" when typing begins: the end of the previous wait/pause
        anchors = command_anchors(warp, sc["start"], sc["start"] + scene_len)
        beats = plan_beats(text, total, anchors)
        n_frames = int(round(total * FPS))
        scene_start_out = t_out
        if sfx:
            sfx_events += _key_events(ctimes, clines, typing, warp, sc["start"],
                                      sc["start"] + scene_len, scene_start_out)
            if web:
                for act in web.actions:
                    for k in act.get("keys", []):
                        if sc["start"] <= k < sc["start"] + scene_len:
                            sfx_events.append((scene_start_out + k - sc["start"], "tick"))
                    for c in act.get("clicks", []):
                        if sc["start"] <= c < sc["start"] + scene_len:
                            sfx_events.append((scene_start_out + c - sc["start"], "space"))
        cam = Camera()  # each scene opens wide
        bar_text, bar_until, bar_shown_at = "", -1.0, 0.0
        bar_ran, ran_bars = False, set()  # the bar's command has executed; commands that did
        bar_typed_idx = -1  # which typed step the bar is showing (typed-log path)
        last_web: np.ndarray | None = None  # browser result stays up through the reading pause
        scene_web: np.ndarray | None = None  # the scene's last browser frame, for the held tail
        for k in range(n_frames):
            tau = k / FPS
            live = tau < scene_len
            t_body = sc["start"] + min(tau, scene_len - 1.0 / FPS)
            web_frame = web.frame(t_body) if web else None
            if web_frame is not None:
                last_web = scene_web = web_frame
            elif last_web is not None and not (typing(t_body) or waiting(t_body)):
                web_frame = last_web
            elif not live and scene_web is not None and total - scene_len > 3.0:
                # the scene is held for its narration: if the browser was the
                # point of the scene, hold on it, not on the terminal after `kill %1`
                web_frame = scene_web
            else:
                last_web = None
            if web_frame is not None:
                # browser scene: full browser frame, no camera, bar shows the action
                fr = watermark(letterbox(web_frame, (0, 0, web_frame.shape[1], web_frame.shape[0])))
                lbl = web.label(t_body) if web else None
                if lbl:
                    if not bar_text or not bar_text.endswith(lbl):
                        bar_shown_at = tau
                    bar_text = WEB_PROMPT + lbl
                    bar_until = tau + BAR_LINGER
                else:
                    bar_until = min(bar_until, tau)
            else:
                frame = body.get(int(round(t_body * FPS)))
                if zoom:
                    if typing(t_body) and live:
                        px, py = cursor_px(t_body)
                        cam.step(ZOOM_TYPING, px, py)
                    else:
                        cam.step(1.0, W / 2, H / 2)
                if zoom and abs(cam.z - 1.0) >= 0.004:
                    fr = watermark(letterbox(cam.apply(frame), content))
                else:
                    # the body reader hands back the same array while the
                    # terminal sits still; the letterboxed, watermarked base
                    # is computed once per distinct picture
                    if frame is not base_src:
                        base_src, base_img = frame, watermark(letterbox(frame, content))
                    fr = base_img
            # pinned command bar: appears as a command is typed, stays while it
            # runs, lingers briefly over the result, then fades
            keys_now = keycast(t_body) if web_frame is None else None
            if keys_now:
                # a keypress: the screen shows no command, so the bar shows the keys
                if bar_text != keys_now:
                    bar_shown_at, bar_text = tau, keys_now
                    bar_ran = True  # a keypress bar never blocks the next command
                bar_until = tau + BAR_LINGER
            elif web_frame is None and live and typed_at:
                hit = typed_at(t_body)
                if hit:
                    ti, prefix, hold = hit
                    shown = TYPED_MARK + typed_prompt(ti) + TYPED_SEP + prefix
                    if bar_typed_idx != ti:
                        bar_shown_at, bar_typed_idx = tau, ti
                    bar_text = shown
                    bar_until = tau + min(BAR_LINGER, max(hold - t_body, 0.0))
            elif web_frame is None and live and (typing(t_body) or waiting(t_body)):
                idx = cursor_at(t_body)
                line = clines[idx] if clines else ""
                block = cblocks[idx] if cblocks else line
                # gate on the command BLOCK, not the cursor line: once a long
                # command wraps, the cursor sits on a row with no prompt on
                # it, and gating on that line froze the bar mid-command
                head = block.split("\n")[0] if block else ""
                m = _PROMPT_RE.match(head)
                # The last character lands during the protected beat before
                # Enter, where typing() is already false -- so gating on
                # typing() alone froze the bar one keystroke short of the
                # command. Keep updating while the block still extends what
                # the bar is showing.
                extends = bool(bar_text) and block.startswith(bar_text)
                # zellij repaints between keystrokes with the cursor parked on
                # an earlier prompt row, so the block is briefly a command that
                # already ran: never let that replace the one being typed
                stale = (not extends and (block in ran_bars
                         or (bool(bar_text) and not bar_ran
                             and bool(m) and len(m.group(2)) > 1)))
                if (typing(t_body) or extends) and m and m.group(2) and not stale:
                    if not bar_text or not extends:
                        bar_shown_at, bar_ran = tau, False
                    bar_text = block
                if waiting(t_body) and bar_text:
                    bar_ran = True
                    ran_bars.add(bar_text)
                bar_until = tau + BAR_LINGER
            src = fr
            bar_img, bar_a = None, 0.0
            if bar_text and tau < bar_until:
                if bar_text not in bar_cache:
                    bar_cache[bar_text] = (
                        keycast_bar_image(bar_text) if bar_text in keylabels
                        else _typed_bar(bar_text) if bar_text.startswith(TYPED_MARK)
                        else command_bar_image(bar_text))
                bar_img = bar_cache[bar_text]
                bar_a = max(min(1.0, (tau - bar_shown_at) / FADE_SEC, (bar_until - tau) / FADE_SEC), 0.0)
            elif bar_text and tau >= bar_until:
                bar_text = ""
            beat_img, beat_a = None, 0.0
            for b in beats:
                if b.start <= tau < b.end:
                    k0 = int((tau - b.start) * FPS)
                    nb = int((b.end - b.start) * FPS)
                    beat_img, beat_a = b.img, _fade(k0, max(nb, 1))
                    break
            fade = _fade(k, n_frames)
            # same picture, same overlays at the same opacity: the frame is
            # the one just written (most frames, while a command runs or the
            # viewer reads). The overlay images live in caches, so their ids
            # are stable for the run.
            key = (id(bar_img), round(bar_a, 3), id(beat_img), round(beat_a, 3), round(fade, 3))
            if src is last_src and key == last_key:
                write(last_out)
                continue
            if bar_img is not None:
                fr = _blend(fr, bar_img, bar_a)
            if beat_img is not None:
                fr = _blend(fr, beat_img, beat_a)
            out_fr = _dim(fr, fade)
            last_src, last_key, last_out = src, key, out_fr
            write(out_fr)
        t_out += n_frames / FPS
        timeline_out.append({"scene": sc["scene"], "narration": text,
                             "start": round(scene_start_out - card_sec, 2),
                             "end": round(t_out, 2)})

    if verdict is not None:
        # the verdict is the last numbered scene: its own card, then the cards
        current[0] = "verdict cards"
        card = watermark(np.asarray(card_image(f"scene {n_scenes} of {n_scenes}", "The verdict",
                                               "where it fits, and the bottom line", title_size=84)))
        card_sec = card_seconds("The verdict", "where it fits, and the bottom line", floor=SCENE_CARD_SEC)
        n = int(card_sec * FPS)
        for k in range(n):
            write(_dim(card, _fade(k, n)))
        t_out += card_sec
        t_out, entries = render_verdict(verdict, tool, verdict_narration, write, t_out,
                                        sfx_events, 0.0)
        entries[0]["start"] = round(entries[0]["start"] - card_sec, 2)
        timeline_out += entries

    # credits: top contributors with avatars
    current[0] = "credits + final card"
    contributors = rinfo.get("contributors", [])[:3]
    if contributors:
        avatars = [avatar(episode_dir, c["login"], c["avatar_url"]) for c in contributors]
        credits = watermark(np.asarray(credits_card(repo, contributors, avatars,
                                                    rinfo.get("contributor_count"))))
        credits_seconds = max(5.0, 2.0 + 1.2 * len(contributors))
        n = int(credits_seconds * FPS)
        start_out = t_out
        for k in range(n):
            write(_dim(credits, _fade(k, n)))
        t_out += credits_seconds
        timeline_out.append({"scene": "credits", "narration": "top contributors: "
                             + ", ".join("@" + c["login"] for c in contributors),
                             "start": round(start_out, 2), "end": round(t_out, 2)})

    outro_seconds = card_seconds(FINAL_TITLE, FINAL_SUBTITLE, floor=OUTRO_SEC)
    outro = np.asarray(final_card(repo_url))  # no watermark: the logo is the card
    n = int(outro_seconds * FPS)
    for k in range(n):
        write(_dim(outro, _fade(k, n)))
    t_out += outro_seconds

    out.stdin.close()  # type: ignore[union-attr]
    out.wait()
    body.close()
    if web:
        web.close()
    if out.returncode != 0:
        raise RuntimeError("ffmpeg failed while writing the cinematic render")

    # audio: synthesized sfx and/or music bed, muxed in a second pass
    if on_progress:
        on_progress(t_out, t_out, "muxing key sounds and music")
    inputs, maps, filters, n_in = ["-i", str(video_only)], [], [], 1
    if sfx and sfx_events:
        sfx_path = episode_dir / "sfx.wav"
        write_sfx_track(sfx_path, sfx_events, t_out)
        inputs += ["-i", str(sfx_path)]
        filters.append(f"[{n_in}:a]volume=1.0[sfx]")
        maps.append("[sfx]")
        n_in += 1
    if music and music.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        filters.append(f"[{n_in}:a]volume={MUSIC_GAIN},afade=t=in:d=2[bed]")
        maps.append("[bed]")
        n_in += 1
    if maps:
        mix = (f"{''.join(maps)}amix=inputs={len(maps)}:normalize=0:dropout_transition=0[a]"
               if len(maps) > 1 else f"{maps[0]}anull[a]")
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *inputs,
                        "-filter_complex", ";".join(filters + [mix]),
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", "-shortest", str(final)], check=True)
        video_only.unlink()
    else:
        video_only.replace(final)

    meta.update({"mode": "cinematic", "intro_seconds": round(intro_seconds, 2),
                 "outro_seconds": round(outro_seconds, 2),
                 "body_timeline": scenes,  # body-relative scene bounds (used by shorts)
                 "scene_card_seconds": SCENE_CARD_SEC, "voice": False,
                 "sfx": bool(sfx and sfx_events), "sfx_events": len(sfx_events),
                 "music": str(music) if music and music.exists() else None,
                 "cold_open": {"scene": cold[2], "start": round(cold[0], 2),
                               "end": round(cold[1], 2), "hook": hook_text} if cold else None,
                 "timeline": timeline_out, "duration": round(t_out, 2)})
    (episode_dir / "render_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return final
