from halka_arz_advisor.spk.profiler import compare_ordering, profile_records


def test_profile_empty_records():
    profile = profile_records([])
    assert profile.record_count == 0
    assert profile.all_observed_keys == frozenset()
    assert profile.first_record is None
    assert profile.last_record is None


def test_profile_reports_record_count_and_keys():
    records = [
        {"borsaKodu": "AAA", "ay": 1, "donem": "2024 / 1"},
        {"borsaKodu": "BBB", "ay": 2, "donem": "2024 / 2"},
    ]
    profile = profile_records(records)
    assert profile.record_count == 2
    assert profile.all_observed_keys == frozenset({"borsaKodu", "ay", "donem"})
    assert profile.first_record == records[0]
    assert profile.last_record == records[1]


def test_profile_detects_keys_missing_from_some_records():
    records = [
        {"borsaKodu": "AAA", "extra": "x"},
        {"borsaKodu": "BBB"},
    ]
    profile = profile_records(records)
    assert "extra" in profile.keys_missing_from_some_records
    assert "borsaKodu" not in profile.keys_missing_from_some_records


def test_profile_detects_keys_always_null():
    records = [
        {"borsaKodu": "AAA", "ilkIslemGorduguPazar": None},
        {"borsaKodu": "BBB", "ilkIslemGorduguPazar": None},
    ]
    profile = profile_records(records)
    assert "ilkIslemGorduguPazar" in profile.keys_always_null
    assert "borsaKodu" not in profile.keys_always_null


def test_profile_reports_mixed_observed_primitive_types():
    records = [
        {"halkaArzOrani": 20.15},
        {"halkaArzOrani": 20},  # int instead of float, still "a number" but a different Python type
        {"halkaArzOrani": "20.15"},  # a string this time — genuinely mixed
    ]
    profile = profile_records(records)
    observed = profile.observed_types_per_key["halkaArzOrani"]
    assert observed == frozenset({"float", "int", "str"})


def test_profile_detects_duplicate_full_records():
    records = [
        {"borsaKodu": "AAA", "ay": 1},
        {"borsaKodu": "BBB", "ay": 2},
        {"borsaKodu": "AAA", "ay": 1},
    ]
    profile = profile_records(records)
    assert (0, 2) in profile.duplicate_full_records


def test_profile_reports_duplicate_identity_candidates_without_deciding():
    records = [
        {"borsaKodu": "AAA", "donem": "2024 / 1"},
        {"borsaKodu": "AAA", "donem": "2024 / 2"},
        {"borsaKodu": "BBB", "donem": "2024 / 3"},
    ]
    profile = profile_records(records)
    candidate_fields = {c.field_name for c in profile.duplicate_identity_candidates}
    assert "borsaKodu" in candidate_fields
    assert "donem" not in candidate_fields  # all three donem values are distinct


def test_compare_ordering_stable_when_identical():
    a = [{"borsaKodu": "AAA"}, {"borsaKodu": "BBB"}]
    b = [{"borsaKodu": "AAA"}, {"borsaKodu": "BBB"}]
    result = compare_ordering(a, b)
    assert result.stable is True
    assert result.same_length is True


def test_compare_ordering_unstable_when_reordered():
    a = [{"borsaKodu": "AAA"}, {"borsaKodu": "BBB"}]
    b = [{"borsaKodu": "BBB"}, {"borsaKodu": "AAA"}]
    result = compare_ordering(a, b)
    assert result.stable is False
    assert "diverge" in result.reason


def test_compare_ordering_unstable_when_length_differs():
    a = [{"borsaKodu": "AAA"}]
    b = [{"borsaKodu": "AAA"}, {"borsaKodu": "BBB"}]
    result = compare_ordering(a, b)
    assert result.stable is False
    assert result.same_length is False
