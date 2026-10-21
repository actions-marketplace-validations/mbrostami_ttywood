# Notes for coding agents working on this repo

(To *use* ttywood from an agent, see `skills/ttywood/SKILL.md` and
`docs/agents.md`. This file is for changing ttywood itself.)

- Python 3.10+, click CLI in `ttywood/cli.py`. Install with
  `pip install -e '.[dev]'`; run `pytest` and `ruff check .` before
  finishing. Tests must not need Docker, network or tokens.
- The engine carries no branding. Do not hardcode a channel name, a person, a
  logo path or a font path; add a key to `ttywood/defaults.toml` and read it
  through `ttywood.config`.
- Prompts live in `ttywood/prompts/*.md` and are loaded with
  `config.load_prompt(name, KEY=value)`, which also fills `{{SHOW}}`.
- Episode state is files: `candidate.json` (what the episode is about),
  `screenplay.yaml` (validated by `ttywood/screenplay.py`), `timeline.json`,
  `warp.json`, `render_meta.json`. When adding a field, keep old episodes
  loading (default it).
- Containers are started in `rehearse.py`, `record.py` and `check.py`; what
  they receive from `candidate.json` (env, local source) is in `sandbox.py`.
- Never commit anything under `demos/`, `series/` or media files.
