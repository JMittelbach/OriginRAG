from __future__ import annotations

from originrag.retrieval.text_cleaning import (
    _best_window_center,
    _candidate_window_positions,
    _window_bounds,
    evidence_window,
)


def test_window_at_zero_keeps_complete_setting_sentence():
    first = "Sensor calibration was tested in Canada."
    text = first + " " + "The laboratory recorded measurements over controlled intervals. " * 20
    assert evidence_window("sensor calibration", text, max_chars=500).startswith(first)


def test_clipping_a_number_never_changes_its_value():
    text = "Observed concentration 0.0091 units in this sample."
    for query in ("", "no-match", "concentration"):
        assert "0.00" not in evidence_window(query, text, max_chars=text.index("91"))


def test_flattened_table_detector_ignores_prose_and_inline_citations():
    from originrag.retrieval.method_description import contains_flattened_table
    assert contains_flattened_table("Table 1.\nSensor metrics\nName\nType\nUnits\nMedian\nRange")
    assert not contains_flattened_table("The results in Table 1.\nWe repeated the measurement in five populations.")


def test_prefix_fallback_keeps_complete_first_sentence():
    first = "The evaluation used 40 devices in Canada."
    text = first + " " + "The laboratory recorded measurements over controlled intervals. " * 20
    for question in ("", "unmatched terminology"):
        assert evidence_window(question, text, max_chars=500).startswith(first)


def test_later_dense_result_window_is_not_hidden_by_first_background_mentions():
    text = (
        "Background: Sensor calibration requires validation. "
        + "The protocol described the equipment and maintenance schedule. " * 18
        + "Methods: Accuracy was measured during validation. "
        + "The equipment was installed under controlled conditions. " * 18
        + "Results: Sensor calibration accuracy was 98.4 percent in 40 devices. "
        + "Sensor calibration accuracy remained 98.4 percent in a second evaluation. "
        + "The evaluation was restricted to indoor conditions. " * 5
    )
    window = evidence_window("sensor calibration accuracy", text, max_chars=480)
    assert "98.4 percent" in window
    assert "40 devices" in window
    assert len(window) <= 480


def test_candidate_positions_remain_bounded_and_cover_late_matches():
    text = ("sensor calibration " * 10_000) + "accuracy sensor"
    positions = _candidate_window_positions(text, ["sensor", "calibration", "accuracy"])
    assert len(positions) <= 256
    assert 0 in positions
    assert max(positions) >= text.rfind("accuracy")


def test_short_window_keeps_complete_terminal_data_identifier():
    text = (
        "Sensor methods and calibration measurements. " * 80
        + "Data availability: measurements were deposited in the archive under DATA123456."
    )
    window = evidence_window(
        "sensor calibration data availability access repository archive deposited", text, max_chars=220,
    )
    assert "DATA123456" in window


def test_scoring_uses_the_same_window_that_will_be_delivered():
    text = "a" * 35 + "sensor " + "a" * 44 + "accuracy " + "a" * 100 + "sensor accuracy " + "a" * 100
    tokens = ["sensor", "accuracy"]
    positions = _candidate_window_positions(text, tokens)
    selected = _best_window_center(text, tokens, positions, 100)

    def score(position):
        start, end, _prefix, _suffix = _window_bounds(len(text), position, 100)
        window = text[start:end]
        return sum(window.count(token) for token in tokens) + 3 * sum(token in window for token in tokens)

    assert score(selected) == max(map(score, positions))
