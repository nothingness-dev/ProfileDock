# Security and privacy

## Security boundary

The configured data root is ProfileDock's write and destructive-operation boundary. Managed metadata, profiles, runtime state, logs, backups, temporary directories, and quarantines must remain beneath it.

ProfileDock validates IDs before path construction, resolves destructive targets, rejects the data root itself, checks existing components for symbolic links and Windows reparse points, and rejects traversal or resolved-path escape. Backup and migration sources receive equivalent tree checks before copying.

## Isolation properties

- Every profile receives a separate Chromium user-data directory.
- ProfileDock never merges browser-data directories.
- Runtime files do not live inside browser data.
- Direct and Playwright running states have distinct, strictly versioned schemas.
- Active profiles block deletion, backup, restore overwrite, migration mutation, and relevant repair operations.

## Process and controller safety

Direct mode records PID and process creation time. Before signaling a process, ProfileDock checks liveness and identity to reduce PID-reuse risk. Ambiguous or unverifiable state fails closed.

Playwright mode uses a loopback controller with a random per-launch token, bounded command size, connection timeouts, strict commands, and constant-time token comparison. Controller availability and PID liveness are both considered before mutation.

These mechanisms do not defend against a malicious process running as the same local OS account and able to read or alter ProfileDock's files or inspect its processes.

## Archive and metadata safety

Restore treats archives as untrusted. It rejects path traversal, absolute paths, link members, special file types, duplicate names, excessive counts and sizes, malformed versioned manifests, unsafe IDs, inconsistent totals, and checksum mismatches.

Metadata and runtime formats reject unknown future versions. Persistent migrations are sequential, idempotent, backed up first, and atomically replaced. Interrupted operations preserve the authoritative original or roll back staged destination changes.

## Sensitive data

ProfileDock does not automate login or maintain a password database. Chromium may store:

- Authentication cookies and tokens.
- Saved passwords or autofill data when the browser permits it.
- Browsing history, downloads, cache, and local storage.
- Extension state and site permissions.

Anyone who can read a profile directory may be able to access or reuse sensitive browser state. Protect the data root with operating-system account permissions, disk encryption, secure backups, and appropriate device controls.

Logs may contain profile IDs, engine names, paths, timestamps, and diagnostic details. ProfileDock redacts known controller secrets and bounds captured browser errors, but callers should avoid placing secrets in URLs, profile names, paths, or command arguments.

## Operational recommendations

- Keep the data root outside source control and shared synchronization folders when possible.
- Use a dedicated OS account on shared systems.
- Close profiles before backup, migration, or maintenance.
- Store backups separately and protect them as sensitive browser data.
- Inspect archive provenance before restore.
- Run `profiledock doctor` after crashes or forced termination.
- Do not manually edit `profiles.json` or `running.json` while ProfileDock is operating.
- Do not weaken directory permissions to solve browser-launch problems.

## Limitations

ProfileDock does not provide encryption at rest, sandbox the browser, protect against websites or malicious extensions, isolate network identity, hide activity from the operating system, defend against administrators, or protect against a fully compromised same-account process.

The detailed threat-by-threat analysis is in the [threat model](../reference/threat-model.md).
