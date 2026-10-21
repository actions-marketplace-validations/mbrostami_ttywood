"""ttywood CLI: plan -> rehearse -> record -> render -> narrate -> meta -> qa."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click

from . import config, plugins

# secrets (CLAUDE_CODE_OAUTH_TOKEN, API keys) may live in a gitignored .env
# instead of the shell profile; see config.load_dotenv_files
config.load_dotenv_files()

# paths.episodes in config; TTYWOOD_DEMOS overrides it (the GitHub Action
# keeps episodes in the runner's temp dir)
EPISODES = config.episodes_root()
STYLES = ("review", "showcase")


def _episode_dir(episode: str) -> Path:
    p = Path(episode)
    if p.exists() or p.is_absolute():
        return p.resolve()
    return (EPISODES / episode).resolve()


@click.group()
@click.version_option(package_name="ttywood")
def main():
    """ttywood: turn a CLI tool into a recorded, edited terminal video.

    An LLM agent tries the tool for real in a Docker container and writes a
    screenplay; a fresh container replays it with human-paced typing under
    asciinema; the recording is cut into a captioned video, a Short, a
    thumbnail and YouTube metadata, and checked by a QA pass.

    Start with `ttywood doctor`, then `ttywood run --repo owner/name`.
    """


@main.command("build-image")
@click.option("--tag", default=None, help="Image tag (default: docker.image from config)")
def build_image(tag: str | None):
    """Build the episode Docker image (Ubuntu + toolchains + zellij + asciinema + Claude Code)."""
    context = config.PACKAGE / "docker"
    subprocess.run(["docker", "build", "-t", tag or config.docker_image(), str(context)], check=True)


def _set_style(ep: Path, style: str | None) -> None:
    if not style:
        return
    cand_path = ep / "candidate.json"
    cand = json.loads(cand_path.read_text())
    cand["style"] = style
    cand_path.write_text(json.dumps(cand, indent=2, ensure_ascii=False))


@main.command()
@click.argument("repo", required=False)
@click.option("--path", "src", type=click.Path(exists=True, file_okay=False), default=None,
              help="Plan for a project on this machine instead of a GitHub repo")
@click.option("--prefer", default="", help="Steer the episode, e.g. 'show the CI integration'")
@click.option("--style", type=click.Choice(STYLES), default=None,
              help="review (default): an honest hands-on review. showcase: the project's own demo")
@click.option("--as", "as_name", default=None, help="Episode directory name (default: the repo name)")
def pick(repo: str | None, src: str | None, prefer: str, style: str | None, as_name: str | None):
    """Plan an episode for a repo you choose (owner/name or a GitHub URL) or a
    local project (--path): the LLM reads its README and writes
    <episodes>/<name>/candidate.json. Then `ttywood run <name> --from rehearse`."""
    from .plan import candidate_for_path, candidate_for_repo
    if bool(repo) == bool(src):
        raise click.UsageError("give a REPO or --path, not both")
    ep = (candidate_for_path(EPISODES, src, prefer=prefer, as_name=as_name, log=click.echo,
                             style=style or "") if src
          else candidate_for_repo(EPISODES, repo, prefer=prefer, as_name=as_name, log=click.echo,
                                  style=style or ""))
    click.echo(f"episode planned: {ep}\n  next: ttywood run {ep.name} --from rehearse")


@main.command()
@click.argument("name")
@click.option("--repo", default="", help="GitHub repo the episode is about (owner/name), for the cards")
@click.option("--path", "src", type=click.Path(exists=True, file_okay=False), default=None,
              help="Local project copied into the recording container at ~/<dirname>")
@click.option("--tool", default="", help="Tool name on the title card (default: NAME)")
@click.option("--tagline", default="", help="One line under the tool name on the title card")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON")
def new(name: str, repo: str, src: str | None, tool: str, tagline: str, as_json: bool):
    """Create an empty episode for a screenplay you write yourself (no LLM).

    \b
    Write <episode>/screenplay.yaml (`ttywood schema` prints its JSON schema,
    `ttywood validate` checks it), then:
      ttywood run NAME --from record
    """
    from .plan import new_episode
    ep = new_episode(EPISODES, name, tool=tool, repo=repo, source=src or "", tagline=tagline)
    if as_json:
        click.echo(json.dumps({"episode": str(ep), "screenplay": str(ep / "screenplay.yaml")}))
    else:
        click.echo(f"episode created: {ep}\n  write {ep / 'screenplay.yaml'}, then: "
                   f"ttywood run {ep.name} --from record")


@main.command()
@click.argument("target")
def validate(target: str):
    """Check a screenplay against the schema: an episode name, its directory,
    or a .yaml file. Exit 1 if invalid."""
    from .screenplay import validation_errors
    path = Path(target) if target.endswith((".yaml", ".yml")) else _episode_dir(target)
    if path.is_dir():
        path = path / "screenplay.yaml"
    if not path.exists():
        raise click.UsageError(f"no screenplay at {path}")
    err = validation_errors(path)
    if err:
        click.echo(err)
        sys.exit(1)
    click.echo(f"ok: {path}")


@main.command()
def schema():
    """Print the screenplay's JSON schema (for agents writing one by hand)."""
    from .screenplay import Screenplay
    click.echo(json.dumps(Screenplay.model_json_schema(), indent=2))


@main.command("config")
@click.option("--json", "as_json", is_flag=True, help="Print the merged config as JSON")
def show_config(as_json: bool):
    """Show which config files apply and the merged settings."""
    data = config.load()
    if as_json:
        click.echo(json.dumps({"sources": [str(s) for s in config.sources()], "config": data},
                              indent=2, ensure_ascii=False))
        return
    click.echo("config files (later wins):")
    for s in config.sources():
        click.echo(f"  {s}")
    click.echo(f"\nepisodes: {EPISODES}\nseries:   {config.series_root()}\n"
               f"image:    {config.docker_image()}\nbrand:    {config.get('brand.name') or '(none)'}")
    names = [getattr(m, "__name__", "?") for m in plugins.loaded()]
    click.echo(f"plugins:  {', '.join(names) or '(none)'}")


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Print the checks as JSON")
def doctor(as_json: bool):
    """Check that everything ttywood needs is installed. Exit 1 if something required is missing."""
    from .doctor import run_checks
    checks = run_checks()
    if as_json:
        click.echo(json.dumps(checks, indent=2))
    else:
        for c in checks:
            mark = "ok  " if c["ok"] else ("MISS" if c["required"] else "skip")
            click.echo(f"  {mark}  {c['name']:<18} {c['detail']}")
    if any(c["required"] and not c["ok"] for c in checks):
        sys.exit(1)


@main.command()
@click.argument("episode")
def info(episode: str):
    """An episode's state as JSON: its files, QA verdict and video URLs."""
    from .index import episode_info
    click.echo(json.dumps(episode_info(_episode_dir(episode)), indent=2, ensure_ascii=False))


@main.group()
def series():
    """Plan a numbered series once, then make one episode at a time."""


@series.command("plan")
@click.argument("subject")
@click.option("--episodes", "count", default=6, show_default=True, help="How many episodes")
@click.option("--repo", default="", help="The project this series is about (owner/name or URL)")
@click.option("--prefer", default="", help="Steer the arc, e.g. 'keyboard-first, no config files'")
@click.option("--slug", default=None, help="Series id (default: Claude's suggestion)")
def series_plan(subject: str, count: int, repo: str, prefer: str, slug: str | None):
    """Ask Claude for the arc: series/<slug>/series.json. Edit it before making
    episodes — it is plain JSON and cheap to change."""
    from .series import plan_series
    out = plan_series(subject, count=count, prefer=prefer, repo=repo, slug=slug, log=click.echo)
    click.echo(f"series planned: {out}\n  next: tt series show {json.loads(out.read_text())['slug']}")


@series.command("list")
def series_list():
    """Every planned series."""
    from .series import known, table
    for d in known():
        click.echo(table(d.name) + "\n")
    if not known():
        click.echo("no series yet: tt series plan \"zellij\" --repo zellij-org/zellij")


@series.command("show")
@click.argument("slug")
def series_show(slug: str):
    """One series and the state of its episodes."""
    from .series import table
    click.echo(table(slug))


@series.command("playlist")
@click.argument("slug")
@click.option("--privacy", type=click.Choice(["public", "unlisted", "private"]), default="public",
              show_default=True, help="Privacy of the playlist itself (its videos keep their own)")
def series_playlist(slug: str, privacy: str):
    """Create the series' YouTube playlist if needed and add every published
    episode to it, in order. Safe to re-run after each publish."""
    from .publish import sync_playlist
    from .series import series_path
    path = series_path(slug)
    if not path.exists():
        raise click.ClickException(f"no series {slug!r} (expected {path})")
    res = sync_playlist(path, privacy=privacy)
    click.echo(json.dumps(res, indent=2))


@series.command("next")
@click.argument("slug")
@click.option("--n", type=int, default=None, help="Make this episode number instead of the next one")
def series_next(slug: str, n: int | None):
    """Create the next episode's directory (candidate.json only, no recording)."""
    from .series import start_next
    ep = start_next(slug, EPISODES, n=n, log=click.echo)
    click.echo(f"episode planned: {ep}\n  next: tt run {ep.name} --from rehearse")


def _slug(text: str, max_words: int = 4) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"the", "a", "an", "how", "to", "with", "for", "of", "and", "in", "it",
            "using", "use", "into", "on", "is", "what", "about", "look", "at"}
    return "-".join([w for w in words if w not in stop][:max_words]) or "focus"


def _focus_name(tool: str, focus: str) -> str:
    """Ask Claude for a short episode slug for the brief; fall back to a
    local slugify. Result: <tool>-<slug>, unique within demos/."""
    slug = ""
    try:
        from .llm import run as llm_run
        out = llm_run(f"Give a 1-3 word lowercase hyphenated slug (letters/digits/hyphens "
                      f"only, no quotes, nothing else) naming this episode topic about the "
                      f"tool '{tool}': {focus}", timeout=90)
        cand = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+){0,3}", cand):
            slug = cand
    except Exception:
        pass
    slug = slug or _slug(focus)
    # the brief usually names the tool; don't end up with "mise-mise-ci"
    slug = "-".join(w for w in slug.split("-") if w != tool.lower()) or "focus"
    base = f"{tool.lower()}-{slug}"
    name, n = base, 2
    while (EPISODES / name).exists():
        name, n = f"{base}-{n}", n + 1
    return name


def _fork_episode(ep: Path, name: str) -> Path:
    """Copy an episode's candidate.json into demos/<name>/ so a focused variant
    sits beside the original instead of overwriting it."""
    new = EPISODES / name
    if new.exists():
        raise click.UsageError(f"episode already exists: {new}")
    new.mkdir(parents=True)
    shutil.copy(ep / "candidate.json", new / "candidate.json")
    return new


@main.command()
@click.argument("episode")
@click.option("--focus", default=None,
              help="Brief for the rehearsal, e.g. 'how it integrates with Kubernetes'")
@click.option("--as", "as_name", default=None,
              help="Name for the focused episode (default: <tool>-<slug of the brief>)")
@click.option("--lang", default=None, type=click.Choice(["en", "fa"]),
              help="Language of narration and metadata (stored in the episode)")
def rehearse(episode: str, focus: str | None, as_name: str | None, lang: str | None):
    """Run Claude Code inside Docker to try the tool and write the screenplay."""
    from .rehearse import rehearse_episode
    ep = _episode_dir(episode)
    if focus:
        tool = json.loads((ep / "candidate.json").read_text()).get("tool", ep.name)
        ep = _fork_episode(ep, as_name or _focus_name(tool, focus))
    from . import ui
    marks = {"command": "$ ", "write": "writes ", "text": "", "result": "result: "}
    rehearse_episode(ep, focus=focus, lang=lang,
                     on_event=lambda k, t: ui.console.print(
                         f"[magenta]{marks[k]}{t[:200]}[/]" if k == "command"
                         else f"[dim]{marks[k]}{t[:200]}[/]") if k in marks else None)
    click.echo(f"rehearsed: {ep}")


@main.command()
@click.argument("episode")
@click.option("--screenplay", type=click.Path(exists=True), default=None,
              help="Override screenplay path (default: <episode>/screenplay.yaml)")
@click.option("--speed", default=1.0, show_default=True, help="Typing speed multiplier")
@click.option("--seed", default=None, type=int, help="RNG seed for reproducible typing")
def record(episode: str, screenplay: str | None, speed: float, seed: int | None):
    """Performance pass: record the screenplay in a fresh container."""
    from .record import record_episode
    if screenplay or seed is not None or speed != 1.0:
        cast = record_episode(_episode_dir(episode),
                              Path(screenplay) if screenplay else None,
                              speed=speed, seed=seed)
        click.echo(f"recorded: {cast}")
    else:
        _record_with_repair(_episode_dir(episode))


@main.command()
@click.argument("episode")
@click.option("--font-size", default=20, show_default=True)
@click.option("--idle-limit", default=3.5, show_default=True,
              help="Cap (s) for idle gaps that are neither typing, pause nor wait")
@click.option("--fast-factor", default=6.0, show_default=True,
              help="Fast-forward speed for command waits (clone/install/output)")
@click.option("--wait-max", default=6.0, show_default=True,
              help="Max on-screen seconds for any single wait")
@click.option("--pause-scale", default=None, type=float,
              help="Multiply the screenplay's reading pauses (default 1.0; 0.4 with voice)")
@click.option("--voice/--no-voice", default=None,
              help="Mux the TTS track (default: auto if voice/manifest.json exists)")
def render(episode: str, font_size: int, idle_limit: float, fast_factor: float,
           wait_max: float, pause_scale: float | None, voice: bool | None):
    """Render demo.cast into final.mp4 (time-lapsed waits, cards, optional voice)."""
    from .render import render_episode
    out = render_episode(_episode_dir(episode), font_size=font_size,
                         idle_limit=idle_limit, fast_factor=fast_factor,
                         wait_max=wait_max, use_voice=voice, pause_scale=pause_scale)
    click.echo(f"rendered: {out}")


@main.command()
@click.argument("episode")
@click.option("--engine", default="say", show_default=True,
              type=click.Choice(["say", "openai", "elevenlabs"]))
@click.option("--voice", "voice_name", default=None,
              help="Voice name/id for the engine (default: Samantha / onyx / George)")
@click.option("--spoken/--no-spoken", default=True, show_default=True,
              help="Rewrite narration for the TTS ear first (phonetic tool names, "
                   "spelled-out acronyms); cached in voice/spoken.json")
def voice(episode: str, engine: str, voice_name: str | None, spoken: bool):
    """Generate one TTS clip per scene into <episode>/voice/ (then re-run render)."""
    from .voice import generate_voice
    out = generate_voice(_episode_dir(episode), engine=engine, voice=voice_name,
                         spoken=spoken)
    click.echo(f"voice track: {out}")


@main.command()
@click.argument("episode")
@click.option("--music", type=click.Path(exists=True), default=None,
              help="Background track (looped, faded, quiet). Default: the synthesized lo-fi bed")
@click.option("--no-music", is_flag=True, help="Key sounds only, no music bed")
@click.option("--zoom", is_flag=True,
              help="Camera follows the cursor while typing (default: steady view + command bar)")
@click.option("--sfx/--no-sfx", default=True, show_default=True,
              help="Synthesized key ticks, Enter thud and done chime at the real times")
@click.option("--cold-open", is_flag=True, default=False,
              help="Experimental: open on one command's output before the welcome card")
def cinema(episode: str, music: str | None, no_music: bool, zoom: bool, sfx: bool,
           cold_open: bool):
    """Speechless render: captions, chapter cards, pinned command bar, key sounds."""
    from .cinema import TERM_FONT_SIZE, render_cinematic
    from .render import render_episode
    ep = _episode_dir(episode)
    render_episode(ep, use_voice=False, font_size=TERM_FONT_SIZE, body_only=True)  # warped body + warp.json
    out = render_cinematic(ep, _music_arg(music, no_music), zoom=zoom, sfx=sfx,
                           cold_open=cold_open)
    click.echo(f"cinematic: {out}")


def _music_arg(music: str | None, no_music: bool) -> Path | None:
    """None = the default synthesized bed; a nonexistent path = no music."""
    if no_music:
        return Path("/dev/null/no-music")
    return Path(music) if music else None


@main.command()
@click.argument("episode")
@click.option("--scene", default=None, help="Scene id (default: the rough-edge scene, else the longest)")
@click.option("--sfx/--no-sfx", default=True, show_default=True)
@click.option("--record", "do_record", is_flag=True,
              help="Record a take made for the format first: the same screenplay up to this "
                   "scene, replayed in a narrow terminal so nothing is cropped on a phone")
@click.option("--cols", default=None, type=int, help="Columns for the recorded Short take (default 62)")
@click.option("--rows", default=None, type=int, help="Rows for the recorded Short take (default 30)")
@click.option("--crop", is_flag=True, help="Force the old path: crop the wide episode recording")
def shorts(episode: str, scene: str | None, sfx: bool, do_record: bool,
           cols: int | None, rows: int | None, crop: bool):
    """Vertical 1080x1920 cut of one scene (<= 58 s) into <episode>/short.mp4."""
    from .shorts import SHORT_COLS, SHORT_ROWS, record_short, render_short
    ep = _episode_dir(episode)
    if do_record:
        from . import ui
        ui.console.print(f"[bold]recording a vertical take[/] ({cols or SHORT_COLS}x{rows or SHORT_ROWS})")
        record_short(ep, scene_id=scene, cols=cols or SHORT_COLS, rows=rows or SHORT_ROWS,
                     on_progress=lambda kind, text: ui.console.print(f"[dim]{kind}: {text[:150]}[/]"))
    out = render_short(ep, scene_id=scene, sfx=sfx, native=False if crop else None)
    click.echo(f"short: {out}")


@main.command()
@click.argument("episode")
def thumbnail(episode: str):
    """Write thumbnail.png: a real frame from the episode under big text."""
    from .thumbnail import make_thumbnail
    click.echo(f"thumbnail: {make_thumbnail(_episode_dir(episode))}")


@main.command()
@click.argument("episode")
@click.option("--no-review", is_flag=True, help="Hard checks only, skip the Claude frame review")
def qa(episode: str, no_review: bool):
    """Quality gate: hard checks + Claude frame review -> qa.md (exit 1 on FAIL)."""
    from .qa import run_qa
    ok, report = run_qa(_episode_dir(episode), review=not no_review)
    click.echo(report.read_text())
    if not ok:
        sys.exit(1)


@main.command()
@click.argument("episode")
@click.option("--privacy", default="private", type=click.Choice(["private", "unlisted", "public"]),
              show_default=True)
@click.option("--short", is_flag=True, help="Upload short.mp4 instead of final.mp4")
@click.option("--dry-run", is_flag=True, help="Print the upload payload, upload nothing")
@click.option("--replace", is_flag=True,
              help="Delete the previously published video of this kind and upload again")
@click.option("--captions", is_flag=True,
              help="Also upload narration.srt as a CC track (off: the video has burnt-in captions)")
@click.option("--synthetic", is_flag=True,
              help="Set YouTube's altered/synthetic-content disclosure (off: real recordings)")
def publish(episode: str, privacy: str, short: bool, dry_run: bool, replace: bool,
            captions: bool, synthetic: bool):
    """Upload to YouTube (private by default) with metadata and thumbnail."""
    from .publish import publish as _publish, unchanged_since_publish
    ep = _episode_dir(episode)
    if replace and unchanged_since_publish(ep, short=short):
        click.echo("unchanged since last upload; nothing to do (quota saved)")
        return
    res = _publish(ep, privacy=privacy, dry_run=dry_run, short=short, replace=replace,
                   captions=captions, synthetic=synthetic)
    click.echo(json.dumps(res, indent=2, ensure_ascii=False))
    if not dry_run and not short:
        # a series episode: keep its URL in series.json, like `run --publish` does
        cand_path = ep / "candidate.json"
        cand = json.loads(cand_path.read_text()) if cand_path.exists() else {}
        if cand.get("series", {}).get("slug"):
            from .publish import sync_playlist
            from .series import record_video
            record_video(cand["series"]["slug"], ep.name, res["url"])
            # ...and into the series' playlist (created on first use)
            from .series import series_path
            pl = sync_playlist(series_path(cand["series"]["slug"]))
            click.echo(f"playlist: {pl['playlist']} ({pl['videos']} videos)")


@main.command()
@click.option("--code", default=None, help="The code (or the whole localhost URL) from step 1")
def auth(code: str | None):
    """YouTube sign-in without a browser on this machine: run once for a URL,
    approve it on any device, then run again with --code."""
    from .publish import auth_finish, auth_url
    if code:
        click.echo(f"token saved: {auth_finish(code)}")
    else:
        click.echo("Open this URL on any device, approve, then copy the `code=` value from the\n"
                   "localhost URL you land on (the page won't load, that's fine) and run:\n"
                   "  ttywood auth --code <code>\n")
        click.echo(auth_url())


@main.command()
def status():
    """Rewrite <episodes>/INDEX.md (which episodes are published, ready, failed) and print it."""
    from .index import write_index
    out = write_index(EPISODES)
    click.echo(out.read_text())


@main.command()
@click.argument("target")
@click.option("--ref", default="", help="Commit, tag or branch to check (default: default branch)")
@click.option("--privileged", is_flag=True, help="Run the container privileged (loop devices, FUSE)")
@click.option("--docker/--no-docker", "use_docker", default=None,
              help="Docker daemon inside the container (implies --privileged). Default: "
                   "auto, on when the README leads with Docker")
@click.option("--as", "as_name", default=None,
              help="Episode directory name (default: the repo name, or <name>-check if an "
                   "episode of that name already exists for another repo)")
def check(target: str, ref: str, privileged: bool, use_docker: bool, as_name: str | None):
    """Clean-room check of a repo (owner/name or URL): Claude follows the docs
    in a fresh container and writes report.json + report.md + badge.svg.
    Exit 1 on a fail verdict, so it can gate a CI job."""
    from .check import repo_slug, run_check
    from .badge import write_badge
    from . import ui
    slug, _ = repo_slug(target)
    name = as_name or slug.split("/")[1].lower()
    ep = EPISODES / name
    if not as_name and ep.exists():
        try:
            other = json.loads((ep / "candidate.json").read_text()).get("repo", "")
        except (OSError, ValueError):
            other = ""
        if other and other.lower() != slug.lower():
            ep = EPISODES / f"{name}-check"
    marks = {"command": "$ ", "write": "writes ", "text": ""}
    ui.console.print(f"[bold]clean-room check[/] {slug}" + (f" @ {ref}" if ref else "") + f" -> {ep}")
    r = run_check(ep, slug, ref=ref, privileged=privileged, docker=use_docker,
                  on_event=lambda k, t: ui.console.print(
                      f"[magenta]{marks[k]}{t[:200]}[/]" if k == "command"
                      else f"[dim]{marks[k]}{t[:200]}[/]") if k in marks else None)
    svg, _ = write_badge(ep, r)
    color = {"pass": "green", "warn": "yellow", "fail": "red", "blocked": "white"}[r.verdict]
    ui.console.print(f"\n[{color} bold]{r.verdict.upper()}[/] {r.summary}")
    for f in r.findings:
        ui.console.print(f"  [{ {'blocker': 'red', 'friction': 'yellow', 'nit': 'dim', 'env': 'dim'}[f.severity] }]"
                         f"{f.severity}[/] {f.title}")
    ui.console.print(f"\n{ep / 'report.md'}\n{ep / 'report.json'}\n{svg}")
    from .index import write_index
    write_index(EPISODES)
    if r.verdict == "fail":
        sys.exit(1)


@main.command()
@click.argument("episode")
def badge(episode: str):
    """Write badge.svg + badge.json (shields endpoint payload) from report.json."""
    from .badge import write_badge
    from .check import load_report
    ep = _episode_dir(episode)
    svg, js = write_badge(ep, load_report(ep / "report.json"))
    click.echo(f"{svg}\n{js}")


@main.command()
@click.argument("episode")
@click.option("--scene", default=None, help="Scene id for the GIF (default: the Short's scene)")
@click.option("--width", default=900, show_default=True)
def gif(episode: str, scene: str | None, width: int):
    """Write readme.gif: one scene of the rendered episode, README sized."""
    from .embed import readme_gif
    click.echo(str(readme_gif(_episode_dir(episode), scene_id=scene, width=width)))


@main.command()
@click.argument("episode")
@click.option("--to", "dest", default=None, type=click.Path(),
              help="Directory to export into (default: <episode>/export/.ttywood)")
@click.option("--no-gif", is_flag=True, help="Skip the README GIF")
def export(episode: str, dest: str | None, no_gif: bool):
    """Bundle badge, report, README GIF and the README snippet into a
    .ttywood/ directory ready to commit into the project's repo."""
    from .embed import ASSET_DIR, export_assets
    ep = _episode_dir(episode)
    out = Path(dest) if dest else ep / "export" / ASSET_DIR
    files = export_assets(ep, out, with_gif=not no_gif)
    for f in files:
        click.echo(str(f))
    click.echo("\n" + (out / "README-snippet.md").read_text())


@main.command()
@click.argument("episode")
def narrate(episode: str):
    """Write narration.md + narration.srt from the rendered timeline."""
    from .narration import write_narration
    md, srt = write_narration(_episode_dir(episode))
    click.echo(f"narration: {md}\nsubtitles: {srt}")


@main.command()
@click.argument("episode")
def meta(episode: str):
    """Generate YouTube title/description/tags into youtube.md."""
    from .metadata import write_metadata
    out = write_metadata(_episode_dir(episode))
    click.echo(f"metadata: {out}")


# "discover" decides what the episode is about: --repo, --path, --series, or
# a plugin's discover() when none is given
STAGES = ["discover", "rehearse", "record", "voice", "render", "narrate", "meta",
          "thumbnail", "shorts", "qa", "publish"]
RECORD_REPAIRS = 2  # how many times Claude may fix the screenplay after a failed take


def _record_with_repair(ep: Path, st=None):
    """Record; on a stalled command hand the failure to Claude to repair the
    screenplay and retry, up to RECORD_REPAIRS times. `st` is a ui stage."""
    from .record import record_episode
    from .rehearse import fix_screenplay
    from .typer_engine import RecordError
    note = st.note if st else (lambda m, *_: click.echo(m))
    activity = st.activity if st else (lambda m: None)

    def on_progress(kind: str, text: str):
        if kind == "status":
            note(text)
            activity(text)
        else:  # a scene id: a command is being typed
            activity(f"{kind}: {text}")
            note(f"{kind}  $ {text}", "magenta")

    for attempt in range(RECORD_REPAIRS + 1):
        try:
            note(f"take {attempt + 1}")
            record_episode(ep, on_progress=on_progress)
            if attempt:
                note(f"recorded after {attempt} repair(s)", "green")
            return
        except RecordError as e:
            if attempt == RECORD_REPAIRS:
                raise
            note(f"take {attempt + 1} failed in scene {e.scene!r} at {e.command!r}: {e.reason}", "red")
            activity("asking Claude to repair the screenplay")
            summary = fix_screenplay(ep, e.scene, e.command, e.reason, e.screen,
                                     log_path=ep / "rehearse.log")
            note(f"repair: {summary}", "cyan")


def _wants_vertical_take(ep: Path) -> bool:
    """A Short recorded at 62 columns, unless the episode is a full-screen
    TUI (zellij collapses at that width, so those are cropped from the wide
    take instead)."""
    from .record import _host_for
    from .screenplay import load_screenplay
    return _host_for(load_screenplay(ep / "screenplay.yaml")) != "tmux"


def _start_vertical_take(ep: Path, st):
    """Record the Short's narrow-terminal take in a background thread (its
    own container, its own cast files). Returns a join() that gives the
    error text, or "" when the take is in the can."""
    import threading
    from .shorts import record_short
    box: dict = {"err": ""}

    def go():
        try:
            record_short(ep, on_progress=lambda kind, text: None)
        except Exception as e:  # reported by the shorts stage, which falls back
            box["err"] = f"{type(e).__name__}: {e}"

    st.note("recording the Short's narrow-terminal take in the background")
    th = threading.Thread(target=go, name="vertical-take", daemon=True)
    th.start()

    def join() -> str:
        th.join()
        return box["err"]
    return join


@main.command()
@click.argument("episode", required=False)
@click.option("--from", "from_stage", default=None, type=click.Choice(STAGES),
              help="Start at this stage and run everything after it. "
                   "Default: discover for a new episode, record for an existing one.")
@click.option("--to", "to_stage", default="qa", type=click.Choice(STAGES),
              show_default=True, help="Stop after this stage (publish is opt-in: --publish)")
@click.option("--repo", "repo_arg", default=None,
              help="Make a new episode about this GitHub repo (owner/name or URL)")
@click.option("--path", "path_arg", type=click.Path(exists=True, file_okay=False), default=None,
              help="Make a new episode about a project on this machine (copied into the containers)")
@click.option("--prefer", default="",
              help="Steer the plan, e.g. 'show the CI integration, skip the web UI'")
@click.option("--style", type=click.Choice(STYLES), default=None,
              help="review (default): an honest hands-on review. showcase: the project's own demo")
@click.option("--series", "series_slug", default=None,
              help="Make the next unmade episode of this series (see `tt series`)")
@click.option("--tts", "tts_engine", default=None,
              type=click.Choice(["say", "openai", "elevenlabs"]),
              help="Voice-over mode: generate a TTS track with this engine and render the "
                   "voiced cut instead of the default speechless cinematic cut")
@click.option("--focus", default=None,
              help="Brief steering the rehearsal (implies --from rehearse for an "
                   "existing episode), e.g. 'integrating with Kubernetes'")
@click.option("--as", "as_name", default=None,
              help="Name for the focused episode (default: <tool>-<slug of the brief>)")
@click.option("--lang", default=None, type=click.Choice(["en", "fa"]),
              help="Language of narration and metadata (default en; stored per episode). "
                   "On an existing episode this forks a new <name>-<lang> episode and "
                   "re-rehearses it.")
@click.option("--music", type=click.Path(exists=True), default=None,
              help="Background track for the cinematic cut (default: synthesized lo-fi bed)")
@click.option("--no-music", is_flag=True, help="Cinematic cut without a music bed")
@click.option("--crop-short", is_flag=True,
              help="Cut the Short from the wide episode recording instead of recording a "
                   "narrow-terminal take for it (faster, but lines run off a phone screen)")
@click.option("--publish", "do_publish", is_flag=True,
              help="After QA passes, upload to YouTube as private (needs an OAuth client, "
                   "see `ttywood auth`)")
def run(episode: str | None, from_stage: str | None, to_stage: str, repo_arg: str | None,
        path_arg: str | None, prefer: str, style: str | None,
        series_slug: str | None, tts_engine: str | None, focus: str | None,
        as_name: str | None, lang: str | None, music: str | None, no_music: bool,
        crop_short: bool, do_publish: bool, **plugin_opts):
    """Run the pipeline, or replay it from any stage for an existing episode.

    Default cut is the speechless cinematic one; every run ends with thumbnail,
    short and the QA gate. Add --publish to upload (private) when QA passes.

    \b
      ttywood run --repo jdx/mise          new episode about a GitHub repo
      ttywood run --path . --style showcase   a demo of the project you are in
      ttywood run --repo jdx/mise --publish   ...and upload it as private
      ttywood run mise                     redo mise from record onwards
      ttywood run mise --from render       re-render only (+ everything after)
      ttywood run mise --from voice --tts elevenlabs    voiced cut instead
      ttywood run mise --focus "using mise inside CI pipelines"
    """
    cinematic = tts_engine is None
    short_take = None  # the Short's narrow take, when it runs during render
    if do_publish:
        to_stage = "publish"
    ep: Path | None = _episode_dir(episode) if episode else None
    if ep and (focus or lang):
        # a focused or translated take is always a NEW episode beside the original
        cand = json.loads((ep / "candidate.json").read_text())
        tool = cand.get("tool", ep.name)
        if focus:
            name = as_name or _focus_name(tool, focus)
            if lang and lang != "en":
                name += f"-{lang}"
        else:
            name = as_name or f"{ep.name}-{lang}"
        ep = _fork_episode(ep, name)
        click.echo(f"[{'focus' if focus else 'lang'}] new episode: {ep}")
    if from_stage is None:
        from_stage = ("rehearse" if (focus or lang or style) else "record") if ep else "discover"
    start, stop = STAGES.index(from_stage), STAGES.index(to_stage)
    if stop < start:
        raise click.UsageError("--to must not be before --from")
    if ep is None and start > 0:
        raise click.UsageError("an EPISODE is required when starting after discover")
    if ep is not None and start > 0 and not ep.exists():
        raise click.UsageError(f"episode not found: {ep}")

    def active(stage: str) -> bool:
        return start <= STAGES.index(stage) <= stop

    if ep is not None and style:
        _set_style(ep, style)
    plan = [st for st in STAGES[start:stop + 1]
            if not (st == "voice" and cinematic)]
    from . import ui
    ui.plan_panel(plan, f"episode {ep.name}" if ep else "a new episode",
                  "speechless cinematic" if cinematic else "voiced")
    run_ui = ui.Runner(plan)

    if active("discover"):
        with run_ui.stage("discover") as st:
            from .plan import candidate_for_path, candidate_for_repo
            if series_slug:
                from .series import start_next
                st.activity(f"taking the next episode of {series_slug}")
                ep = start_next(series_slug, EPISODES, log=lambda m: st.note(m.strip()))
                st.result(str(ep))
                start = STAGES.index("rehearse")   # the series plan is the pick
            elif repo_arg or path_arg:
                st.activity(f"planning the episode for {repo_arg or path_arg}")
                ep = (candidate_for_path(EPISODES, path_arg, prefer=prefer, as_name=as_name,
                                         log=lambda m: st.note(m.strip()), style=style or "")
                      if path_arg else
                      candidate_for_repo(EPISODES, repo_arg, prefer=prefer, as_name=as_name,
                                         log=lambda m: st.note(m.strip()), style=style or ""))
                if lang:
                    cand_path = ep / "candidate.json"
                    cand = json.loads(cand_path.read_text())
                    cand["lang"] = lang
                    cand_path.write_text(json.dumps(cand, indent=2, ensure_ascii=False))
                _set_style(ep, style)
                st.result(str(ep))
                start = STAGES.index("rehearse")   # nothing to discover
            else:
                provider = plugins.discover_provider()
                if provider is None:
                    raise click.UsageError(
                        "nothing to make: pass --repo owner/name, --path DIR, --series SLUG or an "
                        "existing EPISODE (picking a subject on its own is left to plugins)")
                st.activity("picking a subject")
                ep = provider(EPISODES, prefer=prefer, log=lambda m: st.note(m.strip()),
                              **plugin_opts)
                _set_style(ep, style)
                if lang:
                    cand_path = ep / "candidate.json"
                    cand = json.loads(cand_path.read_text())
                    cand["lang"] = lang
                    cand_path.write_text(json.dumps(cand, indent=2, ensure_ascii=False))
                st.result(str(ep))
    assert ep is not None
    if active("rehearse"):
        with run_ui.stage("rehearse", ui.HINTS["rehearse"] + " (10-30 min)") as st:
            from .rehearse import rehearse_episode
            st.activity("starting the rehearsal container")

            def on_event(kind: str, text: str):
                if kind == "command":
                    st.command(text)
                    st.activity("running: " + text)
                elif kind == "write":
                    st.note(f"writes {text}", "cyan")
                    st.activity("writing " + text)
                elif kind == "text":
                    st.note(text)
                elif kind == "result":
                    st.activity("rehearsal finished, validating the screenplay")

            rehearse_episode(ep, focus=focus, lang=lang, on_event=on_event)
            from .screenplay import load_screenplay
            blocked = load_screenplay(ep / "screenplay.yaml").meta.blocked
            if blocked and "docker" in blocked.lower():
                st.note(f"rehearsal blocked ({blocked}); rerunning with a Docker daemon", "yellow")
                cand_path = ep / "candidate.json"
                cand = json.loads(cand_path.read_text())
                cand["docker"] = True
                cand_path.write_text(json.dumps(cand, indent=2, ensure_ascii=False))
                rehearse_episode(ep, focus=focus, lang=lang, on_event=on_event)
            st.result("notes.md + screenplay.yaml" + (f" (focus: {focus})" if focus else "")
                      + (f" (lang: {lang})" if lang else ""))
    if active("record"):
        with run_ui.stage("record", ui.HINTS["record"] + " (real time, about the episode length)") as st:
            _record_with_repair(ep, st)
            st.result("demo.cast + timeline.json")
    if active("voice") and tts_engine and not cinematic:
        with run_ui.stage("voice", f"one TTS clip per scene ({tts_engine})") as st:
            from .voice import generate_voice
            generate_voice(ep, engine=tts_engine)
    if active("render"):
        with run_ui.stage("render", ui.HINTS["render"] + " (1-3 min)" if cinematic
                          else "time-warped terminal video + voice mux") as st:
            from .render import render_episode
            if cinematic:
                from .cinema import TERM_FONT_SIZE, render_cinematic
                st.activity("time-warping the cast and rendering the terminal body (agg)")
                render_episode(ep, use_voice=False, font_size=TERM_FONT_SIZE, body_only=True)
                st.note("terminal body rendered; compositing the cinematic cut")
                if active("shorts") and not crop_short and _wants_vertical_take(ep):
                    # the Short's narrow take is a real-time replay in its own
                    # container; the composite is CPU work. Overlap them.
                    short_take = _start_vertical_take(ep, st)

                def on_frames(secs: float, total: float, label: str):
                    st.total(total)
                    st.advance(completed=min(secs, total))
                    st.activity(f"{label}  ({ui.fmt_secs(secs)} of video)")

                render_cinematic(ep, _music_arg(music, no_music), on_progress=on_frames)
            else:
                render_episode(ep, use_voice=bool(tts_engine) or None)
            st.result("final.mp4")
    if active("narrate"):
        with run_ui.stage("narrate") as st:
            from .narration import write_narration
            write_narration(ep)
            st.result("narration.md + narration.srt")
    if active("meta"):
        with run_ui.stage("meta", ui.HINTS["meta"] + " (1-2 min)") as st:
            from .metadata import write_metadata
            st.activity("waiting for Claude")
            write_metadata(ep)
            st.result("youtube.md")
    if active("thumbnail"):
        with run_ui.stage("thumbnail") as st:
            from .thumbnail import make_thumbnail
            make_thumbnail(ep)
            st.result("thumbnail.png")
    if active("shorts"):
        with run_ui.stage("shorts", "vertical take (narrow terminal) + 9:16 cut") as st:
            if (ep / "warp.json").exists() and json.loads(
                    (ep / "render_meta.json").read_text()).get("body_timeline"):
                from .screenplay import load_screenplay
                from .shorts import record_short, render_short
                native = not crop_short
                if native and not _wants_vertical_take(ep):
                    # a TUI redraws itself to the terminal's width: at the Short
                    # take's 62 columns zellij collapses its status bar to
                    # "Ctrl + g p t n h s o q", so the episode's waits (and the
                    # thing being taught) are gone. Cut those Shorts from the
                    # wide recording instead.
                    native = False
                    st.note("full-screen TUI: cutting the Short from the wide take", "yellow")
                if native and short_take is not None:
                    st.activity("waiting for the vertical take started during render")
                    err = short_take()
                    if err:
                        st.note(f"vertical take failed ({err}); cutting from the wide recording", "yellow")
                        native = False
                    else:
                        st.note("vertical take recorded alongside the render")
                elif native:
                    # a Short recorded for the format: cropping the 120-column
                    # episode leaves half the lines off a phone screen
                    st.activity("recording the vertical take")
                    st.note("recording a narrow-terminal take for the Short")
                    try:
                        record_short(ep, on_progress=lambda kind, text: st.activity(f"{kind}: {text[:80]}"))
                    except Exception as e:
                        st.note(f"vertical take failed ({e}); cutting from the wide recording", "yellow")
                        native = False
                if not native:  # a half-written take must not be mistaken for a good one
                    (ep / "short.cast").unlink(missing_ok=True)
                    (ep / "short_timeline.json").unlink(missing_ok=True)
                render_short(ep, native=native or None)
                st.result("short.mp4")
            else:
                st.result("skipped: voiced cut has no body timeline")
    qa_ok = True
    if active("qa"):
        with run_ui.stage("qa", ui.HINTS["qa"] + " (2-4 min)") as st:
            from .qa import run_qa
            qa_ok, report = run_qa(ep, log=lambda m: (st.note(m), st.activity(m)))
            st.result(f"{'PASS' if qa_ok else 'FAIL'} -> {report}")
    if active("publish"):
        if not qa_ok:
            ui.console.print(f"\n  [red]publish skipped: QA failed, see {ep / 'qa.md'}[/]")
            run_ui.summary(ep)
            sys.exit(1)
        with run_ui.stage("publish", ui.HINTS["publish"] + " (about 50 quota units per upload in practice)") as st:
            from .publish import publish as _publish, unchanged_since_publish
            urls = []
            for is_short in (False, True):
                if is_short and not (ep / "short.mp4").exists():
                    continue
                label = "short" if is_short else "episode"
                if unchanged_since_publish(ep, short=is_short):
                    st.note(f"{label}: unchanged since last upload, skipped")
                    continue
                size = (ep / ("short.mp4" if is_short else "final.mp4")).stat().st_size / 1e6
                st.total(1.0)
                st.activity(f"uploading the {label} ({size:.0f} MB)")
                res = _publish(ep, privacy="private", short=is_short, replace=True,
                               on_progress=lambda f: st.advance(completed=f))
                st.note(f"{label}: {res['url']}" + (" (replaced previous)" if res.get("replaced") else ""), "green")
                urls.append(res["url"])
                if not is_short:
                    cand = json.loads((ep / "candidate.json").read_text())
                    if cand.get("series"):
                        from .series import record_video
                        record_video(cand["series"]["slug"], ep.name, res["url"])
            plugins.after_publish(ep, lambda m: st.note(m))
            st.result(" ".join(urls) or "nothing to upload")
    from .index import write_index
    write_index(EPISODES)
    run_ui.summary(ep)
    if active("qa") and not qa_ok:
        sys.exit(1)


# plugins add their commands (and `run` options) last, so they can see and
# extend everything above
plugins.register_all(main)


if __name__ == "__main__":
    main()
