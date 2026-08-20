import pytest

from profiledock.models import Profile
from profiledock.validation import ValidationError, validate_required_fields


def test_validate_required_fields_engine_valid():
    p1 = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine=None)
    validate_required_fields(p1)

    p2 = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="direct")
    validate_required_fields(p2)

    p3 = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="playwright")
    validate_required_fields(p3)


def test_validate_required_fields_engine_invalid():
    p = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="custom")
    with pytest.raises(ValidationError, match="invalid engine 'custom'"):
        validate_required_fields(p)
