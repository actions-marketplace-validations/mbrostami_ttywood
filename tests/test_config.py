from pathlib import Path


def test_defaults_are_unbranded(cfg):
    assert cfg.get("brand.name") == ""
    assert cfg.get("narration.welcome.en") == ""
    assert cfg.get("show.pitch")
    assert cfg.sources() == [cfg.DEFAULTS]


def test_override_chain_and_relative_paths(cfg, tmp_path, monkeypatch):
    (tmp_path / "xdg" / "ttywood").mkdir(parents=True)
    (tmp_path / "xdg" / "ttywood" / "config.toml").write_text('[brand]\nname = "user"\nslogan = "s"\n')
    (tmp_path / "ttywood.toml").write_text('[brand]\nname = "cwd"\n')
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "c.toml").write_text('[brand]\nlogo = "logo.png"\n[paths]\nepisodes = "eps"\n')
    monkeypatch.setenv("TTYWOOD_CONFIG", str(extra / "c.toml"))
    cfg.load(reload=True)
    assert cfg.get("brand.name") == "cwd"          # ./ttywood.toml beats the user config
    assert cfg.get("brand.slogan") == "s"          # merged key by key
    assert cfg.get("brand.logo") == str((extra / "logo.png").resolve())
    assert cfg.episodes_root() == (extra / "eps").resolve()
    monkeypatch.setenv("TTYWOOD_DEMOS", str(tmp_path / "d"))
    assert cfg.episodes_root() == (tmp_path / "d").resolve()


def test_load_prompt_fills_show_and_honours_override_dirs(cfg, tmp_path, monkeypatch):
    text = cfg.load_prompt("rehearse.md")
    assert "{{SHOW}}" not in text
    assert cfg.get("show.pitch") in text
    prompts = tmp_path / "myprompts"
    prompts.mkdir()
    (prompts / "qa.md").write_text("custom {{SHOW}} {{TOOL}}")
    (tmp_path / "ttywood.toml").write_text(f'[paths]\nprompts = ["{prompts}"]\n[show]\npitch = "X"\n')
    cfg.load(reload=True)
    assert cfg.load_prompt("qa.md", TOOL="fzf") == "custom X fzf"


def test_every_builtin_prompt_loads(cfg):
    for p in sorted(Path(cfg.PROMPTS).glob("*.md")):
        assert cfg.load_prompt(p.name).strip()


def test_dotenv_does_not_override_shell(cfg, tmp_path, monkeypatch):
    env = tmp_path / "secrets.env"
    env.write_text('export TTYWOOD_TEST_A="from-file"\nTTYWOOD_TEST_B=file\n# comment\n')
    (tmp_path / "ttywood.toml").write_text(f'[paths]\nenv_files = ["{env}"]\n')
    cfg.load(reload=True)
    monkeypatch.setenv("TTYWOOD_TEST_B", "shell")
    monkeypatch.delenv("TTYWOOD_TEST_A", raising=False)
    cfg.load_dotenv_files()
    import os
    assert os.environ["TTYWOOD_TEST_A"] == "from-file"
    assert os.environ["TTYWOOD_TEST_B"] == "shell"
    monkeypatch.delenv("TTYWOOD_TEST_A")


def test_youtube_footer_is_appended_once(cfg, tmp_path):
    import json
    from ttywood.publish import build_payload
    ep = tmp_path / "ep"
    ep.mkdir()
    (ep / "youtube.md").write_text("## Titles\n1. A title\n\n## Description\nBody text.\n\n## Tags\na, b\n")
    (ep / "candidate.json").write_text(json.dumps({"repo": "o/r"}))
    assert "Made with" not in build_payload(ep)["snippet"]["description"]
    (tmp_path / "ttywood.toml").write_text('[youtube]\ndescription_footer = "Made with ttywood"\n')
    cfg.load(reload=True)
    desc = build_payload(ep)["snippet"]["description"]
    assert desc.endswith("Made with ttywood") and desc.count("Made with ttywood") == 1
