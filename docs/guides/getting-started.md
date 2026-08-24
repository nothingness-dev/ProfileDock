# Getting started

## Create two isolated profiles

```bash
profiledock create "Personal" --engine direct
profiledock create "Work" --engine playwright
profiledock list
```

Each command creates metadata plus an independent `browser-data` directory. Cookies, sessions, local storage, cache, history, and login state never share a Chromium user-data directory between profiles.

## Launch a profile

Interactive launch asks for a tab count when no value or preset exists:

```bash
profiledock launch Personal
```

For explicit or automated launch:

```bash
profiledock launch Personal --tabs 3
profiledock launch Work --tabs 4 --engine playwright
```

Use `--url` repeatedly to assign starting pages. Remaining tabs open `about:blank`:

```bash
profiledock launch Work --tabs 3 --url https://example.com --url https://github.com
```

Browse and sign in manually. ProfileDock does not automate authentication or store passwords itself.

## Close and relaunch

```bash
profiledock close Personal
profiledock close Work
profiledock launch Personal --tabs 3
profiledock launch Work --tabs 4
```

The browser session persists in each profile's `browser-data`. Closing ProfileDock or changing the current working directory does not change the selected application-data location.

## Verify isolation manually

1. Create Profile A and Profile B.
2. Launch A and sign in to one account.
3. Launch B and sign in to a different account.
4. Close both profiles.
5. Relaunch both.

Each profile should retain only its own account. Never copy files between active browser-data directories.

## Inspect profiles

```bash
profiledock list
profiledock show Personal
profiledock status
profiledock status Work
```

Every profile selector accepts an exact ID, a unique ID prefix, or an exact name. Matching is case-sensitive.

## Configure repeatable launches

```bash
profiledock config set Work default-tabs 4
profiledock config set Work engine playwright
profiledock config set Work browser chromium
profiledock config set Work window-size 1440x900
profiledock config add-url Work https://example.com
profiledock config show Work
```

Explicit launch options override stored presets for one launch. See [Configuration and engines](configuration.md) for precedence and validation.

## Back up before important changes

```bash
profiledock close Work
profiledock backup Work --output work-profile.tar.gz
```

Profiles must be stopped for a consistent backup. See [Data management](data-management.md) before restoring, migrating, or deleting data.

## Optional: shell completion

Enable tab completion for commands and options in your shell:

```bash
profiledock --install-completion
```

Supports bash, zsh, fish, and PowerShell (`--install-completion <shell>` selects one explicitly). On Windows the completer is registered in the all-hosts PowerShell profile, so every host picks it up — the classic console, the VS Code integrated terminal, and ISE alike. Installation never changes your execution policy or Tab key bindings. Open a new terminal window after installing.
