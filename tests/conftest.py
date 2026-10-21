import os

import pytest

# tests never see the developer's plugins or personal config
os.environ["TTYWOOD_NO_PLUGINS"] = "1"
os.environ.pop("TTYWOOD_CONFIG", None)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Isolated config: no user config dir, cwd in tmp, reload after each change."""
    from ttywood import config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "xdg" / "ttywood")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TTYWOOD_CONFIG", raising=False)
    monkeypatch.delenv("TTYWOOD_DEMOS", raising=False)
    config.load(reload=True)
    yield config
    monkeypatch.undo()
    config.load(reload=True)
