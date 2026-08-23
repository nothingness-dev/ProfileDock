import json
from pathlib import Path
from unittest.mock import patch

from typer.main import get_command
from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.cli_contract import CLI_CONTRACT_VERSION, CLI_JSON_OUTPUT_VERSION, ERROR_CATEGORIES, EXIT_SUCCESS, EXIT_USAGE_ERROR, EXIT_USER_ERROR, error_category
from profiledock.models import LaunchConfig, Profile


runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "cli"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def parameter_contract(command):
    arguments = []
    options = {}
    for parameter in command.params:
        if parameter.param_type_name == "argument":
            arguments.append(parameter.name)
        elif parameter.param_type_name == "option" and parameter.name != "help":
            options[parameter.name] = list(parameter.opts)
    return {"arguments": arguments, "options": options}


def test_golden_command_surface_matches_typer_application():
    golden = load_fixture("contract-v1.json")
    command = get_command(app)
    assert golden["contract_version"] == CLI_CONTRACT_VERSION
    assert set(command.commands) == set(golden["commands"])
    root_options = {
        parameter.name: list(parameter.opts)
        for parameter in command.params
        if parameter.param_type_name == "option" and parameter.name != "help"
    }
    assert root_options == golden["global_options"]
    for name, expected in golden["commands"].items():
        actual = command.commands[name]
        assert parameter_contract(actual) == {
            "arguments": expected["arguments"],
            "options": expected["options"],
        }
        if name == "config":
            assert set(actual.commands) == set(expected["commands"])
            for child_name, child_expected in expected["commands"].items():
                assert parameter_contract(actual.commands[child_name]) == {
                    "arguments": child_expected["arguments"],
                    "options": child_expected["options"],
                }


def test_contract_constants_match_golden_values():
    golden = load_fixture("contract-v1.json")
    assert golden["exit_codes"] == {"success": EXIT_SUCCESS, "user_error": EXIT_USER_ERROR, "usage_error": EXIT_USAGE_ERROR}
    assert golden["json"]["output_version"] == CLI_JSON_OUTPUT_VERSION
    assert set(golden["error_categories"]) == ERROR_CATEGORIES


def test_list_json_matches_golden_and_exposes_effective_engine():
    profile = Profile(
        "abc123",
        "Work",
        "2026-01-01T00:00:00+00:00",
        "/profiles/abc123/browser-data",
        engine="direct",
        launch_config=LaunchConfig(engine="playwright"),
    )
    with patch("profiledock.cli.manager") as selected_manager, patch("profiledock.cli.get_status", return_value="stopped"):
        selected_manager.return_value.list_profiles.return_value = [profile]
        selected_manager.return_value.runtime_path.return_value = Path("/runtime/abc123")
        result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == EXIT_SUCCESS
    assert result.stderr == ""
    assert json.loads(result.stdout) == load_fixture("list-v1.json")


def test_show_json_matches_golden_and_is_independent_of_human_renderer():
    profile = Profile(
        "abc123",
        "Work",
        "2026-01-01T00:00:00+00:00",
        "/profiles/abc123/browser-data",
        engine="playwright",
    )
    with patch("profiledock.cli.manager") as selected_manager, patch("profiledock.cli.get_status", return_value="stopped"), patch("profiledock.cli._render_table", side_effect=AssertionError("human renderer used")):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = Path("/runtime/abc123")
        result = runner.invoke(app, ["show", "abc123", "--json"])
    assert result.exit_code == EXIT_SUCCESS
    assert result.stderr == ""
    assert json.loads(result.stdout) == load_fixture("show-v1.json")


def test_operational_error_uses_stderr_category_and_empty_stdout(tmp_path):
    result = runner.invoke(app, ["--data-root", str(tmp_path), "show", "missing"])
    assert result.exit_code == EXIT_USER_ERROR
    assert result.stdout == ""
    assert result.stderr == "Error [not_found]: profile not found: missing\n"


def test_usage_error_is_exit_two_and_does_not_use_operational_prefix():
    result = runner.invoke(app, ["show"])
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "Error [" not in result.output


def test_error_category_contract():
    assert error_category("ambiguous profile identifier") == "ambiguous_profile"
    assert error_category("profile is already running") == "profile_active"
    assert error_category("confirmation required; rerun with --yes") == "confirmation_required"
    assert error_category("metadata is corrupted") == "corrupted_data"
    assert error_category("unsafe path traversal") == "security_violation"


def test_non_interactive_delete_requires_yes(tmp_path):
    created = runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"])
    assert created.exit_code == EXIT_SUCCESS
    result = runner.invoke(app, ["--data-root", str(tmp_path), "--non-interactive", "delete", "Work"])
    assert result.exit_code == EXIT_USER_ERROR
    assert result.stdout == ""
    assert "Error [confirmation_required]" in result.stderr


def test_non_interactive_environment_requires_explicit_tabs(tmp_path, monkeypatch):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"])
    monkeypatch.setenv("PROFILEDOCK_NON_INTERACTIVE", "true")
    result = runner.invoke(app, ["--data-root", str(tmp_path), "launch", "Work"])
    assert result.exit_code == EXIT_USER_ERROR
    assert result.stdout == ""
    assert "use --tabs" in result.stderr


def test_confirmation_decline_aborts_without_deleting(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"])
    result = runner.invoke(app, ["--data-root", str(tmp_path), "delete", "Work"], input="n\n")
    assert result.exit_code == EXIT_USER_ERROR
    listed = runner.invoke(app, ["--data-root", str(tmp_path), "list", "--json"])
    assert len(json.loads(listed.stdout)["data"]) == 1
