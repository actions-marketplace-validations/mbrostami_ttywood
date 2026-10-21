# Security

## The trust model, plainly

ttywood runs other people's code on purpose: the point is to install and use
a tool for real. What protects your machine is Docker, and only Docker.

- Every project is cloned, built and run **inside a container** from the
  `ttywood:latest` image. Nothing from the project runs on the host.
- The host directories a container can see: the episode directory
  (read-write, mounted at `/episode`) and, for `--path` episodes, the project
  directory (read-only, at `/src`). Nothing else.
- Episodes with `docker: true` or `privileged: true` (Docker-first projects,
  FUSE, loop devices) run the container `--privileged`. A privileged
  container is much weaker isolation. Only film such projects if you trust
  them, or run ttywood inside a VM.
- **The rehearsal container receives your Claude credentials**
  (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`), and the agent inside it
  runs with `--dangerously-skip-permissions`. Code from the project under
  test runs in the same container and could read that variable. Prefer an
  `ANTHROPIC_API_KEY` from a dedicated workspace with a spend limit over your
  personal subscription token, and revoke it if you filmed something
  suspicious. Variables listed in an episode's `env` field are passed in the
  same way.
- The recording container gets the Claude credentials only when the episode
  sets `needs_claude_auth` (episodes about Claude Code itself).
- The prompts tell the agent never to print a secret on camera, and secrets
  are passed as environment variables, never typed. Still, watch a video
  before you make it public.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's
private vulnerability reporting on this repository (Security tab, "Report a
vulnerability"). You will get an answer within a week.
