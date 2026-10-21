"""Optional TTS voice track: one clip per scene, generated from the screenplay
narration. The renderer places each clip at its scene's timestamp and holds
the last frame of a scene if the clip outlasts it.

Engines: `say` (macOS, offline, default), `openai` (OPENAI_API_KEY),
`elevenlabs` (ELEVENLABS_API_KEY)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import requests

DEFAULT_VOICES = {"say": "Samantha", "openai": "onyx", "elevenlabs": "JBFqnCBsd6RMkjVDRZzb"}


def _sh(cmd: list[str]):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(p.stdout.strip())


def _to_wav(src: Path, dst: Path):
    _sh(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-ar", "24000", "-ac", "1", str(dst)])


def _tts_say(text: str, voice: str, out: Path):
    aiff = out.with_suffix(".aiff")
    _sh(["say", "-v", voice, "-o", str(aiff), text])
    _to_wav(aiff, out)
    aiff.unlink()


def _tts_openai(text: str, voice: str, out: Path):
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": "gpt-4o-mini-tts", "voice": voice, "input": text,
              "response_format": "mp3"},
        timeout=120)
    r.raise_for_status()
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(r.content)
    _to_wav(mp3, out)
    mp3.unlink()


def _tts_elevenlabs(text: str, voice: str, out: Path):
    # TTYWOOD_ELEVEN_MODEL lets you try newer models (e.g. eleven_v3) without a code change
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
        json={"text": text,
              "model_id": os.environ.get("TTYWOOD_ELEVEN_MODEL", "eleven_multilingual_v2"),
              # steadier, less "performed" read suits technical narration
              "voice_settings": {"stability": 0.6, "similarity_boost": 0.8,
                                 "style": 0.15, "use_speaker_boost": True}},
        timeout=120)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", {})
            msg = f"{detail.get('status', r.status_code)}: {detail.get('message', r.text[:300])}"
        except ValueError:
            msg = r.text[:300]
        raise RuntimeError(f"ElevenLabs refused the request ({msg}). "
                           f"Check quota at elevenlabs.io or use --tts openai / say.")
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(r.content)
    _to_wav(mp3, out)
    mp3.unlink()


ENGINES = {"say": _tts_say, "openai": _tts_openai, "elevenlabs": _tts_elevenlabs}


def generate_voice(episode_dir: Path, engine: str = "say",
                   voice: str | None = None, spoken: bool = True) -> Path:
    from .screenplay import bumpers, load_screenplay

    episode_dir = episode_dir.resolve()
    play = load_screenplay(episode_dir / "screenplay.yaml")
    if engine == "say" and play.meta.lang != "en":
        raise RuntimeError(
            f"macOS `say` has no {play.meta.lang!r} voice; use --tts elevenlabs or "
            f"--tts openai for this episode")
    voice = voice or DEFAULT_VOICES[engine]
    out_dir = episode_dir / "voice"
    out_dir.mkdir(exist_ok=True)
    welcome, signoff = bumpers(play)
    # "welcome" plays over the title card from 0:00, "signoff" over the closing card
    parts = [("welcome", welcome)] + [(s.id, s.narration) for s in play.scenes] \
        + [("signoff", signoff)]
    parts = [(k, v) for k, v in parts if v.strip()]  # no bumpers configured: nothing to say
    texts = dict(parts)
    if spoken:
        from .spoken import spoken_blocks
        texts = spoken_blocks(episode_dir, texts, lang=play.meta.lang)
    clips = []
    for scene_id, _ in parts:
        wav = out_dir / f"{scene_id}.wav"
        ENGINES[engine](" ".join(texts[scene_id].split()), voice, wav)
        clips.append({"scene": scene_id, "file": wav.name,
                      "duration": round(_duration(wav), 2)})
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps({"engine": engine, "voice": voice,
                                    "spoken_script": spoken, "clips": clips}, indent=2))
    return manifest
