# ttywood in your repo

Two things a project gets from ttywood without anyone recording anything by
hand:

1. **A clean-room check.** A fresh Ubuntu 24.04 container follows your docs
   the way a new user would: clone, the install path the README recommends,
   the first run, the examples the README leads with. Every command is timed,
   every place it stalls is written down with the exact error and the fix
   that got past it. Output: `report.md`, `report.json`, a badge.
2. **A demo that never goes stale.** The same run can continue into a
   recorded terminal session (real shell, human-paced typing, zellij +
   asciinema), rendered into a README-sized GIF of the tool doing its best
   trick. Re-run on every release and the GIF matches the code.

Everything lands in `.ttywood/` in the project and arrives as a pull
request, so nothing changes without a maintainer's review.

```
.ttywood/
  badge.svg           verdict + date, for the README
  badge.json          the same as a shields.io endpoint payload
  report.md           the findings, human readable
  report.json         the findings, machine readable (schema below)
  readme.gif          one scene of the recorded demo (demo mode only)
  README-snippet.md   the block to paste into the README once
```

## Install

Add a workflow (see `examples/workflow-check.yml`, `examples/workflow-demo.yml`)
and a Claude credential as a repository secret: an `ANTHROPIC_API_KEY`, or
the token `claude setup-token` prints (pass it as `claude-code-oauth-token`):

```yaml
- uses: actions/checkout@v4
- uses: mbrostami/ttywood@v1
  with:
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    mode: check            # or: demo
```

Then paste `.ttywood/README-snippet.md` into the README, once:

```markdown
[![ttywood clean-room check](.ttywood/badge.svg)](.ttywood/report.md)

[![zoxide demo](.ttywood/readme.gif)](https://youtu.be/...)

<sub>Checked by ttywood on 2026-08-28 at commit `1a2b3c4`: ... [Full report](.ttywood/report.md).</sub>
```

The paths are relative, so the badge and the GIF render on GitHub from the
repo itself. No hosting is involved; when the hosted badge endpoint exists,
`badge.json` is already the payload it serves
(`https://img.shields.io/endpoint?url=...`), and `report-url` is the input
that posts each report to it.

## Inputs

| input | default | what |
|---|---|---|
| `anthropic-api-key` | | for the in-container Claude Code run; this or the next one is required |
| `claude-code-oauth-token` | | a `claude setup-token` token instead of an API key |
| `mode` | `check` | `check` (about 15 min) or `demo` (check + rehearse + record + render, 30 to 60 min) |
| `repo` | this repository | `owner/name` to check |
| `ref` | the triggering sha | commit, tag or branch |
| `privileged` | `false` | privileged container (FUSE, loop devices, mount) |
| `docker` | `false` | a Docker daemon inside the container (implies privileged), for docker-first install docs |
| `commit` | `true` | open a PR updating `.ttywood/` |
| `open-issue` | `false` | file an issue with the findings on a `fail` verdict |
| `fail-on` | `never` | `fail` or `warn` turns the job into a CI gate |
| `asset-dir` | `.ttywood` | where the files go |
| `image` | (build) | a prebuilt `ttywood` image to pull instead of building (5 min cold, cached after) |
| `report-url` | (none) | POST `report.json` here when done |

Outputs: `verdict`, `summary`, `findings`, `report`, `pull-request`. The
report is also written to the job summary and uploaded as an artifact
(`final.mp4` too in demo mode).

## Verdicts

- `pass`: the docs work as written; nits at most.
- `warn`: it installs and runs, but a new user hits real friction on the
  way (an undocumented env var, a stale flag, an interactive prompt).
- `fail`: the documented install or the first run needs a fix the docs do
  not mention (a missing dependency, a build error, an example that errors).
- `blocked`: the sandbox could not follow the documented path (no Docker
  daemon, no GPU, no kernel module) and the docs offer no alternative. Not
  the project's fault; the badge says "not checkable here". Docker-first
  projects: set `docker: true` and the check runs a daemon inside the
  container.

The verdict is derived from the facts in the report (any blocker or a failed
install/first run is `fail`; `env` findings alone make it `blocked`; any
friction is `warn`), not from the model's mood: `check.py` recomputes it
after validation. Finding severities: `blocker`, `friction`, `nit`, and
`env` (a limit of the container, never counted against the project).

## report.json

Schema version 1 (`ttywood/check.py: Report`, mirrored in `ttywood/prompts/check.md`):

```json
{
  "schema": 1,
  "repo": "owner/name", "url": "https://github.com/owner/name",
  "ref": "<commit sha>", "ref_date": "YYYY-MM-DD",
  "tool": "name",
  "verdict": "pass | warn | fail",
  "summary": "one sentence",
  "install": {"method": "cargo install --locked", "documented": true, "ok": true,
              "seconds": 74, "commands": ["..."], "notes": ""},
  "first_run": {"command": "name --version", "ok": true, "seconds": 0.2},
  "examples": [{"name": "...", "command": "...", "ok": true, "seconds": 1, "note": ""}],
  "findings": [{"severity": "blocker | friction | nit", "title": "...", "detail": "...",
                "command": "...", "fix": "...", "docs": "README#install"}],
  "environment": {"os": "ubuntu 24.04", "arch": "x86_64", "privileged": false,
                  "image": "ttywood:latest"},
  "minutes_spent": 12,
  "checked_at": "2026-08-28T10:00:00Z",
  "ttywood": "0.1.0"
}
```

One report is one commit of one repo seen by a stranger's machine. Many of
them, over time and across projects, are the corpus: which install methods
break most, which dependencies go undocumented, how long a first install
really takes. The Action's `report-url` input is where they will be
collected once there is a place to collect them.

## Running it locally

```bash
ttywood check owner/name                # report.json + report.md + badge.svg in <episodes>/name/
ttywood check owner/name --ref v1.2.0 --privileged
ttywood run name --from rehearse --to render   # demo on top of the check
ttywood gif name                        # readme.gif from the rendered episode
ttywood export name --to /path/to/project/.ttywood
```

`ttywood check` exits 1 on `fail`, so it works as a gate in any CI. The
`TTYWOOD_DEMOS` env var moves the episodes directory (the Action keeps them
in the runner's temp dir).

## What runs where

Only the project under test runs inside the container (no host mounts but
the episode directory). The container gets the API key as an environment
variable for the rehearsal; nothing from the runner's checkout is mounted.
Claude Code runs with permissions skipped inside that container, which is
why the container is disposable and the runner's own workspace is never
touched by it.
