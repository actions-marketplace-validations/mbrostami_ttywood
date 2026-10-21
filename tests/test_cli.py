import json
from pathlib import Path

from click.testing import CliRunner

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "fzf_screenplay.yaml"


def _cli():
    from ttywood.cli import main
    return main


def test_help_lists_engine_commands_only():
    out = CliRunner().invoke(_cli(), ["--help"]).output
    for cmd in ("run", "pick", "new", "record", "cinema", "doctor", "schema", "validate"):
        assert cmd in out
    for private in ("targets", "outreach", "newsletter", "daily"):
        assert f"  {private} " not in out


def test_schema_is_json():
    res = CliRunner().invoke(_cli(), ["schema"])
    assert res.exit_code == 0
    assert "Screenplay" in json.loads(res.output)["title"]


def test_validate(tmp_path):
    runner = CliRunner()
    assert runner.invoke(_cli(), ["validate", str(EXAMPLE)]).exit_code == 0
    bad = tmp_path / "screenplay.yaml"
    bad.write_text("meta: {tool: x}\nscenes: []\n")
    assert runner.invoke(_cli(), ["validate", str(tmp_path)]).exit_code == 1


def test_new_and_info(tmp_path, monkeypatch):
    monkeypatch.setenv("TTYWOOD_DEMOS", str(tmp_path))
    import importlib
    import ttywood.cli
    importlib.reload(ttywood.cli)
    runner = CliRunner()
    res = runner.invoke(ttywood.cli.main, ["new", "smoke", "--tool", "fzf", "--json"])
    assert res.exit_code == 0, res.output
    ep = Path(json.loads(res.output)["episode"])
    assert ep == tmp_path / "smoke"
    info = json.loads(runner.invoke(ttywood.cli.main, ["info", "smoke"]).output)
    assert info["status"] == "picked"
    assert runner.invoke(ttywood.cli.main, ["validate", "smoke"]).exit_code == 2  # no screenplay yet
    assert "candidate.json" in info["files"]


def test_run_without_subject_needs_a_plugin():
    res = CliRunner().invoke(_cli(), ["run"])
    assert res.exit_code != 0
    assert "nothing to make" in res.output


def test_every_ffmpeg_call_ignores_the_terminal():
    """ffmpeg reads keys from stdin and turns echo off while it runs; started
    from an interactive shell it eats typed input and can leave the terminal
    without echo. Every call must pass -nostdin."""
    import re
    pkg = Path(__file__).resolve().parent.parent / "ttywood"
    for py in pkg.glob("*.py"):
        for m in re.finditer(r'\["ffmpeg",([^\]]*)', py.read_text()):
            assert '"-nostdin"' in m.group(1), f"{py.name}: ffmpeg without -nostdin"
