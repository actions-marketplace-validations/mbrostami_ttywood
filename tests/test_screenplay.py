from pathlib import Path

import pytest

from ttywood.screenplay import bumpers, clean_text, key_bytes, load_screenplay, validation_errors

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*_screenplay.yaml")), ids=lambda p: p.name)
def test_examples_validate(path):
    assert validation_errors(path) is None


def test_invalid_screenplay_reports(tmp_path):
    bad = tmp_path / "screenplay.yaml"
    bad.write_text("meta: {tool: x}\nscenes: []\n")
    assert "no scenes" in validation_errors(bad)


def test_clean_text_strips_dashes_and_emoji_but_not_code():
    assert clean_text("fast — really 🚀 fast") == "fast, really fast"
    assert clean_text("run `a --b — c`") == "run `a --b — c`"


def test_key_bytes():
    assert key_bytes("Enter") == [13]
    assert key_bytes("C-c") == [3]
    assert key_bytes("M-x") == [27, ord("x")]
    assert key_bytes("Nope") is None


def test_bumpers_default_empty_and_configurable(cfg, tmp_path):
    play = load_screenplay(EXAMPLES / "fzf_screenplay.yaml")
    assert bumpers(play) == ("", "")
    (tmp_path / "ttywood.toml").write_text(
        '[narration.welcome]\nen = "Hi, this is {tool}{tagline}."\n[narration.signoff]\nen = "Bye."\n')
    cfg.load(reload=True)
    welcome, signoff = bumpers(play)
    assert welcome == "Hi, this is fzf, a command-line fuzzy finder."
    assert signoff == "Bye."


def test_meta_repo_must_be_owner_name():
    from ttywood.screenplay import Meta
    assert Meta(tool="x", repo="junegunn/fzf").repo == "junegunn/fzf"
    assert Meta(tool="x", repo="a local project in ~/x").repo == ""
