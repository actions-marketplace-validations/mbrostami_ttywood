"""A quiet lo-fi bed for the speechless cut, synthesized once into
~/.cache/ttywood/bed.wav (no samples, nothing to license). Slow Rhodes-like
chords, a soft bass pluck on 1 and 3, a little vinyl crackle, low-passed so it
sits well under the key sounds. Deterministic: the same file every time."""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

SR = 44100
# synthesized once per machine, kept out of the package directory
BED_PATH = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ttywood" / "bed.wav"

BPM = 72
# Fmaj7  Am7  Dm7  Gm7 (midi), with their bass notes
CHORDS = [[53, 57, 60, 64], [57, 60, 64, 67], [50, 53, 57, 60], [55, 58, 62, 65]]
BASS = [41, 45, 38, 43]


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """FFT low-pass with a soft roll-off (no scipy needed)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    gain = 1 / (1 + (f / cutoff_hz) ** 4)
    return np.fft.irfft(spec * gain, n=len(x))


def _add(buf: np.ndarray, at: int, seg: np.ndarray):
    j = min(at + len(seg), len(buf))
    if j > at:
        buf[at:j] += seg[: j - at]


def synth_bed(seconds: float = 150.0, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / BPM
    bar = 4 * beat
    n = int(seconds * SR)
    out = np.zeros(n)
    t_bar, i = 0.0, 0
    while t_bar < seconds:
        chord, bass = CHORDS[i % 4], BASS[i % 4]
        # pad: soft attack, long decay, slightly detuned partials
        L = int(bar * 1.1 * SR)
        tt = np.arange(L) / SR
        env = np.minimum(tt / 0.7, 1.0) * np.exp(-tt / (bar * 0.85))
        pad = np.zeros(L)
        for m in chord:
            f = _hz(m) * (1 + rng.normal(0, 0.0012))
            pad += (np.sin(2 * np.pi * f * tt)
                    + 0.30 * np.sin(2 * np.pi * 2 * f * tt + 0.3) * np.exp(-tt * 1.5)
                    + 0.08 * np.sin(2 * np.pi * 3 * f * tt) * np.exp(-tt * 3))
        _add(out, int(t_bar * SR), pad * env * 0.09)
        # bass pluck on beats 1 and 3
        for b in (0, 2):
            L2 = int(beat * 1.7 * SR)
            t2 = np.arange(L2) / SR
            fb = _hz(bass)
            env2 = np.minimum(t2 / 0.012, 1.0) * np.exp(-t2 / 0.55)
            pluck = (np.sin(2 * np.pi * fb * t2) + 0.25 * np.sin(2 * np.pi * 2 * fb * t2)
                     * np.exp(-t2 * 4))
            _add(out, int((t_bar + b * beat) * SR), pluck * env2 * 0.20)
        # a sparse high note now and then, an octave above the chord's top
        if rng.random() < 0.5:
            L3 = int(beat * 2.5 * SR)
            t3 = np.arange(L3) / SR
            f3 = _hz(chord[rng.integers(len(chord))] + 12)
            env3 = np.minimum(t3 / 0.05, 1.0) * np.exp(-t3 / 0.9)
            _add(out, int((t_bar + rng.choice([1, 2, 3]) * beat) * SR),
                 np.sin(2 * np.pi * f3 * t3) * env3 * 0.045)
        i += 1
        t_bar += bar
    out = _lowpass(out, 2600.0)
    # vinyl: faint hiss plus sparse crackle
    hiss = _lowpass(rng.normal(0, 1, n), 1800.0) * 0.004
    crackle = np.zeros(n)
    idx = np.flatnonzero(rng.random(n) < 1.2e-5)
    crackle[idx] = rng.uniform(-1, 1, len(idx))
    crackle = _lowpass(crackle, 4000.0) * 0.08
    out += hiss + crackle
    # loop-friendly edges
    fade = int(1.5 * SR)
    ramp = np.linspace(0, 1, fade)
    out[:fade] *= ramp
    out[-fade:] *= ramp[::-1]
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.7).astype(np.float32)


def ensure_bed(path: Path = BED_PATH) -> Path:
    """The default music bed, generated on first use."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (synth_bed() * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path
