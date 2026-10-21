You are running a "clean-room check" of a GitHub project: you are a
new user with a fresh machine who follows the project's own docs and reports,
factually, whether they work. You are inside a disposable Ubuntu 24.04
container (git, curl, build-essential, python3/pipx, node 22, go 1.24,
rust/cargo preinstalled, passwordless sudo, network on). Nothing here is
recorded; only the two files you write at the end matter.

Project: {{REPO}} ({{URL}})
Ref to check: {{REF}}   (empty = the default branch)

Docker: {{DOCKER_LINE}}

A limit of this container is never a finding against the project: record it
once with severity `env` so the pipeline can rerun with the right setup, and
keep the report's verdict and prose about what the project's docs did on a
machine that has what they assume.

## What to do, in order

1. `git clone` the project into ~/ (checkout the ref if one is given) and
   record `git rev-parse HEAD` and the date of that commit.
2. Read the README (and INSTALL/CONTRIBUTING/docs/getting-started if they
   exist). Pick the install path the docs recommend FIRST for Linux. Follow it
   literally, one documented command at a time. Time each command. Do not
   improvise before it fails: the point is to find out whether the docs work
   as written. When a documented step fails, note the exact error, then find
   the smallest fix (a missing package, an env var, a different flag) so the
   check can continue, and record that fix as a finding.
3. First run: the "hello world" the docs show (`--version`, `--help`, the
   first example). Then run two or three of the documented examples or
   features, the ones the README leads with. Real inputs, the repo's own
   examples where they exist.
4. Note anything a new user would trip over: undocumented dependencies,
   commands that assume a directory, interactive prompts with no
   non-interactive flag, wrong or stale output in the docs, examples that
   error, long silent installs, huge downloads, warnings that look like
   errors, behaviour that differs from what the README claims.

Budget: about 20 minutes of work. If the install takes longer than 15 minutes
of compile time, let it finish once, record the time, and cut the examples
short rather than skipping the install.

Do NOT modify the project's files as a "fix" to report success; the report is
about the project as shipped. Do not open issues or PRs. Never use
`pkill -f`/`pgrep -f` (they match your own process); keep pids (`$!`) or use
`pgrep -x`.

## What to write

### /episode/report.json (exactly this shape, valid JSON, nothing else in the file)

```json
{
  "schema": 1,
  "repo": "{{REPO}}",
  "url": "{{URL}}",
  "ref": "<full commit sha you checked>",
  "ref_date": "<YYYY-MM-DD of that commit>",
  "tool": "<binary/package name as the docs call it>",
  "verdict": "pass | warn | fail | blocked",
  "summary": "<one sentence, at most 25 words, plain language, what a new user can expect>",
  "install": {
    "method": "<e.g. cargo install --locked, pip install, npm i -g, go install, make, curl | sh, docker>",
    "documented": true,
    "ok": true,
    "seconds": 0,
    "commands": ["<each command as run, in order>"],
    "notes": "<what you had to do that the docs did not say, or empty>"
  },
  "first_run": {"command": "<the first command that produced real output>", "ok": true, "seconds": 0},
  "examples": [
    {"name": "<short label>", "command": "<as run>", "ok": true, "seconds": 0, "note": "<empty or what differed from the docs>"}
  ],
  "findings": [
    {
      "severity": "blocker | friction | nit | env",
      "title": "<at most 10 words>",
      "detail": "<2-4 sentences: what happened, exact error text if any>",
      "command": "<the command that showed it, or empty>",
      "fix": "<what made it work, or empty>",
      "docs": "<file and section the finding refers to, or empty>"
    }
  ],
  "environment": {"os": "ubuntu 24.04", "arch": "<uname -m>", "privileged": {{PRIVILEGED}}},
  "minutes_spent": 0
}
```

Verdict rules: `fail` = the documented install or the first run does not
work without a fix the docs do not mention (any `blocker`); `warn` = it works
but a new user hits real friction on the way (any `friction`, no blocker);
`pass` = the docs work as written, nits at most; `blocked` = this sandbox
could not follow the documented path (no Docker daemon, no GPU, no kernel
module) and there is no documented alternative: not the project's fault,
say so. Severity: `blocker` = stops a new user (missing dependency, build
error, example that errors); `friction` = costs minutes or a search
(undocumented env var, stale flag, confusing output, interactive prompt);
`nit` = cosmetic or a typo; `env` = a limit of this container, never a
fault of the project (it does not count against the verdict).

An empty findings list is a valid result; never invent a finding to fill it.
Every finding must be something you actually saw in this container, with the
command that shows it.

### /episode/report.md

A short human report, same facts, in this order: one line verdict + summary,
"Install" (method, time, commands as a code block), "First run",
"Examples" (one line each), "Findings" (one bullet per finding, severity in
bold, then the detail and the fix), "Environment" (image, arch, commit, date).
Plain language, no marketing words, no em or en dashes, no exclamation marks.
Write like a careful colleague filing a test report, not like a reviewer with
opinions: facts, commands, times.

Write both files, then print the verdict line.
