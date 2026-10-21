"""Publish to YouTube as PRIVATE (you flip it public after watching): video,
title (first option in youtube.md), description with chapters, tags, thumbnail,
captions (.srt) and the synthetic-content disclosure.

Setup once:
  1. Google Cloud console -> enable "YouTube Data API v3" -> OAuth client
     (Desktop app) -> download JSON to ~/.config/ttywood/client_secret.json
  2. First `ttywood publish` opens a browser to authorize; the token is cached
     at ~/.config/ttywood/youtube_token.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

CONFIG_DIR = Path.home() / ".config" / "ttywood"
CLIENT_SECRET = CONFIG_DIR / "client_secret.json"
TOKEN = CONFIG_DIR / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]


def parse_youtube_md(text: str) -> dict:
    """Pull title / description / tags out of the generated youtube.md."""
    def section(name: str) -> str:
        m = re.search(rf"##\s*{name}.*?\n(.*?)(?=\n##\s|\Z)", text, re.S | re.I)
        return m.group(1).strip() if m else ""
    titles = [re.sub(r"^\s*\d+[.)]\s*", "", l).strip().strip("*").split(" *(")[0].strip()
              for l in section("Titles?").splitlines() if re.match(r"^\s*\d+[.)]", l)]
    tags = [t.strip() for t in section("Tags").replace("\n", ",").split(",") if t.strip()]
    from .screenplay import clean_text
    if not titles:
        # never upload under a placeholder: a wrong title on a live video is
        # worse than a failed publish, and the fix is one `tt meta` away
        raise RuntimeError("youtube.md has no titles; run `tt meta <episode>` "
                           "before publishing")
    return {"title": clean_text(titles[0])[:100],
            "description": "\n".join(clean_text(l) if l.strip() else ""
                                     for l in section("Description").splitlines())[:4900],
            "tags": [clean_text(t) for t in tags[:30]]}


def build_payload(episode_dir: Path, privacy: str = "private", synthetic: bool = False) -> dict:
    """synthetic=True sets YouTube's altered/synthetic-content disclosure. Off by
    default: episodes are real recordings of real sessions; AI-written scripts,
    captions and metadata do not require the disclosure. Turn it on only if an
    episode contains realistic synthetic footage or an imitated real voice."""
    yt = parse_youtube_md((episode_dir / "youtube.md").read_text())
    cand = json.loads((episode_dir / "candidate.json").read_text()) \
        if (episode_dir / "candidate.json").exists() else {}
    lang = cand.get("lang", "en")
    repo = cand.get("repo", "")
    title = yt["title"]
    if repo and repo.lower() not in title.lower():
        title = f"{title[:100 - len(repo) - 3].rstrip()} ({repo})"
    description = yt["description"]
    rj = episode_dir / "repo.json"
    if rj.exists():
        stars = json.loads(rj.read_text()).get("stars")
        if stars and "stars" not in description.lower():
            description = description.replace(f"https://github.com/{repo}",
                                              f"https://github.com/{repo} ({stars:,} stars)", 1)
    footer = (config.get("youtube.description_footer", "") or "").strip()
    if footer and footer not in description:
        description = f"{description.rstrip()}\n\n{footer}"[:5000]
    return {
        "snippet": {"title": title, "description": description, "tags": yt["tags"],
                    "categoryId": "28",  # Science & Technology
                    "defaultLanguage": lang, "defaultAudioLanguage": lang},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": synthetic},
    }


PENDING = CONFIG_DIR / "pending_auth.json"
REDIRECT = "http://localhost"


def auth_url() -> str:
    """No-browser step 1: return the consent URL. Open it on any device,
    approve, then copy the `code=` value from the localhost URL it redirects
    to (that page itself fails to load; expected)."""
    from google_auth_oauthlib.flow import Flow

    if not CLIENT_SECRET.exists():
        raise FileNotFoundError(f"put your OAuth client JSON at {CLIENT_SECRET}")
    flow = Flow.from_client_secrets_file(str(CLIENT_SECRET), scopes=SCOPES, redirect_uri=REDIRECT)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps({"state": state, "code_verifier": flow.code_verifier}))
    return url


def auth_finish(code: str) -> Path:
    """No-browser step 2: exchange the pasted code (or whole redirect URL)."""
    from google_auth_oauthlib.flow import Flow

    pending = json.loads(PENDING.read_text())
    flow = Flow.from_client_secrets_file(str(CLIENT_SECRET), scopes=SCOPES, redirect_uri=REDIRECT,
                                         state=pending["state"])
    flow.code_verifier = pending["code_verifier"]
    code = code.strip()
    if "code=" in code:
        from urllib.parse import parse_qs, urlparse
        code = parse_qs(urlparse(code).query)["code"][0]
    flow.fetch_token(code=code)
    TOKEN.write_text(flow.credentials.to_json())
    PENDING.unlink(missing_ok=True)
    return TOKEN


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    from google.auth.exceptions import RefreshError

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json())
        except RefreshError:
            # a refresh token dies when the OAuth app is in "testing" status
            # (Google expires them after 7 days) or when access was revoked;
            # the only fix is a fresh sign-in
            creds = None
    if not creds or not creds.valid:
        raise RuntimeError("not signed in to YouTube: run `ttywood auth`, approve the URL, "
                           "then `ttywood auth --code <code>`")
    return build("youtube", "v3", credentials=creds)


def _published(episode_dir: Path) -> dict:
    p = episode_dir / "published.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    if "id" in data:  # old single-entry format
        data = {("short" if data.get("short") else "episode"): data}
    return data


def _retry_404(call, tries: int = 6):
    """A video is not addressable for a few seconds after its upload finishes,
    so the thumbnail and caption calls that follow come back 404 until it is.
    Without this the episode is already on YouTube and the exception aborts
    publish() before it records the URL, so a rerun uploads a second copy.
    Same shape as the retry inside sync_playlist, for the same reason."""
    import time
    from googleapiclient.errors import HttpError

    for i in range(tries):
        try:
            return call().execute()
        except HttpError as e:
            if e.resp.status != 404 or i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def publish(episode_dir: Path, privacy: str = "private", dry_run: bool = False,
            short: bool = False, replace: bool = False, captions: bool = False,
            synthetic: bool = False, on_progress=None) -> dict:
    """replace=True deletes the previously published video of this kind first
    (YouTube can't swap a video file in place). The API docs price an upload
    at 1600 quota units but the console counts about 50 per upload in
    practice (10,000/day); still, only replace when the file actually changed.
    captions=True also uploads narration.srt as a CC track; off by default
    because the cinematic cut already burns its captions into the picture and
    a CC track would double them."""
    from googleapiclient.http import MediaFileUpload

    episode_dir = episode_dir.resolve()
    kind = "short" if short else "episode"
    video = episode_dir / ("short.mp4" if short else "final.mp4")
    payload = build_payload(episode_dir, privacy, synthetic)
    if short:
        payload["snippet"]["title"] = (payload["snippet"]["title"][:90] + " #Shorts")[:100]
    thumb = next((episode_dir / n for n in ("thumbnail.png", "thumbnail.jpg")
                  if (episode_dir / n).exists()), None)
    srt = episode_dir / "narration.srt"
    if dry_run:
        return {"video": str(video), "thumbnail": str(thumb),
                "captions": str(srt) if captions and srt.exists() else None, "payload": payload}

    yt = _service()
    published = _published(episode_dir)
    old = published.get(kind)
    if old and not replace:
        raise RuntimeError(f"{kind} already published as {old['url']}; use --replace to "
                           f"delete it and upload again (costs quota)")
    if old and replace:
        try:
            yt.videos().delete(id=old["id"]).execute()
        except Exception as e:  # already gone is fine
            if "404" not in str(e):
                raise
    req = yt.videos().insert(part="snippet,status", body=payload,
                             media_body=MediaFileUpload(str(video), chunksize=8 * 1024 * 1024,
                                                        resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status and on_progress:
            on_progress(status.progress())
    if on_progress:
        on_progress(1.0)
    vid = resp["id"]
    if thumb and not short:
        _retry_404(lambda: yt.thumbnails().set(videoId=vid, media_body=str(thumb)))
    if captions and srt.exists() and not short:
        _retry_404(lambda: yt.captions().insert(
            part="snippet", body={"snippet": {"videoId": vid, "language":
                                  payload["snippet"]["defaultLanguage"], "name": "narration"}},
            media_body=MediaFileUpload(str(srt))))
    import hashlib
    import datetime as dt
    published[kind] = {"id": vid, "url": f"https://youtu.be/{vid}", "privacy": privacy,
                       "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                       "published_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")}
    (episode_dir / "published.json").write_text(json.dumps(published, indent=2))
    from .index import write_index
    write_index(episode_dir.parent)
    return {"id": vid, "url": f"https://youtu.be/{vid}", "replaced": bool(old)}


def unchanged_since_publish(episode_dir: Path, short: bool = False) -> bool:
    """True if the local file is byte-identical to what was last uploaded."""
    import hashlib
    kind = "short" if short else "episode"
    old = _published(episode_dir).get(kind)
    video = episode_dir / ("short.mp4" if short else "final.mp4")
    return bool(old and old.get("sha256") and video.exists()
                and hashlib.sha256(video.read_bytes()).hexdigest() == old["sha256"])


def sync_playlist(series_path: Path, privacy: str = "public") -> dict:
    """One YouTube playlist per series: create it on first use (its id goes
    into series.json under "playlist"), then make sure every episode that has
    a video URL is in it, in episode order. Idempotent: re-running adds only
    the episodes that are missing. The playlist itself can be public while
    its videos are still private; viewers see the private ones only once they
    are switched on."""
    series = json.loads(series_path.read_text())
    yt = _service()
    pl = series.get("playlist") or {}
    if not pl.get("id"):
        resp = yt.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": series["title"],
                              "description": series.get("subject", "")},
                  "status": {"privacyStatus": privacy}}).execute()
        pl = {"id": resp["id"], "url": f"https://www.youtube.com/playlist?list={resp['id']}"}
        series["playlist"] = pl
        series_path.write_text(json.dumps(series, indent=2, ensure_ascii=False) + "\n")
    import time
    from googleapiclient.errors import HttpError

    def retry_404(call, tries: int = 6):
        # a playlist just created is not readable/writable for a few seconds
        for i in range(tries):
            try:
                return call().execute()
            except HttpError as e:
                if e.resp.status != 404 or i == tries - 1:
                    raise
                time.sleep(3 * (i + 1))

    have: list[str] = []
    page = None
    while True:
        resp = retry_404(lambda: yt.playlistItems().list(
            part="snippet", playlistId=pl["id"], maxResults=50, pageToken=page))
        have += [it["snippet"]["resourceId"]["videoId"] for it in resp.get("items", [])]
        page = resp.get("nextPageToken")
        if not page:
            break
    added = []
    for ep in sorted(series["episodes"], key=lambda e: e["n"]):
        url = ep.get("video")
        if not url:
            continue
        vid = url.rstrip("/").rsplit("/", 1)[-1].split("?v=")[-1]
        if vid in have:
            continue
        retry_404(lambda: yt.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": pl["id"],
                              "resourceId": {"kind": "youtube#video", "videoId": vid}}}))
        have.append(vid)
        added.append(f"E{ep['n']:02d}")
    return {"playlist": pl["url"], "videos": len(have), "added": added}
