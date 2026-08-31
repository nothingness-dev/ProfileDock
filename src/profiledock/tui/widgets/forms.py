"""Interactive form controls replacing raw text prompts.

Each :class:`ActionSpec` renders into a :class:`FormPanel`: labeled rows with
``[ARG]``/``[OPT]``/``[SEL]``/``[PICK]``/``[FLG]`` badges, radio choice lists
for engines and detected browsers, a fuzzy profile picker with live status
badges, a checkbox-style Chromium flag configurator, and live validation with
an assembled CLI preview line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.geometry import Offset
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ...browser_detection import DIRECT_BROWSER_ALIASES
from ..actions import (
    ALL_PROFILES,
    CHROMIUM_FLAGS,
    ActionSpec,
    FieldKind,
    FieldSpec,
    build_argv,
    fuzzy_score,
)
from ..backend import BrowserInfo, ProfileRow
from .deck import VimOptionList

CURSOR_GLYPH = ">"
KNOWN_BROWSER_NAMES = frozenset(DIRECT_BROWSER_ALIASES)
CUSTOM_BROWSER = "__custom__"

_STATUS_COLORS = {
    "running": "$success",
    "starting": "$accent",
    "closing": "$accent",
    "stale": "$error",
    "error": "$error",
}


def _radio_prompt(label: str, selected: bool) -> str:
    marker = "[$accent](•)[/]" if selected else "[$text-muted]( )[/]"
    body = f"[$text]{label}[/]"
    return f"{marker} {body}"


def _flag_prompt(label: str, checked: bool) -> str:
    box = "[$success]\\[x][/]" if checked else "[$text-muted]\\[ ][/]"
    body = f"[$text]{label}[/]"
    return f"{box} {body}"


def _profile_prompt(row: ProfileRow, width: int, selected: bool) -> str:
    status = row.status.upper()
    if status == "STOPPED":
        status = "IDLE"
    pid = f" PID {row.pid}" if row.status == "running" and row.pid else ""
    color = _STATUS_COLORS.get(row.status, "$text-muted")
    cursor = "[$accent]>[/]" if selected else " "
    name = row.name.ljust(width)
    label = f"[$text]{name}[/]"
    badge = f"[bold {color}]\\[{status}{pid}][/]"
    return f"{cursor} {label}  {badge}"


class FormInput(Input):
    """Input that never triggers ancestor scrolling on focus."""

    def scroll_visible(self, *args: Any, **kwargs: Any) -> None:
        return


class ChoiceList(VimOptionList):
    """Single-select radio list used for engines and browsers."""

    DEFAULT_CSS = """
    ChoiceList {
        height: auto;
        max-height: 8;
        background: transparent;
        border: none;
        padding: 0 1;
        scrollbar-size: 0 0;
        overflow-x: hidden;
        text-wrap: nowrap;
        text-overflow: clip;
    }
    ChoiceList:focus {
        border: none;
    }
    ChoiceList > .option-list--option-highlighted {
        background: $pd-selection;
    }
    """

    class Changed(Message):
        def __init__(self, choice: ChoiceList, value: str) -> None:
            self.choice = choice
            self.value = value
            super().__init__()

        @property
        def control(self) -> ChoiceList:
            return self.choice

    def __init__(
        self,
        entries: list[tuple[str, str]],
        selected: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._entries = entries
        self._values = [value for _label, value in entries]
        self._selected = selected if selected in self._values else (self._values[0] if self._values else "")

    @property
    def value(self) -> str:
        return self._selected

    def set_value(self, value: str) -> None:
        if value in self._values:
            self._selected = value
            self._repaint()
            self.post_message(self.Changed(self, value))

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        self.set_options(
            Option(_radio_prompt(label, value == self._selected), id=value) for label, value in self._entries
        )
        if self._selected in self._values:
            self.highlighted = self._values.index(self._selected)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        value = str(event.option.id or "")
        if value:
            self._selected = value
            self._repaint()
            self.post_message(self.Changed(self, value))


class FlagsList(VimOptionList):
    """Multi-select checkbox list for Chromium launch flags."""

    DEFAULT_CSS = """
    FlagsList {
        height: auto;
        max-height: 8;
        background: transparent;
        border: none;
        padding: 0 1;
        scrollbar-size: 0 0;
        overflow-x: hidden;
    }
    FlagsList:focus {
        border: none;
    }
    FlagsList > .option-list--option-highlighted {
        background: $pd-selection;
    }
    """

    class Changed(Message):
        def __init__(self, flags: FlagsList, values: list[str]) -> None:
            self.flags = flags
            self.values = values
            super().__init__()

        @property
        def control(self) -> FlagsList:
            return self.flags

    def __init__(self, options: list[str], checked: list[str] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._flags = options
        self._checked: set[str] = set(checked or [])

    @property
    def value(self) -> list[str]:
        return [flag for flag in self._flags if flag in self._checked]

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        self.set_options(Option(_flag_prompt(flag, flag in self._checked), id=flag) for flag in self._flags)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        flag = str(event.option.id or "")
        if flag:
            if flag in self._checked:
                self._checked.discard(flag)
            else:
                self._checked.add(flag)
            self._repaint()
            self.post_message(self.Changed(self, self.value))


class ProfilePicker(Vertical):
    """Fuzzy-searchable profile selector with live status badges."""

    DEFAULT_CSS = """
    ProfilePicker {
        height: auto;
        width: 1fr;
    }
    ProfilePicker Input {
        border: none;
        height: 3;
        background: $panel;
        padding: 0 1;
    }
    ProfilePicker Input:focus {
        border: none;
        background: $panel;
        padding: 0 1;
    }
    ProfilePicker OptionList {
        height: 8;
        background: transparent;
        border: none;
        padding: 0 1;
        overflow-x: hidden;
    }
    ProfilePicker OptionList:focus {
        border: none;
    }
    ProfilePicker OptionList > .option-list--option-highlighted {
        background: $pd-selection;
    }
    """

    class Changed(Message):
        def __init__(self, picker: ProfilePicker, value: str) -> None:
            self.picker = picker
            self.value = value
            super().__init__()

        @property
        def control(self) -> ProfilePicker:
            return self.picker

    def __init__(
        self,
        rows: list[ProfileRow],
        allow_all: bool,
        preselect: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._rows = rows
        self._allow_all = allow_all
        self._value = preselect
        self._query = ""

    @property
    def value(self) -> str:
        return self._value

    def compose(self) -> ComposeResult:
        yield Input(placeholder="fuzzy-search profiles…")
        yield VimOptionList()

    def on_mount(self) -> None:
        self._repaint()

    def _visible_rows(self) -> list[ProfileRow]:
        if not self._query:
            return self._rows
        return [
            row for row in self._rows if fuzzy_score(self._query, f"{row.name} {row.profile_id}") is not None
        ]

    def _repaint(self) -> None:
        listing = self.query_one("ProfilePicker VimOptionList", VimOptionList)
        options: list[Option] = []
        width = max((len(row.name) for row in self._rows), default=8)
        if self._allow_all:
            options.append(
                Option(_radio_prompt("All profiles", self._value == ALL_PROFILES), id=ALL_PROFILES)
            )
        for row in self._visible_rows():
            selected = row.profile_id == self._value
            options.append(Option(_profile_prompt(row, width, selected), id=row.profile_id))
        listing.set_options(options)

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._query = event.value.strip()
        self._repaint()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the picker's search box commits the best visible match
        # instead of being swallowed — otherwise single-pick forms (delete,
        # show, launch) feel dead on pure keyboard.
        event.stop()
        rows = self._visible_rows()
        if rows:
            target = rows[0].profile_id
        elif self._allow_all:
            target = ALL_PROFILES
        else:
            return
        self._value = target
        self._repaint()
        self.post_message(self.Changed(self, target))


class FieldRow(Horizontal):
    """Label + control layout for one form field."""

    DEFAULT_CSS = """
    FieldRow {
        height: auto;
        margin: 0 0 1 0;
    }
    FieldRow .field-label {
        width: 16;
        min-width: 16;
        color: $text;
        padding-top: 1;
        margin-right: 1;
    }
    FieldRow .field-stack {
        height: auto;
        width: 1fr;
    }
    FieldRow .field-hint {
        max-height: 1;
        overflow: hidden;
        text-wrap: nowrap;
        text-overflow: clip;
    }
    """

    def __init__(self, spec: FieldSpec, *children: Widget, **kwargs: Any) -> None:
        super().__init__(*children, **kwargs)
        self.spec = spec


class FormPanel(VerticalScroll):
    """Mode B inspector: interactive parameter entry for one action."""

    def scroll_visible(self, *args: Any, **kwargs: Any) -> None:
        return

    def scroll_to_widget(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def scroll_to_center(self, *args: Any, **kwargs: Any) -> None:
        return

    def scroll_to_region(self, *args: Any, **kwargs: Any) -> Offset:
        return Offset(0, 0)

    DEFAULT_CSS = """
    FormPanel {
        background: transparent;
        padding: 1 2;
    }
    #form-title {
        color: $text;
        text-style: bold;
        margin-bottom: 0;
    }
    #form-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }
    #form-destructive {
        color: $error;
        margin-bottom: 1;
    }
    FormPanel Input {
        border: none;
        background: $panel;
        height: 3;
        padding: 0 1;
    }
    FormPanel Input:focus {
        border: none;
        background: $panel;
        padding: 0 1;
    }
    FormPanel Checkbox {
        border: none;
        background: transparent;
        padding: 0;
        width: auto;
    }
    FormPanel Checkbox:focus {
        border: none;
        background: transparent;
        padding: 0;
    }
    #form-validation {
        height: auto;
        color: $error;
        margin-top: 1;
        max-height: 3;
        overflow: hidden;
        text-wrap: nowrap;
        text-overflow: clip;
    }
    #form-preview {
        height: auto;
        color: $text-muted;
        margin-top: 1;
        max-height: 1;
        overflow: hidden;
        text-wrap: nowrap;
        text-overflow: clip;
    }
    FormPanel .form-buttons {
        height: auto;
        margin-top: 1;
    }
    FormPanel .form-buttons Button {
        margin-right: 1;
        min-width: 12;
    }
    FormPanel FieldRow.advanced {
        display: none;
    }
    FormPanel FieldRow.advanced.-visible {
        display: block;
    }
    FormPanel Label.adv-header {
        display: none;
        color: $text-muted;
        margin-top: 1;
    }
    FormPanel Label.adv-header.-visible {
        display: block;
    }
    """

    class Submitted(Message):
        def __init__(self, form: FormPanel, values: dict[str, object]) -> None:
            self.form = form
            self.values = values
            super().__init__()

        @property
        def control(self) -> FormPanel:
            return self.form

    class Cancelled(Message):
        def __init__(self, form: FormPanel) -> None:
            self.form = form
            super().__init__()

        @property
        def control(self) -> FormPanel:
            return self.form

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spec: ActionSpec | None = None
        self._rows: list[ProfileRow] = []
        self._browsers: list[BrowserInfo] = []
        self._order: list[Widget] = []
        self._submitted_once = False
        self._advanced = False

    @property
    def spec(self) -> ActionSpec | None:
        return self._spec

    @property
    def has_advanced(self) -> bool:
        return self._spec is not None and any(field_spec.advanced for field_spec in self._spec.fields)

    @property
    def advanced_visible(self) -> bool:
        return self._advanced

    def toggle_advanced(self) -> bool:
        self._advanced = not self._advanced
        for widget in self.query(".advanced"):
            widget.set_class(self._advanced, "-visible")
        return self._advanced

    def clear(self) -> None:
        self._spec = None
        self._order = []
        self._advanced = False
        self.remove_children()

    @property
    def field_order(self) -> list[Widget]:
        return list(self._order)

    async def set_context(
        self,
        spec: ActionSpec,
        rows: list[ProfileRow],
        browsers: list[BrowserInfo],
        preselect_profile: str = "",
    ) -> None:
        self._spec = spec
        self._rows = rows
        self._browsers = browsers
        self._submitted_once = False
        self._advanced = False
        self._order = []
        await self.remove_children()
        await self.mount_all(self._build_fields(spec, preselect_profile))
        self.call_after_refresh(self._refresh_preview)
        self.call_after_refresh(self.focus_first)

    @staticmethod
    def _focus_target(widget: Widget) -> Widget | None:
        """Resolve a field to the widget that can actually take focus.

        Composite fields (ProfilePicker) are containers; calling .focus() on
        them silently no-ops, which left picker-first forms without any focus.
        """
        if widget.focusable:
            return widget
        for descendant in widget.walk_children():
            if isinstance(descendant, Widget) and getattr(descendant, "focusable", False):
                return descendant
        return None

    def focus_first(self) -> None:
        """Focus the first field, retrying briefly.

        ``set_context`` runs while the pane is still being shown; an immediate
        focus() can silently no-op because the widget is not yet visible, and
        the form then renders with no focus at all — every key dead.
        """
        if not self._order:
            return

        def try_focus(attempt: int) -> None:
            if self._spec is None or self.styles.display != "block":
                return
            target = self._focus_target(self._order[0])
            if target is not None:
                target.focus()
            if self.app.focused is target:
                return
            if attempt < 8:
                self.set_timer(0.05, lambda: try_focus(attempt + 1))

        try_focus(0)

    def _focused_field_index(self) -> int | None:
        """Index of the field owning focus; a focused descendant counts as its owner."""
        focused = self.app.focused
        if focused is None:
            return None
        for index, widget in enumerate(self._order):
            if widget is focused or focused in widget.walk_children():
                return index
        return None

    def advance(self) -> None:
        index = self._focused_field_index()
        if index is None:
            self.focus_first()
            return
        if index + 1 < len(self._order):
            target = self._focus_target(self._order[index + 1])
            if target is not None:
                target.focus()
            else:
                self._order[index + 1].focus()
        else:
            self.submit()

    def submit(self) -> None:
        self._submitted_once = True
        errors = self.validate()
        if errors:
            self._show_errors(errors)
            return
        self._clear_errors()
        if self._spec is not None:
            self.post_message(self.Submitted(self, self.values()))

    def _show_errors(self, errors: list[str]) -> None:
        try:
            validation = self.query_one("#form-validation", Label)
        except NoMatches:
            return
        validation.update(Text("\n".join(f"✗ {error}" for error in errors), style="bold red"))

    def _clear_errors(self) -> None:
        try:
            validation = self.query_one("#form-validation", Label)
        except NoMatches:
            return
        validation.update("")

    def validate(self) -> list[str]:
        spec = self._spec
        if spec is None:
            return []
        errors: list[str] = []
        for field_spec in spec.fields:
            value = self._field_value(field_spec)
            if field_spec.kind in (FieldKind.TEXT, FieldKind.NUMBER, FieldKind.PATH):
                text = str(value or "").strip()
                if field_spec.required and not text:
                    errors.append(f"{field_spec.label} is required")
                    continue
                if text and field_spec.kind is FieldKind.NUMBER and (not text.isdigit() or int(text) < 1):
                    errors.append(f"{field_spec.label} must be a positive integer")
                if (
                    text
                    and field_spec.kind is FieldKind.PATH
                    and field_spec.name == "archive"
                    and not Path(text).expanduser().exists()
                ):
                    errors.append(f"archive not found: {text}")
            elif field_spec.kind in (FieldKind.PROFILE, FieldKind.PROFILE_OR_ALL):
                if not str(value or "").strip():
                    errors.append(f"{field_spec.label} is required")
            elif field_spec.kind is FieldKind.BROWSER:
                if str(value or "").strip() == CUSTOM_BROWSER:
                    continue
                custom = self._custom_text(f"{field_spec.name}-custom")
                if str(value or "").strip() == "" and custom and not self._browser_path_ok(custom):
                    errors.append(f"browser binary not found: {custom}")
        return errors

    @staticmethod
    def _browser_path_ok(raw: str) -> bool:
        if Path(raw).expanduser().is_file():
            return True
        return raw.strip().lower() in KNOWN_BROWSER_NAMES

    def values(self) -> dict[str, object]:
        spec = self._spec
        if spec is None:
            return {}
        return {field_spec.name: self._field_value(field_spec) for field_spec in spec.fields}

    def _custom_text(self, suffix: str) -> str:
        try:
            custom = self.query_one(f"#field-{suffix}", Input)
        except NoMatches:
            return ""
        return custom.value.strip()

    def _field_value(self, spec: FieldSpec) -> object:
        try:
            if spec.kind in (FieldKind.TEXT, FieldKind.NUMBER, FieldKind.PATH):
                return self.query_one(f"#field-{spec.name}", Input).value.strip()
            if spec.kind is FieldKind.ENGINE:
                return self.query_one(f"#choice-{spec.name}", ChoiceList).value
            if spec.kind is FieldKind.BROWSER:
                choice = self.query_one(f"#choice-{spec.name}", ChoiceList).value
                if choice == CUSTOM_BROWSER:
                    return self._custom_text(f"{spec.name}-custom")
                return choice
            if spec.kind in (FieldKind.PROFILE, FieldKind.PROFILE_OR_ALL):
                return self.query_one(f"#picker-{spec.name}", ProfilePicker).value
            if spec.kind is FieldKind.FLAGS:
                flags = self.query_one(f"#flags-{spec.name}", FlagsList).value
                extra = self._custom_text(f"{spec.name}-custom")
                if extra:
                    flags = flags + [part for part in extra.split() if part.startswith("--")]
                return flags
            if spec.kind is FieldKind.TOGGLE:
                return self.query_one(f"#toggle-{spec.name}", Checkbox).value
        except NoMatches:
            return "" if spec.kind is not FieldKind.FLAGS else []
        return ""

    def _refresh_preview(self) -> None:
        if self._spec is None:
            return
        try:
            preview = self.query_one("#form-preview", Static)
        except NoMatches:
            return
        argv = build_argv(self._spec, self.values())
        preview.update(Text("$ profiledock " + " ".join(argv), style="dim"))
        if self._submitted_once:
            errors = self.validate()
            if errors:
                self._show_errors(errors)
            else:
                self._clear_errors()

    def _build_fields(self, spec: ActionSpec, preselect_profile: str) -> list[Widget]:
        widgets: list[Widget] = [Label(f"profiledock {spec.label}", id="form-title")]
        widgets.append(Label(spec.description, id="form-subtitle"))
        if spec.destructive:
            widgets.append(Label("destructive action - confirmation required", id="form-destructive"))
        advanced_header_done = False
        for field_spec in spec.fields:
            if field_spec.advanced and not advanced_header_done:
                widgets.append(Label("- advanced options -", classes="adv-header advanced"))
                advanced_header_done = True
            widgets.append(self._build_field(field_spec, preselect_profile))
        widgets.append(Label("", id="form-validation"))
        widgets.append(Static("", id="form-preview", markup=False))
        submit_label = f"Run {spec.label}" if not spec.destructive else f"Confirm {spec.label}"
        widgets.append(
            Horizontal(
                Button(
                    submit_label,
                    variant="primary" if not spec.destructive else "error",
                    id="form-submit",
                ),
                Button("Cancel", variant="default", id="form-cancel"),
                classes="form-buttons",
            )
        )
        return widgets

    def _field_label(self, spec: FieldSpec) -> Label:
        markup = f"{spec.label}"
        if spec.required:
            markup += " [$error]*[/]"
        return Label(markup, classes="field-label")

    @staticmethod
    def _hint_label(spec: FieldSpec) -> Label | None:
        return Label(spec.hint, classes="field-hint") if spec.hint else None

    def _build_field(self, spec: FieldSpec, preselect_profile: str) -> Widget:
        if spec.kind in (FieldKind.TEXT, FieldKind.NUMBER, FieldKind.PATH):
            restrict = "[0-9]" if spec.kind is FieldKind.NUMBER else None
            control: Widget = FormInput(
                value=spec.default,
                placeholder=spec.placeholder or spec.label.lower(),
                id=f"field-{spec.name}",
                restrict=restrict,
            )
            return self._simple_row(spec, control)
        if spec.kind is FieldKind.ENGINE:
            entries = [(option, option) for option in (spec.options or ("direct", "playwright"))]
            # Prefer the spec's explicit default, then the first option —
            # which for the launch override is "(inherit)" so the stored
            # preset/profile/environment precedence stays untouched.
            control = ChoiceList(entries, selected=spec.default or entries[0][0], id=f"choice-{spec.name}")
            return self._simple_row(spec, control)
        if spec.kind is FieldKind.BROWSER:
            entries = [(browser.label(), browser.path) for browser in self._browsers]
            entries.append(("Custom Binary Path…", CUSTOM_BROWSER))
            choice = ChoiceList(
                entries,
                selected=entries[0][1] if entries else CUSTOM_BROWSER,
                id=f"choice-{spec.name}",
            )
            custom = FormInput(placeholder="/path/to/browser", id=f"field-{spec.name}-custom")
            custom.display = False
            return self._stacked_row(spec, choice, custom, focus_custom_on=True)
        if spec.kind in (FieldKind.PROFILE, FieldKind.PROFILE_OR_ALL):
            preselect = preselect_profile if self._rows else ""
            if spec.kind is FieldKind.PROFILE_OR_ALL and not preselect:
                preselect = ALL_PROFILES
            control = ProfilePicker(
                self._rows,
                allow_all=spec.kind is FieldKind.PROFILE_OR_ALL,
                preselect=preselect,
                id=f"picker-{spec.name}",
            )
            return self._simple_row(spec, control)
        if spec.kind is FieldKind.FLAGS:
            control = FlagsList(list(CHROMIUM_FLAGS), id=f"flags-{spec.name}")
            custom = FormInput(
                placeholder="extra --flags separated by spaces", id=f"field-{spec.name}-custom"
            )
            return self._stacked_row(spec, control, custom)
        if spec.kind is FieldKind.TOGGLE:
            control = Checkbox(spec.label, value=spec.toggled, id=f"toggle-{spec.name}")
            return self._simple_row(spec, control, labeled=False)
        return Label(f"unsupported field: {spec.name}")

    def _simple_row(self, spec: FieldSpec, *controls: Widget, labeled: bool = True) -> Widget:
        """Assemble one labeled form row; optional muted hint trails the controls."""
        widgets: list[Widget] = [self._field_label(spec)] if labeled else []
        widgets.extend(controls)
        self._order.extend(controls)
        hint = self._hint_label(spec)
        if hint is not None:
            widgets.append(hint)
        return FieldRow(spec, *widgets, id=f"row-{spec.name}", classes="advanced" if spec.advanced else None)

    def _stacked_row(
        self,
        spec: FieldSpec,
        primary: Widget,
        secondary: Widget,
        focus_custom_on: bool = False,
    ) -> Widget:
        """Labeled row whose controls (plus optional hint) sit in one vertical stack."""
        self._order.append(primary)
        if not focus_custom_on:
            self._order.append(secondary)
        stack_children: list[Widget] = [primary, secondary]
        hint = self._hint_label(spec)
        if hint is not None:
            stack_children.append(hint)
        inner = Vertical(*stack_children, classes="field-stack")
        return FieldRow(
            spec,
            self._field_label(spec),
            inner,
            id=f"row-{spec.name}",
            classes="advanced" if spec.advanced else None,
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_preview()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "form-submit":
            self.submit()
        elif event.button.id == "form-cancel":
            self.post_message(self.Cancelled(self))

    def on_choice_list_changed(self, event: ChoiceList.Changed) -> None:
        event.stop()
        choice_id = str(event.choice.id or "")
        name = choice_id.removeprefix("choice-")
        if name:
            try:
                custom = self.query_one(f"#field-{name}-custom", Input)
                custom.display = event.value == CUSTOM_BROWSER
                if event.value == CUSTOM_BROWSER:
                    custom.focus()
                    return
            except NoMatches:
                pass
        # Updating a choice only refreshes the preview — advancing (or worse,
        # submitting) on select made single-option forms fire accidentally.
        self._refresh_preview()

    def on_profile_picker_changed(self, event: ProfilePicker.Changed) -> None:
        event.stop()
        self._refresh_preview()

    def on_flags_list_changed(self, event: FlagsList.Changed) -> None:
        event.stop()
        self._refresh_preview()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self._refresh_preview()
