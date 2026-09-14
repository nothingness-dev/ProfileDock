# Dashboard shell visual QA

final result: blocked

Source: user-attached 2432 × 1598 screenshot, with the dashboard partially
obscured by a dialog. Original filename/local source file was not exposed.
Comparison scope is the upper dashboard only; the dialog and cards are excluded.

Browser runtime setup succeeded, but `get("iab")` returned “Browser is not
available: iab”; discovery returned no browsers. No browser binaries installed.
No rendered desktop/narrow screenshots or interaction verification performed.
Typography, layout, colors, image quality and copy fidelity therefore remain
visually unverified. Type checking/build results are recorded in the checkpoint
and do not constitute visual QA.

Pending manual review: desktop (1440px) and narrow (390px/320px), 200% zoom,
tab focus, Ctrl/Cmd+K, typing/clearing, reduced motion, unavailable controls.
The wordmark is text only pending the original brand mark; no substitute artwork.

## Level 3 QA — 2026-09-14 (current)

final result: blocked

The Level 1 record above is historical. Current source visual truth:
`C:/Users/omidi/.codex/attachments/c2c99d68-fb06-4b54-90fe-1779c70afb40/image-2.png`,
1536 × 1024 pixels, full dashboard. Both attachments were opened directly.
Implementation: http://127.0.0.1:5173/, illustrative 4 profiles / 2 running.
Implementation screenshot path: unavailable. Target review viewports: 1536 × 1024,
390 × 844 and 320px wide; CSS size/density normalization not measured.

Browser setup returned `Browser is not available: iab`. Read troubleshooting;
read-only discovery returned `[]`. No browser binaries installed and no alternate
browser automation used. Full-view and focused-region comparisons could not be
performed. There is no screenshot or visual QA pass to report.

Required fidelity surfaces remain visually unverified:
- Typography: existing system font retained; title, names and metadata styled.
- Spacing/layout: two columns, 4.2:1 covers, compact dock; one column <=760px.
- Colors/tokens: existing navy/lavender, pale Launch, outlined Close, green status.
- Image quality: existing local artwork reused; crop/alpha/small-size review pending.
- Copy: dashboard names and labels implemented; explicit preview/unavailable copy
  and nothing-saved footer intentionally differ from the illustrative reference.

Source review confirms focus-visible styles, focus-within card outline, reduced
motion rule, nonnested controls, native modal and focus restoration code. This is
not browser proof. Keyboard, screen-reader, focus restoration, Escape/Cancel,
200% zoom, long-text layout and browser console checks remain pending.

Automated validation: TypeScript, Vite production build, and two focused model
checks pass. Dev server start/HTTP responses only prove local serving, not render.

Next review: compare default full view against source, inspect card crops/icons,
then test search/clear/Ctrl+K and Cmd+K; hidden-running dock consistency; Launch,
Close; Close all Cancel/Escape/confirm and focus; empty collection toggle; menu
keyboard dismissal; reload reset; narrow/zoom/long-name behavior. Fix findings
within Level 3 only. Do not begin Create profile UI without a new user request.
