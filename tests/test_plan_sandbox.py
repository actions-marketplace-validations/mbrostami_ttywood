import json

import pytest

from ttywood import sandbox
from ttywood.plan import extract_json, new_episode


def test_extract_json_tolerates_fences_and_cut_tails():
    assert extract_json('noise ```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('{"a": "b') == {"a": "b"}
    assert extract_json("no json") is None


def test_new_episode_with_local_source(tmp_path):
    src = tmp_path / "My Project"
    src.mkdir()
    ep = new_episode(tmp_path / "eps", "demo", tool="mytool", source=str(src), tagline="t")
    cand = json.loads((ep / "candidate.json").read_text())
    assert cand["tool"] == "mytool"
    assert cand["source"] == str(src.resolve())
    assert cand["source_name"] == "my-project"
    with pytest.raises(RuntimeError):
        new_episode(tmp_path / "eps", "demo")


def test_env_flags(monkeypatch):
    monkeypatch.setenv("HOST_KEY", "s3cret")
    assert sandbox.env_flags({"env": {"API_KEY": "HOST_KEY"}}) == ["-e", "API_KEY=s3cret"]
    assert sandbox.env_flags({"env": ["HOST_KEY"]}) == ["-e", "HOST_KEY=s3cret"]
    assert sandbox.env_flags({}) == []
    monkeypatch.delenv("HOST_KEY")
    with pytest.raises(RuntimeError, match="export HOST_KEY"):
        sandbox.env_flags({"env": {"API_KEY": "HOST_KEY"}})


def test_source_flags(tmp_path):
    assert sandbox.source_flags({}) == []
    assert sandbox.source_flags({"source": str(tmp_path)}) == ["-v", f"{tmp_path}:/src:ro"]
    with pytest.raises(RuntimeError):
        sandbox.source_flags({"source": str(tmp_path / "gone")})


def test_new_episode_tool_defaults_to_repo_name(tmp_path):
    ep = new_episode(tmp_path, "smoke", repo="junegunn/fzf")
    assert json.loads((ep / "candidate.json").read_text())["tool"] == "fzf"


def test_description_drops_github_shortcodes():
    from ttywood.repoinfo import usable_description
    assert usable_description(":cherry_blossom: A command-line fuzzy finder", "fzf") == \
        "A command-line fuzzy finder"
