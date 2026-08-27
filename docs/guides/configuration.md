# Configuration and browser engines

## Engine choices

### Direct engine

`direct` is the default. It launches an installed Google Chrome or Chromium executable directly and requires no Playwright package.

```bash
profiledock create Work --engine direct
profiledock launch Work --tabs 3 --engine direct
```

Direct mode tracks the browser PID and creation time to avoid terminating an unrelated process after PID reuse. Browser startup preferences can affect the exact visible tab count.

### Playwright engine

`playwright` uses `launch_persistent_context()` with the profile's independent user-data directory. It supports an exact page count and graceful controller-based close.

```bash
pip install -e ".[playwright]"
playwright install chromium
profiledock launch Work --tabs 3 --engine playwright
```

If Playwright Chromium is unavailable, ProfileDock attempts supported installed browser fallbacks and reports a concise launch error.

## Engine precedence

Launch engine resolution is:

1. `profiledock launch <profile> --engine ...`.
2. The profile's launch-config engine.
3. The profile metadata engine.
4. `PROFILEDOCK_DEFAULT_ENGINE`.
5. `direct`.

Only `direct` and `playwright` are valid. JSON profile output exposes the effective resolved engine.

## Profile engine commands

Set the profile-level default while creating:

```bash
profiledock create Work --engine playwright
```

Change it later:

```bash
profiledock set-engine Work direct
```

Set a launch-preset override:

```bash
profiledock config set Work engine playwright
```

## Launch configuration

Show the preset:

```bash
profiledock config show Work
profiledock config show Work --json
```

Set supported values:

```bash
profiledock config set Work default-tabs 4
profiledock config set Work engine playwright
profiledock config set Work browser chromium
profiledock config set Work window-size 1440x900
```

`default-tabs` must be at least 1. `window-size` must contain width and height of at least 100. `browser` may be a supported browser name or an executable path accepted by the selected engine.

Manage start URLs:

```bash
profiledock config add-url Work https://example.com
profiledock config remove-url Work https://example.com
```

Reset the complete launch configuration:

```bash
profiledock config reset Work
```

## Per-launch overrides

```bash
profiledock launch Work \
  --tabs 3 \
  --engine playwright \
  --browser chromium \
  --url https://example.com
```

Options are `--tabs`/`-t`, `--engine`/`-e`, `--browser`/`-b`, and repeatable `--url`/`-u`. The number of URLs cannot exceed the tab count.

## Data-root configuration

The data-root precedence is:

1. Global `--data-root <path>`.
2. `PROFILEDOCK_DATA_ROOT`.
3. Platform default.

Defaults:

- Windows: `%LOCALAPPDATA%\ProfileDock`
- macOS: `~/Library/Application Support/ProfileDock`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/profiledock`

Examples:

```bash
profiledock --data-root /secure/profiledock-data list
```

```powershell
$env:PROFILEDOCK_DATA_ROOT = "D:\Private\ProfileDock"
profiledock list
```

Relative override paths resolve from the current working directory. ProfileDock rejects filesystem roots, the home directory, links, junctions, and unsafe existing roots.

## Non-interactive mode

Use the global option or environment variable:

```bash
profiledock --non-interactive launch Work --tabs 3
```

```bash
PROFILEDOCK_NON_INTERACTIVE=1 profiledock launch Work --tabs 3
```

Truthy environment values are `1`, `true`, `yes`, and `on`. Missing tab counts and confirmations fail instead of prompting. Automation should provide `--yes` for destructive operations and `--json` for machine output.
