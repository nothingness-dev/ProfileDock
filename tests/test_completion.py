import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from profiledock import completion
from profiledock.cli import app
from profiledock.cli_contract import EXIT_SUCCESS, EXIT_USAGE_ERROR

runner = CliRunner()


def test_importing_cli_registers_runtime_completion_classes():
    from typer._click.shell_completion import get_completion_class

    for shell in ("bash", "zsh", "fish", "powershell", "pwsh"):
        assert get_completion_class(shell) is not None, shell


def _fake_profile_probe(profile_path: Path) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=str(profile_path).encode("windows-1252"))


def test_powershell_script_registers_completer_without_side_effects():
    script = completion.get_completion_script(
        prog_name="profiledock", complete_var="_PROFILEDOCK_COMPLETE", shell="powershell"
    )
    assert "Register-ArgumentCompleter -Native -CommandName profiledock" in script
    assert "Set-ExecutionPolicy" not in script
    assert "Set-PSReadLineKeyHandler" not in script
    assert "Import-Module PSReadLine" not in script


def test_every_supported_shell_generates_a_script():
    scripts = {
        shell: completion.get_completion_script(
            prog_name="profiledock", complete_var="_PROFILEDOCK_COMPLETE", shell=shell
        )
        for shell in completion.SUPPORTED_SHELLS
    }
    assert all(scripts.values())
    assert len({scripts["bash"], scripts["zsh"], scripts["fish"], scripts["powershell"]}) == 4
    assert scripts["pwsh"] == scripts["powershell"]


def test_unknown_shell_is_rejected_as_usage_error():
    result = runner.invoke(app, ["--show-completion", "tcsh"])
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "tcsh" in result.output


def test_show_completion_prints_script_and_exits_successfully():
    result = runner.invoke(app, ["--show-completion", "powershell"])
    assert result.exit_code == EXIT_SUCCESS
    assert "Register-ArgumentCompleter" in result.stdout


def test_install_powershell_targets_all_hosts_profile_once(tmp_path):
    profile = tmp_path / "profile.ps1"
    with patch.object(completion.subprocess, "run", return_value=_fake_profile_probe(profile)):
        shell, path = completion.install("powershell")
        assert shell == "powershell"
        assert path == profile
        first = profile.read_text(encoding="utf-8")
        assert first.count("# --- PROFILEDOCK-COMPLETION ---") == 1
        completion.install("powershell")
    assert profile.read_text(encoding="utf-8") == first


def test_cli_install_uses_all_hosts_profile(tmp_path):
    profile = tmp_path / "WindowsPowerShell" / "profile.ps1"
    with patch.object(completion.subprocess, "run", return_value=_fake_profile_probe(profile)):
        result = runner.invoke(app, ["--install-completion", "powershell"])
    assert result.exit_code == EXIT_SUCCESS
    assert f"installed in {profile}" in result.stdout
    content = profile.read_text(encoding="utf-8")
    assert "$Env:_PROFILEDOCK_COMPLETE" in content
    assert "Set-ExecutionPolicy" not in content


def test_windows_detection_falls_back_to_powershell(monkeypatch):
    monkeypatch.setattr(completion, "_detect_shell", lambda: None)
    monkeypatch.setattr(completion.sys, "platform", "win32")
    assert completion._resolve_shell(None) == "powershell"


def test_detection_failure_off_windows_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(completion, "_detect_shell", lambda: None)
    monkeypatch.setattr(completion.sys, "platform", "linux")
    try:
        completion._resolve_shell(None)
    except completion.CompletionError as exc:
        assert "choose from" in str(exc)
    else:
        raise AssertionError("expected CompletionError")


def test_bash_install_writes_rc_source_line(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(completion.Path, "home", classmethod(lambda cls: home))
    shell, path = completion.install("bash")
    assert shell == "bash"
    assert (home / ".bashrc").read_text(encoding="utf-8").count("source '") == 1
    assert path.read_text(encoding="utf-8").startswith("_profiledock_completion()")
