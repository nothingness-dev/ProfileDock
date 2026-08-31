"""Overlay screens: destructive-action confirmation with countdown or typing."""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from ..actions import ActionSpec


class ConfirmModal(ModalScreen[bool]):
    """Centered safeguard for destructive actions.

    Two confirmation styles: a 10-second countdown with Y/N for reversible
    operations (restore), and typed confirmation where the operator must
    re-enter the target profile name for permanent deletes.
    """

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
        background: $background 60%;
    }
    #confirm-box {
        width: 64;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        border: solid $error;
        background: $surface;
        padding: 1 2;
    }
    #confirm-title {
        color: $error;
        text-style: bold;
        margin-bottom: 1;
    }
    #confirm-message {
        color: $text;
        margin-bottom: 1;
    }
    #confirm-input {
        border: none;
        background: $panel;
        height: 3;
        padding: 0 1;
    }
    #confirm-input:focus {
        border: none;
        background: $panel;
        padding: 0 1;
    }
    #confirm-hint {
        color: $text-muted;
    }
    #confirm-countdown {
        color: $accent;
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[Any]] = [
        ("escape", "cancel", "Cancel"),
        ("n", "cancel", "No"),
        ("y", "confirm", "Yes"),
        ("enter", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        spec: ActionSpec,
        target: str,
        typed: bool = False,
        countdown: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._spec = spec
        self._target = target
        self._typed = typed
        self._remaining = countdown

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(f"Warning: profiledock {self._spec.label}", id="confirm-title")
            if self._typed:
                message = (
                    f"Delete profile '{self._target}' and all of its browser data permanently?\n"
                    f"This cannot be undone. Type the profile name to confirm:"
                )
            else:
                message = f"Run 'profiledock {self._spec.label}' on '{self._target}'?"
            yield Static(Text(message), id="confirm-message")
            if self._typed:
                yield Input(placeholder=self._target, id="confirm-input")
                yield Label("[Enter] confirm  ·  [Esc] cancel", id="confirm-hint")
            else:
                yield Label("", id="confirm-countdown")
                yield Label("[Y] confirm  ·  [N/Esc] cancel", id="confirm-hint")

    def on_mount(self) -> None:
        if self._typed:
            self.query_one("#confirm-input", Input).focus()
        else:
            self._update_countdown()
            self.set_interval(1, self._tick)

    def _update_countdown(self) -> None:
        self.query_one("#confirm-countdown", Label).update(
            Text(f"auto-cancel in {self._remaining}s", style="bold")
        )

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self.dismiss(False)
            return
        self._update_countdown()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        if self._typed:
            typed_value = self.query_one("#confirm-input", Input).value.strip()
            if typed_value != self._target:
                mismatch = f"'{typed_value or '(empty)'}' does not match '{self._target}' — type it exactly"
                self.query_one("#confirm-hint", Label).update(Text(mismatch, style="bold red"))
                return
        self.dismiss(True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_confirm()

    @property
    def target(self) -> str:
        return self._target

    @property
    def requires_typed_confirmation(self) -> bool:
        return self._typed

    @property
    def remaining_seconds(self) -> int | None:
        return None if self._typed else self._remaining
