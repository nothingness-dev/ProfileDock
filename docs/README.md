# ProfileDock documentation

ProfileDock is a Python CLI for creating and operating isolated, persistent Chromium profiles. This documentation covers installation, daily use, every command, data storage, recovery, security, automation, development, and removal.

## Start here

- [Installation](guides/installation.md): requirements, automated setup, manual virtual-environment setup, and Playwright installation.
- [Getting started](guides/getting-started.md): create profiles, launch browsers, preserve sessions, and manage multiple accounts.
- [Command reference](reference/commands.md): every command, argument, option, prompt, exit behavior, and example.
- [Configuration and engines](guides/configuration.md): Direct and Playwright engines, launch presets, precedence, and data-root selection.

## Operate and protect data

- [Storage, backup, restore, and migration](guides/data-management.md): directory layout, persistence, metadata, backup archives, restore, and project-local migration.
- [Operations and troubleshooting](guides/operations.md): status, doctor, logs, updating, common failures, testing, and complete removal.
- [Security and privacy](guides/security.md): isolation guarantees, filesystem boundary, sensitive data, and operational recommendations.

## Stable interfaces

- [Planned 1.0 CLI contract](reference/cli-contract.md): commands, options, aliases, exit codes, streams, JSON, resolution, confirmation, and deprecation policy.
- [Format compatibility](reference/format-compatibility.md): metadata, runtime state, launch configuration, backup, and JSON versions.
- [JSON output reference](reference/json-output.md): command-specific machine-output payloads and stream behavior.
- [Threat model](reference/threat-model.md): protected assets, trust boundaries, reviewed threats, and limitations.

## Contributors

- [Development and testing](guides/development.md): editable installation, dependencies, tests, CI equivalents, release versioning, and project layout.

Documentation examples use `profiledock` after activating the project virtual environment. Global options such as `--data-root` and `--non-interactive` appear before the command.
