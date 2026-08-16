# ProfileDock

ProfileDock is a lightweight command-line tool for managing isolated, persistent Chromium profiles. Every profile receives a separate browser data directory, so cookies, sessions, local storage, cache, login state, and browsing data do not leak into another ProfileDock profile.

Current release: `0.3.0`

## Features

- Create, list, launch, close, and delete browser profiles.
- Keep browser state between launches with Playwright persistent contexts.
- Open an exact number of blank tabs.
- Prevent the same profile from launching twice.
- Close browsers gracefully through a local controller process.
- Use Playwright Chromium or fall back to an installed Google Chrome.
- Store all ProfileDock data inside the project directory.

## Requirements

- Python 3.9 or newer.
- Windows, macOS, or Linux.
- Internet access during the initial dependency installation.
- Google Chrome or access to Playwright's Chromium download service.

Git is optional for running ProfileDock but required when cloning or updating the repository with Git.

## Automated setup

Open a terminal in the project directory and run:

```bash
python scripts/setup_project.py
```

The setup script:

1. Creates an isolated `.venv` environment if one does not exist.
2. Upgrades pip inside that environment.
3. Installs ProfileDock and its test dependencies from `requirements.txt`.
4. Uses installed Google Chrome or installs Playwright Chromium.
5. Runs the complete test suite.

The script is safe to run again after pulling updates. It reuses the existing virtual environment.

To skip all browser preparation:

```bash
python scripts/setup_project.py --skip-browser
```

Use `--skip-browser` only when a compatible browser is already available or when you only need to run non-browser functionality.

## Manual setup

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

If Playwright's Chromium download is blocked but Google Chrome is installed, omit the final command. ProfileDock automatically falls back to the installed Chrome channel.

### macOS and Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium
```

## Using the virtual environment

Activation is optional. You can run ProfileDock directly through the isolated environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\profiledock.exe --help
```

macOS and Linux:

```bash
./.venv/bin/profiledock --help
```

To activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, either run the executable directly or temporarily allow local scripts for the current terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

To activate on macOS or Linux:

```bash
source .venv/bin/activate
```

After activation, the `profiledock` command is available directly.

## First profile walkthrough

Create a profile:

```bash
profiledock create "Personal"
```

List profiles and copy the generated ID:

```bash
profiledock list
```

Launch the profile and answer the tab-count prompt:

```bash
profiledock launch <id>
```

You can provide the tab count without a prompt:

```bash
profiledock launch <id> --tabs 5
```

Browse and sign in manually. ProfileDock does not automate login or collect passwords.

Close the browser gracefully:

```bash
profiledock close <id>
```

Launch the same ID again to restore its persistent browser state:

```bash
profiledock launch <id>
```

## Commands

Commands that operate on a single profile accept a profile identifier. The identifier can be a full ID, a unique ID prefix, or an exact profile name. Matching is case-sensitive for both IDs and names. If the input matches more than one profile, ProfileDock prints the matching IDs and names and exits without taking action.

### Version

```bash
profiledock --version
```

Displays the current version of ProfileDock.

### Create

```bash
profiledock create "Profile name"
```

Creates profile metadata and a dedicated browser data directory.

### List

```bash
profiledock list
```

Displays each profile's ID, name, and running status.

### Launch

```bash
profiledock launch <id-or-name>
profiledock launch <id-or-name> --tabs 3
```

Starts a persistent browser context with exactly the requested number of `about:blank` tabs. A running profile cannot be launched a second time.

### Close

```bash
profiledock close <id-or-name>
```

Requests graceful browser shutdown and removes the profile's running-state file.

If the entire browser is closed manually or exits unexpectedly, the controller detects the closure, exits, and removes its running state. The profile can then be launched again normally.

### Delete

```bash
profiledock delete <id-or-name>
```

Asks for confirmation, then permanently removes the profile metadata and browser data. A running profile must be closed before deletion.

For non-interactive use:

```bash
profiledock delete <id-or-name> --yes
```

## Data storage and persistence

ProfileDock stores data relative to the directory where commands are run:

```text
profiledock/
├── profiles.json
├── profiles.json.bak
├── profiles.json.lock
└── profiles/
    └── <profile-id>/
        ├── browser-data/
        └── running.json
```

### Metadata document format

`profiles.json` contains a versioned metadata document:

```json
{
  "schema_version": 1,
  "profiles": [
    {
      "id": "abc123",
      "name": "Personal",
      "created_at": "2024-01-15T10:30:00+00:00",
      "data_dir": "/path/to/profiles/abc123/browser-data",
      "last_launched_at": null
    }
  ]
}
```

The metadata document includes:

- `schema_version`: Version of the metadata format (currently 1)
- `profiles`: Array of profile objects with required fields: `id`, `name`, `created_at`, `data_dir`
- Optional field: `last_launched_at` (ISO-8601 timestamp)

### Metadata migration

ProfileDock automatically migrates older bare-array format to the versioned document format:

- The old `profiles.json` is backed up to `profiles.json.bak` before migration
- Migration validates all profile data before accepting the new format
- Invalid data is rejected before any changes are written

### Metadata safety

ProfileDock implements several safety mechanisms to protect metadata integrity:

**Atomic writes**: All metadata writes are atomic. A temporary file is written, synced to disk, then moved into place. This ensures interrupted writes never leave a corrupted file.

**Cross-process locking**: A lock file (`profiles.json.lock`) prevents concurrent processes from modifying metadata simultaneously.

**Backup recovery**: Before each metadata update, the current file is backed up to `profiles.json.bak`. If the primary file becomes corrupted, ProfileDock can recover from the backup.

**Duplicate prevention**: Profile IDs and data directories must be unique. Duplicate values are rejected before any changes are written.

**Path safety**: Data directories must be under the configured profile root. Symlinks and path traversal attempts are rejected.

**Corruption handling**: ProfileDock never overwrites corrupted metadata automatically. If both the primary and backup files are corrupted, manual intervention is required.

`profiles.json` contains profile metadata only. Chromium stores cookies, local storage, cache, sessions, and login state inside `browser-data`.

`running.json` exists only while a profile controller is active. It contains local process and controller connection information. Stale running-state files are cleaned automatically when detected.

Run ProfileDock commands from the same project directory so they use the same `profiles.json` and `profiles` directory.

## Isolation, security, and privacy

- Every profile uses a separate Chromium user data directory.
- ProfileDock does not automate authentication or store passwords itself.
- Websites and Chromium may store credentials, cookies, tokens, and browsing history inside `browser-data`.
- Anyone with access to a profile directory may be able to access its browser state.
- Do not commit `profiles.json`, `profiles`, `.venv`, or `.tmp`; they are excluded by `.gitignore`.
- Closing a profile does not delete its browsing data.
- Deleting a profile permanently removes its local browser data.

## Testing

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

macOS and Linux:

```bash
./.venv/bin/python -m pytest
```

The integration test launches a real persistent browser context, verifies the requested tab count, closes it, relaunches it, and confirms that a persistent cookie remains. It uses Playwright Chromium or installed Google Chrome and skips only when neither is available.

Run only fast unit tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not browser"
```

Run only browser and controller integration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -m browser
```

## Updating the project

If the project was cloned with Git:

```bash
git pull
python scripts/setup_project.py
```

Rerunning setup updates the editable installation and executes the tests. Review the current version in `pyproject.toml`, `profiledock.__version__`, or the release tag.

## Troubleshooting

### Chromium download returns HTTP 403

Playwright's CDN may be unavailable in some locations. Install Google Chrome and rerun setup. The script detects Chrome and avoids the blocked download.

### `profiledock` is not recognized

Activate `.venv`, or run the executable using its full project-local path:

```powershell
.\.venv\Scripts\profiledock.exe --help
```

### Profile is already running

Close it normally:

```bash
profiledock close <id>
```

If the browser or controller stopped unexpectedly, run `profiledock list`. ProfileDock detects dead controller processes and cleans stale running state.

### Profile data directory is missing

The profile metadata exists but its `browser-data` directory was moved or deleted. Restore the directory from a backup or delete and recreate the affected profile.

### `profiles.json` is corrupted

ProfileDock attempts to recover from `profiles.json.bak` automatically. If both files are corrupted, restore from a manual backup. ProfileDock intentionally refuses to overwrite corrupted metadata automatically.

### `profiles.json.lock` exists but no process is running

If ProfileDock was interrupted, a stale lock file may prevent operations. Delete `profiles.json.lock` manually to resume normal operation.

### Controller launch failure

When `profiledock launch` fails, the CLI now shows a specific error message instead of a generic failure. Common causes and fixes:

**Playwright package not installed:**
```
Error: No module named 'playwright'
```
Rerun project setup with `python scripts/setup_project.py`, or install Playwright through the active virtual environment.

**No supported browser found:**
```
Error: Playwright Chromium: <error>\nGoogle Chrome: <error>
```
Either install Playwright Chromium (`playwright install chromium`) or install Google Chrome on your system.

**Browser process failed to launch:**
The error message will include Playwright's diagnostic details. Check that the profile data directory exists and is accessible.

**Controller startup timed out:**
The browser took longer than 30 seconds to become ready. Check system resources and close other applications.

**Controller process exited unexpectedly:**
The controller subprocess crashed. Check for system resource constraints or permission issues.

When a controller fails, ProfileDock preserves a diagnostic file at `profiles/<id>/controller.error`. It contains a stable error category, a bounded diagnostic message, and the browser channel attempted when relevant. Diagnostics are limited to 4 KiB, controller tokens are redacted, and the file is automatically cleaned up on the next successful launch.

## Removing ProfileDock

Profile data is project-local, so decide whether to keep or delete it before removing the project.

### 1. Close running profiles

List profiles and close every entry marked `running`:

```bash
profiledock list
profiledock close <id>
```

### 2. Back up profiles if needed

To preserve login state and browser data, copy these items to a secure location:

```text
profiles.json
profiles.json.bak
profiles/
```

They must be restored together into the same project directory structure.

### 3. Remove Playwright-managed browsers if needed

This removes browsers installed by this Playwright environment. It does not remove system Google Chrome.

Windows:

```powershell
.\.venv\Scripts\python.exe -m playwright uninstall
```

macOS and Linux:

```bash
./.venv/bin/python -m playwright uninstall
```

### 4. Remove only the isolated environment

Deactivate it first if active:

```bash
deactivate
```

Then delete the `.venv` directory. This removes installed Python dependencies but keeps source code and profiles. The setup script can recreate it later.

### 5. Remove the complete project

Close all profiles, leave the project directory in your terminal, and delete the `profiledock` folder using File Explorer, Finder, or your desktop environment's file manager. Moving it to the Recycle Bin or Trash is recommended because it remains recoverable until emptied.

Deleting the complete project also deletes `profiles.json` and every `profiles/<id>/browser-data` directory unless they were backed up first.

## Versioning

ProfileDock follows Semantic Versioning. Stable releases use annotated Git tags such as `v0.3.0`.

## License

ProfileDock is available under the MIT License. See `LICENSE` for the full terms.
