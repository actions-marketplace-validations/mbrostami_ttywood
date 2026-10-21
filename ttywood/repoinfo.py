"""Facts about the repo for the cards: the project's own description (used as
the title-card subtitle when it's usable), the URL, stars, and the top
contributors with their avatars. Cached in <episode>/repo.json + credits/."""

from __future__ import annotations

import json
import re
import os
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw

UA = {"User-Agent": "ttywood", "Accept": "application/vnd.github+json"}
if os.environ.get("GITHUB_TOKEN"):
    UA["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"


# a project's own one-liner often ends in a call to action or a chat invite;
# that is the project talking to its users, not a subtitle for a review
_URL = re.compile(r"(?i)\b(?:https?://\S+|(?:www\.)?[\w-]+\.(?:com|dev|io|ai|org|net|gg|sh|app|xyz)(?:/\S*)?)")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u2B50\u2705\u26A1]")
# GitHub renders ":cherry_blossom:" as an emoji; the API returns the shortcode
_SHORTCODE = re.compile(r":[a-z0-9_+-]+:")
_CTA_WORDS = re.compile(
    r"(?i)\b(join|star (us|this|the)|give us a star|follow (us|me)|sponsor|subscribe|"
    r"discord|slack|telegram|matrix|newsletter|waitlist|sign ?up|don'?t be shy|"
    r"try it (now|today)|check ?it ?out|contact us|hiring|we'?re hiring)\b")


def clean_description(text: str, tool: str) -> str:
    """The repo one-liner with the project's marketing stripped: emoji gone,
    any sentence that invites the reader somewhere (Discord, a star, a
    newsletter) or carries a link gone, a leading repeat of the tool name
    gone. What is left is a plain description of what the tool does."""
    t = _EMOJI.sub("", _SHORTCODE.sub("", text or ""))
    kept = []
    for part in re.split(r"(?<=[.!?])\s+|\s+[|·•]\s+", t):
        if not part.strip():
            continue
        if _URL.search(part) or _CTA_WORDS.search(part):
            continue
        kept.append(part.strip())
    t = " ".join(kept)
    t = _URL.sub("", t)
    t = " ".join(t.split()).strip(" .,:;-!")
    for sep in (" - ", " – ", ": ", " — "):
        if t.lower().startswith(tool.lower() + sep.rstrip()):
            t = t.split(sep, 1)[1].strip()
            break
    t = re.sub(r"(?i)[,;]?\s+(?:and|or|with|for|plus)$", "", t).strip(" .,:;-!")
    return t


def usable_description(text: str | None, tool: str) -> str:
    """The repo's own one-liner, if it reads like a subtitle: 12-140 chars,
    not just the name, no marketing, no invites, no emoji."""
    if not text:
        return ""
    t = clean_description(text, tool)
    if not 12 <= len(t) <= 140 or t.lower() == tool.lower():
        return ""
    return t[:1].upper() + t[1:]


def fetch_repo_info(episode_dir: Path, repo: str, refresh: bool = False) -> dict:
    cache = episode_dir / "repo.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    info = {"repo": repo, "html_url": f"https://github.com/{repo}", "description": "",
            "stars": None, "contributors": []}
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}", headers=UA, timeout=20)
        if r.ok:
            d = r.json()
            info.update({"html_url": d.get("html_url", info["html_url"]),
                         "description": d.get("description") or "",
                         "stars": d.get("stargazers_count"), "homepage": d.get("homepage") or ""})
        r = requests.get(f"https://api.github.com/repos/{repo}/contributors",
                         params={"per_page": 100}, headers=UA, timeout=20)
        if r.ok:
            people = [c for c in r.json() if c.get("type", "User") == "User"]
            info["contributors"] = [
                {"login": c["login"], "avatar_url": c["avatar_url"],
                 "contributions": c.get("contributions", 0)} for c in people[:3]]
            # 100 per page: a second page means "100+", enough for the credits line
            info["contributor_count"] = len(people) if 'rel="next"' not in r.headers.get("Link", "") \
                else 101
    except requests.RequestException:
        pass
    cache.write_text(json.dumps(info, indent=2))
    return info


def avatar(episode_dir: Path, login: str, url: str, size: int = 160) -> Image.Image | None:
    """Round avatar image, cached under credits/."""
    out_dir = episode_dir / "credits"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{login}.png"
    if not path.exists():
        try:
            r = requests.get(url, params={"s": size * 2}, headers=UA, timeout=20)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            return None
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        img.putalpha(mask)
        img.save(path)
    return Image.open(path).convert("RGBA")
