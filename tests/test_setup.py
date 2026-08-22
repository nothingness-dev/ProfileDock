import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent


def test_runtime_requirements_install_only_base_project():
    lines = [line.strip() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == ["-e ."]


def test_setup_script_exposes_documented_non_interactive_options():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "setup_project.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--dev" in result.stdout
    assert "--with-playwright" in result.stdout
    assert "--test" in result.stdout


def test_setup_documentation_matches_script_flags():
    documentation = (ROOT / "docs" / "guides" / "installation.md").read_text(encoding="utf-8")
    for option in ("--dev", "--with-playwright", "--test"):
        assert option in documentation
