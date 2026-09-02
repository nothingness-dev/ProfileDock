# Command reference

## Invocation and global options

```text
profiledock [GLOBAL OPTIONS] COMMAND [ARGUMENTS] [OPTIONS]
```

Running `profiledock` with no command opens a full-screen interactive shell (see the getting-started guide) when stdout is a terminal, the `interactive` extra is installed (`pip install "profiledock[interactive]"`), and `--non-interactive`/`PROFILEDOCK_NON_INTERACTIVE` are not set. Otherwise it prints the usage summary with exit code 2. Press `q` to leave the shell; `Esc` steps back from forms and result views.

Global options must appear before the command:

| Option | Meaning |
|---|---|
| `--data-root PATH` | Override the application-data root for this command. |
| `--verbose`, `-v` | Enable verbose and trace logging behavior. |
| `--log-level LEVEL` | Set the log threshold: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. Invalid values fail with a usage error. |
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

Shows identity, effective engine, status, timestamps, data directory, and launch configuration when present. When the profile is running it also reports live CPU %, resident memory (RSS), and process count; stopped profiles report the disk footprint breakdown (browser data, cache, cookies, logs). With `--json`, a `metrics` object is always included in `data` (`live` is `null` when not running) and is described in the [JSON output reference](json-output.md).

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

Shows the profile's launch preset. JSON uses launch-configuration schema version 2; a stored proxy is redacted to `user:***@host` when it contains credentials.

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
| `proxy` | `http://`, `https://`, or `socks5://` URL, optionally `user:pass@host:port`; `none` clears it. |
| `user-agent` | Non-empty user-agent string (max 512 characters). |
| `locale` | Locale tag such as `en` or `en-GB`. |
| `timezone` | IANA timezone name such as `Europe/Berlin`. |

Examples:

```bash
profiledock config set Work default-tabs 4
profiledock config set Work engine playwright
profiledock config set Work browser chromium
profiledock config set Work window-size 1440x900
profiledock config set Work proxy socks5://127.0.0.1:9050
profiledock config set Work locale en-GB
profiledock config set Work timezone Europe/Berlin
profiledock config set Work proxy none
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
profiledock status [PROFILE] [--watch] [--interval SECONDS] [--json] [--metrics]
```

Options:

| Option | Meaning |
|---|---|
| `--watch`, `-w` | Continuously poll and display live status. |
| `--interval SECONDS`, `-i SECONDS` | Polling interval in seconds when using `--watch` (default: 1.0). |
| `--json` | Emit versioned JSON output. |
| `--metrics`, `-m` | Include per-profile resource metrics: process-tree CPU %, resident memory (RSS), process count, active tabs, and disk footprint. |

Without a selector, reports every profile. With a selector, reports one. Status values include `stopped`, `starting`, `running`, `closing`, `crashed`, `stale`, and `error` where applicable. JSON `data` remains an array in both forms and exposes the effective engine. With `--metrics`, each item gains a `metrics` object shaped like the `show` metrics payload (JSON output reference) and human output appends `CPU%`, `RSS`, `PROCS`, `TABS`, and `DISK` columns. Default output (without `--metrics`) is unchanged.

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
| `--headless` | Run a Playwright browser in the background without a visible window. |
| `--wait-timeout SECONDS` | Seconds to wait for the browser and controller to become fully ready (default: 30). |
| `--proxy URL` | Proxy for this launch: `http://`, `https://`, or `socks5://`, optionally `user:pass@host:port`. Overrides the stored preset. Requires the Playwright engine when credentials are present. |
| `--user-agent STRING` | Custom user agent; overrides the stored preset. |
| `--locale TAG` | Browser locale such as `en-GB`; overrides the stored preset. |
| `--timezone ZONE` | IANA timezone such as `Europe/Berlin`; overrides the stored preset. |

Proxy strings with embedded credentials are accepted by the Playwright engine and are always redacted to `user:***@host` in `show`, `config show`, and logs. The Direct engine supports only credentialess proxies via `--proxy-server`.

Playwright launches open a visible Chromium window by default; pass `--headless` for a background Playwright launch. The Direct engine does not accept `--headless`. The command returns only after the controller and browser are fully ready, and a failed startup rolls back all runtime artifacts. When no tab count or preset exists, interactive mode prompts. Non-interactive mode requires `--tabs`. Start URLs cannot outnumber tabs. Duplicate launch is refused while the profile is starting or already running. Launch writes runtime state outside `browser-data` and records the launch timestamp after success.

## `close`

```text
profiledock close PROFILE [--timeout SECONDS]
profiledock close --all [--timeout SECONDS]
```

Requests graceful Playwright context shutdown or safely terminates the verified Direct browser process. `--timeout SECONDS` bounds how long the command waits for the browser and controller to terminate and for persistent data to flush (default: 15). The command returns only after the state file is removed and the recorded browser process has exited; a stuck browser is terminated only when its recorded process identity matches. Crashed runtime state is recovered automatically, and a browser process is never signalled unless its identity matches the recorded process. Missing, stale, malformed, or unverifiable state is handled conservatively.

With `--all`/`-a`, every profile is closed in turn and already-stopped profiles are counted rather than treated as errors. Specify either one profile or `--all`, not both.

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

Checks Python, storage, metadata, browser availability, runtime state, directories, orphan data, and version consistency. Repairs include stale-state cleanup (including unreadable running-state files), temporary-operation cleanup, valid metadata recovery, legacy metadata migration, and explicitly requested directory repairs. Active or ambiguous profile state blocks unsafe mutation. Requesting `--recreate-missing` or `--reattach-orphans` with `--json` without `--yes` fails on stderr instead of prompting; supply `--yes` for automated runs.

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

## `verify`

```text
profiledock verify ARCHIVE [--json]
```

Validates a backup archive without restoring it: manifest schema, totals, member paths and sizes, then every file's SHA-256 against the manifest. Nothing is written to any data root. Exits non-zero and lists failing members when a checksum fails; otherwise prints `All checksums verified.`

## `logs`

```text
profiledock logs [PROFILE] [--last N] [--json]
```

Reads structured local logs, optionally filtered by resolved profile ID. `--last`/`-n` limits the newest entries and must be a positive integer. Sensitive controller tokens and known secrets are redacted before logging.

## `tabs`

```text
profiledock tabs PROFILE [--json]
```

List open browser tabs, page titles, and active URLs for a running Playwright session. This command does not auto-start a stopped profile.

## `open-tab`

```text
profiledock open-tab PROFILE [URL] [--json]
```

Opens a new tab dynamically in a Playwright browser session without restarting it. If the profile is stopped, ProfileDock starts a headless Playwright controller that remains active until `profiledock close PROFILE` is run.

## `close-tab`

```text
profiledock close-tab PROFILE INDEX [--json]
```

Closes a specific tab index in an active Playwright browser session.

## `read`

```text
profiledock read PROFILE [URL] [--tab N] [--json]
```

Reads page content as formatted Markdown in the terminal using the profile's persistent authenticated session. A stopped profile is started headlessly and remains active until explicitly closed.

## `shot`

```text
profiledock shot PROFILE [URL] [--output FILE] [--tab N] [--full-page] [--json]
```

Captures a PNG screenshot of a page through the profile's persistent browser session. A stopped profile is started headlessly and remains active. `--output` chooses the destination file (default `./<profile>-<timestamp>.png`); `--full-page` captures the entire scrollable page instead of the current viewport. The output path must be a `.png` file in an existing directory; nothing is captured until navigation succeeds, so a failed navigation writes no file.

## `pdf`

```text
profiledock pdf PROFILE [URL] [--output FILE] [--tab N] [--json]
```

Exports the page as a PDF through the profile's persistent browser session. A stopped profile is started headlessly and remains active. PDF rendering requires a headless Chromium session: if the profile is running headed, the command fails with a clear message advising a close-and-retry. `--output` chooses the destination file (default `./<profile>-<timestamp>.pdf`).

## `eval`

```text
profiledock eval PROFILE SCRIPT [--tab N] [--json]
```

Evaluates a JavaScript expression in the active page context and prints the serialized result. This intentionally runs arbitrary browser-side JavaScript with the selected profile's page privileges; use only expressions you trust. Evaluation is terminated after 10 seconds. A stopped profile is started headlessly and remains active until explicitly closed.

## `cookies`

```text
profiledock cookies PROFILE [--output FILE] [--url URL] [--json]
```

Exports live session cookies directly from browser RAM, bypassing SQLite filesystem locks. Cookie output is sensitive authentication material. File output uses a private atomic JSON write and refuses links or non-file targets. A stopped profile is started headlessly and remains active until explicitly closed.

## `top`

```text
profiledock top [PROFILE] [--watch] [--interval SECONDS] [--json]
```

Live resource monitor (like `docker stats`): reports each profile's process-tree CPU %, resident memory (RSS), process count, active tabs, and total disk footprint. Running profiles report live telemetry measured over a short sampling window; stopped profiles report their disk footprint with `-` for live columns.

Options:

| Option | Meaning |
|---|---|
| `--watch`, `-w` | Continuously refresh until Ctrl+C. Defaults to on when stdout is a TTY and off when piped (also disabled by `--non-interactive`). |
| `--interval SECONDS`, `-i SECONDS` | Refresh interval in seconds (default: 1.0). |
| `--json` | Emit versioned JSON telemetry. In `--watch --json` mode a compact JSON snapshot is printed per refresh for streaming ingestion. |

```bash
profiledock top
profiledock top Personal --json
profiledock top --watch --interval 2
```

JSON `data` is an object with `interval_seconds`, `watch`, and a `profiles` array; each row carries `profile_id`, `name`, `engine`, `status`, `cpu_percent`, `memory_rss_bytes`, `process_count`, `tab_count`, and `disk_total_bytes`. A non-running profile reports `null` for the live metrics.

## Exit codes, streams, and errors

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Operational, validation, confirmation, storage, security, profile, or browser error. |
| `2` | CLI syntax or usage error. |

Human and JSON success output use stdout. Operational errors use stderr. Errors begin `Error [category]:`, and actionable failures add a `Next steps:` line on stderr suggesting the recovery command (for example, running `profiledock list` after an unknown profile). Stable categories and JSON guarantees are defined in the [CLI contract](cli-contract.md), with every payload described in the [JSON output reference](json-output.md).

## Terminal adaptation

Interactive terminals receive color and Unicode status symbols (`✓`/`!`/`✗`) on doctor checks and results; consoles or pipes that cannot render them automatically fall back to plain ASCII markers (`[ok]`/`!`/`x`). Color is disabled by `NO_COLOR`, `TERM=dumb`, non-TTY output, or `PROFILEDOCK_COLOR=never`; it can be forced in scripts with `PROFILEDOCK_COLOR=always`. Machine JSON output never contains ANSI escapes or symbols.
