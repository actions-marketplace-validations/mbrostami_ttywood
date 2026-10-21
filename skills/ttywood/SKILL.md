---
name: ttywood
description: Record a real terminal demo video of a CLI tool or of the project you are working on, with ttywood. Use when the user asks for a demo video, screencast, terminal recording, README GIF, YouTube video or Short of a command-line tool, or wants to show a feature working in a real shell.
---

# Recording a terminal video with ttywood

ttywood replays a screenplay keystroke by keystroke in a fresh Docker
container (asciinema + zellij), then cuts the recording into a captioned
1080p video, a vertical Short, a thumbnail and YouTube metadata, and runs a
QA pass. Everything is files on disk; every command below is
non-interactive.

## Before anything

```bash
ttywood doctor --json
```

Exit 1 means something required is missing. The usual fixes: start Docker,
`ttywood build-image` (about 5 minutes, once), export
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`.

## Pick a path

**You already know what to show** (you just built the feature): write the
screenplay yourself. It is faster and more precise.

```bash
ttywood new NAME --path . --tool TOOLNAME --tagline "one plain line" --json
ttywood schema          # JSON schema of screenplay.yaml
```

Write `<episode>/screenplay.yaml`:

```yaml
meta:
  tool: mytool
  tagline: "one plain line"
  terminal: {cols: 120, rows: 30}
scenes:
  - id: setup
    narration: "One or two sentences the viewer reads while this happens."
    commands:
      - type: "cd ~/myproject && make install"   # --path copies the project to ~/<dirname>
        wait_for: {regex: "installed", timeout: 300}
  - id: first-run
    narration: "What the viewer should notice in the output."
    commands:
      - type: "mytool --help | head -20"
        post_pause: 2
  - id: outro
    narration: "The one sentence to leave with."
    dwell: 2
```

Then:

```bash
ttywood validate NAME
ttywood run NAME --from record      # record, render, short, thumbnail, QA
ttywood info NAME                   # JSON with the paths of final.mp4 etc.
```

**You want ttywood to work it out** (a repo you have not used, or a full
review): it rehearses with its own agent in a container first.

```bash
ttywood run --path . --style showcase      # this project, as its own demo
ttywood run --repo owner/name              # a GitHub repo, as an honest review
```

Add `--focus "..."` to steer what the middle of the video is about.

## Rules for screenplays that record on the first take

- Test each command first in the same image:
  `docker run --rm -v "$PWD:/src:ro" ttywood:latest bash -lc 'cp -r /src ~/p && cd ~/p && CMD'`.
- Every command must finish without a human: non-interactive flags, or a
  `keys:` step that answers a prompt after a `wait_for: {regex: ...}` on it.
- Inside a REPL or TUI the shell prompt never returns: use
  `wait_for: {regex: "<that program's prompt>"}` or `wait_for: {stable: 2}`.
- Never print a secret. Put `"env": {"NAME": "HOST_VAR"}` in
  `candidate.json` and use `"$NAME"` in commands.
- Keep typed lines under about 60 characters; long heredocs are clipped in
  the command bar.
- Nothing staged: if something breaks for real it stays in, nothing is broken
  on purpose.

## Timing

Recording runs in real time (about the video's length), the render a few
minutes, QA a few more. With a rehearsal, add 10 to 30 minutes. Run long
commands in the background and poll `ttywood info NAME`.

## Results

`ttywood info NAME` lists what exists. `qa.md` starts with
`Verdict: PASS` or `Verdict: FAIL`; `run` and `ttywood qa` exit 1 on FAIL.
Read `qa.md` and the frames in `qa/` before telling the user the video is
done. `ttywood gif NAME` makes a README-sized GIF of one scene.

Full reference: docs/screenplay.md and docs/agents.md in the ttywood repo.
