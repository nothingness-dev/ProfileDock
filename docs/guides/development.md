# Development and testing

## Development environment

```bash
python scripts/setup_project.py --dev --with-playwright --test
```

Or install manually:

```bash
pip install -r requirements-dev.lock
pip install -e ".[playwright]"
playwright install chromium
```

`requirements.txt` contains runtime requirements. `requirements-dev.lock` contains reproducible development and test requirements. `pyproject.toml` remains the package metadata and optional-extra source.

Regenerate the lockfile only after intentional dependency changes:

```bash
pip install -e ".[dev]"
pip freeze > requirements-dev.lock
```

Review the diff before committing; platform-specific pins differ between operating systems.

## Test suite

Run all tests:

```bash
python -m pytest -q
```

Run unit tests without browser-marked integration tests:

```bash
python -m pytest -q -m "not browser"
```

Run browser tests after installing Playwright Chromium or a supported system browser:

```bash
python -m pytest -q -m browser
```

Tests use temporary data roots and must never write into real user application-data directories. Controller tests create and terminate their own processes.

### Test isolation budget

The unit suite (`-m "not browser"`) targets sub-second isolation: every unit test should complete in well under 0.5 seconds of call time, keeping the full unit run around 15 seconds on developer hardware. Benchmark with:

```bash
python -m pytest -q -m "not browser" --durations=25
```

Reference numbers (Windows 11, Python 3.12): unit suite ≈14–15 s for 305 tests; median unit test <0.05 s. The only unit tests approaching the 0.5 s line are the startup-timeout process test (~0.6 s, dominated by a deliberate 0.2 s timeout plus process teardown) and a CLI round-trip test that performs 10 sequential invocations (~0.6 s total). Browser-marked tests are exempt from the budget; they launch real processes and take up to ~11 s each.

If a new unit test appears above the 1 s mark in `--durations`, look for accidental sleeps, redundant metadata migrations per assertion, or filesystem work outside `tmp_path` before accepting the cost.

## Important coverage

The suite covers profile lifecycle, resolver precedence, data-root platforms and precedence, metadata locking and migrations, engine selection, launch configuration, runtime protocol validation, duplicate launch detection, process identity, controller authentication, logging, doctor and repair behavior, backup and restore security, migration rollback, symlink and junction boundaries, historical format fixtures, and golden CLI contracts.

## Golden CLI fixtures

`tests/fixtures/cli/contract-v1.json` freezes commands, arguments, options, aliases, exit codes, streams, JSON commands, engine precedence, profile resolution, data-root precedence, confirmations, non-interactive behavior, and error categories.

Golden JSON outputs ensure human rendering changes cannot modify machine output accidentally. Update a golden fixture only when intentionally versioning the relevant contract and documentation.

## Linting and type checking

Ruff (lint and format) and mypy (strict mode) are configured in `pyproject.toml`. Both run against `src/profiledock`, `tests`, and `scripts`:

```bash
ruff check src tests scripts
ruff format --check src tests scripts
mypy
```

`mypy` runs in strict mode with no errors; keep it that way for new code. Ruff ignores document the codebase's deliberate patterns: best-effort try/except-pass cleanup paths, blind excepts at browser/controller teardown boundaries, and typer's call-in-default idiom. Per-file ignores cover long diagnostic strings in doctor, migration, restore, and tests.

## Project layout

```text
profiledock/
├── docs/
├── scripts/
├── src/profiledock/
├── tests/
├── pyproject.toml
├── requirements.txt
└── requirements-dev.lock
```

The package keeps models, storage, profile operations, browser/process control, backup, restore, migration, diagnostics, logging, validation, CLI behavior, and version constants in focused modules without adding unnecessary framework layers.

## Verification before release

```bash
python -m compileall -q src tests
ruff check src tests scripts && ruff format --check src tests scripts
mypy
python -m pytest -q
git diff --check
profiledock --version
```

Inspect the worktree and commit history. Documentation links, JSON fixtures, package version, release commit, and annotated tag must agree.

## Versioning

ProfileDock uses semantic versioning and tags releases as `vMAJOR.MINOR.PATCH`. New backward-compatible functionality uses a minor release; fixes use a patch release; incompatible stable-contract changes require a major release and the documented deprecation process.

The planned 1.0 CLI surface is frozen in the [CLI contract](../reference/cli-contract.md). Persistent-format compatibility is documented separately in [Format compatibility](../reference/format-compatibility.md).
