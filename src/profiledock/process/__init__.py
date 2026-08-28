"""Process management for ProfileDock.

Split out of the former single-module ``process_manager`` implementation:

- ``errors``    — exception types shared by all process handling
- ``state``     — runtime state files (paths, atomic writes, validation)
- ``identity``  — process identity, discovery and termination primitives
- ``ipc``       — controller client communication
- ``direct``    — direct Chrome engine lifecycle
- ``playwright``— Playwright engine launcher lifecycle
- ``controller``— controller subprocess entry point (IPC server side)
- ``manager``   — status, running checks and close orchestration

``profiledock.process_manager`` remains the stable import surface and the
``python -m`` entry point; it re-exports everything from this package.
"""
