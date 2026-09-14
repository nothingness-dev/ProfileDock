# Graphical UI implementation checkpoint

Current: Level 3 implemented; types/build/model checks passed; browser and visual
QA remain pending. See the final Level 3 entry below for current evidence and resume boundary.

## Historical Level 2 checkpoint — assets prepared; awaiting visual review

Started from clean `d4ee535` on `feat/graphical-dashboard-shell`.
No applicable AGENTS.md found in repository or ancestor directories.
Level 1 is preserved: isolated React/TypeScript/Vite dashboard in `apps/ui`;
its type check/build previously passed, browser verification remained pending.

Level 2 boundaries: four reusable cover/icon pairs, typed appearance registry,
and isolated development-only asset preview. No profile cards, picker screen,
forms, Python/backend changes, profile data, persistence, desktop packaging,
or deployment. No Level 3 work. Subsequent user instruction authorizes committing
and pushing this work on the separate `feat/appearance-assets` branch.

Approved direction: midnight navy, restrained purple ambience, metallic 3D
identities: Ember coral/copper knot and ribbons; Prism cobalt brackets/chrome
center and angular blue glass; Orbit violet ring/sphere and planet/orbits;
Vertex translucent lime/dark cubes and matching glass geometry.

Supplied references (UI screenshots, not standalone assets), both inspected:
- `E:/Downloads/ChatGPT Image Sep 13, 2026, 08_35_54 PM.png` — dashboard.
- `E:/Downloads/ChatGPT Image Sep 13, 2026, 08_35_36 PM.png` — create dialog.
These filenames supersede the unknown filename in the prior Level 1 record.

## Checklist and files

- [x] Inspect checkpoint, working tree, existing UI and available image files.
- [x] Generate and directly inspect four independent local cover/icon pairs.
- [x] Verify actual dimensions, alpha and all eight local references.
- [x] Add stable typed IDs, labels, cover/icon URLs and identity accent colors.
- [x] Add isolated preview with 4:1 cover crops and 96px/64px icon swatches.
- [x] Type check, production build and isolated in-memory preview build.
- [ ] Browser review at desktop/narrow widths and screenshot capture: unavailable.
- [ ] User visual acceptance of regenerated artwork and small-size rendering.

Added: `apps/ui/src/appearances.ts`, eight PNGs and `asset-inventory.md` under
`apps/ui/src/assets/appearances/`, `apps/ui/asset-preview.html`, and
`apps/ui/src/dev/asset-preview.{tsx,css}`.
Updated: `apps/ui/README.md` and this checkpoint. Level 2 is recorded on
`feat/appearance-assets`, based on the Level 1 commit `d4ee535`.
Main dashboard, tokens, dependencies, npm lockfile and Python remain unchanged.

Architecture: IDs `ember`, `prism`, `orbit`, `vertex` are appearance presets,
independent of profile names or Python models. Explicit Vite local imports;
no remote URLs, fake default profiles or generation pipeline. Preview is a
separate HTML entry with a DEV guard; production builds only `index.html`.
The main entry does not import the registry or preview.

## Run/check (PowerShell)

Requires Node 22.12+ and npm. From repository root:

```powershell
cd apps\ui
npm.cmd ci
npm.cmd run dev
# Open http://127.0.0.1:5173/asset-preview.html (use Vite's printed port).
# Main dashboard remains at /. Ctrl+C stops the server.
npm.cmd run check
npm.cmd run build
```

Other shells: use `cd apps/ui` and `npm` instead of `npm.cmd`.
`npm.cmd run preview` previews production, which intentionally excludes the asset page.

## Actual validation and limitations (2026-09-14)

Node 24.18.0 / npm 11.16.0; reused existing node_modules, no install needed.
Strict TypeScript and Vite production build passed. A separate Vite API build
with `asset-preview.html`, DEV=true and write=false passed and resolved all eight
PNGs in memory. Checked every registry file reference; all exist. Production
output contains only dashboard HTML/JS/CSS. `git diff --check` passed.
No Python tests or browser installation; no backend/browser launches.

All images directly inspected. Covers: 2172 x 724, fully opaque; icons:
1254 x 1254, actual alpha transparency. Full pixel scan and review details
(including faint generated alpha edge pixels) are in the asset inventory.
Images are newly generated recreations, not exact original exports.

Browser selection returned `No browser is available`; discovery returned `[]`.
No browser screenshots, preview viewport checks or small-size/crop approval.
Level 1 browser QA also remains pending. No requested asset files are missing;
original standalone source artwork remains unavailable. Remaining user input:
accept the generated pairs or describe corrections after reviewing the preview.

## Resume here

Read this checkpoint, inspect `git status`, and open the development asset preview.
Review all four pairs at card/picker sizes on navy and light backgrounds, including
alpha fringes and the centered cover crop. Address only Level 2 visual feedback.
Do not wire presets into profiles, build screens or begin Level 3 without a new
explicit request. STOP at this review boundary.

## Level 3 started — 2026-09-14

The new Level 3 request supersedes the Level 2 stop boundary above; prior
entries remain historical. Working branch: `feat/ui-profile-dashboard`.
Verified base: `6f01c20` (Level 2 feature tip); all eight nonempty PNGs and
`appearances.ts` exist. `12eafd2` on main has identical UI/checkpoint contents.
No applicable AGENTS.md exists in the repository or ancestors. Worktree was clean.
Tracker read from `E:/Downloads/profiledock-design-and-implementation-tracker.md`.
Both attached images exist and were opened; image-2.png is the full dashboard,
image-1.png is the future create dialog. No required assets are missing.

Implement only labeled in-memory dashboard fixtures, cards, search, empty
collection preview, running dock and confirmed close-all. Preserve shell,
assets/dependencies and backend. No real profiles, persistence or future screens.

First coherent change: typed preview model and focused behavior checks.
Actual validation: strict TypeScript and two Node behavior tests passed.
Browser selection reported `Browser is not available: iab`; troubleshooting
followed and discovery returned `[]`. Visual and browser interaction QA pending.
Resume here: implement reusable card, dashboard composition and session dock;
then update design decisions/prompt history and final validation evidence.

## Level 3 implementation finished — 2026-09-14

This is the current implementation status; Level 2 entries above are preserved
as history. Level 3 code is complete; visual/browser verification is pending.
No Level 4 work has started.

Branch: `feat/ui-profile-dashboard`; verified prerequisite base `6f01c20`.
Commits completed in coherent increments:
- `97cadd5` — feat(ui): add isolated dashboard preview model
- `bd522ef` — feat(ui): render searchable preview profile cards
- `c910a2e` — feat(ui): add preview session dock and close-all confirmation
- Final documentation commit: `docs(ui): record Level 3 implementation and validation`
  (its resulting hash is available from `git log -1` and the final delivery report;
  a commit cannot contain its own hash).

### Implemented

- Typed frontend-only fixtures with distinct identity/name/appearance/engine/
  browser/status/last-opened/tab-count fields; existing appearance registry reused.
- Reusable cover/status/menu/identity/metadata/action cards; responsive two-column
  grid and single-column narrow layout. Focus highlight represents actual focus.
- Name search, trimmed/case-insensitive matching, visible/total/running counts,
  clear-search, no-results and Ctrl/Cmd+K. Shortcut respects an open modal.
- Explicit UI preview notice; genuine empty-array preview toggle separate from
  search misses and any backend state. No connection or saved-data claims.
- Launch/Close change React memory only. Dock derives from the same full
  collection even when search hides cards. Close-all requires a native accessible
  dialog; Cancel initial focus, Escape dismissal, return focus to trigger/dock.
- Disabled Create/settings/tabs with accessible explanations, simple card
  disclosures with Escape/outside dismissal. No fake successes or other screens.
- Preserved reduced motion, existing system fonts/Phosphor icons and all assets.
  No Python/backend/storage/network integration, dependencies or lockfile changes.
- README, design decisions, exact prompt history and visual-QA record maintained.

### Actual validation

Node v24.18.0 / npm 11.16.0; existing node_modules reused, no installation.
From `E:/GitHub/ProfileDock/apps/ui`:

```powershell
npm.cmd run check
node --experimental-strip-types --test tests/preview-model.test.mjs
npm.cmd run build
npm.cmd run dev -- --port 5173 --strictPort
```

Strict TypeScript passed. Two focused Node tests passed: trimmed/case-insensitive
filtering, no matches, empty/blank queries, invariant collection/running counts;
immutable launch/close/close-all, cleared stale tab counts, empty close-all and
fixture recreation/reset. These are model tests, not browser interaction tests.
Production build passed (47 modules); all eight PNG imports resolved and are now
included in dashboard output. Separate asset-preview HTML remains excluded.
Vite started on http://127.0.0.1:5173/; GET / and /src/main.tsx returned HTTP 200.
Source review applied the React checklist (derived counts, lazy state initializer,
functional immutable updates, event cleanup, direct icon imports).
`git diff --check` passed. Diff is confined to frontend/continuity documentation.
No Python suite, browser installation, push, merge, rebase or deployment performed.

### Pending / limitations

Browser setup failed (`Browser is not available: iab`); troubleshooting followed,
discovery `[]`. No rendered screenshot, console check, desktop/narrow comparison,
actual keyboard/assistive-technology check, 200% zoom or crop/alpha acceptance.
`apps/ui/design-qa.md` remains explicitly blocked for visual QA, not passed.
Both supplied screenshots exist; no user image upload is needed. Generated Level 2
artwork differs from exact source exports and is ~11.5 MB total; unchanged here.
Original standalone brand mark remains unavailable; existing text wordmark retained.
Tab counts are illustrative and disappear after a simulated status change.
Closing all does not close real browsers; empty preview does not read real data.
No real persistence, backend, Create/profile settings/tabs or desktop integration.

### Resume here — STOP at Level 3

Review the running local preview or restart with the command above. When browser
access is available, perform the explicit checklist in `apps/ui/design-qa.md`.
Any requested visual fixes remain Level 3 work. Next proposed level is Create
profile UI, but do not execute it without a new explicit request.
