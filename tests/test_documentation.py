import re
from pathlib import Path

from typer.main import get_command

from profiledock.cli import app

ROOT = Path(__file__).parent.parent


def markdown_files():
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_documentation_links_resolve():
    pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for document in markdown_files():
        for target in pattern.findall(document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path_value = target.split("#", 1)[0]
            assert (document.parent / path_value).resolve().exists(), f"broken link in {document}: {target}"


def test_command_reference_covers_complete_cli_surface():
    command = get_command(app)
    reference = (ROOT / "docs" / "reference" / "commands.md").read_text(encoding="utf-8")
    for name in command.commands:
        assert f"## `{name}`" in reference or name == "config"
    for name in command.commands["config"].commands:
        assert f"## `config {name}`" in reference


def test_readme_is_a_minimal_documentation_entry_point():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.splitlines()) <= 150
    assert "docs/README.md" in readme
    assert "docs/reference/commands.md" in readme


def test_standalone_reference_documents_are_consolidated():
    assert not (ROOT / "CLI_CONTRACT.md").exists()
    assert not (ROOT / "FORMAT_COMPATIBILITY.md").exists()
    assert not (ROOT / "THREAT_MODEL.md").exists()
