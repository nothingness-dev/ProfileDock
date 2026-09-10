# Installation

## Requirements

- Python 3.10 or newer.
- Windows, macOS, or Linux.
- Google Chrome or Chromium for the default Direct engine.
- Playwright Chromium only when using the Playwright engine or browser integration tests.

The base runtime depends on Typer and the Python standard library. Browser automation is optional.

## Automated isolated setup

From the repository root, run:

```bash
python scripts/setup_project.py
```

### What the virtual environment is and why it matters

The setup script creates a virtual environment in `.venv`: a self-contained folder with its own Python interpreter link, its own pip, and its own `site-packages` directory. Every package ProfileDock needs is installed inside `.venv/Lib/site-packages` (or `.venv/lib` on macOS and Linux) and nowhere else.

This gives you two guarantees:

- **No conflicts with your computer.** Your system Python's packages are never read or modified. Other projects and their virtual environments are untouched, and installing ProfileDock cannot break anything else on the machine.
- **No conflicts inside the project.** Package versions are resolved together at install time. With `--dev`, every dependency is pinned to the exact tested version from `requirements-dev.lock`, so a fresh clone always produces the same working set of versions — no resolution surprises, no drift over time.

The only thing a virtual environment shares with your system is the Python interpreter itself: the environment is created from whatever Python version you ran the setup script with, and it uses that interpreter's standard library. If you later uninstall or upgrade that Python installation, recreate the environment by deleting `.venv` and running the setup script again.

### Setup options

The setup script creates `.venv`, upgrades pip, and installs the minimal editable runtime from `requirements.txt`. It does not download a browser or run tests unless requested.

Useful setup options:

```bash
python scripts/setup_project.py --dev
python scripts/setup_project.py --with-playwright
python scripts/setup_project.py --dev --with-playwright
python scripts/setup_project.py --dev --with-playwright --test
```

`--dev` installs `requirements-dev.lock`, a lockfile that pins every development and test dependency to the exact tested version. It is the recommended choice for anyone working on ProfileDock or running its tests, because it guarantees a reproducible environment: the same clone on any machine gets the same library versions. `--with-playwright` installs the Playwright extra and Chromium. `--test` installs the test extra when necessary and runs pytest after setup. The options are non-interactive and may be combined.

## Manual setup on Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For development:

```powershell
pip install -r requirements-dev.lock
```

For the Playwright engine:

```powershell
pip install -e ".[playwright]"
playwright install chromium
```

If PowerShell blocks activation, either adjust the current-process execution policy or invoke `.venv\Scripts\python.exe` and `.venv\Scripts\profiledock.exe` directly.

## Manual setup on macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For development:

```bash
pip install -r requirements-dev.lock
```

For the Playwright engine:

```bash
pip install -e ".[playwright]"
playwright install chromium
```

## Verify the installation

```bash
profiledock --version
profiledock doctor
profiledock --help
```

If `profiledock` is not found, confirm the virtual environment is active or run it by its full path inside `.venv`.

## Virtual-environment lifecycle

Activate before use:

```powershell
.\.venv\Scripts\Activate.ps1
```

```bash
source .venv/bin/activate
```

Leave the environment with:

```bash
deactivate
```

See [Operations and troubleshooting](operations.md) for updating or removing the installation.
