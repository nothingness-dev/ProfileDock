# ProfileDock 1.0 CLI contract

This document freezes the command-line interface planned for ProfileDock 1.0. The executable contract is version 1 and is maintained in `tests/fixtures/cli/contract-v1.json`. Tagged pre-1.0 releases may add capabilities, but they must follow the compatibility and deprecation rules below.

## Command surface

The top-level commands are `create`, `list`, `show`, `rename`, `set-engine`, `status`, `launch`, `close`, `delete`, `doctor`, `migrate`, `backup`, `restore`, `logs`, and `config`.

The `config` commands are `show`, `set`, `add-url`, `remove-url`, and `reset`. `config set` accepts the setting names `default-tabs`, `engine`, `browser`, and `window-size`.

The root options are `--data-root`, `--verbose`/`-v`, `--log-level`, `--non-interactive`, `--install-completion`, `--show-completion`, and `--version`/`-V`. The complete argument requirements and option aliases are recorded in the golden contract fixture and checked against the generated Typer application in every test run.

Human output is terminal-aware: color and Unicode status symbols appear only on interactive terminals that support them, never in piped or redirected output, and are disabled by `NO_COLOR`. Machine JSON output is unaffected.

## Engine contract

The only engine values are `direct` and `playwright`.

- `create --engine` and `launch --engine` support the `-e` alias.
- `set-engine <profile> <engine>` updates profile metadata.
- `config set <profile> engine <engine>` updates the launch preset.
- Launch resolution is: explicit `launch --engine`, launch-config engine, profile engine, `PROFILEDOCK_DEFAULT_ENGINE`, then `direct`.
- Profile JSON returned by `list`, `show`, and `status` exposes the effective engine, not merely the nullable metadata value.
- Launch-config JSON exposes its independently stored engine value.

## Profile resolution

Commands accepting a profile selector resolve it in this order:

1. Exact, case-sensitive profile ID.
2. Unique, case-sensitive profile-ID prefix.
3. Exact, case-sensitive profile name.

Multiple prefix or name matches return the `ambiguous_profile` error category. Empty and unmatched selectors return `not_found`. No fuzzy or case-insensitive matching is performed.

## Data-root resolution

Every command uses the same precedence:

1. `--data-root <path>`.
2. `PROFILEDOCK_DATA_ROOT`.
3. The platform application-data default.

Relative overrides resolve from the process working directory. Unsafe filesystem roots, home directories, links, junctions, and invalid existing targets are rejected.

## Exit codes and streams

- `0`: successful command or completed no-op.
- `1`: user, operational, validation, confirmation, storage, security, profile-resolution, or browser error.
- `2`: command-line syntax or usage error generated before command execution.

Human success output, prompts, and JSON success documents use standard output. Operational errors use standard error and leave standard output empty. A machine-readable migration failure report uses the normal versioned JSON envelope on standard error and also leaves standard output empty. Usage diagnostics use Typer's usage-error stream behavior.

Operational errors begin with `Error [<category>]:`. Version 1 categories are:

- `ambiguous_profile`
- `browser_launch_failed`
- `confirmation_required`
- `corrupted_data`
- `invalid_input`
- `not_found`
- `profile_active`
- `security_violation`
- `storage_error`

Error wording may become clearer, but the category and exit-code class are the stable automation interface.

## JSON contract

Commands supporting `--json` emit JSON output version 1 with exactly this envelope:

```json
{
  "output_version": 1,
  "command": "list",
  "data": []
}
```

The JSON commands are `list`, `show`, `status`, `config show`, `doctor`, `migrate`, `backup`, `restore`, and `logs`. Consumers must reject unsupported `output_version` values. Golden output fixtures cover profile listing and profile detail, including effective-engine behavior.

Human rendering and JSON serialization use separate paths. Tests replace the human renderer with a failing implementation while asserting byte-equivalent JSON data, preventing human-output improvements from changing the JSON contract accidentally.

## Confirmation and non-interactive behavior

Profile deletion, source removal during migration, and destructive doctor repairs require confirmation unless `--yes`/`-y` is supported and supplied. Declining a Typer confirmation aborts with exit code 1 and does not mutate data.

`--non-interactive` and truthy `PROFILEDOCK_NON_INTERACTIVE` values (`1`, `true`, `yes`, or `on`) prohibit prompts. A missing confirmation fails with `confirmation_required`. Launching without an explicit or configured tab count fails with `invalid_input` and instructs the caller to use `--tabs`. JSON migration with source removal also requires `--yes` and never prompts.

Automation should always provide `--tabs`, `--yes` when required, and `--json` when machine-readable output is needed.

## Compatibility and deprecation policy

Until 1.0, additive commands and options may be introduced in minor releases. Existing command names, argument order, option meanings, aliases, exit-code classes, stream assignments, JSON version-1 fields, engine values, resolution order, precedence rules, and non-interactive semantics will not be removed or changed without deprecation.

A deprecation must:

1. Be documented in the README and this contract.
2. Preserve the old behavior for at least one minor release and at least 90 days.
3. Emit a warning only on standard error and never corrupt JSON on standard output.
4. Offer the replacement in the same release that begins deprecation.
5. Remove or alter the contract only in a major release, except for a security correction that cannot safely retain prior behavior.

Additive JSON fields require a new JSON output version because version 1 envelopes and payload fixtures are strict. A new version may be offered alongside the old version during migration; ProfileDock never silently changes the requested machine format.
