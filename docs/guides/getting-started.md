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

## Optional: interactive mode

Running `profiledock` with no command opens a full-screen, keyboard-driven control center instead of a plain menu. Requires the optional Textual extra:

```bash
pip install "profiledock[interactive]"
```

The interface has two panes plus global bars:

- **Command Deck (left)** — every command grouped by section (Profile Lifecycle, Configuration, Backup & Data) with keybind, glyph, and description columns. Press `/` to fuzzy-filter in place.
- **Inspector (right)** — a live profile rail with running/PID badges, telemetry cards for the highlighted profile (engine, data dir, disk usage, launch preset), and an amber preview of the highlighted command.
- **Header** — workspace badge, title, and live telemetry (running count, storage, engine). **Footer** — breadcrumb plus contextual key chips.

Commands with parameters open interactive forms instead of text prompts: a fuzzy profile picker with status badges, radio pickers for engines and auto-detected browsers (with versions), and a checkbox configurator for Chromium launch flags. A live `profiledock …` preview line assembles as you type. The launch form keeps things simple — pick a profile, optionally set a tab count, done. Engine, browser, flags, and start URLs are tucked behind `Ctrl+O` (Advanced). Pressing `Enter` on a profile in the rail launches it. Destructive actions (`delete`, `restore`) require an explicit confirmation modal — deleting a profile demands re-typing its name.

Keys: `j`/`k` or `↑`/`↓` navigate, `Enter` runs, `Tab`/`Shift+Tab` switch panes and form fields, `Esc` steps back, `/` filters commands, `t` cycles themes (obsidian, tokyo-night, catppuccin-mocha, nord), and single letters run commands instantly (`l` list, `s` status, `d` doctor, `g` logs, `c` create, `i` show, `o` launch, `w` close, `r` rename, `e` set-engine, `b` backup, `u` restore, `x` delete, `q` quit). Set `PROFILEDOCK_ICONS=1` for Nerd Font glyphs. Mouse selection works everywhere — one click runs a command or picks a form option.

Without the extra (or in scripts/pipes), bare `profiledock` prints the usage summary as before. `--non-interactive` disables the shell entirely.
