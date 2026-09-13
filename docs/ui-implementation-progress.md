# Graphical UI implementation checkpoint

## Current level: 1 — shell implemented; awaiting review

Branch: `feat/graphical-dashboard-shell`; baseline: clean `d508029`.
Only an isolated React/TypeScript/Vite frontend in `apps/ui`. No Python,
CLI/TUI/MCP, storage, browser behavior, data access, cards, dialogs, or deployment changes.

Visual direction: compact horizontal navigation, midnight navy with subtle purple
ambience, white headings, lavender secondary text, purple-to-blue “every you.”,
fine translucent borders and a purple outlined dashboard Create profile button.
Reference: user-supplied dashboard screenshot with a create-profile dialog over it
(2432 × 1598 original). **Supplied filename: not exposed by the attachment interface**;
no source image file is present in the repository. The dialog is out of scope.

- [x] Inspect working tree, instructions, README, pyproject and Python architecture.
- [x] Create a separate branch before edits.
- [x] Implement tokens, navigation, heading, disabled Create profile, search, preview placeholder.
- [x] Run focused frontend type check and production build.
- [ ] Inspect desktop/narrow layouts and capture screenshots if browser tooling is available.
- [x] Record final files, commands, limitations and resume instructions.

## Implementation and boundaries

`apps/ui` is independent of Python. React owns only ephemeral search text;
typing, clearing with focus restoration, and Ctrl/Cmd+K are implemented.
Navigation exposes the current Profiles section and visibly disabled destinations.
Create profile is disabled with a visible, associated explanation. The placeholder
explicitly describes a preview, not an empty real collection. English/LTR,
responsive wrapping, keyboard focus, skip link, reduced-motion and forced-color
rules are included, but not browser-verified.

Dependencies: React/React DOM, Phosphor line icons; development uses TypeScript,
React type declarations and Vite. Exact versions and one npm lockfile; no router,
backend adapter, UI framework, remote fonts, data reads, or Python modifications.
Icons use individual imports to avoid processing the full icon catalog.

Files added: `apps/ui/{.gitignore,README.md,design-qa.md,index.html,package.json,
package-lock.json,tsconfig.json}`, `apps/ui/src/{main.tsx,tokens.css,styles.css,
vite-env.d.ts}`, and this checkpoint. No existing tracked files changed.

## Run and validate (PowerShell)

Requires Node.js 22.12+ with npm. From the repository root:

```powershell
cd apps\ui
npm.cmd ci
npm.cmd run dev
# Open the address Vite prints, normally http://127.0.0.1:5173; Ctrl+C stops it.
npm.cmd run check
npm.cmd run build
# Optional local production preview:
npm.cmd run preview
```

Other shells: use `cd apps/ui` and `npm` instead of `npm.cmd`.

Validation performed on Node 24.18.0 / npm 11.16.0: dependency installation,
strict TypeScript check and Vite production build passed. Initial type check
identified missing Vite CSS import declarations; fixed via `vite-env.d.ts`.
Existing Python paths and contracts were checked for changes and remained intact.
No Python tests, browser binaries, backend processes, push or deployment.

Browser blocker: in-app browser selection returned unavailable; runtime browser
discovery returned `[]`. No visual inspection, screenshots, keyboard interaction
tests, or viewport checks performed. See `apps/ui/design-qa.md` (blocked).
Review at 1440px, 390px and 320px, plus 200% zoom, before visual approval.

Missing visual assets: original reference file/filename and standalone brand mark
(wordmark is text only). Four matching cover/icon pairs remain entirely deferred.

Next level (not started): prepare four matching cover/icon asset pairs
(Ember, Prism, Orbit, Vertex). No generated assets or profile substitutes in Level 1.

Resume here: inspect this checkpoint and `git status` on
`feat/graphical-dashboard-shell`. Run the frontend and complete the pending visual
review when a browser is available. Apply only Level 1 feedback until approved;
then prepare the four asset pairs as the next separately reviewed level. STOP here.
