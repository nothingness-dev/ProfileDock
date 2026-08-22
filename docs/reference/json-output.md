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

`command` is `show`. `data` is one profile object.

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

## Errors and forward compatibility

CLI syntax failures exit 2 and are not JSON documents because parsing fails before command execution. Operational failures exit 1. Automation must use exit status in addition to parsing output.

Adding fields to strict version-1 payloads requires a new JSON output version. ProfileDock does not silently alter the requested machine format. See the [CLI contract](cli-contract.md) and [format compatibility](format-compatibility.md).
