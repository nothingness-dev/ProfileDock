# Operations and troubleshooting

## Routine status checks

```bash
profiledock list
profiledock status
profiledock show Work
profiledock doctor
```

Use `--json` on supported commands for automation. Human output may improve without notice; JSON follows the versioned contract.

## Doctor

`profiledock doctor` checks Python compatibility, data-root writability, directory permissions, metadata and backup validity, profile paths, browser availability, runtime permissions and state, orphan directories, and package-version consistency.

```bash
profiledock doctor
profiledock doctor --json
profiledock doctor --repair
```

Explicit directory repairs:

```bash
profiledock doctor --repair --reattach-orphans
profiledock doctor --repair --recreate-missing
profiledock doctor --repair --reattach-orphans --yes
```

Doctor removes only verifiably stale runtime state automatically. Malformed or ambiguous state is retained for review. Destructive repairs require stopped profiles and confirmation where appropriate.

## Logs

```bash
profiledock logs
profiledock logs Work
profiledock logs Work --last 20
profiledock logs --json
```

Logs are stored below the configured data root, not in profile browser data. Entries contain timestamps, levels, events, correlation IDs, optional profile IDs and engines, and bounded details.

## Updating ProfileDock

Activate the virtual environment, update the repository by your normal version-control workflow, and reinstall dependencies and the editable package:

```bash
pip install -r requirements.txt
profiledock --version
profiledock doctor
```

Development environments should also install `requirements-dev.lock`. When Playwright requirements change, reinstall its extra and browser:

```bash
pip install -e ".[playwright]"
playwright install chromium
```

ProfileDock migrates supported persistent formats automatically with backup and rollback guarantees. Back up important profiles before upgrading across releases.

## Troubleshooting

### `profiledock` is not recognized

Activate `.venv`, reinstall with `pip install -e .`, or invoke the executable directly from `.venv/bin` or `.venv\Scripts`.

### Browser executable not found

Install Chrome, Chromium, or Brave for Direct mode. For Playwright mode, install the optional dependency and Chromium:

```bash
pip install -e ".[playwright]"
playwright install chromium
```

Use `profiledock doctor` to see detected browser availability.

### Playwright browser download fails with HTTP 403

The network or package mirror may block Playwright downloads. Use Direct mode with an installed browser, configure an allowed Playwright download source according to your environment, or install from a network that permits the official browser package.

### Profile is already running

```bash
profiledock status Work
profiledock close Work
```

If the browser was terminated externally, run `profiledock doctor`. Do not delete ambiguous `running.json` manually until you confirm no related process or controller is active.

### Profile data directory is missing

Run `profiledock doctor`. If the directory was deleted and no backup exists, `doctor --repair --recreate-missing` can create an empty directory, but it cannot recover the previous browser session.

### Metadata is corrupted

Run `profiledock doctor --repair`. ProfileDock can recover a valid metadata backup. It will not overwrite evidence when both primary and backup are invalid.

### Metadata permission denied (files created elevated)

If `doctor` reports "this account cannot read it" with an `icacls` hint, the metadata files were created from an administrator terminal and their Windows ACLs exclude your normal account. Fix once from an ADMINISTRATOR PowerShell:

```powershell
icacls "$env:LOCALAPPDATA\ProfileDock" /reset /T /C
```

Then keep using ProfileDock from non-elevated terminals.

### A lock file exists

`metadata/profiles.lock` is a coordination file, not proof of an active lock. ProfileDock uses an operating-system lock on the file and can reuse it safely.

### Controller launch failure

Inspect `profiledock status`, `profiledock doctor`, and `profiledock logs Work`. Verify the browser installation and runtime-directory permissions. Controller error messages are bounded and secrets are redacted.

### Backup reports locked browser files

Close the profile and ensure background browser processes have exited. Chrome background mode or another application may retain SQLite database locks.

## Removing ProfileDock

Removal has separate code, environment, browser, and profile-data parts. Back up before deleting anything important.

### 1. Close profiles

```bash
profiledock status
profiledock close Work
```

Repeat close for every running profile.

### 2. Back up data if needed

```bash
profiledock backup --all --output profiledock-final-backup.tar.gz
```

Verify and move the archive before deleting the data root.

### 3. Remove the virtual environment

Deactivate it, then remove only the repository's `.venv` directory using your file manager or an explicit platform command. This removes installed Python packages but not application data.

### 4. Remove Playwright browsers if desired

Playwright browser binaries are shared through its cache and may be used by other projects. Use Playwright's documented uninstall command only if you intend to remove those shared browser packages.

### 5. Remove application data

Delete the resolved data root only after verifying its exact path:

- Windows: `%LOCALAPPDATA%\ProfileDock`
- macOS: `~/Library/Application Support/ProfileDock`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/profiledock`
- Custom: the directory selected by `PROFILEDOCK_DATA_ROOT` or `--data-root`

Deleting the data root permanently removes profiles, cookies, sessions, history, cache, metadata, runtime records, logs, and local metadata backups. ProfileDock intentionally does not provide a command that deletes the entire data root.

### 6. Remove source code

After the virtual environment and any required data are handled, remove the repository directory through your file manager or version-control workspace tooling.
