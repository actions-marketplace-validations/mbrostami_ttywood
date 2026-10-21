"""Clean-room check: Claude Code, inside a fresh container, follows a project's
own docs like a new user and files a factual test report (report.json +
report.md). No screenplay, no recording: this is the part of the rehearsal
that finds "the README does not work on a clean machine", on its own, cheap
enough to run on every release from a GitHub Action.

report.json is the unit of the ttywood corpus: one commit of one repo, what a
stranger's install looked like, what broke. Its shape is `Report` below and
prompts/check.md; keep the two in sync (schema version bump on change)."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import load_prompt
from .rehearse import IMAGE, _auth_flags, _run_claude, _sh, start_dockerd

SCHEMA = 1
MAX_FIX_ROUNDS = 2
# env = a limit of our sandbox, not the project's fault (no Docker daemon,
# no GPU, no kernel module); it never counts against the project
Severity = Literal["blocker", "friction", "nit", "env"]
# blocked = the documented path could not be tried here (env findings only)
Verdict = Literal["pass", "warn", "fail", "blocked"]


class Install(BaseModel):
    method: str = ""
    documented: bool = True
    ok: bool = False
    seconds: float = 0
    commands: list[str] = Field(default_factory=list)
    notes: str = ""


class Step(BaseModel):
    command: str = ""
    ok: bool = False
    seconds: float = 0


class Example(Step):
    name: str = ""
    note: str = ""


class Finding(BaseModel):
    severity: Severity
    title: str
    detail: str = ""
    command: str = ""
    fix: str = ""
    docs: str = ""


class Environment(BaseModel):
    os: str = "ubuntu 24.04"
    arch: str = ""
    privileged: bool = False
    docker: bool = False      # a Docker daemon was available inside the container
    image: str = IMAGE


class Report(BaseModel):
    schema_: int = Field(default=SCHEMA, alias="schema")
    repo: str
    url: str = ""
    ref: str = ""
    ref_date: str = ""
    tool: str = ""
    verdict: Verdict
    summary: str = ""
    install: Install = Field(default_factory=Install)
    first_run: Step = Field(default_factory=Step)
    examples: list[Example] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    environment: Environment = Field(default_factory=Environment)
    minutes_spent: float = 0
    checked_at: str = ""          # filled by us, UTC
    ttywood: str = ""             # filled by us: pipeline version
    cost: dict = Field(default_factory=dict)  # filled by us: cost_usd, tokens_in/out, turns, seconds

    model_config = {"populate_by_name": True}

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    def consistent_verdict(self) -> Verdict:
        """The verdict the rules in prompts/check.md imply from the facts."""
        broken = self.count("blocker") or not self.install.ok or not self.first_run.ok
        if broken and self.count("env") and not self.count("blocker"):
            return "blocked"  # our sandbox stopped it, the project may be fine
        if broken:
            return "fail"
        if self.count("friction"):
            return "warn"
        return "pass"


def load_report(path: Path) -> Report:
    return Report.model_validate(json.loads(path.read_text()))


def report_errors(path: Path) -> str | None:
    if not path.exists():
        return f"{path.name} was not written"
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        return f"{path.name} is not valid JSON: {e}"
    try:
        Report.model_validate(data)
    except ValidationError as e:
        return "\n".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
    return None


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("ttywood")
    except Exception:
        return "0"


def repo_slug(target: str) -> tuple[str, str]:
    """'owner/name' or a GitHub URL -> (owner/name, https url)."""
    m = re.match(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", target.strip())
    if not m:
        raise ValueError(f"not a GitHub repo: {target!r} (want owner/name or a github.com URL)")
    slug = f"{m.group(1)}/{m.group(2)}"
    return slug, f"https://github.com/{slug}"


def run_check(episode_dir: Path, repo: str, ref: str = "", privileged: bool = False,
              docker: bool | None = None, on_event=None) -> Report:
    """Run the check in a fresh container; returns the validated report.
    Writes report.json, report.md, check.log and check_prompt.md into episode_dir.
    docker=True starts a Docker daemon inside the (then privileged) container
    so docker-first install docs can be followed as written."""
    episode_dir = episode_dir.resolve()
    episode_dir.mkdir(parents=True, exist_ok=True)
    slug, url = repo_slug(repo)
    if docker is None:  # auto: the README leads with Docker -> give it a daemon
        from .plan import readme_wants_docker
        docker = readme_wants_docker(slug)
        if docker and on_event:
            on_event("text", "README leads with Docker: running the container with a Docker daemon inside")
    privileged = privileged or docker
    cand_path = episode_dir / "candidate.json"
    if not cand_path.exists():  # a check-only directory still shows up in the ledger
        cand_path.write_text(json.dumps({
            "repo": slug, "url": url, "tool": slug.split("/")[1],
            "angle": "the tool as its README presents it: install, first run, then the "
                     "documented examples, with whatever a new user trips over shown honestly",
            "privileged": privileged, "docker": docker, "check_only": True}, indent=2))
    prompt = load_prompt("check.md")
    docker_line = ("A Docker daemon is running in this container: `docker run`, `docker compose` "
                   "and the project's Docker install path work and should be followed as documented."
                   if docker else
                   "There is NO Docker daemon here and none can be started. If the only documented "
                   "install path is Docker, try it once, record the failure as a finding with "
                   "severity `env`, then fall back to the next documented path (npm, pip, binary "
                   "release, source build). If no non-Docker path is documented, stop after the "
                   "clone: install.ok false, the `env` finding, verdict `blocked`.")
    for key, val in (("REPO", slug), ("URL", url), ("REF", ref), ("DOCKER_LINE", docker_line),
                     ("PRIVILEGED", "true" if privileged else "false")):
        prompt = prompt.replace("{{" + key + "}}", str(val))
    (episode_dir / "check_prompt.md").write_text(prompt)
    for name in ("report.json", "report.md"):
        (episode_dir / name).unlink(missing_ok=True)

    container = f"ttywood-chk-{episode_dir.name}"
    log_path = episode_dir / "check.log"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    priv = ["--privileged"] if privileged else []
    _sh(["docker", "run", "-d", "--name", container, *priv,
         "-v", f"{episode_dir}:/episode", *_auth_flags(), IMAGE, "sleep", "infinity"])
    stats: dict = {}
    try:
        if docker:
            driver = start_dockerd(container)
            if on_event:
                on_event("text", f"docker daemon up inside the container ({driver})")
        rc = _run_claude(container, "/episode/check_prompt.md", log_path, on_event=on_event,
                         stats=stats)
        if rc != 0:
            raise RuntimeError(f"check claude exited {rc}; see {log_path}")
        for round_ in range(MAX_FIX_ROUNDS + 1):
            err = report_errors(episode_dir / "report.json")
            if err is None:
                break
            if round_ == MAX_FIX_ROUNDS:
                raise RuntimeError(f"report.json still invalid: {err}")
            fix = episode_dir / "fix_prompt.md"
            fix.write_text("The file /episode/report.json failed validation against the schema "
                           "in the brief. Rewrite it in place, same facts, exact shape.\n\n"
                           f"Validation errors:\n{err}\n")
            if on_event:
                on_event("text", f"report invalid (round {round_ + 1}): {err[:200]}; asking Claude to fix it")
            _run_claude(container, "/episode/fix_prompt.md", log_path, continue_session=True,
                        on_event=on_event, stats=stats)
        arch = subprocess.run(["docker", "exec", container, "uname", "-m"],
                              capture_output=True, text=True).stdout.strip()
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    report = load_report(episode_dir / "report.json")
    report.environment.arch = report.environment.arch or arch
    report.environment.privileged = privileged
    report.environment.docker = docker
    report.environment.image = IMAGE
    report.checked_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report.ttywood = _version()
    report.cost = stats
    implied = report.consistent_verdict()
    md = episode_dir / "report.md"
    if implied != report.verdict:  # the facts win over Claude's wording, in both files
        report.verdict = implied
        if md.exists():
            md.write_text(re.sub(r"(?i)\bverdict:?\s*\**\s*(pass|warn|fail)\b",
                                 f"Verdict: {implied.upper()}", md.read_text(), count=1))
    payload = json.dumps(report.model_dump(by_alias=True), indent=2, ensure_ascii=False) + "\n"
    (episode_dir / "report.json").write_text(payload)
    # history: one file per check, so a repo's page can show verdict over commits
    hist = episode_dir / "reports"
    hist.mkdir(exist_ok=True)
    (hist / f"{report.checked_at.replace(':', '')}_{report.ref[:7] or 'noref'}.json").write_text(payload)
    if not md.exists():
        md.write_text(report_markdown(report))
    return report


def report_markdown(r: Report) -> str:
    """A deterministic report.md from the JSON (used when Claude's own is
    missing, and by the Action for the job summary)."""
    icon = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "blocked": "BLOCKED"}[r.verdict]
    lines = [f"# ttywood check: {r.repo}", "",
             f"**{icon}** {r.summary}", "",
             f"Commit `{r.ref[:12]}` ({r.ref_date}), checked {r.checked_at[:10]} on "
             f"{r.environment.os} {r.environment.arch}"
             + (" (privileged container)" if r.environment.privileged else "") + ".", "",
             "## Install", "",
             f"{r.install.method or 'unknown method'}, {'ok' if r.install.ok else 'FAILED'}, "
             f"{r.install.seconds:.0f} s" + (", not the documented path" if not r.install.documented else ""),
             "", "```"] + r.install.commands + ["```"]
    if r.install.notes:
        lines += ["", r.install.notes]
    lines += ["", "## First run", "",
              f"`{r.first_run.command}`: {'ok' if r.first_run.ok else 'FAILED'} ({r.first_run.seconds:.0f} s)"]
    if r.examples:
        lines += ["", "## Examples", ""]
        for ex in r.examples:
            lines.append(f"- {ex.name}: `{ex.command}` {'ok' if ex.ok else 'FAILED'} "
                         f"({ex.seconds:.0f} s)" + (f". {ex.note}" if ex.note else ""))
    lines += ["", "## Findings", ""]
    if not r.findings:
        lines.append("None. The docs worked as written.")
    for f in r.findings:
        lines.append(f"- **{f.severity}** {f.title}. {f.detail}"
                     + (f" Command: `{f.command}`." if f.command else "")
                     + (f" Fix: {f.fix}" if f.fix else "")
                     + (f" (docs: {f.docs})" if f.docs else ""))
    cost = f", ${r.cost['cost_usd']:.2f} of model time" if r.cost.get("cost_usd") else ""
    lines += ["", f"Time spent: {r.minutes_spent:.0f} min{cost}. ttywood {r.ttywood}.", ""]
    return "\n".join(lines)
