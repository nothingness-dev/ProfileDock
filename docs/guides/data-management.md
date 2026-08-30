# Storage, backup, restore, and migration

## Application-data layout

```text
<data-root>/
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

Metadata, profile browser data, runtime state, logs, and backups are separate. Runtime state is never stored inside `browser-data`.

Platform defaults:

- Windows: `%LOCALAPPDATA%\ProfileDock`
- macOS: `~/Library/Application Support/ProfileDock`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/profiledock`

`--data-root` overrides `PROFILEDOCK_DATA_ROOT`, which overrides the platform default. Every command uses the same resolver.

## What persists

Chromium stores cookies, sessions, local storage, cache, history, extensions, and login state in each profile's independent `browser-data`. ProfileDock stores only management metadata outside that directory. Closing and relaunching the same profile reuses its browser data.

## Metadata

`metadata/profiles.json` uses schema version 1:

```json
{
  "schema_version": 1,
  "profiles": [
    {
      "id": "abc123",
      "name": "Personal",
      "created_at": "2026-01-01T00:00:00+00:00",
      "data_dir": "/path/to/profiles/abc123/browser-data",
      "last_launched_at": null,
      "engine": "direct",
      "launch_config": null
    }
  ]
}
```

Writes use validation, an operating-system metadata lock, backup of the previous value, private temporary files, `fsync`, and atomic replacement. IDs, names, timestamps, path boundaries, duplicate IDs, and duplicate directories are validated.

Legacy bare-array metadata is backed up and migrated sequentially. Unsupported future versions are rejected and never downgraded. See [Format compatibility](../reference/format-compatibility.md).

## Back up profiles

Close selected profiles first:

```bash
profiledock close Work
profiledock backup Work --output work-profile.tar.gz
profiledock backup --all --output all-profiles.tar.gz
```

Use `--force` only to replace an existing output archive deliberately. Use `--json` for a versioned report.

Backup guarantees:

- Refuses active or starting profiles.
- Preserves IDs, names, timestamps, engines, and launch configurations.
- Includes every accepted browser-data file with size and SHA-256 checksum.
- Excludes runtime state, controller errors, logs, and temporary files.
- Rejects symlinks, junctions, reparse points, unsafe output locations, and changing source trees.
- Writes to a temporary archive, reopens and verifies it, then atomically replaces the destination.

Keep backups on storage protected from the same failure or account compromise as the primary data root.

## Restore archives

```bash
profiledock restore work-profile.tar.gz
profiledock restore work-profile.tar.gz --json
```

Restore validates the complete archive before committing. It rejects absolute paths, `..` traversal, backslashes used for cross-platform escape, links, unsafe types, duplicate members, oversized archives, bad manifests, unknown future versions, unsafe profile IDs, inconsistent totals, and checksum mismatches.

Conflicting IDs and names are refused. `--force` permits supported replacement but never permits active-profile overwrite or filesystem-boundary escape. Temporary extraction and quarantined replacement make restore rollback-safe.

## Verify an archive

```bash
profiledock verify work-profile.tar.gz
profiledock verify work-profile.tar.gz --json
```

`verify` checks a backup archive without restoring it: manifest schema, totals, member paths and sizes, then every file's SHA-256 against the manifest. Nothing is written to any data root, so it is safe to run against archives from untrusted sources before choosing to restore them. A non-zero exit lists the members whose content no longer matches the archive manifest.

## Migrate project-local data

Before migration:

1. Close all legacy profiles and background browser processes.
2. Copy the legacy project directory or at least `profiles.json` and `profiles/` to separate backup storage.
3. Confirm the destination data root with `profiledock doctor`.

Run:

```bash
profiledock migrate --from-project /path/to/legacy/project
profiledock migrate --from-project /path/to/legacy/project --json
```

Migration detects project-local `profiles.json` and `profiles/`, validates source metadata and exact profile paths, refuses source profiles that appear active, detects destination ID and name conflicts, copies to temporary profile directories, verifies the copy, and commits metadata only after data is ready. Incomplete destination changes roll back. Rerunning after success reports matching profiles as already migrated.

The source remains unchanged by default. To remove source profile data after successful migration:

```bash
profiledock migrate --from-project /path/to/legacy/project --remove-source --yes
```

`--remove-source` requires explicit confirmation. With `--json`, `--yes` is mandatory so no prompt can corrupt machine output.

## Delete a profile

```bash
profiledock close Work
profiledock delete Work
profiledock delete Work --yes
```

Deletion validates the ID before constructing paths, requires an exact managed directory, resolves the target under the configured root, rejects root deletion and link escapes, refuses active state, quarantines data, updates metadata, and rolls back pre-commit changes on failure.

Deletion is permanent unless an independent backup exists.

## Recovery

Run diagnostics before manual edits:

```bash
profiledock doctor
profiledock doctor --repair
```

Doctor can recover a valid metadata backup, migrate legacy metadata, remove verifiably stale runtime state, and clean known incomplete temporary operations. If primary and backup metadata are both corrupted, or runtime state is ambiguous, ProfileDock preserves evidence and requires manual review.
