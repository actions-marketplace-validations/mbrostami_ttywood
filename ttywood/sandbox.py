"""What an episode's containers get from candidate.json, shared by the
rehearsal and every recording take:

  "env": {"NAME": "HOST_VAR"}  pass host secrets in as environment variables,
                               never typed or echoed on camera. A list of
                               names, or "" as the host name, means the same
                               name on both sides.
  "source": "/abs/path"        a local project (see plan.candidate_for_path):
                               mounted read-only at /src and copied to
                               ~/<source_name> off camera before anything runs.
"""

from __future__ import annotations

import os
import subprocess

SOURCE_MOUNT = "/src"
# platform-specific build output and caches: rebuilt inside the container
SOURCE_EXCLUDES = (".venv", "venv", "node_modules", "__pycache__", ".DS_Store", "target",
                   ".mypy_cache", ".pytest_cache", ".tox")


def env_flags(candidate: dict) -> list[str]:
    spec = candidate.get("env") or {}
    if isinstance(spec, list):
        spec = {name: name for name in spec}
    flags: list[str] = []
    for name, host in spec.items():
        value = os.environ.get(host or name) or os.environ.get(name)
        if not value:
            raise RuntimeError(f"this episode passes {name} into the container: export "
                               f"{host or name} before rehearsing or recording")
        flags += ["-e", f"{name}={value}"]
    return flags


def source_flags(candidate: dict) -> list[str]:
    src = candidate.get("source")
    if not src:
        return []
    if not os.path.isdir(src):
        raise RuntimeError(f"episode source directory is gone: {src}")
    return ["-v", f"{src}:{SOURCE_MOUNT}:ro"]


def source_name(candidate: dict) -> str:
    return candidate.get("source_name") or "project"


def copy_source(container: str, candidate: dict) -> None:
    """Copy the mounted project to ~/<source_name> unless it is already
    there (a series snapshot keeps the previous episode's copy)."""
    if not candidate.get("source"):
        return
    name = source_name(candidate)
    excludes = " ".join(f"--exclude={e}" for e in SOURCE_EXCLUDES)
    script = (f"test -e ~/{name} || {{ mkdir -p ~/{name} && "
              f"tar -C {SOURCE_MOUNT} {excludes} -cf - . | tar -C ~/{name} -xf -; }}")
    p = subprocess.run(["docker", "exec", container, "bash", "-lc", script],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"copying the project into the container failed: {p.stderr[-800:]}")
