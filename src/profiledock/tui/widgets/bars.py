"""Global status bars: the header telemetry line and the footer command bar."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from .. import theme


def _token(name: str, app_theme: str) -> str:
    return theme.variable(name, app_theme)


class HeaderBar(Static):
    """Single-row global header: workspace badge, title, live telemetry."""

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $foreground;
    }
    """

    workspace: reactive[str] = reactive("[0] Default")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._metrics: tuple[int, int, str, str] = (0, 0, "-", "auto")

    def set_workspace(self, label: str) -> None:
        self.workspace = label

    def set_metrics(self, running: int, total: int, storage: str, engine: str) -> None:
        self._metrics = (running, total, storage, engine)
        self.refresh()

    def _render_line(self) -> Table:
        running, total, storage, engine = self._metrics
        app_theme = str(self.app.theme)
        line = Table.grid(expand=True)
        line.add_column(justify="left", ratio=1)
        line.add_column(justify="center", ratio=2)
        line.add_column(justify="right", ratio=1)

        badge = Text(f" {self.workspace} ", style=f"bold black on {_token('pd-amber', app_theme)}")

        title = Text()
        title.append("ProfileDock", style="bold")
        title.append(" ─ ", style=_token("pd-muted", app_theme))
        title.append("Isolated Chromium Profile Manager", style=_token("pd-cyan", app_theme))

        metrics = Text()
        metrics.append("Active: ", style=_token("pd-muted", app_theme))
        metrics.append(f"{running}/{total}", style="bold green" if running else "bold")
        metrics.append(" Running", style=_token("pd-muted", app_theme))
        metrics.append(" │ ", style=_token("pd-border", app_theme))
        metrics.append("Storage: ", style=_token("pd-muted", app_theme))
        metrics.append(storage, style="bold")
        metrics.append(" │ ", style=_token("pd-border", app_theme))
        metrics.append("Engine: ", style=_token("pd-muted", app_theme))
        metrics.append(engine, style="bold")

        line.add_row(badge, title, metrics)
        return line


class FooterBar(Widget):
    """Two-row footer: breadcrumb/status line plus keyboard hint chips."""

    DEFAULT_CSS = """
    FooterBar {
        dock: bottom;
        height: 2;
        padding: 0 2;
        background: $surface;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._breadcrumb: Sequence[str] = ("Home",)
        self._chips: Sequence[tuple[str, str]] = ()
        self._status: str | None = None
        self._theme_label = ""

    def compose(self) -> ComposeResult:
        yield Static(id="footer-top")
        yield Static(id="footer-bottom")

    def set_context(
        self,
        breadcrumb: Sequence[str],
        chips: Sequence[tuple[str, str]],
        status: str | None = None,
    ) -> None:
        self._breadcrumb = tuple(breadcrumb)
        self._chips = tuple(chips)
        self._status = status
        self._repaint()

    def set_theme_label(self, label: str) -> None:
        self._theme_label = label
        self._repaint()

    def _breadcrumb_text(self, app_theme: str) -> Text:
        muted = _token("pd-muted", app_theme)
        crumb = Text()
        for index, part in enumerate(self._breadcrumb):
            if index:
                crumb.append(" › ", style=muted)  # noqa: RUF001
            crumb.append(part, style="bold" if index == len(self._breadcrumb) - 1 else "")
        return crumb

    def _chips_text(self, app_theme: str) -> Text:
        muted = _token("pd-muted", app_theme)
        border = _token("pd-border", app_theme)
        chips = Text()
        for index, (key, label) in enumerate(self._chips):
            if index:
                chips.append("  ")
            chips.append(f" {key} ", style=f"bold black on {border}")
            chips.append(f" {label}", style=muted)
        return chips

    def _repaint(self) -> None:
        if not self.is_mounted:
            return
        app_theme = str(self.app.theme)
        muted = _token("pd-muted", app_theme)
        amber = _token("pd-amber", app_theme)

        top_grid = Table.grid(expand=True)
        top_grid.add_column(justify="left", ratio=1)
        top_grid.add_column(justify="right")
        crumb = self._breadcrumb_text(app_theme)
        if self._status:
            crumb.append("  ")
            crumb.append(f" {self._status} ", style=f"black on {amber}")
        top_grid.add_row(crumb, Text())
        self.query_one("#footer-top", Static).update(top_grid)

        bottom_grid = Table.grid(expand=True)
        bottom_grid.add_column(justify="left", ratio=1)
        bottom_grid.add_column(justify="right")
        right_hint = Text()
        if self._theme_label:
            right_hint.append(f" {self._theme_label} ", style=f"black on {muted}")
        bottom_grid.add_row(self._chips_text(app_theme), right_hint)
        self.query_one("#footer-bottom", Static).update(bottom_grid)


def breadcrumb(*parts: str) -> list[str]:
    return ["Home", *[part for part in parts if part]]
