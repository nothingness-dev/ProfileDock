# ProfileDock

ProfileDock is a lightweight Python CLI for managing isolated, persistent Chromium profiles. Every profile has its own browser user-data directory, preserving cookies, sessions, local storage, cache, history, and login state independently.

## Features

- Direct Chrome, Chromium, or Brave launches with no automation dependency.
- Optional Playwright persistent contexts with exact page counts.
- Create, list, inspect, configure, launch, close, rename, and delete profiles.
- Versioned backup, restore, legacy migration, diagnostics, repair, logs, and JSON output.
- Platform application-data storage with configurable data roots.
- Transactional metadata and filesystem operations with strict security boundaries.
- Frozen planned 1.0 CLI contract and tested historical format compatibility.

## Quick installation

Python 3.9 or newer is required.

```bash
python scripts/setup_project.py
```

Manual setup:

```bash
python -m venv .venv
```

Activate `.venv`, then run:

```bash
pip install -r requirements.txt
pip install -e .
```

For the Playwright engine:

```bash
pip install -e ".[playwright]"
playwright install chromium
```

## Quick start

```bash
profiledock create "Personal" --engine direct
profiledock create "Work" --engine playwright
profiledock list
profiledock launch Personal --tabs 3
profiledock close Personal
profiledock launch Personal --tabs 3
```

Login is always manual. Relaunching the same profile reuses its persistent browser data.

## Core commands

```text
profiledock create NAME
profiledock list
profiledock show PROFILE
profiledock config show PROFILE
profiledock status [PROFILE]
profiledock launch PROFILE [--tabs N]
profiledock close PROFILE
profiledock rename PROFILE NEW_NAME
profiledock set-engine PROFILE ENGINE
profiledock backup PROFILE --output ARCHIVE [--exclude-cache]
profiledock restore ARCHIVE
profiledock migrate --from-project PATH
profiledock doctor
profiledock logs [PROFILE]
profiledock delete PROFILE
```

Run `profiledock --help` or read the [complete command reference](docs/reference/commands.md) for every argument, option, alias, exit code, stream, confirmation, and JSON behavior.

## Data location

- Windows: `%LOCALAPPDATA%\ProfileDock`
- macOS: `~/Library/Application Support/ProfileDock`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/profiledock`

Override with global `--data-root PATH` or `PROFILEDOCK_DATA_ROOT`. The CLI option has highest precedence.

## Documentation

The [full documentation](docs/README.md) covers:

- [Installation](docs/guides/installation.md) and [getting started](docs/guides/getting-started.md)
- [Every command](docs/reference/commands.md)
- [Configuration and browser engines](docs/guides/configuration.md)
- [Storage, backup, restore, migration, and recovery](docs/guides/data-management.md)
- [Operations, troubleshooting, updates, and complete removal](docs/guides/operations.md)
- [Security and privacy](docs/guides/security.md) and the [threat model](docs/reference/threat-model.md)
- [Planned 1.0 CLI compatibility contract](docs/reference/cli-contract.md)
- [Versioned format compatibility](docs/reference/format-compatibility.md)
- [Command-specific JSON output schemas](docs/reference/json-output.md)
- [Development and testing](docs/guides/development.md)

## Security note

Browser-data directories contain sensitive session information. Keep the selected data root private, protect backups, and close profiles before backup or migration. ProfileDock does not encrypt browser data or protect against another malicious process running as the same OS account.

## Development

```bash
python scripts/setup_project.py --dev --with-playwright
python -m pytest -q
```

## License

ProfileDock is licensed under the [MIT License](LICENSE).
