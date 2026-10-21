"""Generate YouTube metadata (title options, description with chapters, tags)
from the rehearsal notes + rendered timeline, via the configured LLM."""

from __future__ import annotations

import json
from pathlib import Path

from .config import load_prompt
from .llm import run as llm_run


def write_metadata(episode_dir: Path) -> Path:
    episode_dir = episode_dir.resolve()
    notes = (episode_dir / "notes.md").read_text() \
        if (episode_dir / "notes.md").exists() else "(no rehearsal notes)"
    candidate = json.loads((episode_dir / "candidate.json").read_text()) \
        if (episode_dir / "candidate.json").exists() else {}
    render_meta = json.loads((episode_dir / "render_meta.json").read_text()) \
        if (episode_dir / "render_meta.json").exists() else {}

    from .screenplay import LANGUAGES, banned_phrases, clean_markdown

    lang = candidate.get("lang", "en")
    template = load_prompt("metadata.md")
    prompt = (template
              .replace("{{LANGUAGE}}", LANGUAGES.get(lang, "English"))
              .replace("{{CANDIDATE}}", json.dumps(candidate, indent=2, ensure_ascii=False))
              .replace("{{NOTES}}", notes)
              .replace("{{TIMELINE}}", json.dumps(
                  render_meta.get("timeline", []), indent=2)))

    text = clean_markdown(llm_run(prompt, timeout=600))
    # An empty answer (a hiccup, a rate limit) used to be written out as an
    # empty youtube.md; publish then fell back to "ttywood episode" with no
    # description, and a real upload went out with a placeholder title. Fail
    # here instead: a missing file is recoverable, a published one is not.
    if "## Titles" not in text or len(text.strip()) < 80:
        raise RuntimeError(
            f"LLM returned no usable metadata ({len(text.strip())} chars); "
            f"youtube.md not written. Raw answer tail:\n{text[-800:]}")
    if hits := banned_phrases(text):
        text += f"\n\n<!-- style check: contains {hits}; consider rephrasing -->\n"
    out = episode_dir / "youtube.md"
    out.write_text(text)
    return out
