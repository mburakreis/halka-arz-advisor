from halka_arz_advisor.notify.analysis_state import SentAnalysesState, load_state, save_state


def test_load_state_reports_first_run_when_file_missing(tmp_path):
    state, is_first_run = load_state(tmp_path / "sent.json")
    assert is_first_run is True
    assert state.sent_hashes == set()
    assert state.initialized_at_utc is None


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "state" / "sent.json"
    state = SentAnalysesState(initialized_at_utc="2026-01-01T00:00:00Z", sent_hashes={"abc123", "def456"})
    save_state(path, state)

    loaded, is_first_run = load_state(path)
    assert is_first_run is False
    assert loaded.initialized_at_utc == "2026-01-01T00:00:00Z"
    assert loaded.sent_hashes == {"abc123", "def456"}


def test_empty_but_existing_file_is_not_a_first_run(tmp_path):
    path = tmp_path / "sent.json"
    save_state(path, SentAnalysesState())

    _, is_first_run = load_state(path)
    assert is_first_run is False


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "sent.json"
    save_state(path, SentAnalysesState())
    assert path.exists()
