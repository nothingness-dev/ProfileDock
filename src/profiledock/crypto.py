"""Optional passphrase encryption for backup archives.

Uses AES-256-GCM with a key derived via PBKDF2-HMAC-SHA256 (600,000
iterations per OWASP 2023 guidance). The ``cryptography`` package is an
optional dependency: import errors surface as a user-facing BackupError
rather than a traceback.

Envelope layout (newline-delimited header)::

    PROFILEDOCK-ENC-V1\\n
    {"version":1,"kdf":"PBKDF2-HMAC-SHA256","iterations":N,"salt":"<b64>","iv":"<b64>"}\\n
    <AES-256-GCM ciphertext (includes the 16-byte GCM tag)>

The header line doubles as GCM associated data, so any tampering with
salt, IV, or iteration count invalidates the ciphertext.
"""

import base64
import json
import os
from typing import Any

from .backup import ENCRYPTED_ARCHIVE_MAGIC, BackupError

PBKDF2_ITERATIONS = 600_000
_ENVELOPE_VERSION = 1
# Encryption/decryption buffer the whole payload in memory; archives beyond
# this cap are rejected with a clear message instead of risking exhaustion.
MAX_ENCRYPTED_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
MAX_ENCRYPTED_HEADER_BYTES = 64 * 1024


def _require_cryptography() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise BackupError(
            "encrypted backups require the 'cryptography' package; "
            "install it with: pip install profiledock[encryption]"
        ) from exc
    return AESGCM


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    derived: bytes = kdf.derive(passphrase.encode("utf-8"))
    return derived


def encrypt_payload(payload: bytes, passphrase: str) -> bytes:
    aesgcm_cls = _require_cryptography()
    if passphrase == "":
        raise BackupError("passphrase must not be empty")
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(passphrase, salt)
    header = {
        "version": _ENVELOPE_VERSION,
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
    }
    header_line = json.dumps(header, separators=(",", ":")).encode("utf-8")
    aesgcm = aesgcm_cls(key)
    ciphertext: bytes = aesgcm.encrypt(iv, payload, header_line)
    return ENCRYPTED_ARCHIVE_MAGIC + header_line + b"\n" + ciphertext


def decrypt_payload(blob: bytes, passphrase: str) -> bytes:
    aesgcm_cls = _require_cryptography()
    if not blob.startswith(ENCRYPTED_ARCHIVE_MAGIC):
        raise BackupError("not a ProfileDock encrypted archive")
    header_line, ciphertext = _split_envelope(blob)
    try:
        header = json.loads(header_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"corrupted encrypted archive header: {exc}") from exc
    if not isinstance(header, dict) or header.get("version") != _ENVELOPE_VERSION:
        raise BackupError("unsupported encrypted archive header version")
    if header.get("kdf") != "PBKDF2-HMAC-SHA256":
        raise BackupError(f"unsupported key derivation: {header.get('kdf')}")
    if not isinstance(header.get("salt"), str) or not isinstance(header.get("iv"), str):
        raise BackupError("encrypted archive header is missing salt or iv")
    try:
        salt = base64.b64decode(header["salt"])
        iv = base64.b64decode(header["iv"])
    except Exception as exc:
        raise BackupError(f"corrupted encrypted archive header: {exc}") from exc
    if len(iv) != 12:
        raise BackupError("encrypted archive header has an invalid iv length")
    key = _derive_key(passphrase, salt)
    try:
        aesgcm = aesgcm_cls(key)
        plaintext: bytes = aesgcm.decrypt(iv, ciphertext, header_line)
        return plaintext
    except Exception as exc:
        raise BackupError("decryption failed: wrong passphrase or corrupted encrypted archive") from exc


def _split_envelope(blob: bytes) -> tuple[bytes, bytes]:
    newline = blob.find(b"\n", len(ENCRYPTED_ARCHIVE_MAGIC))
    if newline < 0:
        raise BackupError("encrypted archive header is truncated")
    header_line = blob[len(ENCRYPTED_ARCHIVE_MAGIC) : newline]
    if len(header_line) > MAX_ENCRYPTED_HEADER_BYTES:
        raise BackupError("encrypted archive header is implausibly large")
    return header_line, blob[newline + 1 :]
