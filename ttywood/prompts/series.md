You are planning a numbered series for {{SHOW}}. Every episode is a real
terminal session: commands typed on camera, real output, an
honest verdict. No slides, no talking head, no stock footage. The host reads a
narration over the recording.

Plan a {{COUNT}}-episode series on: **{{SUBJECT}}**

{{PREFER}}

Facts we already have (may be empty):
```json
{{FACTS}}
```

Each episode is recorded in an Ubuntu 24.04 container with git, curl,
build-essential, python3/pipx, node 22/npm, go 1.24, rust/cargo and a headless
Chromium. No API keys, no accounts, no GPU, no display, one machine.

A series earns a returning viewer, so it has an arc: episode 1 must stand alone
for someone who has never touched the subject, and each later episode builds on
what the earlier ones established instead of restarting from install. Order the
episodes by what a learner actually needs next, not by feature list order. Every
episode must be a session someone would watch on its own merits — a concrete
task with a visible result, not "part 3 of the configuration reference".

Respond with ONLY a JSON object (no markdown fences, no commentary):
{
  "slug": "short-kebab-case-id-for-the-series",
  "title": "series title as it appears in episode titles, no episode number",
  "subject": "{{SUBJECT}}",
  "audience": "one sentence: who this series is for and what they already know",
  "arc": "2-3 sentences: where the viewer starts, where they end up, why this order",
  "episodes": [
    {
      "n": 1,
      "title": "episode title, no number, no series name, plain and concrete",
      "goal": "one sentence: what the viewer can do at the end that they could not do before",
      "teaches": ["the specific commands, keybindings or config keys this episode covers"],
      "assumes": "what earlier episodes already established (empty for episode 1)",
      "angle": "3-5 sentences: what this episode shows, in order, naming the actual commands and the real input the demo uses. The honest question it answers.",
      "structure": "tour | bug-hunt | versus | speedrun | mystery"
    }
  ]
}

Rules for the plan:
- Exactly {{COUNT}} episodes, numbered from 1.
- `teaches` lists things that appear on screen, not themes. "split panes with
  Ctrl-p n, resize with Ctrl-n, float with Ctrl-p w" — not "learn layouts".
- No episode may depend on a key, an account, a GPU, a second machine or a
  desktop. If part of the subject needs one, leave it out and say nothing.
- Vary `structure`. A whole series of `tour` is a manual read aloud; use
  `versus` where the subject has an incumbent to compare against, `bug-hunt` or
  `mystery` where something is surprising or breaks, `speedrun` where the point
  is how far you get.
- Later episodes must not re-teach earlier ones. If episode 4 needs a config
  file that episode 2 wrote, say so in `assumes` and rebuild it in one command
  on camera rather than typing it out again.
