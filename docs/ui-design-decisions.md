# UI design decisions

## Level 1–2 history

The existing shell uses English/LTR, midnight navy, subtle violet ambience,
white/lavender type, a compact horizontal topbar and system fonts with Phosphor
utilities. Level 2 supplies typed Ember/Prism/Orbit/Vertex appearance presets.
The artwork is regenerated, not exact source exports; visual acceptance remains
pending. Appearances are independent of profile names and are not user profiles.

## Level 3 — implemented 2026-09-14

Authority: exact Level 3 prompt in the supplied tracker, reproduced in
`ui-prompt-history.md`; full dashboard attachment `image-2.png` (1536 × 1024).
`image-1.png` is the create dialog and does not authorize implementing that screen.

- Reuse the eight Level 2 files unchanged. Centered 4.2:1 desktop cover crop,
  sculptural identity images at 112 × 94 CSS px, compact utility controls.
- Two equal desktop columns; one column at 760px and below. Smaller widths
  wrap the action below the metadata. Names use overflow wrapping.
- Preserve navy/lavender tokens, pale lavender Launch and outlined Close.
  Green marks running status only. Cards highlight only when focus is inside;
  Research has no permanent selected decoration.
- Preview notice, unavailable-action explanations and empty-collection toggle
  intentionally add content absent from the screenshot. Footer says nothing is
  saved; screenshot's saved-device claim would misrepresent this preview.
- Keep the existing text wordmark and omit desktop window controls. The original
  brand mark is unavailable as a standalone asset; no substitute is drawn.
- Profile IDs, names, appearance IDs, engines, browser, status, last-opened text
  and optional tab counts are independent typed fields. Initial tab counts are
  illustrative; changing status clears them instead of fabricating new counts.
- Search changes visible cards only. Dock/counts derive from the whole selected
  collection. Empty preview uses an actual empty array; leaving it restores
  example state. Reload resets everything. Nothing persists.
- Close all uses a native modal dialog with Cancel initial focus, Escape dismissal
  and focus restoration to its trigger or the dock when the trigger is disabled.
  Ctrl/Cmd+K does not steal modal focus. Menus are simple disclosures containing
  disabled future actions; no fake screens or success notifications.

## Verified versus pending

Types, build, model behavior checks and source review completed. Browser setup
reported unavailable and discovery returned `[]`; rendering, contrast, crop,
focus trapping/restoration, zoom and responsive behavior need visual/manual QA.
These are implemented design decisions, not claims of browser verification.

Future settings icon sizes (32–40px sections, ~48px header, 16–20px utilities)
and Light/Dark/System with Violet/Cobalt/Teal/Rose are designed only. Create UI,
settings, tabs and all subsequent levels are pending and outside this change.
