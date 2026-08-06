from halka_arz_advisor.notify.state import SeenRecordsState, load_state, save_state


def test_load_state_reports_first_run_when_file_missing(tmp_path):
    state, is_first_run = load_state(tmp_path / "seen.json")
    assert is_first_run is True
    assert state.ipo_identities == set()
    assert state.application_identities == set()
    assert state.initialized_at_utc is None


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "state" / "seen.json"
    state = SeenRecordsState(
        initialized_at_utc="2026-01-01T00:00:00Z",
        ipo_identities={"ipo:PATEK:2024 / 2"},
        application_identities={"application:X AŞ:2024-01-01"},
    )
    save_state(path, state)

    loaded, is_first_run = load_state(path)
    assert is_first_run is False
    assert loaded.initialized_at_utc == "2026-01-01T00:00:00Z"
    assert loaded.ipo_identities == {"ipo:PATEK:2024 / 2"}
    assert loaded.application_identities == {"application:X AŞ:2024-01-01"}


def test_empty_but_existing_file_is_not_a_first_run(tmp_path):
    path = tmp_path / "seen.json"
    save_state(path, SeenRecordsState())

    _, is_first_run = load_state(path)
    assert is_first_run is False


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "seen.json"
    save_state(path, SeenRecordsState())
    assert path.exists()
