"""Screenplay schema: the contract between the rehearsal pass (Claude writes it)
and the performance pass (typer engine replays it deterministically)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Terminal(BaseModel):
    cols: int = 120
    rows: int = 30


LANGUAGES = {"en": "English", "fa": "Farsi (Persian)"}


class ColdOpen(BaseModel):
    """The first seconds of the video: one command whose output shows what
    the tool is about, chosen by whoever actually used it (the rehearsal),
    with a one-line hook caption."""
    scene: str
    command: str      # exactly as typed in that scene
    hook: str = ""    # <= 12 words, the caption over the cold open


class Meta(BaseModel):
    tool: str
    repo: str = ""
    tagline: str = ""
    lang: str = "en"  # language of narration/metadata: en | fa
    terminal: Terminal = Field(default_factory=Terminal)
    # regex matched against the last non-empty line of the screen to detect
    # the shell prompt. Matches the PS1 baked into the Docker image.
    # the demo prompt, optionally prefixed by an activated virtualenv
    # ("(venv) demo ~/x $"), which is otherwise a silent wait timeout
    prompt_regex: str = r"^(\([^)]+\) )?demo .*\$ ?$"
    # scene id that best shows the tool in use (the Short is cut from it)
    short_scene: Optional[str] = None
    # the command whose output opens the video (see ColdOpen)
    cold_open: Optional[ColdOpen] = None
    # run the recording container with --privileged (loop devices, /dev/fuse,
    # mounts); the rehearsal takes it from candidate.json instead
    privileged: bool = False
    # set by the rehearsal when this sandbox cannot follow the only documented path
    # (e.g. "needs docker"); the pipeline reruns with the right setup
    blocked: Optional[str] = None
    # which multiplexer hosts the recording: "zellij" (the default look)
    # or "tmux" (invisible: no status bar, no prefix key). An episode about a
    # multiplexer must not run inside itself, or the outer session eats the
    # keybindings being taught. Left unset, record.py infers it.
    host: Optional[str] = None

    @field_validator("repo")
    @classmethod
    def _owner_name(cls, v: str) -> str:
        # the cards build https://github.com/<repo> from it; anything that is
        # not owner/name (a local project, a sentence) means "no repo"
        v = (v or "").strip()
        return v if re.fullmatch(r"[\w.-]+/[\w.-]+", v) else ""


class WaitFor(BaseModel):
    prompt: bool = True            # wait for the shell prompt to return
    regex: Optional[str] = None    # or: wait until this regex appears on screen
    stable: Optional[float] = None # or: wait until the screen stopped changing for N s
    url: Optional[str] = None      # or: wait until this URL answers (HTTP 2xx/3xx) from inside the container
    timeout: float = 180.0
    settle: float = 0.6            # extra dwell after the condition is met

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.prompt and not self.regex and not self.stable and not self.url:
            raise ValueError("wait_for needs prompt:true, a regex, stable:<seconds> or url:<http url>")
        return self


# named keys -> byte sequences for `zellij action write`
KEYMAP: dict[str, list[int]] = {
    "Enter": [13],
    "Tab": [9],
    "S-Tab": [27, 91, 90],  # back-tab (CSI Z): Claude Code cycles permission modes with it
    "Esc": [27],
    "Space": [32],
    "Backspace": [127],
    "C-c": [3],
    "C-d": [4],
    "C-r": [18],
    "Up": [27, 91, 65],
    "Down": [27, 91, 66],
    "Right": [27, 91, 67],
    "Left": [27, 91, 68],
}


_CHORD = re.compile(r"^([CM])-(.)$")
_ARROWS = {"Up": 65, "Down": 66, "Right": 67, "Left": 68}


def key_bytes(name: str) -> list[int] | None:
    """The bytes a key name sends, or None if it isn't a key.

    Beyond the named keys: `C-<letter>` (control) and `M-<char>` (alt, sent as
    ESC + the character). A modifier chord must reach the terminal as one
    write — typing ESC and `n` as two characters 75 ms apart is Escape then n,
    not Alt-n, which is how a keybinding demo silently does nothing.
    """
    if name in KEYMAP:
        return KEYMAP[name]
    if name.startswith("M-") and name[2:] in _ARROWS:
        # Alt+arrow is the arrow's CSI with modifier 3: ESC [ 1 ; 3 <final>
        return [27, 91, 49, 59, 51, _ARROWS[name[2:]]]
    m = _CHORD.match(name)
    if m:
        mod, ch = m.groups()
        if mod == "C" and ch.isalpha():
            return [ord(ch.lower()) - 96]
        if mod == "M":
            return [27, ord(ch)]
    if len(name) == 1 and name.isprintable():
        return [ord(name)]
    return None


_KEY_PRETTY = {"Up": "↑", "Down": "↓", "Left": "←", "Right": "→",
               "Enter": "Enter", "Esc": "Esc", "Tab": "Tab", "Space": "Space",
               "Backspace": "Backspace", "S-Tab": "Shift-Tab"}


def keys_label(keys: list[str]) -> str:
    """How a key sequence reads on the command bar: `C-p n` -> "Ctrl-p  n".

    Keystrokes leave no trace on screen, so in an episode about keybindings the
    bar would otherwise sit empty (or stale) through the only thing that
    matters. This is what gets drawn instead."""
    out = []
    for k in keys:
        if k.startswith(("C-", "M-")):
            mod = "Ctrl" if k[0] == "C" else "Alt"
            rest = k[2:]
            out.append(f"{mod}-{_KEY_PRETTY.get(rest, rest)}")
        else:
            out.append(_KEY_PRETTY.get(k, k))
    return "  ".join(out)


WEB_ACTIONS = {"goto", "click", "fill", "type", "press", "hover", "wait", "scroll"}


class WebAction(BaseModel):
    """One browser step, executed by container/webdriver.py (Playwright)."""
    action: str
    label: str = ""                   # shown in the command bar, e.g. "click  New folder"
    url: Optional[str] = None
    target: Optional[str] = None      # css | text:... | role:button:Name | label:... | placeholder:...
    text: Optional[str] = None
    key: Optional[str] = None
    ms: Optional[int] = None
    dy: Optional[int] = None
    # click only if the target shows up within `ms` (default 3 s), else skip:
    # for onboarding modals, surveys and banners that may or may not appear
    optional: Optional[bool] = None

    @field_validator("action")
    @classmethod
    def _known(cls, v):
        if v not in WEB_ACTIONS:
            raise ValueError(f"unknown web action {v!r}; allowed: {sorted(WEB_ACTIONS)}")
        return v

    @model_validator(mode="after")
    def _fields(self):
        need = {"goto": "url", "click": "target", "fill": "target", "type": "text",
                "press": "key", "hover": "target"}.get(self.action)
        if need and getattr(self, need) is None:
            raise ValueError(f"web action {self.action!r} needs `{need}`")
        if self.action == "fill" and self.text is None:
            raise ValueError("web action 'fill' needs `text`")
        return self

    def default_label(self) -> str:
        return {
            "goto": f"open  {self.url}", "click": f"click  {_pretty(self.target)}",
            "fill": f"type  {self.text}", "type": f"type  {self.text}",
            "press": f"press  {self.key}", "hover": f"hover  {_pretty(self.target)}",
            "wait": "wait", "scroll": "scroll",
        }[self.action]


def _pretty(target: Optional[str]) -> str:
    if not target:
        return ""
    for p in ("text:", "role:button:", "role:link:", "label:", "placeholder:"):
        if target.startswith(p):
            return target[len(p):]
    return target


class Command(BaseModel):
    type: Optional[str] = None        # text typed char-by-char with human jitter
    keys: Optional[list[str]] = None  # named keys (see KEYMAP), sent as-is
    web: Optional[list[WebAction]] = None  # browser steps instead of terminal input
    # zellij layout action, e.g. "new-pane -d right", "move-focus left",
    # "close-pane"; keystrokes always go to the focused pane
    zellij: Optional[str] = None
    enter: bool = True                # press Enter after `type`
    pre_pause: float = 0.9            # "thinking" pause before typing starts
    post_pause: float = 0.8           # linger after completion so viewers can read
    wait_for: WaitFor = Field(default_factory=WaitFor)

    @field_validator("keys")
    @classmethod
    def _known_keys(cls, v):
        if v:
            unknown = [k for k in v if key_bytes(k) is None]
            if unknown:
                raise ValueError(f"unknown keys {unknown}; allowed: {sorted(KEYMAP)}, "
                                 "C-<letter>, M-<char>, or a single character")
        return v

    @model_validator(mode="after")
    def _type_or_keys(self):
        if not self.type and not self.keys and not self.web and not self.zellij:
            raise ValueError("command needs `type`, `keys`, `web` or `zellij`")
        if self.web and (self.type or self.keys):
            raise ValueError("a command is either terminal (`type`/`keys`) or `web`, not both")
        if self.zellij and (self.type or self.keys or self.web):
            raise ValueError("`zellij` is its own step; put typing in the next command")
        if self.zellij and self.zellij.split()[0] not in (
                "new-pane", "move-focus", "close-pane", "toggle-fullscreen", "resize",
                "focus-next-pane", "focus-previous-pane", "toggle-pane-frames"):
            raise ValueError(f"unsupported zellij action {self.zellij!r}")
        return self


class Screenplay(BaseModel):
    meta: Meta
    scenes: list[Scene]
    verdict: Optional["Verdict"] = None

    @field_validator("scenes")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("screenplay has no scenes")
        return v

    def has_web(self) -> bool:
        return any(c.web for s in self.scenes for c in s.commands)


class Scene(BaseModel):
    id: str
    narration: str
    commands: list[Command] = Field(default_factory=list)
    dwell: float = 0.0  # extra seconds of stillness (e.g. a narration-only outro)


class Verdict(BaseModel):
    """Structured review, rendered as designed closing cards."""
    use_cases: list[str] = Field(default_factory=list)   # 3-4 concrete situations
    good_for: list[str] = Field(default_factory=list)    # 1-2 kinds of people/teams
    not_for: list[str] = Field(default_factory=list)     # 1-2 kinds of people/teams
    pros: list[str] = Field(default_factory=list)        # 3, tied to what was seen
    cons: list[str] = Field(default_factory=list)        # 3, tied to what was seen
    bottom_line: str = ""                                # <= 20 words

    @model_validator(mode="after")
    def _counts(self):
        if not 2 <= len(self.use_cases) <= 5:
            raise ValueError(f"verdict.use_cases needs 2-5 items, got {len(self.use_cases)}")
        if not self.bottom_line.strip():
            raise ValueError("verdict.bottom_line is required")
        return self


TAGLINE_JOIN = {"en": ", ", "fa": "، "}

# Text-level guardrails applied to everything spoken or published.
_DASHES = re.compile(r"\s*[—–]\s*")
BANNED_PHRASES = [
    "delve", "seamless", "leverage", "game-changer", "game changer", "unleash",
    "elevate", "in today's video", "without further ado", "let's dive in",
    "dive into", "it's worth noting", "at the end of the day", "in this video we",
    "buckle up", "supercharge", "revolutionize", "cutting-edge", "robust",
]


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def clean_text(text: str) -> str:
    """No em/en dashes (they read as machine-written), no emoji (they come
    from copied README badges); collapse whitespace. Inline code (`...`) is
    left exactly as written: `--path .` is a command."""
    parts = re.split(r"(`[^`]*`)", text)
    for i, part in enumerate(parts):
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            continue
        part = EMOJI.sub("", _DASHES.sub(", ", part))
        part = re.sub(r",\s*,", ",", part)
        part = re.sub(r"\s+([.,;:!?،])(?=\s|$)", r"\1", part)  # keep ".env", "a .toml"
        parts[i] = re.sub(r"[ \t]+", " ", part)
    return "".join(parts).strip()


def clean_markdown(text: str) -> str:
    """clean_text line by line, leaving fenced code blocks untouched and
    dropping any chatter before the first heading."""
    out, in_fence, seen_heading = [], False, False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not seen_heading:
            if line.startswith("#"):
                seen_heading = True
            else:
                continue
        out.append(line if in_fence or not line.strip() else clean_text(line))
    return "\n".join(out) + "\n"


def banned_phrases(text: str) -> list[str]:
    low = text.lower()
    return [p for p in BANNED_PHRASES if p in low]


def bumpers(play: Screenplay) -> tuple[str, str]:
    """(welcome, signoff) spoken over the title and closing cards, in the
    episode's language, from narration.welcome / narration.signoff in config
    (empty by default: nothing is spoken). The welcome starts at 0:00."""
    from . import config
    welcome = config.get("narration.welcome", {}) or {}
    signoff = config.get("narration.signoff", {}) or {}
    lang = play.meta.lang
    tagline = ""
    if play.meta.tagline:
        tagline = TAGLINE_JOIN.get(lang, ", ") + play.meta.tagline.rstrip(".")
    w = welcome.get(lang) or welcome.get("en") or ""
    s = signoff.get(lang) or signoff.get("en") or ""
    return (clean_text(w.format(tool=play.meta.tool, tagline=tagline)), clean_text(s))


def load_screenplay(path: str | Path) -> Screenplay:
    with open(path) as f:
        data = yaml.safe_load(f)
    play = Screenplay.model_validate(data)
    for scene in play.scenes:
        scene.narration = clean_text(scene.narration)
    play.meta.tagline = clean_text(play.meta.tagline)
    if play.verdict:
        v = play.verdict
        for name in ("use_cases", "good_for", "not_for", "pros", "cons"):
            setattr(v, name, [clean_text(x).rstrip(".") for x in getattr(v, name)])
        v.bottom_line = clean_text(v.bottom_line)
    return play


def validation_errors(path: str | Path) -> Optional[str]:
    """Return a human-readable error string, or None if the file is valid."""
    try:
        load_screenplay(path)
        return None
    except Exception as e:  # yaml errors + pydantic errors alike
        return str(e)
