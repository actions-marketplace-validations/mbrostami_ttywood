"""Settings: the shipped defaults.toml, overridden key by key by config files
from plugins, the user's ~/.config/ttywood/config.toml, ./ttywood.toml and
$TTYWOOD_CONFIG (later wins). See defaults.toml for every key.

Everything that makes an episode look like one particular channel (its name,
logo, welcome line, the pitch the prompts use) lives in config, so the engine
itself carries no branding. `get("brand.name")` reads one value."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PACKAGE = Path(__file__).resolve().parent
DEFAULTS = PACKAGE / "defaults.toml"
USER_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ttywood"

# keys holding paths (or lists of paths): resolved against the directory of
# the file that set them, with ~ expanded
PATH_KEYS = {"paths.episodes", "paths.series", "paths.prompts", "paths.sfx", "paths.env_files",
             "brand.logo", "fonts.ui", "fonts.mono", "fonts.icons"}

_cache: dict | None = None
_sources: list[Path] = []


def _resolve_paths(data: dict, base: Path, prefix: str = "") -> None:
    for key, val in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(val, dict):
            _resolve_paths(val, base, dotted + ".")
        elif dotted in PATH_KEYS:
            fix = lambda p: str((base / Path(p).expanduser()).resolve()) if p else p
            data[key] = [fix(p) for p in val] if isinstance(val, list) else fix(val)


def _merge(into: dict, new: dict) -> None:
    for key, val in new.items():
        if isinstance(val, dict) and isinstance(into.get(key), dict):
            _merge(into[key], val)
        else:
            into[key] = val


def config_files() -> list[Path]:
    """Every config file that applies, lowest priority first."""
    from .plugins import plugin_config_files
    files = [DEFAULTS, *plugin_config_files(), USER_DIR / "config.toml", Path.cwd() / "ttywood.toml"]
    env = os.environ.get("TTYWOOD_CONFIG", "")
    files += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    seen, out = set(), []
    for f in files:
        f = f.resolve()
        if f.is_file() and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def load(reload: bool = False) -> dict:
    global _cache, _sources
    if _cache is not None and not reload:
        return _cache
    merged: dict = {}
    _sources = config_files()
    for f in _sources:
        with open(f, "rb") as fh:
            data = tomllib.load(fh)
        _resolve_paths(data, f.parent)
        _merge(merged, data)
    _cache = merged
    return merged


def sources() -> list[Path]:
    load()
    return list(_sources)


def get(dotted: str, default: Any = None) -> Any:
    node: Any = load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def path(dotted: str) -> Path | None:
    val = get(dotted)
    return Path(val).expanduser() if val else None


def first_existing(dotted: str) -> str | None:
    """First file that exists from a list-of-paths key (fonts)."""
    for p in get(dotted, []) or []:
        if p and Path(p).expanduser().is_file():
            return str(Path(p).expanduser())
    return None


def episodes_root() -> Path:
    env = os.environ.get("TTYWOOD_DEMOS")
    return Path(env).expanduser().resolve() if env else (path("paths.episodes") or Path("demos")).resolve()


def series_root() -> Path:
    return (path("paths.series") or Path("series")).resolve()


def docker_image() -> str:
    return get("docker.image", "ttywood:latest")


def load_dotenv_files() -> None:
    """KEY=value lines (optional `export `, optional quotes) from the user
    config dir's .env, the checkout's .env and paths.env_files. A variable
    already set in the shell wins."""
    files = [USER_DIR / ".env", PACKAGE.parent / ".env"]
    files += [Path(p) for p in get("paths.env_files", []) or []]
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                os.environ.setdefault(key, value)


# ---- prompts ------------------------------------------------------------------

PROMPTS = PACKAGE / "prompts"


def prompt_path(name: str) -> Path:
    """A prompt file by name, from paths.prompts first, then plugin prompt
    dirs, then the built-in ones."""
    from .plugins import plugin_prompt_dirs
    for d in [*(Path(p) for p in get("paths.prompts", []) or []), *plugin_prompt_dirs(), PROMPTS]:
        if (d / name).is_file():
            return d / name
    raise FileNotFoundError(f"no prompt {name!r} (looked in paths.prompts, plugins and {PROMPTS})")


def load_prompt(name: str, **values: Any) -> str:
    """Read a prompt and fill {{KEY}} placeholders. {{SHOW}} (show.pitch) is
    always available."""
    text = prompt_path(name).read_text()
    values.setdefault("SHOW", get("show.pitch", ""))
    for key, val in values.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text
