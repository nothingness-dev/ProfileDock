# Format compatibility

ProfileDock versions every persistent or machine-consumed format independently. A package version does not imply that every format changes.

## Compatibility guarantees

- Current-version documents are validated strictly. Missing required fields, unknown fields, invalid types, and unsupported values are rejected.
- Historical formats are upgraded through ordered, one-version-at-a-time adapters. Running an adapter more than once produces the same result.
- Persistent metadata and runtime-state migrations create a byte-for-byte backup before replacing the original.
- Replacements are atomic. If validation, backup, or replacement fails, the original remains authoritative.
- Versions newer than the installed ProfileDock supports are rejected and are not rewritten, deleted, or downgraded.
- Writers emit only the current format. ProfileDock never silently writes an older version.
- Backup archives are immutable inputs. Historical values embedded in a supported archive are normalized in memory; the source archive is never modified.

## Profile metadata

`profiles.json` uses schema version 1. The document contains exactly `schema_version` and `profiles`. Every version 1 profile contains `id`, `name`, `created_at`, `data_dir`, `last_launched_at`, `engine`, and `launch_config`. The `engine` value is `direct`, `playwright`, or `null`.

The historical version 0 format was a bare array. Early version 1 files that omitted optional fields are accepted by the compatibility adapter and canonicalized. Migration writes the original to the configured metadata backup location before replacing it.

## Launch configuration

Launch configuration schema version 2 contains `schema_version`, `default_tabs`, `start_urls`, `engine`, `browser`, `window_width`, `window_height`, `proxy`, `user_agent`, `locale`, and `timezone`. Version 1 remains readable: it migrates to version 2 by adding the four identity fields at `null`. Unversioned launch configurations are version 0 and migrate to the current version by adding the version field and explicit defaults. The stored `proxy` value may embed credentials; every display surface (show, config show, logs, JSON output) redacts them to `user:***@host`.

## Runtime controller protocol

`running.json` uses protocol version 2 and is discriminated by `engine`.

- `direct` state identifies the browser process with `pid`, `launcher_pid`, and `process_create_time`, plus launch status and browser details. It never contains a controller token or port.
- `playwright` state identifies the local controller with `controller_pid`, `controller_started_at`, a loopback `port`, and an authentication `token`, plus launch status and page details. When the browser main process can be identified, the state also records `browser_pid` and `browser_create_time` for identity-verified process management, and `headless` records the visibility of the launch. A Playwright state never authorizes operating-system process termination as a Direct state, and a recorded browser PID is never signalled unless its create time matches `browser_create_time`.

Unversioned Direct and Playwright state and Playwright protocol version 1 migrate sequentially to protocol version 2. A pre-migration copy is retained beside the runtime state as `running.json.v<version>.bak`. Runtime files remain outside `browser-data`.

Playwright protocol version 2 accepts authenticated, newline-delimited JSON commands for lifecycle, tab management, page reading, JavaScript evaluation, and cookie export. Requests and responses are size-bounded, command arguments are validated, and legacy authenticated `probe` and `close` strings remain supported for compatibility with existing clients.

## Backup archives

Backup archive format version 1 contains a strict `backup_manifest.json` with the producing ProfileDock version, creation time, aggregate counts, profile metadata, launch configuration, file sizes, and SHA-256 checksums. Unknown manifest fields, inconsistent totals, unsafe members, unsupported future versions, and malformed embedded metadata are rejected.

## JSON CLI output

Commands that support `--json` emit output version 1:

```json
{
  "output_version": 1,
  "command": "list",
  "data": []
}
```

Consumers must check `output_version` before reading `data` and reject versions they do not support. Human-readable output is not a stable machine interface.

The complete planned 1.0 command, option, exit-code, stream, confirmation, resolution, and deprecation guarantees are defined in the [CLI contract](cli-contract.md). Golden fixtures enforce both the Typer command surface and JSON payloads.

## Support policy

ProfileDock keeps readers for every historical format shipped by a tagged release unless a future major release explicitly documents removal. Fixtures for every historical version are part of the automated test suite. New fields require a format-version change unless the existing schema explicitly permits them.
