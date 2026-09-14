# ProfileDock dashboard preview

Isolated React/TypeScript/Vite frontend through Level 3. Requires Node.js
22.12+ and npm; no Python setup. It never reads or writes real profiles.

From the repository root in PowerShell:

```powershell
cd apps\ui
npm.cmd ci
npm.cmd run dev -- --port 5173 --strictPort
```

Open http://127.0.0.1:5173/. Stop with Ctrl+C. Other shells use `npm`.

```powershell
npm.cmd run check
node --experimental-strip-types --test tests/preview-model.test.mjs
npm.cmd run build
npm.cmd run preview
```

The dashboard is visibly labeled **UI preview**. Personal, Development,
Research and Sandbox are illustrative fixtures, never default user profiles.
Launch/Close change only React memory. Reload restores the fixtures. Close all
requires confirmation; Cancel/Escape leave state unchanged. The running dock
and total running count include profiles hidden by search.

Search filters names (case-insensitive, trimmed), supports Ctrl/Cmd+K and clear,
and distinguishes no matches from the explicit Empty collection preview toggle.
The toggle temporarily shows a genuinely empty array and preserves the examples'
state when switched off. It is not a backend connection indicator.

Create profile, settings, tabs, Backups and Diagnostics are unavailable. Card
menus explain future destinations. Example tab counts disappear after status
changes, since the preview cannot inspect or open real tabs.

Tokens: `src/tokens.css`; shell: `src/main.tsx` / `src/styles.css`; Level 3 layout:
`src/dashboard.css`; reusable components: `ProfileCard.tsx`, `SessionDock.tsx`;
immutable fixture model: `preview-model.ts`. Existing Phosphor icons and system
fonts are reused. No new dependencies, router, persistence or backend integration.

The development-only http://127.0.0.1:5173/asset-preview.html remains available
for Level 2 cover crops and icon swatches. `src/appearances.ts` imports eight
original generated PNGs; see the asset inventory for provenance. The production
build now includes those images because the dashboard uses them, but still
excludes the separate asset-preview HTML entry.

Strict types, build and model checks passed. Browser discovery returned no
available surfaces; visual fidelity, narrow layouts, actual focus/keyboard
behavior and console checks remain unverified. See `design-qa.md` and the
[checkpoint](../../docs/ui-implementation-progress.md) for evidence and limits.
