from profiledock.process_manager import is_running, state_path


def test_create_list_delete(manager):
    profile = manager.create("Personal")
    assert manager.list_profiles() == [profile]
    assert profile.id in profile.data_dir
    assert __import__("pathlib").Path(profile.data_dir).is_dir()
    manager.delete(profile.id)
    assert manager.list_profiles() == []
    assert not __import__("pathlib").Path(profile.data_dir).parent.exists()


def test_running_state_stale_file_is_cleaned(manager):
    profile = manager.create("Personal")
    path = state_path(profile.data_dir)
    path.write_text('{"pid": 999999, "port": 1}', encoding="utf-8")
    assert not is_running(profile.data_dir)
    assert not path.exists()

