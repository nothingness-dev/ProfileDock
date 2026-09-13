"""Tests for optional encrypted backup archives.

Encrypted archives are a self-describing envelope: a short JSON header
followed by AES-256-GCM ciphertext of the standard .tar.gz payload. The
default archive format (plain .tar.gz) is unchanged.
"""

import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from profiledock.backup import (
    ENCRYPTED_ARCHIVE_MAGIC,
    BackupError,
    create_backup_archive,
    is_encrypted_archive,
    verify_backup_archive,
)
from profiledock.cli import EXIT_SUCCESS, app
from profiledock.data_root import DataPaths
from profiledock.models import METADATA_SCHEMA_VERSION, MetadataDocument, Profile
from profiledock.restore import restore_backup_archive
from profiledock.storage import save_metadata

runner = CliRunner()


def make_paths(root: Path) -> DataPaths:
    layout = DataPaths.from_root(root)
    layout.prepare()
    return layout


def make_profile(paths: DataPaths, pid: str = "p1", name: str = "EncryptedWork") -> Profile:
    p_data = paths.profiles_dir / pid / "browser-data"
    p_data.mkdir(parents=True, exist_ok=True)
    (p_data / "cookies.sqlite").write_text("secret-cookie-data", encoding="utf-8")
    (p_data / "Preferences").write_text("{}", encoding="utf-8")
    profile = Profile(
        id=pid,
        name=name,
        created_at="2026-01-01T00:00:00+00:00",
        data_dir=str(p_data),
        engine="direct",
    )
    doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
    save_metadata(doc, paths.profiles_file, paths.profiles_dir)
    return profile


def test_encrypt_with_passphrase_creates_envelope(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="correct horse battery staple")
    assert out.exists()
    assert is_encrypted_archive(out)
    raw = out.read_bytes()
    assert raw.startswith(ENCRYPTED_ARCHIVE_MAGIC)
    # The underlying tar must not be readable without the passphrase.
    with pytest.raises(tarfile.ReadError):
        with tarfile.open(out, "r:gz"):
            pass


def test_unencrypted_default_has_no_magic(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "plain.tar.gz"
    create_backup_archive([profile], paths, out)
    assert not is_encrypted_archive(out)
    assert out.read_bytes()[:2] == b"\x1f\x8b"


def test_restore_detects_and_decrypts(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="hunter2")
    restored = tmp_path / "restored_root"
    data_paths = DataPaths.from_root(restored)
    data_paths.prepare()
    report = restore_backup_archive(out, data_paths, passphrase="hunter2")
    assert report.total_restored == 1
    restored_file = restored / "profiles" / "p1" / "browser-data" / "cookies.sqlite"
    assert restored_file.read_text(encoding="utf-8") == "secret-cookie-data"


def test_restore_wrong_passphrase_fails(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="hunter2")
    data_paths = DataPaths.from_root(tmp_path / "restored_root")
    data_paths.prepare()
    from profiledock.restore import InvalidArchiveError

    with pytest.raises((InvalidArchiveError, BackupError)):
        restore_backup_archive(out, data_paths, passphrase="wrong")
    # Nothing may have been restored.
    assert not (tmp_path / "restored_root" / "profiles" / "p1").exists()


def test_verify_reports_encryption_without_passphrase(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="hunter2")
    with pytest.raises(BackupError):
        verify_backup_archive(out)


def test_verify_encrypted_with_passphrase(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="hunter2")
    report = verify_backup_archive(out, passphrase="hunter2")
    assert report.checksum_failures == []


def test_restore_plain_archive_backward_compatible(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "plain.tar.gz"
    create_backup_archive([profile], paths, out)
    data_paths = DataPaths.from_root(tmp_path / "restored_root")
    data_paths.prepare()
    report = restore_backup_archive(out, data_paths)
    assert report.total_restored == 1


def test_cli_backup_passphrase_flag(tmp_path):
    paths = make_paths(tmp_path)
    make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    env = {"PROFILEDOCK_DATA_ROOT": str(tmp_path), "PROFILEDOCK_NON_INTERACTIVE": "1"}
    result = runner.invoke(
        app,
        ["backup", "EncryptedWork", "--output", str(out), "--passphrase", "hunter2", "--json"],
        env=env,
    )
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert is_encrypted_archive(out)


def test_cli_restore_auto_detects_encryption(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="hunter2")
    env = {"PROFILEDOCK_DATA_ROOT": str(tmp_path / "r2"), "PROFILEDOCK_NON_INTERACTIVE": "1"}
    result = runner.invoke(
        app,
        ["restore", str(out), "--passphrase", "hunter2", "--json"],
        env=env,
    )
    assert result.exit_code == EXIT_SUCCESS, result.output


def test_env_var_passphrase(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_BACKUP_PASSPHRASE", "from-env")
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase=None, use_env_passphrase=True)
    assert is_encrypted_archive(out)


def test_empty_passphrase_rejected(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    with pytest.raises(BackupError):
        create_backup_archive([profile], paths, out, passphrase="")


def test_header_tampering_detected(tmp_path):
    paths = make_paths(tmp_path)
    profile = make_profile(paths)
    out = tmp_path / "backups" / "enc.tar.gz"
    create_backup_archive([profile], paths, out, passphrase="hunter2")
    blob = out.read_bytes()
    # Flip a character inside the base64 salt: the GCM tag must reject it.
    magic_len = len(ENCRYPTED_ARCHIVE_MAGIC)
    newline = blob.index(b"\n", magic_len)
    header = bytearray(blob[magic_len:newline])
    header[-2] = ord("A") if header[-2] != ord("A") else ord("B")
    out.write_bytes(blob[:magic_len] + bytes(header) + blob[newline:])
    data_paths = DataPaths.from_root(tmp_path / "restored_root")
    data_paths.prepare()
    from profiledock.restore import InvalidArchiveError

    with pytest.raises(InvalidArchiveError):
        restore_backup_archive(out, data_paths, passphrase="hunter2")
    assert not (tmp_path / "restored_root" / "profiles" / "p1").exists()
