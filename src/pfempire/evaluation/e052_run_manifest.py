"""Fail-closed claim-eligibility boundary for E052 run manifests."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping


RUN_MANIFEST_SCHEMA_VERSION = "pfempire_e052_external_run_v1"
CLAIM_ELIGIBLE_EXECUTION_PHASE = "heldout"
ARM_SCHEDULE_SCHEMA_VERSION = "pfempire_e052_arm_schedule_v1"


def validate_claim_eligible_run_manifest(value: Any) -> Mapping[str, Any]:
    """Accept only an explicitly authorized held-out run for pooled scoring."""

    if not isinstance(value, Mapping):
        raise ValueError("E052 run manifest must be an object")
    expected = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "status": "complete",
        "execution_phase": CLAIM_ELIGIBLE_EXECUTION_PHASE,
        "execution_purpose": "heldout_confirmatory",
        "heldout_execution_authorized": True,
        "claim_eligible": True,
    }
    mismatched = [key for key, expected_value in expected.items() if value.get(key) != expected_value]
    if mismatched:
        raise ValueError(
            "run manifest is not an authorized claim-eligible held-out run: "
            + ", ".join(mismatched)
        )
    provenance = value.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("claim-eligible run manifest lacks provenance")
    provenance_expected = {
        "execution_phase": CLAIM_ELIGIBLE_EXECUTION_PHASE,
        "execution_purpose": "heldout_confirmatory",
        "heldout_execution_authorized": True,
        "claim_eligible": True,
    }
    provenance_mismatched = [
        key
        for key, expected_value in provenance_expected.items()
        if provenance.get(key) != expected_value
    ]
    if provenance_mismatched:
        raise ValueError(
            "run provenance is not an authorized claim-eligible held-out run: "
            + ", ".join(provenance_mismatched)
        )
    schedule = provenance.get("execution_schedule")
    expected_schedule_keys = {
        "schema_version",
        "status",
        "method",
        "order_seed",
        "order_seed_commitment_sha256",
    }
    if not isinstance(schedule, Mapping) or set(schedule) != expected_schedule_keys:
        raise ValueError("claim-eligible run provenance lacks the frozen arm schedule")
    seed = schedule.get("order_seed")
    if (
        schedule.get("schema_version") != ARM_SCHEDULE_SCHEMA_VERSION
        or schedule.get("status") != "frozen"
        or schedule.get("method") != "sha256_question_seed_balanced_rotation_v1"
        or isinstance(seed, bool)
        or not isinstance(seed, int)
        or seed < 0
        or schedule.get("order_seed_commitment_sha256")
        != hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
    ):
        raise ValueError("claim-eligible run provenance arm schedule is invalid")
    return value
