"""Command deck: the categorized, filterable command list in the left pane."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..actions import GROUP_TITLES, ActionSpec, fuzzy_score, group_icon, grouped_actions

CURSOR_GLYPH = "❯"  # noqa: RUF001


class VimOptionList(OptionList):
    """OptionList with vim-style j/k navigation and stable focus geometry."""

    BINDINGS: ClassVar[list[Any]] = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def scroll_visible(self, *args: Any, **kwargs: Any) -> None:
        return


def _name_width() -> int:
    return max((len(action.label) for _, actions in grouped_actions() for action in actions), default=10)


def _group_header(group_id: str) -> Option:
    icon = group_icon(group_id)
    _nerd, _fallback, title = GROUP_TITLES[group_id]
    return Option(f"[bold $accent]{icon}  {title}[/]", id=f"group:{group_id}", disabled=True)


def _command_prompt(spec: ActionSpec, name_width: int, desc_width: int, highlighted: bool) -> str:
    name = spec.label.ljust(name_width)
    key = f" {spec.hotkey.upper()} "
    description = spec.description
    if len(description) > desc_width:
        description = description[: max(1, desc_width - 1)] + "…"
    if highlighted:
        cursor = f"[$accent]{CURSOR_GLYPH}[/]"
        pill = f"[bold black on $accent]{key}[/]"
        glyph = f"[$secondary]{spec.icon}[/]"
        label = f"[$text]{name}[/]"
        mark = "[$accent]│[/]"
        desc = f"[$text]{description}[/]"
    else:
        cursor = " "
        pill = f"[bold $primary]{key}[/]"
        glyph = f"[$secondary]{spec.icon}[/]"
        label = f"[$text]{name}[/]"
        mark = "[$text-muted]│[/]"
        desc = f"[$text-muted]{description}[/]"
    return f"{cursor} {pill} {glyph} {label} {mark} {desc}"


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
    """Grouped command list with a full-width selection pill and cursor."""

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
    CommandDeck > .option-list--option-hover {
        background: $pd-selection 60%;
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
        """A command was chosen with Enter or click."""

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
        prefix = 1 + 1 + 3 + 1 + 1 + 1 + _name_width() + 1 + 1 + 1
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

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        spec = self._specs.get(str(event.option.id or ""))
        if spec is not None:
            self.post_message(self.Selected(self, spec))
