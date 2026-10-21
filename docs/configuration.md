# Configuration

ttywood ships with neutral defaults: no channel name, no logo, no welcome
line. Everything that makes the videos look like *your* channel is a config
value. [`ttywood/defaults.toml`](../ttywood/defaults.toml) lists every key
with a comment.

Config files are merged key by key, later wins:

1. `ttywood/defaults.toml` (shipped)
2. files contributed by installed [plugins](plugins.md)
3. `~/.config/ttywood/config.toml`
4. `./ttywood.toml` in the current directory
5. `$TTYWOOD_CONFIG` (a path, or several separated by `:`)

Relative paths inside a file are resolved against that file's directory, so a
config can sit next to its logo. `ttywood config` prints which files applied
and the result; `ttywood config --json` prints all of it.

## A channel config

```toml
# ~/.config/ttywood/config.toml
[paths]
episodes = "~/videos/episodes"

[show]
# how every prompt describes what is being made
pitch = '"shellshock", a YouTube channel that tries one new CLI tool a week'

[brand]
name = "shellshock"
accent_chars = 5            # "shell" in the accent colour, "shock" in white
slogan = "One CLI tool a week, tried for real."
author = "shellshock by Ada"
logo = "logo.png"
logo_crop = []              # [left, top, right, bottom] to use part of the image

[cards]
final_title = "thanks for watching"
final_subtitle = "new episode every Friday"

[youtube]
description_footer = "New episode every Friday: youtube.com/@shellshock"

[narration.welcome]         # voiced cut only (--tts): spoken over the title card
en = "Welcome to shellshock. Today: {tool}{tagline}."

[narration.signoff]
en = "That's the episode. See you next Friday."
```

With a `brand.name` the video opens on a welcome card (logo, name, slogan),
every frame carries a small watermark, and the name appears on the
thumbnail and the Short. Without one, the video opens on the tool's own
title card and there is no channel mark anywhere.

## Fonts

`fonts.ui`, `fonts.mono` and `fonts.icons` are lists; the first file that
exists wins, so one config works on macOS and Linux. On Debian/Ubuntu
`apt install fonts-dejavu-core` covers the defaults. A Nerd Font in
`fonts.icons` adds the GitHub mark to Shorts.

## Prompts

Every LLM prompt is a Markdown file in `ttywood/prompts/`. To change one,
copy it into a directory listed in `paths.prompts` (or a plugin's
`PROMPT_DIRS`) under the same name and edit the copy. `{{SHOW}}` is replaced
with `show.pitch` everywhere.

## Environment

| variable | used for |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | the agent inside the rehearsal container (required) |
| `TTYWOOD_DEMOS` | overrides `paths.episodes` |
| `TTYWOOD_CONFIG` | extra config files |
| `TTYWOOD_LLM` | host-side LLM: `claude` (default), `omp`, `openai-compat`, `shell` (see `ttywood/llm.py`) |
| `TTYWOOD_NO_PLUGINS=1` | ignore installed plugins |
| `GITHUB_TOKEN` | higher GitHub API limits for repo facts |
| `ELEVENLABS_API_KEY`, `OPENAI_API_KEY` | voiced cuts (`--tts elevenlabs` / `--tts openai`) |

Secrets can also live in a gitignored `.env` (KEY=value lines) in
`~/.config/ttywood/`, in the checkout, or in any file listed in
`paths.env_files`. A variable already set in the shell wins.

YouTube uploads need an OAuth client at `~/.config/ttywood/client_secret.json`
(Google Cloud console, YouTube Data API v3, Desktop app); `ttywood auth`
signs in without a local browser.
