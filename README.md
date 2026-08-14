# ProfileDock

ProfileDock is a lightweight CLI for managing isolated, persistent Chromium profiles. Each profile has its own cookies, sessions, local storage, cache, login state, and Chromium user-data directory.

Current release: `0.1.0`

## Install

```bash
pip install -e .
playwright install chromium
```

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

ProfileDock follows Semantic Versioning. Stable releases use Git tags such as `v0.1.0`.

## Development

```bash
pip install -e ".[test]"
pytest
```

The Chromium integration test is skipped when the Playwright Chromium binary is not installed.
