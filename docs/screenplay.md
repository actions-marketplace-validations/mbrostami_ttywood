# The screenplay

`screenplay.yaml` is the contract between whoever decides what happens on
camera (the rehearsing agent, your own agent, or you) and the recorder that
performs it. The recorder replays it keystroke by keystroke in a fresh
container, so everything in it must work from a clean machine, in order, with
no human answering prompts.

```bash
ttywood schema                      # JSON schema of the whole file
ttywood validate path/to/episode    # exit 1 with the errors if it is invalid
```

## Shape

```yaml
meta:
  tool: fzf                         # name on the title card
  repo: junegunn/fzf                # optional: stars, contributors, URL on the cards
  tagline: "a command-line fuzzy finder"
  lang: en                          # en | fa: language of the narration
  terminal: {cols: 120, rows: 30}
  short_scene: filter               # optional: the scene the Short is cut from
  privileged: false                 # loop devices, FUSE, mounts
  host: zellij                      # zellij (default) | tmux (for multiplexer episodes)

scenes:
  - id: install                     # becomes the chapter title ("Install")
    narration: >                    # captions in the speechless cut, voice with --tts
      Installation is a single script.
    commands:
      - type: "./install --bin"     # typed with human-like timing, then Enter
        wait_for: {regex: "fzf executable", timeout: 300}
      - type: "./bin/fzf --version"
        post_pause: 1.5             # seconds to linger so viewers can read

  - id: outro
    narration: "One script, instant startup, composes with everything."
    dwell: 2                        # a narration-only closing scene

verdict:                            # optional: drawn as closing cards
  use_cases: ["filter any list in a pipe", "history search", "file picker"]
  bottom_line: "The fuzzy finder everything else integrates with."
```

## Commands

Each command is exactly one of:

| field | what it does |
|---|---|
| `type: "..."` | types the text character by character, then presses Enter (`enter: false` to skip it) |
| `keys: [...]` | sends named keys: `Enter`, `Tab`, `S-Tab`, `Esc`, `Space`, `Backspace`, `Up`/`Down`/`Left`/`Right`, `C-<letter>`, `M-<char>`, `M-Up`..., or one character |
| `web: [...]` | browser steps in a headless Chromium, recorded and cut in beside the terminal |
| `zellij: "..."` | a zellij layout action: `new-pane -d right`, `move-focus left`, `close-pane`, ... |

Timing fields on every command: `pre_pause` (default 0.9 s, the "thinking"
pause before typing), `post_pause` (0.8 s), and `wait_for`.

## Waiting

`wait_for` decides when a command is done. Getting it right is most of the
work of a screenplay that records on the first take.

| wait | done when |
|---|---|
| `{prompt: true}` (default) | the last non-empty line matches `meta.prompt_regex` (the image's `demo ~/dir $` prompt) |
| `{regex: "..."}` | the regex appears anywhere on screen |
| `{stable: 2}` | the screen has not changed for 2 seconds (REPLs, TUIs) |
| `{url: "http://127.0.0.1:5173"}` | that URL answers 2xx/3xx from inside the container (dev servers) |

Every wait takes `timeout` (default 180 s) and `settle` (0.6 s extra dwell).
Inside another program (a REPL, a TUI, an AI CLI) the shell prompt never
comes back: use `regex` on that program's own prompt or `stable`.

Waits are fast-forwarded in the render (at most a few seconds on screen),
while typing and `post_pause` play in real time, so a slow install costs the
viewer nothing.

## Web steps

```yaml
- web:
    - {action: goto, url: "http://127.0.0.1:8080"}
    - {action: fill, target: "label:Username", text: "admin"}
    - {action: click, target: "role:button:Log in"}
    - {action: click, target: "text:Skip", optional: true}   # only if it shows up
```

Actions: `goto`, `click`, `fill`, `type`, `press`, `hover`, `wait`, `scroll`.
Targets: a CSS selector, `text:...`, `role:button:Name`, `label:...`,
`placeholder:...`. `label:` on the step sets what the command bar shows.
See `examples/filebrowser_screenplay.yaml`.

## Rules that save takes

- Nothing may wait for a human. Use non-interactive flags, or answer the
  prompt with a `keys` step that waits for the prompt text first.
- Never put a secret on screen. Pass it with the episode's `env` field (see
  [agents.md](agents.md)) and reference it as `"$NAME"` inside commands.
- A file written through a heredoc is shown line by line in the command bar;
  keep lines under about 60 characters.
- Do not stage mistakes. If something real breaks, it stays in; nothing is
  broken on purpose.
- If a take fails, `ttywood record` (via `run`) hands the failure to the LLM,
  which edits the screenplay and retries, up to two times.
