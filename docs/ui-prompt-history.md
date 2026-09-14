# UI prompt history

## Prior levels (historical record)

- Level 1: isolated React/TypeScript/Vite shell; commit `d4ee535`.
- Level 2: four cover/icon pairs, typed registry and isolated development asset
  preview; verified feature tip `6f01c20`. Browser/user visual acceptance pending.
- Main integration `12eafd2` has identical UI/checkpoint contents to Level 2.

## Level 3 execution — 2026-09-14

User requested ONLY the exact prompt below, verified Level 2 prerequisite assets,
small Conventional Commits on `feat/ui-profile-dashboard` based on Level 2,
continuity docs, no push/merge/rebase/deployment, and a stop after this level.
Source: `E:/Downloads/profiledock-design-and-implementation-tracker.md`.
Full visual authority: attachment `image-2.png`; `image-1.png` is future context.
Both supplied images were available; no reattachment required.

Implementation and actual validation are recorded in `ui-implementation-progress.md`.
No next-level prompt has been executed. The following is preserved verbatim from
the source tracker, not rewritten as a narrower task:

## Level 3 — exact ready-to-paste prompt

```text
Implement ProfileDock Level 3 ONLY: the profile dashboard cards and local preview interactions.

Read applicable AGENTS.md, docs/ui-implementation-progress.md, git status/log, and the existing apps/ui implementation first. Level 1 was reported at d4ee535; the user reports Level 2 is done, but verify the appearance registry, all four cover/icon pairs and current branch before assuming completion. Reuse existing structure and dependencies. Do not restart scaffolding.

Use the attached FULL dashboard image as the visual authority. Other screenshots are future references, not scope for this level. Use an applicable UI skill if available.

Prerequisite gate: if required Level 2 assets or registry are missing, document exactly what is absent and finish only safe prerequisite work already defined in Level 2. Do not replace missing artwork with emoji, generic icons, remote stock URLs or entire screenshots. Do not claim Level 3 complete with missing assets.

GIT WORKFLOW
- Work on feat/ui-profile-dashboard, creating it from the verified current Level 2 tip if needed. If it already exists, inspect and resume safely. Preserve prerequisite commits and unrelated changes; never reset, force checkout or silently stash user work.
- Commit each coherent completed change separately as you progress, with descriptive Conventional Commit messages. Aim for several useful commits, not one final dump or artificial commit-count inflation.
- Natural boundaries: preview model/data; reusable profile card; grid/search; session dock/actions; focused fixes; checkpoint/docs. Adapt to actual changes; no empty commits or intentionally broken commits.
- Stage only your own files. Do not push, merge, rebase or deploy. Record branch, base and commit hashes.

SCOPE
1. Reuse Level 2 appearance IDs/assets in a small typed frontend preview model. Keep profile ID, display name, appearance ID, engine, status, last-opened value and optional tab count distinct. No Python metadata changes.
2. Build a reusable card matching the dashboard: wide cover, small status at upper left, menu upper right, sculptural identity icon, profile name, engine/browser, supporting metadata and Launch or Close action. Display Closed for the closed UI state. Keep names/URLs from overflowing.
3. Compose the responsive two-column desktop grid, collapsing to one column when needed. Use illustrative Personal/Ember, Development/Prism, Research/Orbit and Sandbox/Vertex fixtures in an explicitly labeled UI preview. Never seed real profiles or present fixtures as live data.
4. Wire the existing search to these fixtures, with accurate visible/total counts, clear-search, no-results state, and the existing Ctrl/Cmd+K behavior. Include an isolated preview of a genuinely empty collection without interpreting backend disconnection as empty data.
5. Build the compact running-profile dock from the same in-memory state. In this clearly labeled preview ONLY, Launch/Close may change in-memory status. Close all must show a small accessible confirmation and then update preview state. Never launch/kill real browsers, access real profiles or persist simulated state.
6. Future actions (Create profile, settings, tabs) remain visibly unavailable with a short accessible explanation. Do not add fake success toasts or implement their screens. Card menus may expose these disabled destinations.

VISUAL RULES
Preserve the approved compact topbar, navy background, subtle violet ambience, white/lavender typography and supplied 3D artwork. No sidebar, stats widgets, emoji or exaggerated glow. Pale lavender Launch, quiet outlined Close; never green action buttons. Green is allowed for small running indicators. Selection/focus highlights must represent a real UI state, not permanently decorate the Research fixture.

Latest global icon decision: settings section icons will be 32–40 CSS px, header about 48px, utilities 16–20px with restrained depth and glow. This does not require shrinking profile identity artwork to utility size. Settings and theme switching are out of scope now.

ACCESSIBILITY AND VALIDATION
Avoid nested interactive buttons in cards. Provide keyboard focus, accessible names and dialog focus handling for Close all, plus reduced-motion behavior. Check only relevant frontend types/build and focused behavior checks for filtering/status counts if appropriate. Use a browser preview if available at desktop and narrow widths; record what was actually inspected. Do not run the unrelated Python suite or install browser binaries just for this task. Never claim visual verification without it.

CONTINUITY
Update docs/ui-implementation-progress.md and maintain concise docs/ui-design-decisions.md and docs/ui-prompt-history.md. Keep designed, implemented, verified and pending states separate. Record this Level 3 scope, work completed, actual checks, missing assets, branch/commits, exact run commands and a short Resume here section. Do not overwrite earlier history. Save the checkpoint before ending if interrupted.

Finish with a concise report and a screenshot if available. Next proposed level is Create profile UI, but STOP after Level 3 and wait for feedback. No backend, real data, other pages, cloud features, desktop packaging, push or deployment.
```
