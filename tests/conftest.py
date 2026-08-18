import pytest

from profiledock.profile_manager import ProfileManager


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "app-data"))


@pytest.fixture
def manager(tmp_path):
    return ProfileManager(tmp_path)
