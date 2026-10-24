from __future__ import annotations

import copy
import re
from itertools import combinations

import pytest

from originrag.analysis.prompts import _automatic_overlap_block


CAVEATS = """- Use these signals internally to avoid double-counting. Do not mention an overlap pair or cite a
  report merely because it appears in this list. Each cited report's substantive evidence must
  directly contribute to the answer.
- Treat a same-publication-family pair as versions of one publication, not independent support.
- Prefer the journal version when its visible evidence supports the claim. Cite the preprint too
  only when it supplies uniquely needed visible evidence, and explicitly label it as the related
  preprint/version rather than a second independent source.
- A shared trial, dataset, or cohort identifier is a candidate dependence signal, not proof that
  every analysis or sample is identical.
"""
HEADING = "\n\nAutomatic overlap signals (review-assistive, not adjudicated truth):\n"
KINDS = [
    "same_doi_family", "same_preprint_family", "same_publication_version",
    "same_dataset_accession", "same_trial_id", "same_cohort",
]


def signal(left, right, kind="same_dataset_accession", shared=("CAL-2024",)):
    return {
        "paper_id_a": left,
        "paper_id_b": right,
        "signal_type": kind,
        "details": {"shared": list(shared)},
    }


def pack_for(signals, visible=("P0", "P1", "P2")):
    return {
        "chunks": [
            {"paper_id": paper, "evidence_id": f"E{index:03d}", "text": "Calibration report."}
            for index, paper in enumerate(visible, 1)
        ],
        "automatic_lineage_signals": signals,
    }


def edge_lines(block):
    return [line for line in block.splitlines() if re.match(r"^- same_\w+: ", line)]


def decode_edges(block):
    """Expand only explicitly serialized edges; never infer transitive pairs."""
    edges = set()
    for line in edge_lines(block):
        kind, body = line[2:].split(": ", 1)
        pair, _, suffix = body.partition("; shared identifier: ")
        left, targets = pair.split(" <-> ")
        left = left.split(" (", 1)[0]
        if targets.startswith("each of: "):
            targets = targets[len("each of: "):]
        for right in re.findall(r"(\S+) \([^)]*\)", targets):
            edges.add((kind, left, right, suffix))
    return edges


def expected_edges(signals):
    return {
        (item["signal_type"], item["paper_id_a"], item["paper_id_b"],
         ", ".join(map(str, item["details"].get("shared") or item["details"].get("shared_labels") or [])))
        for item in signals if item["signal_type"] in KINDS
    }


def original_edge_lines(pack):
    visible = {}
    for chunk in pack["chunks"]:
        visible.setdefault(chunk["paper_id"], []).append(chunk["evidence_id"])
    lines = []
    for item in pack["automatic_lineage_signals"]:
        left, right, kind = item["paper_id_a"], item["paper_id_b"], item["signal_type"]
        shared = item["details"].get("shared") or item["details"].get("shared_labels") or []
        suffix = f"; shared identifier: {', '.join(map(str, shared))}" if shared else ""
        lines.append(
            f"- {kind}: {left} ({', '.join(visible.get(left, [])) or 'no visible chunk'}) "
            f"<-> {right} ({', '.join(visible.get(right, [])) or 'no visible chunk'}){suffix}"
        )
    return lines


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("shared", [[], ["CAL-2024"], ["CAL-A", "CAL-B"]])
def test_single_edge_format_and_all_caveats_are_unchanged(kind, shared):
    pack = pack_for([signal("P0", "P1", kind, shared)])

    assert _automatic_overlap_block(pack) == HEADING + original_edge_lines(pack)[0] + "\n" + CAVEATS


def test_clique_compacts_repetition_without_losing_or_inventing_any_pair():
    papers = [f"P{index:02d}" for index in range(12)]
    signals = [signal(left, right) for left, right in combinations(papers, 2)]
    pack = pack_for(signals, visible=papers)
    before = copy.deepcopy(pack)

    block = _automatic_overlap_block(pack)
    original = HEADING + "\n".join(original_edge_lines(pack)) + "\n" + CAVEATS

    assert decode_edges(block) == expected_edges(signals)
    assert len(edge_lines(block)) == len(papers) - 1
    assert len(block) < len(original) * 0.65
    assert block.endswith(CAVEATS)
    assert pack == before


def test_chain_does_not_imply_transitive_or_cross_target_edges():
    signals = [signal("P0", "P1"), signal("P1", "P2"), signal("P0", "P3")]
    block = _automatic_overlap_block(pack_for(signals))

    assert decode_edges(block) == expected_edges(signals)
    assert "P0 (E001) <-> each of: P1 (E002), P3 (no visible chunk)" in block
    assert len(edge_lines(block)) == 2


def test_different_kinds_and_shared_identifier_sequences_remain_separate():
    signals = [
        signal("P0", "P1", "same_dataset_accession", ["CAL-A", "CAL-B"]),
        signal("P0", "P2", "same_dataset_accession", ["CAL-B", "CAL-A"]),
        signal("P0", "P2", "same_dataset_accession", ["CAL-C"]),
        signal("P0", "P1", "same_cohort", ["CAL-A", "CAL-B"]),
        signal("P0", "P2", "same_cohort", ["CAL-A", "CAL-B"]),
    ]

    block = _automatic_overlap_block(pack_for(signals))

    assert decode_edges(block) == expected_edges(signals)
    assert len(edge_lines(block)) == 4
    assert sum("each of:" in line for line in edge_lines(block)) == 1


def test_shared_labels_fallback_and_unknown_visible_endpoints_are_preserved():
    signals = [signal("UNKNOWN", "P0"), signal("UNKNOWN", "OTHER")]
    signals[1]["details"] = {"shared_labels": ["CAL-2024"]}
    pack = pack_for(signals)
    pack["chunks"].append({"paper_id": "P0", "evidence_id": "E004", "text": "Calibration notes."})

    block = _automatic_overlap_block(pack)

    assert decode_edges(block) == expected_edges(signals)
    assert "UNKNOWN (no visible chunk) <-> each of: P0 (E001, E004), OTHER (no visible chunk)" in block
    assert len(edge_lines(block)) == 1


def test_duplicate_edges_can_be_deduplicated_but_reversed_edges_are_retained():
    first = signal("P0", "P1")
    signals = [first, copy.deepcopy(first), signal("P1", "P0"), signal("P0", "P2")]

    block = _automatic_overlap_block(pack_for(signals))

    assert decode_edges(block) == expected_edges(signals)
    assert "each of: P1 (E002), P2 (E003)" in block
    assert len(edge_lines(block)) == 2


def test_unsupported_signal_types_stay_filtered_out():
    assert _automatic_overlap_block(pack_for([])) == ""
    unsupported = signal("P0", "P1", "unaccepted_signal")
    assert _automatic_overlap_block(pack_for([unsupported])) == ""
    supported = signal("P0", "P1")

    assert decode_edges(_automatic_overlap_block(pack_for([unsupported, supported]))) == expected_edges([supported])
