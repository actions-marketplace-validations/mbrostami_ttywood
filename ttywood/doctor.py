"""`ttywood doctor`: is everything the pipeline needs installed and reachable?"""

from __future__ import annotations

import os
import shutil
import subprocess

from . import config


def _version(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (p.stdout or p.stderr).strip().splitlines()[0] if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def run_checks() -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, required: bool = True):
        checks.append({"name": name, "ok": bool(ok), "required": required, "detail": detail})

    for tool, why in (("docker", "every rehearsal and recording runs in a container"),
                      ("ffmpeg", "renders and muxes the video"),
                      ("ffprobe", "reads durations"),
                      ("agg", "turns the asciinema cast into video frames")):
        path = shutil.which(tool)
        add(tool, path, path or f"not on PATH ({why})")
    daemon = _version(["docker", "info", "--format", "{{.ServerVersion}}"]) if shutil.which("docker") else ""
    add("docker daemon", daemon, f"server {daemon}" if daemon else "not reachable (is Docker running?)")
    image = config.docker_image()
    has_image = bool(daemon) and subprocess.run(["docker", "image", "inspect", image],
                                                capture_output=True).returncode == 0
    add("episode image", has_image, image if has_image else f"{image} missing: run `ttywood build-image`")
    backend = os.environ.get("TTYWOOD_LLM", "claude")
    claude = shutil.which("claude")
    add("claude CLI", claude or backend != "claude",
        claude or "not on PATH (npm install -g @anthropic-ai/claude-code), or set TTYWOOD_LLM",
        required=backend == "claude")
    auth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    add("container auth", auth, "set" if auth else
        "export CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) or ANTHROPIC_API_KEY")
    for key in ("ui", "mono"):
        f = config.first_existing(f"fonts.{key}")
        add(f"font ({key})", f, f or f"none of fonts.{key} exists (Linux: apt install fonts-dejavu-core)")
    icons = config.first_existing("fonts.icons")
    add("font (icons)", icons, icons or "no Nerd Font: Shorts skip the GitHub mark", required=False)
    for var, use in (("GITHUB_TOKEN", "higher GitHub API limits"),
                     ("ELEVENLABS_API_KEY", "--tts elevenlabs"), ("OPENAI_API_KEY", "--tts openai")):
        add(var, os.environ.get(var), "set" if os.environ.get(var) else f"unset ({use})", required=False)
    from .publish import CLIENT_SECRET
    add("YouTube client", CLIENT_SECRET.exists(),
        str(CLIENT_SECRET) if CLIENT_SECRET.exists() else "no OAuth client (only needed for publish)",
        required=False)
    episodes = config.episodes_root()
    add("episodes dir", True, str(episodes), required=False)
    return checks
