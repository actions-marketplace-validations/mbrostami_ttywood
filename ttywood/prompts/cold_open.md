Pick the cold open for an episode of {{SHOW}} about {{TOOL}}: the first seven
seconds of the video, before any title card. It shows ONE command being
finished and its output landing, with a one-line caption. It decides whether
anyone keeps watching, so it must be the moment that makes a developer think
"oh, that's what this does": the tool's signature result, a striking table, the
moment it catches a problem, the diff or report it produces.

Never pick: clone, install, build, a version check, `ls`/`cd`/`mkdir`, a
command whose output is a JSON receipt, a hash, a wall of logs, or an error
that is only interesting with context.

Rehearsal notes (what the reviewer saw the tool do):
{{NOTES}}

The scenes, the commands typed in each (exactly as typed) and, where
available, what each one actually put on screen in the recording. Judge by
the output as a viewer would see it for a few seconds: something visual and
self-explanatory (a table, a graph, a colored diff, a list of findings) beats
a receipt, a hash, a one-line "ok", or a version string, however important
the command is to the story.
{{COMMANDS}}

Answer in **{{LANGUAGE}}** for the hook. No em or en dashes, no hype words, no
exclamation marks. Output ONLY a JSON object, nothing else:

{"scene": "<scene id>", "command": "<one command from that scene, copied exactly>", "hook": "<= 12 words, what the viewer is looking at and why it matters"}
