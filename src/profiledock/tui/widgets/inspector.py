"""Right-pane inspector widgets: profile rail, telemetry cards, output view."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..actions import ActionSpec, icons_enabled
from ..backend import ProfileRow
from .deck import CURSOR_GLYPH, VimOptionList

PROFILE_GLYPH = "󰈹"
PROFILE_FALLBACK = "#"

_STATUS_STYLE = {
    "running": ("●", "green"),
    "starting": ("◐", "yellow"),
    "closing": ("◐", "yellow"),
    "stale": ("✗", "red"),
    "error": ("✗", "red"),
    "stopped": ("○", "$text-muted"),
}


def _status_badge(status: str, pid: int | None) -> str:
    dot, color = _STATUS_STYLE.get(status, ("○", "$text-muted"))
    label = status.upper()
    if status == "running" and pid:
        label = f"RUNNING PID {pid}"
    if status == "stopped":
        label = "IDLE"
    return f"[$text-muted]\\[[/][{color}]{dot} {label}[/][$text-muted]][/]"


class ProfileRail(VimOptionList):
    """Compact profile list with live status badges."""

    DEFAULT_CSS = """
    ProfileRail {
        background: transparent;
        border: none;
        padding: 0 1;
        overflow-x: hidden;
    }
    ProfileRail:focus {
        border: none;
    }
    ProfileRail > .option-list--option-highlighted {
        background: $pd-selection;
    }
    """

    class RailMessage(Message):
        def __init__(self, rail: ProfileRail, row: ProfileRow | None) -> None:
            self.rail = rail
            self.row = row
            super().__init__()

        @property
        def control(self) -> ProfileRail:
            return self.rail

    class Highlighted(RailMessage):
        """The highlighted profile changed."""

    class Selected(RailMessage):
        """A profile row was chosen with Enter or click."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rows: dict[str, ProfileRow] = {}
        self._highlighted_id: str | None = None

    def on_mount(self) -> None:
        self.set_rows([])

    @property
    def current_row(self) -> ProfileRow | None:
        highlighted = self.highlighted
        if highlighted is None:
            return None
        option = self.get_option_at_index(highlighted)
        return self._rows.get(str(option.id))

    def row_for(self, profile_id: str) -> ProfileRow | None:
        return self._rows.get(profile_id)

    @property
    def rows(self) -> list[ProfileRow]:
        return list(self._rows.values())

    def _name_width(self, rows: list[ProfileRow]) -> int:
        longest = max((len(row.name) for row in rows), default=8)
        available = (self.size.width or 80) - 2 - 1 - 1 - 1 - 2 - 18
        return max(6, min(longest, available))

    def set_rows(self, rows: list[ProfileRow], keep_id: str | None = None) -> None:
        self._rows = {row.profile_id: row for row in rows}
        width = self._name_width(rows)
        options: list[Option] = []
        for row in rows:
            options.append(Option(_row_prompt(row, width, False), id=row.profile_id))
        if not rows:
            options.append(Option("  [$text-muted]no profiles yet — press C to create[/]", disabled=True))
        self.clear_options()
        self.add_options(options)
        self._highlighted_id = None
        target = keep_id if keep_id in self._rows else (rows[0].profile_id if rows else None)
        if target is not None:
            self.highlighted = list(self._rows).index(target)

    def on_resize(self) -> None:
        if self._rows:
            self.set_rows(list(self._rows.values()), keep_id=self._highlighted_id)

    def _refresh_prompt(self, option_id: str, highlighted: bool) -> None:
        row = self._rows.get(option_id)
        if row is None:
            return
        width = self._name_width(list(self._rows.values()))
        try:
            self.replace_option_prompt(option_id, _row_prompt(row, width, highlighted))
        except OptionDoesNotExist:
            return

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        option_id = str(event.option.id) if event.option.id else None
        if option_id and option_id != self._highlighted_id:
            previous = self._highlighted_id
            self._highlighted_id = option_id
            self.call_after_refresh(self._swap_prompts, previous, option_id)
        self.post_message(self.Highlighted(self, self._rows.get(option_id or "")))

    def _swap_prompts(self, previous: str | None, current: str) -> None:
        if previous:
            self._refresh_prompt(previous, False)
        self._refresh_prompt(current, True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.post_message(self.Selected(self, self._rows.get(str(event.option.id or ""))))


def _row_prompt(row: ProfileRow, name_width: int, highlighted: bool) -> str:
    icon = PROFILE_GLYPH if icons_enabled() else PROFILE_FALLBACK
    name = row.name
    if len(name) > name_width:
        name = name[: max(1, name_width - 1)] + "…"
    name = name.ljust(name_width)
    cursor = f"[$accent]{CURSOR_GLYPH}[/]" if highlighted else " "
    glyph = f"[$secondary]{icon}[/]"
    label = f"[$text]{name}[/]"
    return f"{cursor} {glyph} {label}  {_status_badge(row.status, row.pid)}"


class TelemetryCards(Static):
    """Label/value telemetry card grid for the selected profile."""

    DEFAULT_CSS = """
    TelemetryCards {
        background: transparent;
        padding: 1 2;
    }
    """

    def show_entries(self, title: str, entries: list[tuple[str, Text]]) -> None:
        table = Table(show_header=False, box=None, padding=(0, 3, 0, 0), title=title, title_justify="left")
        table.add_column("key", style="bold dim", no_wrap=True)
        table.add_column("value")
        for label, value in entries:
            table.add_row(f"{label}:", value)
        self.update(table)

    def show_message(self, message: str) -> None:
        self.update(Text(message, style="italic dim"))


class CommandPreview(Static):
    """Amber action-path banner describing the highlighted command."""

    DEFAULT_CSS = """
    CommandPreview {
        padding: 0 2;
        height: auto;
    }
    """

    def show_action(self, spec: ActionSpec) -> None:
        destructive = "\n  [bold $error]⚠ destructive: requires confirmation[/]" if spec.destructive else ""
        self.update(
            f"[bold $accent]profiledock {spec.label}[/]"
            f" [$text-muted]─[/] [$text]{spec.description}[/]"
            f"\n[$text-muted]key {spec.hotkey.upper()} · group {spec.group}{destructive}[/]"
        )

    def show_placeholder(self) -> None:
        self.update("[$text-muted italic]highlight a command to preview it[/]")


class OutputPane(VerticalScroll):
    """Scrollable result view with a CLI-faithful exit badge."""

    can_focus = True

    DEFAULT_CSS = """
    OutputPane {
        background: transparent;
        padding: 0 1 1 1;
    }
    #output-body {
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._body = Static("", id="output-body", markup=False)

    def compose(self) -> ComposeResult:
        yield self._body

    def set_result(
        self,
        argv: list[str],
        exit_code: int,
        body: Text,
        category: str = "",
        hint: str = "",
    ) -> None:
        title = Text()
        if exit_code == 0:
            title.append(" OK ", style="bold black on green")
        else:
            label = f" EXIT {exit_code} " + (f"[{category}] " if category else "")
            title.append(label, style="bold black on red")
        title.append("  $ ", style="dim")
        title.append("profiledock " + " ".join(argv))
        if exit_code != 0 and hint:
            body = body.copy()
            body.append(f"\nNext steps: {hint}", style="italic dim")
        combined = Text()
        combined.append_text(title)
        combined.append("\n\n")
        combined.append_text(body)
        self._body.update(combined)
        self.scroll_home(animate=False)

    def set_busy(self, message: str) -> None:
        self._body.update(Text(f"… {message}", style="italic yellow"))
