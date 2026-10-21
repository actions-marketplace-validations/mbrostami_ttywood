"""Human-like keystroke injection into a zellij session running inside Docker.

A single persistent `docker exec -i <container> bash` process is kept open as a
low-latency command channel; every keystroke becomes a `zellij action
write-chars` (or `write` for raw bytes) executed through it.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import select
import shlex
import subprocess
import time

from .screenplay import KEYMAP, Command, Scene, Screenplay, key_bytes, keys_label


MIN_WAIT_TIMEOUT = 25.0  # screenplays underestimate; never give up sooner than this


TYPE_SPEED = 1.6  # keystroke cadence multiplier (pauses and waits stay real time)

class RecordError(RuntimeError):
    """A screenplay command didn't finish on camera. Carries enough context
    for Claude to fix the screenplay."""

    def __init__(self, scene: str, command: str, reason: str, screen: str):
        self.scene, self.command, self.reason, self.screen = scene, command, reason, screen
        super().__init__(f"scene {scene!r}, command {command!r}: {reason}\n"
                         f"--- screen ---\n{screen}")


class ContainerShell:
    """Persistent bash inside the container; run() round-trips in ~10ms
    instead of the ~100ms a fresh `docker exec` costs."""

    def __init__(self, container: str):
        self.proc = subprocess.Popen(
            ["docker", "exec", "-i", container, "bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._n = 0
        self._buf = b""

    def run(self, cmd: str, timeout: float = 30.0) -> str:
        assert self.proc.stdin and self.proc.stdout
        self._n += 1
        marker = f"__DT_DONE_{self._n}__"
        # leading newline guarantees the marker sits on its own line even when
        # the command's output has no trailing newline (zellij dump-screen)
        self.proc.stdin.write(f"{cmd}\nprintf '\\n%s\\n' {marker}\n".encode())
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        needle = f"\n{marker}\n".encode()
        while True:
            idx = self._buf.find(needle)
            if idx >= 0:
                text = self._buf[:idx].decode("utf-8", "replace")
                self._buf = self._buf[idx + len(needle):]
                return text
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"container shell timed out on: {cmd}")
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError("container shell died")
            self._buf += chunk

    def close(self):
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
            self.proc.terminate()
        except Exception:
            pass


WEB_PYTHON = "/home/demo/.local/share/pipx/venvs/playwright/bin/python"


class WebChannel:
    """Persistent Playwright driver inside the container (container/webdriver.py):
    one JSON action per line in, one JSON reply per line out."""

    def __init__(self, container: str):
        # stderr goes to /episode/webdriver.log inside the container so a
        # crash (Chromium failing to start, OOM) can be read afterwards
        self.proc = subprocess.Popen(
            ["docker", "exec", "-i", container, "bash", "-c",
             f"exec {WEB_PYTHON} /opt/ttywood/webdriver.py 2>>/episode/webdriver.log"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self._n = 0
        try:
            ready = self._read(timeout=120)
        except RuntimeError as e:
            raise RuntimeError(f"{e}; see <episode>/webdriver.log") from None
        if not ready.get("ready"):
            raise RuntimeError(f"web driver did not start: {ready}")
        self.t0 = float(ready["t0"])

    def _read(self, timeout: float) -> dict:
        assert self.proc.stdout
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("web driver did not answer")
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                continue
            line = self.proc.stdout.readline()
            if line == "":
                raise RuntimeError("web driver died")
            if line.strip():
                return json.loads(line)

    def send(self, action: dict, timeout: float = 90.0) -> dict:
        assert self.proc.stdin
        self._n += 1
        self.proc.stdin.write(json.dumps({"id": self._n, **action}) + "\n")
        self.proc.stdin.flush()
        return self._read(timeout)

    def close(self) -> tuple[str | None, float]:
        """Returns (video path in the container, wall time the recording ended).
        Chromium only starts emitting frames at the first paint, so the video's
        start is unknowable up front; the caller derives it as end - duration."""
        sent_at = time.time()
        try:
            # finalizing a long recording can take Chromium a while
            reply = self.send({"action": "close"}, timeout=300)
            # the last video frame is written when close *starts*, not when it returns
            return reply.get("video"), float(reply.get("start", sent_at))
        except Exception:
            # the close hung or the driver died: the webm on disk is still
            # usable (record.py remuxes an unfinalized one); its last frame
            # is from about when close was sent
            return None, sent_at
        finally:
            self.proc.terminate()


ENTER_PAUSE_MIN = 0.30  # seconds of stillness between the last character and Enter

_TMUX_SPLIT = {"right": "-h", "left": "-hb", "down": "-v", "up": "-vb"}
_TMUX_FOCUS = {"right": "-R", "left": "-L", "up": "-U", "down": "-D"}


_ZELLIJ_ARROW = {"left": "M-Left", "right": "M-Right", "up": "M-Up", "down": "M-Down"}


def _zellij_keys(action: str) -> list[str]:
    """A screenplay layout step as the keys a viewer would actually press,
    for episodes where zellij is the subject rather than the host."""
    head, *rest = action.split()
    words = [w for w in rest if not w.startswith("-")]
    where = words[0] if words else "right"
    if head == "move-focus":
        return [_ZELLIJ_ARROW.get(where, "M-Right")]
    if head == "new-pane":
        return ["M-n"] if where in ("right", "") else ["C-p", where[0]]
    if head == "close-pane":
        return ["C-p", "x"]
    if head == "toggle-fullscreen":
        return ["C-p", "f"]
    return []


def _tmux_layout(action: str, session: str) -> str:
    """The screenplay speaks zellij; translate it when tmux is hosting."""
    head, *rest = action.split()
    words = [w for w in rest if not w.startswith("-")]
    where = words[0] if words else "right"
    t = f"-t {shlex.quote(session)}"
    if head == "new-pane":
        return f"split-window {_TMUX_SPLIT.get(where, '-h')} {t}"
    if head == "move-focus":
        return f"select-pane {_TMUX_FOCUS.get(where, '-R')} {t}"
    if head == "close-pane":
        return f"kill-pane {t}"
    if head == "toggle-fullscreen":
        return f"resize-pane -Z {t}"
    if head in ("focus-next-pane", "focus-previous-pane"):
        return f"select-pane {'-t :.+' if head == 'focus-next-pane' else '-t :.-'}"
    return f"display-message {t} ''"   # nothing to do (pane frames, resize)


class TyperEngine:
    def __init__(self, container: str, session: str = "demo",
                 speed: float = 1.0, seed: int | None = None, host: str = "zellij"):
        self.container = container
        # Which multiplexer hosts the recording. An episode about zellij cannot
        # be recorded inside zellij: the outer session eats exactly the
        # keybindings the episode is teaching, and two status bars are on
        # screen at once. `tmux` hosts it invisibly instead (status bar off,
        # send-keys injects bytes straight into the pane, so nothing of ours
        # intercepts Ctrl-p or Ctrl-n).
        self.host = host
        self.web: WebChannel | None = None
        self.web_spans: list[tuple[float, float]] = []   # wall-clock spans of browser use
        self.web_actions: list[dict] = []                # per-action log (wall-clock)
        self.shell = ContainerShell(container)
        self.session = session
        self.speed = speed  # >1 = faster typing
        self.rng = random.Random(seed)
        self.on_progress = None  # optional callback(scene_id, command_label)
        self.timeline: list[dict] = []
        # wall-clock intervals of deliberate stillness (pre/post pauses, dwell)
        # that the renderer must NOT idle-compress
        self.protected: list[tuple[float, float]] = []
        # wall-clock intervals spent waiting for a command to finish; the
        # renderer time-lapses these (clone/install/output scroll)
        self.waits: list[tuple[float, float]] = []
        # (start, end, label) of every keys: step, for the command bar
        self.keycasts: list[tuple[float, float, str]] = []
        self.keystrokes: list[float] = []  # wall-clock time of every typed char (clock alignment)
        # every `type:` step as ground truth for the command bar: the exact
        # text, the wall-clock time each character landed, and the Enter.
        # The renderer draws the bar from this, never from the screen.
        self.typed: list[dict] = []
        self._scene_id = ""

    def _pause(self, seconds: float):
        if seconds <= 0:
            return
        start = time.time()
        time.sleep(seconds)
        self.protected.append((start, time.time()))

    # ---- low-level key delivery -------------------------------------------

    def _zellij(self, action_args: str, timeout: float = 15.0) -> str:
        return self.shell.run(
            f"zellij -s {shlex.quote(self.session)} action {action_args} </dev/null 2>&1",
            timeout=timeout,
        )

    def _tmux(self, args: str, timeout: float = 15.0) -> str:
        return self.shell.run(f"tmux {args} </dev/null 2>&1", timeout=timeout)

    def write_char(self, ch: str):
        if self.host == "tmux":
            # -H sends raw bytes as hex, which is the only form tmux does not
            # reinterpret: `send-keys -l ';'` silently sends nothing, because
            # tmux takes a bare ';' as its own command separator, so every
            # semicolon in a typed command was disappearing on camera
            hexed = " ".join(f"{b:02x}" for b in ch.encode())
            self._tmux(f"send-keys -t {shlex.quote(self.session)} -H -- {hexed}")
        else:
            self._zellij(f"write-chars -- {shlex.quote(ch)}")
        self.keystrokes.append(time.time())

    def press(self, key: str):
        codes = key_bytes(key) or KEYMAP[key]
        if self.host == "tmux":  # -H takes the raw bytes as hex
            self._tmux(f"send-keys -t {shlex.quote(self.session)} -H -- "
                       + " ".join(f"{b:02x}" for b in codes))
        else:
            self._zellij(f"write -- {' '.join(str(b) for b in codes)}")

    def dump_screen(self) -> str:
        if self.host == "tmux":
            return self._tmux(f"capture-pane -p -t {shlex.quote(self.session)}")
        return self._zellij("dump-screen")

    # ---- human typing model ------------------------------------------------

    def _char_delay(self, prev: str, ch: str) -> float:
        # the recording is real time and typing was 75% of it (744 s of a
        # 989 s take): the render fast-forwards any command that takes more
        # than a few seconds to type, so a brisk typist costs nothing on
        # screen and saves minutes per take. Pauses and waits are not scaled.
        d = self.rng.lognormvariate(math.log(0.075 / TYPE_SPEED), 0.5)
        d = min(max(d, 0.025), 0.4)
        if prev == " ":
            d += 0.03  # settling onto a new word
        if ch in "|&;<>$~{}[]()'\"`=/\\-":
            d += self.rng.uniform(0.04, 0.16) / TYPE_SPEED  # symbols are slower to reach
        return d / self.speed

    def type_text(self, text: str):
        prev = ""
        for ch in text:
            time.sleep(self._char_delay(prev, ch))
            self.write_char(ch)
            prev = ch

    # ---- waiting -----------------------------------------------------------

    def wait_for(self, cmd: Command, prompt_regex: str):
        wait_start = time.time()
        try:
            self._wait_for(cmd, prompt_regex)
        finally:
            self.waits.append((wait_start, time.time()))

    def _wait_for(self, cmd: Command, prompt_regex: str):
        wf = cmd.wait_for
        deadline = time.monotonic() + max(wf.timeout, MIN_WAIT_TIMEOUT)
        prompt_re = re.compile(prompt_regex)
        target_re = re.compile(wf.regex) if wf.regex else None
        last_screen, unchanged_since = None, time.monotonic()
        next_probe = 0.0
        while time.monotonic() < deadline:
            screen = self.dump_screen()
            if target_re and target_re.search(screen):
                break
            if wf.url and time.monotonic() >= next_probe:
                # a server started in the background: done when the URL answers
                if self._url_up(wf.url):
                    break
                next_probe = time.monotonic() + 2.0
            if wf.prompt and not target_re and not wf.stable and not wf.url:
                lines = [l.rstrip() for l in screen.splitlines() if l.strip()]
                if lines and prompt_re.search(lines[-1]):
                    break
            if wf.stable:
                # REPLs with right-side prompts/clocks: done once output stopped
                # changing for `stable` seconds (clock text is masked out)
                key = re.sub(r"\d{1,2}:\d{2}(:\d{2})?( ?[AP]M)?", "", screen).rstrip()
                if key != last_screen:
                    last_screen, unchanged_since = key, time.monotonic()
                elif time.monotonic() - unchanged_since >= wf.stable:
                    break
            time.sleep(0.5)
        else:
            screen = "\n".join(l.rstrip() for l in self.dump_screen().splitlines()
                               if l.strip())[-2500:]
            raise TimeoutError(
                f"did not finish within {max(wf.timeout, MIN_WAIT_TIMEOUT):.0f}s "
                f"(waiting for {'regex ' + repr(wf.regex) if wf.regex else 'url ' + wf.url if wf.url else 'the shell prompt'})"
                f"\n{screen}")
        time.sleep(wf.settle)

    def _url_up(self, url: str) -> bool:
        p = subprocess.run(["docker", "exec", self.container, "curl", "-s", "-o", "/dev/null",
                            "-m", "5", "-w", "%{http_code}", url], capture_output=True, text=True)
        return p.stdout.strip()[:1] in ("2", "3")

    def wait_for_session(self, timeout: float = 30.0):
        """`zellij action` blocks forever on a session that doesn't exist yet,
        so poll list-sessions before the first action."""
        deadline = time.monotonic() + timeout
        listing = ("tmux list-sessions -F '#S' </dev/null 2>&1" if self.host == "tmux"
                   else "zellij list-sessions -n </dev/null 2>&1")
        while time.monotonic() < deadline:
            out = self.shell.run(listing, timeout=10)
            if any(line.split()[:1] == [self.session] for line in out.splitlines()):
                return
            time.sleep(0.5)
        raise TimeoutError(f"{self.host} session {self.session!r} never appeared")

    def wait_for_first_prompt(self, prompt_regex: str, timeout: float = 60.0):
        self.wait_for_session()
        probe = Command(type="x", wait_for={"prompt": True, "timeout": timeout})
        self._wait_for(probe, prompt_regex)  # not a real wait: keep it out of self.waits

    # ---- screenplay execution ---------------------------------------------

    def start_web(self):
        if self.web is None:
            self.web = WebChannel(self.container)

    def run_web(self, cmd: Command):
        """Browser steps. The span counts as protected (played at 1x) so the
        renderer can map browser video time linearly onto it."""
        self.start_web()
        assert self.web is not None
        span_start = time.time()
        for act in cmd.web or []:
            payload = act.model_dump(exclude_none=True)
            payload["label"] = act.label or act.default_label()
            reply = self.web.send(payload)
            if not reply.get("ok"):
                raise TimeoutError(f"web action failed: {reply.get('error', '?')}\n"
                                   f"(action: {payload['label']})")
            self.web_actions.append(reply)
        span_end = time.time()
        self.web_spans.append((span_start, span_end))
        self.protected.append((span_start, span_end))

    def run_command(self, cmd: Command, prompt_regex: str):
        self._pause(cmd.pre_pause / self.speed)
        if cmd.web:
            self.run_web(cmd)
            self._pause(cmd.post_pause / self.speed)
            return
        if cmd.zellij:
            # layout change (split, focus...); a new pane gets focus and a fresh
            # shell, so wait for its prompt before moving on
            if self.host == "tmux":
                # tmux only hosts when the episode drives zellij itself, so a
                # layout step belongs to the zellij ON CAMERA: send its
                # keybinding, not a tmux pane command, which would move the
                # invisible host's focus and leave the demo where it was
                for key in _zellij_keys(cmd.zellij):
                    self.press(key)
                    time.sleep(0.25)
            else:
                self._zellij(cmd.zellij)
            if cmd.zellij.startswith("new-pane"):
                time.sleep(0.8)
                self.wait_for(cmd, prompt_regex)
            else:
                time.sleep(0.4)
            self._pause(cmd.post_pause / self.speed)
            return
        if cmd.type:
            n0 = len(self.keystrokes)
            started = time.time()
            self.type_text(cmd.type)
            enter_at = None
            if cmd.enter:
                # a beat before Enter: the viewer reads the finished command,
                # and the bar shows it complete. Protected, so the renderer
                # never compresses it away; at least ENTER_PAUSE_MIN.
                self._pause(max(ENTER_PAUSE_MIN,
                                self.rng.uniform(0.35, 1.1) / self.speed))
                self.press("Enter")
                enter_at = time.time()
            self.typed.append({"scene": self._scene_id, "start": started,
                               "end": time.time(), "enter": enter_at,
                               "text": cmd.type, "chars": list(self.keystrokes[n0:])})
        if cmd.keys:
            # a keypress leaves nothing on screen, so the bar is driven from
            # here instead: when it started, and what to draw
            started = time.time()
            for key in cmd.keys:
                time.sleep(self.rng.uniform(0.15, 0.5) / self.speed)
                self.press(key)
            self.keycasts.append((started, time.time(), keys_label(cmd.keys)))
        self.wait_for(cmd, prompt_regex)
        self._pause(cmd.post_pause / self.speed)

    def run_scene(self, scene: Scene, prompt_regex: str):
        start = time.time()
        self._scene_id = scene.id
        for cmd in scene.commands:
            what = cmd.type or (f"keys {cmd.keys}" if cmd.keys else
                                f"zellij {cmd.zellij}" if cmd.zellij else
                                "web: " + " -> ".join((a.label or a.default_label())
                                                      for a in (cmd.web or [])))
            if self.on_progress:
                self.on_progress(scene.id, what)
            try:
                self.run_command(cmd, prompt_regex)
            except TimeoutError as e:
                reason, _, screen = str(e).partition("\n")
                raise RecordError(scene.id, what, reason, screen) from None
        if scene.dwell:
            self._pause(scene.dwell)
        self.timeline.append(
            {"scene": scene.id, "narration": scene.narration,
             "start": start, "end": time.time()}
        )

    def perform(self, play: Screenplay):
        prompt_regex = play.meta.prompt_regex
        self.wait_for_first_prompt(prompt_regex)
        for scene in play.scenes:
            self.run_scene(scene, prompt_regex)

    def close(self):
        self.shell.close()
