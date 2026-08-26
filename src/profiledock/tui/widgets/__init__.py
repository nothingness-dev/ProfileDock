"""Widget toolkit for the ProfileDock TUI."""

from __future__ import annotations

from .bars import FooterBar, HeaderBar, breadcrumb
from .deck import CommandDeck
from .forms import ChoiceList, FieldRow, FlagsList, FormPanel, ProfilePicker
from .inspector import CommandPreview, OutputPane, ProfileRail, TelemetryCards
from .overlays import ConfirmModal

__all__ = [
    "ChoiceList",
    "CommandDeck",
    "CommandPreview",
    "ConfirmModal",
    "FieldRow",
    "FlagsList",
    "FooterBar",
    "FormPanel",
    "HeaderBar",
    "OutputPane",
    "ProfilePicker",
    "ProfileRail",
    "TelemetryCards",
    "breadcrumb",
]
