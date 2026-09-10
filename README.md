# ProfileDock

ProfileDock is a lightweight Python CLI for managing isolated, persistent Chromium profiles. Every profile has its own browser user-data directory, preserving cookies, sessions, local storage, cache, history, and login state independently. It also automates through each profile's authenticated session (read, screenshot, PDF, JavaScript, cookies), pins per-profile identity presets (proxy, user agent, locale, timezone), and monitors live resource usage.

## Installation

Python 3.10 or newer is required. Google Chrome or Chromium is required for the default Direct engine. From the cloned repository root:

```bash
python scripts/setup_project.py --dev
```

This creates an isolated `.venv` — nothing touches your system Python — and installs the exact pinned dependency versions from `requirements-dev.lock`. Activate it, then verify:

```powershell
.\.venv\Scripts\Activate.ps1
```

```bash
source .venv/bin/activate
```

```bash
profiledock --version
```

For the Playwright engine add:

```bash
python scripts/setup_project.py --with-playwright
```

Manual setup, per-platform commands, and troubleshooting: [Installation guide](docs/guides/installation.md).

## Quick start

```bash
profiledock create "Personal" --engine direct
profiledock create "Work" --engine playwright
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
profiledock launch PROFILE [--tabs N]
profiledock close PROFILE
profiledock status [PROFILE]
profiledock top [PROFILE]
profiledock read PROFILE [URL]
profiledock shot PROFILE [URL] [--full-page]
profiledock pdf PROFILE [URL]
profiledock eval PROFILE SCRIPT
profiledock cookies PROFILE [--output FILE]
profiledock config set PROFILE SETTING VALUE
profiledock backup PROFILE --output ARCHIVE
profiledock restore ARCHIVE
profiledock doctor
profiledock delete PROFILE
```

Full command list with every argument, option, alias, exit code, and JSON behavior: [command reference](docs/reference/commands.md).

## Documentation

The [full documentation](docs/README.md) covers:

- [Installation](docs/guides/installation.md) and [getting started](docs/guides/getting-started.md)
- [Every command](docs/reference/commands.md)
- [Configuration and browser engines](docs/guides/configuration.md)
- [Storage, backup, restore, migration, and recovery](docs/guides/data-management.md)
- [Operations, troubleshooting, updates, and complete removal](docs/guides/operations.md)
- [Security and privacy](docs/guides/security.md) and the [threat model](docs/reference/threat-model.md)
- [Planned 1.0 CLI compatibility contract](docs/reference/cli-contract.md)
- [Command-specific JSON output schemas](docs/reference/json-output.md)
- [Development and testing](docs/guides/development.md)

## Security note

Browser-data directories contain sensitive session information. Keep the data root private, protect backups, and close profiles before backup or migration.

## Development

```bash
python scripts/setup_project.py --dev --with-playwright
python -m pytest -q
```

## License

ProfileDock is licensed under the [MIT License](LICENSE).
