# ProfileDock

ProfileDock is a lightweight command-line tool for managing isolated, persistent Chromium profiles. Every profile receives a separate browser data directory, so cookies, sessions, local storage, cache, login state, and browsing data do not leak into another ProfileDock profile.

Current release: `0.12.0`

## Features

- Create, list, launch, close, and delete browser profiles.
- Keep browser state between launches with isolated browser data directories.
- Choose direct system-browser launches or Playwright persistent contexts.
- Open an exact number of blank tabs with the Playwright engine.
- Prevent the same profile from launching twice.
- Close browsers gracefully through a local controller process.
- Use an installed Chrome, Chromium, or Brave browser directly, or use Playwright Chromium.
- Store ProfileDock data in the operating system's application-data directory.
- Override storage with `--data-root` or `PROFILEDOCK_DATA_ROOT`.

## Requirements

- Python 3.9 or newer.
- Windows, macOS, or Linux.
- Internet access during the initial dependency installation.
- Google Chrome, Chromium, Brave, or access to Playwright's Chromium download service.

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
4. Uses an installed Chrome, Chromium, or Brave browser, or installs Playwright Chromium.
5. Runs the complete test suite.

The script is safe to run again after pulling updates. It reuses the existing virtual environment.

Setup does not create or remove ProfileDock application data. The data root is resolved when a `profiledock` command runs.

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

If Playwright's Chromium download is blocked but Chrome, Chromium, or Brave is installed, omit the final command.

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

By default, ProfileDock uses `%LOCALAPPDATA%\ProfileDock` on Windows, `~/Library/Application Support/ProfileDock` on macOS, and `${XDG_DATA_HOME:-~/.local/share}/profiledock` on Linux.

Commands can be run from any working directory: the default application-data location and an absolute `PROFILEDOCK_DATA_ROOT` remain the same. A relative `--data-root` or environment value is intentionally resolved from the working directory where the command is run.

To use another location for one invocation, place the global option before the command:

```bash
profiledock --data-root /path/to/profiledock-data list
```

To use another location for every command in the current environment, set `PROFILEDOCK_DATA_ROOT`. The CLI option takes precedence over the environment variable, and the environment variable takes precedence over the platform default.

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

## Dual Browser Engines

ProfileDock offers two distinct browser launch engines:

### Direct browser (`--engine direct`, default)

- Launches an installed Google Chrome, Chromium, or Brave executable with an isolated `--user-data-dir`.
- Does not use Playwright to control the browser.
- Requests the selected number of blank tabs, but browser startup and session-restore settings can affect the final tab count.
- Does not guarantee that any website will accept a login; website policies and browser security checks still apply.

### Playwright Context (`--engine playwright`)

- **Automated context**: Launches Playwright persistent context with local controller management.
- **Exact tab control**: Opens exactly the requested number of blank pages after launch.

Engine selection precedence is the command-line `--engine` override, the engine stored on the profile, `PROFILEDOCK_DEFAULT_ENGINE`, and finally `direct`. The environment variable must be `direct` or `playwright`.

### Managing Multiple Concurrent Accounts

```bash
profiledock create "Work" --engine direct
profiledock create "Personal" --engine direct

profiledock launch Work --tabs 2
profiledock launch Personal --tabs 2
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
profiledock create "Profile name" --engine direct
profiledock create "Profile name" --engine playwright
```

Creates profile metadata and a dedicated browser data directory. You can optionally configure the default launch engine (`direct` or `playwright`).

### List

```bash
profiledock list
profiledock list --json
```

Displays each profile's ID, name, effective engine, and status in a formatted table, or outputs JSON with `--json`.

### Show

```bash
profiledock show <id-or-name>
profiledock show <id-or-name> --json
```

Displays all safe profile metadata (ID, name, engine, status, created at, data directory, and last launched timestamp). Controller authentication tokens are never displayed.

### Rename

```bash
profiledock rename <id-or-name> "New name"
```

Validates the new non-empty name and renames the profile atomically.

### Set Engine

```bash
profiledock set-engine <id-or-name> direct
profiledock set-engine <id-or-name> playwright
```

Updates the default launch engine for an existing profile.

### Config (Launch Presets)

```bash
profiledock config show <id-or-name>
profiledock config show <id-or-name> --json

profiledock config set <id-or-name> default-tabs 4

profiledock config set <id-or-name> engine direct
profiledock config set <id-or-name> engine playwright

profiledock config set <id-or-name> browser chrome
profiledock config set <id-or-name> browser /path/to/custom/chrome

profiledock config set <id-or-name> window-size 1440x900

profiledock config add-url <id-or-name> https://github.com
profiledock config add-url <id-or-name> https://news.ycombinator.com
profiledock config remove-url <id-or-name> https://github.com


profiledock config reset <id-or-name>
```

Configured presets automatically apply when launching the profile. Any explicit options passed to `profiledock launch` (such as `--tabs`, `--engine`, `--browser`, `--url`) override the stored profile presets for that session.

Direct-browser aliases are `chrome`, `chromium`, and `brave`, including their common platform command names. Playwright accepts supported channels such as `chromium`, `chrome`, and `msedge`. Custom executable paths are resolved to absolute paths when saved. Only `http`, `https`, and `about` start URLs are accepted, and the number of start URLs cannot exceed the requested tab count; remaining tabs open `about:blank`.


### Status

```bash
profiledock status
profiledock status <id-or-name>
profiledock status --json
profiledock status <id-or-name> --json
```

Reports profile status (`stopped`, `starting`, `running`, `closing`, `stale`, or `error` where detectable). When invoked without an identifier, status reports across all profiles.

**Exit codes:**

ProfileDock uses stable exit codes for scripting compatibility:

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | User error (profile not found, validation error, profile already running, etc.) |
| `2` | Reserved for system errors (optional, not currently used) |

### Launch

```bash
profiledock launch <id-or-name>
profiledock launch <id-or-name> --tabs 3
profiledock launch <id-or-name> --engine direct
profiledock launch <id-or-name> --engine playwright
```

Starts the selected persistent browser engine. Playwright opens exactly the requested number of `about:blank` pages; direct mode passes the requested blank tabs to the installed browser, whose startup settings can affect the final count. A running profile cannot be launched a second time. You can override the engine for a single launch with `--engine`.


### Close

```bash
profiledock close <id-or-name>
```

Requests browser shutdown and removes the profile's running-state file only after the tracked process exits. Direct mode escalates to forced process-tree termination if graceful shutdown times out.

If the entire browser is closed manually or exits unexpectedly, the controller detects the closure, exits, and removes its running state. The profile can then be launched again normally.

### Delete

```bash
profiledock delete <id-or-name>
```

Asks for confirmation, then permanently removes the profile metadata and browser data. A running profile must be closed before deletion. The profile directory is quarantined during the metadata update and restored if that update fails.

For non-interactive use:

```bash
profiledock delete <id-or-name> --yes
```

### Doctor

```bash
profiledock doctor
profiledock doctor --json
profiledock doctor --repair
```

Performs comprehensive diagnostic checks across the environment and profile storage.

**Diagnostic checks:**

- **`python_version`**: Verifies Python is >= 3.9.
- **`writable_data_root`**: Checks that the application-data directory is writable.
- **`metadata_schema`**: Validates the `profiles.json` schema (version 1) and contents.
- **`metadata_backup_state`**: Inspects the backup file (`profiles.json.bak`) for validity.
- **`profile_directories_exist`**: Ensures every configured profile has a corresponding `browser-data` directory.
- **`profile_paths_under_data_root`**: Validates that all profile data paths reside safely within the profile root boundary.
- **`playwright_package`**: Checks if the `playwright` Python package is installed.
- **`playwright_chromium`**: Verifies if Playwright Chromium browser executable is installed.
- **`system_chrome`**: Verifies if system Google Chrome or Chromium is available as fallback.
- **`system_chrome_executable`**: Detects a Chrome, Chromium, or Brave executable for direct mode.
- **`browser_availability`**: Aggregate check verifying that at least one usable browser engine is present.
- **`runtime_permissions`**: Checks read/write permissions on the `runtime/` directory.
- **`stale_running_state`**: Detects leftover `running.json` state files from terminated processes.
- **`orphan_profile_directories`**: Identifies folders in `profiles/` that are not listed in metadata.
- **`version_consistency`**: Verifies that the runtime version matches installed package metadata.

**Repair capabilities (`--repair`):**

- Cleans up stale runtime `running.json` files automatically for both `direct` and `playwright` engines.
- Cleans up incomplete temporary migration and restore directories (`.temp_restore_*`, `.temp_migrating_*`, `.m-*`, `.quarantine_*`).
- Recovers valid metadata from `profiles.json.bak` if `profiles.json` is missing or corrupted.
- Automatically migrates legacy bare-array format metadata to versioned schema.
- Safely reattaches discovered orphan profile directories when explicitly requested (`--reattach-orphans`).
- Recreates missing empty `browser-data` directories when explicitly confirmed (`--recreate-missing`).
- Never deletes browser data directories automatically.
- Never guesses profile ownership or overwrites valid metadata.
- Never repairs or overwrites unsupported future schema versions.


**Exit codes:**

Exits with `0` when all critical checks pass (including with warnings). Exits with `1` if any check fails (`FAILED` status).

### Backup

```bash
profiledock backup <id-or-name> --output /path/to/backup.tar.gz
profiledock backup --all --output /path/to/all_profiles.tar.gz
profiledock backup <id-or-name> --output /path/to/backup.tar.gz --force
profiledock backup <id-or-name> --output /path/to/backup.tar.gz --json
```

Creates a versioned, self-contained, verified archive of profile metadata and browser data.

**Backup guarantees & requirements:**

- **Stopped state required**: Profiles must be fully stopped (`get_status() == "stopped"`) before backup. Active or starting profiles are refused to avoid partial or inconsistent database states.
- **Engine metadata preserved**: Retains profile configuration, including whether direct Chrome or Playwright engine is used.
- **Clean archives**: Automatically excludes transient runtime state (`running.json`, `controller.error`) and logs.
- **Manifest & checksums**: Every backup includes `backup_manifest.json` with archive schema format version (version 1), creation timestamp, ProfileDock version, and SHA-256 checksums of every file.
- **Atomic output & validation**: Writes to a temporary archive first, reopens it, verifies every archived file's size and SHA-256 checksum, and then replaces the target destination.
- **Filesystem boundaries**: Refuses linked or reparse-point content and refuses output paths inside a profile's `browser-data` directory.
- **No silent overwrites**: Refuses to overwrite an existing archive unless `--force` is provided.
- **Windows locking resilience**: If browser SQLite databases (`Cookies`, `Web Data`, `History`) are locked by background Chrome processes, fails gracefully with clear troubleshooting instructions to terminate background processes.

### Restore

```bash
profiledock restore /path/to/backup.tar.gz
profiledock restore /path/to/backup.tar.gz --force
profiledock restore /path/to/backup.tar.gz --json
```

Restores profiles from a verified ProfileDock backup archive into the active data root.

**Security & validation safeguards:**

- **Manifest & format verification**: Validates `backup_manifest.json` schema version (version 1) and verifies SHA-256 checksums for every restored file.
- **Path traversal protection**: Rejects absolute paths, parent directory traversal (`..`), and symlinks/hardlinks in archive members.
- **Decompression bomb prevention**: Restricts single member extraction to 5 GiB and total archive extraction to 20 GiB.
- **Strict manifest validation**: Rejects unsafe profile IDs, unsafe relative file paths, duplicate archive members, unlisted files, invalid sizes, and malformed checksums before extraction.
- **Atomic extraction & quarantine**: Extracts into isolated temporary directories first and quarantines existing conflicting directories during replacement.
- **Transactional metadata update**: Preserves the stored `engine` preference (`direct` or `playwright`) and rolls back directory changes if metadata validation fails.
- **Conflict protection**: Prevents silent overwrite when profiles with conflicting IDs or names already exist in the destination (requires `--force` to overwrite).
- **Running-profile protection**: Even with `--force`, ProfileDock refuses to overwrite a profile while it is running.

### Migrate



```bash
profiledock migrate --from-project <path>
profiledock migrate --from-project <path> --json
profiledock migrate --from-project <path> --remove-source --yes
```

Migrates profiles and browser data from a legacy or another project directory into the active ProfileDock data root.

**Migration safety & guarantees:**

- **Pre-migration backup**: It is strongly recommended to create a copy/backup of both the source directory and the destination data root before initiating migration.
- **Safety checks**: Detects both legacy and application-data layouts, validates every source field and timestamp, requires exact `profiles/<id>/browser-data` paths, rejects links and overlapping roots, and refuses migration if a source controller is running.
- **Conflict detection**: Prevents silent overwrite by detecting ID, name, metadata, content, final-directory, and interrupted temporary-directory conflicts.
- **Verified copying**: Copies profile directories into private temporary destination folders and compares complete directory and SHA-256 file manifests before finalization.
- **Automatic rollback**: Incomplete changes in the destination are rolled back cleanly if copying or validation fails.
- **Source preservation**: Leaves source files untouched by default and detects source changes during copying. `--remove-source` deletes tracked source data only after successful migration and explicit confirmation; untracked profile or runtime entries block removal.
- **Idempotent**: Re-running migration skips profiles only when their metadata and copied browser content are identical, without rewriting destination metadata.

When combining `--remove-source` with `--json`, also pass `--yes`. This keeps standard output valid JSON without an interactive confirmation prompt.

### Logs

```bash
profiledock logs
profiledock logs <id-or-name>
profiledock logs <id-or-name> --last 100
profiledock logs <id-or-name> --json
```

Displays structured, bounded, privacy-safe execution logs.

**Logging & Privacy Guarantees:**

- **Structured & Correlated**: Emits JSON log entries with unique Correlation IDs shared between CLI operations and controller processes.
- **Engine Lifecycle**: Records engine routing (`direct` vs `playwright`), native browser process PID lifecycle, Playwright IPC events, and browser executable paths used.
- **Strict Privacy Redaction**: Never logs passwords, authentication tokens, cookies, LocalStorage data, or authorization headers. Full URLs are automatically sanitized to preserve origin and top path without query parameters or credentials.
- **Size & Rotation Bounded**: Log files automatically rotate at 2 MiB with configurable retention backups, ensuring bounded disk usage.
- **Failure-Safe**: Logging failures never block or interrupt browser launches or CLI commands.

## Data storage and persistence


ProfileDock resolves one data root for each command and uses this structure:

```text
ProfileDock/
├── metadata/
│   ├── profiles.json
│   └── profiles.lock
├── backups/
│   └── profiles.json.bak
├── profiles/
│   └── <profile-id>/
│       └── browser-data/
├── runtime/
│   └── <profile-id>/
│       ├── running.json
│       └── controller.error
└── logs/
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
      "last_launched_at": null,
      "engine": "direct"
    }
  ]
}
```

The metadata document includes:

- `schema_version`: Version of the metadata format (currently 1)
- `profiles`: Array of profile objects with required fields: `id`, `name`, `created_at`, `data_dir`
- Optional fields: `last_launched_at` (ISO-8601 timestamp) and `engine` (`direct` or `playwright`)

### Metadata migration

ProfileDock automatically migrates older bare-array format to the versioned document format:

- The old `profiles.json` is backed up to `profiles.json.bak` before migration
- Migration validates all profile data before accepting the new format
- Invalid data is rejected before any changes are written

### Metadata safety

ProfileDock implements several safety mechanisms to protect metadata integrity:

**Atomic writes**: Metadata, backups, and controller state use unique temporary files and atomic replacement. Transient Windows sharing violations are retried within a bounded interval.

**Cross-process locking**: A lock file (`metadata/profiles.lock`) coordinates concurrent metadata modifications through an operating-system file lock. Its presence alone does not mean ProfileDock is locked.

**Backup recovery**: Before each metadata update, the current file is backed up to `profiles.json.bak`. The `profiledock doctor --repair` command can restore a valid backup after primary-file corruption.

**Duplicate prevention**: Profile IDs and data directories must be unique. Duplicate values are rejected before any changes are written.

**Path safety**: Data directories must exactly match `profiles/<id>/browser-data`. Symlinks, junctions, reparse points, duplicate paths, and path traversal attempts are rejected.

**Managed-directory safety**: ProfileDock rejects unsafe managed directories, path-like profile IDs, runtime paths beneath `browser-data`, and deletion targets that do not exactly match `profiles/<id>/browser-data`.

**Private storage**: On POSIX systems, ProfileDock restricts managed directories to the owner and writes metadata, lock, controller-state, and controller-error files with owner-only permissions. Windows access remains governed by the directory's inherited ACLs.

**Corruption handling**: ProfileDock never overwrites corrupted metadata automatically. If both the primary and backup files are corrupted, manual intervention is required.

`profiles.json` contains profile metadata only. Chromium stores cookies, local storage, cache, sessions, and login state inside `browser-data`.

`running.json` exists while a launch is tracked. Playwright state contains the profile ID, controller PID, start time, loopback port, status, and a random authentication token; the controller accepts a close request only when its token matches. Direct-mode state contains the profile ID, tracked browser PID, launch PID, browser channel, start time, and status. State writes are atomic, and stale files are cleaned automatically.

Runtime state, logs, backups, metadata, and browser data are separated. Runtime files are never written inside `browser-data`.

## Isolation, security, and privacy

- Every profile uses a separate Chromium user data directory.
- ProfileDock does not automate authentication or store passwords itself.
- Websites and Chromium may store credentials, cookies, tokens, and browsing history inside `browser-data`.
- Anyone with access to a profile directory may be able to access its browser state.
- Keep the selected data root private and out of source control.
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

The integration test launches a real persistent browser context, verifies the requested tab count, closes it, relaunches it, and confirms that a persistent cookie remains. It uses Playwright Chromium or a detected Chrome, Chromium, or Brave executable and skips only when none is available.

Run only fast unit tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not browser"
```

Run only browser and controller integration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -m browser
```

## Continuous Integration & Local Equivalents

ProfileDock runs automated GitHub Actions CI across **Ubuntu**, **macOS**, and **Windows** on Python **3.9**, **3.10**, **3.11**, **3.12**, and **3.13**.

### Local Equivalents of CI Checks

**1. Build distribution package:**
```bash
python -m pip install --upgrade build
python -m build
```

**2. Lightweight install test (Standard library + Typer only):**
```bash
python -m pip install .
python -m pytest -m "not browser"
```

**3. Full install test (with Playwright extras):**
```bash
python -m pip install ".[playwright,test]"
python -m playwright install chromium
python -m pytest
```

**4. Code formatting & whitespace check:**
```bash
git diff --check
```

**5. Version alignment check:**
```bash
python -c "import importlib.metadata, profiledock; assert importlib.metadata.version('profiledock') == profiledock.__version__"
```

## Dependency Management & Policy

ProfileDock adheres to a strict, reproducible dependency policy designed for maximum portability and resilience:

- **Core Minimalism**: Base runtime (`profiledock`) strictly depends only on `typer>=0.9` and the Python standard library. Direct engine operations (`--engine direct`) and CLI features require zero external automation packages.
- **Optional Playwright Extra**: Playwright automation capabilities are isolated under `[project.optional-dependencies] playwright = ["playwright>=1.40"]`.
- **Reproducible Lockfile**: A cross-platform development lockfile is maintained at `requirements-dev.lock`. To update the lockfile in a clean environment:
  ```bash
  pip install -e .[test,playwright]
  pip freeze > requirements-dev.lock
  ```
- **Automated Dependency Updates**: Dependabot is configured via `.github/dependabot.yml` to monitor both `pip` packages and GitHub Actions weekly.
- **Upgrading Dependencies**:
  ```bash
  python -m pip install --upgrade --upgrade-strategy eager -e .[test,playwright]
  python -m pytest
  ```



## JSON output format

All JSON output from `--json` flags is stable and safe for scripting:

**`list --json`:**

Always returns an array of profile objects:

```json
[
  {
    "id": "abc123",
    "name": "Work",
    "status": "stopped",
    "created_at": "2026-01-01T00:00:00+00:00",
    "data_dir": "/path/to/profiles/abc123/browser-data",
    "last_launched_at": null,
    "engine": "direct"
  }
]
```

**`status --json`:**

Returns an array containing identity, effective engine, and runtime status:

```json
[
  {
    "id": "abc123",
    "name": "Work",
    "engine": "direct",
    "status": "stopped"
  }
]
```

**`show <profile> --json`:**

Returns a single profile object with all metadata:

```json
{
  "id": "abc123",
  "name": "Work",
  "status": "running",
  "created_at": "2026-01-01T00:00:00+00:00",
  "data_dir": "/path/to/profiles/abc123/browser-data",
  "last_launched_at": "2026-01-15T12:30:00+00:00",
  "engine": "playwright"
}
```

**`doctor --json`:**

Returns diagnostic checks, repairs performed, and overall health status:

```json
{
  "checks": [
    {
      "id": "python_version",
      "status": "ok",
      "summary": "Python version is 3.11.0 (>= 3.9 required)."
    },
    {
      "id": "stale_running_state",
      "status": "warning",
      "summary": "Found 1 stale running.json file(s).",
      "action": "Run 'profiledock doctor --repair' to clean stale running-state files."
    }
  ],
  "repairs": [
    {
      "id": "repair_stale_running_state",
      "status": "ok",
      "summary": "Cleaned up 1 stale running.json file(s)."
    }
  ],
  "healthy": true
}
```

**`migrate --json`:**

Returns details of migrated, skipped, and failed profiles:

```json
{
  "source_root": "/path/to/source",
  "destination_root": "/path/to/destination",
  "migrated": [
    {
      "id": "abc123",
      "name": "Work",
      "status": "migrated",
      "message": "successfully migrated"
    }
  ],
  "skipped": [],
  "failed": [],
  "source_removed": false
}
```

**`backup --json`:**

Returns backup report, format version, and details for each backed-up profile:

```json
{
  "output_path": "/path/to/backup.tar.gz",
  "format_version": 1,
  "profiledock_version": "0.8.2",
  "created_at": "2026-08-20T12:00:00+00:00",
  "total_profiles": 1,
  "total_files": 12,
  "total_bytes": 1048576,
  "profiles": [
    {
      "id": "abc123",
      "name": "Work",
      "engine": "direct",
      "status": "backed_up",
      "file_count": 12,
      "total_bytes": 1048576,
      "message": "successfully backed up"
    }
  ]
}
```

**`restore --json`:**

Returns restore report, format version, and profile counts:

```json
{
  "archive_path": "/path/to/backup.tar.gz",
  "format_version": 1,
  "profiledock_version": "0.8.2",
  "total_restored": 1,
  "total_files": 12,
  "total_bytes": 1048576,
  "restored": [
    {
      "id": "abc123",
      "name": "Work",
      "engine": "direct",
      "status": "restored",
      "file_count": 12,
      "total_bytes": 1048576,
      "message": "successfully restored"
    }
  ],
  "skipped": []
}
```

Failures also use this report shape, place the error in `failed`, and exit with code `1`. Human-readable runs print migration, skip, conflict, and failure information directly in the terminal.



**`logs --json`:**

Returns an array of structured, redacted log entries:

```json
[
  {
    "timestamp": "2026-08-21T12:00:00+00:00",
    "level": "INFO",
    "event": "browser_process_spawned",
    "correlation_id": "a1b2c3d4e5f6",
    "profile_id": "abc123",
    "engine": "direct",
    "pid": 12345,
    "result": "success"
  }
]
```

**Guarantees:**


- JSON output is always valid and parseable
- No human prose mixed with JSON
- No sensitive data exposed (tokens, secrets, passwords)
- Field names and types are stable across versions
- `status` and `list` always return arrays for consistent scripting
- Timestamps use ISO-8601 format with a timezone offset when present

## Updating the project

If the project was cloned with Git:

```bash
git pull
python scripts/setup_project.py
```

Rerunning setup updates the editable installation and executes the tests. Review the current version in `pyproject.toml`, `profiledock.__version__`, or the release tag.

## Troubleshooting

### Chromium download returns HTTP 403

Playwright's CDN may be unavailable in some locations. Install Chrome, Chromium, or Brave and rerun setup. The script detects supported system browsers and avoids the blocked download.

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

Run `profiledock doctor --repair` to validate and restore `profiles.json.bak`. If both files are corrupted, restore from a manual backup. Normal profile commands intentionally refuse to overwrite corrupted metadata automatically.

### `profiles.lock` exists but no process is running

This is normal. ProfileDock uses an operating-system lock on this file, so an unlocked file may remain on disk safely. Do not use file existence to decide whether a metadata operation is active.

### Controller launch failure

When `profiledock launch` fails, the CLI now shows a specific error message instead of a generic failure. Common causes and fixes:

**Playwright package not installed:**
```
Error: No module named 'playwright'
```
Rerun project setup with `python scripts/setup_project.py`, or install Playwright through the active virtual environment.

**No supported browser found:**
```
Error: Playwright Chromium: <error>\nGoogle Chrome: <error>\nSystem browser: <error or not found>
```
Either install Playwright Chromium (`playwright install chromium`) or install Chrome, Chromium, or Brave on your system.

**Browser process failed to launch:**
The error message will include Playwright's diagnostic details. Check that the profile data directory exists and is accessible.

**Controller startup timed out:**
The browser took longer than 30 seconds to become ready. Check system resources and close other applications.

**Controller process exited unexpectedly:**
The controller subprocess crashed. Check for system resource constraints or permission issues.

When a controller fails, ProfileDock preserves a diagnostic file at `runtime/<id>/controller.error` beneath the selected data root. It contains a stable error category, a bounded diagnostic message, and the browser channel attempted when relevant. Diagnostics are limited to 4 KiB, controller tokens are redacted, and the file is automatically cleaned up on the next successful launch.

## Removing ProfileDock

Profile data is separate from the source project, so decide whether to keep or delete both locations.

### 1. Close running profiles

List profiles and close every entry marked `running`:

```bash
profiledock list
profiledock close <id>
```

### 2. Back up profiles if needed

To preserve login state and browser data, copy these items to a secure location:

```text
metadata/
backups/
profiles/
```

They must be restored together beneath the same data root.

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

Then delete the `.venv` directory. This removes installed Python dependencies but keeps source code and application data. The setup script can recreate it later.

### 5. Remove the complete project

Close all profiles, leave the project directory in your terminal, and delete the `profiledock` folder using File Explorer, Finder, or your desktop environment's file manager. Moving it to the Recycle Bin or Trash is recommended because it remains recoverable until emptied.

Deleting the source project does not delete ProfileDock application data. Remove the platform data root separately only after closing profiles and confirming that no browser state must be retained.

The default application-data roots are `%LOCALAPPDATA%\ProfileDock` on Windows, `~/Library/Application Support/ProfileDock` on macOS, and `${XDG_DATA_HOME:-~/.local/share}/profiledock` on Linux. If `PROFILEDOCK_DATA_ROOT` or `--data-root` was used, remove that selected directory instead. Deleting the data root permanently removes every ProfileDock profile, session, cookie, cache, backup, and runtime record stored there.

## Versioning

ProfileDock follows Semantic Versioning. Stable releases use annotated Git tags such as `v0.12.0`.

## License

ProfileDock is available under the MIT License. See `LICENSE` for the full terms.
