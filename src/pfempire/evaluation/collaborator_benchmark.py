from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from pfempire.evaluation.malaria_evidence_bench import empty_item, normalize_item, normalize_question


TASK_MODE_MAP = {
    "evidence question": "evidence_qa",
    "mechanism": "mechanism_mapping",
    "method comparison": "method_comparison",
    "contradiction between studies": "contradiction_finder",
    "hypothesis generation": "hypothesis_generator",
    "dataset/paper search": "dataset_finder",
    "research gap": "gap_finder",
}

FACET_PATTERNS = {
    "var_pfemp1": r"\bvar\b|\bpfemp1\b|\bcidr|\bdomain cassette|\bdc8\b|\bdc13\b",
    "severe_malaria": r"\bsevere[- ]malaria\b",
    "cerebral_malaria": r"\bcerebral[- ]malaria\b",
    "pregnancy_malaria": r"\b(?:pregnan|placent|var2csa)\w*\b",
    "host_immunity": r"\b(?:immun|antibod|b cells?|t cells?|monocyt|cytokin)\w*\b",
    "single_cell": r"\b(?:single[- ]cell|scrna)\b",
    "parasite_transcriptomics": r"\b(?:transcriptom|rna[- ]seq|gene expression|var expression)\w*\b",
    "drug_resistance": r"\b(?:drug resistance|artemisinin|kelch13|k13|resistan)\w*\b",
    "vaccines": r"\b(?:vaccin|rts,s|r21|rh5)\w*\b",
    "datasets": r"\b(?:dataset|cohort|accession|repository|data resource)\w*\b",
    "transmission": r"\b(?:transmission|gametocyt|mosquito)\w*\b",
    "vivax": r"\b(?:plasmodium vivax|p\. vivax)\b",
    "computational_methods": r"\b(?:machine learning|classifier|integration|benchmark|retrieval|rag)\w*\b",
}


def import_collaborator_rows(
    source_rows: list[dict[str, str]], papers_tsv: Path
) -> tuple[list[dict[str, str]], dict]:
    registry = build_registry_index(papers_tsv)
    imported: list[dict[str, str]] = []
    unresolved_counter: Counter[str] = Counter()
    corrected_dois: Counter[str] = Counter()
    for source in source_rows:
        source_id = clean(source.get("Benchmark ID"))
        resolved_ids, unresolved, corrected = resolve_source_ids(
            source.get("DOI / PMID", ""), registry
        )
        unresolved_counter.update(unresolved)
        corrected_dois.update(corrected)
        task_mode = TASK_MODE_MAP.get(clean(source.get("Question type")).lower(), "evidence_qa")
        answerability = clean(source.get("Answerability / abstention"))
        abstain = bool(
            re.search(
                r"\bshould (?:cautiously )?abstain\b|\bshould decline\b",
                answerability,
                flags=re.IGNORECASE,
            )
        )
        item = empty_item()
        item.update(
            {
                "item_id": f"MEB_COLLAB_{source_id}",
                "split": "candidate",
                "review_status": "candidate",
                "task_mode": task_mode,
                "difficulty": clean(source.get("Difficulty")).lower() or "medium",
                "question": clean(source.get("Question")),
                "expected_answer_nuggets": split_bullets(source.get("Expected core points", "")),
                "expected_facets": infer_facets(
                    f"{source.get('Question', '')} {source.get('Expected core points', '')}"
                ),
                "expected_paper_ids": resolved_ids,
                "expected_stance": (
                    "not_enough_evidence"
                    if abstain
                    else "mixed"
                    if task_mode == "contradiction_finder"
                    else "not_applicable"
                ),
                "abstain_expected": abstain,
                "provenance": "collaborator_scientific_benchmark_100_english",
                "review_notes": review_notes(source, unresolved, corrected),
            }
        )
        imported.append(normalize_item(item))
    return imported, {
        "n_source_rows": len(source_rows),
        "n_imported": len(imported),
        "n_with_paper_ids": sum(bool(item["expected_paper_ids"]) for item in imported),
        "n_without_paper_ids": sum(not item["expected_paper_ids"] for item in imported),
        "n_abstention": sum(item["abstain_expected"] == "true" for item in imported),
        "task_modes": dict(sorted(Counter(item["task_mode"] for item in imported).items())),
        "difficulty": dict(sorted(Counter(item["difficulty"] for item in imported).items())),
        "unresolved_identifiers": dict(sorted(unresolved_counter.items())),
        "corrected_dois": dict(sorted(corrected_dois.items())),
    }


def merge_unique_rows(
    existing: list[dict[str, str]], additions: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int]:
    addition_ids = {normalize_item(row)["item_id"] for row in additions}
    merged = [
        normalize_item(row)
        for row in existing
        if normalize_item(row)["item_id"] not in addition_ids
    ]
    questions = {normalize_question(row["question"]) for row in merged}
    skipped = 0
    for row in additions:
        normalized = normalize_item(row)
        key = normalize_question(normalized["question"])
        if key in questions:
            skipped += 1
            continue
        merged.append(normalized)
        questions.add(key)
    return merged, skipped


def build_registry_index(papers_tsv: Path) -> dict[tuple[str, str], str]:
    rows: list[dict[str, str]] = []
    with Path(papers_tsv).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    canonical_by_doi: dict[str, str] = {}
    for row in rows:
        doi = normalize_doi(row.get("doi", ""))
        if not doi:
            continue
        current_id = canonical_by_doi.get(doi)
        if current_id is None:
            canonical_by_doi[doi] = clean(row.get("paper_id"))
            continue
        current = next(item for item in rows if clean(item.get("paper_id")) == current_id)
        if registry_row_score(row) > registry_row_score(current):
            canonical_by_doi[doi] = clean(row.get("paper_id"))

    index: dict[tuple[str, str], str] = {}
    for row in rows:
        doi = normalize_doi(row.get("doi", ""))
        paper_id = canonical_by_doi.get(doi) or clean(row.get("paper_id"))
        values = {
            "DOI": doi,
            "PMID": digits(row.get("pmid", "")),
            "PMCID": clean(row.get("pmcid")).upper(),
        }
        for kind, value in values.items():
            if paper_id and value:
                index[(kind, value)] = paper_id
    return index


def registry_row_score(row: dict[str, str]) -> tuple[int, int, int]:
    metadata_score = sum(
        bool(clean(row.get(field)))
        for field in ("pmid", "pmcid", "topics", "methods", "systems")
    )
    duplicate_path_penalty = "_2" in clean(row.get("local_pdf_relpath"))
    sync_only_penalty = "registry_doi_sync" in clean(row.get("notes"))
    return metadata_score, -int(duplicate_path_penalty), -int(sync_only_penalty)


def resolve_source_ids(
    source_text: str, registry: dict[tuple[str, str], str]
) -> tuple[list[str], list[str], list[str]]:
    paper_ids: list[str] = []
    unresolved: list[str] = []
    corrected: list[str] = []
    for line in str(source_text or "").splitlines():
        references: list[tuple[str, str]] = []
        references.extend(("DOI", normalize_doi(value)) for value in re.findall(r"DOI:\s*([^;\n•]+)", line, re.I))
        references.extend(("PMID", value) for value in re.findall(r"PMID:\s*(\d+)", line, re.I))
        references.extend(("PMCID", value.upper()) for value in re.findall(r"PMCID:\s*(PMC\d+)", line, re.I))
        for kind, value in references:
            paper_id = registry.get((kind, value))
            if not paper_id and kind == "DOI" and value.endswith(".001"):
                corrected_value = value[:-4]
                paper_id = registry.get((kind, corrected_value))
                if paper_id:
                    corrected.append(f"{value}->{corrected_value}")
            if paper_id:
                if paper_id not in paper_ids:
                    paper_ids.append(paper_id)
            else:
                reference = f"{kind}:{value}"
                if reference not in unresolved:
                    unresolved.append(reference)
    return paper_ids, unresolved, corrected


def split_bullets(value: str) -> list[str]:
    return [
        clean(re.sub(r"^[\s•*-]+", "", line))
        for line in str(value or "").splitlines()
        if clean(re.sub(r"^[\s•*-]+", "", line))
    ]


def infer_facets(value: str) -> list[str]:
    text = str(value or "").lower()
    return [facet for facet, pattern in FACET_PATTERNS.items() if re.search(pattern, text, re.I)]


def review_notes(source: dict[str, str], unresolved: list[str], corrected: list[str]) -> str:
    parts = [
        f"Original ID: {clean(source.get('Benchmark ID'))}",
        f"Answerability: {clean(source.get('Answerability / abstention'))}",
        f"Evidence location: {clean(source.get('Relevant section or page'))}",
        f"Rationale: {clean(source.get('Why scientifically important or difficult'))}",
        f"Source citations: {clean(source.get('Relevant papers'))}",
    ]
    if corrected:
        parts.append(f"Corrected DOI aliases: {'; '.join(corrected)}")
    if unresolved:
        parts.append(f"Corpus sources still missing: {'; '.join(unresolved)}")
    return " | ".join(parts)


def normalize_doi(value: str) -> str:
    return re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        clean(value).lower(),
    ).rstrip(".,;)")


def digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def clean(value: object) -> str:
    return " ".join(str(value or "").split())
