"""Spoken-script pass: rewrite narration for the TTS ear (phonetic tool
names, spelled-out acronyms, flags and paths in words). The readable text in
narration.md is untouched; only what the voice engine receives changes.

Results live in <episode>/voice/spoken.json {glossary, spoken} and are reused
on later voice runs. Edit that file to correct a pronunciation, then rerun
`ttywood run <ep> --from voice --tts <engine>`; delete it to regenerate."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import load_prompt
from .llm import run as llm_run
from .screenplay import LANGUAGES, clean_text


def spoken_blocks(episode_dir: Path, blocks: dict[str, str], lang: str = "en",
                  refresh: bool = False) -> dict[str, str]:
    voice_dir = episode_dir / "voice"
    voice_dir.mkdir(exist_ok=True)
    cache = voice_dir / "spoken.json"
    if cache.exists() and not refresh:
        data = json.loads(cache.read_text())
        if all(k in data.get("spoken", {}) for k in blocks):
            return {k: clean_text(data["spoken"][k]) for k in blocks}

    template = load_prompt("spoken.md")
    prompt = (template
              .replace("{{LANGUAGE}}", LANGUAGES.get(lang, "English"))
              .replace("{{BLOCKS}}", json.dumps(blocks, indent=2, ensure_ascii=False)))
    p_out = llm_run(prompt, timeout=600)
    m = re.search(r"\{.*\}", p_out, re.DOTALL)
    if not m:
        raise RuntimeError(f"no JSON in spoken-script output:\n{p_out[-1500:]}")
    data = json.loads(m.group(0))
    missing = [k for k in blocks if k not in data.get("spoken", {})]
    if missing:
        raise RuntimeError(f"spoken-script output lacks blocks: {missing}")
    cache.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return {k: clean_text(data["spoken"][k]) for k in blocks}
