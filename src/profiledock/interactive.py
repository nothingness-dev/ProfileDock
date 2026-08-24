"""Interactive full-screen shell launched by bare `profiledock` on a TTY.

Built on Textual (optional dependency, extra `interactive`). Commands run
through the real Typer application via CliRunner so prompts, hints, exit
codes, and output formatting stay identical to direct invocation.
"""

from typing import Any, ClassVar, Optional

from .cli_contract import EXIT_SUCCESS

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

    TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    TEXTUAL_AVAILABLE = False

if TEXTUAL_AVAILABLE:
    CommandSpec = tuple[str, str, tuple[str, ...], bool]
    MENU_ITEMS: list[CommandSpec] = [
        ("list", "List all profiles", (), False),
        ("status", "Show runtime status", (), False),
        ("doctor", "Check installation and data health", (), False),
        ("logs", "Read recent logs", ("--last", "25"), False),
        ("create", "Create a new profile", ("name:",), False),
        ("show", "Show profile details", ("profile:",), False),
        ("launch", "Launch a profile's browser", ("profile:",), False),
        ("close", "Close a running profile", ("profile:",), False),
        ("rename", "Rename a profile", ("profile:", "new name:"), False),
        ("set-engine", "Set a profile's engine", ("profile:", "engine (direct/playwright):"), False),
        ("backup", "Back up a profile", ("profile (empty for --all):", "--output path:"), False),
        ("restore", "Restore from an archive", ("archive path:",), True),
        ("delete", "Delete a profile permanently", ("profile:",), True),
    ]

    QUIT_LABEL = "quit"


def run_interactive() -> int:
    """Run the interactive shell; returns a process-style exit code."""
    if not TEXTUAL_AVAILABLE:
        return 1
    app = InteractiveApp()
    app.run()
    return 0


if TEXTUAL_AVAILABLE:

    class InteractiveApp(App[None]):
        TITLE = "ProfileDock"
        SUB_TITLE = "isolated persistent Chromium profiles"

        CSS = """
        ListView { height: auto; max-height: 70%; margin: 1 2; }
        ListItem { padding: 0 2; }
        ListItem.--highlighted { background: $accent; }
        #output-pane { display: none; height: 1fr; border: round $primary;
                       padding: 0 2; margin: 0 2; }
        #prompt-bar { display: none; height: auto; margin: 0 2; }
        #status-line { dock: bottom; height: 1; padding: 0 2; color: $text-muted; }
        """

        BINDINGS: ClassVar[list[Any]] = [
            Binding("q", "quit", "Quit"),
            Binding("escape", "back_to_menu", "Back"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self._awaiting_spec: Optional[tuple[str, tuple[str, ...], bool]] = None
            self._answers: dict[str, str] = {}

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Vertical():
                yield ListView(
                    *[
                        ListItem(Label(f"{name}  —  {summary}"), name=name)
                        for name, summary, _, _ in MENU_ITEMS
                    ],
                    id="menu",
                )
                yield Static("", id="output-pane")
                yield Input(placeholder="", id="prompt-bar")
                yield Label("", id="status-line")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#menu", ListView).focus()

        def _set_status(self, text: str) -> None:
            self.query_one("#status-line", Label).update(text)

        def _show_output(self, title: str, body: str) -> None:
            pane = self.query_one("#output-pane", Static)
            pane.update(f"[b]{title}[/b]\n\n{body.rstrip() or '(no output)'}")
            pane.styles.display = "block"

        def _hide_output(self) -> None:
            pane = self.query_one("#output-pane", Static)
            pane.styles.display = "none"
            pane.update("")

        def _run_cli(self, argv: list[str], destructive: bool) -> None:
            from typer.testing import CliRunner

            from .cli import app as typer_app

            result = CliRunner().invoke(typer_app, argv)
            combined = result.output
            if result.stderr:
                combined += "\n" + result.stderr
            status = "ok" if result.exit_code == EXIT_SUCCESS else f"exit {result.exit_code}"
            self._show_output(f"$ profiledock {' '.join(argv)}  [{status}]", combined)
            self._set_status("")

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            chosen = str(event.item.name)
            if chosen == QUIT_LABEL:
                self.exit()
                return
            spec = next((item for item in MENU_ITEMS if item[0] == chosen), None)
            if spec is None:
                return
            _, _, prompt_labels, destructive = spec
            self._hide_output()
            if not prompt_labels:
                self._run_cli([chosen], destructive)
                return
            self._begin_prompts(chosen, prompt_labels, destructive)

        def _begin_prompts(self, command: str, prompt_labels: tuple[str, ...], destructive: bool) -> None:
            self._awaiting_spec = (command, prompt_labels, destructive)
            self._answers = {}
            bar = self.query_one("#prompt-bar", Input)
            bar.styles.display = "block"
            self._next_prompt()

        def _next_prompt(self) -> None:
            if self._awaiting_spec is None:
                return
            command, prompt_labels, _ = self._awaiting_spec
            remaining = [label for label in prompt_labels if label not in self._answers]
            if not remaining:
                self._finish_prompts()
                return
            bar = self.query_one("#prompt-bar", Input)
            bar.placeholder = remaining[0]
            bar.value = ""
            bar.focus()
            self._set_status(f"{command}: enter {remaining[0].rstrip(':')} (Enter to continue)")

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if self._awaiting_spec is None:
                return
            command, prompt_labels, _ = self._awaiting_spec
            current = event.input.placeholder
            self._answers[current] = event.value
            self._next_prompt()

        def _finish_prompts(self) -> None:
            if self._awaiting_spec is None:
                return
            command, _, destructive = self._awaiting_spec
            self._awaiting_spec = None
            bar = self.query_one("#prompt-bar", Input)
            bar.styles.display = "none"
            argv = [command]
            for label, value in self._answers.items():
                cleaned = value.strip()
                if label.startswith("--"):
                    flag = label.split(" ", 1)[0]
                    if cleaned:
                        argv.extend([flag, cleaned])
                elif cleaned:
                    argv.append(cleaned)
            self._run_cli(argv, destructive)

        def action_back_to_menu(self) -> None:
            self._awaiting_spec = None
            bar = self.query_one("#prompt-bar", Input)
            bar.styles.display = "none"
            self._hide_output()
            self.query_one("#menu", ListView).focus()
            self._set_status("")
