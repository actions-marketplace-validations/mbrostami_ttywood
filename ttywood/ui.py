"""Terminal UI for the pipeline: a plan panel, one progress row per stage
(spinner or bar, elapsed time, what is happening right now), dim log lines
for the details (commands Claude runs in the container, takes, uploads) and
a summary table at the end. Built on rich; falls back to plain lines when
stdout is not a terminal."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn, TaskProgressColumn,
                           TextColumn, TimeElapsedColumn)
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

EST_MIN = {"discover": 3, "rehearse": 20, "record": 6, "voice": 2, "render": 5, "narrate": 0,
           "meta": 2, "thumbnail": 0, "shorts": 1, "qa": 3, "publish": 2}
HINTS = {
    "discover": "the LLM reads the project and plans the episode (or a plugin picks one)",
    "rehearse": "Claude Code tries the tool inside Docker, writes notes.md and screenplay.yaml",
    "record": "fresh container, asciinema + zellij, human-typed replay (self-repairs a stalled take)",
    "voice": "one TTS clip per scene",
    "render": "time-warped terminal video, then the cinematic composite",
    "narrate": "timestamped narration.md + narration.srt",
    "meta": "Claude writes youtube.md: titles, description, chapters, tags",
    "thumbnail": "thumbnail.png from a real frame + big text",
    "shorts": "9:16 cut of the tool-in-use scene",
    "qa": "hard checks + Claude frame review",
    "publish": "YouTube upload, private (unchanged files are skipped)",
}


def fmt_secs(secs: float) -> str:
    return f"{int(secs // 60)}m{int(secs % 60):02d}s" if secs >= 60 else f"{secs:.0f}s"


def _short(text: str, n: int = 96) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def plan_panel(plan: list[str], what: str, cut: str):
    total = sum(EST_MIN.get(s, 1) for s in plan)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column(style="bold")
    table.add_column(style="dim")
    for i, s in enumerate(plan, 1):
        table.add_row(f"{i}", s, HINTS.get(s, ""))
    console.print(Panel(table, title=f"[bold]ttywood[/] · {what}",
                        subtitle=f"{len(plan)} stages · roughly {total} min · {cut} cut",
                        border_style="cyan", padding=(1, 2)))


class Runner:
    """One progress task per stage. Use as:

        with ui.stage("record") as st:
            st.activity("typing `git clone`")     # live text on the row
            st.total(12); st.advance()            # turns the spinner into a bar
            st.note("take 1 ...")                 # dim line under the row
            st.result("demo.cast")                # shown on the ✓ line
    """

    def __init__(self, plan: list[str]):
        self.plan = plan
        self.t_run = time.monotonic()
        self.done: list[tuple[str, str, float, bool]] = []
        self.progress = Progress(
            SpinnerColumn("dots", style="cyan"),
            TextColumn("[bold]{task.fields[label]}[/] [dim]{task.fields[activity]}"),
            BarColumn(bar_width=24, style="grey35", complete_style="cyan"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console, transient=True, refresh_per_second=8)
        self.progress.start()
        self._task = None
        self._name = ""
        self._result = ""

    # -- stage lifecycle ----------------------------------------------------
    @contextmanager
    def stage(self, name: str, hint: str | None = None):
        i = self.plan.index(name) + 1 if name in self.plan else 0
        label = f"[{i}/{len(self.plan)}] {name}"
        console.print(Text.assemble(("\n" + label, "bold cyan"), ("  " + (hint or HINTS.get(name, "")), "dim")))
        self._name, self._result = name, ""
        self._task = self.progress.add_task("", total=None, label=name, activity="")
        t0 = time.monotonic()
        try:
            yield self
        except BaseException as e:
            secs = time.monotonic() - t0
            self.progress.remove_task(self._task)
            self.done.append((name, f"{type(e).__name__}: {_short(str(e), 200)}", secs, False))
            console.print(f"  [bold red]✗ {name}[/] failed after {fmt_secs(secs)}: "
                          f"[red]{type(e).__name__}: {_short(str(e), 300)}[/]")
            raise
        secs = time.monotonic() - t0
        self.progress.remove_task(self._task)
        self.done.append((name, self._result, secs, True))
        console.print(f"  [bold green]✓ {name}[/] [dim]{fmt_secs(secs)}[/]"
                      + (f"  {self._result}" if self._result else ""))

    # -- inside a stage -------------------------------------------------------
    def activity(self, text: str):
        if self._task is not None:
            self.progress.update(self._task, activity=_short(text))

    def total(self, n: float | None):
        if self._task is not None:
            self.progress.update(self._task, total=n, completed=0)

    def advance(self, n: float = 1, completed: float | None = None):
        if self._task is not None:
            if completed is None:
                self.progress.advance(self._task, n)
            else:
                self.progress.update(self._task, completed=completed)

    def note(self, text: str, style: str = "dim"):
        console.print(f"    [{style}]{_short(text, 160)}[/]")

    def command(self, cmd: str):
        """A command Claude ran in the container (rehearsal)."""
        console.print(Text.assemble(("    $ ", "bold magenta"), (_short(cmd, 140), "magenta")))

    def result(self, text: str):
        self._result = text

    # -- end ------------------------------------------------------------------
    def summary(self, ep: Path | None):
        self.progress.stop()
        table = Table(box=None, padding=(0, 2), show_header=False)
        table.add_column(style="bold")
        table.add_column(style="dim", justify="right")
        table.add_column()
        for name, res, secs, ok in self.done:
            table.add_row(("✓ " if ok else "✗ ") + name, fmt_secs(secs),
                          res if ok else f"[red]{res}[/]")
        all_ok = all(ok for *_, ok in self.done)
        console.print(Panel(table, title=("[bold green]done[/]" if all_ok else "[bold red]failed[/]")
                            + f" in {fmt_secs(time.monotonic() - self.t_run)}",
                            subtitle=str(ep) if ep else None,
                            border_style="green" if all_ok else "red", padding=(1, 2)))
