"""Pluggable LLM adapter for host-side (non-container) calls.

All modules that previously called ``claude -p`` now call ``llm.run()``.
The backend is chosen by the ``TTYWOOD_LLM`` environment variable:

* ``claude``        (default) — subprocess ``claude -p …`` exactly as before
* ``omp``                     — subprocess ``omp -p …`` (oh-my-pi, omp.sh),
                                same flag convention; set ``TTYWOOD_OMP_MODEL``
                                to pick a model, e.g. ``openai/gpt-4o``
* ``openai-compat``           — HTTP POST to any OpenAI-compatible chat endpoint
                                (Ollama, vLLM, OpenRouter, LM Studio, …)
* ``shell``                   — pipe the prompt to an arbitrary shell command

Only the **host-side** ``claude -p`` calls are routed here.  The in-container
rehearsal/check flow (``_run_claude()`` in ``rehearse.py``) keeps calling the
``claude`` CLI directly because it depends on ``--output-format stream-json``
and live tool-call execution.

Environment variables
---------------------
TTYWOOD_LLM              Backend name: claude (default), omp, openai-compat, shell
TTYWOOD_OMP_MODEL        For omp: model flag value, e.g. ``openai/gpt-4o``
                         (passed as ``omp -p --model <value>``)
TTYWOOD_LLM_BASE_URL     For openai-compat: base URL, e.g. http://localhost:11434/v1
TTYWOOD_LLM_MODEL        For openai-compat: model name, e.g. llama3.1:8b
TTYWOOD_LLM_API_KEY      For openai-compat: API key (use "ollama" for local Ollama)
TTYWOOD_LLM_TIMEOUT      Override timeout in seconds (default: per-call value)
TTYWOOD_LLM_CMD          For shell: command template; {{PROMPT}} is replaced with
                         the prompt text. If absent the prompt is piped to stdin.
"""

from __future__ import annotations

import os
import subprocess

# ── helpers ──────────────────────────────────────────────────────────────────

def _backend() -> str:
    return os.environ.get("TTYWOOD_LLM", "claude").strip().lower()


def _timeout(default: int) -> int:
    raw = os.environ.get("TTYWOOD_LLM_TIMEOUT", "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        return default


# ── backends ─────────────────────────────────────────────────────────────────

def _run_claude(prompt: str, allowed_tools: str | None, input_mode: str,
                timeout: int, cwd=None) -> str:
    """Original behaviour: subprocess ``claude -p``."""
    # --allowedTools is variadic, so the prompt must come BEFORE it or it is
    # swallowed as a tool name and claude complains that no input was given
    cmd = ["claude", "-p"] + ([] if input_mode == "stdin" else [prompt])
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if input_mode == "stdin":
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
    else:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(
            f"claude -p failed (exit {p.returncode}): {(p.stderr or p.stdout)[-2000:]}")
    return p.stdout


def _run_omp(prompt: str, allowed_tools: str | None, input_mode: str,
             timeout: int) -> str:
    """oh-my-pi ``omp -p``: same flag convention as claude, supports 60+ providers.

    Set TTYWOOD_OMP_MODEL to choose the model, e.g.:
        TTYWOOD_OMP_MODEL=openai/gpt-4o
        TTYWOOD_OMP_MODEL=anthropic/claude-opus-4-5
    """
    cmd = ["omp", "-p"]
    model = os.environ.get("TTYWOOD_OMP_MODEL", "").strip()
    if model:
        cmd += ["--model", model]
    # omp does not have an --allowedTools flag (it uses its own tool surface),
    # so we silently ignore allowed_tools for this backend.
    if input_mode == "stdin":
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=timeout)
    else:
        p = subprocess.run(cmd + [prompt], capture_output=True, text=True,
                           timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(
            f"omp -p failed (exit {p.returncode}): {(p.stderr or p.stdout)[-2000:]}")
    return p.stdout


def _run_openai_compat(prompt: str, timeout: int) -> str:
    """POST to any OpenAI-compatible /chat/completions endpoint."""
    try:
        import requests as _req
    except ImportError:  # pragma: no cover
        raise RuntimeError(
            "requests is required for TTYWOOD_LLM=openai-compat; "
            "it is already a ttywood dependency — check your virtualenv.")
    base = os.environ.get("TTYWOOD_LLM_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError(
            "TTYWOOD_LLM=openai-compat requires TTYWOOD_LLM_BASE_URL "
            "(e.g. http://localhost:11434/v1 for Ollama)")
    model = os.environ.get("TTYWOOD_LLM_MODEL", "")
    if not model:
        raise RuntimeError(
            "TTYWOOD_LLM=openai-compat requires TTYWOOD_LLM_MODEL "
            "(e.g. llama3.1:8b or gpt-4o)")
    api_key = os.environ.get("TTYWOOD_LLM_API_KEY", "ollama")
    r = _req.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout)
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(
            f"Unexpected openai-compat response shape: {e}\n{data}") from e


def _run_shell(prompt: str, timeout: int) -> str:
    """Pipe prompt to an arbitrary shell command or fill {{PROMPT}} placeholder."""
    cmd_template = os.environ.get("TTYWOOD_LLM_CMD", "")
    if not cmd_template:
        raise RuntimeError(
            "TTYWOOD_LLM=shell requires TTYWOOD_LLM_CMD "
            "(e.g. 'sgpt --model gpt-4o' or 'ollama run llama3')")
    if "{{PROMPT}}" in cmd_template:
        cmd = cmd_template.replace("{{PROMPT}}", prompt)
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
    else:
        p = subprocess.run(cmd_template, shell=True, input=prompt,
                           capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(
            f"LLM shell command failed (exit {p.returncode}): "
            f"{(p.stderr or p.stdout)[-2000:]}")
    return p.stdout


# ── public API ────────────────────────────────────────────────────────────────

def run(prompt: str, *,
        allowed_tools: str | None = None,
        input_mode: str = "arg",
        timeout: int = 600,
        cwd=None) -> str:
    """Run the configured LLM and return the text output.

    Parameters
    ----------
    prompt:
        The full prompt text.
    allowed_tools:
        Comma-separated tool names (only honoured by the ``claude`` backend).
    input_mode:
        ``"arg"`` — prompt is passed as a CLI argument (default).
        ``"stdin"`` — prompt is piped to stdin.
    timeout:
        Seconds before giving up.  Overridable via ``TTYWOOD_LLM_TIMEOUT``.
    cwd:
        Working directory for CLI backends (``claude`` only); lets the model's
        own tools (Read) resolve relative paths inside an episode directory.

    Returns
    -------
    str
        The model's text response (stdout of the subprocess / body of the API
        response).

    Raises
    ------
    RuntimeError
        On non-zero exit, HTTP error, or missing configuration.
    """
    backend = _backend()
    t = _timeout(timeout)

    if backend == "claude":
        return _run_claude(prompt, allowed_tools, input_mode, t, cwd=cwd)
    elif backend == "omp":
        return _run_omp(prompt, allowed_tools, input_mode, t)
    elif backend == "openai-compat":
        return _run_openai_compat(prompt, t)
    elif backend == "shell":
        return _run_shell(prompt, t)
    else:
        raise RuntimeError(
            f"Unknown TTYWOOD_LLM backend {backend!r}. "
            "Valid values: claude (default), omp, openai-compat, shell.")


def describe_backend() -> str:
    """One-line human-readable description of the active backend."""
    backend = _backend()
    if backend == "claude":
        return "claude (Claude Code CLI)"
    if backend == "omp":
        model = os.environ.get("TTYWOOD_OMP_MODEL", "")
        return f"omp (oh-my-pi){f' · {model}' if model else ''}"
    if backend == "openai-compat":
        base = os.environ.get("TTYWOOD_LLM_BASE_URL", "(no base URL)")
        model = os.environ.get("TTYWOOD_LLM_MODEL", "(no model)")
        return f"openai-compat · {model} @ {base}"
    if backend == "shell":
        cmd = os.environ.get("TTYWOOD_LLM_CMD", "(no command)")
        return f"shell · {cmd}"
    return f"unknown ({backend})"
