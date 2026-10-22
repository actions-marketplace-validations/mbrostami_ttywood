<p align="center">
  <img src="https://raw.githubusercontent.com/mbrostami/ttywood/main/docs/assets/ttywood-logo.svg" alt="ttywood" width="420">
</p>

<p align="center">
  <b>Real terminal demos, recorded and edited by agents.</b><br>
  An agent tries a CLI tool for real in a container, a fresh container replays it with
  human-paced typing, and the recording comes out as a captioned video, a Short, a
  thumbnail and YouTube metadata.
</p>

<p align="center">
  <a href="https://www.youtube.com/@ttywood">Example videos on YouTube (@ttywood)</a> ·
  <a href="docs/agents.md">Use it from your agent</a> ·
  <a href="docs/screenplay.md">Screenplay format</a> ·
  <a href="LICENSE">MIT</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/mbrostami/ttywood/main/docs/assets/demo.gif" alt="ttywood rendering a recorded episode into a video, a thumbnail and a Short" width="900">
  <br><sub>Cut by <code>ttywood gif</code> from the episode in which ttywood films itself.</sub>
</p>

---

Every video on the [ttywood channel](https://www.youtube.com/@ttywood) was
made by this pipeline, start to finish: no screen recorder, no editing
software, no human at the keyboard.

## How it works

```
 repo or local project
        │
        ▼
 ┌─────────────┐   Claude Code runs headless in a disposable Ubuntu container:
 │  rehearse   │   clones or copies the project, installs it, uses it, and writes
 └─────────────┘   notes.md + screenplay.yaml (what to type, what to wait for, what to say)
        │
        ▼
 ┌─────────────┐   a FRESH container replays the screenplay keystroke by keystroke
 │   record    │   (asciinema + zellij, human typing rhythm); a failed take goes back
 └─────────────┘   to the LLM, which fixes the screenplay and retries
        │
        ▼
 ┌─────────────┐   waits fast-forwarded, typing at 1x, chapter cards, captions,
 │   render    │   a pinned command bar, synthesized key sounds, closing verdict cards,
 └─────────────┘   a 9:16 Short of the best scene, a thumbnail from a real frame
        │
        ▼
 ┌─────────────┐   hard checks (duration, audio, black frames) + an LLM looking at
 │     qa      │   frames across the video; then optional private YouTube upload
 └─────────────┘
```

What you get in the episode directory: `final.mp4` (1920x1080),
`short.mp4` (1080x1920), `thumbnail.png`, `youtube.md` (titles, description
with chapters, tags), `narration.srt`, `qa.md`, and every intermediate file,
so any stage can be re-run on its own.

Nothing is faked: every command on screen ran in the recorded session, and
the output is whatever the tool printed.

## Quick start

Requirements: macOS or Linux, Python 3.10+, Docker, `ffmpeg`, [`agg`](https://github.com/asciinema/agg),
and the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI.

```bash
brew install ffmpeg agg                  # Linux: apt install ffmpeg fonts-dejavu-core, agg from its releases
npm install -g @anthropic-ai/claude-code

pipx install git+https://github.com/mbrostami/ttywood
# or, to hack on it:
#   git clone https://github.com/mbrostami/ttywood && cd ttywood
#   python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

claude setup-token                       # prints a long-lived token
export CLAUDE_CODE_OAUTH_TOKEN=...       # or ANTHROPIC_API_KEY=sk-ant-...

ttywood build-image                      # the episode image, about 5 minutes, once
ttywood doctor                           # everything green?
```

See the renderer work in a minute, with no Docker and no tokens: the repo
ships a recorded episode (`examples/fzf-episode/`), and only `ffmpeg`, `agg`
and a font are needed to cut it:

```bash
git clone https://github.com/mbrostami/ttywood && cd ttywood
ttywood cinema examples/fzf-episode && open examples/fzf-episode/final.mp4
```

Check the recorder too, still without spending a token, by replaying a
hand-written screenplay in Docker:

```bash
ttywood new smoke-fzf --repo junegunn/fzf
cp examples/fzf_screenplay.yaml ~/ttywood/episodes/smoke-fzf/screenplay.yaml
ttywood record smoke-fzf && ttywood cinema smoke-fzf
open ~/ttywood/episodes/smoke-fzf/final.mp4
```

(`examples/` is in the repo; with a pipx install, download the file from GitHub.)

Then a real episode, end to end (30 to 60 minutes):

```bash
ttywood run --repo junegunn/fzf
```

## Making videos

```bash
# a GitHub repo, as an honest hands-on review
ttywood run --repo owner/name
ttywood run --repo owner/name --prefer "show the CI integration"

# the project you are working on, as its own demo; it does not need to be pushed
ttywood run --path . --style showcase

# your own screenplay, no rehearsal
ttywood new my-demo --path .
$EDITOR ~/ttywood/episodes/my-demo/screenplay.yaml     # see docs/screenplay.md
ttywood run my-demo --from record

# a focused take on an existing episode (a new episode beside it)
ttywood run fzf --focus "fzf inside git workflows"

# re-run from any stage
ttywood run fzf --from render            # re-render and everything after it
ttywood run fzf --from render --to render

# upload as private once QA passes (needs a YouTube OAuth client, see docs/configuration.md)
ttywood run fzf --from record --publish
```

`run` shows its plan, then one live row per stage with what is happening right
now: the commands the agent runs in the rehearsal, every command as it is
typed on camera, repairs, render progress, the QA verdict.

| stage | what it does | writes |
|---|---|---|
| discover | `--repo` / `--path`: the LLM reads the project and plans the episode | `candidate.json` |
| rehearse | Claude Code in Docker tries the tool for real | `notes.md`, `screenplay.yaml` |
| record | a fresh container, asciinema + zellij, human-typed replay | `demo.cast`, `timeline.json` |
| voice | only with `--tts say\|openai\|elevenlabs`: one TTS clip per scene | `voice/` |
| render | time warp, cards, captions, command bar, key sounds, music bed | `final.mp4` |
| narrate | timestamped script and subtitles | `narration.md`, `narration.srt` |
| meta | YouTube titles, description with chapters, tags | `youtube.md` |
| thumbnail | a real frame under big text | `thumbnail.png` |
| shorts | a narrow-terminal take of one scene, cut 9:16 | `short.mp4` |
| qa | hard checks plus an LLM review of frames; exit 1 on FAIL | `qa.md`, `qa/` |
| publish | only with `--publish`: private YouTube upload | `published.json` |

More:

- **Series.** `ttywood series plan "zellij" --repo zellij-org/zellij --episodes 6`
  plans an arc; `ttywood run --series zellij` makes the next episode, starting
  from a snapshot of the previous episode's container.
- **Web UIs.** Screenplays can drive a headless Chromium; the browser video is
  cut in beside the terminal ([format](docs/screenplay.md#web-steps)).
- **Languages.** `--lang fa` writes narration and metadata in Farsi (voiced
  cuts need `--tts elevenlabs` or `openai`).
- **Voiced cut.** `--tts say|openai|elevenlabs` instead of the default
  speechless cut; a pronunciation pass rewrites tool names for the ear first.
- **Other LLMs on the host.** Planning, metadata and QA go through
  `ttywood/llm.py`: `TTYWOOD_LLM=openai-compat` (Ollama, vLLM, OpenRouter),
  `omp` or `shell`. The in-container rehearsal always uses Claude Code.

## From your agent

ttywood is built to be driven by coding agents: non-interactive commands,
JSON where it matters (`doctor --json`, `info`, `new --json`, `schema`), exit
codes that mean something, and a screenplay format an agent can write
directly when it already knows what to show.

```bash
# Claude Code: install the skill as a plugin
/plugin marketplace add mbrostami/ttywood
/plugin install ttywood@ttywood

# any other agent: point it at skills/ttywood/SKILL.md
```

Then ask your agent for "a demo video of the feature we just built".
[docs/agents.md](docs/agents.md) has the details for any agent.

## Clean-room check and the GitHub Action

The rehearsal on its own is useful: `ttywood check owner/name` has a fresh
container follow a project's install docs like a new user and writes a
factual report (`report.json`, `report.md`, a badge), exit 1 on `fail`. The
[Action](docs/action.md) runs it on every release, optionally records a
README GIF, and opens a PR with the results:

```yaml
- uses: mbrostami/ttywood@v1
  with:
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # or claude-code-oauth-token
    mode: demo          # check = report + badge only
```

## Make it yours

The engine ships unbranded. Your channel name, logo, welcome line and the way
the prompts describe your show are [config](docs/configuration.md);
anything you want to keep out of a public repo (your own way of choosing
subjects, publishing extras, private prompts) is a [plugin](docs/plugins.md).

## Cost, time and safety

- A 40-line test project took about 7 minutes end to end; real tools with
  long installs take 30 to 60. Token cost depends on how much the agent has to try: a
  clean-room `check` of a small CLI cost about $0.30 in testing, and the
  report logs the cost of every check.
- The projects you film are untrusted code. They only run inside Docker; the
  only host directory mounted read-write is the episode directory, and a
  `--path` project is mounted read-only. **The rehearsal container does
  receive your Claude credentials** (that is how the agent inside it works),
  so film projects you are willing to run, and prefer an API key with a spend
  limit. See [SECURITY.md](SECURITY.md).
- Uploads are private; you flip them public after watching.

## Contributing

Bug reports and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
`pytest` runs the fast tests, no Docker or tokens needed.

## License

[MIT](LICENSE)
