"""ProfileDock TUI application: layout, state machine, and key handling.

State machine
-------------
``BROWSE``  command deck + profile rail + telemetry preview (Mode A)
``FORM``    interactive parameter entry for one action (Mode B)
``OUTPUT``  scrollable CLI-faithful result view

Transitions
-----------
BROWSE --Enter/hotkey (instant action)--> OUTPUT
BROWSE --Enter/hotkey (parameterized)--> FORM --> (ConfirmModal if destructive) --> OUTPUT
OUTPUT --Esc--> BROWSE,  FORM --Esc--> BROWSE,  any --q--> exit
``/`` toggles in-place deck filtering; ``Tab`` cycles deck <-> rail in BROWSE.
"""

from __future__ import annotations

from typing import Any, Callable, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Input, Label

from ..data_root import DataPaths
from . import backend, theme
from .actions import ACTIONS, ACTIONS_BY_ID, GROUP_TITLES, ActionSpec
from .widgets import (
    CommandDeck,
    CommandPreview,
    ConfirmModal,
    FooterBar,
    FormPanel,
    HeaderBar,
    OutputPane,
    ProfileRail,
    TelemetryCards,
    breadcrumb,
)

MIN_WIDTH = 70
MIN_HEIGHT = 18

BROWSE_CHIPS = [
    ("↑↓", "Navigate"),
    ("Enter", "Run"),
    ("Tab", "Pane"),
    ("/", "Filter"),
    ("T", "Theme"),
    ("Q", "Quit"),
]
FORM_CHIPS = [("Tab", "Next Field"), ("Enter", "Submit"), ("Esc", "Back")]
OUTPUT_CHIPS = [("↑↓", "Scroll"), ("Esc", "Back"), ("Q", "Quit")]


class Mode:
    BROWSE = "browse"
    FORM = "form"
    OUTPUT = "output"


class ProfileDockApp(App[None]):
    """Keyboard-driven manager for isolated persistent Chromium profiles."""

    TITLE = "ProfileDock"
    SUB_TITLE = "isolated persistent Chromium profiles"

    CSS = """
    Screen { background: $background; }

    #main { height: 1fr; margin: 0 1; }

    .panel {
        border: solid $pd-border;
        background: $surface;
    }
    .panel:focus-within {
        border: solid $primary;
    }

    #deck-panel {
        width: 35%;
        min-width: 30;
    }
    #deck-filter {
        display: none;
        height: 3;
        border: none;
        background: $panel;
        padding: 0 1;
        margin: 0 1 0 1;
    }
    #deck-filter:focus {
        border: none;
        background: $panel;
        padding: 0 1;
    }
    #deck { height: 1fr; }

    #inspector-panel {
        width: 1fr;
        margin-left: 1;
    }
    #inspect-pane { height: 1fr; }
    #rail {
        height: 45%;
        border-bottom: solid $pd-border;
    }
    #cards { height: 1fr; }
    #preview {
        height: auto;
        border-top: solid $pd-border;
    }

    #form-pane, #output-pane { display: none; height: 1fr; }

    #too-small {
        display: none;
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $error;
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[Any]] = [
        Binding("q", "quit_app", "Quit"),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("escape", "step_back", "Back", show=False),
        Binding("t", "cycle_theme", "Theme", show=False),
        Binding("tab", "switch_pane", "Switch Pane", show=False),
        Binding("shift+tab", "switch_pane_back", "Switch Pane", show=False),
        Binding("/", "filter", "Filter", show=False),
        Binding("ctrl+o", "toggle_advanced", "Advanced", show=False),
        *[Binding(spec.hotkey, f"exec('{spec.id}')", spec.label, show=False) for spec in ACTIONS],
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        theme.register_profiledock_themes(self)
        self.theme = theme.configured_theme()
        self._paths: DataPaths | None = None
        self._rows: list[backend.ProfileRow] = []
        self._browsers: list[backend.BrowserInfo] = []
        self._mode: str = Mode.BROWSE
        self._busy = False
        self._selected_profile_id: str | None = None
        self._pending: tuple[ActionSpec, dict[str, object]] | None = None
        self._last_spec: ActionSpec | None = None

    # ------------------------------------------------------------------
    # layout

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="app-header")
        with Horizontal(id="main"):
            with Vertical(id="deck-panel", classes="panel"):
                yield Input(placeholder="/ filter commands…", id="deck-filter")
                yield CommandDeck(id="deck")
            with Vertical(id="inspector-panel", classes="panel"):
                with Vertical(id="inspect-pane"):
                    yield ProfileRail(id="rail")
                    yield TelemetryCards(id="cards")
                    yield CommandPreview(id="preview")
                yield FormPanel(id="form-pane")
                yield OutputPane(id="output-pane")
        yield FooterBar(id="app-footer")
        yield Label(
            f"Terminal too small for ProfileDock — resize to at least {MIN_WIDTH}x{MIN_HEIGHT}.",
            id="too-small",
        )

    def on_mount(self) -> None:
        self.query_one("#app-footer", FooterBar).set_theme_label(str(self.theme))
        self.query_one("#deck-panel", Vertical).border_title = "Command Deck"
        self.query_one("#inspector-panel", Vertical).border_title = "Inspector"
        self.query_one("#rail", ProfileRail).border_title = "Profiles"
        self._paths = self._resolve_paths()
        self.query_one("#deck", CommandDeck).focus()
        self._update_footer()
        self._check_size()
        self.refresh_profiles(with_sizes=True)
        self._prefetch_browsers()
        self.set_interval(5, self._periodic_refresh)

    @staticmethod
    def _resolve_paths() -> DataPaths | None:
        try:
            from ..cli import selected_paths

            return selected_paths()
        except Exception:
            pass
        try:
            from ..data_root import resolve_data_root

            return resolve_data_root(prepare=True)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # background workers

    def _launch_worker(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        group: str,
    ) -> None:
        def wrapper() -> None:
            try:
                result = fn()
            except Exception:
                return
            try:
                self.call_from_thread(on_done, result)
            except Exception:
                if not self.is_running:
                    return
                raise

        self.run_worker(wrapper, thread=True, group=group, exclusive=True)

    def refresh_profiles(self, with_sizes: bool = False) -> None:
        if self._paths is None:
            return
        keep = self._selected_profile_id
        paths = self._paths
        self._launch_worker(
            lambda: backend.list_profile_rows(paths, with_sizes=with_sizes),
            lambda rows: self._apply_rows(rows, keep),
            group="profiles",
        )

    def _prefetch_browsers(self) -> None:
        self._launch_worker(backend.detect_browsers, self._apply_browsers, group="browsers")

    def _apply_browsers(self, browsers: list[backend.BrowserInfo]) -> None:
        self._browsers = browsers

    def _apply_rows(self, rows: list[backend.ProfileRow], keep: str | None) -> None:
        self._rows = rows
        rail = self.query_one("#rail", ProfileRail)
        rail.set_rows(rows, keep_id=keep or self._selected_profile_id)
        self._update_header()
        self._show_cards()

    def _periodic_refresh(self) -> None:
        if not self._busy:
            self.refresh_profiles(with_sizes=False)

    def _update_header(self) -> None:
        header = self.query_one("#app-header", HeaderBar)
        running, storage = backend.storage_summary(self._rows)
        engines = [backend.effective_engine(row.profile) for row in self._rows]
        engine = max(set(engines), key=engines.count) if engines else "auto"
        header.set_metrics(running, len(self._rows), storage, engine)

    def _show_cards(self) -> None:
        rail = self.query_one("#rail", ProfileRail)
        cards = self.query_one("#cards", TelemetryCards)
        row = rail.current_row
        if row is None:
            if not self._rows:
                cards.show_message("no profiles yet — press C to create one")
            return
        self._selected_profile_id = row.profile_id
        self._render_cards(row)

    def _render_cards(self, row: backend.ProfileRow) -> None:
        if self._paths is None:
            return
        cards = self.query_one("#cards", TelemetryCards)
        paths = self._paths
        self._launch_worker(
            lambda: backend.profile_card(paths, row),
            lambda entries: cards.show_entries(f"Profile: {row.name}", entries),
            group="cards",
        )

    # ------------------------------------------------------------------
    # mode handling

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        inspect_pane = self.query_one("#inspect-pane", Vertical)
        form_pane = self.query_one("#form-pane", FormPanel)
        output_pane = self.query_one("#output-pane", OutputPane)
        inspect_pane.styles.display = "block" if mode == Mode.BROWSE else "none"
        form_pane.styles.display = "block" if mode == Mode.FORM else "none"
        output_pane.styles.display = "block" if mode == Mode.OUTPUT else "none"

        interactive = mode == Mode.BROWSE
        deck = self.query_one("#deck", CommandDeck)
        rail = self.query_one("#rail", ProfileRail)
        filt = self.query_one("#deck-filter", Input)
        if not interactive and filt.display:
            filt.value = ""
            filt.display = False
            deck.set_filter("")
        deck.disabled = not interactive
        rail.disabled = not interactive
        deck.reset_armed()
        rail.reset_armed()

        if mode == Mode.BROWSE:
            deck.focus()
        elif mode == Mode.FORM:
            form_pane.focus_first()
        else:
            output_pane.focus()
        self._update_footer()

    def _footer_args(self) -> tuple[list[str], list[tuple[str, str]]]:
        if self._mode == Mode.FORM:
            form = self.query_one("#form-pane", FormPanel)
            spec = form.spec
            group_title = GROUP_TITLES[spec.group][2] if spec else ""
            chips = list(FORM_CHIPS)
            if form.has_advanced:
                label = "Hide Advanced" if form.advanced_visible else "Advanced"
                chips.append(("Ctrl+O", label))
            return breadcrumb(group_title, spec.label if spec else "form"), chips
        if self._mode == Mode.OUTPUT:
            label = self._last_spec.label if self._last_spec else ""
            return breadcrumb(label, "Result"), OUTPUT_CHIPS
        spec = self.query_one("#deck", CommandDeck).current_spec
        group_title = GROUP_TITLES[spec.group][2] if spec else ""
        return breadcrumb(group_title, spec.label if spec else ""), BROWSE_CHIPS

    def _update_footer(self) -> None:
        crumbs, chips = self._footer_args()
        self.query_one("#app-footer", FooterBar).set_context(crumbs, chips)

    # ------------------------------------------------------------------
    # action flow

    async def begin_action(self, spec: ActionSpec, preselect: str | None = None) -> None:
        if self._busy:
            return
        self._last_spec = spec
        if spec.instant:
            self._execute(spec, {})
            return
        preselect_id = preselect or self._selected_profile_id
        if not preselect_id and self._rows:
            preselect_id = self._rows[0].profile_id
        form = self.query_one("#form-pane", FormPanel)
        await form.set_context(spec, self._rows, self._browsers, preselect_profile=preselect_id or "")
        self._set_mode(Mode.FORM)

    def _execute(self, spec: ActionSpec, values: dict[str, object]) -> None:
        self._last_spec = spec
        self._set_mode(Mode.OUTPUT)
        output = self.query_one("#output-pane", OutputPane)
        if self._paths is None:
            output.set_result(
                [spec.label], 1, Text("data root unavailable", style="bold red"), "storage_error"
            )
            return
        self._busy = True
        output.set_busy(f"running profiledock {spec.label} …")
        paths = self._paths
        self._launch_worker(
            lambda: backend.run_action(paths, spec.id, values),
            self._apply_result,
            group="action",
        )

    def _apply_result(self, result: backend.ActionResult) -> None:
        self._busy = False
        output = self.query_one("#output-pane", OutputPane)
        output.set_result(result.argv, result.exit_code, result.body, result.category, result.hint)
        self.refresh_profiles(with_sizes=result.ok)
        crumbs, chips = self._footer_args()
        status = "ok" if result.ok else f"error: {result.category or 'failed'}"
        self.query_one("#app-footer", FooterBar).set_context(crumbs, chips, status=status)

    def _cancel_form(self) -> None:
        self._pending = None
        self.query_one("#form-pane", FormPanel).clear()
        self._set_mode(Mode.BROWSE)

    # ------------------------------------------------------------------
    # event handlers

    def on_command_deck_highlighted(self, event: CommandDeck.Highlighted) -> None:
        try:
            preview = self.query_one("#preview", CommandPreview)
        except NoMatches:
            return
        if event.spec is not None:
            preview.show_action(event.spec)
        else:
            preview.show_placeholder()
        self._update_footer()

    async def on_command_deck_selected(self, event: CommandDeck.Selected) -> None:
        if event.spec is not None:
            await self.begin_action(event.spec)

    def on_profile_rail_highlighted(self, event: ProfileRail.Highlighted) -> None:
        if self._mode != Mode.BROWSE:
            return
        if event.row is not None:
            self._selected_profile_id = event.row.profile_id
            self._render_cards(event.row)
        else:
            self.query_one("#cards", TelemetryCards).show_message("no profiles yet — press C to create one")

    async def on_profile_rail_selected(self, event: ProfileRail.Selected) -> None:
        if event.row is not None:
            await self.begin_action(ACTIONS_BY_ID["launch"], preselect=event.row.profile_id)

    def on_form_panel_submitted(self, event: FormPanel.Submitted) -> None:
        spec = event.form.spec
        if spec is None:
            return
        if spec.destructive:
            values = event.values
            raw_target = str(
                values.get("profile") or values.get("archive") or values.get("target") or ""
            ).strip()
            target = next((row.name for row in self._rows if row.profile_id == raw_target), raw_target)
            modal = ConfirmModal(spec, target or "all profiles", typed=spec.id == "delete")
            self._pending = (spec, values)
            self.push_screen(modal, self._on_confirmed)
            return
        self._execute(spec, event.values)

    def _on_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed or self._pending is None:
            return
        spec, values = self._pending
        self._pending = None
        self._execute(spec, values)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "deck-filter":
            self.query_one("#deck", CommandDeck).set_filter(event.value)
            self._update_footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "deck-filter":
            deck = self.query_one("#deck", CommandDeck)
            spec = deck.current_spec
            self._close_filter()
            if spec is not None:
                await self.begin_action(spec)

    # ------------------------------------------------------------------
    # bindings

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("exec", "filter"):
            return self._mode == Mode.BROWSE and not self._busy
        if action in ("switch_pane", "switch_pane_back"):
            return self._mode == Mode.BROWSE
        if action == "toggle_advanced":
            form = self.query_one("#form-pane", FormPanel)
            return self._mode == Mode.FORM and form.has_advanced
        return True

    def action_quit_app(self) -> None:
        self.exit()

    async def action_exec(self, action_id: str) -> None:
        spec = ACTIONS_BY_ID.get(action_id)
        if spec is not None:
            await self.begin_action(spec)

    def action_cycle_theme(self) -> None:
        new_theme = theme.next_theme(str(self.theme))
        self.theme = new_theme
        footer = self.query_one("#app-footer", FooterBar)
        footer.set_theme_label(new_theme)
        crumbs, chips = self._footer_args()
        footer.set_context(crumbs, chips, status=f"theme: {new_theme}")

    async def action_toggle_advanced(self) -> None:
        form = self.query_one("#form-pane", FormPanel)
        form.toggle_advanced()
        self._update_footer()

    def action_switch_pane(self) -> None:
        focused = self.focused
        rail = self.query_one("#rail", ProfileRail)
        deck = self.query_one("#deck", CommandDeck)
        (rail if focused is deck else deck).focus()

    def action_switch_pane_back(self) -> None:
        self.action_switch_pane()

    def action_filter(self) -> None:
        if self._mode != Mode.BROWSE:
            return
        filt = self.query_one("#deck-filter", Input)
        filt.display = True
        filt.focus()

    def _close_filter(self) -> None:
        filt = self.query_one("#deck-filter", Input)
        filt.value = ""
        filt.display = False
        deck = self.query_one("#deck", CommandDeck)
        deck.set_filter("")
        deck.focus()
        self._update_footer()

    def action_step_back(self) -> None:
        filt = self.query_one("#deck-filter", Input)
        if filt.display and (filt.has_focus or filt.value):
            self._close_filter()
            return
        if self._mode == Mode.FORM:
            self._cancel_form()
        elif self._mode == Mode.OUTPUT:
            self._set_mode(Mode.BROWSE)

    # ------------------------------------------------------------------
    # responsive shell

    def on_resize(self) -> None:
        self._check_size()

    def _check_size(self) -> None:
        size = self.size
        too_small = size.width < MIN_WIDTH or size.height < MIN_HEIGHT
        self.query_one("#too-small", Label).styles.display = "block" if too_small else "none"
        main = self.query_one("#main", Horizontal)
        main.styles.display = "none" if too_small else "block"
