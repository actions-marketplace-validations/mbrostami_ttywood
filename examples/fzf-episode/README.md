# A recorded episode, ready to render

`examples/fzf_screenplay.yaml` recorded by `ttywood record`: the asciinema
cast (`demo.cast`) and the keystroke timeline (`timeline.json`). Rendering
it needs no Docker and no LLM, only `ffmpeg`, `agg` and a font:

```bash
ttywood cinema examples/fzf-episode      # -> examples/fzf-episode/final.mp4
ttywood thumbnail examples/fzf-episode   # -> thumbnail.png
ttywood shorts examples/fzf-episode      # -> short.mp4 (9:16)
```

Re-record it yourself (needs Docker and `ttywood build-image`) with
`ttywood record examples/fzf-episode`.
