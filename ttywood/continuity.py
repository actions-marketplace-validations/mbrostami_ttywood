"""Series continuity: an episode starts exactly where the previous one ended.

Every take normally begins from the clean image, so nothing an earlier episode
built (a cloned repo, a CLAUDE.md, Claude's own memory) exists when the next
one opens. For a series that is wrong: the viewer left the last episode in a
particular state and expects to find it again. So the main take of a series
episode is committed as an image, ttywood:<slug>-eNN, and the next episode's
rehearsal and recording both start from it. A re-recorded episode re-snapshots,
so later episodes never inherit a state the camera did not see.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import docker_image

CLEAN_IMAGE = docker_image()


def _series(episode_dir: Path) -> dict | None:
    try:
        return json.loads((episode_dir / "candidate.json").read_text()).get("series")
    except (OSError, ValueError):
        return None


def snapshot_tag(episode_dir: Path) -> str | None:
    """The image this episode's finished main take is saved as (series only)."""
    s = _series(episode_dir)
    if not s or not s.get("slug") or not s.get("n"):
        return None
    return f"ttywood:{s['slug']}-e{int(s['n']):02d}"


def image_exists(tag: str) -> bool:
    p = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    return p.returncode == 0


def base_image(episode_dir: Path) -> str:
    """Where this episode starts: the previous series episode's snapshot when
    there is one, otherwise the clean image."""
    s = _series(episode_dir)
    if s and s.get("slug") and int(s.get("n") or 0) > 1:
        prev = f"ttywood:{s['slug']}-e{int(s['n']) - 1:02d}"
        if image_exists(prev):
            return prev
    return CLEAN_IMAGE


def continuity_brief(episode_dir: Path) -> str:
    """Tell the rehearsing agent that the previous episode's state is present."""
    base = base_image(episode_dir)
    if base == CLEAN_IMAGE:
        return ""
    return (
        "\n\nCONTINUITY: this container is a snapshot of the previous episode's "
        "recording, taken the moment its last command finished. Every file, "
        "directory, repo clone, config and Claude memory that episode created is "
        "already here, exactly as the viewer last saw it (look around with ls "
        "and cat before assuming anything). Do NOT re-clone, re-install or "
        "re-create what already exists, and do not tidy it up: the episode "
        "opens in that state and continues from it. If the previous episode "
        "left an interactive program running it is NOT running now; only the "
        "filesystem carried over. This overrides anything above about restoring "
        "or rebuilding earlier episodes' work in one command, including the "
        "'assumes' line: what earlier episodes built is not restored, it is "
        "simply here. The opening may show it (a version, a grep of the config, "
        "an ls) so a viewer starting here knows what is on screen, but it must "
        "not reinstall the tool, rewrite its config or re-create files that "
        "exist.\n"
    )


def snapshot(container: str, episode_dir: Path, tell=None) -> str | None:
    """Commit the finished main-take container as this episode's image."""
    tag = snapshot_tag(episode_dir)
    if not tag:
        return None
    if tell:
        tell("status", f"saving the end state as {tag} for the next episode")
    # The multiplexer that hosted this take leaves a dead session behind
    # (zellij: "demo already exists, but is dead"), and the next take would
    # fail to open its own. That runtime state is the camera's, not the
    # episode's, so it is dropped; the viewer's files are untouched.
    # Only session state goes: zellij keeps the resurrectable session list in
    # contract_version_*/session_info and per-session plugin data in UUID
    # directories, but the same cache also records that the release notes
    # were already shown (<version>/seen_release_notes), and the viewer's
    # machine would keep that, so it stays.
    subprocess.run(["docker", "exec", container, "bash", "-lc",
                    "rm -rf /tmp/zellij-* /tmp/tmux-* /tmp/ttywood-* ~/.cache/tmux "
                    "~/.cache/zellij/contract_version_* 2>/dev/null; "
                    "find ~/.cache/zellij -maxdepth 1 -type d -regextype posix-extended "
                    "-regex '.*/[0-9a-f-]{36}' -exec rm -rf {} + 2>/dev/null; true"],
                   capture_output=True)
    subprocess.run(["docker", "commit", "-c", 'CMD ["sleep", "infinity"]', container, tag],
                   check=True, capture_output=True)
    return tag
