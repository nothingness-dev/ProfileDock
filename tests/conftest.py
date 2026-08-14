import pytest

from profiledock.profile_manager import ProfileManager


@pytest.fixture
def manager(tmp_path):
    return ProfileManager(tmp_path)

