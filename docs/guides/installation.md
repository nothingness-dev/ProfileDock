# Installation

## Requirements

- Python 3.9 or newer.
- Windows, macOS, or Linux.
- Google Chrome, Chromium, or Brave for the default Direct engine.
- Playwright Chromium only when using the Playwright engine or browser integration tests.

The base runtime depends on Typer and the Python standard library. Browser automation is optional.

## Automated isolated setup

From the repository root, run:

```bash
python scripts/setup_project.py
```

The setup script creates `.venv`, upgrades pip, and installs the minimal editable runtime from `requirements.txt`. It does not download a browser or run tests unless requested.

Useful setup options:

```bash
python scripts/setup_project.py --dev
python scripts/setup_project.py --with-playwright
python scripts/setup_project.py --dev --with-playwright
python scripts/setup_project.py --dev --with-playwright --test
```

`--dev` installs `requirements-dev.lock`. `--with-playwright` installs the Playwright extra and Chromium. `--test` installs the test extra when necessary and runs pytest after setup. The options are non-interactive and may be combined.

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
