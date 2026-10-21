"""Plugins: separately installed packages that extend ttywood without
forking it. A plugin is a module registered under the `ttywood.plugins`
entry-point group:

    # the plugin's pyproject.toml
    [project.entry-points."ttywood.plugins"]
    mything = "mything.plugin"

Every attribute is optional:

    CONFIG_FILES: list[Path]   config files merged over the defaults
    PROMPT_DIRS:  list[Path]   searched for prompts before the built-in ones
    def register(cli): ...     add commands or options to the click group
    def discover(episodes_root: Path, *, prefer: str, log, **options) -> Path:
        pick a subject on its own and create the episode directory;
        `ttywood run` calls it when no EPISODE, --repo, --path or --series
        is given. `options` are the extra `run` options the plugin added.
    def after_publish(episode_dir: Path, log) -> None:
        runs after `run --publish` uploaded the episode

TTYWOOD_NO_PLUGINS=1 disables them all (handy for tests and bug reports)."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType


@lru_cache(maxsize=1)
def loaded() -> tuple[ModuleType, ...]:
    if os.environ.get("TTYWOOD_NO_PLUGINS"):
        return ()
    from importlib.metadata import entry_points
    mods = []
    for ep in entry_points(group="ttywood.plugins"):
        try:
            mods.append(ep.load())
        except Exception as e:  # a broken plugin must not take the CLI down
            print(f"ttywood: plugin {ep.name!r} failed to load: {e}", file=sys.stderr)
    return tuple(mods)


def plugin_config_files() -> list[Path]:
    return [Path(p) for m in loaded() for p in getattr(m, "CONFIG_FILES", [])]


def plugin_prompt_dirs() -> list[Path]:
    return [Path(p) for m in loaded() for p in getattr(m, "PROMPT_DIRS", [])]


def register_all(cli) -> None:
    for m in loaded():
        if hasattr(m, "register"):
            m.register(cli)


def discover_provider():
    """The first plugin's `discover`, or None."""
    for m in loaded():
        if hasattr(m, "discover"):
            return m.discover
    return None


def after_publish(episode_dir: Path, log) -> None:
    for m in loaded():
        if hasattr(m, "after_publish"):
            m.after_publish(episode_dir, log)
