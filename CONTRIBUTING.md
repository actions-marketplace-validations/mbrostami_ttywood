# Contributing

Thanks for helping. Bug reports, screenplays that broke the recorder,
renderer fixes and new plugins are all welcome.

## Setup

```bash
git clone https://github.com/mbrostami/ttywood && cd ttywood
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # fast tests: no Docker, no network, no tokens
.venv/bin/ruff check .
```

The end-to-end path needs Docker and the episode image (`ttywood
build-image`). The cheapest real test is the hand-written fzf screenplay in
the README's quick start: it records and renders without any LLM call.

## Layout

| path | what |
|---|---|
| `ttywood/cli.py` | every command; `run` is the pipeline |
| `ttywood/plan.py` | planning an episode from a repo, a local path, or nothing |
| `ttywood/rehearse.py` | the in-container agent run, screenplay validation and repair |
| `ttywood/record.py`, `typer_engine.py` | the performance pass: container, asciinema, typing |
| `ttywood/render.py` | the time warp and the plain cut |
| `ttywood/cinema.py` | the default speechless cut: cards, captions, command bar, sound |
| `ttywood/shorts.py`, `thumbnail.py`, `qa.py`, `publish.py` | what comes after |
| `ttywood/screenplay.py` | the screenplay schema (pydantic) |
| `ttywood/config.py`, `defaults.toml`, `plugins.py` | settings and extension points |
| `ttywood/prompts/` | every LLM prompt, as Markdown |
| `ttywood/docker/` | the episode image |

## Guidelines

- Keep the engine unbranded. Anything channel-specific is a config key in
  `defaults.toml` (with an empty or neutral default) or lives in a plugin.
- Prompts are code. A prompt change should say in the PR which episode or
  take it fixed, ideally with the failing `rehearse.log` excerpt.
- Generated prose goes through `screenplay.clean_text` (no em dashes, no
  emoji). Keep it that way.
- Never commit episode output (`demos/`, recordings, videos); `.gitignore`
  already covers it.
- Run with `TTYWOOD_NO_PLUGINS=1` when reproducing a bug, and say so.

## Reporting a bug

Include `ttywood doctor --json`, the command you ran, and the relevant part
of the episode's `rehearse.log` (or the output of `run`). For a recording or
render bug, the episode's `screenplay.yaml` and `timeline.json` usually let
us reproduce it without your machine.
