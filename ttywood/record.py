"""Performance pass: fresh container, asciinema records a zellij session,
the typer engine replays the screenplay, timeline lands next to the cast."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import sandbox
from .config import docker_image
from .continuity import base_image, snapshot
from .screenplay import Screenplay, load_screenplay
from .typer_engine import TyperEngine

IMAGE = docker_image()


def _sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# tmux hosts invisibly: no status bar, no prefix key, nothing of ours on screen
# or in the keystream. Written into the container before recording starts.
TMUX_CONF = "\n".join((
    "set -g status off",
    "set -g escape-time 0",
    "set -g default-terminal screen-256color",
    "unbind C-b",
    "set -g pane-border-style fg=colour238",
    "set -g pane-active-border-style fg=colour240",
))
TMUX_CONF_PATH = "/tmp/ttywood-tmux.conf"


def _host_for(play: "Screenplay") -> str:
    """Which multiplexer hosts the recording. Normally zellij, whose status bar
    and pane frames are the default look. But an episode ABOUT a multiplexer
    cannot run inside itself: the outer session swallows the very keybindings
    being taught and two status bars fight for the screen. When the screenplay
    drives one, tmux hosts instead, with no status bar and no prefix bound, so
    the subject owns every key and every row."""
    if getattr(play.meta, "host", None):
        return play.meta.host
    typed = " ".join(c.type or "" for s in play.scenes for c in (s.commands or []))
    return "tmux" if re.search(r"(^|[|&;(\s])zellij(\s|$)", typed) else "zellij"


def record_episode(episode_dir: Path, screenplay_path: Path | None = None,
                   speed: float = 1.0, seed: int | None = None, on_progress=None,
                   play: "Screenplay | None" = None, cast_name: str = "demo.cast",
                   timeline_name: str = "timeline.json", tag: str = "rec") -> Path:
    """on_progress(kind, text): kind in status | command(scene_id) for live UI.

    `play` overrides the screenplay on disk (the vertical Short take passes a
    trimmed, narrow-terminal copy), and cast_name/timeline_name/tag keep that
    take's artifacts and container beside the main ones."""
    episode_dir = episode_dir.resolve()
    play = play or load_screenplay(screenplay_path or episode_dir / "screenplay.yaml")
    tell = on_progress or (lambda kind, text: None)
    cols, rows = play.meta.terminal.cols, play.meta.terminal.rows
    host = _host_for(play)
    container = f"ttywood-{tag}-{episode_dir.name}"
    cast_path = episode_dir / cast_name
    cast_path.unlink(missing_ok=True)
    if cast_name == "demo.cast":  # the main take owns web/; a Short take must not wipe it
        shutil.rmtree(episode_dir / "web", ignore_errors=True)

    subprocess.run(["docker", "rm", "-f", container],
                   capture_output=True)  # stale leftovers
    use_docker = _candidate_flag(episode_dir, "docker")
    priv = ["--privileged"] if play.meta.privileged or use_docker or _candidate_flag(episode_dir, "privileged") else []
    # an episode whose subject is an AI CLI has to be able to sign in on
    # camera, so the recording container gets the same auth the rehearsal
    # does -- as an env var, never typed or echoed into the recording
    auth = []
    if _candidate_flag(episode_dir, "needs_claude_auth"):
        from .rehearse import _auth_flags
        # the updater cannot write to the npm prefix in the image and would
        # print a red "Auto-update failed" strip in every session on camera
        auth = _auth_flags() + ["-e", "DISABLE_AUTOUPDATER=1"]
    cand = _candidate(episode_dir)
    auth += sandbox.env_flags(cand)

    image = base_image(episode_dir)
    if image != IMAGE:
        tell("status", f"starting from the previous episode's end state ({image})")
    _sh(["docker", "run", "-d", "--name", container, *priv,
         "-v", f"{episode_dir}:/episode", *sandbox.source_flags(cand), *auth,
         image, "sleep", "infinity"])
    # the viewer's git knows who they are; a commit typed on camera must not
    # end in "Please tell me who you are"
    subprocess.run(["docker", "exec", container, "bash", "-lc",
                    "git config --global user.name demo; "
                    "git config --global user.email demo@example.com"],
                   capture_output=True)
    sandbox.copy_source(container, cand)
    if _candidate_flag(episode_dir, "needs_claude_auth"):
        # The env token does authenticate the interactive TUI -- but only once
        # first-run onboarding is out of the way. Left alone, `claude` opens on
        # a theme picker, then "Select login method", then an OAuth code paste,
        # and the episode never reaches Claude Code at all. Marking onboarding
        # done off camera is the difference; no credential file is involved.
        subprocess.run(["docker", "exec", container, "bash", "-lc",
                        "python3 - <<'EOF'\n"
                        "import json, pathlib\n"
                        "p = pathlib.Path.home() / '.claude.json'\n"
                        "d = json.loads(p.read_text()) if p.exists() else {}\n"
                        "d.update({'hasCompletedOnboarding': True, 'theme': 'dark'})\n"
                        "p.write_text(json.dumps(d))\n"
                        "EOF"], capture_output=True)
        tell("status", "claude signed in from the environment; onboarding skipped")
    if _candidate_flag(episode_dir, "userns_sandbox"):
        from .rehearse import unlock_etc
        unlock_etc(container)
    if use_docker:
        from .rehearse import start_dockerd
        tell("status", f"docker daemon up inside the container ({start_dockerd(container)})")
    tell("status", f"container {container} up{' (privileged)' if priv else ''}, "
                   f"starting asciinema + {host} ({cols}x{rows})")
    engine = None
    try:
        # Start the recording: a detached exec with a TTY we size ourselves.
        inner = (f"tmux -f {TMUX_CONF_PATH} new-session -s demo" if host == "tmux"
                 else "zellij -s demo")
        setup = (f"printf %s {shlex.quote(TMUX_CONF)} > {TMUX_CONF_PATH}; "
                 if host == "tmux" else "")
        rec_cmd = (
            f"stty cols {cols} rows {rows}; {setup}"
            f"asciinema rec -q --overwrite -c '{inner}' /episode/{cast_name} "
            f"2>/episode/{Path(cast_name).stem}.log; "
            f"echo \"asciinema exit $?\" >> /episode/{Path(cast_name).stem}.log"
        )
        rec_start_wall = time.time()  # fallback; refined via the cast header
        _sh(["docker", "exec", "-d", "-t", container, "bash", "-lc", rec_cmd])

        engine = TyperEngine(container, session="demo", speed=speed, seed=seed, host=host)
        try:
            engine.wait_for_session(timeout=30)
        except TimeoutError:
            # zellij occasionally fails to come up on a cold container; one retry
            subprocess.run(["docker", "exec", container, "pkill", "-f", f"asciinema|{host}"],
                           capture_output=True)
            time.sleep(1)
            rec_start_wall = time.time()
            _sh(["docker", "exec", "-d", "-t", container, "bash", "-lc", rec_cmd])
            engine.wait_for_session(timeout=30)
        engine.wait_for_first_prompt(play.meta.prompt_regex, timeout=60)
        if play.has_web():
            tell("status", "launching headless Chromium for the web scenes")
            engine.start_web()  # launch Chromium before the first scene so no scene waits for it
        engine.on_progress = lambda scene_id, what: tell(scene_id, what)
        engine.perform(play)
        tell("status", "all scenes done, closing the session")
        web_video, web_end = engine.web.close() if engine.web else (None, 0.0)

        # End on camera: exit the shell, which closes the pane and the session,
        # which ends the recording.
        last_cmd = play.scenes[-1].commands[-1].type if play.scenes[-1].commands else ""
        if (last_cmd or "").strip() != "exit":
            time.sleep(0.8)
            engine.type_text("exit")
            engine.press("Enter")
        # with several panes each one needs its own exit; keep going until the
        # session (and with it asciinema) is gone. A pane may still be running
        # a foreground server (`moon run api:dev`), so stop it first.
        for _ in range(6):
            try:
                _wait_recording_finished(container, timeout=6)
                break
            except TimeoutError:
                engine.press("C-c")
                time.sleep(0.8)
                engine.type_text("exit")
                engine.press("Enter")
        try:
            _wait_recording_finished(container, timeout=30)
        except TimeoutError:
            # An episode that leaves servers running in several panes can
            # outlast the exit rounds above. The take itself is already on
            # disk (a cast is line-delimited JSON, complete up to this point),
            # so end the session from outside rather than throw a good
            # recording away over a missing footer.
            tell("status", "panes still alive; closing the session from outside")
            subprocess.run(["docker", "exec", container, "bash", "-lc",
                            "tmux kill-server 2>/dev/null; zellij ka -y 2>/dev/null; "
                            "pkill -f asciinema"], capture_output=True)
            time.sleep(2)
            if not cast_path.exists() or cast_path.stat().st_size < 1000:
                raise
        header_ts = _cast_start_timestamp(cast_path, fallback=rec_start_wall)
        # the header timestamp is a whole second: refine it by lining up the
        # typed characters with their echoes in the cast (sub-50 ms accuracy)
        header_ts = _align_clock(cast_path, header_ts, engine.keystrokes)
        timeline = [
            {"scene": t["scene"], "narration": t["narration"],
             "start": round(t["start"] - header_ts, 2),
             "end": round(t["end"] - header_ts, 2)}
            for t in engine.timeline
        ]
        rel = lambda ivs: [[round(a - header_ts, 2), round(b - header_ts, 2)] for a, b in ivs]
        data = {"scenes": timeline, "protected": rel(engine.protected),
                "waits": rel(engine.waits),
                "keys": [[round(a - header_ts, 2), round(b - header_ts, 2), lbl]
                         for a, b, lbl in engine.keycasts],
                # what was typed and when, character by character: the command
                # bar's source of truth (see cinema._typed_lookup)
                "typed": [{"scene": e["scene"], "text": e["text"],
                           "start": round(e["start"] - header_ts, 3),
                           "end": round(e["end"] - header_ts, 3),
                           "enter": round(e["enter"] - header_ts, 3) if e["enter"] else None,
                           "chars": [round(c - header_ts, 3) for c in e["chars"]]}
                          for e in engine.typed]}
        if engine.web:
            # browser video time = cast time - t0; t0 = (recording end) - (video length)
            video_rel = f"web/{Path(web_video).name}" if web_video else None
            if not video_rel:  # close() failed: take the webm Chromium left behind
                found = sorted((episode_dir / "web").glob("*.webm"))
                if found:
                    video_rel = f"web/{found[-1].name}"
                    tell("status", f"browser close timed out; using {video_rel} as recorded")
            t0 = engine.web.t0
            if video_rel and (episode_dir / video_rel).exists():
                t0 = web_end - _video_duration(episode_dir / video_rel, fix=True)
            data["web"] = {
                "video": video_rel,
                "t0": round(t0 - header_ts, 3),
                "spans": rel(engine.web_spans),
                "actions": [{"label": a.get("label", ""), "action": a.get("action"),
                             "start": round(a["start"] - header_ts, 3),
                             "end": round(a["end"] - header_ts, 3),
                             "keys": [round(k - header_ts, 3) for k in a.get("keys", [])],
                             "clicks": [round(c - header_ts, 3) for c in a.get("clicks", [])]}
                            for a in engine.web_actions]}
        (episode_dir / timeline_name).write_text(json.dumps(data, indent=2))
        if cast_name == "demo.cast":  # the main take is the state the viewer saw
            snapshot(container, episode_dir, tell)
        return cast_path
    finally:
        if engine:
            engine.close()
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def _video_duration(path: Path, fix: bool = False) -> float:
    """Seconds of video. An unfinalized webm (Chromium killed before it wrote
    the header) reports no duration; with fix=True it is remuxed in place
    into a finalized file first."""
    probe = lambda: subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    out = probe()
    if (not out or out == "N/A") and fix:
        tmp = path.with_suffix(".fixed.webm")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-c", "copy", str(tmp)],
                       capture_output=True)
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(path)
            out = probe()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _candidate(episode_dir: Path) -> dict:
    try:
        return json.loads((episode_dir / "candidate.json").read_text())
    except (OSError, ValueError):
        return {}


def _candidate_flag(episode_dir: Path, key: str) -> bool:
    return bool(_candidate(episode_dir).get(key))


def _wait_recording_finished(container: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = subprocess.run(["docker", "exec", container, "pgrep", "-f", "asciinema"],
                           capture_output=True)
        if p.returncode != 0:
            return
        time.sleep(0.5)
    raise TimeoutError("asciinema did not finish writing the cast")


def _align_clock(cast_path: Path, header_ts: float, keystrokes: list[float]) -> float:
    """Find the cast start time (wall clock) that best explains where the typed
    characters' echoes landed in the cast: scan offsets around the header's
    whole-second timestamp and keep the one with the most keystrokes matched
    by an output event within 60 ms."""
    if len(keystrokes) < 5:
        return header_ts
    try:
        events = [json.loads(l)[0] for l in cast_path.read_text().splitlines()[1:] if l.strip()]
    except Exception:
        return header_ts
    import bisect
    keys = keystrokes[:400]

    def score(ts: float) -> int:
        hits = 0
        for k in keys:
            t = k - ts
            i = bisect.bisect_left(events, t)
            if (i < len(events) and events[i] - t <= 0.06) or (i > 0 and t - events[i - 1] <= 0.02):
                hits += 1
        return hits

    best_ts, best = header_ts, -1
    for step in range(-150, 151):  # +-1.5 s in 10 ms steps
        ts = header_ts + step / 100.0
        s = score(ts)
        if s > best:
            best_ts, best = ts, s
    return best_ts


def _cast_start_timestamp(cast_path: Path, fallback: float) -> float:
    try:
        header = json.loads(cast_path.read_text().splitlines()[0])
        return float(header["timestamp"])
    except Exception:
        return fallback
