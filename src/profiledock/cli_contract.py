from typing import Final

CLI_CONTRACT_VERSION: Final = 1
CLI_JSON_OUTPUT_VERSION: Final = 1

EXIT_SUCCESS: Final = 0
EXIT_USER_ERROR: Final = 1
EXIT_USAGE_ERROR: Final = 2

ERROR_CATEGORIES: Final = frozenset({
    "ambiguous_profile",
    "browser_launch_failed",
    "confirmation_required",
    "corrupted_data",
    "invalid_input",
    "not_found",
    "profile_active",
    "security_violation",
    "storage_error",
})


def error_category(message: str) -> str:
    value = message.lower()
    if "playwright chromium" in value or "browser launch" in value:
        return "browser_launch_failed"
    if "ambiguous profile" in value:
        return "ambiguous_profile"
    if "profile not found" in value or "does not exist" in value or "not found" in value:
        return "not_found"
    if "confirmation" in value or "requires --yes" in value:
        return "confirmation_required"
    if "already running" in value or "profile is running" in value or "profile is active" in value:
        return "profile_active"
    if "corrupt" in value or "unsupported" in value and "version" in value:
        return "corrupted_data"
    if "unsafe" in value or "escape" in value or "traversal" in value:
        return "security_violation"
    if "launch" in value or "browser" in value and "failed" in value:
        return "browser_launch_failed"
    if "metadata" in value or "data root" in value or "could not write" in value:
        return "storage_error"
    return "invalid_input"
