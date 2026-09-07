from __future__ import annotations

import pytest

from pfempire.task_modes import TaskModeConfigError, get_task_mode, load_task_modes


def test_load_task_modes_contains_core_modes() -> None:
    modes = load_task_modes()

    assert "evidence_qa" in modes
    assert "gap_finder" in modes
    assert modes["evidence_qa"]["max_chunks"] == 12
    assert "results" in modes["evidence_qa"]["preferred_sections"]


def test_get_task_mode_rejects_unknown_mode() -> None:
    with pytest.raises(TaskModeConfigError):
        get_task_mode("not_a_mode")
