"""Command deck: the categorized, filterable command list in the left pane."""

from __future__ import annotations

from typing import Any, ClassVar

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..actions import GROUP_TITLES, ActionSpec, fuzzy_score, grouped_actions

CURSOR_GLYPH = ">"


class VimOptionList(OptionList):
    """OptionList with vim-style navigation; clicks move focus, Enter runs."""

    BINDINGS: ClassVar[list[Any]] = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._enter_armed = False

    def scroll_visible(self, *args: Any, **kwargs: Any) -> None:
        return

    def reset_armed(self) -> None:
        """Drop any pending keyboard-select arming (used across mode switches)."""
        self._enter_armed = False

    async def _on_key(self, event: events.Key) -> None:
        """Arm the next select so only genuine keyboard Enter reaches it."""
        if event.key == "enter":
            self._enter_armed = True

    async def _on_click(self, event: events.Click) -> None:
        """Clicks move the selection; running a command stays on Enter."""
        clicked_option: int | None = event.style.meta.get("option")
        if clicked_option is not None and not self._options[clicked_option].disabled:
            self.highlighted = clicked_option

    def _highlighted_option_id(self) -> str | None:
        index = self.highlighted
        if index is None:
            return None
        option = self.get_option_at_index(index)
        return str(option.id) if option.id else None

    def action_select(self) -> None:
        """Consume at most one armed Enter; clicks route here unarmed and stay inert."""
        armed, self._enter_armed = self._enter_armed, False
        if not armed:
            return
        identifier = self._highlighted_option_id()
        if identifier is not None:
            self._emit_selected(identifier)

    def _emit_selected(self, identifier: str) -> None:
        """Subclasses turn the highlighted id into their Selected message."""
        return


def _name_width() -> int:
    return max((len(action.label) for _, actions in grouped_actions() for action in actions), default=10)


def _group_header(group_id: str) -> Option:
    _nerd, _fallback, title = GROUP_TITLES[group_id]
    return Option(f"[bold]{title}[/]", id=f"group:{group_id}", disabled=True)


def _command_prompt(spec: ActionSpec, name_width: int, desc_width: int, highlighted: bool) -> str:
    name = spec.label.ljust(name_width)
    key = f"[{spec.hotkey.upper()}]"
    description = spec.description
    if len(description) > desc_width:
        description = description[: max(1, desc_width - 1)] + "…"
    cursor = f"[$accent]{CURSOR_GLYPH}[/]" if highlighted else " "
    label = f"[$text]{name}[/]"
    desc = f"[$text-muted]{description}[/]"
    return f"{cursor} {key} {label}  {desc}"


def _all_actions() -> list[ActionSpec]:
    return [action for _, actions in grouped_actions() for action in actions]


def _filtered(actions: list[ActionSpec], query: str) -> list[ActionSpec]:
    if not query:
        return actions
    scored: list[tuple[int, ActionSpec]] = []
    for action in actions:
        score = fuzzy_score(query, f"{action.label} {action.description}")
        if score is not None:
            scored.append((score, action))
    scored.sort(key=lambda pair: (-pair[0], pair[1].label))
    return [action for _, action in scored]


class CommandDeck(VimOptionList):
    """Grouped, filterable command list with a plain selection cursor."""

    DEFAULT_CSS = """
    CommandDeck {
        background: transparent;
        border: none;
        padding: 0 1;
        overflow-x: hidden;
    }
    CommandDeck:focus {
        border: none;
    }
    CommandDeck > .option-list--option-highlighted {
        background: $pd-selection;
    }
    """

    class DeckMessage(Message):
        def __init__(self, deck: CommandDeck, spec: ActionSpec | None) -> None:
            self.deck = deck
            self.spec = spec
            super().__init__()

        @property
        def control(self) -> CommandDeck:
            return self.deck

    class Selected(DeckMessage):
        """A command was chosen with Enter."""

    class Highlighted(DeckMessage):
        """The highlighted command changed; the preview pane follows."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._specs: dict[str, ActionSpec] = {}
        self._highlighted_id: str | None = None
        self._query = ""

    def on_mount(self) -> None:
        self.call_after_refresh(self.set_filter, "")

    def on_resize(self) -> None:
        self.set_filter(self._query)

    def _desc_width(self) -> int:
        width = self.size.width or 80
        prefix = 1 + 1 + 3 + 1 + _name_width() + 2
        return max(8, width - 2 - prefix)

    @property
    def current_spec(self) -> ActionSpec | None:
        highlighted = self.highlighted
        if highlighted is None:
            return None
        option = self.get_option_at_index(highlighted)
        return self._specs.get(str(option.id))

    def spec_for(self, action_id: str) -> ActionSpec | None:
        return self._specs.get(action_id)

    def set_filter(self, query: str) -> None:
        self._query = query
        self._specs = {}
        options: list[Option] = []
        width = _name_width()
        desc_width = self._desc_width()
        for group_id, actions in grouped_actions():
            matches = _filtered(actions, query)
            if not matches:
                continue
            options.append(_group_header(group_id))
            for spec in matches:
                self._specs[spec.id] = spec
                options.append(Option(_command_prompt(spec, width, desc_width, False), id=spec.id))
        if not options:
            options.append(Option("  [$text-muted]no matching commands[/]", disabled=True))
        self.clear_options()
        self.add_options(options)
        self._highlighted_id = None
        first_command = next((index for index, option in enumerate(options) if not option.disabled), None)
        if first_command is not None:
            self.highlighted = first_command

    def focus_first(self) -> None:
        self.highlighted = 0
        self.focus()

    def _refresh_prompt(self, option_id: str, highlighted: bool) -> None:
        spec = self._specs.get(option_id)
        if spec is None:
            return
        try:
            self.replace_option_prompt(
                option_id, _command_prompt(spec, _name_width(), self._desc_width(), highlighted)
            )
        except OptionDoesNotExist:
            return

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        option_id = str(event.option.id) if event.option.id else None
        if option_id and option_id != self._highlighted_id:
            previous = self._highlighted_id
            self._highlighted_id = option_id
            self.call_after_refresh(self._swap_prompts, previous, option_id)
        self.post_message(self.Highlighted(self, self._specs.get(option_id or "")))

    def _swap_prompts(self, previous: str | None, current: str) -> None:
        if previous:
            self._refresh_prompt(previous, False)
        self._refresh_prompt(current, True)

    def _emit_selected(self, identifier: str) -> None:
        spec = self._specs.get(identifier)
        if spec is not None:
            self.post_message(self.Selected(self, spec))
