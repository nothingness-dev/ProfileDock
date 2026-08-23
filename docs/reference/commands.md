# Command reference

## Invocation and global options

```text
profiledock [GLOBAL OPTIONS] COMMAND [ARGUMENTS] [OPTIONS]
```

Global options must appear before the command:

| Option | Meaning |
|---|---|
| `--data-root PATH` | Override the application-data root for this command. |
| `--verbose`, `-v` | Enable verbose and trace logging behavior. |
| `--log-level LEVEL` | Set the log threshold: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `--non-interactive` | Prohibit prompts and fail when required input is missing. |
| `--version`, `-V` | Print the installed ProfileDock version and exit. |
| `--help` | Show help. |

Profile arguments shown as `<profile>` resolve by exact case-sensitive ID, unique case-sensitive ID prefix, then exact case-sensitive name.

## `create`

```text
profiledock create NAME [--engine direct|playwright]
```

Creates profile metadata and `profiles/<id>/browser-data`. `--engine`/`-e` sets the profile engine; omitting it leaves the metadata value nullable and uses normal engine precedence.

```bash
profiledock create "Personal"
profiledock create "Work" --engine playwright
```

## `list`

```text
profiledock list [--json]
```

Lists every profile with ID, name, effective engine, and runtime status. Empty human output says no profiles were found. JSON `data` is always an array.

## `show`

```text
profiledock show PROFILE [--json]
```

Shows identity, effective engine, status, timestamps, data directory, and launch configuration when present.

## `rename`

```text
profiledock rename PROFILE NEW_NAME
```

Renames metadata without changing the profile ID, browser-data directory, or browser session.

## `set-engine`

```text
profiledock set-engine PROFILE direct|playwright
```

Updates the profile-level engine. A launch-config engine can still override it.

## `config show`

```text
profiledock config show PROFILE [--json]
```

Shows the profile's launch preset. JSON uses launch-configuration schema version 1.

## `config set`

```text
profiledock config set PROFILE SETTING VALUE
```

Supported settings:

| Setting | Value |
|---|---|
| `default-tabs` | Positive integer. |
| `engine` | `direct` or `playwright`. |
| `browser` | Supported browser name or executable path. |
| `window-size` | `WIDTHxHEIGHT`, each at least 100. |

Examples:

```bash
profiledock config set Work default-tabs 4
profiledock config set Work engine playwright
profiledock config set Work browser chromium
profiledock config set Work window-size 1440x900
```

## `config add-url`

```text
profiledock config add-url PROFILE URL
```

Validates and appends a start URL if it is not already stored.

## `config remove-url`

```text
profiledock config remove-url PROFILE URL
```

Removes a stored start URL. The URL must match the stored normalized value.

## `config reset`

```text
profiledock config reset PROFILE
```

Clears the complete launch preset and restores inherited/default launch behavior.

## `status`

```text
profiledock status [PROFILE] [--json]
```

Without a selector, reports every profile. With a selector, reports one. Status values include `stopped`, `starting`, `running`, `closing`, `stale`, and `error` where applicable. JSON `data` remains an array in both forms and exposes the effective engine.

## `launch`

```text
profiledock launch PROFILE [OPTIONS]
```

Options:

| Option | Meaning |
|---|---|
| `--tabs N`, `-t N` | Number of tabs or pages, at least 1. |
| `--engine VALUE`, `-e VALUE` | One-launch `direct` or `playwright` override. |
| `--browser VALUE`, `-b VALUE` | Browser name or executable path. |
| `--url URL`, `-u URL` | Start URL; repeat for multiple pages. |

When no tab count or preset exists, interactive mode prompts. Non-interactive mode requires `--tabs`. Start URLs cannot outnumber tabs. Duplicate launch is refused. Launch writes runtime state outside `browser-data` and records the launch timestamp after success.

## `close`

```text
profiledock close PROFILE
```

Requests graceful Playwright context shutdown or safely terminates the verified Direct browser process. Runtime state is cleaned after closure. Missing, stale, malformed, or unverifiable state is handled conservatively.

## `delete`

```text
profiledock delete PROFILE [--yes]
```

Deletes metadata and the profile's managed browser-data directory. Running profiles must be closed. Without `--yes`/`-y`, ProfileDock confirms interactively. Declining aborts with exit code 1 and leaves data unchanged. Non-interactive mode requires `--yes`.

## `doctor`

```text
profiledock doctor [OPTIONS]
```

Options:

| Option | Meaning |
|---|---|
| `--repair` | Perform supported safe repairs. |
| `--reattach-orphans` | With `--repair`, reattach safe orphan profile directories. |
| `--recreate-missing` | With `--repair`, recreate missing empty browser-data directories. |
| `--yes`, `-y` | Skip repair confirmation where supported. |
| `--json` | Emit a versioned diagnostic report. |

Checks Python, storage, metadata, browser availability, runtime state, directories, orphan data, and version consistency. Repairs include stale-state cleanup, temporary-operation cleanup, valid metadata recovery, legacy metadata migration, and explicitly requested directory repairs. Active or ambiguous profile state blocks unsafe mutation.

## `migrate`

```text
profiledock migrate --from-project PATH [OPTIONS]
```

Options:

| Option | Meaning |
|---|---|
| `--from-project PATH` | Required legacy project-local source. |
| `--remove-source` | Remove migrated source data after successful migration. |
| `--yes`, `-y` | Confirm source removal. |
| `--json` | Emit a versioned migration report. |

Detects legacy `profiles.json` and `profiles/`, validates metadata and paths, refuses active profiles, checks ID and name conflicts, copies into temporary destinations, verifies content, updates destination metadata after copying, and rolls back incomplete destination changes. IDs and timestamps are preserved. Source data stays unchanged unless both `--remove-source` and confirmation are supplied. Repeated migration is idempotent.

JSON success reports use stdout. JSON failure reports use stderr and leave stdout empty. `--remove-source --json` requires `--yes` and never prompts.

## `backup`

```text
profiledock backup PROFILE --output ARCHIVE [--force] [--exclude-cache] [--json]
profiledock backup --all --output ARCHIVE [--force] [--exclude-cache] [--json]
```

Options are `--all`/`-a`, required `--output`/`-o`, `--force`/`-f`, `--exclude-cache`/`-C`, and `--json`. Specify either one profile or `--all`, not both. Every selected profile must be stopped. Pass `--exclude-cache`/`-C` to skip transient Chromium caches and optimize archive size.

The command creates a versioned `.tar.gz` archive with metadata, engine and launch configuration, file sizes, and SHA-256 checksums. Runtime files, logs, links, junctions, and temporary files are excluded or rejected. Output is staged and verified before atomic replacement. Existing output requires `--force`.

## `restore`

```text
profiledock restore ARCHIVE [--force] [--json]
```

Validates archive type, paths, member count, expanded size, manifest schema, IDs, metadata, totals, sizes, and checksums before committing. Conflicting IDs or names are refused unless the supported conflict can be replaced with `--force`; active profiles are never overwritten. Extraction and metadata update are transactional.

## `logs`

```text
profiledock logs [PROFILE] [--last N] [--json]
```

Reads structured local logs, optionally filtered by resolved profile ID. `--last`/`-n` limits the newest entries. Sensitive controller tokens and known secrets are redacted before logging.

## Exit codes, streams, and errors

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Operational, validation, confirmation, storage, security, profile, or browser error. |
| `2` | CLI syntax or usage error. |

Human and JSON success output use stdout. Operational errors use stderr. Errors begin `Error [category]:`. Stable categories and JSON guarantees are defined in the [CLI contract](cli-contract.md), with every payload described in the [JSON output reference](json-output.md).
