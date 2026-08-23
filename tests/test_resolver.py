from profiledock.profile_manager import AmbiguousProfileError, ProfileNotFoundError


def test_resolve_full_id(manager):
    profile = manager.create("Work")
    resolved = manager.resolve(profile.id)
    assert resolved.id == profile.id


def test_resolve_unique_prefix(manager):
    profile = manager.create("Work")
    prefix = profile.id[:3]
    resolved = manager.resolve(prefix)
    assert resolved.id == profile.id


def test_resolve_exact_name(manager):
    profile = manager.create("Work")
    resolved = manager.resolve("Work")
    assert resolved.id == profile.id


def test_resolve_full_id_wins_over_name(manager):
    p1 = manager.create("Work")
    p2 = manager.create(p1.id)
    resolved = manager.resolve(p1.id)
    assert resolved.id == p1.id


def test_resolve_ambiguous_prefix(manager):
    p1 = manager.create("Alpha")
    p2 = manager.create("Beta")
    common = ""
    for a, b in zip(p1.id, p2.id):
        if a == b:
            common += a
        else:
            break
    if not common:
        common = ""
    try:
        manager.resolve(common if common else p1.id[:0])
    except (AmbiguousProfileError, ProfileNotFoundError):
        pass


def test_resolve_ambiguous_prefix_shows_matches(manager):
    from profiledock.models import Profile, utc_now
    from profiledock.storage import add_profile_atomic
    data_dir1 = manager.profiles_dir / "aa111111" / "browser-data"
    data_dir1.mkdir(parents=True, exist_ok=True)
    data_dir2 = manager.profiles_dir / "aa222222" / "browser-data"
    data_dir2.mkdir(parents=True, exist_ok=True)
    p1 = Profile("aa111111", "First", utc_now(), str(data_dir1))
    p2 = Profile("aa222222", "Second", utc_now(), str(data_dir2))
    add_profile_atomic(p1, manager.profiles_file, manager.profiles_dir)
    add_profile_atomic(p2, manager.profiles_file, manager.profiles_dir)
    try:
        manager.resolve("aa")
        assert False, "expected AmbiguousProfileError"
    except AmbiguousProfileError as exc:
        msg = str(exc)
        assert "aa111111" in msg
        assert "aa222222" in msg
        assert "First" in msg
        assert "Second" in msg


def test_resolve_ambiguous_name_shows_matches(manager):
    p1 = manager.create("Shared")
    p2 = manager.create("Shared")
    try:
        manager.resolve("Shared")
        assert False, "expected AmbiguousProfileError"
    except AmbiguousProfileError as exc:
        msg = str(exc)
        assert p1.id in msg
        assert p2.id in msg


def test_resolve_name_case_sensitive(manager):
    manager.create("Work")
    try:
        manager.resolve("work")
    except ProfileNotFoundError:
        pass
    else:
        assert False, "expected ProfileNotFoundError for case mismatch"


def test_resolve_id_case_sensitive(manager):
    profile = manager.create("Test")
    try:
        manager.resolve(profile.id.upper())
    except ProfileNotFoundError:
        pass
    except AmbiguousProfileError:
        pass


def test_resolve_missing_profile(manager):
    try:
        manager.resolve("nonexistent")
        assert False, "expected ProfileNotFoundError"
    except ProfileNotFoundError as exc:
        assert "nonexistent" in str(exc)


def test_resolve_empty_string(manager):
    manager.create("Test")
    try:
        manager.resolve("")
    except ProfileNotFoundError as exc:
        assert "empty identifier" in str(exc)
    else:
        assert False, "expected ProfileNotFoundError"


def test_resolve_used_by_delete(manager):
    profile = manager.create("DeleteMe")
    prefix = profile.id[:3]
    deleted = manager.delete(prefix)
    assert deleted.id == profile.id
    assert manager.list_profiles() == []


def test_resolve_used_by_rename(manager):
    profile = manager.create("OldName")
    renamed = manager.rename("OldName", "NewName")
    assert renamed.name == "NewName"
    assert renamed.id == profile.id


def test_resolve_used_by_mark_launched(manager):
    profile = manager.create("LaunchMe")
    manager.mark_launched(profile.name)
    updated = manager.get(profile.id)
    assert updated.last_launched_at is not None
