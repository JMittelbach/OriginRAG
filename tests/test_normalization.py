from __future__ import annotations

import math

import pytest

from pfempire.utils.normalization import (
    finite_float,
    is_finite_number,
    normalize_string_list,
    safe_int,
)


def test_normalize_string_list_handles_json_tsv_and_iterables() -> None:
    assert normalize_string_list("ocr_noise;table_like") == ["ocr_noise", "table_like"]
    assert normalize_string_list('["ocr_noise", "table_like"]') == [
        "ocr_noise",
        "table_like",
    ]
    assert normalize_string_list(["ocr_noise", "", "ocr_noise"]) == ["ocr_noise"]
    assert normalize_string_list("") == []


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "not-a-number", None])
def test_finite_float_replaces_nonfinite_or_malformed_values(value: object) -> None:
    assert finite_float(value, 2.5) == 2.5
    assert math.isfinite(finite_float(value, 2.5))
    assert not is_finite_number(value)


def test_safe_int_uses_default_for_malformed_values() -> None:
    assert safe_int("12", 99) == 12
    assert safe_int("not-an-int", 99) == 99
