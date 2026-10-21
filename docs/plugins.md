# Plugins

A plugin is a separately installed Python package that extends ttywood
without forking it: your channel's config and prompts, extra commands, a way
of choosing subjects on its own, something to do after an upload. It is the
place for anything you do not want in a public repo.

## Registering

```toml
# your plugin's pyproject.toml
[project]
name = "ttywood-mychannel"
dependencies = ["ttywood"]

[project.entry-points."ttywood.plugins"]
mychannel = "ttywood_mychannel.plugin"
```

```bash
pip install -e ./ttywood-mychannel
ttywood config        # lists the plugin and its config file
```

## What a plugin module can define

All of it is optional.

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# merged over defaults.toml, before the user's own config
CONFIG_FILES = [ROOT / "config.toml"]

# searched for prompts before the built-in ones; a file named like a
# built-in prompt (rehearse.md, metadata.md, ...) replaces it
PROMPT_DIRS = [Path(__file__).parent / "prompts"]


def register(cli):
    """Add commands to the click group, or options to existing ones."""
    import click

    @cli.command()
    def hello():
        click.echo("hi from the plugin")

    # extra options on `run` arrive as keyword arguments to discover() below
    cli.commands["run"].params.append(click.Option(["--language"], default=""))


def discover(episodes_root: Path, *, prefer: str = "", log=print, language: str = "", **_) -> Path:
    """`ttywood run` with no EPISODE, --repo, --path or --series calls this:
    pick a subject, create episodes_root/<name>/candidate.json, return the
    directory. ttywood.plan.candidate_for_repo() does the planning part."""
    from ttywood.plan import candidate_for_repo
    return candidate_for_repo(episodes_root, "junegunn/fzf", prefer=prefer, log=log)


def after_publish(episode_dir: Path, log) -> None:
    """Runs after `run --publish` uploaded the episode."""
    log(f"published {episode_dir.name}")
```

Plugins load when ttywood starts; one that fails to import prints a
warning and is skipped. `TTYWOOD_NO_PLUGINS=1` turns them all off, which is
the first thing to try when reporting a bug.
