# ProfileDock

ProfileDock is a lightweight CLI for managing isolated, persistent Chromium profiles. Each profile has its own cookies, sessions, local storage, cache, login state, and Chromium user-data directory.

Current release: `0.1.2`

## Project setup

Python 3.9 or newer is required. The setup script creates an isolated `.venv`, installs the project and test dependencies from `requirements.txt`, prepares a compatible browser, and runs the test suite. It uses Google Chrome when installed and otherwise installs Playwright Chromium.

```bash
python scripts/setup_project.py
```

If Chromium is already installed or its download is unavailable, skip that step:

```bash
python scripts/setup_project.py --skip-browser
```

## Manual installation

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
```

On macOS or Linux, use `.venv/bin/python` instead.

## Usage

```bash
profiledock create "Personal"
profiledock list
profiledock launch <id>
profiledock launch <id> --tabs 5
profiledock close <id>
profiledock delete <id>
```

`launch` asks for the number of tabs and opens that many `about:blank` tabs. `--tabs N` can be used for scripts. The browser remains controlled by a small local controller so `close` can gracefully shut it down from another terminal.

Profile metadata is stored in `profiles.json`; browser data is stored under `profiles/<id>/browser-data/`. These directories are independent, so logging into one profile does not affect another. ProfileDock does not automate login and never stores passwords itself. Chromium and websites may still store credentials according to their own settings; protect the profile data directories accordingly.

## Versioning

ProfileDock follows Semantic Versioning. Stable releases use Git tags such as `v0.1.2`.

## Development

```bash
.venv\Scripts\python -m pytest
```

The browser integration test uses Playwright Chromium or an installed Google Chrome. It is skipped when neither browser is available.
