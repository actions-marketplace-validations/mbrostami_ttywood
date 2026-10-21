"""Rehearsal pass: Claude Code runs headless *inside* the container, actually
tries the tool, and writes notes.md + screenplay.yaml into the episode dir.
The rehearsal container is destroyed afterwards — none of it is on camera.

Auth: the in-container Claude needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN
in the host environment (create the latter once with `claude setup-token`).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import sandbox
from .config import docker_image, load_prompt
from .continuity import base_image, continuity_brief
from .screenplay import validation_errors

IMAGE = docker_image()
MAX_FIX_ROUNDS = 2
MAX_CONTINUE_ROUNDS = 3
AUTH_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def _sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def _auth_flags() -> list[str]:
    flags: list[str] = []
    for var in AUTH_VARS:
        if os.environ.get(var):
            flags += ["-e", f"{var}={os.environ[var]}"]
    if flags:
        flags += ["-e", "DISABLE_AUTOUPDATER=1"]
    if not flags:
        raise RuntimeError(
            "no Claude auth for the container: export ANTHROPIC_API_KEY, or run "
            "`claude setup-token` once and export CLAUDE_CODE_OAUTH_TOKEN")
    return flags


def _run_claude(container: str, prompt_file: str, log_path: Path,
                continue_session: bool = False, on_event=None,
                stats: dict | None = None) -> int:
    """Run Claude Code in the container and stream what it does: every
    tool call (the commands it runs, files it writes) and its text go to the
    log as readable lines and, if given, to on_event(kind, text) with kind in
    command | write | read | tool | text | result. `stats`, if given, is
    updated with the run's cost_usd, tokens, turns and seconds (summed over
    calls), the numbers that say what a check costs."""
    flags = "-c " if continue_session else ""
    # the prompt goes in over stdin, not argv: with it on the command line a
    # `pkill -f <tool>` inside the rehearsal matches Claude's own process
    cmd = (f"cd /home/demo && claude {flags}-p "
           f"--dangerously-skip-permissions --output-format stream-json --verbose < {prompt_file}")
    emit = on_event or (lambda kind, text: None)
    with open(log_path, "a") as log, subprocess.Popen(
            ["docker", "exec", container, "bash", "-lc", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
        assert p.stdout is not None
        for raw in p.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                log.write(raw)
                continue
            for kind, text in _events(ev):
                log.write(f"[{kind}] {text}\n")
                log.flush()
                emit(kind, text)
            if stats is not None and ev.get("type") == "result":
                usage = ev.get("usage") or {}
                stats["cost_usd"] = round(stats.get("cost_usd", 0) + (ev.get("total_cost_usd") or 0), 4)
                stats["turns"] = stats.get("turns", 0) + (ev.get("num_turns") or 0)
                stats["seconds"] = round(stats.get("seconds", 0) + (ev.get("duration_ms") or 0) / 1000, 1)
                stats["tokens_in"] = stats.get("tokens_in", 0) + (usage.get("input_tokens") or 0) \
                    + (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                stats["tokens_out"] = stats.get("tokens_out", 0) + (usage.get("output_tokens") or 0)
                log.write(f"[cost] ${stats['cost_usd']} {stats['tokens_in']} in / {stats['tokens_out']} out, "
                          f"{stats['turns']} turns, {stats['seconds']} s\n")
        p.wait(timeout=3600)
    return p.returncode


def _events(ev: dict):
    """Readable (kind, text) pairs out of one stream-json event."""
    if ev.get("type") == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                yield "text", block["text"].strip()
            elif block.get("type") == "tool_use":
                name, inp = block.get("name", ""), block.get("input", {})
                if name == "Bash":
                    yield "command", inp.get("command", "")
                elif name in ("Write", "Edit", "MultiEdit"):
                    yield "write", inp.get("file_path", "")
                elif name in ("Read", "Grep", "Glob", "WebFetch"):
                    yield "read", inp.get("file_path") or inp.get("pattern") or inp.get("url", "")
                else:
                    yield "tool", name
    elif ev.get("type") == "result":
        yield "result", (ev.get("result") or "")[:2000]


def unlock_etc(container: str) -> None:
    """Swap Docker's /etc/hosts, /etc/hostname and /etc/resolv.conf bind
    mounts for plain files with the same contents, off camera. Tools that
    stack an overlayfs on /etc from a new user namespace (drop) fail with
    `overlayfs: failed to clone lowerpath`: in that namespace the three
    bind mounts are locked children of /etc, and the kernel refuses them."""
    subprocess.run(["docker", "exec", "-u", "root", container, "bash", "-c",
                    "for f in hosts hostname resolv.conf; do "
                    "cp /etc/$f /tmp/.$f && umount /etc/$f && cp /tmp/.$f /etc/$f; "
                    "rm -f /tmp/.$f; done"],
                   capture_output=True)


def start_dockerd(container: str, timeout: float = 60) -> str:
    """Bring up a Docker daemon inside a --privileged episode container
    (docker-first projects: `docker run`, `docker compose up`). Storage is
    vfs: overlay2 is refused on top of the outer overlay, and fuse-overlayfs
    starts but containers fail with `exec: invalid argument` on Docker
    Desktop's kernel. vfs copies layers (slower pulls, more disk) but runs
    everything. Returns the storage driver; raises if no daemon comes up."""
    for driver in ("vfs",):
        subprocess.run(["docker", "exec", container, "sudo", "pkill", "-x", "dockerd"], capture_output=True)
        cmd = (f"sudo nohup dockerd --storage-driver={driver} --iptables=true "
               f"> /tmp/dockerd.log 2>&1 &")
        subprocess.run(["docker", "exec", container, "bash", "-lc", cmd], capture_output=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            p = subprocess.run(["docker", "exec", container, "docker", "info"], capture_output=True, text=True)
            if p.returncode == 0:
                return driver
            time.sleep(2)
    log = subprocess.run(["docker", "exec", container, "tail", "-20", "/tmp/dockerd.log"],
                         capture_output=True, text=True).stdout
    raise RuntimeError(f"dockerd did not start in {container}:\n{log[-1500:]}")


# Episode structures: rotated so consecutive episodes don't share a skeleton
# (YouTube's "inauthentic content" policy targets template-shaped series).
STRUCTURES = {
    "tour": "Classic tour: clone, install, first run, then the 2-3 features that earn "
            "the stars, one rough edge shown honestly, verdict.",
    "bug-hunt": "Bug hunt: open with the claim from the README, install, then go "
                "looking for where it breaks: edge cases, large or odd but legitimate "
                "inputs, the repo's own examples. Two or three real findings drive the "
                "episode (never a staged mistake that gets fixed on camera); end with "
                "what still works well and the verdict.",
    "versus": "Versus: name the tool people use today for this job (the incumbent), "
              "install the new one, and do the same 3 tasks the incumbent does. Each "
              "scene is one task, side by side in words. Verdict: switch or not.",
    "speedrun": "Speedrun: how far can you get in 5 minutes from a clean machine? "
                "Tight scenes, each with a concrete goal (installed, first output, "
                "one real result, one integration). Verdict on what the 5 minutes "
                "proved and what it didn't.",
    "mystery": "Mystery first: open on the most surprising output or feature (show "
               "it before explaining it), then rewind: clone, install, and build back "
               "up to that moment. End with the catch and the verdict.",
}

ENV_DOCKER = """A Docker daemon is running in this container and in the recording container:
`docker run`, `docker compose up` and the project's Docker install path work
and should be followed exactly as the README documents them."""

ENV_NO_DOCKER = """There is NO Docker daemon here and none will be in the recording container
(the `docker` CLI exists but nothing listens on the socket). This is a limit
of OUR environment, not a fact about the project, so it must never appear on
camera: do not run a `docker` command to show it failing, do not say "the
README says Docker but there is no Docker here", do not count it as a finding
or a rough edge. If the README's first install path is Docker, pick the next
documented path (binary release, npm, pip, cargo, source build) and present
it as the path for a machine without Docker, in one neutral sentence. If NO
non-Docker path is documented, write notes.md saying so, set
`meta.blocked: "needs docker"` in the screenplay and stop; the pipeline will
rerun with a daemon."""

SOURCE_TEMPLATE = """## THE PROJECT IS ALREADY HERE

This project is not cloned from anywhere: its source is in `~/{name}` in this
container and will be in the same place, untouched, when the recording
starts. Do not `git clone` it and do not look for it on GitHub. Build and
install it from `~/{name}` the way its README says. Scene one opens with
`cd ~/{name}` and a look at what is there, then the build or install.
"""

STYLE_TEMPLATES = {
    "review": "",
    "showcase": """## STYLE: SHOWCASE (overrides the reviewer tone below)

This video is the project's own demo: it shows what the tool does, at its
best, to someone deciding whether to try it. Every honesty rule still holds
(real commands, real output, nothing staged or faked, no claim the session
does not show), but it is not a review: notes.md still records what broke,
the narration does not hunt for flaws, and there is no "rough edge" scene. In
the verdict, `use_cases` are who it is for; `bottom_line` is what it is.
""",
}

FOCUS_TEMPLATE = """## EPISODE FOCUS (overrides the generic story arc below)

{focus}

The episode still opens with clone + install + a first run so viewers can
follow from scratch, but the middle scenes (the "best features" part) must be
about the focus above, not a generic tour. If the focus needs extra software
(a cluster, a database, a second tool), set it up in this container within
what THIS ENVIRONMENT above allows (kind/k3d are not available; Docker only
when the environment section says a daemon is running); otherwise prefer
lightweight local substitutes (a mock server, sqlite, `--dry-run`, generated
manifests) and say so in the narration as a choice, never as a sandbox
failure. If the focus is genuinely impossible here, demo the closest real
thing and explain the gap in notes.md and the outro.
"""


def pick_structure(demos_root: Path) -> str:
    """Least-recently-used structure across existing episodes (ties broken
    randomly), so consecutive episodes never share a skeleton."""
    import random
    recent: list[str] = []
    for cand in sorted(demos_root.glob("*/candidate.json"), key=lambda p: p.stat().st_mtime):
        try:
            s = json.loads(cand.read_text()).get("structure")
            if s in STRUCTURES:
                recent.append(s)
        except Exception:
            pass
    unused = [s for s in STRUCTURES if s not in recent[-(len(STRUCTURES) - 1):]]
    return random.choice(unused or list(STRUCTURES))


def fix_screenplay(episode_dir: Path, scene: str, command: str, reason: str,
                   screen: str, log_path: Path | None = None):
    """A recording failed: let Claude (host-side, with the rehearsal notes as
    context) repair screenplay.yaml so the same failure can't recur."""
    episode_dir = episode_dir.resolve()
    prompt = f"""The recording of screenplay.yaml (in this directory) failed while
replaying it keystroke-by-keystroke in a fresh container.

Failed scene: {scene}
Failed command: {command}
Why: {reason}

What the terminal showed at that moment:
```
{screen}
```

How waiting works: `wait_for: {{prompt: true}}` succeeds only when the LAST
non-empty line of the screen matches `meta.prompt_regex` (default
`^demo .*\\$ ?$`, the bash prompt `demo ~/dir $`). Inside another REPL or
shell (nu, python, psql, a TUI...) that never matches: use
`wait_for: {{regex: ...}}` matching that REPL's own prompt at the end of the
screen, or widen `meta.prompt_regex` to an alternation. Many REPL prompts have
a right-aligned clock/status on the same line, so a `$`-anchored prompt regex
never matches; the robust choice for a REPL step is `wait_for: {{stable: 2}}`
(done when the screen stopped changing for 2 s). `regex` is searched over the
whole visible screen, so make it something that only appears once the command
has finished.

Diagnose the real cause (typical: an interactive prompt like a trust/confirm
question, a command run from the wrong directory, a regex that never appears,
a `prompt: true` wait inside a REPL, a step that assumed rehearsal state) and
fix screenplay.yaml IN PLACE with the Edit tool. Keep the story, narration and scene ids; change as little as
necessary — typically the failing command, its neighbours, or its wait_for.
Nothing in the screenplay may wait for interactive input; prefer
non-interactive flags/env or running from a directory where the prompt can't
trigger. notes.md and rehearse_prompt.md here contain what was learned in
rehearsal. Reply with one line describing the fix."""
    play_path = episode_dir / "screenplay.yaml"
    before = play_path.read_text() if play_path.exists() else ""
    p = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", "Read,Edit,Write,Grep,Glob"],
        cwd=episode_dir, capture_output=True, text=True, timeout=900)
    if log_path:
        with open(log_path, "a") as log:
            log.write(f"\n=== fix_screenplay ({scene} / {command}) ===\n{p.stdout}{p.stderr}\n")
    after = play_path.read_text() if play_path.exists() else ""
    # a non-zero exit with an edited, valid screenplay is still a usable repair
    # (the CLI also exits non-zero on unrelated settings warnings)
    if p.returncode != 0 and after == before:
        raise RuntimeError(f"claude fix_screenplay failed (exit {p.returncode}): "
                           f"{(p.stderr or p.stdout)[-1500:]}")
    err = validation_errors(play_path)
    if err:
        raise RuntimeError(f"fixed screenplay is invalid: {err}")
    if after == before:
        raise RuntimeError("fix_screenplay changed nothing; the screenplay still fails")
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "(no summary)"


def rehearse_episode(episode_dir: Path, focus: str | None = None,
                     lang: str | None = None, on_event=None):
    from .screenplay import LANGUAGES

    episode_dir = episode_dir.resolve()
    cand_path = episode_dir / "candidate.json"
    candidate = json.loads(cand_path.read_text())
    if focus:
        candidate["focus"] = focus
    if lang:
        candidate["lang"] = lang
    if focus or lang:
        cand_path.write_text(json.dumps(candidate, indent=2, ensure_ascii=False))
    focus = candidate.get("focus")
    lang = candidate.get("lang", "en")
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; choose from {sorted(LANGUAGES)}")
    structure = candidate.get("structure") or pick_structure(episode_dir.parent)
    candidate["structure"] = structure
    cand_path.write_text(json.dumps(candidate, indent=2, ensure_ascii=False))
    container = f"ttywood-reh-{episode_dir.name}"
    log_path = episode_dir / "rehearse.log"

    style = candidate.get("style", "review")
    if style not in STYLE_TEMPLATES:
        raise ValueError(f"unknown style {style!r}; choose from {sorted(STYLE_TEMPLATES)}")
    source = bool(candidate.get("source"))
    prompt = load_prompt("rehearse.md")
    for key, val in (("SOURCE_STEP",
                      f"The source is already in ~/{sandbox.source_name(candidate)}: build and install "
                      "it from there the way the README recommends." if source else
                      "Clone it into ~/ and install it the way the README recommends."),
                     ("SOURCE_SECTION",
                      SOURCE_TEMPLATE.format(name=sandbox.source_name(candidate)) if source else ""),
                     ("STYLE_SECTION", STYLE_TEMPLATES[style]),
                     ("SUBJECT", f"{candidate.get('repo', '')} ({candidate.get('url', '')})"
                      if candidate.get("repo") else
                      f"a local project in ~/{sandbox.source_name(candidate)}"),
                     ("REPO", candidate.get("repo", "")),
                     ("URL", candidate.get("url", "")),
                     ("TOOL", candidate.get("tool", "")),
                     ("ANGLE", candidate.get("angle", "")),
                     ("LANG", lang), ("LANGUAGE", LANGUAGES[lang]),
                     ("STRUCTURE_NAME", structure), ("STRUCTURE_BRIEF", STRUCTURES[structure]),
                     ("FOCUS_SECTION", FOCUS_TEMPLATE.format(focus=focus) if focus else ""),
                     ("SERIES_SECTION",
                      "## THIS EPISODE'S PLACE IN THE SERIES\n\n"
                      + candidate["series"]["brief"] + continuity_brief(episode_dir)
                      if candidate.get("series") else "")):
        prompt = prompt.replace("{{" + key + "}}", str(val))
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    if "docker" not in candidate:
        # the README leads with Docker -> the container gets a daemon, so the
        # episode never opens on "no Docker here", which would be our failure
        from .plan import readme_wants_docker
        candidate["docker"] = readme_wants_docker(candidate.get("repo", ""), candidate.get("source", ""))
        cand_path.write_text(json.dumps(candidate, indent=2, ensure_ascii=False))
        if candidate["docker"] and on_event:
            on_event("text", "README leads with Docker: running the container with a Docker daemon inside")
    use_docker = bool(candidate.get("docker"))
    prompt = prompt.replace("{{ENVIRONMENT}}", ENV_DOCKER if use_docker else ENV_NO_DOCKER)
    (episode_dir / "rehearse_prompt.md").write_text(prompt)
    priv = ["--privileged"] if candidate.get("privileged") or use_docker else []
    image = base_image(episode_dir)
    if image != IMAGE and on_event:
        on_event("text", f"starting from the previous episode's end state ({image})")
    _sh(["docker", "run", "-d", "--name", container, *priv,
         "-v", f"{episode_dir}:/episode", *sandbox.source_flags(candidate),
         *_auth_flags(), *sandbox.env_flags(candidate),
         image, "sleep", "infinity"])
    subprocess.run(["docker", "exec", container, "bash", "-lc",
                    "git config --global user.name demo; "
                    "git config --global user.email demo@example.com"],
                   capture_output=True)
    sandbox.copy_source(container, candidate)
    if candidate.get("userns_sandbox"):
        unlock_etc(container)
    # a rerun must never inherit the previous rehearsal's files: with them in
    # place an early-stopping Claude looks like a success
    for stale in ("screenplay.yaml", "notes.md"):
        (episode_dir / stale).unlink(missing_ok=True)
    try:
        if use_docker:
            driver = start_dockerd(container)
            if on_event:
                on_event("text", f"docker daemon up inside the container ({driver})")
        rc = _run_claude(container, "/episode/rehearse_prompt.md", log_path, on_event=on_event)
        if rc != 0:
            raise RuntimeError(f"rehearsal claude exited {rc}; see {log_path}")
        # Claude sometimes ends its turn mid-task ("still waiting on the
        # background timing check"); continue the same session until both
        # files exist, a few times, before calling it a failure
        for round_ in range(MAX_CONTINUE_ROUNDS):
            if (episode_dir / "screenplay.yaml").exists() and (episode_dir / "notes.md").exists():
                break
            if on_event:
                on_event("text", f"rehearsal ended without notes.md + screenplay.yaml; "
                                 f"asking Claude to continue (round {round_ + 1})")
            cont = episode_dir / "continue_prompt.md"
            cont.write_text("You stopped before finishing. Continue the rehearsal from where you "
                            "were: wait for or rerun whatever was pending, then write "
                            "/episode/notes.md and /episode/screenplay.yaml as the brief asks. "
                            "Do not end your turn until both files are written.\n")
            _run_claude(container, "/episode/continue_prompt.md", log_path,
                        continue_session=True, on_event=on_event)

        # validate the screenplay; give Claude a couple of rounds to fix it
        for round_ in range(MAX_FIX_ROUNDS + 1):
            play_path = episode_dir / "screenplay.yaml"
            if not play_path.exists():
                err: str | None = "screenplay.yaml was not written to /episode/"
            else:
                err = validation_errors(play_path)
            if err is None:
                break
            if round_ == MAX_FIX_ROUNDS:
                raise RuntimeError(f"screenplay still invalid: {err}")
            fix = episode_dir / "fix_prompt.md"
            fix.write_text(
                "The screenplay at /episode/screenplay.yaml failed validation.\n"
                "Fix it in place, keeping the schema from the original brief.\n\n"
                f"Validation errors:\n{err}\n")
            if on_event:
                on_event("text", f"screenplay invalid (round {round_ + 1}): {err[:200]}; "
                                 "asking Claude to fix it")
            _run_claude(container, "/episode/fix_prompt.md", log_path,
                        continue_session=True, on_event=on_event)

        if not (episode_dir / "notes.md").exists():
            raise RuntimeError("rehearsal finished but notes.md is missing")
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
