"""Planning an episode: decide what it is about and write candidate.json.

Three ways in, none of which searches the internet for a subject:
  candidate_for_repo  a GitHub repo you name; the LLM reads its README
  candidate_for_path  a project on this machine (not necessarily published);
                      the LLM reads it from disk
  new_episode         no LLM at all: an empty episode for a screenplay you
                      (or your agent) write by hand

Picking a subject on its own (trending lists and the like) is a plugin's job,
see plugins.py."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import requests

from .config import load_prompt
from .llm import run as llm_run

UA = {"User-Agent": "ttywood"}


def extract_json(text: str) -> dict | None:
    """The first JSON object in a model answer, tolerating a cut-off tail
    (a missing closing quote/brace) and a ```json fence."""
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    if "```" in body:
        body = body.split("```")[0]
    for tail in ("", "}", "\"}", "\"\n}"):
        m = re.search(r"\{.*\}", body + tail, re.DOTALL)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    return None


def _wants_docker(readme: str) -> bool:
    head = readme[:6000].lower()  # the quick start lives near the top
    return bool(re.search(r"docker (run|compose)|docker-compose|docker\.com|\bdockerfile\b", head))


def local_readme(path: Path) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        if (path / name).is_file():
            return (path / name).read_text(errors="replace")
    return ""


def readme_wants_docker(repo: str = "", source: str = "") -> bool:
    """Does the README's install story lead with Docker (`docker run`,
    `docker compose`, an install script that needs a daemon)? Then the
    episode/check container must have a daemon, or the demo would open on a
    failure that is ours, not the project's. `source` is a local checkout."""
    if source:
        return _wants_docker(local_readme(Path(source)))
    if not repo:
        return False
    try:
        r = requests.get(f"https://raw.githubusercontent.com/{repo}/HEAD/README.md",
                         headers=UA, timeout=15)
        text = r.text if r.status_code == 200 else ""
    except Exception:
        return False
    return _wants_docker(text)


STYLE_STEER = {
    "review": "",
    "showcase": "Style: SHOWCASE. This is the project's own demo, made to show what it does at its "
                "best to someone deciding whether to try it. Plan the strongest real use, not a hunt "
                "for flaws: `structure` is tour, speedrun or mystery, never bug-hunt or versus.",
}


def _steer(prefer: str, style: str) -> str:
    parts = [STYLE_STEER.get(style or "review", "")]
    if prefer:
        parts.append(f"Editor's steer for this episode: {prefer}")
    return "\n\n".join(p for p in parts if p)


def _write_candidate(ep: Path, pick: dict) -> Path:
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "candidate.json").write_text(json.dumps(pick, indent=2, ensure_ascii=False))
    return ep


def _fresh_dir(episodes_root: Path, name: str) -> Path:
    ep = episodes_root / name
    if ep.exists() and (ep / "candidate.json").exists():
        raise RuntimeError(f"episode already exists: {ep} (pass --as to make another take)")
    return ep


def candidate_for_repo(episodes_root: Path, repo: str, prefer: str = "",
                       as_name: str | None = None, log=print, style: str = "") -> Path:
    """Plan an episode for a repo we picked ourselves: the LLM reads its
    README and writes candidate.json (tool, tagline, angle, install hint,
    structure, whether the install needs Docker)."""
    from .check import repo_slug
    from .repoinfo import fetch_repo_info, usable_description
    slug, url = repo_slug(repo)
    tool = slug.split("/")[1]
    ep = _fresh_dir(episodes_root, as_name or tool.lower())
    ep.mkdir(parents=True, exist_ok=True)
    info = fetch_repo_info(ep, slug)
    facts = {"repo": slug, "url": url, "stars": info.get("stars"),
             "description": usable_description(info.get("description"), tool),
             "homepage": info.get("homepage", "")}
    prompt = load_prompt(
        "pick.md", SUBJECT=f"{slug} ({url})", REPO=slug, URL=url,
        READ_SOURCE="Read its README (WebFetch, and the docs it links if the install is unclear) "
                    "and plan the episode.",
        FACTS=json.dumps(facts, indent=1), PREFER=_steer(prefer, style))
    log(f"  planning the episode for {slug} (LLM reads the README, 1-3 min)")
    output = llm_run(prompt, allowed_tools="WebFetch,WebSearch", input_mode="stdin", timeout=900)
    pick = extract_json(output)
    if not pick or not pick.get("tool"):
        raise RuntimeError(f"no usable JSON from LLM:\n{output[-1500:]}")
    pick.setdefault("repo", slug)
    pick.setdefault("url", url)
    if prefer:
        pick["prefer"] = prefer
    if style:
        pick["style"] = style
    if pick.get("blocked"):
        log(f"  note: {pick['blocked']}")
    _write_candidate(ep, pick)
    log(f"  {pick['tool']}: {pick.get('tagline', '')}")
    return ep


def github_remote(path: Path) -> str:
    """owner/name of a checkout's GitHub origin, or ""."""
    try:
        url = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def candidate_for_path(episodes_root: Path, path: str | Path, prefer: str = "",
                       as_name: str | None = None, log=print, style: str = "") -> Path:
    """Plan an episode for a project on this machine. The source is copied
    into the rehearsal and recording containers (read-only mount, copied to
    ~/<name> off camera), so it does not have to be pushed anywhere."""
    src = Path(path).expanduser().resolve()
    if not src.is_dir():
        raise RuntimeError(f"not a directory: {src}")
    name = re.sub(r"[^a-z0-9-]+", "-", src.name.lower()).strip("-") or "project"
    ep = _fresh_dir(episodes_root, as_name or name)
    repo = github_remote(src)
    prompt = load_prompt(
        "pick.md", SUBJECT=f"the project in {src}", REPO=repo, URL=f"https://github.com/{repo}" if repo else "",
        READ_SOURCE="The project is on this machine and is your working directory. Read its README, "
                    "its build files and its docs with Read, Glob and Grep (do not modify anything) "
                    "and plan the episode. In the recording container the source is already at "
                    f"~/{name}: the episode starts there instead of with a clone.",
        FACTS=json.dumps({"path": str(src), "directory_name": name, "github": repo}, indent=1),
        PREFER=_steer(prefer, style))
    log(f"  planning the episode for {src} (LLM reads the project, 1-3 min)")
    output = llm_run(prompt, allowed_tools="Read,Glob,Grep", input_mode="stdin", timeout=900, cwd=src)
    pick = extract_json(output)
    if not pick or not pick.get("tool"):
        raise RuntimeError(f"no usable JSON from LLM:\n{output[-1500:]}")
    pick["source"] = str(src)
    pick["source_name"] = name
    pick["repo"] = repo or ""
    pick["url"] = f"https://github.com/{repo}" if repo else ""
    if prefer:
        pick["prefer"] = prefer
    if style:
        pick["style"] = style
    _write_candidate(ep, pick)
    log(f"  {pick['tool']}: {pick.get('tagline', '')}")
    return ep


def new_episode(episodes_root: Path, name: str, tool: str = "", repo: str = "",
                source: str = "", tagline: str = "") -> Path:
    """An episode directory with a minimal candidate.json and no LLM call,
    for a screenplay written by hand or by an agent."""
    ep = _fresh_dir(episodes_root, name)
    cand: dict = {"tool": tool or name, "tagline": tagline}
    if repo:
        from .check import repo_slug
        slug, url = repo_slug(repo)
        cand.update(repo=slug, url=url, tool=tool or slug.split("/")[1])
    if source:
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise RuntimeError(f"not a directory: {src}")
        cand["source"] = str(src)
        cand["source_name"] = re.sub(r"[^a-z0-9-]+", "-", src.name.lower()).strip("-") or "project"
        if not repo and (gh := github_remote(src)):
            cand.update(repo=gh, url=f"https://github.com/{gh}")
    return _write_candidate(ep, cand)
