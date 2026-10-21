You are rehearsing an episode of {{SHOW}}. You are inside a disposable Ubuntu
container (git, curl, build-essential, python3/pipx, node 22, go 1.24,
rust/cargo are preinstalled; you have passwordless sudo). Nothing you do here
is recorded — this is the rehearsal. Your job:

Tool: {{TOOL}} — {{SUBJECT}}
Episode angle: {{ANGLE}}

## THIS ENVIRONMENT

{{ENVIRONMENT}}

General rule, always: a limit of this container (no Docker daemon, no GPU,
no kernel module, no systemd, no browser on a display) is never shown as if
it were the project's problem. Never type a command on camera that you know
fails only because of this sandbox, and never narrate the sandbox's limits
as findings. The viewer must only ever see the project succeed or fail on
its own terms. If the sandbox blocks the only documented path, stop and say
so in notes.md instead of improvising a failure scene.

{{SOURCE_SECTION}}
{{STYLE_SECTION}}
{{FOCUS_SECTION}}
{{SERIES_SECTION}}

## LANGUAGE AND STYLE OF EVERYTHING YOU WRITE

Narration, tagline and notes.md are in **{{LANGUAGE}}** (`meta.lang: "{{LANG}}"`).
Commands, file names and tool output stay exactly as they are; only the
spoken/written prose is in {{LANGUAGE}}. Tool and command names are kept in
Latin script inside {{LANGUAGE}} sentences.

Write like a person talking to a friend, not like marketing copy or an AI
assistant:
- NEVER use em dashes or en dashes (— –). Use commas, periods, or "and".
- No AI-sounding vocabulary or filler: delve, seamless, leverage, robust,
  game-changer, unleash, elevate, supercharge, revolutionize, cutting-edge,
  "in today's video", "without further ado", "let's dive in", "it's worth
  noting", "at the end of the day", "whether you're X or Y".
- No hype, no exclamation-mark pileups, no rhetorical "Ready? Let's go".
- Specific beats generic: name the command, the number, the file.
- NEVER copy the project's own words. The README, the GitHub description and
  the docs are marketing written by the maintainers; quote a claim only to
  test it ("the README says one command, so let's time it"), never as our
  description of the tool. The tagline and every narration line are yours,
  written from what you saw in this rehearsal.
- No emoji anywhere, and none of the project's calls to action: no "join the
  Discord", "star the repo", "sign up", "check out the docs", no links, no
  invitations of any kind. We review tools, we don't recruit for them.

## Step 1 — actually try the tool

{{SOURCE_STEP}} Then USE it:
run its main features, push on the edges, note what impresses and what
frustrates. Time how long installs take. Capture exact output snippets you can
use as `wait_for` regexes later. Spend real effort here — the review segment
lives or dies on genuine hands-on findings.

## Where the two output files go

`/episode` is an absolute path: a directory mounted from the host, and the only
place the pipeline ever reads. Write `/episode/notes.md` and
`/episode/screenplay.yaml` there and nowhere else. Do NOT create or use
`~/episode`, `$HOME/episode` or a relative `episode/` directory: files written
there are invisible to the pipeline, the run is failed as "screenplay.yaml was
not written to /episode/", and every later fix round edits a file nobody reads.
If you are asked to fix the screenplay, re-read `/episode/screenplay.yaml`
first and edit that exact path.

## Step 2 — write /episode/notes.md

Sections: What it is / Install experience (exact commands + rough durations) /
What worked (with the commands) / What broke or annoyed me / Verdict (honest:
who should use this, who shouldn't, star-worthy or hype). Be a reviewer, not a
marketer — at least two genuine criticisms.

## Step 3 — write /episode/screenplay.yaml

The screenplay is replayed EXACTLY, keystroke by keystroke, in a FRESH
identical container (your rehearsal state will be gone — the demo must start
from an empty home directory and every command must work from scratch, in
order). Schema:

```yaml
meta:
  tool: "{{TOOL}}"
  repo: "{{REPO}}"
  lang: "{{LANG}}"
  tagline: "one line for the title card, in {{LANGUAGE}}"
  short_scene: "<scene id>"  # the one scene a 45-second Short shows. Pick the
                             # most VISUALLY INFORMATIVE scene: a typed command
                             # whose OUTPUT fills the screen with something worth
                             # looking at -- a results table or list, a generated
                             # file, a live TUI redraw, a diff, a rich report.
                             # It MUST be a scene with a typed terminal command
                             # (the Short is a terminal recording): never a
                             # web-only scene, a narration-only/outro scene, the
                             # clone/install, or a scene whose payload is a bare
                             # prompt, an error, or a wall of plain prose. If the
                             # tool's best moment is a surprise, use that scene
                             # only when a command produces the surprise on
                             # screen. Keep install/setup in their OWN scene so
                             # the scene you pick here is pure tool-in-use.
  cold_open:                 # the first 7 seconds of the video, before any card
    scene: "<scene id>"
    command: "<one command from that scene, exactly as typed>"
    hook: "<= 12 words, what the viewer is looking at and why it matters"
  terminal: {cols: 120, rows: 30}
  privileged: false          # true only if the episode brief says the container is privileged
scenes:
  - id: intro                # short slug
    narration: "2-4 spoken sentences for this scene"
    commands:
      - type: "{{TOOL}} --version"   # typed char-by-char on camera
        pre_pause: 1.0              # thinking pause before typing (seconds)
        post_pause: 1.5             # linger after output (viewer reads it)
        wait_for: {prompt: true, timeout: 120}
      - type: "some-long-build"
        wait_for: {regex: "Compiling|Installed", timeout: 600}  # regex on screen
  - id: outro
    narration: "one sentence, the same as verdict.bottom_line"
    dwell: 2                 # narration-only scene: replaced by the verdict cards
verdict:                     # rendered as closing cards, never typed, no captions
  use_cases: ["3-4 concrete situations where you'd reach for it"]
  bottom_line: "one sentence, at most 20 words"
```

In a SERIES episode the verdict is about THIS episode, not about the tool in
general. Episode 4 of a zellij series must not close on "zellij is a good
multiplexer" -- it closes on what this episode established: which keys clash,
which fix survives a restart, when you would still reach for locked mode.
`use_cases` are situations for the thing this episode taught; `bottom_line` is
the one sentence a viewer should leave with, and it should read differently
from every other episode's. One line or several, as long as it belongs to
this episode alone.

`cold_open` decides whether anyone keeps watching: pick the ONE command whose
output, seen for a few seconds with no context, makes a developer think
"oh, that's what this does" (the tool's signature result, a striking table,
the moment it catches something, the diff it produces). Never a clone,
install, version check, `ls`, or a command whose output is a JSON receipt or
a wall of logs. The hook is a plain sentence about what is on screen, no hype.

The spoken explanation must be COMPLETE when the last terminal scene ends:
every review point (what impressed, what broke, who it's for) is said in the
scene where its evidence is on screen. The verdict cards show only the use
cases and the bottom line, with no narration over them. Verdict items are one
line each, at most 12 words, no trailing period, concrete.

### Two panes (server + client, watcher + editor...)

The terminal is zellij, so a scene can split the screen. A `zellij:` command
is a layout step (nothing is typed); keystrokes always go to the focused pane
and `wait_for` watches the focused pane:

```yaml
      - type: "iperf3 -s"                       # server, left pane
        wait_for: {regex: "Server listening", timeout: 20}
      - zellij: "new-pane -d right"             # split; the new pane gets focus
      - type: "iperf3 -c 127.0.0.1 -t 3"        # client, right pane
        wait_for: {prompt: true, timeout: 30}
      - zellij: "move-focus left"               # back to the server pane
      - keys: ["C-c"]
        wait_for: {prompt: true, timeout: 10}
```

Allowed: `new-pane -d right|down`, `move-focus left|right|up|down`,
`close-pane`, `toggle-fullscreen`. Keep it to two panes; text gets small.

### Web UIs (only if the tool has one)

A headless Chromium with a visible cursor is available and its video is
recorded alongside the terminal. Start the server in the terminal
(`nohup <server> > server.log 2>&1 &`, so its log never draws over later
commands), then wait for it to actually answer, not for a log line: a first
build of a Vite/Next/moon app can take minutes and prints before it serves:

```yaml
      - type: "nohup npm run dev > server.log 2>&1 &"
        wait_for: {url: "http://127.0.0.1:5173", timeout: 900}   # polls until HTTP 2xx/3xx
```

Big self-hosted apps are welcome subjects (that is often the interesting
episode) as long as they start with one command inside this container; time
the first start during the rehearsal and set `timeout` to three times that.
Then use `web:` commands instead of `type:`:

```yaml
      - web:
          - {action: goto, url: "http://127.0.0.1:8080", label: "open  localhost:8080"}
          - {action: fill, target: "placeholder:Username", text: "admin"}
          - {action: fill, target: "placeholder:Password", text: "admin"}
          - {action: click, target: "role:button:Login", label: "click  Login"}
          - {action: wait, text: "My files"}
          - {action: click, target: "text:New folder"}
        pre_pause: 1.0
        post_pause: 2.0
```

Actions: goto(url), click(target), fill(target, text), type(text), press(key),
hover(target), wait(ms | target | text), scroll(dy). A click may carry
`optional: true`: it is skipped when the target does not show up within 3 s
(`ms` to change). Use it for every onboarding modal, survey, "Get started",
"Skip", cookie or telemetry banner an app shows on first launch: they
appear in one run and not the next, and an un-dismissed modal swallows the
next click on the page behind it. Never `wait` for a text that is visible
behind such a modal as proof the page is ready; wait for the control you
are about to click. Targets: `text:...`,
`role:button:Name`, `role:link:Name`, `label:...`, `placeholder:...`, or a CSS
selector. Prefer text/role/label targets (stable) over CSS. Viewport is
1600x900. Rehearse web steps for real with Playwright (python: use
`/home/demo/.local/share/pipx/venvs/playwright/bin/python`, sync API,
headless chromium) and only write steps you saw work; screenshots help you
find the right texts. Memory is shared with the app you are testing: always
use `with sync_playwright() as p:` and `browser.close()` so every script
exits clean, run one browser at a time, and `pkill -f chromium` between
checks; a leaked Chromium next to a large app gets this whole rehearsal
killed. Keep the app running in the terminal between scenes; the final scene
may stop it (`kill %1`).

## EPISODE STRUCTURE: {{STRUCTURE_NAME}}

{{STRUCTURE_BRIEF}}

Every episode still contains install -> first run somewhere near the start and
an honest verdict at the end, but the order, emphasis and scene titles follow
the structure above. Scene ids should reflect it (not the
generic intro/install/features/rough-edge every time). Write the structure
name into notes.md under a "## Structure" heading.

Rules:
- 5-8 scenes, total runtime target 4-7 minutes of terminal time.
- `wait_for.prompt: true` means "the last non-empty screen line matches
  `meta.prompt_regex`" (the bash prompt `demo ~/dir $`). Inside another REPL
  or shell (nu, python, psql...) that never matches: use `wait_for.regex`
  matching that REPL's own prompt at the end of the screen (e.g. `nushell> ?$`),
  or set `meta.prompt_regex` to an alternation covering both. Many REPL
  prompts carry a right-aligned clock or status on the same line, so never
  anchor a prompt regex with `$`; simplest for REPL steps is
  `wait_for: {stable: 2}` (done when the screen stopped changing for 2 s).
  Use a `regex` for long builds or commands that end without a prompt.
  Generous timeouts: real install time x3.
- Do not set `meta.prompt_regex` unless the shell prompt genuinely differs
  from the default, and never make one that requires trailing whitespace: the
  screen is right-stripped before matching, so `demo .*\$ $` matches nothing
  and every step in the episode times out.
- A `wait_for.regex` must match a SHORT, CONTIGUOUS, ASCII-ONLY fragment that
  you have confirmed byte-for-byte. Status bars and TUI chrome are full of
  decoration you cannot see when you read the screen: zellij's bar looks like
  `Ctrl + <g> LOCK` but is really `Ctrl +` + two powerline glyphs + ` <g> LOCK`,
  so a regex spanning that gap never matches and the take stalls until it times
  out. Before writing a regex against a TUI, dump the screen and check the
  actual bytes (`... | cat -v`, or grep for the exact fragment). Then match the
  smallest distinctive piece — `<g> LOCK`, not the whole bar — and never write
  a regex that crosses a run of spaces, a box-drawing character or an icon.
- NEVER clone unless the episode uses the checkout. `git clone` belongs on
  camera only when later commands genuinely need the working tree (the install
  builds from source, the demo runs the repo's own examples/fixtures, the tool
  is pointed at that source). If the tool installs from a package manager,
  a release binary, `go install`, `pipx`, `cargo install`, `npm i -g` or a
  curl-to-shell script, the first scene is the install and there is no clone,
  no `cd <repo>`, no `ls` of the source tree — a directory listing nobody
  looks at again is dead airtime, and one more command that can fail on
  camera for reasons that have nothing to do with the tool. Same for reading
  the README on camera: use it in the rehearsal, don't screen it.
  If the episode does clone, time your own `git clone` in this rehearsal and
  set that scene's timeout to three times it. A large monorepo (hundreds of MB
  of history) takes minutes on a normal line: clone it with `--depth 1` on
  camera and say so, unless the episode needs history.
- Only non-interactive commands, or interactive ones driven via `keys:`
  (allowed: Enter, Tab, Esc, Space, Backspace, C-c, C-d, C-r, Up, Down, Left,
  Right). Every interactive command MUST end back at a shell prompt before the
  next scene.
- Keep typed commands under ~90 characters; prefer several short commands over
  one monster pipeline.
- Editing an existing file: ONE `sed -i` is fine for a single line, but the
  moment a file needs two or more changes, open it in an editor on camera
  (vim: `type: "/pattern"` to search, `type: "3x"` with `enter: false`,
  `keys: ["Esc"]`, `type: ":wq"`), or write it with one heredoc if it is
  short. A run of sed one-liners is the most boring thing a viewer can watch
  and teaches nothing about the file. Dry-run the vim keystrokes in tmux
  before writing them into the screenplay. Never type a whole file on camera: a 30-line JSON or
  YAML through a heredoc is two minutes of nothing to watch. Use the files
  the repo ships (examples/, fixtures, its own docs), or generate the input
  with the tool itself or a one-liner (`seq`, `printf`, a `curl` of a public
  API). If the episode really needs a small file, keep it to 3 or 4 short
  lines. (Typing longer than 6 s is fast-forwarded in the render anyway, so
  a long file is never seen being written, only its result.)
- Narration is what the human host will read aloud while that scene plays —
  conversational, first person, no timestamps, mention what the viewer is
  seeing. The outro narration delivers the honest verdict from your notes.
- NOTHING may wait for interactive input you are not driving. A confirmation
  like "Trust this config? [y/n]", a pager, or a "press enter" that you do not
  answer stalls the recording and fails the episode. Use non-interactive
  flags/env (`-y`, `--yes`, `--no-pager`, `CI=1`, tool-specific env vars), run
  the command from a directory where the prompt cannot trigger, or drive it
  with `keys:`. An editor you open ON PURPOSE and drive with `keys:` is fine
  and is often the right choice — see the next rule.
- EDIT LIKE A HUMAN WHEN THE EDIT IS THE POINT. If a scene's subject is a
  config file — what to change and why — open it in the editor and make the
  change on camera (`vim file`, then `keys:` to move and type, `:wq`). A run
  of `sed -i 's|^// default_mode "locked"|default_mode "locked"|' file` lines
  is unreadable at video size, hides the file you are talking about, and
  forces a `grep` afterwards just to prove anything happened; five of them in
  a row is five identical-looking commands where the viewer wanted to see the
  file. Reach for `sed`/`printf`/heredocs only for setup the viewer does not
  need to watch, or for a change so mechanical that seeing the file adds
  nothing. Still never type a long file out by hand: open what the tool
  generated and change the two lines that matter.
- NEVER PUT A CREDENTIAL ON CAMERA. Some episodes need the container to be
  signed in to something; that arrives as an environment variable and must
  stay there. Never type, echo, `env`, `printenv`, `cat` a config file, or
  run any command whose output could contain a token, key or session id, and
  never open a file that holds one. If a tool prints a credential as part of
  its normal output, that command does not go in the screenplay.
- NO STAGED MISTAKES. Never write a deliberately wrong file, flag or input
  on camera and then fix it: to the viewer that looks like the presenter
  fumbling, not like the tool working. If the tool's validation or error
  reporting is worth showing, feed it a broken example the repo itself ships
  (say so in the narration: "the repo's own broken example") and never
  repair it afterwards; otherwise leave that feature to the narration. Every
  file the episode writes must be correct the first time.
- Killing processes: never `pkill -f` / `pgrep -f <name>`; the pattern also
  matches shells whose command line mentions the name (including the one
  running your own script). Keep the pid (`cmd & PID=$!`), or use `pgrep -x`,
  `pidof`, `fuser -k <mountpoint>`.
- Every package you installed while exploring must be in the screenplay's
  install commands: before writing it, run `apt-mark showmanual` (and check
  pipx/cargo/npm globals) and compare with what the screenplay installs. A
  runtime package you pulled in for a test (fuse3, sqlite3, a -dev's runtime
  twin) and forgot is the classic way the fresh recording container fails.
- VERIFY before writing: every command in the screenplay must be one you
  actually ran successfully in this rehearsal, in this order, from a clean
  clone. If you're unsure a sequence works from scratch, re-test it in a fresh
  directory now.

Write both files, then briefly print a summary of the scenes.
