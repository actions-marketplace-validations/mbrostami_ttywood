# Using ttywood from an agent

ttywood is a plain CLI with files on disk, so any coding agent that can run
shell commands (Claude Code, Codex, Cursor, Aider, your own) can drive it.
Two ways to make a video, depending on how much the agent already knows.

## 1. Let ttywood work it out

The agent hands over a repo or a directory; ttywood's own rehearsal agent
tries the tool in a container and writes the screenplay.

```bash
ttywood doctor --json                         # prerequisites, exit 1 if one is missing
ttywood run --path . --style showcase         # the project in the current directory
ttywood run --repo owner/name                 # or a GitHub repo
ttywood info <episode>                        # JSON: status, QA verdict, artifact paths
```

`--path` copies the project into the containers (read-only mount, copied to
`~/<dirname>` off camera), so unpublished work can be filmed. `--style
showcase` makes the project's own demo; the default `review` is an honest
hands-on review with a verdict. `--prefer "..."` steers the plan and
`--focus "..."` steers the rehearsal ("show the CI integration").

A full run takes 30 to 60 minutes: rehearsal 10 to 30, recording in real
time, render a few minutes, QA a few. Run it in the background and poll
`ttywood info`.

## 2. Write the screenplay yourself

An agent that has just built a feature knows exactly what to show. Skip the
rehearsal: create an empty episode, write `screenplay.yaml`, record.

```bash
ttywood new my-feature --path . --tool mytool --tagline "what it does" --json
ttywood schema > /tmp/screenplay.schema.json  # the format, as JSON schema
# write <episode>/screenplay.yaml (see docs/screenplay.md)
ttywood validate <episode>                    # exit 1 with the errors
ttywood run my-feature --from record          # record, render, Short, thumbnail, QA
ttywood info my-feature                       # where final.mp4 and the rest are
```

Before recording, test every command in a throwaway container with the same
image, because the take is replayed blind:

```bash
docker run --rm -v "$PWD:/src:ro" ttywood:latest bash -lc 'cp -r /src ~/p && cd ~/p && <command>'
```

If a take still fails, `run` hands the failure (the command, the screen at
that moment) to the LLM, which fixes the screenplay and retries twice. The
repair is logged to `<episode>/rehearse.log`.

## Outputs

| file | what |
|---|---|
| `final.mp4` | 1920x1080, captions, chapter cards, command bar, key sounds |
| `short.mp4` | 1080x1920, one scene, at most 58 s |
| `thumbnail.png` | a real frame with the title over it |
| `youtube.md` | title options, description with chapters, tags |
| `narration.srt` | subtitles |
| `qa.md` | the QA verdict (`Verdict: PASS` / `Verdict: FAIL`) and why |
| `readme.gif` | `ttywood gif <episode>`: one scene, README sized |

`ttywood qa` exits 1 on FAIL and `run` exits 1 when QA fails, so an agent can
branch on the exit code.

## Secrets on camera

An episode that needs an API key gets it as an environment variable, never
typed. Add to `candidate.json`:

```json
"env": {"MYTOOL_API_KEY": "MYTOOL_API_KEY"}
```

The key maps the name inside the container to the host variable it is read
from. Commands then reference `"$MYTOOL_API_KEY"`, which the shell expands
off screen.

## Giving an agent the skill

[`skills/ttywood/SKILL.md`](../skills/ttywood/SKILL.md) is an Agent Skill
with the above in agent-sized form. The repo is also a Claude Code plugin
marketplace, so in Claude Code:

```
/plugin marketplace add mbrostami/ttywood
/plugin install ttywood@ttywood
```

Or copy it by hand: `mkdir -p ~/.claude/skills && cp -r skills/ttywood ~/.claude/skills/`.

Other agents: point them at `skills/ttywood/SKILL.md` or paste it into their
instructions file.
