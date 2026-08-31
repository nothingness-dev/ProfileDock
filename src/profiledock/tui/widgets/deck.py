"""Command deck: the categorized, filterable command list in the left pane."""

from __future__ import annotations

from typing import Any, ClassVar

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..actions import ACTIONS_BY_ID, GROUP_TITLES, ActionSpec, fuzzy_score, grouped_actions

CURSOR_GLYPH = ">"


class VimOptionList(OptionList):
    """OptionList with vim-style navigation.

    ``double_click_selects`` controls mouse behavior: when False (default)
    a single click selects like keyboard Enter; when True the first click
    only highlights (letting the preview pane follow) and a double click
    selects. Keyboard Enter always selects.
    """

    BINDINGS: ClassVar[list[Any]] = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    double_click_selects = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Chain count of the click currently being dispatched. Textual runs
        # every _on_click in the MRO, so the stock OptionList handler fires
        # alongside ours and unconditionally calls action_select(); the gate
        # below lets a single click highlight-only in double-click mode.
        self._click_chain = 0

    def scroll_visible(self, *args: Any, **kwargs: Any) -> None:
        return

    def reset_armed(self) -> None:
        """Kept for mode-switch call sites; nothing to reset without arming."""
        return

    async def _on_click(self, event: events.Click) -> None:
        """Record the click chain; selection gating happens in action_select."""
        clicked_option: int | None = event.style.meta.get("option")
        if clicked_option is None or self._options[clicked_option].disabled:
            return
        self._click_chain = event.chain
        if not self.double_click_selects and self.highlighted != clicked_option:
            self.highlighted = clicked_option

    def action_select(self) -> None:
        """Select, but in double-click mode swallow chain-1 clicks."""
        if self.double_click_selects and self._click_chain == 1:
            self._click_chain = 0
            return
        self._click_chain = 0
        super().action_select()

    def _highlighted_option_id(self) -> str | None:
        index = self.highlighted
        if index is None:
            return None
        option = self.get_option_at_index(index)
        return str(option.id) if option.id else None


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
    """Rank actions against a query spanning label, description, hotkey, and id.

    Label matches outrank everything: typing ``la`` should surface ``launch``
    before commands that merely contain those letters elsewhere. Subsequence
    fuzzy matching keeps typos like ``lunch`` useful.
    """
    if not query:
        return actions
    scored: list[tuple[int, int, str]] = []
    for action in actions:
        label_score = fuzzy_score(query, action.label)
        if label_score is not None:
            # Label matches rank above all others; higher fuzzy score wins.
            key = (0, -(100000 + label_score), action.label)
        else:
            haystack = f"{action.label} {action.description} {action.id} {action.hotkey}"
            anywhere = fuzzy_score(query, haystack)
            if anywhere is None:
                continue
            key = (1, -anywhere, action.label)
        scored.append(key)
    results: list[ActionSpec] = []
    for key in sorted(scored):
        resolved = ACTIONS_BY_ID.get(key[2])
        if resolved is not None:
            results.append(resolved)
    return results


class CommandDeck(VimOptionList):
    """Grouped, filterable command list with a plain selection cursor.

    Single click previews a command (the inspector follows the highlight);
    double click runs it. Keyboard Enter always runs.
    """

    double_click_selects = True

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
        self.match_count = 0

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
        total_matches = 0
        for group_id, actions in grouped_actions():
            matches = _filtered(actions, query)
            if not matches:
                continue
            total_matches += len(matches)
            options.append(_group_header(group_id))
            for spec in matches:
                self._specs[spec.id] = spec
                options.append(Option(_command_prompt(spec, width, desc_width, False), id=spec.id))
        if not options:
            options.append(
                Option(
                    f"  [$text-muted]no commands match {query!r} — esc to clear[/]",
                    disabled=True,
                )
            )
        self.clear_options()
        self.add_options(options)
        self._highlighted_id = None
        self.match_count = total_matches
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
        identifier = str(event.option.id) if event.option.id else None
        if identifier is not None:
            spec = self._specs.get(identifier)
            if spec is not None:
                self.post_message(self.Selected(self, spec))
