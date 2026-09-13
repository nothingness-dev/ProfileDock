# ProfileDock graphical shell

Independent Level 1 frontend preview. Requires Node.js 22.12+ and npm.
No Python setup is needed. No profile data is read or written.

From the repository root in PowerShell:

```powershell
cd apps\ui
npm.cmd ci
npm.cmd run dev
```

Open the local address Vite prints (normally http://127.0.0.1:5173).
Stop with Ctrl+C. On other shells, use `npm` instead of `npm.cmd`.

```powershell
npm.cmd run check
npm.cmd run build
npm.cmd run preview
```

`check` runs strict TypeScript checking; `build` creates static files in `dist`.
The frontend uses npm and its own `package-lock.json`; Python lockfiles are unrelated.

Search accepts text, has an accessible clear button and supports Ctrl+K or Cmd+K
to focus. Filtering is deferred. Create profile, Backups, Diagnostics and Settings
are visibly unavailable. Navigation keeps Profiles active without adding routes.

Tokens live in `src/tokens.css`; responsive layout lives in `src/styles.css`.
`src/main.tsx` holds the small shell and ephemeral search state. Phosphor supplies
consistent line icons. System fonts avoid a remote font dependency. No backend,
router, persistence, sample profiles, or desktop integration is included.

See [the implementation checkpoint](../../docs/ui-implementation-progress.md)
before continuing work.
