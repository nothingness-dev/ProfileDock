import pytest

from profiledock.mcp_server import validate_tool_arguments


def test_valid_arguments_pass_through():
    args = {"profile_id": "work", "script": "1+1", "tab_index": 2}
    assert validate_tool_arguments("profile_eval", args) == args


def test_rejects_empty_profile_id():
    with pytest.raises(ValueError, match="profile_id must not be empty"):
        validate_tool_arguments("profile_eval", {"profile_id": " ", "script": "x"})


def test_rejects_missing_required_argument():
    with pytest.raises(ValueError, match="missing required tool argument"):
        validate_tool_arguments("profile_eval", {"profile_id": "p"})


def test_rejects_unknown_argument():
    with pytest.raises(ValueError, match="unknown tool argument"):
        validate_tool_arguments("profile_eval", {"profile_id": "p", "script": "x", "bogus": 1})


def test_rejects_wrong_argument_type():
    with pytest.raises(ValueError, match="invalid type for script"):
        validate_tool_arguments("profile_eval", {"profile_id": "p", "script": 5})


def test_rejects_string_for_boolean_slot():
    with pytest.raises(ValueError, match="invalid type for headless"):
        validate_tool_arguments("profile_launch", {"profile_id": "p", "headless": "yes"})


def test_rejects_negative_tab_index():
    with pytest.raises(ValueError, match="tab_index must not be negative"):
        validate_tool_arguments("profile_eval", {"profile_id": "p", "script": "x", "tab_index": -1})


def test_rejects_zero_tabs():
    with pytest.raises(ValueError, match="tabs must be at least 1"):
        validate_tool_arguments("profile_launch", {"profile_id": "p", "tabs": 0})


def test_rejects_unknown_tool():
    with pytest.raises(ValueError, match="unknown tool"):
        validate_tool_arguments("no_such_tool", {})


def test_rejects_non_object_arguments():
    with pytest.raises(ValueError, match="tool arguments must be an object"):
        validate_tool_arguments("profile_eval", "not-a-dict")
