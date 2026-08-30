# JSON output reference

## Envelope

Commands supporting `--json` emit output version 1:

```json
{
  "output_version": 1,
  "command": "list",
  "data": []
}
```

The top-level fields are exactly `output_version`, `command`, and `data`. Consumers must check `output_version` before interpreting `data`. JSON success uses stdout. Operational errors normally use categorized human text on stderr; migration JSON failure reports use the versioned envelope on stderr and leave stdout empty.

Human formatting and JSON serialization are independent. Tables, labels, spacing, and other human-output improvements do not change JSON fixtures.

## Profile object

`list` and `show` profile objects expose the effective engine after launch-config, profile, environment, and default precedence:

```json
{
  "id": "abc123",
  "name": "Work",
  "created_at": "2026-01-01T00:00:00+00:00",
  "data_dir": "/path/to/profiles/abc123/browser-data",
  "last_launched_at": null,
  "engine": "playwright",
  "status": "stopped"
}
```

When a launch configuration exists, `launch_config` is also present and follows its versioned schema.

## `list --json`

`command` is `list`. `data` is an array of profile objects and is empty when no profiles exist.

## `show PROFILE --json`

`command` is `show`. `data` is one profile object. Since the resource-monitoring release, `data` also always carries a `metrics` object:

```json
{
  "profile_id": "abc123",
  "name": "Work",
  "engine": "direct",
  "status": "stopped",
  "live": null,
  "storage": {
    "total_bytes": 1048576,
    "browser_data_bytes": 786432,
    "cache_bytes": 204800,
    "cookies_storage_bytes": 8192,
    "logs_bytes": 49152
  }
}
```

The `storage` object is always present. `live` is `null` when the profile is not verifiably running; otherwise it reports:

- `status` — `running`, `stopped`, or `degraded`
- `total_cpu_percent` — aggregated process-tree CPU over the sampling window
- `total_memory_rss_bytes` — resident memory (working set on Windows) across the tree
- `process_count` — number of sampled processes
- `processes` — per-process rows with `pid`, `name` (`browser`|`renderer`|`gpu`|`utility`|`controller`), `cpu_percent`, `memory_rss_bytes`, `memory_vms_bytes`
- `tab_count` — active tabs when known, else `null`

## `status [PROFILE] --json`

`command` is `status`. `data` is always an array, including when one profile was selected:

```json
{
  "output_version": 1,
  "command": "status",
  "data": [
    {
      "id": "abc123",
      "name": "Work",
      "engine": "direct",
      "status": "running"
    }
  ]
}
```

With `--metrics`/`-m`, each item gains a `metrics` key carrying the same object described under `show PROFILE --json`; the default (no `--metrics`) payload is unchanged.

## `top [PROFILE] --json`

`command` is `top`. `data` is an object with `interval_seconds`, `watch`, and a `profiles` array. Each row:

```json
{
  "profile_id": "abc123",
  "name": "Work",
  "engine": "direct",
  "status": "running",
  "cpu_percent": 12.5,
  "memory_rss_bytes": 1234567,
  "process_count": 4,
  "tab_count": 3,
  "disk_total_bytes": 1048576
}
```

Live columns (`cpu_percent`, `memory_rss_bytes`, `process_count`, `tab_count`) are `null` for non-running profiles; `disk_total_bytes` is always present. In `--watch --json` mode one compact (non-indented) snapshot is emitted per refresh as a newline-delimited stream.

## `config show PROFILE --json`

`command` is `config show`. `data` follows launch-configuration schema version 1:

```json
{
  "schema_version": 1,
  "default_tabs": 4,
  "start_urls": ["https://example.com"],
  "engine": "playwright",
  "browser": "chromium",
  "window_width": 1440,
  "window_height": 900
}
```

The config `engine` is the stored preset and may be `null`; it is not replaced by the effective profile engine.

## `doctor --json`

`command` is `doctor`. `data` contains `checks`, `repairs`, and `healthy`:

```json
{
  "checks": [
    {
      "id": "python_version",
      "status": "ok",
      "summary": "Python version is supported."
    }
  ],
  "repairs": [],
  "healthy": true
}
```

Each diagnostic has `id`, `status`, and `summary`, with optional `action`. Status is `ok`, `warning`, or `failed`.

## `migrate --json`

`command` is `migrate`. `data` contains source and destination roots plus `migrated`, `skipped`, and `failed` result arrays. Each result includes profile identity when available, status, and message. Successful and idempotently skipped operations exit 0 unless failures remain. Failure reports exit 1 and are written as JSON to stderr.

## `backup --json`

`command` is `backup`. `data` contains:

- `output_path`
- `format_version`
- `profiledock_version`
- `created_at`
- `total_profiles`
- `total_files`
- `total_bytes`
- `profiles`

Each profile result includes `id`, `name`, nullable metadata `engine`, status, file count, byte count, and message.

## `restore --json`

`command` is `restore`. `data` contains archive path, archive format and producing ProfileDock versions, restored counts and totals, plus `restored` and `skipped` profile result arrays.

## `logs --json`

`command` is `logs`. `data` is an array of structured entries:

```json
{
  "timestamp": "2026-01-01T00:00:00+00:00",
  "level": "INFO",
  "event": "profile_launched",
  "correlation_id": "abc123",
  "profile_id": "profile-id",
  "engine": "playwright",
  "details": {}
}
```

Optional values depend on the event. Known secrets are redacted before storage and output.

## Live Playwright commands

`tabs --json` returns an array of objects with `index`, `title`, and `url`.

`open-tab --json` returns one tab object with `index`, `title`, and `url`.

`close-tab --json` returns `index` and `remaining_tabs`.

`read --json` returns `url`, `title`, Markdown `content`, and a `links` array. Each link has `index`, `text`, and `url`.

`eval --json` returns an object containing the serialized `result` value.

`cookies --json` returns an array of Playwright cookie objects when writing to stdout. With `--output`, data contains the absolute `output_file` and exported cookie `count`; the sensitive cookies are written only to that private JSON file.

## Errors and forward compatibility

CLI syntax failures exit 2 and are not JSON documents because parsing fails before command execution. Operational failures exit 1. Automation must use exit status in addition to parsing output.

Adding fields to strict version-1 payloads requires a new JSON output version. ProfileDock does not silently alter the requested machine format. See the [CLI contract](cli-contract.md) and [format compatibility](format-compatibility.md).
