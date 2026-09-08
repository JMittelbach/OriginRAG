from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.analysis.answer_format import normalize_answer_format  # noqa: E402
from originrag.analysis.answer_backends import generate_answer_with_backend  # noqa: E402
from originrag.analysis.answer_qc import (  # noqa: E402
    annotate_publication_family_relationships,
    assess_answer_quality,
    ensure_required_lineage_caveat,
    normalize_inline_evidence_labels,
)
from originrag.analysis.evidence_pack import (  # noqa: E402
    apply_certification_core_roles,
    build_evidence_pack,
    format_evidence_pack_markdown,
    freeze_prompt_visible_result,
)
from originrag.analysis.evidence_pack_grading import (  # noqa: E402
    format_evidence_pack_grade_markdown,
    grade_evidence_pack,
    question_relevance_terms,
)
from originrag.analysis.evidence_table import format_evidence_table_markdown, write_evidence_table_tsv  # noqa: E402
from originrag.analysis.faithfulness import check_answer_grounding, write_grounding_report  # noqa: E402
from originrag.analysis.llm_backends import generate_answer  # noqa: E402
from originrag.analysis.ollama_client import (  # noqa: E402
    answer_model as ollama_answer_model,
    call_ollama_generate,
)
from originrag.analysis.prompts import build_answer_repair_prompt, build_citation_answer_prompt  # noqa: E402
from originrag.analysis.template_answers import (  # noqa: E402
    build_data_accessibility_answer,
    build_marker_positive_state_answer,
    build_template_answer,
)
from originrag.analysis.llm_backends import LLMResult  # noqa: E402
from originrag.provenance.source_type import classify_source_document  # noqa: E402
from originrag.retrieval.chunk_quality import classify_chunk_quality  # noqa: E402
from originrag.retrieval.fusion import reciprocal_rank_fusion  # noqa: E402
from originrag.retrieval.fts_index import (  # noqa: E402
    DEFAULT_INDEX_PATH,
    corpus_fingerprint,
    ensure_current_index,
    index_has_candidate_record_store,
    search_chunks_bm25,
)
from originrag.retrieval.index_cache import resolve_read_bundle  # noqa: E402
from originrag.retrieval.keyword import (  # noqa: E402
    contains_normalized_term,
    normalize_text,
    search_chunks,
)
from originrag.retrieval.metadata import load_paper_metadata  # noqa: E402
from originrag.retrieval.paper_retrieval import search_papers  # noqa: E402
from originrag.retrieval.paper_selection import (  # noqa: E402
    collapse_detected_publication_versions,
    retain_direct_clinical_expression_papers,
)
from originrag.retrieval.query_plan import (  # noqa: E402
    IMMUNE_RECEPTOR_BINDING_ALIASES,
    NamedStudyReference,
    PAPER_SEARCH_MODES,
    PARASITE_OMICS_ANCHOR,
    RetrievalQuery,
    classify_paper_search_mode,
    explicit_named_study_references,
    required_context_anchor_groups,
    required_entity_anchor_groups,
    named_antibody_anchor_groups,
    requested_expression_family_alternatives,
    requested_immune_receptor_binding,
    requested_source_function_alternatives,
    requested_source_function_title_alternatives,
)
from originrag.retrieval.source_intent import (  # noqa: E402
    assess_intervention_evidence,
    is_epcr_pfemp1_intervention_source_request,
)
from originrag.retrieval.query_planner import plan_queries  # noqa: E402
from originrag.retrieval.llm_query_planner import parse_json_object  # noqa: E402
from originrag.retrieval.adaptive import adaptive_retrieve  # noqa: E402
from originrag.trace.method_versions import (  # noqa: E402
    EVIDENCE_ACCOUNTING_VERSION,
    LINEAGE_GRAPH_SCHEMA_VERSION,
    ROBUST_LINEAGE_CERTIFICATE_VERSION,
)
from originrag.trace.io import read_jsonl  # noqa: E402
from originrag.trace.lineage import suggest_lineage_signals  # noqa: E402
from originrag.trace.runtime import estimate_tokens, run_trace_selection  # noqa: E402
from originrag.utils.io import write_json  # noqa: E402


GRADE_ORDER = {"bad": 0, "weak": 1, "usable": 2, "good": 3}

PAPER_SEARCH_DETAIL_FIELDS = (
    "clinical_phenotype",
    "parasite_feature",
    "direction_of_association",
    "cohort",
    "assay",
    "developmental_stage_adjustment",
)
PAPER_SEARCH_DETAIL_NOT_REPORTED = "not reported in supplied evidence"
PAPER_REASON_BATCH_SIZE = 12
PAPER_DETAIL_BATCH_SIZE = 4
PAPER_DETAIL_RETRY_BATCH_SIZE = 2
PAPER_DETAIL_MAX_RETRY_CALLS = 2
PAPER_DETAIL_MAX_MODEL_CALLS = 8
PAPER_DETAIL_MAX_TOTAL_SECONDS = 360
PAPER_DETAIL_MODEL_STATUS = "model_extracted_unverified"
PAPER_DETAIL_FALLBACK_STATUS = "not_reported_fallback"
PAPER_DETAIL_DETERMINISTIC_STATUS = "deterministic_excerpt_supported"
SOURCE_TYPE_FRONT_TEXT_MAX_CHARS = 24_000
NAMED_STUDY_FULLTEXT_SUPPORT_MAX_CHARS = 2_500
TOPICAL_SOURCE_GENERIC_OVERLAP_TERMS = frozenset(
    {
        "cell",
        "cells",
        "energy",
        "infection",
        "measure",
        "measured",
        "measurement",
        "measurements",
        "measuring",
        "model",
        "models",
        "primary",
        "response",
        "responses",
        "study",
        "studies",
        "type",
        "types",
    }
)
NAMED_STUDY_ACCESSION_REQUEST_PATTERN = re.compile(
    r"\b(?:accession|public data|repository identifier|deposited data)\b",
    flags=re.IGNORECASE,
)
NAMED_STUDY_ACCESSION_PATTERN = re.compile(
    r"\b(?:GSE|PRJNA|PRJEB|ERP|SRP|PXD|MTBLS)\d+\b",
    flags=re.IGNORECASE,
)

PAPER_DETAIL_GEOGRAPHY_ALIASES = (
    ("benin", "beninese"),
    ("burkina faso", "burkinabe", "burkinabè"),
    ("cameroon", "cameroonian"),
    ("cote d ivoire", "ivory coast", "ivorian"),
    ("democratic republic of congo", "drc", "congolese"),
    ("ethiopia", "ethiopian"),
    ("gabon", "gabonese"),
    ("gambia", "gambian"),
    ("ghana", "ghanaian"),
    ("guinea", "guinean"),
    ("india", "indian"),
    ("indonesia", "indonesian"),
    ("kenya", "kenyan"),
    ("malawi", "malawian"),
    ("mali", "malian"),
    ("mozambique", "mozambican"),
    ("nigeria", "nigerian"),
    ("papua new guinea", "papuan"),
    ("senegal", "senegalese"),
    ("sudan", "sudanese"),
    ("tanzania", "tanzanian"),
    ("thailand", "thai"),
    ("uganda", "ugandan"),
    ("vietnam", "vietnamese"),
    ("zambia", "zambian"),
)

PAPER_DETAIL_PHENOTYPE_ALIASES = (
    ("cerebral malaria", "cerebral", "cm"),
    ("retinopathy", "retinopathy positive", "retinopathy negative", "rp", "rn"),
    ("severe malarial anaemia", "severe malarial anemia", "sma"),
    ("respiratory distress", "rd"),
    ("hyperlactataemia", "hyperlactatemia", "hyperlactate", "lactate", "hl"),
    ("acidosis", "acidotic"),
    ("impaired consciousness", "ic"),
    ("asymptomatic", "asymptomatic parasitemia", "asymptomatic parasitaemia", "ap"),
    ("uncomplicated malaria", "uncomplicated", "um"),
)


PRESETS = {
    "quick": {
        "answer": {"limit": 6, "candidate_multiplier": 4, "max_per_paper": 1, "max_chars_per_chunk": 900},
        "sources": {"limit": 8, "candidate_multiplier": 8, "max_per_paper": 2, "max_chars_per_chunk": 900},
        "gaps": {"limit": 6, "candidate_multiplier": 5, "max_per_paper": 1, "max_chars_per_chunk": 900},
        "hypothesis": {"limit": 6, "candidate_multiplier": 5, "max_per_paper": 1, "max_chars_per_chunk": 850},
    },
    "balanced": {
        "answer": {"limit": 10, "candidate_multiplier": 4, "max_per_paper": 2, "max_chars_per_chunk": 1200},
        "sources": {"limit": 12, "candidate_multiplier": 8, "max_per_paper": 2, "max_chars_per_chunk": 1200},
        "gaps": {"limit": 8, "candidate_multiplier": 5, "max_per_paper": 1, "max_chars_per_chunk": 1000},
        "hypothesis": {"limit": 8, "candidate_multiplier": 5, "max_per_paper": 1, "max_chars_per_chunk": 1000},
    },
    "deep": {
        "answer": {"limit": 18, "candidate_multiplier": 7, "max_per_paper": 2, "max_chars_per_chunk": 1100},
        "sources": {"limit": 16, "candidate_multiplier": 8, "max_per_paper": 3, "max_chars_per_chunk": 1400},
        "gaps": {"limit": 12, "candidate_multiplier": 6, "max_per_paper": 2, "max_chars_per_chunk": 1100},
        "hypothesis": {"limit": 10, "candidate_multiplier": 6, "max_per_paper": 2, "max_chars_per_chunk": 1000},
    },
    "wide": {
        "answer": {"limit": 28, "candidate_multiplier": 10, "max_per_paper": 1, "max_chars_per_chunk": 800},
        "sources": {"limit": 20, "candidate_multiplier": 10, "max_per_paper": 2, "max_chars_per_chunk": 900},
        "gaps": {"limit": 14, "candidate_multiplier": 8, "max_per_paper": 1, "max_chars_per_chunk": 850},
        "hypothesis": {"limit": 12, "candidate_multiplier": 8, "max_per_paper": 1, "max_chars_per_chunk": 800},
    },
    "pro": {
        "answer": {"limit": 28, "candidate_multiplier": 10, "max_per_paper": 2, "max_chars_per_chunk": 950},
        "sources": {"limit": 24, "candidate_multiplier": 8, "max_per_paper": 3, "max_chars_per_chunk": 1400},
        "gaps": {"limit": 18, "candidate_multiplier": 8, "max_per_paper": 2, "max_chars_per_chunk": 1300},
        "hypothesis": {"limit": 16, "candidate_multiplier": 8, "max_per_paper": 2, "max_chars_per_chunk": 1300},
    },
}

RESULT_FIELDS = [
    "rank",
    "score",
    "raw_score",
    "metadata_score",
    "section_weight",
    "category_boost",
    "source_boost",
    "quality_penalty",
    "rrf_score",
    "paper_rrf_score",
    "paper_relevance_score",
    "paper_id",
    "author",
    "year",
    "title",
    "journal",
    "category",
    "chunk_id",
    "page_start",
    "page_end",
    "section",
    "section_confidence",
    "quality_flags",
    "snippet",
    "retrieval_query_labels",
]


def main() -> int:
    args = parse_args()
    output_dir = make_output_dir(resolve_repo_path(args.out_dir), args.question)
    try:
        result = run_question(args, output_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"OriginRAG input error: {exc}", file=sys.stderr)
        return 2
    print_summary(args.question, args.backend, result, output_dir)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask a citation-aware question over the active OriginRAG corpus.")
    parser.add_argument("question")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument(
        "--candidate-pool",
        help=(
            "Use this frozen JSONL candidate pool instead of running retrieval. "
            "Intended for fixed-pool evaluation."
        ),
    )
    parser.add_argument(
        "--candidate-pool-question-id",
        help="Require every fixed-pool row to carry this question_id.",
    )
    parser.add_argument(
        "--retriever",
        choices=["keyword", "bm25"],
        default="keyword",
        help="Candidate retrieval backend. BM25 uses the persistent local FTS5 index.",
    )
    parser.add_argument(
        "--retrieval-index",
        default=str(DEFAULT_INDEX_PATH.relative_to(REPO_ROOT)),
        help="SQLite FTS5 index used by --retriever bm25.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Final evidence chunks. Overrides the selected preset.",
    )
    parser.add_argument(
        "--candidate-multiplier",
        type=int,
        default=None,
        help="Retrieve this many times more candidates before selecting final evidence chunks. Overrides the selected preset.",
    )
    parser.add_argument("--max-per-paper", type=int, default=None, help="Maximum selected chunks per paper. Overrides the selected preset.")
    parser.add_argument(
        "--max-chars-per-chunk",
        type=int,
        default=None,
        help="Maximum characters per final evidence chunk. Overrides the selected preset.",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="pro",
        help="Retrieval/context profile. Use pro for the 48 GB Mac Qwen workflow, wide for paper diversity, quick for smoke tests.",
    )
    parser.add_argument(
        "--backend",
        choices=["none", "template", "ollama", "deepseek", "qwen", "queue"],
        default="none",
    )
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help=(
            "Use the keyword-only Adaptive Retrieval v1 path with one bounded "
            "evidence-quality correction pass. BM25 already uses deterministic "
            "multi-query fusion and cannot be combined with this flag."
        ),
    )
    parser.add_argument("--task-mode", default="evidence_qa", help="Planner task mode for adaptive retrieval.")
    parser.add_argument("--planner-backend", choices=["rule_based", "ollama"], default="rule_based")
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--planner-temperature", type=float, default=0.0)
    parser.add_argument("--planner-timeout", type=int, default=120)
    parser.add_argument("--answer-model", default=None)
    parser.add_argument("--answer-temperature", type=float, default=0.0)
    parser.add_argument("--answer-timeout", type=int, default=120)
    parser.add_argument(
        "--min-evidence-grade",
        choices=["bad", "weak", "usable", "good"],
        default="usable",
    )
    parser.add_argument(
        "--force-answer",
        action="store_true",
        help="Allow answer backend even when the evidence grade is below the threshold.",
    )
    parser.add_argument(
        "--allow-provisional-synthesis",
        action="store_true",
        help=(
            "When retrieval evidence is usable but lineage certification is unresolved, allow a "
            "clearly bounded paper-level synthesis. This is the web product behavior; it never "
            "upgrades the lineage certificate or permits independent-replication claims."
        ),
    )
    parser.add_argument(
        "--task",
        choices=["answer", "gaps", "hypothesis", "sources"],
        default="answer",
        help="Prompt style for the final model output.",
    )
    parser.add_argument("--out-dir", default="outputs/answers")
    parser.add_argument("--category")
    parser.add_argument("--include-low-quality", action="store_true")
    parser.add_argument("--show-context", action="store_true")
    parser.add_argument("--query-mode", choices=["evidence", "methods", "background"], default="evidence")
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--exclude-tables", action="store_true")
    parser.add_argument("--exclude-figures", action="store_true")
    parser.add_argument("--exclude-supplement", action="store_true")
    parser.add_argument(
        "--allow-missing-doi-sources",
        action="store_true",
        help="Allow retrieved evidence from papers without DOI metadata. Default is DOI-only sources.",
    )
    parser.add_argument(
        "--repair-attempts",
        type=int,
        default=2,
        help="If a model answer fails QC, retry with a repair prompt this many times.",
    )
    parser.add_argument(
        "--no-template-fallback",
        action="store_true",
        help="Disable deterministic template fallback when model answers fail QC.",
    )
    parser.add_argument(
        "--profile-instruction",
        default="",
        help="Optional user preference text for answer tone/shape. It cannot override evidence and citation rules.",
    )
    parser.add_argument(
        "--evidence-selector",
        choices=["current", "topk", "mmr", "trace_no_lineage", "trace", "trace_flow", "trace_flow_no_anchor", "trace_flow_unbounded", "trace_flow_relevance_reserve", "trace_flow_diverse_reserve"],
        default="trace_flow",
        help=(
            "Evidence composition layer. The default lineage-aware route treats missing origin "
            "relations as uncertainty and withholds unsupported answers; use current explicitly "
            "for the established paper-count baseline."
        ),
    )
    parser.add_argument("--trace-config", default="config/trace_rag.yaml")
    parser.add_argument("--trace-blueprint")
    parser.add_argument("--trace-lineage-graph")
    parser.add_argument("--trace-token-budget", type=int, default=8000)
    parser.add_argument("--trace-selector-algorithm", choices=["lazy_greedy", "beam_search"], default="lazy_greedy")
    parser.add_argument("--show-trace-certificate", action="store_true")
    return parser.parse_args()


def run_question(
    args: argparse.Namespace,
    output_dir: Path,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    def progress(stage: str) -> None:
        if progress_callback is not None:
            progress_callback(stage)

    progress("Planning a focused literature search")
    chunks_dir_source = resolve_repo_path(args.chunks_dir)
    papers_tsv_source = resolve_repo_path(args.papers)
    validate_corpus_inputs(chunks_dir_source, papers_tsv_source)
    chunks_dir = chunks_dir_source
    papers_tsv = papers_tsv_source
    output_dir.mkdir(parents=True, exist_ok=True)
    answer_question = str(getattr(args, "answer_question", "") or args.question)
    retrieval_question = str(getattr(args, "retrieval_question", "") or args.question)
    task = str(getattr(args, "task", "answer") or "answer")
    configured_planner_backend = str(
        getattr(args, "configured_planner_backend", getattr(args, "planner_backend", "rule_based"))
        or "rule_based"
    )
    configured_answer_backend = str(
        getattr(args, "configured_answer_backend", getattr(args, "backend", "none")) or "none"
    )
    paper_search_mode = None
    if task == "sources":
        paper_search_mode = str(
            getattr(args, "paper_search_mode", "") or classify_paper_search_mode(answer_question)
        )
        if paper_search_mode not in PAPER_SEARCH_MODES:
            raise ValueError(f"Unsupported paper search mode: {paper_search_mode}")
    effective_planner_backend = (
        "rule_based"
        if paper_search_mode == "targeted_lookup"
        else str(getattr(args, "planner_backend", "rule_based") or "rule_based")
    )
    effective_answer_backend = (
        "none" if task == "sources" else str(getattr(args, "backend", "none") or "none")
    )
    settings = resolve_run_settings(args)
    evidence_limit = settings["limit"]
    candidate_multiplier = settings["candidate_multiplier"]
    max_per_paper = settings["max_per_paper"]
    max_chars_per_chunk = settings["max_chars_per_chunk"]
    evidence_selector = str(getattr(args, "evidence_selector", "current") or "current")
    candidate_max_per_paper = candidate_passage_limit(
        task=task,
        evidence_selector=evidence_selector,
        max_per_paper=max_per_paper,
    )
    candidate_limit = max(evidence_limit, evidence_limit * max(candidate_multiplier, 1))
    adaptive_result = None
    validated_corpus_fingerprint: str | None = None
    validated_candidate_record_store: bool | None = None
    retrieval_index_source: Path | None = None
    retrieval_index_read: Path | None = None
    retrieval_cache_generation: str | None = None
    retrieval_cache_generation_path: Path | None = None
    retrieval_cache_receipt_path: Path | None = None
    retrieval_cache_receipt_sha256: str | None = None
    candidate_pool_path = (
        resolve_repo_path(str(args.candidate_pool))
        if getattr(args, "candidate_pool", None)
        else None
    )
    if candidate_pool_path is not None:
        query_plan = plan_queries(
            retrieval_question,
            task_mode=getattr(args, "task_mode", "evidence_qa"),
            planner_backend="rule_based",
            max_queries=max_retrieval_queries(args.task, settings["preset"]),
            task=args.task,
        )
        retrieval_queries = retrieval_queries_from_plan(query_plan)
        results = load_fixed_candidate_pool(
            candidate_pool_path,
            expected_question_id=getattr(args, "candidate_pool_question_id", None),
        )
    elif bool(getattr(args, "adaptive", False)):
        progress("Searching and ranking the local corpus")
        requested_retriever = str(getattr(args, "retriever", "keyword") or "keyword")
        if requested_retriever == "bm25":
            raise ValueError(
                "Adaptive retrieval requires --retriever keyword; omit --adaptive to use bm25"
            )
        adaptive_result = adaptive_retrieve(
            retrieval_question,
            task_mode=getattr(args, "task_mode", "evidence_qa"),
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
            retriever=requested_retriever,
            max_chunks=candidate_limit,
            max_per_paper=candidate_max_per_paper,
        )
        query_plan = adaptive_result["query_plan"]
        retrieval_queries = retrieval_queries_from_plan(query_plan)
        results = adaptive_result["retrieval_candidates"]
    else:
        query_plan = plan_queries(
            retrieval_question,
            task_mode=getattr(args, "task_mode", "evidence_qa"),
            planner_backend=effective_planner_backend,
            planner_model=getattr(args, "planner_model", None),
            max_queries=max_retrieval_queries(args.task, settings["preset"]),
            planner_temperature=float(getattr(args, "planner_temperature", 0.0) or 0.0),
            planner_timeout=int(getattr(args, "planner_timeout", 120) or 120),
            task=args.task,
        )
        retrieval_queries = retrieval_queries_from_plan(query_plan)
        progress("Searching and ranking the local corpus")
        requested_retriever = str(getattr(args, "retriever", "keyword") or "keyword")
        retrieval_index_source = resolve_repo_path(
            str(getattr(args, "retrieval_index", DEFAULT_INDEX_PATH) or DEFAULT_INDEX_PATH)
        )
        retrieval_index_read = retrieval_index_source
        if requested_retriever == "bm25" and retrieval_queries:



            read_bundle = resolve_read_bundle(
                retrieval_index_source,
                chunks_dir_source,
                papers_tsv_source,
            )
            retrieval_index_read = read_bundle.index_path
            if read_bundle.chunks_dir is None or read_bundle.papers_tsv is None:
                raise RuntimeError("Resolved retrieval bundle omitted corpus paths")
            chunks_dir = read_bundle.chunks_dir
            papers_tsv = read_bundle.papers_tsv
            validate_corpus_inputs(chunks_dir, papers_tsv)
            retrieval_cache_generation = read_bundle.cache_generation
            retrieval_cache_generation_path = read_bundle.cache_generation_path
            retrieval_cache_receipt_path = read_bundle.cache_receipt_path
            retrieval_cache_receipt_sha256 = read_bundle.cache_receipt_sha256
            validated_corpus_fingerprint = (
                read_bundle.corpus_content_digest
                if read_bundle.corpus_content_digest is not None
                else ensure_current_index(
                    retrieval_index_source, chunks_dir, papers_tsv
                )
            )
            validated_candidate_record_store = (
                True
                if read_bundle.candidate_record_store_validated
                else None
            )
        results = retrieve_chunks_for_queries(
            retrieval_queries=retrieval_queries,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
            candidate_limit=candidate_limit,
            max_per_paper=candidate_max_per_paper,
            include_low_quality=args.include_low_quality,
            exclude_tables=args.exclude_tables,
            exclude_figures=args.exclude_figures,
            exclude_supplement=args.exclude_supplement,
            category=args.category,
            require_doi=not bool(getattr(args, "allow_missing_doi_sources", False)),
            min_score=args.min_score,
            query_mode=args.query_mode,
            retriever=requested_retriever,
            retrieval_index=retrieval_index_read,
            validated_corpus_fingerprint=validated_corpus_fingerprint,
            validated_candidate_record_store=validated_candidate_record_store,
        )
        results = merge_explicit_doi_anchors(
            answer_question,
            results,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
        )
        results = merge_explicit_named_study_anchors(
            answer_question,
            results,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
        )
        if paper_search_mode == "targeted_lookup" and extract_explicit_dois(
            answer_question
        ):



            results = [row for row in results if row.get("explicit_identifier_anchor")]





    required_entity_groups = required_entity_anchor_groups(answer_question)
    intervention_source_request = (
        task == "sources"
        and is_epcr_pfemp1_intervention_source_request(answer_question)
    )
    entity_anchor_groups = [
        *required_entity_groups,
        *required_context_anchor_groups(answer_question),
    ]
    if entity_anchor_groups:
        results = retain_entity_anchored_evidence(
            results,
            entity_anchor_groups,
            question=answer_question,
            max_chars_per_chunk=max_chars_per_chunk,
            anchor_only_evidence_window=bool(required_entity_groups),
        )
    if task == "sources" and required_entity_groups:
        results = retain_direct_entity_source_evidence(
            results,
            question=answer_question,
            entity_anchor_groups=required_entity_groups,
            max_chars_per_chunk=max_chars_per_chunk,
        )
    if intervention_source_request:
        results = retain_direct_intervention_source_evidence(
            results,
            question=answer_question,
            max_chars_per_chunk=max_chars_per_chunk,
        )
    if task == "answer" and str(args.query_mode or "evidence") == "evidence":
        results = remove_attribution_only_answer_candidates(
            results,
            question=answer_question,
            max_chars_per_chunk=max_chars_per_chunk,
        )

    trace_result = None
    named_study_context_support: list[dict] = []
    named_study_context_reserved_tokens = 0
    if evidence_selector != "current":
        progress("Selecting evidence and auditing study origins")
        trace_candidates = (
            shortlist_answer_candidates(
                results,
                max_papers=12,
                question=answer_question,
                max_chars_per_chunk=max_chars_per_chunk,
            )
            if task == "answer"
            else results
        )
        trace_inputs = [
            freeze_prompt_visible_result(
                answer_question,
                result,
                max_chars_per_chunk,
            )
            for result in trace_candidates
        ]
        trace_inputs = annotate_trace_input_source_types(
            trace_inputs,
            chunks_dir,
        )
        (
            trace_inputs,
            named_study_context_support,
            named_study_context_reserved_tokens,
        ) = reserve_named_study_prompt_context(
            answer_question,
            trace_inputs,
            token_budget=int(getattr(args, "trace_token_budget", 8000) or 8000),
        )
        certifying_selection_token_budget = max(
            1,
            int(getattr(args, "trace_token_budget", 8000) or 8000)
            - named_study_context_reserved_tokens,
        )
        trace_result = run_trace_selection(
            answer_question,
            getattr(args, "task_mode", "evidence_qa"),
            trace_inputs,
            output_dir,
            selector=evidence_selector,
            config_path=resolve_repo_path(getattr(args, "trace_config", "config/trace_rag.yaml")),
            blueprint_path=(resolve_repo_path(args.trace_blueprint) if getattr(args, "trace_blueprint", None) else None),
            lineage_graph_path=(resolve_repo_path(args.trace_lineage_graph) if getattr(args, "trace_lineage_graph", None) else None),
            token_budget=certifying_selection_token_budget,
            selector_algorithm=str(getattr(args, "trace_selector_algorithm", "lazy_greedy")),
        )
        evidence_results = merge_selected_named_study_context(
            trace_result["selected_results"],
            named_study_context_support,
        )
    else:
        evidence_results = results

    progress("Checking source types and evidence coverage")

    paper_search_results = []
    paper_search_origin_records: list[dict] = []
    paper_search_origin_lineage_context: dict[str, list[dict]] = {}
    paper_search_detail_fields: tuple[str, ...] = ()
    paper_search_relevance_gate = {
        "status": "not_applicable",
        "blocked": False,
    }
    if task == "sources":
        paper_search_detail_fields = requested_paper_detail_fields(answer_question)
        paper_search_results = retrieve_papers_for_queries(
            question=answer_question,
            retrieval_queries=retrieval_queries,
            papers_tsv=papers_tsv,
            candidate_limit=candidate_limit,
            result_limit=evidence_limit,
            category=args.category,
            task_mode=getattr(args, "task_mode", "evidence_qa"),
            paper_search_mode=paper_search_mode,
            chunk_results=results,
        )
        if entity_anchor_groups:
            paper_search_results = retain_papers_with_anchored_evidence(
                paper_search_results,
                results,
            )
        if intervention_source_request:
            paper_search_results = retain_papers_with_direct_intervention_evidence(
                paper_search_results,
                results,
            )
        paper_search_results, paper_search_lineage_context = annotate_paper_search_records(
            paper_search_results,
            chunks_dir,
        )
        paper_search_results = retain_direct_clinical_expression_papers(
            answer_question,
            paper_search_results,
            paper_search_lineage_context,
        )
        paper_search_origin_records = [dict(paper) for paper in paper_search_results]
        paper_search_origin_lineage_context = dict(paper_search_lineage_context)
        paper_search_results = collapse_detected_publication_versions(
            paper_search_results,
            paper_search_lineage_context,
        )
        displayed_ids = {
            str(paper.get("paper_id") or "") for paper in paper_search_results
        }
        paper_search_lineage_context = {
            paper_id: context
            for paper_id, context in paper_search_lineage_context.items()
            if paper_id in displayed_ids
        }
        evidence_results = [
            result
            for result in evidence_results
            if str(result.get("paper_id") or "") in displayed_ids
        ]
        if requests_primary_research(answer_question):
            requested_dois = extract_explicit_dois(answer_question)
            paper_search_results = [
                paper
                for paper in paper_search_results
                if str(
                    (paper.get("source_type_assessment") or {}).get("source_type") or ""
                )
                in {"primary_research", "reanalysis_secondary_data"}
                or (
                    paper_search_mode == "targeted_lookup"
                    and paper_search_record_matches_requested_doi(
                        paper,
                        requested_dois,
                    )
                )
            ]
            displayed_ids = {
                str(paper.get("paper_id") or "") for paper in paper_search_results
            }
            paper_search_lineage_context = {
                paper_id: context
                for paper_id, context in paper_search_lineage_context.items()
                if paper_id in displayed_ids
            }
            evidence_results = [
                result
                for result in evidence_results
                if str(result.get("paper_id") or "") in displayed_ids
            ]
        paper_search_relevance_gate = assess_topical_source_search_relevance(
            answer_question,
            evidence_results,
            paper_search_mode=paper_search_mode,
            max_chunks=evidence_limit,
            max_chars_per_chunk=max_chars_per_chunk,
        )
        if paper_search_relevance_gate["blocked"]:




            paper_search_results = []
            paper_search_lineage_context = {}
            evidence_results = []
        displayed_family_ids = {
            str(version.get("paper_id") or "")
            for paper in paper_search_results
            for version in paper.get("publication_family_reports") or [paper]
            if str(version.get("paper_id") or "")
        }
        paper_search_origin_records = [
            paper
            for paper in paper_search_origin_records
            if str(paper.get("paper_id") or "") in displayed_family_ids
        ]
        paper_search_origin_lineage_context = {
            paper_id: context
            for paper_id, context in paper_search_origin_lineage_context.items()
            if paper_id in displayed_family_ids
        }
        paper_search_results = reindex_paper_search_results(paper_search_results)
        if (
            paper_search_results
            and configured_answer_backend != "none"
            and paper_search_mode != "targeted_lookup"
        ):
            progress("Explaining why each paper matches the question")
            explanation_args = {
                "model": str(
                    getattr(args, "answer_model", "") or ollama_answer_model()
                ),
                "timeout": int(getattr(args, "answer_timeout", 120) or 120),
                "document_context": paper_search_lineage_context,
                "requested_fields": paper_search_detail_fields,
            }
            if paper_search_detail_fields:
                paper_search_results, reason_meta = (
                    explain_paper_search_matches_in_batches(
                        answer_question,
                        paper_search_results,
                        results,
                        batch_size=PAPER_DETAIL_BATCH_SIZE,
                        **explanation_args,
                    )
                )
            else:
                reason_candidates = paper_search_results[:PAPER_REASON_BATCH_SIZE]
                explained, reason_meta = explain_paper_search_matches(
                    answer_question,
                    reason_candidates,
                    results,
                    **explanation_args,
                )
                paper_search_results = explained + add_fallback_selection_reasons(
                    paper_search_results[PAPER_REASON_BATCH_SIZE:]
                )
        else:
            paper_search_results = add_fallback_selection_reasons(paper_search_results)
            reason_meta = {
                "attempted": False,
                "used": False,
                "model": None,
                "error": None,
            }
        paper_search_results = add_intervention_evidence_boundaries(
            answer_question,
            paper_search_results,
        )
        paper_search_results = ensure_paper_detail_completeness(
            paper_search_results,
            paper_search_detail_fields,
        )
        if paper_search_detail_fields:
            deterministic_detail_count = sum(
                PAPER_DETAIL_DETERMINISTIC_STATUS
                in set((paper.get("question_field_statuses") or {}).values())
                for paper in paper_search_results
            )
            reason_meta["detail_complete_record_count"] = len(
                paper_search_results
            )
            reason_meta["deterministic_detail_record_count"] = (
                deterministic_detail_count
            )
            reason_meta["fallback_detail_record_count"] = sum(
                PAPER_DETAIL_FALLBACK_STATUS
                in set((paper.get("question_field_statuses") or {}).values())
                for paper in paper_search_results
            )
    else:
        paper_search_lineage_context = {}

    paper_search = None
    if task == "sources":
        planner_attempted = bool(query_plan.get("planner_call_performed", False))
        planner_assisted = planner_attempted and (
            query_plan.get("planner_backend_used") == "ollama"
        )
        paper_search = {
            "mode": paper_search_mode,
            "method": (
                "llm_assisted_query_expansion"
                if planner_assisted
                else (
                    "deterministic_fallback"
                    if planner_attempted
                    else "deterministic_query_expansion"
                )
            ),
            "planner_attempted": planner_attempted,
            "planner_assisted": planner_assisted,
            "planner_fallback_used": bool(query_plan.get("planner_fallback_used", False)),
            "ranking": "deterministic_local_retrieval",
            "generative_answer_used": False,
            "generative_match_explanations_used": bool(reason_meta["used"]),
            "match_explanation_model": reason_meta["model"],
            "match_explanation_error": reason_meta["error"],
            "match_explanation_attempt_errors": list(
                reason_meta.get("attempt_errors") or []
            ),
            "match_explanation_attempt_count": int(
                reason_meta.get("batch_count") or 0
            ),
            "match_explanation_retry_count": int(
                reason_meta.get("retry_call_count") or 0
            ),
            "detail_complete_count": int(
                reason_meta.get("detail_complete_record_count") or 0
            ),
            "detail_fallback_count": int(
                reason_meta.get("fallback_detail_record_count") or 0
            ),
            "requested_detail_fields": list(paper_search_detail_fields),
            "n_matches": len(paper_search_results),
            "topical_relevance_gate": paper_search_relevance_gate,
        }

    pack = build_evidence_pack(
        answer_question,
        evidence_results,
        max_chunks=(len(evidence_results) if trace_result is not None else evidence_limit),
        max_chars_per_chunk=max_chars_per_chunk,
    )
    design_context_by_paper: dict[str, str] = {}
    lineage_context_by_paper: dict[str, list[dict]] = {}
    for chunk in pack.get("chunks", []):
        paper_id = str(chunk.get("paper_id") or "")
        if paper_id and paper_id not in design_context_by_paper:
            lineage_context_by_paper[paper_id] = load_document_lineage_context(
                chunks_dir, paper_id
            )
            design_context_by_paper[paper_id] = document_context_text(
                lineage_context_by_paper[paper_id], max_chars=120_000
            )
        chunk["source_type_assessment"] = classify_source_document(
            chunk,
            "\n".join(
                part
                for part in (
                    design_context_by_paper.get(paper_id, ""),
                    str(chunk.get("text") or ""),
                )
                if part
            ),
        )
    lineage_records = (
        paper_search_origin_records
        if task == "sources"
        else pack.get("chunks", [])
    )
    lineage_context = (
        paper_search_origin_lineage_context
        if task == "sources"
        else lineage_context_by_paper
    )
    distinct_papers = {}
    for chunk in lineage_records:
        paper_id = str(chunk.get("paper_id") or "")
        if paper_id and paper_id not in distinct_papers:
            distinct_papers[paper_id] = chunk
    pack["automatic_lineage_signals"] = [
        signal.model_dump(mode="json")
        for signal in suggest_lineage_signals(
            list(distinct_papers.values()), lineage_context
        )
    ]
    evidence_grade = grade_evidence_pack(pack)
    pack["grade"] = evidence_grade
    pack["bounded_direct_report"] = evidence_grade.get("bounded_direct_report") or {}
    pack["task"] = args.task
    pack["retrieval_question"] = retrieval_question
    pack["retrieval_queries"] = [query.__dict__ for query in retrieval_queries]
    if trace_result is not None:
        pack["answer_candidate_shortlist_count"] = len(trace_candidates)
    if paper_search is not None:
        pack["paper_search"] = paper_search
        pack["paper_search_sources"] = [
            {
                key: paper.get(key)
                for key in (
                    "paper_id",
                    "author",
                    "year",
                    "title",
                    "doi",
                    "source_type_assessment",
                    "entity_source_directness",
                    "clinical_expression_directness",
                    "publication_version_display",
                    "publication_version_relationship_types",
                    "publication_family_reports",
                    "publication_family_report_count",
                    "question_fields",
                    "question_field_statuses",
                    "question_fields_status",
                )
            }
            for paper in paper_search_results
        ]
    if trace_result is not None:
        named_context_chunk_count = sum(
            bool(chunk.get("prompt_context_only"))
            for chunk in pack.get("chunks", [])
        )
        pack["trace_certificate_text_scope"] = (
            "certifying_selected_chunks_only_context_chunks_excluded"
            if named_context_chunk_count
            else "exact_prompt_visible_chunks"
        )
        pack["named_study_prompt_context"] = {
            "support_chunk_count": named_context_chunk_count,
            "reserved_token_cost": named_study_context_reserved_tokens,
            "configured_total_token_budget": int(
                getattr(args, "trace_token_budget", 8000) or 8000
            ),
            "certifying_selection_token_budget": max(
                1,
                int(getattr(args, "trace_token_budget", 8000) or 8000)
                - named_study_context_reserved_tokens,
            ),
            "certificate_effect": "excluded",
            "scope": "same_report_prompt_context",
        }
        pack["trace_sufficiency_certificate"] = trace_result["certificate"].model_dump(mode="json")
        pack["trace_robust_lineage"] = trace_result["robust_lineage"].model_dump(
            mode="json"
        )
        pack["trace_certifying_evidence"] = trace_result[
            "selection"
        ].run_config.get("certifying_evidence", {})

        pack["trace_certification_core"] = trace_result[
            "selection"
        ].run_config.get("certification_core", {})
        apply_certification_core_roles(pack)
        pack["trace_lineage_edges"] = [
            edge.model_dump(mode="json") for edge in trace_result["edges"]
        ]
        pack["trace_lineage_review_priorities"] = [
            priority.model_dump(mode="json")
            for priority in trace_result["lineage_review_priorities"]
        ]
        pack["trace_lineage_review_screening"] = trace_result[
            "lineage_review_screening"
        ]
    retrieval_evidence_is_sufficient = evidence_grade_meets_threshold(
        str(evidence_grade.get("grade") or "bad"),
        str(getattr(args, "min_evidence_grade", "usable") or "usable"),
    )
    evidence_is_sufficient = retrieval_evidence_is_sufficient
    if (
        trace_result is not None
        and trace_result["certificate"].evidence_sufficiency not in {"usable", "good"}
    ):
        evidence_is_sufficient = False
    if (
        trace_result is not None
        and trace_result["robust_lineage"].status != "robustly_sufficient"
    ):
        evidence_is_sufficient = False
    provisional_synthesis = provisional_synthesis_is_authorized(
        requested=bool(getattr(args, "allow_provisional_synthesis", False)),
        task=task,
        retrieval_evidence_is_sufficient=retrieval_evidence_is_sufficient,
        bounded_direct_report_supported=bool(
            pack["bounded_direct_report"].get("supported")
        ),
        trace_available=trace_result is not None,
        evidence_is_sufficient=evidence_is_sufficient,
    )
    pack["provisional_synthesis_authorized"] = provisional_synthesis
    profile_instruction = str(getattr(args, "profile_instruction", "") or "")


    if getattr(args, "conversation_context", None):
        pack["conversation_context"] = args.conversation_context
    prompt = build_citation_answer_prompt(
        answer_question,
        pack,
        task=args.task,
        profile_instruction=profile_instruction,
    )

    retrieved_path = output_dir / "retrieved_chunks.tsv"
    pack_json_path = output_dir / "evidence_pack.json"
    pack_path = output_dir / "evidence_pack.md"
    evidence_table_tsv_path = output_dir / "evidence_table.tsv"
    evidence_table_md_path = output_dir / "evidence_table.md"
    prompt_path = output_dir / "answer_prompt.md"
    config_path = output_dir / "run_config.json"
    query_plan_path = output_dir / "query_plan.json"
    query_plan_source_path = output_dir / "query_plan_source.txt"
    planner_raw_output_path = output_dir / "planner_raw_output.txt"
    planner_validation_path = output_dir / "planner_validation.json"
    retrieval_candidates_path = output_dir / "retrieval_candidates.tsv"
    paper_search_results_path = output_dir / "paper_search_results.json"
    evidence_quality_path = output_dir / "evidence_quality.json"
    grade_json_path = output_dir / "evidence_pack_grade.json"
    grade_md_path = output_dir / "evidence_pack_grade.md"
    answer_path = output_dir / "answer.md"
    partial_answer_path = output_dir / "answer_partial.md"
    withheld_answer_path = output_dir / "answer_withheld.md"
    qc_rejected_answer_path = output_dir / "answer_rejected_qc.md"
    rejected_answer_path = output_dir / "answer_rejected.md"
    answer_qc_path = output_dir / "answer_qc.json"
    attempts_path = output_dir / "answer_attempts.json"
    answer_grounding_report_path = output_dir / "answer_grounding_report.md"
    answer_grounding_issues_path = output_dir / "answer_grounding_issues.tsv"
    answer_grounding_json_path = output_dir / "answer_grounding_report.json"

    write_json(query_plan_path, safe_query_plan(query_plan))
    query_plan_source_path.write_text(str(query_plan.get("query_plan_source") or "rule_based") + "\n", encoding="utf-8")
    if query_plan.get("planner_raw_output") is not None:
        planner_raw_output_path.write_text(str(query_plan.get("planner_raw_output") or ""), encoding="utf-8")
    write_json(planner_validation_path, query_plan.get("planner_validation") or {})
    write_results_tsv(retrieved_path, results)
    write_results_tsv(retrieval_candidates_path, results)
    if paper_search is not None:
        write_json(paper_search_results_path, paper_search_results)
    write_json(pack_json_path, pack)
    pack_path.write_text(format_evidence_pack_markdown(pack), encoding="utf-8")
    write_json(grade_json_path, evidence_grade)
    write_json(evidence_quality_path, evidence_grade)
    grade_md_path.write_text(format_evidence_pack_grade_markdown(evidence_grade), encoding="utf-8")
    write_evidence_table_tsv(evidence_table_tsv_path, pack)
    evidence_table_md_path.write_text(format_evidence_table_markdown(pack), encoding="utf-8")
    prompt_path.write_text(prompt, encoding="utf-8")
    if task == "sources":
        llm_result = LLMResult(backend="none", answer=None)
        answer_qc = {
            "applicable": False,
            "passed": None,
            "task": "sources",
            "warnings": [],
        }
        answer_attempts = []
        answer_backend_meta = {
            "backend_requested": "none",
            "backend_used": "none",
            "model": None,
            "call_performed": False,
            "blocked": False,
            "block_reason": "paper_search_returns_ranked_records",
        }
    else:
        progress("Writing a citation-grounded answer")
        llm_result, answer_qc, answer_attempts, answer_backend_meta = generate_with_qc_repair(
            question=answer_question,
            task=args.task,
            backend=effective_answer_backend,
            initial_prompt=prompt,
            evidence_pack=pack,
            output_dir=output_dir,
            max_repair_attempts=max(0, int(args.repair_attempts or 0)),
            use_template_fallback=not args.no_template_fallback,
            profile_instruction=profile_instruction,
            answer_model=getattr(args, "answer_model", None),
            answer_temperature=float(getattr(args, "answer_temperature", 0.0) or 0.0),
            answer_timeout=int(getattr(args, "answer_timeout", 120) or 120),
            evidence_is_sufficient=(evidence_is_sufficient or provisional_synthesis),
            force_answer=bool(getattr(args, "force_answer", False)),
        )
    progress("Validating claims and citations")
    llm_result, qc_rejected_path = reject_answer_failed_qc(
        llm_result,
        answer_qc,
        qc_rejected_answer_path,
    )
    llm_result, grounding_report, final_answer_path, grounding_rejected_path = (
        persist_grounded_answer(
            llm_result,
            pack,
            task=args.task,
            answer_path=answer_path,
            rejected_answer_path=rejected_answer_path,
            report_path=answer_grounding_report_path,
            issues_path=answer_grounding_issues_path,
            report_json_path=answer_grounding_json_path,
        )
    )
    final_rejected_path = grounding_rejected_path or qc_rejected_path
    final_withheld_answer_path = None
    final_partial_answer_path = final_answer_path if provisional_synthesis else None
    if trace_result is not None and final_answer_path is None and not evidence_is_sufficient:
        bounded_answer = build_trace_withheld_answer(pack) + "\n"
        if trace_publication_family_edges(pack):
            partial_answer_path.write_text(bounded_answer, encoding="utf-8")
            final_partial_answer_path = partial_answer_path
            final_answer_path = partial_answer_path
        else:
            withheld_answer_path.write_text(bounded_answer, encoding="utf-8")
            final_withheld_answer_path = withheld_answer_path
    elif qc_rejected_path is not None:
        withheld_answer_path.write_text(build_qc_withheld_answer(answer_qc) + "\n", encoding="utf-8")
        final_withheld_answer_path = withheld_answer_path
    write_json(answer_qc_path, answer_qc)
    write_json(attempts_path, answer_attempts)
    answer_usage = aggregate_answer_usage(answer_attempts)

    run_config = {
        **runtime_provenance(
            chunks_dir,
            papers_tsv,
            corpus_state_fingerprint=validated_corpus_fingerprint,
        ),
        "question": answer_question,
        "retrieval_question": retrieval_question,
        "retrieval_queries": [query.__dict__ for query in retrieval_queries],
        "planner_backend_requested": query_plan.get("planner_backend_requested", "rule_based"),
        "planner_backend_used": query_plan.get("planner_backend_used", "rule_based"),
        "planner_backend_configured": configured_planner_backend,
        "planner_model": query_plan.get("planner_model"),
        "planner_call_performed": bool(query_plan.get("planner_call_performed", False)),
        "planner_fallback_used": bool(query_plan.get("planner_fallback_used", False)),
        "fallback_used": bool(query_plan.get("planner_fallback_used", False)) or any(
            attempt.get("kind") == "template_fallback" for attempt in answer_attempts
        ),
        "answer_backend_requested": answer_backend_meta.get(
            "backend_requested", effective_answer_backend
        ),
        "answer_backend_used": answer_backend_meta.get("backend_used", llm_result.backend),
        "answer_backend_configured": configured_answer_backend,
        "answer_model": answer_backend_meta.get("model"),
        "answer_temperature": float(getattr(args, "answer_temperature", 0.0) or 0.0),
        "answer_timeout_seconds": int(getattr(args, "answer_timeout", 120) or 120),
        "answer_call_performed": bool(answer_backend_meta.get("call_performed", False)),
        "repository_contract_fallback_used": bool(
            answer_backend_meta.get("repository_contract_fallback_used", False)
        ),
        "answer_prompt_tokens": answer_usage["prompt_eval_count"],
        "answer_generated_tokens": answer_usage["eval_count"],
        "answer_total_duration_seconds": answer_usage["total_duration"] / 1_000_000_000,
        "answer_load_duration_seconds": answer_usage["load_duration"] / 1_000_000_000,
        "backend": effective_answer_backend,
        "task": args.task,
        "task_mode": getattr(args, "task_mode", "evidence_qa"),
        "paper_search_mode": paper_search_mode,
        "paper_search_method": (None if paper_search is None else paper_search["method"]),
        "paper_search_ranking": (None if paper_search is None else paper_search["ranking"]),
        "paper_search_generative_answer_used": (
            None if paper_search is None else paper_search["generative_answer_used"]
        ),
        "paper_search_match_explanation_call_performed": (
            None if paper_search is None else bool(reason_meta["attempted"])
        ),
        "paper_search_match_explanations_used": (
            None if paper_search is None else bool(reason_meta["used"])
        ),
        "paper_search_match_explanation_model": (
            None if paper_search is None else reason_meta["model"]
        ),
        "paper_search_match_explanation_error": (
            None if paper_search is None else reason_meta["error"]
        ),
        "paper_search_match_explanation_attempt_errors": (
            None
            if paper_search is None
            else list(reason_meta.get("attempt_errors") or [])
        ),
        "paper_search_match_explanation_attempt_count": (
            None
            if paper_search is None
            else int(reason_meta.get("batch_count") or 0)
        ),
        "paper_search_match_explanation_retry_count": (
            None
            if paper_search is None
            else int(reason_meta.get("retry_call_count") or 0)
        ),
        "paper_search_detail_complete_count": (
            None
            if paper_search is None
            else int(reason_meta.get("detail_complete_record_count") or 0)
        ),
        "paper_search_detail_fallback_count": (
            None
            if paper_search is None
            else int(reason_meta.get("fallback_detail_record_count") or 0)
        ),
        "paper_search_match_explanation_prompt_tokens": (
            None if paper_search is None else int(reason_meta.get("prompt_eval_count") or 0)
        ),
        "paper_search_match_explanation_generated_tokens": (
            None if paper_search is None else int(reason_meta.get("eval_count") or 0)
        ),
        "paper_search_match_explanation_load_seconds": (
            None
            if paper_search is None
            else int(reason_meta.get("load_duration") or 0) / 1_000_000_000
        ),
        "paper_search_match_explanation_duration_seconds": (
            None
            if paper_search is None
            else int(reason_meta.get("total_duration") or 0) / 1_000_000_000
        ),
        "planner_temperature": float(getattr(args, "planner_temperature", 0.0) or 0.0),
        "planner_timeout_seconds": int(getattr(args, "planner_timeout", 120) or 120),
        "preset": settings["preset"],
        "backend_error": llm_result.error,
        "chunks_dir": str(
            chunks_dir_source.relative_to(REPO_ROOT)
            if chunks_dir_source.is_relative_to(REPO_ROOT)
            else chunks_dir_source
        ),
        "chunks_dir_read": str(
            chunks_dir.relative_to(REPO_ROOT)
            if chunks_dir.is_relative_to(REPO_ROOT)
            else chunks_dir
        ),
        "papers": str(
            papers_tsv_source.relative_to(REPO_ROOT)
            if papers_tsv_source.is_relative_to(REPO_ROOT)
            else papers_tsv_source
        ),
        "papers_read": str(
            papers_tsv.relative_to(REPO_ROOT)
            if papers_tsv.is_relative_to(REPO_ROOT)
            else papers_tsv
        ),
        "adaptive_requested": bool(getattr(args, "adaptive", False)),
        "adaptive_correction_actions": (
            [] if adaptive_result is None else adaptive_result.get("correction_actions", [])
        ),
        "retriever_requested": str(getattr(args, "retriever", "keyword") or "keyword"),
        "retriever": (
            str(getattr(args, "retriever", "keyword") or "keyword")
            if adaptive_result is None
            else adaptive_result.get("retriever", "keyword")
        ),
        "retriever_fallback_reason": (
            None
            if adaptive_result is None
            else adaptive_result.get("retriever_fallback_reason")
        ),
        "retrieval_index": str(getattr(args, "retrieval_index", "")),
        "retrieval_index_source": (
            None if retrieval_index_source is None else str(retrieval_index_source)
        ),
        "retrieval_index_read": (
            None if retrieval_index_read is None else str(retrieval_index_read)
        ),
        "retrieval_cache_generation": retrieval_cache_generation,
        "retrieval_cache_generation_path": (
            None
            if retrieval_cache_generation_path is None
            else str(retrieval_cache_generation_path)
        ),
        "retrieval_cache_receipt_path": (
            None
            if retrieval_cache_receipt_path is None
            else str(retrieval_cache_receipt_path)
        ),
        "retrieval_cache_receipt_sha256": retrieval_cache_receipt_sha256,
        "fixed_candidate_pool": candidate_pool_path is not None,
        "candidate_pool_path": (
            None if candidate_pool_path is None else relative_output(candidate_pool_path)
        ),
        "candidate_pool_sha256": (
            None if candidate_pool_path is None else sha256_file(candidate_pool_path)
        ),
        "candidate_pool_question_id": getattr(
            args, "candidate_pool_question_id", None
        ),
        "limit": evidence_limit,
        "candidate_limit": candidate_limit,
        "candidate_multiplier": candidate_multiplier,
        "max_per_paper": max_per_paper,
        "candidate_max_per_paper": candidate_max_per_paper,
        "max_chars_per_chunk": max_chars_per_chunk,
        "repair_attempts_requested": max(0, int(args.repair_attempts or 0)),
        "repair_attempts_used": sum(1 for attempt in answer_attempts if attempt.get("kind") == "repair"),
        "template_fallback_enabled": not args.no_template_fallback,
        "template_fallback_used": any(
            attempt.get("kind") == "template_fallback" for attempt in answer_attempts
        ),
        "profile_instruction_set": bool(profile_instruction.strip()),
        "answer_qc_passed": answer_qc.get("passed"),
        "answer_grounding_passed": None if grounding_report is None else grounding_report.get("passed"),
        "answer_rejected_by_grounding": grounding_rejected_path is not None,
        "answer_rejected_by_qc": qc_rejected_path is not None,
        "answer_withheld_by_lineage_certificate": final_withheld_answer_path is not None,
        "answer_partial_by_lineage_certificate": final_partial_answer_path is not None,
        "provisional_synthesis_authorized": provisional_synthesis,
        "category": args.category,
        "include_low_quality": args.include_low_quality,
        "query_mode": args.query_mode,
        "min_score": args.min_score,
        "n_chunks": pack["n_chunks"],
        "n_candidate_chunks": len(results),
        "n_paper_search_matches": len(paper_search_results),
        "paper_search_topical_relevance_gate": (
            None if paper_search is None else paper_search_relevance_gate
        ),
        "n_unique_papers": pack["n_unique_papers"],
        "evidence_pack_grade": evidence_grade.get("grade"),
        "evidence_grade": evidence_grade.get("grade"),
        "min_evidence_grade": getattr(args, "min_evidence_grade", "usable"),
        "evidence_sufficient_for_answer": evidence_is_sufficient,
        "retrieval_evidence_sufficient_for_synthesis": retrieval_evidence_is_sufficient,
        "force_answer": bool(getattr(args, "force_answer", False)),
        "evidence_pack_score": evidence_grade.get("score"),
        "evidence_pack_grade_reasons": evidence_grade.get("reasons", []),
        "evidence_selector": evidence_selector,
        "trace_selector_algorithm": getattr(args, "trace_selector_algorithm", None),
        "trace_token_budget": getattr(args, "trace_token_budget", None),
        "trace_certificate_grade": None if trace_result is None else trace_result["certificate"].evidence_sufficiency,
        "trace_allowed_claim_strength": None if trace_result is None else trace_result["certificate"].allowed_claim_strength,
        "trace_robust_lineage_status": (
            None if trace_result is None else trace_result["robust_lineage"].status
        ),
        "trace_robust_effective_evidence_lower": (
            None
            if trace_result is None
            else trace_result["robust_lineage"].effective_evidence.lower
        ),
        "trace_robust_effective_evidence_upper": (
            None
            if trace_result is None
            else trace_result["robust_lineage"].effective_evidence.upper
        ),
        "trace_robust_lineage_count_lower": (
            None if trace_result is None else trace_result["robust_lineage"].lineage_count_lower
        ),
        "trace_robust_lineage_count_upper": (
            None if trace_result is None else trace_result["robust_lineage"].lineage_count_upper
        ),
        "trace_robust_partition_bound_method": (
            None
            if trace_result is None
            else trace_result["robust_lineage"].partition_bound_method
        ),
        "trace_robust_exact_for_declared_uncertainty": (
            None
            if trace_result is None
            else trace_result["robust_lineage"].exact_for_declared_lineage_uncertainty
        ),
        "trace_robust_joint_optimistic_feasibility": (
            None
            if trace_result is None
            else trace_result["robust_lineage"].joint_optimistic_feasibility
        ),
        "trace_lineage_review_priority_count": (
            None
            if trace_result is None
            else len(trace_result["lineage_review_priorities"])
        ),
        "trace_lineage_review_eligible_pair_count": (
            None
            if trace_result is None
            else trace_result["lineage_review_screening"]["eligible_pair_count"]
        ),
        "trace_lineage_review_simulated_pair_count": (
            None
            if trace_result is None
            else trace_result["lineage_review_screening"]["simulated_pair_count"]
        ),
        "trace_lineage_review_screening_exhaustive": (
            None
            if trace_result is None
            else trace_result["lineage_review_screening"]["exhaustive"]
        ),
        "trace_evidence_accounting_version": (
            EVIDENCE_ACCOUNTING_VERSION if trace_result is not None else None
        ),
        "trace_robust_lineage_certificate_version": (
            ROBUST_LINEAGE_CERTIFICATE_VERSION
            if trace_result is not None
            else None
        ),
        "trace_certificate_text_scope": (
            "exact_prompt_visible_chunks" if trace_result is not None else None
        ),
        "trace_lineage_graph_schema_version": (
            trace_result["selection"].run_config.get(
                "lineage_graph_input_schema_version"
            )
            if trace_result is not None
            else None
        ),
        "trace_lineage_graph_runtime_schema_version": (
            LINEAGE_GRAPH_SCHEMA_VERSION if trace_result is not None else None
        ),
        "trace_lineage_graph_input_status": (
            trace_result["selection"].run_config.get("lineage_graph_input_status")
            if trace_result is not None
            else None
        ),
    }
    write_json(config_path, run_config)

    if args.show_context:
        print(pack_path.read_text(encoding="utf-8"))
    if trace_result is not None and bool(getattr(args, "show_trace_certificate", False)):
        print((output_dir / "trace_selection_report.md").read_text(encoding="utf-8"))

    return {
        "results": results,
        "pack": pack,
        "llm_result": llm_result,
        "answer_qc": answer_qc,
        "retrieved_path": retrieved_path,
        "retrieval_candidates_path": retrieval_candidates_path,
        "paper_search_results_path": (
            paper_search_results_path if paper_search is not None else None
        ),
        "query_plan_path": query_plan_path,
        "planner_validation_path": planner_validation_path,
        "pack_path": pack_path,
        "pack_json_path": pack_json_path,
        "evidence_quality_path": evidence_quality_path,
        "evidence_pack_grade_path": grade_json_path,
        "evidence_pack_grade_md_path": grade_md_path,
        "evidence_table_tsv_path": evidence_table_tsv_path,
        "evidence_table_md_path": evidence_table_md_path,
        "prompt_path": prompt_path,
        "answer_path": final_answer_path,
        "partial_answer_path": final_partial_answer_path,
        "withheld_answer_path": final_withheld_answer_path,
        "rejected_answer_path": final_rejected_path,
        "answer_qc_path": answer_qc_path,
        "answer_attempts_path": attempts_path,
        "answer_grounding_report_path": answer_grounding_report_path if grounding_report else None,
        "grounding_report": grounding_report,
        "evidence_grade": evidence_grade,
        "answer_grounding_issues_path": answer_grounding_issues_path if grounding_report else None,
        "answer_attempts": answer_attempts,
        "settings": settings,
        "evidence_selector": evidence_selector,
        "trace_result": trace_result,
        "paper_search": paper_search,
        "paper_search_results": paper_search_results,
    }


def persist_grounded_answer(
    llm_result: LLMResult,
    evidence_pack: dict,
    *,
    task: str,
    answer_path: Path,
    rejected_answer_path: Path,
    report_path: Path,
    issues_path: Path,
    report_json_path: Path,
) -> tuple[LLMResult, dict | None, Path | None, Path | None]:
    if not llm_result.answer:
        return llm_result, None, None, None
    report = check_answer_grounding(llm_result.answer, evidence_pack)
    write_grounding_report(report, report_path, issues_path)
    write_json(report_json_path, report)
    if report.get("passed"):
        answer_path.write_text(llm_result.answer + "\n", encoding="utf-8")
        return llm_result, report, answer_path, None
    rejected_answer_path.write_text(llm_result.answer + "\n", encoding="utf-8")
    error = "answer_rejected_by_grounding_check"
    if llm_result.error:
        error = f"{llm_result.error}; {error}"
    return (
        LLMResult(backend=llm_result.backend, answer=None, error=error),
        report,
        None,
        rejected_answer_path,
    )


def reject_answer_failed_qc(
    llm_result: LLMResult,
    answer_qc: dict,
    rejected_path: Path,
) -> tuple[LLMResult, Path | None]:
    if not llm_result.answer or answer_qc.get("passed") is not False:
        return llm_result, None
    rejected_path.write_text(llm_result.answer + "\n", encoding="utf-8")
    error = "answer_rejected_by_quality_check"
    if llm_result.error:
        error = f"{llm_result.error}; {error}"
    return LLMResult(backend=llm_result.backend, answer=None, error=error), rejected_path


def build_trace_withheld_answer(evidence_pack: dict) -> str:
    certificate = evidence_pack.get("trace_sufficiency_certificate") or {}
    robust = evidence_pack.get("trace_robust_lineage") or {}
    raw_papers = int(certificate.get("raw_paper_count") or evidence_pack.get("n_unique_papers") or 0)
    if raw_papers == 0:
        question_id = str(certificate.get("question_id") or "").strip()
        unresolved = []
        for raw_facet in certificate.get("unresolved_facets", []):
            facet = str(raw_facet or "").strip()
            if question_id and facet.startswith(f"{question_id}_"):
                facet = facet[len(question_id) + 1 :]
            label = facet.replace("_", " ").strip()
            if label:
                unresolved.append(label)
        missing_text = ", ".join(unresolved) or "the question's required concepts"
        return (
            "## No directly matching evidence retrieved\n\n"
            "The current local corpus returned no prompt-visible passage that "
            "jointly satisfied the required question facets, so no literature "
            "synthesis was generated.\n\n"
            f"Missing facets: {missing_text}.\n\n"
            "This is a corpus-conditional retrieval result, not evidence that no "
            "relevant studies exist. A broader search or additional direct full "
            "texts are needed before answering the question."
        )
    missing_pairs = int(robust.get("missing_pair_count") or 0)
    uncertain_pairs = int(robust.get("uncertain_pair_count") or 0)
    reasons = [str(reason) for reason in certificate.get("reasons", []) if str(reason).strip()]
    reason_text = ", ".join(reasons) or "certificate requirements not met"
    family_edges = trace_publication_family_edges(evidence_pack)
    family_count = publication_family_upper_bound(raw_papers, family_edges)
    family_section = ""
    if family_edges:
        pair_lines = []
        for edge in family_edges:
            left = trace_paper_label(evidence_pack, str(edge.get("paper_id_a") or ""))
            right = trace_paper_label(evidence_pack, str(edge.get("paper_id_b") or ""))
            relation = ", ".join(str(value) for value in edge.get("relationship_types", []))
            locator = str(edge.get("origin_evidence_locator") or edge.get("explanation") or "").strip()
            suffix = f" Evidence: {locator}" if locator else ""
            pair_lines.append(f"- {left} ↔ {right} ({relation}).{suffix}")
        family_section = (
            "\n\n### Detected publication-family collapse\n\n"
            + "\n".join(pair_lines)
            + f"\n\nAfter these explicit version links, the {raw_papers} reports represent at most "
            f"{family_count} publication-family units. OriginRAG therefore does not count every "
            "report or DOI as independent evidence. These links collapse publication versions; "
            "they do not by themselves prove that all remaining units are independent study origins."
        )
    heading = (
        "## Publication family identified; independent origins unresolved"
        if family_edges
        else "## Answer withheld: independent origins unresolved"
    )
    return (
        f"{heading}\n\n"
        f"OriginRAG retrieved {raw_papers} reports, but it does not certify an exact independent-origin "
        "count. A report is not treated as an independent study merely because it has a separate DOI.\n\n"
        f"The lineage audit found {missing_pairs} missing and {uncertain_pairs} uncertain paper-pair "
        "relations. Unknown study, cohort, dataset, and publication-family relations remain unknown; "
        "they are never promoted to independent evidence.\n\n"
        f"Certificate status: {robust.get('status', 'unknown')}. Blocking reasons: {reason_text}.\n\n"
        f"{family_section}\n\n"
        "The retrieved excerpts and evidence table remain available, but Qwen was not authorized to "
        f"turn the {raw_papers} reports into a stronger numerical origin claim."
    )


def trace_publication_family_edges(evidence_pack: dict) -> list[dict]:
    paper_ids = {
        str(chunk.get("paper_id") or "")
        for chunk in evidence_pack.get("chunks", [])
        if str(chunk.get("paper_id") or "")
    }
    accepted_relations = {
        "document_explicit_publication_family",
        "same_doi_family",
        "same_preprint_family",
        "same_publication_version",
    }
    return [
        edge
        for edge in evidence_pack.get("trace_lineage_edges", [])
        if str(edge.get("paper_id_a") or "") in paper_ids
        and str(edge.get("paper_id_b") or "") in paper_ids
        and accepted_relations.intersection(edge.get("relationship_types", []))
        and edge.get("origin_review_status") != "rejected"
    ]


def publication_family_upper_bound(raw_papers: int, edges: list[dict]) -> int:
    nodes = {
        str(edge.get(key) or "")
        for edge in edges
        for key in ("paper_id_a", "paper_id_b")
        if str(edge.get(key) or "")
    }
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for edge in edges:
        left, right = str(edge["paper_id_a"]), str(edge["paper_id_b"])
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    collapsed = len(nodes) - len({find(node) for node in nodes})
    return max(0, raw_papers - collapsed)


def trace_paper_label(evidence_pack: dict, paper_id: str) -> str:
    chunks = [
        chunk for chunk in evidence_pack.get("chunks", []) if str(chunk.get("paper_id") or "") == paper_id
    ]
    doi = next((str(chunk.get("doi") or "").strip() for chunk in chunks if chunk.get("doi")), "")
    return f"{paper_id} (DOI {doi})" if doi else paper_id


def build_qc_withheld_answer(answer_qc: dict) -> str:
    warnings = [str(warning) for warning in answer_qc.get("warnings", []) if str(warning).strip()]
    warning_text = ", ".join(warnings) or "quality requirements not met"
    return (
        "## Answer withheld: quality check failed\n\n"
        "OriginRAG generated a draft, but did not publish it as a supported answer because the "
        f"final quality check failed: {warning_text}.\n\n"
        "The rejected draft and its QC report remain in the run directory for diagnosis. No failed "
        "draft is promoted merely because its citations are syntactically grounded."
    )


def max_retrieval_queries(task: str, preset: str) -> int:
    if task == "sources":



        return 6
    if preset in {"pro", "wide"}:
        return 4
    if preset == "quick":


        return 3
    return 3


def retrieval_queries_from_plan(query_plan: dict) -> list[RetrievalQuery]:
    retrieval_queries = []
    for subquery in query_plan.get("subqueries") or query_plan.get("queries") or []:
        if not isinstance(subquery, dict):
            continue
        query_text = str(subquery.get("query") or "").strip()
        if not query_text:
            continue
        label = str(subquery.get("query_id") or subquery.get("query_type") or "query")
        intent = str(subquery.get("label") or subquery.get("query_type") or "").strip()
        retrieval_queries.append(
            RetrievalQuery(label=label, query=query_text, intent=intent or None)
        )
    return retrieval_queries


def retrieve_papers_for_queries(
    *,
    question: str,
    retrieval_queries: list[RetrievalQuery],
    papers_tsv: Path,
    candidate_limit: int,
    result_limit: int,
    category: str | None,
    task_mode: str,
    paper_search_mode: str | None,
    chunk_results: list[dict],
) -> list[dict]:
    """Fuse metadata matches while keeping paper identity and ranking deterministic."""
    ranked_lists = []
    for retrieval_query in retrieval_queries:
        hits = search_papers(
            retrieval_query.query,
            papers_tsv=papers_tsv,
            limit=candidate_limit,
            category=category,
            task_mode=task_mode,
        )
        for hit in hits:
            hit["query_id"] = retrieval_query.label
        ranked_lists.append(hits)
    fused = reciprocal_rank_fusion(ranked_lists, id_key="paper_id")
    if paper_search_mode == "targeted_lookup":
        requested_dois = extract_explicit_dois(question)
        if requested_dois:
            doi_matches = [
                paper
                for paper in fused
                if normalize_doi_text(str(paper.get("doi") or ""))
                in requested_dois
            ]


            return doi_matches[:result_limit]
        exact_identifier = [
            paper
            for paper in fused
            if any(
                marker in str(paper.get("rationale") or "")
                for marker in [
                    "exact DOI match",
                    "exact PMID match",
                    "exact PMCID match",
                    "exact BibKey match",
                ]
            )
        ]
        if exact_identifier:
            return exact_identifier[:result_limit]
        if any(extract_explicit_dois(query.query) for query in retrieval_queries):
            return []
        exact_title = [
            paper
            for paper in fused
            if "exact query phrase in title" in str(paper.get("rationale") or "")
        ]
        if exact_title:
            return exact_title[:result_limit]
        uniquely_resolved_author_year: list[dict] = []
        for reference in explicit_named_study_references(question):
            if not reference.year:
                continue
            surname = normalize_text(reference.surname)
            matches = [
                paper
                for paper in fused
                if str(paper.get("year") or "") == str(reference.year)
                and contains_normalized_term(
                    normalize_text(str(paper.get("author") or "")),
                    surname,
                )
            ]
            if len(matches) == 1:
                row = dict(matches[0])
                rationale = str(row.get("rationale") or "").strip()
                row["rationale"] = (
                    f"{rationale}; exact author/year metadata match".strip("; ")
                )
                row["explicit_named_study_identity_only"] = True
                uniquely_resolved_author_year.append(row)
        if uniquely_resolved_author_year:
            return reciprocal_rank_fusion(
                [uniquely_resolved_author_year],
                id_key="paper_id",
            )[:result_limit]
        anchored_ids = {
            str(chunk.get("paper_id") or "")
            for chunk in chunk_results
            if chunk.get("explicit_identifier_anchor")
            or chunk.get("explicit_named_study_anchor")
        }
        if anchored_ids:
            resolved = [
                paper
                for paper in fused
                if str(paper.get("paper_id") or "") in anchored_ids
            ]
            if not resolved:
                resolved = [
                    chunk
                    for chunk in chunk_results
                    if str(chunk.get("paper_id") or "") in anchored_ids
                ]
            return resolved[:result_limit]



        return []
    chunk_papers = []
    seen_papers = set()
    for chunk in chunk_results:
        paper_id = str(chunk.get("paper_id") or "")
        if not paper_id or paper_id in seen_papers:
            continue
        seen_papers.add(paper_id)
        paper = dict(chunk)
        paper["rationale"] = paper.get("rationale") or "full-text match"
        chunk_papers.append(paper)
    combined = reciprocal_rank_fusion([fused, chunk_papers], id_key="paper_id")
    if named_antibody_anchor_groups(question):
        combined = retain_papers_with_anchored_evidence(combined, chunk_results)
    combined = rerank_direct_clinical_paper_matches(
        question,
        combined,
        chunk_results,
    )
    combined = diversify_explicit_expression_family_results(
        question,
        combined,
        chunk_results,
        result_limit=result_limit,
    )
    return combined[:result_limit]


def diversify_explicit_expression_family_results(
    question: str,
    papers: list[dict],
    chunk_results: list[dict],
    *,
    result_limit: int,
) -> list[dict]:
    """Reserve a few direct family facets in an explicit broad paper search.

    Ordinal clinical-directness tiers can otherwise fill every visible slot
    with PfEMP1 reports before a directly requested RIFIN or whole-transcriptome
    facet is reached.  This bounded reserve is only activated for a source
    request that explicitly lists at least three parasite-expression families.
    Candidates must already have query-anchored full-text evidence, and a
    STEVOR paper is never promoted merely because STEVOR occurs in its title.
    """

    normalized_question = normalize_text(question)
    if not requested_expression_family_alternatives(normalized_question):
        return reindex_paper_search_results(papers)

    anchored_ids = {
        str(result.get("paper_id") or "")
        for result in chunk_results
        if str(result.get("paper_id") or "")
    }
    eligible = [
        paper
        for paper in papers
        if str(paper.get("paper_id") or "") in anchored_ids
    ]
    if not eligible or result_limit <= 0:
        return []

    initial = eligible[:result_limit]
    required: list[dict] = []
    required_ids: set[str] = set()

    def add_candidates(predicate, quota: int, *, strength=None) -> None:
        candidates = [paper for paper in eligible if predicate(paper)]
        if strength is not None:
            candidates.sort(
                key=lambda paper: (
                    -int(strength(paper)),
                    -float(paper.get("rrf_score") or 0.0),
                    int(paper.get("rank") or 10**9),
                )
            )
        seen_titles: set[str] = set()
        kept = 0
        for paper in candidates:
            title_key = normalize_text(str(paper.get("title") or ""))
            if not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            paper_id = str(paper.get("paper_id") or "")
            if paper_id not in required_ids:
                required.append(paper)
                required_ids.add(paper_id)
            kept += 1
            if kept >= quota:
                return

    add_candidates(_is_direct_clinical_rifin_title, 1)
    add_candidates(_is_direct_clinical_stevor_expression_title, 1)
    add_candidates(
        _is_clinical_core_transcriptome_title,
        4,
        strength=_core_transcriptome_title_strength,
    )
    add_candidates(_is_clinical_stage_or_circulation_title, 1)
    add_candidates(_is_nonsevere_expression_comparison_title, 1)

    selected = list(initial)
    selected_ids = {str(paper.get("paper_id") or "") for paper in selected}
    for reserve in required:
        reserve_id = str(reserve.get("paper_id") or "")
        if reserve_id in selected_ids:
            continue
        evict_index = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if str(selected[index].get("paper_id") or "") not in required_ids
            ),
            None,
        )
        if evict_index is None:
            break
        selected_ids.discard(str(selected[evict_index].get("paper_id") or ""))
        selected[evict_index] = {
            **reserve,
            "facet_reserve": True,
        }
        selected_ids.add(reserve_id)

    original_order = {
        str(paper.get("paper_id") or ""): index
        for index, paper in enumerate(eligible)
    }
    selected.sort(
        key=lambda paper: original_order.get(
            str(paper.get("paper_id") or ""),
            10**9,
        )
    )
    return reindex_paper_search_results(selected)


def _title_has_clinical_expression_outcome(title: str) -> bool:
    normalized = normalize_text(title)
    has_malaria_context = any(
        contains_normalized_term(normalized, term)
        for term in ("malaria", "plasmodium", "clinical samples")
    )
    has_outcome = any(
        contains_normalized_term(normalized, term)
        for term in (
            "severe",
            "cerebral",
            "anemia",
            "anaemia",
            "acidosis",
            "hyperlactatemia",
            "hyperlactataemia",
            "respiratory distress",
            "uncomplicated",
            "asymptomatic",
            "clinical presentation",
            "clinical presentations",
            "clinical phenotype",
            "clinical phenotypes",
            "clinical cases",
            "severe manifestations",
            "malaria risk",
            "malaria syndromes",
        )
    )
    return has_malaria_context and has_outcome


def _is_direct_clinical_rifin_title(paper: dict) -> bool:
    title = str(paper.get("title") or "")
    normalized = normalize_text(title)
    return any(
        contains_normalized_term(normalized, term) for term in ("rifin", "rifins")
    ) and (
        _title_has_clinical_expression_outcome(title)
    )


def _is_direct_clinical_stevor_expression_title(paper: dict) -> bool:
    title = str(paper.get("title") or "")
    normalized = normalize_text(title)
    return (
        any(
            contains_normalized_term(normalized, term)
            for term in ("stevor", "stevors")
        )
        and any(
            contains_normalized_term(normalized, term)
            for term in ("expression", "expressed", "transcription", "transcriptome")
        )
        and _title_has_clinical_expression_outcome(title)
    )


def _is_clinical_core_transcriptome_title(paper: dict) -> bool:
    title = str(paper.get("title") or "")
    normalized = normalize_text(title)
    return _title_has_clinical_expression_outcome(title) and any(
        contains_normalized_term(normalized, term)
        for term in (
            "transcriptome",
            "transcriptomic",
            "parasite transcription",
            "gene expression signatures",
        )
    )


def _core_transcriptome_title_strength(paper: dict) -> int:
    normalized = normalize_text(str(paper.get("title") or ""))
    if contains_normalized_term(normalized, "dual transcriptome"):
        return 4
    if contains_normalized_term(normalized, "transcriptome"):
        return 3
    if contains_normalized_term(normalized, "transcriptomic"):
        return 2
    return 1


def _is_clinical_stage_or_circulation_title(paper: dict) -> bool:
    title = str(paper.get("title") or "")
    normalized = normalize_text(title)
    return _title_has_clinical_expression_outcome(title) and any(
        contains_normalized_term(normalized, term)
        for term in (
            "circulation time",
            "developmental stage",
            "stage composition",
            "parasite age",
            "life cycle composition",
        )
    )


def _is_nonsevere_expression_comparison_title(paper: dict) -> bool:
    title = str(paper.get("title") or "")
    normalized = normalize_text(title)
    return (
        any(
            contains_normalized_term(normalized, term)
            for term in ("asymptomatic", "uncomplicated")
        )
        and any(
            contains_normalized_term(normalized, term)
            for term in ("expression", "expresses", "transcriptome", "transcriptomic")
        )
        and any(
            contains_normalized_term(normalized, term)
            for term in ("malaria", "plasmodium")
        )
    )


def rerank_direct_clinical_paper_matches(
    question: str,
    papers: list[dict],
    chunk_results: list[dict],
) -> list[dict]:
    """Rank direct reports ahead of mechanistic and background-only matches.

    RRF remains the ranker inside each ordinal directness tier.  The tiers only
    apply when the question itself requests an entity--clinical-outcome
    relationship.  Facets must co-occur in a title/abstract statement; matches
    distributed across unrelated chunks never become direct evidence merely by
    being in the same paper.
    """

    specification = paper_directness_specification(question)
    if specification is None:



        return reindex_paper_search_results(papers)

    chunks_by_paper: defaultdict[str, list[str]] = defaultdict(list)
    for chunk in chunk_results:
        paper_id = str(chunk.get("paper_id") or "")
        text = " ".join(str(chunk.get("text") or "").split())
        if paper_id and text and len(chunks_by_paper[paper_id]) < 4:
            chunks_by_paper[paper_id].append(text)

    reranked = []
    for paper in papers:
        row = dict(paper)
        tier, reasons = paper_directness_tier(
            title=str(row.get("title") or ""),
            passages=chunks_by_paper.get(str(row.get("paper_id") or ""), []),
            specification=specification,
        )
        row["question_directness_tier"] = tier
        row["question_directness_label"] = {
            2: "direct_clinical_report",
            1: "mechanistic_or_indirect",
            0: "background_or_distributed",
        }[tier]
        row["question_directness_reasons"] = reasons
        reranked.append(row)

    reranked.sort(
        key=lambda row: (
            -int(row.get("question_directness_tier") or 0),
            -float(row.get("rrf_score") or 0.0),
            -float(row.get("score") or 0.0),
            str(row.get("paper_id") or ""),
        )
    )
    return reindex_paper_search_results(reranked)


DIRECTNESS_QUERY_RELATIONS = (
    "association",
    "associations",
    "associated",
    "correlation",
    "correlations",
    "correlated",
    "relationship",
    "relationships",
    "linked",
    "predict",
    "expression",
)

DIRECTNESS_OUTCOME_ALIASES = (
    (
        ("severe", "severity", "critical"),
        ("severe", "severity", "severely", "critical", "disease severity"),
    ),
    (
        ("cerebral", "brain swelling", "neurologic", "neurological"),
        ("cerebral", "brain swelling", "neurologic", "neurological", "brain"),
    ),
    (
        ("clinical phenotype", "clinical phenotypes", "clinical syndrome", "clinical syndromes"),
        (
            "clinical phenotype",
            "clinical phenotypes",
            "clinical syndrome",
            "clinical syndromes",
            "disease phenotype",
            "severe",
            "severity",
            "cerebral",
            "anemia",
            "anaemia",
            "respiratory distress",
            "mortality",
        ),
    ),
    (
        ("mortality", "death", "survival", "fatal"),
        ("mortality", "death", "survival", "fatal", "fatality"),
    ),
    (
        ("outcome", "outcomes", "disease course", "clinical course"),
        ("outcome", "outcomes", "disease course", "clinical course", "susceptibility"),
    ),
)

DIRECTNESS_MEASUREMENT_ALIASES = (
    (
        ("expression", "expressed", "transcription", "transcript", "transcripts"),
        (
            "expression",
            "expressed",
            "expressing",
            "overexpression",
            "overexpressed",
            "transcription",
            "transcript",
            "transcripts",
        ),
    ),
    (
        ("binding", "adhesion", "adherence", "cytoadherence"),
        ("binding", "binds", "adhesion", "adherence", "cytoadherence"),
    ),
    (
        ("antibody", "antibodies", "serologic", "serological"),
        ("antibody", "antibodies", "serologic", "serological", "immunity"),
    ),
)

DIRECTNESS_STRONG_RELATIONS = (
    "associated with",
    "association between",
    "association with",
    "no association",
    "not associated",
    "correlated with",
    "correlation between",
    "not correlated",
    "differentially associated",
    "differential expression",
    "expression increases",
    "expression increased",
    "elevated in",
    "enriched in",
    "higher in",
    "lower in",
    "increased risk",
    "decreased risk",
    "more likely",
    "less likely",
    "indicator of",
    "indicators of",
    "critical for",
    "predictive of",
    "predicts",
    "compared with",
    "compared to",
    "differs between",
    "differed between",
)

DIRECTNESS_WEAK_RELATIONS = (
    "linked to",
    "linking",
    "implicated in",
    "binding to",
    "binds to",
    "mediates",
    "mechanism",
)

DIRECTNESS_REPORT_MARKERS = (
    "we found",
    "we observed",
    "we report",
    "we show",
    "we showed",
    "we demonstrate",
    "we demonstrated",
    "we analyzed",
    "we analysed",
    "we measured",
    "we quantified",
    "we assessed",
    "we evaluated",
    "our results",
    "our data",
    "our findings",
    "in this study",
    "in this dataset",
    "in our dataset",
    "this study found",
    "analysis revealed",
    "was significantly",
    "were significantly",
    "odds ratio",
    "confidence interval",
    "p value",
)

DIRECTNESS_PASSIVE_RESULTS = (
    "was highly expressed",
    "were highly expressed",
    "was overexpressed",
    "were overexpressed",
    "expression was elevated",
    "expression was higher",
    "expression increased",
    "was enriched",
    "were enriched",
    "showed higher expression",
    "had higher expression",
)

DIRECTNESS_BACKGROUND_MARKERS = (
    "previously",
    "previous study",
    "previous studies",
    "prior study",
    "prior studies",
    "has been shown",
    "have been shown",
    "was shown",
    "were shown",
    "reported by",
    "known to",
)

DIRECTNESS_OUTCOME_ONLY_CONTEXT_ALIASES = frozenset(
    {"severity", "severe", "critical"}
)


def paper_directness_specification(question: str) -> dict[str, tuple] | None:
    """Extract only directness facets that are explicitly requested."""

    normalized_question = normalize_text(question)
    if not any(
        contains_normalized_term(normalized_question, marker)
        for marker in DIRECTNESS_QUERY_RELATIONS
    ):
        return None

    entity_aliases = [
        alias
        for group in required_entity_anchor_groups(question)
        for alias in group
        if alias
    ]
    if re.search(r"\bvar\s+(?:gene|genes|expression|transcript|transcripts)\b", normalized_question):
        entity_aliases.extend(("var", "var gene", "var genes", "var expression", "var transcript"))
    entity_aliases = list(dict.fromkeys(entity_aliases))

    context_groups = []
    for raw_group in required_context_anchor_groups(question):
        group = tuple(dict.fromkeys(alias for alias in raw_group if alias))
        normalized_group = {normalize_text(alias) for alias in group}



        if group and not normalized_group.issubset(
            DIRECTNESS_OUTCOME_ONLY_CONTEXT_ALIASES
        ):
            context_groups.append(group)
    outcome_aliases = []
    for triggers, aliases in DIRECTNESS_OUTCOME_ALIASES:
        if any(contains_normalized_term(normalized_question, trigger) for trigger in triggers):
            outcome_aliases.extend(aliases)
    outcome_aliases = list(dict.fromkeys(outcome_aliases))

    measurement_aliases = []
    for triggers, aliases in DIRECTNESS_MEASUREMENT_ALIASES:
        if any(contains_normalized_term(normalized_question, trigger) for trigger in triggers):
            measurement_aliases.extend(aliases)

    if not entity_aliases or not outcome_aliases:
        return None
    return {
        "entity_aliases": tuple(entity_aliases),
        "context_groups": tuple(context_groups),
        "outcome_aliases": tuple(outcome_aliases),
        "measurement_aliases": tuple(dict.fromkeys(measurement_aliases)),
    }


def paper_directness_tier(
    *,
    title: str,
    passages: list[str],
    specification: dict[str, tuple],
) -> tuple[int, list[str]]:
    """Return direct report (2), mechanistic/indirect (1), or background (0)."""

    normalized_title = normalize_text(title)
    document_has_entity = text_has_any_alias(
        " ".join([title, *passages]),
        specification["entity_aliases"],
    )
    title_has_entity = text_has_any_alias(title, specification["entity_aliases"])
    title_has_outcome = text_has_requested_outcome(title, specification)
    title_has_measurement = text_has_requested_measurement(title, specification)
    title_has_strong_relation = text_has_any_alias(
        normalized_title,
        DIRECTNESS_STRONG_RELATIONS,
    )
    title_has_weak_relation = text_has_any_alias(
        normalized_title,
        DIRECTNESS_WEAK_RELATIONS,
    )




    abstract_supports_title_claim = any(
        text_has_any_alias(sentence, specification["entity_aliases"])
        and text_has_requested_outcome(sentence, specification, context_text=title)
        and text_has_requested_measurement(sentence, specification)
        and has_attribution_safe_strong_relation(sentence, require_report=False)
        for sentence in directness_sentences(passages)
    )
    if (
        title_has_outcome
        and title_has_strong_relation
        and (
            (title_has_entity and title_has_measurement)
            or (document_has_entity and abstract_supports_title_claim)
        )
    ):
        return 2, ["title states requested clinical relation; entity verified in title/abstract"]

    direct_sentence = False
    mechanistic_sentence = False
    for sentence in directness_sentences(passages):
        has_entity = text_has_any_alias(sentence, specification["entity_aliases"])
        has_outcome = text_has_requested_outcome(
            sentence,
            specification,
            context_text=title,
        )
        has_measurement = text_has_requested_measurement(sentence, specification)
        if not has_entity or not has_outcome or not has_measurement:
            continue
        has_strong_relation = has_attribution_safe_strong_relation(
            sentence,
            require_report=True,
        )
        has_weak_relation = text_has_any_alias(sentence, DIRECTNESS_WEAK_RELATIONS)
        reports_current_result = text_has_any_alias(
            sentence,
            DIRECTNESS_REPORT_MARKERS,
        ) or is_safe_passive_result(sentence)
        if has_strong_relation and reports_current_result:
            direct_sentence = True
            break
        if has_weak_relation and reports_current_result:
            mechanistic_sentence = True

    if direct_sentence:
        return 2, ["same sentence reports requested entity--clinical-outcome relation"]
    if title_has_entity and title_has_outcome and (title_has_weak_relation or title_has_strong_relation):
        return 1, ["title presents mechanistic or indirect entity--outcome relation"]
    if mechanistic_sentence:
        return 1, ["same sentence reports mechanistic entity--outcome evidence"]
    return 0, ["requested facets occur only as background or across separate passages"]


def text_has_requested_outcome(
    text: str,
    specification: dict[str, tuple],
    *,
    context_text: str = "",
) -> bool:
    """Require a requested clinical qualifier and, when named, disease context."""

    if not text_has_any_alias(text, specification["outcome_aliases"]):
        return False
    context_groups = specification["context_groups"]
    searchable_context = " ".join(part for part in (text, context_text) if part)
    return all(text_has_any_alias(searchable_context, group) for group in context_groups)


def text_has_requested_measurement(
    text: str,
    specification: dict[str, tuple],
) -> bool:
    measurement_aliases = specification["measurement_aliases"]
    return not measurement_aliases or text_has_any_alias(text, measurement_aliases)


def is_safe_passive_result(sentence: str) -> bool:
    """Accept compact passive results while rejecting citation-like background."""

    if not text_has_any_alias(sentence, DIRECTNESS_PASSIVE_RESULTS):
        return False
    if text_has_any_alias(sentence, DIRECTNESS_BACKGROUND_MARKERS):
        return False
    if re.search(r"\[[0-9,;\- ]+\]", sentence):
        return False
    if re.search(
        r"\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.?)?[,]?\s+(?:19|20)\d{2}\)",
        sentence,
    ):
        return False
    return True


def has_attribution_safe_strong_relation(
    sentence: str,
    *,
    require_report: bool,
) -> bool:
    """Keep a reported relation local to its attribution and endpoint.

    A sentence can mention a cited clinical association and then report a new
    mechanistic endpoint (for example, neutrophil killing). A later ``we
    found`` must not turn the earlier cited association into this paper's own
    clinical result.
    """

    if is_safe_passive_result(sentence):
        return True
    normalized = normalize_text(sentence)
    numeric_citation_spans = [
        match.span()
        for match in re.finditer(
            r"\[[0-9,;\-\s]+\]|\([0-9,;\-\s]+\)",
            sentence,
        )
    ]
    report_positions = [
        position
        for marker in DIRECTNESS_REPORT_MARKERS
        if (position := normalized.find(normalize_text(marker))) >= 0
    ]
    for relation in DIRECTNESS_STRONG_RELATIONS:
        normalized_relation = normalize_text(relation)
        start = normalized.find(normalized_relation)
        while start >= 0:




            has_numeric_citation = bool(numeric_citation_spans)
            if not relation_has_external_attribution(normalized, start, normalized_relation):
                if not has_numeric_citation and (not require_report or any(
                    0 <= start - report_position <= 260
                    for report_position in report_positions
                )):
                    return True
            start = normalized.find(normalized_relation, start + len(normalized_relation))
    return False


def relation_has_external_attribution(
    normalized_sentence: str,
    relation_start: int,
    normalized_relation: str,
) -> bool:
    """Detect citation/background attribution attached to a relation phrase."""

    prefix = normalized_sentence[max(0, relation_start - 180) : relation_start]
    if any(
        contains_normalized_term(prefix, marker)
        for marker in DIRECTNESS_BACKGROUND_MARKERS
    ):
        return True
    suffix = normalized_sentence[
        relation_start + len(normalized_relation) : relation_start + len(normalized_relation) + 320
    ]
    return bool(re.search(r"\bet\s+al\b.{0,40}\b(?:19|20)\d{2}\b", suffix))


def text_has_any_alias(text: str, aliases: tuple[str, ...] | list[str]) -> bool:
    normalized = normalize_text(text)
    return any(
        contains_coupled_parasite_omics_language(text)
        if alias == PARASITE_OMICS_ANCHOR
        else contains_normalized_term(normalized, alias)
        for alias in aliases
    )


def directness_sentences(passages: list[str]) -> list[str]:
    sentences = []
    for passage in passages:
        compact_passage = " ".join(str(passage or "").split())
        sentences.extend(
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", compact_passage)
            if sentence.strip()
        )
    return sentences


def reindex_paper_search_results(papers: list[dict]) -> list[dict]:
    return [{**paper, "rank": rank} for rank, paper in enumerate(papers, start=1)]


def requested_paper_detail_fields(question: str) -> tuple[str, ...]:
    """Return explicitly requested per-paper fields, never inferred search facets."""
    normalized = normalize_text(question)
    per_paper_request = bool(
        re.search(
            r"\b(?:for each|per|each) (?:paper|study|article)\b|"
            r"\b(?:pro|je) (?:paper|studie|artikel)\b|"
            r"\bfur (?:jedes|jede|jeden) (?:paper|studie|artikel)\b",
            normalized,
        )
    )
    if not per_paper_request:
        return ()
    patterns = {
        "clinical_phenotype": (
            r"\bclinical (?:phenotypes?|outcomes?|presentations?)\b|"
            r"\bklinisch\w* (?:phanotyp\w*|endpunkt\w*|bild\w*)\b"
        ),
        "parasite_feature": (
            r"\bparasite (?:features?|signals?|genes?|proteins?)\b|"
            r"\bparasitar\w* (?:merkmal\w*|signal\w*|gen\w*|protein\w*)\b"
        ),
        "direction_of_association": (
            r"\bdirection (?:of (?:the )?)?(?:association|effect)\b|"
            r"\b(?:association|effect) direction\b|"
            r"\brichtung (?:der )?(?:assoziation|wirkung)\b"
        ),
        "cohort": (
            r"\bcohorts?\b|\bstudy populations?\b|\bpatient populations?\b|"
            r"\bkohort\w*\b|\bstudienpopulation\w*\b"
        ),
        "assay": (
            r"\bassays?\b|\bmeasurement methods?\b|\bexperimental methods?\b|"
            r"\b(?:mess|analyse)methode\w*\b"
        ),
        "developmental_stage_adjustment": (
            r"\b(?:developmental|parasite|life[- ]cycle) stage\b.{0,100}"
            r"\b(?:adjust|control|correct|account)|"
            r"\b(?:adjust|control|correct|account)\w*\b.{0,100}"
            r"\b(?:developmental|parasite|life[- ]cycle) stage\b|"
            r"\b(?:entwicklungsstad\w*|parasitenalter)\b.{0,100}"
            r"\b(?:adjust|kontroll|korrigier|berucksichtig)\w*\b"
        ),
    }
    return tuple(
        field
        for field in PAPER_SEARCH_DETAIL_FIELDS
        if re.search(patterns[field], normalized)
    )


def explain_paper_search_matches(
    question: str,
    papers: list[dict],
    chunk_results: list[dict],
    *,
    model: str,
    timeout: int = 120,
    document_context: dict[str, list[dict]] | None = None,
    requested_fields: tuple[str, ...] | None = None,
    generator=call_ollama_generate,
) -> tuple[list[dict], dict]:
    """Add question-specific reasons without allowing the model to rerank papers."""
    requested_fields = (
        requested_paper_detail_fields(question)
        if requested_fields is None
        else tuple(field for field in requested_fields if field in PAPER_SEARCH_DETAIL_FIELDS)
    )
    fallback = add_fallback_selection_reasons(papers)
    candidates = paper_reason_candidates(
        papers,
        chunk_results,
        document_context=document_context,
        requested_fields=requested_fields,
    )
    prompt = paper_reason_prompt(
        question,
        candidates,
        requested_fields=requested_fields,
    )
    output_tokens_per_paper = 105 + 45 * len(requested_fields)
    output_token_budget = (
        min(2400, max(700, len(candidates) * 500))
        if requested_fields
        else min(3600, max(600, len(candidates) * output_tokens_per_paper))
    )
    result = generator(
        prompt,
        model=model,
        temperature=0.0,
        timeout=timeout,
        num_predict=output_token_budget,
        format=paper_reason_schema(candidates, requested_fields=requested_fields),
    )
    usage = getattr(result, "metadata", None) or {}
    meta = {
        "attempted": True,
        "used": False,
        "model": result.model,
        "error": result.error,
        "prompt_eval_count": int(usage.get("prompt_eval_count") or 0),
        "eval_count": int(usage.get("eval_count") or 0),
        "load_duration": int(usage.get("load_duration") or 0),
        "total_duration": int(usage.get("total_duration") or 0),
    }
    if not result.text:
        return ensure_paper_detail_completeness(fallback, requested_fields), meta
    try:
        allowed_ids = {str(paper.get("paper_id") or "") for paper in papers}
        reason_records = parse_paper_reason_records(
            result.text,
            allowed_ids,
            requested_fields=requested_fields,
        )
        if not reason_records:
            raise ValueError("no_valid_reasons")
    except (ValueError, json.JSONDecodeError) as exc:
        meta["error"] = f"invalid_match_explanations: {exc}"
        return ensure_paper_detail_completeness(fallback, requested_fields), meta
    enriched = []
    candidates_by_id = {
        str(candidate.get("paper_id") or ""): candidate for candidate in candidates
    }
    for paper in fallback:
        paper_id = str(paper.get("paper_id") or "")
        reason_record = reason_records.get(paper_id, {})
        question_fields = reason_record.get("question_fields")
        question_field_statuses = reason_record.get("question_field_statuses")
        candidate = candidates_by_id.get(paper_id, {})
        supplied_excerpts = list(candidate.get("retrieved_excerpts") or [])
        if isinstance(question_fields, dict):
            question_fields = guard_developmental_stage_detail(
                question_fields,
                supplied_excerpts,
            )
            if (
                question_field_statuses
                and question_fields.get("developmental_stage_adjustment")
                == PAPER_SEARCH_DETAIL_NOT_REPORTED
            ):
                original_value = str(
                    (reason_record.get("question_fields") or {}).get(
                        "developmental_stage_adjustment"
                    )
                    or ""
                )
                if original_value != PAPER_SEARCH_DETAIL_NOT_REPORTED:
                    question_field_statuses = {
                        **question_field_statuses,
                        "developmental_stage_adjustment": PAPER_DETAIL_FALLBACK_STATUS,
                    }
            inferred_stage_detail = infer_developmental_stage_detail(supplied_excerpts)
            if (
                inferred_stage_detail
                and question_fields.get("developmental_stage_adjustment")
                == PAPER_SEARCH_DETAIL_NOT_REPORTED
            ):
                question_fields["developmental_stage_adjustment"] = inferred_stage_detail
                question_field_statuses = {
                    **(question_field_statuses or {}),
                    "developmental_stage_adjustment": PAPER_DETAIL_DETERMINISTIC_STATUS,
                }
            question_fields, question_field_statuses = guard_local_paper_details(
                question_fields,
                question_field_statuses or {},
                supplied_excerpts,
            )
        model_reason = sanitize_model_selection_reason(
            str(reason_record.get("reason") or "")
        )
        use_model_reason = bool(model_reason) and not requested_fields
        enriched.append(
            {
                **paper,
                "selection_reason": (
                    model_reason if use_model_reason else paper["selection_reason"]
                ),
                "selection_reason_status": (
                    "qwen_grounded"
                    if use_model_reason
                    else "deterministic_fallback"
                ),
                **(
                    {
                        "question_fields": question_fields,
                        "question_field_statuses": question_field_statuses,
                    }
                    if question_fields
                    else {}
                ),
            }
        )
    meta["used"] = bool(reason_records)
    return ensure_paper_detail_completeness(enriched, requested_fields), meta


def explain_paper_search_matches_in_batches(
    question: str,
    papers: list[dict],
    chunk_results: list[dict],
    *,
    model: str,
    timeout: int = 120,
    document_context: dict[str, list[dict]] | None = None,
    requested_fields: tuple[str, ...] = (),
    batch_size: int = PAPER_DETAIL_BATCH_SIZE,
    retry_batch_size: int = PAPER_DETAIL_RETRY_BATCH_SIZE,
    max_retry_calls: int = PAPER_DETAIL_MAX_RETRY_CALLS,
    max_model_calls: int = PAPER_DETAIL_MAX_MODEL_CALLS,
    max_total_seconds: int = PAPER_DETAIL_MAX_TOTAL_SECONDS,
    generator=call_ollama_generate,
) -> tuple[list[dict], dict]:
    """Extract requested fields within hard call/time bounds and otherwise mark them absent."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if retry_batch_size < 1:
        raise ValueError("retry_batch_size must be positive")
    if max_retry_calls < 0:
        raise ValueError("max_retry_calls must be non-negative")
    if max_model_calls < 1:
        raise ValueError("max_model_calls must be positive")
    if max_total_seconds < 1:
        raise ValueError("max_total_seconds must be positive")
    explained: list[dict] = []
    batch_metadata: list[dict] = []
    started = time.monotonic()
    model_calls = 0
    budget_exhausted = False
    for start in range(0, len(papers), batch_size):
        elapsed = time.monotonic() - started
        if model_calls >= max_model_calls or elapsed >= max_total_seconds:
            budget_exhausted = True
            explained.extend(add_fallback_selection_reasons(papers[start:]))
            break
        paper_batch = papers[start : start + batch_size]
        batch, metadata = explain_paper_search_matches(
            question,
            paper_batch,
            chunk_results,
            model=model,
            timeout=max(
                1,
                min(timeout, int(max_total_seconds - elapsed)),
            ),
            document_context=document_context,
            requested_fields=requested_fields,
            generator=generator,
        )
        model_calls += 1
        complete_records = sum(
            paper_details_are_resolved(record, requested_fields)
            for record in batch
        )
        metadata = {
            **metadata,
            "complete": complete_records == len(paper_batch),
            "call_kind": "initial",
        }
        if complete_records != len(paper_batch) and not metadata.get("error"):
            metadata["error"] = (
                "incomplete_match_explanations: "
                f"{complete_records}/{len(paper_batch)} records"
            )
        explained.extend(batch)
        batch_metadata.append(metadata)

    retry_calls = 0
    missing = [
        paper
        for paper in explained
        if not paper_details_are_resolved(paper, requested_fields)
    ]
    for start in range(0, len(missing), retry_batch_size):
        elapsed = time.monotonic() - started
        if (
            retry_calls >= max_retry_calls
            or model_calls >= max_model_calls
            or elapsed >= max_total_seconds
        ):
            budget_exhausted = bool(missing[start:])
            break
        retry_input = missing[start : start + retry_batch_size]
        retried, metadata = explain_paper_search_matches(
            question,
            retry_input,
            chunk_results,
            model=model,
            timeout=max(
                1,
                min(timeout, int(max_total_seconds - elapsed)),
            ),
            document_context=document_context,
            requested_fields=requested_fields,
            generator=generator,
        )
        retry_calls += 1
        model_calls += 1
        complete_records = sum(
            paper_details_are_resolved(record, requested_fields)
            for record in retried
        )
        metadata = {
            **metadata,
            "complete": complete_records == len(retry_input),
            "call_kind": "retry",
        }
        if complete_records != len(retry_input) and not metadata.get("error"):
            metadata["error"] = (
                "incomplete_match_explanations: "
                f"{complete_records}/{len(retry_input)} records"
            )
        retry_by_id = {
            str(record.get("paper_id") or ""): record for record in retried
        }
        explained = [
            merge_paper_detail_attempts(
                record,
                retry_by_id.get(str(record.get("paper_id") or "")),
                requested_fields,
            )
            for record in explained
        ]
        batch_metadata.append(metadata)

    explained = ensure_paper_detail_completeness(explained, requested_fields)
    model_extracted_count = sum(
        paper_details_are_model_extracted(record, requested_fields)
        for record in explained
    )
    resolved_count = sum(
        paper_details_are_resolved(record, requested_fields)
        for record in explained
    )
    deterministic_detail_count = sum(
        PAPER_DETAIL_DETERMINISTIC_STATUS
        in set((record.get("question_field_statuses") or {}).values())
        for record in explained
    )
    fallback_detail_count = sum(
        PAPER_DETAIL_FALLBACK_STATUS
        in set((record.get("question_field_statuses") or {}).values())
        for record in explained
    )
    if resolved_count != len(explained):
        elapsed = time.monotonic() - started
        budget_exhausted = budget_exhausted or (
            retry_calls >= max_retry_calls
            or model_calls >= max_model_calls
            or elapsed >= max_total_seconds
        )
    detail_complete_count = sum(
        all(
            field in (record.get("question_fields") or {})
            for field in requested_fields
        )
        for record in explained
    )
    aggregated = aggregate_paper_reason_metadata(batch_metadata)
    aggregated.update(
        {
            "initial_batch_count": sum(
                metadata.get("call_kind") == "initial"
                for metadata in batch_metadata
            ),
            "retry_call_count": retry_calls,
            "model_extracted_record_count": model_extracted_count,
            "deterministic_detail_record_count": deterministic_detail_count,
            "fallback_detail_record_count": fallback_detail_count,
            "detail_complete_record_count": detail_complete_count,
            "complete": resolved_count == len(explained),
            "detail_complete": detail_complete_count == len(explained),
            "model_call_count": model_calls,
            "model_call_limit": max_model_calls,
            "model_time_budget_seconds": max_total_seconds,
            "model_budget_exhausted": budget_exhausted,
        }
    )
    aggregated["attempt_errors"] = [
        f"{metadata.get('call_kind', 'batch')}_{index}: {metadata['error']}"
        for index, metadata in enumerate(batch_metadata, start=1)
        if metadata.get("error")
    ]
    aggregated["error"] = (
        None
        if aggregated["complete"]
        else (
            "incomplete_match_explanations: "
            f"{resolved_count}/{len(explained)} records; "
            "remaining requested fields use explicit not-reported fallback"
        )
    )
    return explained, aggregated


def aggregate_paper_reason_metadata(batch_metadata: list[dict]) -> dict:
    """Aggregate model usage and failures without hiding a partial batch result."""
    models = list(
        dict.fromkeys(
            str(metadata.get("model") or "")
            for metadata in batch_metadata
            if metadata.get("model")
        )
    )
    errors = [
        f"batch_{index}: {metadata['error']}"
        for index, metadata in enumerate(batch_metadata, start=1)
        if metadata.get("error")
    ]
    used_batch_count = sum(bool(metadata.get("used")) for metadata in batch_metadata)
    complete_batch_count = sum(
        bool(metadata.get("complete")) for metadata in batch_metadata
    )
    return {
        "attempted": any(metadata.get("attempted") for metadata in batch_metadata),
        "used": bool(used_batch_count),
        "model": models[0] if len(models) == 1 else (", ".join(models) or None),
        "error": "; ".join(errors) or None,
        "prompt_eval_count": sum(
            int(metadata.get("prompt_eval_count") or 0)
            for metadata in batch_metadata
        ),
        "eval_count": sum(
            int(metadata.get("eval_count") or 0) for metadata in batch_metadata
        ),
        "load_duration": sum(
            int(metadata.get("load_duration") or 0) for metadata in batch_metadata
        ),
        "total_duration": sum(
            int(metadata.get("total_duration") or 0) for metadata in batch_metadata
        ),
        "batch_count": len(batch_metadata),
        "used_batch_count": used_batch_count,
        "complete_batch_count": complete_batch_count,
        "complete": bool(batch_metadata)
        and complete_batch_count == len(batch_metadata),
    }


def paper_details_are_model_extracted(
    paper: dict,
    requested_fields: tuple[str, ...],
) -> bool:
    """Return whether every requested field came from a valid, unverified model record."""
    if not requested_fields:
        return True
    fields = paper.get("question_fields") or {}
    statuses = paper.get("question_field_statuses") or {}
    return all(
        field in fields and statuses.get(field) == PAPER_DETAIL_MODEL_STATUS
        for field in requested_fields
    )


def paper_details_are_resolved(
    paper: dict,
    requested_fields: tuple[str, ...],
) -> bool:
    """Return whether every field has model or deterministic excerpt support."""
    if not requested_fields:
        return True
    fields = paper.get("question_fields") or {}
    statuses = paper.get("question_field_statuses") or {}
    return all(
        field in fields
        and statuses.get(field)
        in {PAPER_DETAIL_MODEL_STATUS, PAPER_DETAIL_DETERMINISTIC_STATUS}
        for field in requested_fields
    )


def ensure_paper_detail_completeness(
    papers: list[dict],
    requested_fields: tuple[str, ...],
) -> list[dict]:
    """Materialize every requested field without inventing absent details."""
    requested_fields = tuple(
        field for field in requested_fields if field in PAPER_SEARCH_DETAIL_FIELDS
    )
    if not requested_fields:
        return [dict(paper) for paper in papers]
    complete = []
    for paper in papers:
        supplied_fields = (
            dict(paper.get("question_fields") or {})
            if isinstance(paper.get("question_fields"), dict)
            else {}
        )
        supplied_statuses = (
            dict(paper.get("question_field_statuses") or {})
            if isinstance(paper.get("question_field_statuses"), dict)
            else {}
        )
        legacy_model_extraction = str(
            paper.get("question_fields_status") or ""
        ) in {
            "qwen_grounded",
            PAPER_DETAIL_MODEL_STATUS,
        }
        fields: dict[str, str] = {}
        statuses: dict[str, str] = {}
        for field in requested_fields:
            if field in supplied_fields:
                value = _clean_paper_detail_value(supplied_fields[field])
                status = str(supplied_statuses.get(field) or "")
                if status == "qwen_grounded":
                    status = PAPER_DETAIL_MODEL_STATUS
                if status not in {
                    PAPER_DETAIL_MODEL_STATUS,
                    PAPER_DETAIL_FALLBACK_STATUS,
                    PAPER_DETAIL_DETERMINISTIC_STATUS,
                }:
                    status = (
                        PAPER_DETAIL_MODEL_STATUS
                        if legacy_model_extraction
                        else PAPER_DETAIL_FALLBACK_STATUS
                    )
            else:
                value = PAPER_SEARCH_DETAIL_NOT_REPORTED
                status = PAPER_DETAIL_FALLBACK_STATUS
            fields[field] = value
            statuses[field] = status
        unique_statuses = set(statuses.values())
        overall_status = (
            PAPER_DETAIL_MODEL_STATUS
            if unique_statuses == {PAPER_DETAIL_MODEL_STATUS}
            else (
                PAPER_DETAIL_FALLBACK_STATUS
                if unique_statuses == {PAPER_DETAIL_FALLBACK_STATUS}
                else (
                    PAPER_DETAIL_DETERMINISTIC_STATUS
                    if unique_statuses == {PAPER_DETAIL_DETERMINISTIC_STATUS}
                    else "mixed_extraction"
                )
            )
        )
        complete.append(
            {
                **paper,
                "question_fields": fields,
                "question_field_statuses": statuses,
                "question_fields_status": overall_status,
            }
        )
    return complete


def merge_paper_detail_attempts(
    original: dict,
    retry: dict | None,
    requested_fields: tuple[str, ...],
) -> dict:
    """Merge a retry by paper ID while retaining deterministic rank order."""
    if retry is None:
        return original
    merged = dict(original)
    if (
        str(merged.get("selection_reason_status") or "") != "qwen_grounded"
        and str(retry.get("selection_reason_status") or "") == "qwen_grounded"
    ):
        merged["selection_reason"] = retry.get("selection_reason")
        merged["selection_reason_status"] = "qwen_grounded"
    original_fields = dict(merged.get("question_fields") or {})
    original_statuses = dict(merged.get("question_field_statuses") or {})
    retry_fields = dict(retry.get("question_fields") or {})
    retry_statuses = dict(retry.get("question_field_statuses") or {})
    for field in requested_fields:
        if (
            original_statuses.get(field)
            not in {PAPER_DETAIL_MODEL_STATUS, PAPER_DETAIL_DETERMINISTIC_STATUS}
            and retry_statuses.get(field)
            in {PAPER_DETAIL_MODEL_STATUS, PAPER_DETAIL_DETERMINISTIC_STATUS}
        ):
            original_fields[field] = retry_fields[field]
            original_statuses[field] = retry_statuses[field]
    merged["question_fields"] = original_fields
    merged["question_field_statuses"] = original_statuses
    return ensure_paper_detail_completeness([merged], requested_fields)[0]


def paper_reason_candidates(
    papers: list[dict],
    chunk_results: list[dict],
    *,
    document_context: dict[str, list[dict]] | None = None,
    requested_fields: tuple[str, ...] = (),
) -> list[dict]:
    document_context = document_context or {}
    chunks_by_paper: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunk_results:
        paper_id = str(chunk.get("paper_id") or "")
        text = " ".join(str(chunk.get("text") or "").split())
        if paper_id and text:
            chunks_by_paper[paper_id].append({**chunk, "text": text})
    candidates = []
    detail_budget = max(1400, min(3000, 24_000 // max(1, len(papers))))
    for paper in papers:
        paper_id = str(paper.get("paper_id") or "")
        if requested_fields:
            excerpts = _paper_detail_excerpts(
                chunks_by_paper.get(paper_id, []),
                document_context.get(paper_id, []),
                requested_fields=requested_fields,
                max_chars=detail_budget,
            )
        else:
            excerpts = [
                str(chunk.get("text") or "")[:800]
                for chunk in chunks_by_paper.get(paper_id, [])[:1]
            ]
        if not excerpts:
            excerpts = _paper_reason_document_excerpts(document_context.get(paper_id, []))
        candidates.append(
            {
                "paper_id": str(paper.get("paper_id") or ""),
                "title": str(paper.get("title") or ""),
                "author": str(paper.get("author") or ""),
                "year": str(paper.get("year") or ""),
                "topics": str(paper.get("topics") or ""),
                "retrieved_excerpts": excerpts,
            }
        )
    return candidates


def _paper_detail_excerpts(
    retrieved_chunks: list[dict],
    document_chunks: list[dict],
    *,
    requested_fields: tuple[str, ...],
    max_chars: int,
) -> list[str]:
    """Select a small result/methods/context bundle for per-paper extraction."""
    field_terms = {
        "clinical_phenotype": (
            "cerebral",
            "anaemia",
            "anemia",
            "acidosis",
            "lactate",
            "respiratory distress",
            "severe malaria",
            "uncomplicated",
            "asymptomatic",
        ),
        "parasite_feature": (
            "expression",
            "transcript",
            "pfemp1",
            "var gene",
            "rifin",
            "stevor",
            "domain cassette",
        ),
        "direction_of_association": (
            "associated",
            "higher",
            "lower",
            "increased",
            "decreased",
            "enriched",
            "correlated",
        ),
        "cohort": (
            "cohort",
            "patients",
            "children",
            "participants",
            "samples",
            "enrolled",
        ),
        "assay": (
            "assay",
            "microarray",
            "rna-seq",
            "rt-pcr",
            "qpcr",
            "transcriptome",
            "sequencing",
        ),
        "developmental_stage_adjustment": (
            "developmental stage",
            "parasite age",
            "hours post invasion",
            "stage adjusted",
            "stage correction",
            "synchron",
        ),
    }

    def score(chunk: dict) -> tuple[int, int]:
        section = normalize_text(str(chunk.get("section") or ""))
        text = normalize_text(str(chunk.get("text") or ""))
        section_score = 0
        if any(label in section for label in ("method", "material", "participant")):
            section_score = 8
        elif any(label in section for label in ("result", "finding")):
            section_score = 7
        elif any(label in section for label in ("abstract", "summary")):
            section_score = 6
        term_score = sum(
            2
            for field in requested_fields
            for term in field_terms[field]
            if contains_normalized_term(text, term)
        )
        return section_score + term_score, -int(chunk.get("chunk_index") or 0)

    ordered = list(retrieved_chunks[:2])
    ordered.extend(
        sorted(document_chunks, key=score, reverse=True)[: max(3, len(requested_fields))]
    )
    excerpts: list[str] = []
    seen: set[str] = set()
    remaining = max_chars
    for chunk in ordered:
        compact = " ".join(str(chunk.get("text") or "").split())
        fingerprint = normalize_text(compact[:240])
        if not compact or not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        section = " ".join(str(chunk.get("section") or "excerpt").split()) or "excerpt"
        page_start = chunk.get("page_start")
        location = f"section={section}"
        if page_start not in (None, ""):
            location += f", page={page_start}"
        prefix = f"[{location}] "
        allowance = min(1100, remaining - len(prefix))
        if allowance < 240:
            break
        excerpts.append(prefix + compact[:allowance])
        remaining -= len(excerpts[-1])
        if remaining < 240:
            break
    return excerpts


def _paper_reason_document_excerpts(chunks: list[dict]) -> list[str]:
    """Return bounded front-matter evidence when a ranked paper had no top chunk."""
    excerpts = []
    for chunk in chunks:
        text = " ".join(str(chunk.get("text") or "").split())
        section = str(chunk.get("section") or "").lower()
        if not text:
            continue
        if section in {"title", "abstract", "summary", "results"} or not excerpts:
            excerpts.append(text[:1800])
        if len(excerpts) >= 2:
            break
    return excerpts


def paper_reason_prompt(
    question: str,
    candidates: list[dict],
    *,
    requested_fields: tuple[str, ...] = (),
) -> str:
    example = {
        "paper_id": "exact supplied ID",
        "reason": "one concise sentence",
        **{field: "concise evidence-grounded value" for field in requested_fields},
    }
    detail_instructions = ""
    if requested_fields:
        detail_instructions = f"""
The user explicitly requested these per-paper fields: {", ".join(requested_fields)}.
Return every requested field for every paper. For a field that is not stated in the supplied
material, use exactly \"{PAPER_SEARCH_DETAIL_NOT_REPORTED}\". Keep each value factual and no
longer than 18 words.
For direction_of_association, report the stated direction or null result, not a direction inferred
from the paper's topic. For developmental_stage_adjustment, distinguish an explicitly reported
adjustment or control from no information. Never write \"not adjusted\" merely because adjustment
is not described; write \"{PAPER_SEARCH_DETAIL_NOT_REPORTED}\" unless the excerpt explicitly says
that developmental stage was not adjusted for.
"""
    return f"""You explain why already-ranked scientific papers are relevant to a user's search.
The retrieval system, not you, selected and ranked these papers. Do not add, remove, or reorder them.

User question:
{question}

Candidate records and retrieved excerpts:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Return JSON only:
{{"reasons": [{json.dumps(example, ensure_ascii=False)}]}}

For every supplied paper ID, write one sentence in the language of the user question. Explain the
scientific connection: what the paper studies, measures, compares, or reports that bears on the
question. Never explain relevance merely as keyword, metadata, ranking, corpus, or full-text match.
Use only the supplied title, topics, and excerpts. Do not infer a result, cohort, independence, or
causal relationship that is not stated. If the supplied material is insufficient, say precisely
that the paper is potentially relevant by scope but the requested relationship is not established
in the retrieved excerpt. Use "associated with" rather than "directly links" unless the excerpt
itself reports a clinical comparison; receptor or cell-binding assays alone establish a mechanistic
measurement, not a clinical-phenotype association.
{detail_instructions}Do not use markdown."""


def paper_reason_schema(
    candidates: list[dict],
    *,
    requested_fields: tuple[str, ...] = (),
) -> dict:
    paper_ids = [str(candidate.get("paper_id") or "") for candidate in candidates]
    item_properties = {
        "paper_id": {"type": "string", "enum": paper_ids},
        "reason": {"type": "string", "minLength": 20, "maxLength": 300},
        **{
            field: {"type": "string", "minLength": 3, "maxLength": 240}
            for field in requested_fields
        },
    }
    return {
        "type": "object",
        "properties": {
            "reasons": {
                "type": "array",
                "minItems": len(paper_ids),
                "maxItems": len(paper_ids),
                "items": {
                    "type": "object",
                    "properties": item_properties,
                    "required": ["paper_id", "reason", *requested_fields],
                },
            }
        },
        "required": ["reasons"],
    }


def parse_paper_reason_output(text: str, allowed_ids: set[str]) -> dict[str, str]:
    """Parse either legacy JSON or truncation-tolerant tab-separated reasons."""
    return {
        paper_id: str(record["reason"])
        for paper_id, record in parse_paper_reason_records(text, allowed_ids).items()
    }


def parse_paper_reason_records(
    text: str,
    allowed_ids: set[str],
    *,
    requested_fields: tuple[str, ...] = (),
) -> dict[str, dict]:
    """Parse grounded match records while accepting the legacy reason-only form."""
    requested_fields = tuple(
        field for field in requested_fields if field in PAPER_SEARCH_DETAIL_FIELDS
    )
    records: dict[str, dict] = {}
    try:
        payload = parse_json_object(text)
        raw_reasons = payload.get("reasons")
        if isinstance(raw_reasons, list):
            for item in raw_reasons:
                if not isinstance(item, dict):
                    continue
                _add_paper_reason_record(
                    records,
                    allowed_ids,
                    item,
                    requested_fields=requested_fields,
                )
            return records
    except (ValueError, json.JSONDecodeError):
        pass
    for line in str(text or "").splitlines():
        if "\t" not in line:
            continue
        paper_id, reason = line.split("\t", 1)
        _add_paper_reason_record(
            records,
            allowed_ids,
            {"paper_id": paper_id, "reason": reason},
            requested_fields=requested_fields,
        )
    return records


def _add_paper_reason_record(
    records: dict[str, dict],
    allowed_ids: set[str],
    item: dict,
    *,
    requested_fields: tuple[str, ...],
) -> None:
    clean_id = str(item.get("paper_id") or "").strip()
    clean_reason = " ".join(str(item.get("reason") or "").split())
    if (
        clean_id not in allowed_ids
        or clean_id in records
        or not 20 <= len(clean_reason) <= 420
    ):
        return
    record = {"reason": clean_reason}
    supplied_fields = [field for field in requested_fields if field in item]
    if supplied_fields:
        record["question_fields"] = {
            field: _clean_paper_detail_value(item.get(field))
            for field in supplied_fields
        }
        record["question_field_statuses"] = {
            field: (
                PAPER_DETAIL_MODEL_STATUS
                if isinstance(item.get(field), str)
                and 3 <= len(" ".join(item[field].split())) <= 420
                else PAPER_DETAIL_FALLBACK_STATUS
            )
            for field in supplied_fields
        }
    records[clean_id] = record


def _clean_paper_detail_value(value: object) -> str:
    if not isinstance(value, str):
        return PAPER_SEARCH_DETAIL_NOT_REPORTED
    clean_value = " ".join(value.split())
    if not 3 <= len(clean_value) <= 420:
        return PAPER_SEARCH_DETAIL_NOT_REPORTED
    return clean_value


def sanitize_model_selection_reason(reason: str) -> str:
    """Remove speculative interpretation tails from a measured-scope explanation."""
    clean = " ".join(str(reason or "").split())
    if not clean:
        return ""
    clean = re.split(
        r"[,;]\s*(?:"
        r"suggesting|indicating|implying|pointing to|providing|supporting|offering|"
        r"linking|potentially contributing"
        r")\b",
        clean,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].rstrip(" ,;")
    return clean if 20 <= len(clean) <= 420 else ""


def guard_local_paper_details(
    question_fields: dict[str, str],
    question_field_statuses: dict[str, str],
    supplied_excerpts: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Reject common cross-paper cohort and clinical-result contamination."""
    guarded = dict(question_fields)
    statuses = dict(question_field_statuses)
    excerpt_text = " ".join(str(excerpt or "") for excerpt in supplied_excerpts)
    statements = [
        statement.strip()
        for excerpt in supplied_excerpts
        for statement in re.split(r"(?<=[.!?;])\s+|[\r\n]+", str(excerpt or ""))
        if statement.strip()
    ]

    cohort = str(guarded.get("cohort") or "")
    if cohort and cohort != PAPER_SEARCH_DETAIL_NOT_REPORTED:
        for aliases in PAPER_DETAIL_GEOGRAPHY_ALIASES:
            if text_has_any_alias(cohort, aliases) and not text_has_any_alias(
                excerpt_text, aliases
            ):
                _replace_paper_detail_with_fallback(guarded, statuses, "cohort")
                break

    phenotype = str(guarded.get("clinical_phenotype") or "")
    phenotype_groups = _detail_alias_groups_in_value(
        phenotype,
        PAPER_DETAIL_PHENOTYPE_ALIASES,
    )
    if phenotype_groups and not all(
        any(
            text_has_any_alias(statement, aliases)
            and not _paper_detail_statement_is_background(statement)
            for statement in statements
        )
        for aliases in phenotype_groups
    ):
        _replace_paper_detail_with_fallback(
            guarded,
            statuses,
            "clinical_phenotype",
        )

    direction = str(guarded.get("direction_of_association") or "")
    direction_groups = _detail_alias_groups_in_value(
        direction,
        PAPER_DETAIL_PHENOTYPE_ALIASES,
    )
    if direction_groups:
        feature_aliases = (
            "expression",
            "expressed",
            "transcript",
            "transcripts",
            "pfemp1",
            "var gene",
            "rifin",
            "stevor",
            "transcriptome",
            "parasite stage",
            "developmental stage",
        )
        relation_aliases = (
            "associated",
            "association",
            "higher",
            "lower",
            "increased",
            "decreased",
            "elevated",
            "enriched",
            "correlated",
            "did not differ",
            "no difference",
        )
        supported = any(
            text_has_any_alias(statement, feature_aliases)
            and text_has_any_alias(statement, relation_aliases)
            and all(text_has_any_alias(statement, aliases) for aliases in direction_groups)
            and not _paper_detail_statement_is_background(statement)
            for statement in statements
        )
        if not supported:
            _replace_paper_detail_with_fallback(
                guarded,
                statuses,
                "direction_of_association",
            )
    return guarded, statuses


def _detail_alias_groups_in_value(
    value: str,
    alias_groups: tuple[tuple[str, ...], ...],
) -> list[tuple[str, ...]]:
    if not value or value == PAPER_SEARCH_DETAIL_NOT_REPORTED:
        return []
    return [aliases for aliases in alias_groups if text_has_any_alias(value, aliases)]


def _paper_detail_statement_is_background(statement: str) -> bool:
    normalized = normalize_text(statement)
    return any(
        contains_normalized_term(normalized, marker)
        for marker in (*DIRECTNESS_BACKGROUND_MARKERS, "previous work", "earlier work")
    ) or bool(re.search(r"\[[0-9,;\- ]+\]", statement))


def _replace_paper_detail_with_fallback(
    fields: dict[str, str],
    statuses: dict[str, str],
    field: str,
) -> None:
    fields[field] = PAPER_SEARCH_DETAIL_NOT_REPORTED
    statuses[field] = PAPER_DETAIL_FALLBACK_STATUS


def guard_developmental_stage_detail(
    question_fields: dict[str, str],
    supplied_excerpts: list[str],
) -> dict[str, str]:
    """Require a local, stage-specific adjustment statement in supplied text."""
    guarded = dict(question_fields)
    value = str(guarded.get("developmental_stage_adjustment") or "").strip()
    if not value or value == PAPER_SEARCH_DETAIL_NOT_REPORTED:
        return guarded
    normalized_value = normalize_text(value)
    claims_no_adjustment = bool(
        re.search(
            r"\b(?:not|no|without|did not|was not|were not)\b.{0,60}"
            r"\b(?:adjust|account|control|correct)",
            normalized_value,
        )
        or re.search(
            r"\b(?:adjust|account|control|correct)\w*\b.{0,40}\bnot\b",
            normalized_value,
        )
        or "unadjusted" in normalized_value
        or bool(re.search(r"\badjust(?:ed|ment)?\s+no\b", normalized_value))
    )
    support = any(
        stage_adjustment_statement_supports(
            sentence,
            negative=claims_no_adjustment,
        )
        for excerpt in supplied_excerpts
        for sentence in re.split(
            r"(?<=[.!?;])\s+|[\r\n]+",
            str(excerpt or ""),
        )
        if sentence.strip()
    )
    if not support:
        guarded["developmental_stage_adjustment"] = (
            PAPER_SEARCH_DETAIL_NOT_REPORTED
        )
    return guarded


def infer_developmental_stage_detail(supplied_excerpts: list[str]) -> str | None:
    """Recover only explicit adjustment or stage-modelling statements from excerpts."""
    statements_by_excerpt = [
        [
            statement.strip()
            for statement in re.split(
                r"(?<=[.!?;])\s+|[\r\n]+",
                str(excerpt or ""),
            )
            if statement.strip()
        ]
        for excerpt in supplied_excerpts
    ]
    current_statements_by_excerpt = [
        [
            statement
            for statement in statements
            if not _paper_detail_statement_is_background(statement)
        ]
        for statements in statements_by_excerpt
    ]
    current_statements = [
        statement
        for statements in current_statements_by_excerpt
        for statement in statements
    ]
    has_explicit_stage_adjustment = any(
        stage_adjustment_statement_supports(statement, negative=False)
        for statement in current_statements
    ) or any(
        re.search(
            r"\b(?:parasite developmental stages?|developmental stages?|"
            r"stage compositions?)\b.{0,700}\bparasite mixtures?\b.{0,140}"
            r"\badjust\w*\b.{0,80}\b(?:gene expression|expression values?)\b",
            normalize_text(" ".join(statements)),
        )
        for statements in current_statements_by_excerpt
    )
    if has_explicit_stage_adjustment:
        return "yes—parasite developmental stage was adjusted or included as a covariate"
    if any(
        stage_adjustment_statement_supports(statement, negative=True)
        for statement in current_statements
    ):
        return "no—analysis explicitly states parasite stage was not adjusted"
    normalized = normalize_text(" ".join(current_statements))
    stage_modelling_patterns = (
        r"\b(?:predict|predicted|estimate|estimated|determine|determined)\w*\b.{0,100}"
        r"\b(?:stage composition|parasite age|developmental stages?)\b",
        r"\b(?:stage composition|parasite age|developmental stages?)\b.{0,100}"
        r"\b(?:predict|predicted|estimate|estimated|maximum likelihood|mixture model)\w*\b",
        r"\bdeconvolution\b.{0,180}\bparasite developmental stages?\b",
    )
    if any(re.search(pattern, normalized) for pattern in stage_modelling_patterns):
        return (
            "stage composition or parasite age was estimated; "
            "no covariate adjustment stated in supplied evidence"
        )
    return None


def stage_adjustment_statement_supports(statement: str, *, negative: bool) -> bool:
    """Check one local statement for adjustment whose object is parasite stage."""
    normalized = normalize_text(statement)
    if not normalized or _paper_detail_statement_is_background(statement):
        return False
    stage = (
        r"(?:parasite developmental stages?|parasite stages?|developmental stages?|"
        r"parasite age|hours post invasion|life cycle stage|"
        r"intraerythrocytic age|stage compositions?)"
    )
    negative_patterns = (
        rf"\b(?:did not|was not|were not)\s+"
        rf"(?:adjust|control|correct|account)\w*\s+(?:for\s+)?(?:the\s+)?{stage}\b",
        rf"\b(?:no|without)\s+(?:adjustment|control|correction)\s+"
        rf"(?:for|of)\s+(?:the\s+)?{stage}\b",
        rf"\b{stage}(?:\s+estimate\w*)?\s+(?:was|were)\s+not\s+"
        rf"(?:adjust|control|correct|account|include|use)\w*\b",
    )
    has_negative_support = any(
        re.search(pattern, normalized) for pattern in negative_patterns
    )
    if negative:
        return has_negative_support
    if has_negative_support:
        return False
    positive_patterns = (
        rf"\b(?:adjusted|controlled|corrected|normalized)\s+"
        rf"(?:for|by|using)\s+(?:the\s+)?{stage}\b",
        rf"\baccounted\s+for\s+(?:the\s+)?{stage}\b",
        rf"\b(?:adjustment|control|correction|normalization)\s+"
        rf"(?:for|of)\s+(?:the\s+)?{stage}\b",
        rf"\b(?:included|entered|used)\s+(?:the\s+)?{stage}"
        rf"(?:\s+estimate\w*)?\s+(?:as|in)\s+(?:a\s+)?"
        rf"(?:covariate|regression|model)\b",
        rf"\b{stage}(?:\s+estimate\w*)?\s+(?:was|were)\s+"
        rf"(?:included|entered|used|incorporated)\s+(?:as|in)\s+(?:a\s+)?"
        rf"(?:covariate|regression|model)\b",
        rf"\b(?:adjusted|controlled|corrected|normalized)\s+(?:the\s+)?"
        rf"(?:gene expression|expression values?|analys(?:is|es)|models?|regressions?)\s+"
        rf"(?:for|by|using)\s+(?:(?:variation|differences?|heterogeneity|"
        rf"estimates?|proportions?)\s+(?:in|of)\s+)?(?:the\s+)?{stage}\b",
        rf"\b{stage}(?:\s+estimate\w*)?\s+(?:was|were)\s+"
        rf"(?:adjusted|controlled|corrected|accounted)\s+for\b",
        rf"\b(?:corrected|normalized|deconvolved)\s+(?:the\s+)?{stage}\b",
        rf"\bincorporat\w*\b.{{0,120}}\b{stage}\b.{{0,80}}\bcovariate\w*\b",
    )
    return any(re.search(pattern, normalized) for pattern in positive_patterns)


def _add_paper_reason(
    reasons: dict[str, str],
    allowed_ids: set[str],
    paper_id: str,
    reason: str,
) -> None:
    records: dict[str, dict] = {}
    _add_paper_reason_record(
        records,
        allowed_ids,
        {"paper_id": paper_id, "reason": reason},
        requested_fields=(),
    )
    if records:
        reasons.update(
            {record_id: str(record["reason"]) for record_id, record in records.items()}
        )


def requests_primary_research(question: str) -> bool:
    normalized = " ".join(str(question or "").lower().split())
    return bool(
        re.search(
            r"\b(?:primary (?:papers?|research|studies|articles?)|original studies|"
            r"empirical (?:papers?|research|studies|articles?)|"
            r"primär(?:e|en|er)? (?:paper|studien|forschung|artikel)|"
            r"primär(?:papers?|studien|forschung|artikel)|"
            r"empirisch(?:e|en|er)? (?:paper|studien|forschung|artikel)|"
            r"(?:estudios?|art[ií]culos?|investigaci[oó]n|investigaciones) primari[oa]s?|"
            r"(?:etudes?|études?|articles?|recherches?|travaux) primaires?)\b",
            normalized,
        )
    )


def add_fallback_selection_reasons(papers: list[dict]) -> list[dict]:
    enriched = []
    for paper in papers:
        title = " ".join(str(paper.get("title") or "this paper").split())
        ranking_reason = str(paper.get("rationale") or "")
        if "exact DOI match" in ranking_reason:
            selection_reason = "Selected because its DOI exactly matches the requested identifier."
        elif "exact PMID match" in ranking_reason:
            selection_reason = "Selected because its PMID exactly matches the requested identifier."
        elif "exact PMCID match" in ranking_reason:
            selection_reason = "Selected because its PMCID exactly matches the requested identifier."
        else:
            selection_reason = (
                f'Potentially relevant from its reported scope ("{title}"), but the '
                "retrieved material did not support a more specific question-level explanation."
            )
        enriched.append(
            {
                **paper,
                "ranking_reason": ranking_reason,
                "selection_reason": selection_reason,
                "selection_reason_status": "deterministic_fallback",
            }
        )
    return enriched


def add_intervention_evidence_boundaries(
    question: str,
    papers: list[dict],
) -> list[dict]:
    """State the retained intervention level without upgrading it to benefit."""

    if not is_epcr_pfemp1_intervention_source_request(question):
        return papers
    level_labels = {
        "human_interventional_outcome": "human interventional outcome evidence",
        "animal_intervention": "animal intervention evidence",
        "preclinical_functional_intervention": (
            "preclinical functional intervention evidence"
        ),
        "experimental_intervention_scope": "experimental intervention evidence",
        "identity_only_unverified": "identity-only, unverified intervention scope",
    }
    bounded: list[dict] = []
    for paper in papers:
        row = dict(paper)
        level = str(row.get("intervention_evidence_level") or "")
        status = str(row.get("clinical_benefit_evidence_status") or "")
        reason = str(row.get("selection_reason") or "").strip()
        if status == "human_clinical_benefit_test_identified":
            boundary = (
                "The retained excerpt reports a human clinical-benefit test; "
                "verify its endpoint and design in the full text."
            )
        elif level == "identity_only_unverified":
            boundary = (
                "This record is retained only because its identifier was requested "
                "exactly; the retained evidence did not verify intervention testing "
                "or a human clinical-benefit test."
            )
        else:
            label = level_labels.get(level, "experimental intervention evidence")
            boundary = (
                f"This is {label}; no human clinical-benefit test was identified "
                "in the retained evidence."
            )
        row["selection_reason"] = f"{reason} {boundary}".strip()
        row["intervention_scope_boundary_status"] = "deterministic"
        bounded.append(row)
    return bounded


def annotate_paper_search_records(
    papers: list[dict], chunks_dir: Path
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Classify every displayed paper and gather its full lineage context."""
    contexts: dict[str, list[dict]] = {}
    annotated = []
    for paper in papers:
        paper_id = str(paper.get("paper_id") or "")
        context = load_document_lineage_context(chunks_dir, paper_id) if paper_id else []
        contexts[paper_id] = context
        full_text = document_context_text(context, max_chars=120_000)
        annotated.append(
            {
                **paper,
                "source_type_assessment": classify_source_document(
                    paper,
                    "\n".join(
                        part for part in (full_text, str(paper.get("text") or "")) if part
                    ),
                ),
            }
        )
    return annotated, contexts


def annotate_trace_input_source_types(
    trace_inputs: list[dict],
    chunks_dir: Path,
) -> list[dict]:
    """Classify each prompt-visible report before TRACE scores direct roles.

    TRACE receives at most a bounded shortlist, but one selected excerpt can
    omit the document's review declaration. Read only the beginning of each
    shortlisted document, cache that bounded text by file identity, and reuse
    one assessment for repeated chunks from the same report.
    """

    assessments: dict[str, dict[str, object]] = {}
    annotated: list[dict] = []
    for row in trace_inputs:
        paper_id = str(row.get("paper_id") or "").strip()
        assessment = assessments.get(paper_id) if paper_id else None
        if assessment is None:
            front_text = (
                load_document_design_context(
                    chunks_dir,
                    paper_id,
                    max_chars=SOURCE_TYPE_FRONT_TEXT_MAX_CHARS,
                )
                if paper_id
                else ""
            )
            assessment = classify_source_document(
                row,
                front_text or str(row.get("text") or ""),
            )
            if paper_id:
                assessments[paper_id] = assessment
        annotated.append(
            {
                **row,
                "source_type_assessment": dict(assessment),
            }
        )
    return annotated


def assess_topical_source_search_relevance(
    question: str,
    candidates: list[dict],
    *,
    paper_search_mode: str | None,
    max_chunks: int,
    max_chars_per_chunk: int,
) -> dict:
    """Fail closed only for an extreme topical-discovery mismatch.

    Paper search and passage retrieval deliberately have permissive lexical
    candidate stages. A non-empty pool must not become a public source list
    when its only overlap consists of generic words such as ``primary`` or
    ``type``. Reuse the standard evidence-pack grader so this guard has one
    declared relevance definition rather than a second ad hoc score.

    Targeted lookups are exempt because an exact DOI can be resolved from
    metadata without local full text. A broad multifacet search is also not
    blocked merely because no single passage covers every requested facet: the
    gate requires a ``bad`` grade and every matched question term to be a
    generic cross-domain word. One distinctive anchor such as a gene symbol
    keeps the result visible even when untranslated request words make the
    aggregate coverage ratio look artificially small. Generic scientific nouns
    such as ``cell``, ``energy`` or ``model`` do not count as such an anchor.
    """

    if paper_search_mode != "topical_discovery":
        return {
            "status": "not_applicable",
            "blocked": False,
            "reason": "not_topical_discovery",
        }
    if not candidates:
        return {
            "status": "not_applicable",
            "blocked": False,
            "reason": "no_candidate_evidence",
        }

    provisional_pack = build_evidence_pack(
        question,
        candidates,
        max_chunks=max_chunks,
        max_chars_per_chunk=max_chars_per_chunk,
    )
    assessment = grade_evidence_pack(provisional_pack)
    reasons = list(assessment.get("reasons") or [])
    metrics = assessment.get("metrics") or {}
    question_term_hits = {
        str(term)
        for term in metrics.get("question_term_hits") or []
        if str(term)
    }
    distinctive_question_term_hits = sorted(
        question_term_hits.difference(TOPICAL_SOURCE_GENERIC_OVERLAP_TERMS)
    )
    blocked = bool(
        assessment.get("grade") == "bad"
        and not distinctive_question_term_hits
    )
    return {
        "status": (
            "blocked_extreme_topical_mismatch" if blocked else "passed"
        ),
        "blocked": blocked,
        "criterion": "bad_grade_and_only_generic_term_overlap",
        "pre_filter_grade": assessment.get("grade"),
        "pre_filter_score": assessment.get("score"),
        "pre_filter_reasons": reasons,
        "question_term_coverage": metrics.get("question_term_coverage"),
        "max_passage_question_term_coverage": metrics.get(
            "max_passage_question_term_coverage"
        ),
        "distinctive_question_term_hits": distinctive_question_term_hits,
    }


def evidence_grade_meets_threshold(grade: str, threshold: str) -> bool:
    return GRADE_ORDER.get(str(grade or "bad"), 0) >= GRADE_ORDER.get(str(threshold or "usable"), 2)


def provisional_synthesis_is_authorized(
    *,
    requested: bool,
    task: str,
    retrieval_evidence_is_sufficient: bool,
    bounded_direct_report_supported: bool,
    trace_available: bool,
    evidence_is_sufficient: bool,
) -> bool:
    """Allow weak-grade output only as a TRACE-bounded individual-paper report."""

    return bool(
        requested
        and task == "answer"
        and trace_available
        and not evidence_is_sufficient
        and (retrieval_evidence_is_sufficient or bounded_direct_report_supported)
    )


def safe_query_plan(query_plan: dict) -> dict:
    payload = dict(query_plan)
    payload.pop("planner_raw_output", None)
    return payload


def load_fixed_candidate_pool(
    path: Path, *, expected_question_id: str | None = None
) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"fixed candidate pool does not exist: {path}")
    rows = read_jsonl(path)
    if not rows:
        raise ValueError(f"fixed candidate pool is empty: {path}")
    chunk_ids = [str(row.get("chunk_id") or "").strip() for row in rows]
    if any(not chunk_id for chunk_id in chunk_ids):
        raise ValueError("every fixed candidate row requires a chunk_id")
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("fixed candidate pool contains duplicate chunk_id values")
    if any(not str(row.get("paper_id") or "").strip() for row in rows):
        raise ValueError("every fixed candidate row requires a paper_id")
    if expected_question_id:
        mismatched = sorted(
            {
                str(row.get("question_id") or "").strip()
                for row in rows
                if str(row.get("question_id") or "").strip()
                != expected_question_id
            }
        )
        if mismatched:
            raise ValueError(
                "fixed candidate pool question_id mismatch: " + ", ".join(mismatched)
            )
    return [normalize_fixed_candidate_row(row, rank=index) for index, row in enumerate(rows, 1)]


def normalize_fixed_candidate_row(row: dict, *, rank: int) -> dict:
    normalized = dict(row)
    normalized.setdefault("rank", rank)
    normalized.setdefault("score", row.get("raw_retrieval_score", 0))
    normalized.setdefault("snippet", str(row.get("text") or "")[:500])
    return normalized


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def retrieve_chunks_for_queries(
    *,
    retrieval_queries: list[RetrievalQuery],
    chunks_dir: Path,
    papers_tsv: Path,
    candidate_limit: int,
    max_per_paper: int,
    include_low_quality: bool,
    exclude_tables: bool,
    exclude_figures: bool,
    exclude_supplement: bool,
    category: str | None,
    require_doi: bool,
    min_score: float,
    query_mode: str,
    retriever: str = "keyword",
    retrieval_index: Path = DEFAULT_INDEX_PATH,
    validated_corpus_fingerprint: str | None = None,
    validated_candidate_record_store: bool | None = None,
) -> list[dict]:
    retrieval_queries = list(retrieval_queries or [])
    candidate_record_store_available = validated_candidate_record_store
    if (
        retriever == "bm25"
        and retrieval_queries
        and validated_corpus_fingerprint is None
    ):



        ensure_current_index(retrieval_index, chunks_dir, papers_tsv)
    if (
        retriever == "bm25"
        and retrieval_queries
        and candidate_record_store_available is None
    ):
        candidate_record_store_available = index_has_candidate_record_store(
            retrieval_index,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
        )

    ranked_lists: list[list[dict]] = []
    for retrieval_query in retrieval_queries:
        search_backend = search_chunks_bm25 if retriever == "bm25" else search_chunks
        search_kwargs = {}
        if retriever == "bm25":
            search_kwargs["index_path"] = retrieval_index
            search_kwargs["validate_index"] = False
            search_kwargs["candidate_record_store_available"] = (
                candidate_record_store_available
            )
        query_results = search_backend(
            query=retrieval_query.query,
            chunks_dir=chunks_dir,
            papers_tsv=papers_tsv,
            limit=candidate_limit,
            max_per_paper=max_per_paper,
            include_low_quality=include_low_quality,
            exclude_references=True,
            exclude_tables=exclude_tables,
            exclude_figures=exclude_figures,
            exclude_supplement=exclude_supplement,
            category=category,
            require_doi=require_doi,
            min_score=min_score,
            query_mode=query_mode_for_retrieval(retrieval_query, query_mode),
            **search_kwargs,
        )
        decorated: list[dict] = []
        for result in query_results:
            candidate = dict(result)
            candidate["query_id"] = retrieval_query.label
            candidate["retrieval_query_labels"] = retrieval_query.label
            candidate["retrieval_query"] = retrieval_query.query
            decorated.append(candidate)
        ranked_lists.append(decorated)

    candidates = reciprocal_rank_fusion(ranked_lists)
    candidates = rank_candidates_by_paper_rrf(candidates)
    candidates = promote_intent_passages(
        candidates,
        data_accessibility_query_labels={
            query.label
            for query in retrieval_queries
            if (query.intent or query.label) == "data_accessibility"
        },
    )
    for candidate in candidates:
        labels = list(candidate.get("contributing_queries") or [])
        candidate["retrieval_query_labels"] = ";".join(labels)
        candidate["retrieval_query_hits"] = len(labels)
    results = diversify_merged_results(candidates, limit=candidate_limit, max_per_paper=max_per_paper)
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
    return results


def retain_entity_anchored_evidence(
    candidates: list[dict],
    anchor_groups: list[tuple[str, ...]],
    *,
    question: str = "",
    max_chars_per_chunk: int | None = None,
    anchor_only_evidence_window: bool = True,
) -> list[dict]:
    """Keep only passages that explicitly carry every required entity.

    A broad topical or methodological match is not direct evidence for a named
    gene or protein. Filtering before evidence selection prevents shared words
    such as ``signature`` or ``annotation`` from displacing exact entity hits.
    """
    retained = []
    for candidate in candidates:
        copy = dict(candidate)
        copy["required_entity_anchors"] = [list(group) for group in anchor_groups]




        copy["anchor_only_evidence_window"] = bool(anchor_only_evidence_window)
        exact_named_report_context = bool(
            copy.get("explicit_named_study_support")
            and copy.get("named_study_source_text_scope")
            == "fulltext_question_support"
        )
        if (
            copy.get("explicit_identifier_anchor")
            or copy.get("explicit_named_study_anchor")
            or exact_named_report_context
        ):






            copy["required_entity_anchors_verified"] = False
            copy["identifier_identity_anchor_verified"] = bool(
                copy.get("explicit_identifier_anchor")
            )
            if copy.get("explicit_identifier_anchor"):
                copy["entity_source_directness"] = (
                    "exact_identifier_identity_only"
                )
            copy["named_study_identity_anchor_verified"] = bool(
                copy.get("explicit_named_study_anchor")
                or exact_named_report_context
            )
            retained.append(copy)
            continue
        copy["required_entity_anchors_verified"] = True
        visible = (
            freeze_prompt_visible_result(question, copy, max_chars_per_chunk)
            if question and max_chars_per_chunk is not None
            else copy
        )
        raw_searchable = "\n".join(
            str(visible.get(field) or "") for field in ("title", "text")
        )
        searchable = normalize_text(raw_searchable)
        if all(
            any(
                contains_entity_anchor_alias(raw_searchable, searchable, alias)
                for alias in group
            )
            for group in anchor_groups
        ):
            retained.append(copy)
    return retained


def retain_papers_with_anchored_evidence(
    papers: list[dict],
    anchored_candidates: list[dict],
) -> list[dict]:
    """Remove metadata-only paper matches from an entity-specific source list."""
    anchored_paper_ids = {
        str(result.get("paper_id") or "")
        for result in anchored_candidates
        if str(result.get("paper_id") or "")
    }
    directness_by_paper = {
        str(result.get("paper_id") or ""): str(
            result.get("entity_source_directness") or ""
        )
        for result in anchored_candidates
        if str(result.get("paper_id") or "")
        and str(result.get("entity_source_directness") or "")
    }
    return [
        {
            **paper,
            **(
                {
                    "entity_source_directness": directness_by_paper[
                        str(paper.get("paper_id") or "")
                    ]
                }
                if str(paper.get("paper_id") or "") in directness_by_paper
                else {}
            ),
        }
        for paper in papers
        if str(paper.get("paper_id") or "") in anchored_paper_ids
    ]


def retain_direct_intervention_source_evidence(
    candidates: list[dict],
    *,
    question: str,
    max_chars_per_chunk: int | None = None,
) -> list[dict]:
    """Keep passages that directly test an intervention named by the query.

    Association, structural-mechanism, and target-rationale passages remain
    useful for normal questions, but must not be displayed as intervention
    studies when the user explicitly asks whether clinical benefit was tested.
    """

    retained: list[dict] = []
    for candidate in candidates:
        if candidate.get("explicit_identifier_anchor"):



            retained.append(
                {
                    **candidate,
                    "intervention_evidence_level": "identity_only_unverified",
                    "clinical_benefit_evidence_status": (
                        "not_identified_in_retained_evidence"
                    ),
                    "intervention_evidence_route": (
                        "explicit_identifier_identity_only"
                    ),
                }
            )
            continue
        visible = (
            freeze_prompt_visible_result(question, candidate, max_chars_per_chunk)
            if max_chars_per_chunk is not None
            else candidate
        )
        assessment = assess_intervention_evidence(
            title=str(candidate.get("title") or ""),
            text=str(visible.get("text") or ""),
            section=str(candidate.get("section") or ""),
        )
        if assessment is None:
            continue
        retained.append(
            {
                **candidate,
                "intervention_evidence_level": assessment.evidence_level,
                "clinical_benefit_evidence_status": (
                    assessment.clinical_benefit_status
                ),
                "intervention_evidence_route": assessment.route,
            }
        )
    return retained


def retain_papers_with_direct_intervention_evidence(
    papers: list[dict],
    candidates: list[dict],
) -> list[dict]:
    """Restrict a paper list to direct intervention evidence and copy its scope."""

    level_order = {
        "human_interventional_outcome": 4,
        "animal_intervention": 3,
        "preclinical_functional_intervention": 2,
        "experimental_intervention_scope": 1,
        "identity_only_unverified": 0,
    }
    assessment_by_paper: dict[str, dict] = {}
    for candidate in candidates:
        paper_id = str(candidate.get("paper_id") or "")
        level = str(candidate.get("intervention_evidence_level") or "")
        if not paper_id or not level:
            continue
        previous = assessment_by_paper.get(paper_id)
        if previous is None or level_order.get(level, 0) > level_order.get(
            str(previous.get("intervention_evidence_level") or ""),
            0,
        ):
            assessment_by_paper[paper_id] = candidate

    annotation_fields = (
        "intervention_evidence_level",
        "clinical_benefit_evidence_status",
        "intervention_evidence_route",
    )
    return [
        {
            **paper,
            **{
                field: assessment_by_paper[paper_id].get(field)
                for field in annotation_fields
            },
        }
        for paper in papers
        if (paper_id := str(paper.get("paper_id") or "")) in assessment_by_paper
    ]


SOURCE_DIRECT_SECTIONS = {
    "abstract",
    "findings",
    "materials and methods",
    "methods",
    "results",
    "summary",
}
SOURCE_OWN_STUDY_PATTERNS = (
    r"\bwe (?:analysed|analyzed|assessed|characterized|compared|demonstrated|"
    r"examined|found|identified|investigated|measured|observed|report|reported|"
    r"show|showed|tested)\b",
    r"\bour (?:analysis|data|experiment|experiments|findings|results|study)\b",
    r"\b(?:here,? we|in this study|the present study|this study)\b",
    r"\b(?:results|findings) (?:demonstrate|demonstrated|indicate|indicated|"
    r"show|showed|suggest|suggested)\b",
    r"\b(?:was|were) (?:analysed|analyzed|assessed|characterized|compared|"
    r"measured|tested)\b",
)
SOURCE_BACKGROUND_ATTRIBUTION_PATTERNS = (
    r"\b(?:previous|prior|earlier|other|several|recent) "
    r"(?:study|studies|work|reports?)\b",
    r"\b(?:studies|reports?) (?:have |has )?"
    r"(?:shown|reported|found|described|suggested|proposed|implicated)\b",
    r"\b(?:has|have|had) been "
    r"(?:shown|reported|found|described|associated|suggested|proposed|implicated)\b",
    r"\b(?:according to|reported by|suggested by|proposed by)\b",
    r"\bsuch (?:rifin|stevor|binding|interaction|rosetting)\b",
    r"\[[0-9,;\-–—\s]+\]",
    r"\(\s*\d+(?:\s*[-–—,;]\s*\d+)*\s*\)",
    r"\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.?)?[,]?\s+(?:19|20)\d{2}\)",
)


def retain_direct_entity_source_evidence(
    candidates: list[dict],
    *,
    question: str,
    entity_anchor_groups: list[tuple[str, ...]],
    max_chars_per_chunk: int | None = None,
) -> list[dict]:
    """Keep study-level evidence for an explicit entity-function search.

    A paper can qualify through a direct title or through its own abstract,
    methods, or results. A lone background attribution in a Discussion does
    not turn the citing paper into a primary study of the named entity. The
    input order is retained so this guard never becomes a second ranker.
    """

    normalized_question = normalize_text(question)
    function_aliases = requested_source_function_alternatives(
        normalized_question
    )
    title_function_aliases = requested_source_function_title_alternatives(
        normalized_question
    )
    if named_antibody_anchor_groups(question):
        function_aliases = ("antibody", "antibodies", "antiserum", "antisera")
        title_function_aliases = ()
    if not function_aliases or not entity_anchor_groups:
        return candidates

    retained: list[dict] = []
    for candidate in candidates:
        directness = direct_entity_source_evidence(
            candidate,
            question=question,
            entity_anchor_groups=entity_anchor_groups,
            function_aliases=function_aliases,
            title_function_aliases=title_function_aliases,
            max_chars_per_chunk=max_chars_per_chunk,
        )
        if directness is None and candidate.get("explicit_identifier_anchor"):





            directness = "exact_identifier_identity_only"
        if directness is None:
            continue
        retained.append(
            {
                **candidate,
                "entity_source_directness": directness,
            }
        )
    return retained


def direct_entity_source_evidence(
    candidate: dict,
    *,
    question: str,
    entity_anchor_groups: list[tuple[str, ...]],
    function_aliases: tuple[str, ...],
    title_function_aliases: tuple[str, ...],
    max_chars_per_chunk: int | None,
) -> str | None:
    """Return the evidence route supporting direct paper-topic relevance."""

    normalized_question = normalize_text(question)
    direct_entity_groups = list(entity_anchor_groups)
    if requested_immune_receptor_binding(normalized_question):



        direct_entity_groups.append(IMMUNE_RECEPTOR_BINDING_ALIASES)

    title = str(candidate.get("title") or "")
    normalized_title = normalize_text(title)
    if source_text_carries_entity_and_function(
        title,
        normalized_title,
        direct_entity_groups,
        title_function_aliases,
    ):
        return "title_entity_function"

    visible = (
        freeze_prompt_visible_result(question, candidate, max_chars_per_chunk)
        if max_chars_per_chunk is not None
        else candidate
    )





    text = strip_repeated_source_title(
        str(visible.get("text") or ""),
        title,
    )
    section = str(candidate.get("section") or "unknown").strip().lower()
    for statement in source_evidence_statements(text):
        normalized_statement = normalize_text(statement)
        if not source_text_carries_entity_and_function(
            statement,
            normalized_statement,
            direct_entity_groups,
            function_aliases,
        ):
            continue
        own_study = any(
            re.search(pattern, statement, flags=re.IGNORECASE)
            for pattern in SOURCE_OWN_STUDY_PATTERNS
        )
        attributed_background = any(
            re.search(pattern, statement, flags=re.IGNORECASE)
            for pattern in SOURCE_BACKGROUND_ATTRIBUTION_PATTERNS
        )
        if own_study:
            return "own_study_entity_function_statement"
        if section in SOURCE_DIRECT_SECTIONS and not attributed_background:
            return f"{section}_entity_function_statement"
    return None


def strip_repeated_source_title(text: str, title: str) -> str:
    """Remove one front-matter title copy before passage-level assessment."""

    compact_text = " ".join(str(text or "").split())
    compact_title = " ".join(str(title or "").split())
    if not compact_text or not compact_title:
        return compact_text
    title_start = compact_text.casefold().find(compact_title.casefold())
    if not 0 <= title_start <= 800:
        return compact_text
    title_end = title_start + len(compact_title)
    return " ".join(
        (compact_text[:title_start], compact_text[title_end:])
    ).strip()


def source_text_carries_entity_and_function(
    raw_text: str,
    normalized_text: str,
    entity_anchor_groups: list[tuple[str, ...]],
    function_aliases: tuple[str, ...],
) -> bool:
    return all(
        any(
            contains_entity_anchor_alias(raw_text, normalized_text, alias)
            for alias in group
        )
        for group in entity_anchor_groups
    ) and any(
        contains_normalized_term(normalized_text, alias)
        for alias in function_aliases
    )


def source_evidence_statements(text: str) -> list[str]:
    """Split prose conservatively while retaining OCR-derived line wrapping."""

    compact_text = " ".join(str(text or "").split())
    return [
        statement.strip()
        for statement in re.split(r"(?<=[.!?;])\s+", compact_text)
        if statement.strip()
    ]


def contains_entity_anchor_alias(
    raw_text: str,
    normalized_text: str,
    alias: str,
) -> bool:
    """Match symbols without confusing lowercase prose plus footnotes for genes."""
    clean_alias = str(alias or "").strip()
    if clean_alias == PARASITE_OMICS_ANCHOR:
        return contains_coupled_parasite_omics_language(raw_text)
    if re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9]*", clean_alias):
        matches = re.findall(
            rf"(?<![A-Za-z0-9]){re.escape(clean_alias)}(?![A-Za-z0-9])",
            raw_text,
            flags=re.IGNORECASE,
        )
        return any(not match.islower() for match in matches)
    return contains_normalized_term(normalized_text, clean_alias)


def contains_coupled_parasite_omics_language(text: str) -> bool:
    """Require omics language to describe the parasite, not only the host."""

    for raw_segment in str(text or "").splitlines():
        segment = normalize_text(raw_segment)
        if not segment:
            continue
        explicit_parasite_omics = any(
            contains_normalized_term(segment, term)
            for term in (
                "parasite transcriptome",
                "parasite transcriptomic",
                "parasite transcription",
                "parasite gene expression",
                "parasite rna seq",
                "parasite rna sequencing",
                "dual transcriptome",
                "host parasite transcriptome",
            )
        )
        if explicit_parasite_omics:
            return True




        host_omics = bool(
            re.search(
                r"\b(?:host|human|whole blood|peripheral blood)\b.{0,45}"
                r"\b(?:transcriptom\w*|transcription|gene expression|rna seq(?:uencing)?)\b",
                segment,
            )
        )
        if host_omics:
            continue
        parasite = r"(?:plasmodium(?: falciparum)?|p falciparum|malaria parasite)"
        omics = r"(?:transcriptom\w*|transcription|gene expression|rna seq(?:uencing)?)"
        if re.search(rf"\b{parasite}\b.{{0,80}}\b{omics}\b", segment) or re.search(
            rf"\b{omics}\b.{{0,80}}\b(?:of )?{parasite}\b",
            segment,
        ):
            return True
    return False


def query_mode_for_retrieval(query: RetrievalQuery, default_mode: str) -> str:
    if (query.intent or query.label) == "data_accessibility":
        return "methods"
    return default_mode


def promote_intent_passages(
    candidates: list[dict], *, data_accessibility_query_labels: set[str]
) -> list[dict]:
    """Put a matching availability passage in a paper's first retained slot.

    Paper-level RRF still decides which papers rank highest. This only changes
    which passage represents a paper when the user explicitly asks about data
    access or reanalysis and a repository/deposition passage was retrieved.
    """
    if not data_accessibility_query_labels:
        return candidates
    promoted = list(candidates)
    first_index: dict[str, int] = {}
    intent_candidates: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for index, candidate in enumerate(promoted):
        paper_id = str(candidate.get("paper_id") or "")
        if not paper_id:
            continue
        first_index.setdefault(paper_id, index)
        query_ranks = candidate.get("query_ranks") or {}
        matched_labels = data_accessibility_query_labels.intersection(query_ranks)
        if not matched_labels:
            continue
        if not is_data_accessibility_passage(str(candidate.get("text") or "")):
            continue
        intent_candidates[paper_id].append((index, candidate))

    for paper_id, matches in intent_candidates.items():
        target_index = first_index[paper_id]
        best_index, best_candidate = min(
            matches,
            key=lambda item: (
                min(
                    int((item[1].get("query_ranks") or {}).get(label, 10**9))
                    for label in data_accessibility_query_labels
                ),
                -float(item[1].get("score") or 0.0),
                str(item[1].get("chunk_id") or ""),
            ),
        )
        best_candidate["evidence_window_intent"] = "data_accessibility"
        if best_index != target_index:
            promoted[target_index], promoted[best_index] = (
                promoted[best_index],
                promoted[target_index],
            )
    return promoted


def is_data_accessibility_passage(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        marker in normalized
        for marker in (
            "data availability",
            "data access",
            "gene expression omnibus",
            "geo repository",
            "deposited in",
            "deposited to",
            "uploaded in",
            "uploaded to",
            "accession number",
            "accession code",
        )
    )


def rank_candidates_by_paper_rrf(candidates: list[dict], *, k: int = 60) -> list[dict]:
    """Carry multi-query support across different chunks from the same paper."""
    best_query_ranks: dict[str, dict[str, int]] = defaultdict(dict)
    best_chunk_score: dict[str, float] = defaultdict(float)
    best_metadata_score: dict[str, float] = defaultdict(float)
    for candidate in candidates:
        paper_id = str(candidate.get("paper_id") or "")
        best_chunk_score[paper_id] = max(best_chunk_score[paper_id], float(candidate.get("score") or 0.0))
        best_metadata_score[paper_id] = max(
            best_metadata_score[paper_id], float(candidate.get("metadata_score") or 0.0)
        )
        for label, raw_rank in (candidate.get("query_ranks") or {}).items():
            rank = int(raw_rank)
            current = best_query_ranks[paper_id].get(str(label))
            if current is None or rank < current:
                best_query_ranks[paper_id][str(label)] = rank

    for candidate in candidates:
        paper_id = str(candidate.get("paper_id") or "")
        ranks = best_query_ranks.get(paper_id, {})
        candidate["paper_rrf_score"] = round(sum(1.0 / (k + rank) for rank in ranks.values()), 6)
        candidate["paper_query_hits"] = len(ranks)



        candidate["paper_relevance_score"] = round(
            float(candidate["paper_rrf_score"])
            + min(best_chunk_score[paper_id], 150.0) / 10000.0
            + min(best_metadata_score[paper_id], 24.0) / 1000.0,
            6,
        )
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("paper_relevance_score") or 0.0),
            -float(item.get("paper_rrf_score") or 0.0),
            -float(item.get("rrf_score") or 0.0),
            -float(item.get("score") or 0.0),
            str(item.get("chunk_id") or ""),
        ),
    )


def retrieval_result_key(result: dict) -> str:
    chunk_id = str(result.get("chunk_id") or "").strip()
    if chunk_id:
        return chunk_id
    source_file = str(result.get("source_file") or "").strip()
    line_number = str(result.get("line_number") or "").strip()
    return f"{source_file}:{line_number}" if source_file and line_number else ""


def shortlist_answer_candidates(
    results: list[dict],
    *,
    max_papers: int,
    question: str = "",
    max_chars_per_chunk: int = 1800,
) -> list[dict]:
    """Keep diverse papers plus one focused support passage per named report.

    Retrieval still searches the broad candidate pool.  This stage prevents
    duplicate passages and low-ranked constraint-repair candidates from
    displacing the strongest papers in the expensive prompt-visible set. For
    entity-constrained questions, prefer the passage that retains the most
    required anchors after prompt-windowing; otherwise a high-scoring context
    passage can hide the direct gene--phenotype statement later in the same
    paper.
    """

    paper_order: list[str] = []
    best_by_paper: dict[str, tuple[tuple[int, ...], dict]] = {}
    rows_by_paper: defaultdict[str, list[dict]] = defaultdict(list)
    for result in results:
        paper_id = str(result.get("paper_id") or "")
        if not paper_id:
            continue
        rows_by_paper[paper_id].append(result)
        if paper_id not in best_by_paper:
            paper_order.append(paper_id)
        quality = prompt_visible_answer_quality(
            result,
            question=question,
            max_chars_per_chunk=max_chars_per_chunk,
        )
        current = best_by_paper.get(paper_id)
        current_is_named_anchor = bool(
            current and current[1].get("explicit_named_study_anchor")
        )
        result_is_named_anchor = bool(result.get("explicit_named_study_anchor"))
        if (
            current is None
            or (result_is_named_anchor and not current_is_named_anchor)
            or (not current_is_named_anchor and quality > current[0])
        ):
            best_by_paper[paper_id] = (quality, result)
    limit = max(1, max_papers)
    shortlisted: list[dict] = []
    for paper_id in paper_order:
        primary = best_by_paper[paper_id][1]
        shortlisted.append(primary)
        if len(shortlisted) >= limit:
            break

        if primary.get("explicit_named_study_anchor"):
            support_candidates = [
                row
                for row in rows_by_paper[paper_id]
                if str(row.get("chunk_id") or "")
                != str(primary.get("chunk_id") or "")
                and not row.get("explicit_named_study_anchor")
                and not set(row.get("quality_flags") or []).intersection(
                    {
                        "reference_like",
                        "table_like",
                        "supplement_like",
                        "low_text_quality",
                    }
                )
            ]
            if support_candidates:
                best_support = max(
                    support_candidates,
                    key=lambda row: named_study_support_quality(
                        row,
                        question=question,
                        max_chars_per_chunk=max_chars_per_chunk,
                    ),
                )
                if named_study_support_quality(
                    best_support,
                    question=question,
                    max_chars_per_chunk=max_chars_per_chunk,
                )[0] > 0:
                    shortlisted.append(
                        {
                            **best_support,
                            "explicit_named_study_support": True,
                            "named_study_surname": str(
                                primary.get("named_study_surname") or ""
                            ),
                            "named_study_year": str(
                                primary.get("named_study_year") or ""
                            ),
                        }
                    )
        if len(shortlisted) >= limit:
            break
    return shortlisted[:limit]


def named_study_support_quality(
    result: dict,
    *,
    question: str,
    max_chars_per_chunk: int,
) -> tuple[int, int, int, int, int, float]:
    """Rank a second, question-focused passage from an exact named report."""

    visible = freeze_prompt_visible_result(question, result, max_chars_per_chunk)
    text = str(visible.get("text") or "")
    normalized = normalize_text(text)
    terms = question_relevance_terms(question)
    term_hits = sum(
        contains_normalized_term(normalized, term)
        for term in terms
    )
    section = str(result.get("section") or "unknown").lower()
    own_result = int(
        any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in ANSWER_OWN_RESULT_PATTERNS
        )
    )
    problem_flags = len(
        set(result.get("quality_flags") or []).intersection(
            {"reference_like", "table_like", "supplement_like", "low_text_quality"}
        )
    )
    accession_requested = bool(
        NAMED_STUDY_ACCESSION_REQUEST_PATTERN.search(normalize_text(question))
    )
    accession_present = int(
        accession_requested
        and bool(NAMED_STUDY_ACCESSION_PATTERN.search(text))
    )
    return (
        term_hits + 100 * accession_present,
        term_hits,
        int(section in {"abstract", "results", "methods"}),
        own_result,
        -problem_flags,
        float(result.get("score") or 0.0),
    )


def reserve_named_study_prompt_context(
    question: str,
    candidates: list[dict],
    *,
    token_budget: int,
) -> tuple[list[dict], list[dict], int]:
    """Reserve bounded same-report detail without adding certifying candidates.

    A resolved named-report anchor establishes report identity and supplies its
    front summary. A second passage can add requested methods, results, or an
    explicitly requested background distinction. It is useful answer context,
    but it is not another report or origin. Remove such passages from TRACE,
    reserve their prompt cost inside the caller's total token budget, and label
    them so the evidence pack excludes them from the certificate.
    """

    if token_budget <= 0:
        return list(candidates), [], 0
    anchors_by_paper = {
        str(candidate.get("paper_id") or ""): candidate
        for candidate in candidates
        if candidate.get("explicit_named_study_anchor")
        and str(candidate.get("paper_id") or "")
    }
    selector_candidates = [
        candidate
        for candidate in candidates
        if not candidate.get("explicit_named_study_support")
    ]
    if not anchors_by_paper:
        return selector_candidates, [], 0

    terms = question_relevance_terms(question)
    ranked_support: list[tuple[tuple[int, float, int, str], dict, int]] = []
    for candidate in candidates:
        if not candidate.get("explicit_named_study_support"):
            continue
        paper_id = str(candidate.get("paper_id") or "")
        anchor = anchors_by_paper.get(paper_id)
        if anchor is None:
            continue
        incremental_hits = named_study_incremental_question_terms(
            question,
            anchor_text=str(anchor.get("text") or ""),
            support_text=str(candidate.get("text") or ""),
            question_terms=terms,
        )
        if not incremental_hits:
            continue
        cost = estimate_tokens(str(candidate.get("text") or ""))
        context = {
            **candidate,
            "prompt_context_only": True,
            "evidence_selection_reason": "named_study_same_report_context",
            "named_study_incremental_question_terms": incremental_hits,
        }
        ranked_support.append(
            (
                (
                    -len(incremental_hits),
                    -float(candidate.get("score") or 0.0),
                    cost,
                    str(candidate.get("chunk_id") or ""),
                ),
                context,
                cost,
            )
        )

    anchor_cost = sum(
        estimate_tokens(str(anchor.get("text") or ""))
        for anchor in anchors_by_paper.values()
    )
    context_budget = min(
        token_budget // 2,
        max(0, token_budget - anchor_cost),
    )
    selected_support: list[dict] = []
    reserved = 0
    selected_papers: set[str] = set()
    for _, candidate, cost in sorted(ranked_support, key=lambda item: item[0]):
        paper_id = str(candidate.get("paper_id") or "")
        if paper_id in selected_papers or reserved + cost > context_budget:
            continue
        selected_support.append(candidate)
        selected_papers.add(paper_id)
        reserved += cost
    return selector_candidates, selected_support, reserved


def named_study_incremental_question_terms(
    question: str,
    *,
    anchor_text: str,
    support_text: str,
    question_terms: list[str] | None = None,
) -> list[str]:
    """Return literal or identifier-intent details absent from a front anchor."""

    terms = question_terms or question_relevance_terms(question)
    normalized_anchor = normalize_text(anchor_text)
    normalized_support = normalize_text(support_text)
    anchor_hits = {
        term
        for term in terms
        if contains_normalized_term(normalized_anchor, term)
    }
    incremental = {
        term
        for term in terms
        if term not in anchor_hits
        and contains_normalized_term(normalized_support, term)
    }
    if (
        NAMED_STUDY_ACCESSION_REQUEST_PATTERN.search(normalize_text(question))
        and not NAMED_STUDY_ACCESSION_PATTERN.search(anchor_text)
        and NAMED_STUDY_ACCESSION_PATTERN.search(support_text)
    ):
        incremental.add("repository_accession")
    return sorted(incremental)


def merge_selected_named_study_context(
    selected_results: list[dict],
    context_support: list[dict],
) -> list[dict]:
    """Place reserved context after its selected anchor, never on its own."""

    support_by_paper = {
        str(candidate.get("paper_id") or ""): candidate
        for candidate in context_support
        if str(candidate.get("paper_id") or "")
    }
    merged: list[dict] = []
    for result in selected_results:
        merged.append(result)
        if not result.get("explicit_named_study_anchor"):
            continue
        support = support_by_paper.get(str(result.get("paper_id") or ""))
        if support is not None:
            merged.append(support)
    return merged


ANSWER_BACKGROUND_SECTIONS = {"background", "introduction", "discussion", "conclusion"}
ANSWER_OWN_RESULT_PATTERNS = (
    r"\bwe (?:found|observed|show|showed|report|reported|demonstrate|demonstrated|"
    r"identified|measured|quantified|analysed|analyzed|assessed|evaluated)\b",
    r"\bour (?:study|analysis|data|results|findings)\b",
    r"\b(?:in this study|here,? we|this study (?:found|shows|showed|reports?))\b",
)
ANSWER_EXPLICIT_CURRENT_RESULT_PATTERNS = (
    r"\b(?:here,?\s+)?we (?:further\s+)?(?:found|observed|show|showed|report|reported|"
    r"demonstrate|demonstrated|identified|measured|quantified|analysed|analyzed|"
    r"assessed|evaluated)\b",
    r"\bour (?:data|results|findings)\b",
    r"\b(?:results|data|findings) (?:presented|reported) here\b",
    r"\b(?:our|the) data from (?:this|the present) study\b",
)
ANSWER_ANAPHORIC_RESULT_PATTERNS = (
    r"\b(?:the|these) (?:data|results|findings) (?:demonstrate|demonstrated|indicate|"
    r"indicated|show|showed|suggest|suggested)\b",
)
ANSWER_CURRENT_RESULT_PATTERNS = (
    *ANSWER_EXPLICIT_CURRENT_RESULT_PATTERNS,
    *ANSWER_ANAPHORIC_RESULT_PATTERNS,
)
ANSWER_RELATION_PATTERN = re.compile(
    r"\b(?:associat\w*|correlat\w*|compar\w*|contrast\w*|differ\w*|distinguish\w*|"
    r"higher|lower|increase\w*|decrease\w*|reduce\w*|elevat\w*|enrich\w*|"
    r"fewer|greater|more|less|significant\w*)\b",
    flags=re.IGNORECASE,
)
ANSWER_DIRECTION_PATTERN = re.compile(
    r"\b(?:higher|lower|increase\w*|decrease\w*|reduce\w*|elevat\w*|enrich\w*|"
    r"fewer|greater|more|less|abundant|focused|differ\w*|contrast\w*)\b",
    flags=re.IGNORECASE,
)
ANSWER_PRIOR_WORK_PATTERN = re.compile(
    r"\bet\s+al\.?\b|\b(?:previous|prior|earlier|recently)\b|\breported by\b|"
    r"\b[A-Za-z][A-Za-z'’-]+\s*\((?:19|20)\d{2}\)|"
    r"\([A-Za-z][A-Za-z'’-]+(?:\s+et\s+al\.?)?,?\s+(?:19|20)\d{2}\)|"
    r"\[[0-9,;\-\s]+\]",
    flags=re.IGNORECASE,
)
ANSWER_BACKGROUND_ATTRIBUTION_PATTERNS = (
    r"\b(?:previous|prior|earlier|other|several) (?:study|studies|work|reports?)\b",
    r"\b(?:studies|reports?) (?:have |has )?(?:shown|reported|found|described)\b",
    r"\b(?:has|have|had) been (?:shown|reported|found|described|associated)\b",
    r"\baccording to\b",
    r"\breported by\b",
    r"\[[0-9,;\-\s]+\]",
    r"\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.?)?[,]?\s+(?:19|20)\d{2}\)",
)


def remove_attribution_only_answer_candidates(
    candidates: list[dict],
    *,
    question: str,
    max_chars_per_chunk: int,
) -> list[dict]:
    """Exclude prompt passages whose only query match is attributed prior work.

    This operates on the exact prompt-visible window and never blacklists a
    paper. A paper with a direct Results/Abstract passage remains eligible even
    when another retrieved Discussion passage only cites the relevant claim.
    """

    terms = question_relevance_terms(question)
    if not terms:
        return candidates
    return [
        candidate
        for candidate in candidates
        if not attribution_only_answer_candidate(
            candidate,
            question=question,
            question_terms=terms,
            max_chars_per_chunk=max_chars_per_chunk,
        )
    ]


def attribution_only_answer_candidate(
    candidate: dict,
    *,
    question: str,
    question_terms: list[str],
    max_chars_per_chunk: int,
) -> bool:
    """Return whether all question-matching sentences cite background work."""

    section = str(candidate.get("section") or "unknown").strip().lower()
    if section not in ANSWER_BACKGROUND_SECTIONS:
        return False
    visible = freeze_prompt_visible_result(question, candidate, max_chars_per_chunk)
    relevant_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(visible.get("text") or "")):
        normalized_sentence = normalize_text(sentence)
        if any(
            contains_normalized_term(normalized_sentence, term)
            for term in question_terms
        ):
            relevant_sentences.append(sentence)
    if not relevant_sentences:
        return False
    if any(
        re.search(pattern, sentence, flags=re.IGNORECASE)
        for sentence in relevant_sentences
        for pattern in ANSWER_OWN_RESULT_PATTERNS
    ):
        return False
    return all(
        any(
            re.search(pattern, sentence, flags=re.IGNORECASE)
            for pattern in ANSWER_BACKGROUND_ATTRIBUTION_PATTERNS
        )
        for sentence in relevant_sentences
    )


def prompt_visible_anchor_quality(
    result: dict,
    *,
    question: str,
    max_chars_per_chunk: int,
) -> tuple[int, int, int, int]:
    """Score required anchors that remain visible after evidence windowing."""

    groups = [
        tuple(str(alias) for alias in group if str(alias).strip())
        for group in (result.get("required_entity_anchors") or [])
        if isinstance(group, (list, tuple))
    ]
    if not question or not groups:
        return (0, 0, 0, 0)
    frozen = freeze_prompt_visible_result(question, result, max_chars_per_chunk)
    raw_visible = str(frozen.get("text") or "")



    prompt_visible_searchable = " ".join(
        part for part in (str(result.get("title") or ""), raw_visible) if part
    )
    normalized_visible = normalize_text(prompt_visible_searchable)
    covered = sum(
        any(
            contains_entity_anchor_alias(
                prompt_visible_searchable,
                normalized_visible,
                alias,
            )
            for alias in group
        )
        for group in groups
    )
    sentence_coverages: list[tuple[bool, ...]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", raw_visible):
        normalized_sentence = normalize_text(sentence)
        sentence_coverages.append(
            tuple(
                any(
                    contains_entity_anchor_alias(
                        sentence,
                        normalized_sentence,
                        alias,
                    )
                    for alias in group
                )
                for group in groups
            )
        )
    max_sentence_coverage = max(
        (sum(sentence) for sentence in sentence_coverages),
        default=0,
    )




    entity_relation = int(
        any(sentence[0] and any(sentence[1:]) for sentence in sentence_coverages)
        if len(groups) > 1
        else False
    )



    return (
        int(covered == len(groups)),
        entity_relation,
        max_sentence_coverage,
        covered,
    )


def prompt_visible_answer_quality(
    result: dict,
    *,
    question: str,
    max_chars_per_chunk: int,
) -> tuple[int, ...]:
    """Prefer a paper's prompt-visible result passage over topical context.

    Paper-level retrieval decides which reports enter the candidate set. This
    tie-breaker only chooses the single passage that represents each report.
    Required entity co-location remains the strongest criterion; when those
    anchors do not apply, current-study result language and question-linked
    comparison sentences prevent front matter or background prose from hiding
    a lower-ranked answer-bearing passage from the same paper.
    """

    anchor_quality = (
        (0, 0, 0, 0)
        if result.get("anchor_only_evidence_window") is False
        else prompt_visible_anchor_quality(
            result,
            question=question,
            max_chars_per_chunk=max_chars_per_chunk,
        )
    )
    if not question:
        return (*anchor_quality, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    frozen = freeze_prompt_visible_result(question, result, max_chars_per_chunk)
    raw_visible = " ".join(str(frozen.get("text") or "").split())
    question_terms = question_relevance_terms(question)
    if not raw_visible or not question_terms:
        return (*anchor_quality, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<!et al\.)(?<=[.!?;])\s+", raw_visible)
        if sentence.strip()
    ]

    def term_hits(sentence: str) -> int:
        normalized_sentence = normalize_text(sentence)
        return sum(
            contains_normalized_term(normalized_sentence, term)
            for term in question_terms
        )

    directional_result_hits = 0
    for index, sentence in enumerate(sentences):
        if not ANSWER_DIRECTION_PATTERN.search(sentence):
            continue
        explicit_current_result = any(
            re.search(pattern, sentence, flags=re.IGNORECASE)
            for pattern in ANSWER_EXPLICIT_CURRENT_RESULT_PATTERNS
        )
        attribution_context = " ".join(
            part
            for part in (
                sentences[index - 1] if index else "",
                sentence,
            )
            if part
        )
        if (
            not explicit_current_result
            and ANSWER_PRIOR_WORK_PATTERN.search(attribution_context)
        ):
            continue
        directional_result_hits = max(
            directional_result_hits,
            term_hits(sentence),
        )
    current_result_hits = 0
    for index, sentence in enumerate(sentences):
        if not any(
            re.search(pattern, sentence, flags=re.IGNORECASE)
            for pattern in ANSWER_CURRENT_RESULT_PATTERNS
        ):
            continue
        explicit_current_result = any(
            re.search(pattern, sentence, flags=re.IGNORECASE)
            for pattern in ANSWER_EXPLICIT_CURRENT_RESULT_PATTERNS
        )
        attribution_context = " ".join(
            part
            for part in (
                sentences[index - 1] if index else "",
                sentence,
            )
            if part
        )
        if (
            not explicit_current_result
            and ANSWER_PRIOR_WORK_PATTERN.search(attribution_context)
        ):
            continue
        current_result_hits = max(current_result_hits, term_hits(sentence))
    relation_hits = 0
    for index, sentence in enumerate(sentences):
        if not ANSWER_RELATION_PATTERN.search(sentence):
            continue
        explicit_current_result = any(
            re.search(pattern, sentence, flags=re.IGNORECASE)
            for pattern in ANSWER_EXPLICIT_CURRENT_RESULT_PATTERNS
        )
        attribution_context = " ".join(
            part
            for part in (
                sentences[index - 1] if index else "",
                sentence,
            )
            if part
        )
        if (
            not explicit_current_result
            and ANSWER_PRIOR_WORK_PATTERN.search(attribution_context)
        ):
            continue
        relation_hits = max(relation_hits, term_hits(sentence))
    normalized_visible = normalize_text(raw_visible)
    total_hits = sum(
        contains_normalized_term(normalized_visible, term)
        for term in question_terms
    )
    section = str(result.get("section") or "unknown").strip().lower()
    problem_flags = len(
        set(result.get("quality_flags") or []).intersection(
            {"reference_like", "table_like", "supplement_like", "low_text_quality"}
        )
    )
    return (
        *anchor_quality,
        int(directional_result_hits > 0),
        directional_result_hits,
        int(current_result_hits > 0),
        current_result_hits,
        int(relation_hits > 0),
        relation_hits,
        total_hits,
        int(section in {"abstract", "findings", "results", "summary"}),
        -problem_flags,
    )


def merge_explicit_doi_anchors(
    question: str,
    results: list[dict],
    *,
    chunks_dir: Path,
    papers_tsv: Path,
) -> list[dict]:
    """Keep every explicitly named local DOI in the prompt-visible candidate set."""

    requested = extract_explicit_dois(question)
    if not requested:
        return results
    metadata = load_paper_metadata(papers_tsv)
    anchors = []
    for paper_id, paper in metadata.items():
        doi = normalize_doi_text(str(paper.get("doi") or ""))
        if doi not in requested:
            continue
        chunk_path = chunks_dir / f"{paper_id}.jsonl"
        if not chunk_path.is_file():
            continue
        chunks = list(read_jsonl(chunk_path))
        chunk = min(chunks, key=identifier_anchor_chunk_priority, default=None)
        if chunk is None:
            continue
        anchors.append(
            {
                **paper,
                **chunk,
                "paper_id": paper_id,
                "doi": paper.get("doi") or doi,
                "score": 10_000.0,
                "query_id": "EXPLICIT_DOI",
                "retrieval_query_labels": "EXPLICIT_DOI",
                "retrieval_query_hits": 1,
                "contributing_queries": ["EXPLICIT_DOI"],
                "explicit_identifier_anchor": True,
            }
        )
    anchored_ids = {str(row.get("paper_id") or "") for row in anchors}
    return anchors + [
        row for row in results if str(row.get("paper_id") or "") not in anchored_ids
    ]


NAMED_STUDY_RESOLUTION_STOPWORDS = {
    "according",
    "also",
    "compare",
    "each",
    "from",
    "main",
    "paper",
    "report",
    "separate",
    "study",
    "their",
    "which",
    "with",
    "without",
}


def merge_explicit_named_study_anchors(
    question: str,
    results: list[dict],
    *,
    chunks_dir: Path,
    papers_tsv: Path,
) -> list[dict]:
    """Prepend the front summary of every unambiguously named local report.

    Broad multi-query RRF can otherwise bury a requested comparison paper, or
    retain only a later background passage from it.  This anchor is narrower
    than topical retrieval: it requires an explicit ``Surname et al.`` mention
    and an unambiguous metadata resolution.  Other passages from the same
    report remain available to downstream selection.
    """

    references = explicit_named_study_references(question)
    if not references:
        return results
    metadata = load_paper_metadata(papers_tsv)
    existing_by_chunk = {
        str(row.get("chunk_id") or ""): row
        for row in results
        if str(row.get("chunk_id") or "")
    }
    anchors: list[dict] = []
    injected_support: list[dict] = []
    anchored_chunks: set[str] = set()
    anchored_papers: set[str] = set()
    for reference in references:
        resolved = resolve_named_study_reference(reference, metadata)
        if resolved is None:
            continue
        paper_id, paper = resolved
        if paper_id in anchored_papers:
            continue
        chunk_path = chunks_dir / f"{paper_id}.jsonl"
        if not chunk_path.is_file():
            continue
        chunks = list(read_jsonl(chunk_path))
        chunk = min(
            (
                row
                for row in chunks
                if str(row.get("section") or "").lower() != "references"
            ),
            key=lambda row: named_study_anchor_chunk_priority(row, paper),
            default=None,
        )
        if chunk is None:
            continue
        chunk_id = str(chunk.get("chunk_id") or "")
        existing = existing_by_chunk.get(chunk_id, {})
        candidate = {
            **paper,
            **chunk,
            **existing,
            "paper_id": paper_id,
            "score": 10_000.0,
            "raw_score": 10_000.0,
            "metadata_score": 10_000.0,
            "query_id": "EXPLICIT_NAMED_STUDY",
            "retrieval_query_labels": "EXPLICIT_NAMED_STUDY",
            "retrieval_query_hits": 1,
            "contributing_queries": ["EXPLICIT_NAMED_STUDY"],
            "explicit_named_study_anchor": True,
            "named_study_surname": reference.surname,
            "named_study_year": reference.year or "",
        }



        candidate["text"] = str(chunk.get("text") or "")
        summary_excerpt = named_study_summary_excerpt(str(candidate.get("text") or ""))
        if summary_excerpt:
            candidate["text"] = summary_excerpt
            candidate["named_study_source_text_scope"] = "front_summary_excerpt"
            visible_quality = classify_chunk_quality(summary_excerpt)
            candidate["quality_flags"] = visible_quality["flags"]
            candidate["quality_penalty"] = visible_quality["quality_penalty"]
        if not str(candidate.get("section") or "").strip() and named_study_front_summary(
            chunk,
            paper,
        ):
            candidate["section"] = "abstract"
            candidate["section_confidence"] = 0.95
        anchors.append(candidate)
        support = missing_named_study_fulltext_support(
            question,
            paper_id=paper_id,
            paper=paper,
            chunks=chunks,
            anchor=candidate,
            existing_results=results,
        )
        if support is not None:
            injected_support.append(support)
        anchored_papers.add(paper_id)
        if chunk_id:
            anchored_chunks.add(chunk_id)
    injected_support_ids = {
        str(support.get("chunk_id") or "") for support in injected_support
    }
    return anchors + injected_support + [
        row
        for row in results
        if str(row.get("chunk_id") or "") not in anchored_chunks
        and str(row.get("chunk_id") or "") not in injected_support_ids
    ]


def missing_named_study_fulltext_support(
    question: str,
    *,
    paper_id: str,
    paper: dict,
    chunks: list[dict],
    anchor: dict,
    existing_results: list[dict],
) -> dict | None:
    """Recover one clean missing detail passage from an exact named report."""

    terms = question_relevance_terms(question)
    if not terms:
        return None
    anchor_chunk_id = str(anchor.get("chunk_id") or "")
    existing_by_chunk = {
        str(result.get("chunk_id") or ""): result
        for result in existing_results
        if str(result.get("chunk_id") or "")
    }

    def incremental_hits(row: dict) -> list[str]:
        return named_study_incremental_question_terms(
            question,
            anchor_text=str(anchor.get("text") or ""),
            support_text=str(row.get("text") or ""),
            question_terms=terms,
        )

    candidates: list[dict] = []
    blocked_flags = {
        "reference_like",
        "table_like",
        "supplement_like",
        "low_text_quality",
    }
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if (
            not chunk_id
            or chunk_id == anchor_chunk_id
            or str(chunk.get("section") or "").lower() == "references"
        ):
            continue
        quality = classify_chunk_quality(str(chunk.get("text") or ""))
        if set(quality["flags"]).intersection(blocked_flags):
            continue
        added_terms = incremental_hits(chunk)
        if not added_terms:
            continue
        existing = existing_by_chunk.get(chunk_id, {})
        candidates.append(
            {
                **paper,
                **chunk,
                **existing,
                "paper_id": paper_id,
                "score": float(existing.get("score") or 0.0),
                "quality_flags": quality["flags"],
                "quality_penalty": quality["quality_penalty"],
                "query_id": "EXPLICIT_NAMED_STUDY_SUPPORT",
                "retrieval_query_labels": "EXPLICIT_NAMED_STUDY_SUPPORT",
                "retrieval_query_hits": 1,
                "contributing_queries": ["EXPLICIT_NAMED_STUDY_SUPPORT"],
                "explicit_named_study_support": True,
                "named_study_surname": str(
                    anchor.get("named_study_surname") or ""
                ),
                "named_study_year": str(anchor.get("named_study_year") or ""),
                "named_study_source_text_scope": "fulltext_question_support",
                "named_study_incremental_question_terms": added_terms,
            }
        )
        candidates[-1]["text"] = str(chunk.get("text") or "")
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: named_study_support_quality(
            row,
            question=question,
            max_chars_per_chunk=NAMED_STUDY_FULLTEXT_SUPPORT_MAX_CHARS,
        ),
    )


def resolve_named_study_reference(
    reference: NamedStudyReference,
    metadata: dict[str, dict],
) -> tuple[str, dict] | None:
    """Resolve one named report only when metadata gives one clear winner."""

    surname = normalize_text(reference.surname)
    candidates: list[tuple[str, dict]] = []
    for paper_id, paper in metadata.items():
        author_tokens = set(normalize_text(str(paper.get("author") or "")).split())
        if surname not in author_tokens:
            continue
        if reference.year and str(paper.get("year") or "").strip() != reference.year:
            continue
        candidates.append((paper_id, paper))
    if len(candidates) == 1:
        paper_id, paper = candidates[0]
        quoted_phrases = named_study_quoted_context_phrases(reference.context)
        title = normalize_text(str(paper.get("title") or ""))
        if quoted_phrases and not any(
            phrase in title for phrase in quoted_phrases
        ):
            return None
        return paper_id, paper
    if not candidates:
        return None

    scored = sorted(
        (
            (named_study_metadata_resolution_score(reference, paper), paper_id, paper)
            for paper_id, paper in candidates
        ),
        key=lambda row: (-row[0], row[1]),
    )
    best_score, best_id, best_paper = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 8.0 or best_score - runner_up < 4.0:
        return None
    return best_id, best_paper


def named_study_metadata_resolution_score(
    reference: NamedStudyReference,
    paper: dict,
) -> float:
    context = normalize_text(reference.context).replace("pfpan", "pangenome")
    title = normalize_text(str(paper.get("title") or ""))
    journal = normalize_text(str(paper.get("journal") or ""))
    paper_type = normalize_text(str(paper.get("paper_type") or ""))
    searchable = " ".join((title, journal, paper_type))
    score = 0.0
    for normalized_phrase in named_study_quoted_context_phrases(
        reference.context
    ):
        if normalized_phrase in title:
            score += 60.0
    context_tokens = {
        token
        for token in context.split()
        if len(token) >= 4
        and token not in NAMED_STUDY_RESOLUTION_STOPWORDS
        and token not in {normalize_text(reference.surname), "preprint"}
        and not re.fullmatch(r"(?:19|20)\d{2}", token)
    }
    score += sum(2.0 if len(token) >= 8 else 1.0 for token in context_tokens if token in searchable)
    if "preprint" in context:
        if any(marker in searchable for marker in ("biorxiv", "medrxiv", "preprint")):
            score += 30.0
        else:
            score -= 5.0
    return score


def named_study_quoted_context_phrases(context: str) -> list[str]:
    """Return quoted title hints placed directly after a named citation."""

    prefix = re.search(
        r"\bet\s+al\.?\s*(?:,\s*)?"
        r"(?:\([^)]{0,80}\)|(?:19|20)\d{2})?\s*"
        r"[,;:\-–—]?\s*(?:titled\s+)?",
        context or "",
        flags=re.IGNORECASE,
    )
    if not prefix:
        return []
    tail = str(context or "")[prefix.end() :]
    phrases = []
    for pattern in (
        r'^["“„]([^"”“]{8,160})["”]',
        r"^[‘']([^'’]{8,160})[’'](?!\w)",
    ):
        match = re.match(pattern, tail)
        if match:
            phrases.append(match.group(1))
    return [
        normalized
        for phrase in phrases
        if (normalized := normalize_text(phrase))
    ]


def named_study_anchor_chunk_priority(chunk: dict, paper: dict) -> tuple[int, int, int]:
    """Prefer the title/abstract front chunk for a resolved named report."""

    front_summary = named_study_front_summary(chunk, paper)
    section = str(chunk.get("section") or "").lower()
    try:
        chunk_index = int(chunk.get("chunk_index") or 10**9)
    except (TypeError, ValueError):
        chunk_index = 10**9
    return (
        0 if front_summary else 1,
        0 if section == "abstract" else 1,
        chunk_index,
    )


def named_study_front_summary(chunk: dict, paper: dict) -> bool:
    text = normalize_text(str(chunk.get("text") or ""))
    title = normalize_text(str(paper.get("title") or ""))
    if not text or not title:
        return False
    return title in text and (
        "abstract" in text
        or any(
            marker in text
            for marker in ("here we", "we identified", "we found", "we report", "results")
        )
    )


def named_study_summary_excerpt(text: str, *, max_chars: int = 3_500) -> str:
    """Return a verbatim front-summary span for a resolved named report."""

    raw = str(text or "")
    if not raw:
        return ""
    abstract = re.search(
        r"\bA\s*B\s*S\s*T\s*R\s*A\s*C\s*T\b\s*:?",
        raw,
        flags=re.IGNORECASE,
    )
    if abstract:
        return raw[abstract.end() : abstract.end() + max_chars].strip()
    own_summary = re.search(r"\bHere\s*,?\s+we\b", raw, flags=re.IGNORECASE)
    if own_summary:
        excerpt = raw[own_summary.start() : own_summary.start() + max_chars]
        conclusion = re.search(
            r"\b(?:This study|These findings|Our findings|Together,? these findings)\b"
            r".*?[.!?](?=\s+[A-Z]|\s*$)",
            excerpt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if conclusion:
            excerpt = excerpt[: conclusion.end()]
        return excerpt.strip()
    return raw[:max_chars].strip()


def extract_explicit_dois(text: str) -> set[str]:
    matches = re.findall(r"10\.\d{4,9}/[^\s<>\]\[{}]+", text or "", flags=re.IGNORECASE)
    return {normalize_doi_text(match) for match in matches if normalize_doi_text(match)}


def paper_search_record_matches_requested_doi(
    paper: dict,
    requested_dois: set[str],
) -> bool:
    """Return whether a displayed report or version family was requested exactly."""

    if not requested_dois:
        return False
    reports = [paper, *(paper.get("publication_family_reports") or [])]
    return any(
        normalize_doi_text(str(report.get("doi") or "")) in requested_dois
        for report in reports
        if isinstance(report, dict) and str(report.get("doi") or "").strip()
    )


def normalize_doi_text(value: str) -> str:
    clean = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.IGNORECASE)
    return clean.rstrip(".,;:!?)]}\"").lower()


def identifier_anchor_chunk_priority(chunk: dict) -> tuple[int, int]:
    section = str(chunk.get("section") or "").lower()
    priorities = {"abstract": 0, "results": 1, "methods": 2, "discussion": 3}
    return priorities.get(section, 5 if section != "references" else 9), int(
        chunk.get("chunk_index") or 0
    )


def diversify_merged_results(candidates: list[dict], limit: int, max_per_paper: int) -> list[dict]:
    kept: list[dict] = []
    per_paper_counts: defaultdict[str, int] = defaultdict(int)
    seen_chunks = set()
    for candidate in candidates:
        chunk_key = retrieval_result_key(candidate)
        if chunk_key in seen_chunks:
            continue
        paper_id = str(candidate.get("paper_id") or "")
        if max_per_paper > 0 and per_paper_counts[paper_id] >= max_per_paper:
            continue
        kept.append(candidate)
        seen_chunks.add(chunk_key)
        per_paper_counts[paper_id] += 1
        if len(kept) >= limit:
            break
    return kept


def generate_with_qc_repair(
    question: str,
    task: str,
    backend: str,
    initial_prompt: str,
    evidence_pack: dict,
    output_dir: Path,
    max_repair_attempts: int,
    use_template_fallback: bool,
    profile_instruction: str = "",
    answer_model: str | None = None,
    answer_temperature: float = 0.0,
    answer_timeout: int = 120,
    evidence_is_sufficient: bool = True,
    force_answer: bool = False,
) -> tuple[object, dict, list[dict], dict]:
    if backend in {"ollama", "qwen", "deepseek"} and task == "answer":
        marker_state_answer = build_marker_positive_state_answer(
            question, list(evidence_pack.get("chunks") or [])
        )
        if marker_state_answer:
            marker_state_result = normalize_llm_result(
                LLMResult(
                    backend="bounded_evidence_contract",
                    answer=marker_state_answer,
                    error=None,
                ),
                task,
                evidence_pack=evidence_pack,
            )
            marker_state_qc = assess_answer_quality(
                marker_state_result.answer, evidence_pack, task=task
            )
            if marker_state_qc.get("passed"):
                attempts = [
                    write_answer_attempt(
                        output_dir=output_dir,
                        attempt_index=0,
                        kind="bounded_marker_state_direct",
                        llm_result=marker_state_result,
                        qc_report=marker_state_qc,
                    )
                ]
                return (
                    marker_state_result,
                    marker_state_qc,
                    attempts,
                    {
                        "backend_requested": backend,
                        "backend_used": "bounded_evidence_contract",
                        "model": answer_model,
                        "call_performed": False,
                        "blocked": False,
                        "block_reason": None,
                        "bounded_contract_direct_used": True,
                    },
                )
        repository_answer = build_data_accessibility_answer(
            evidence_pack, question=question
        )
        if repository_answer:
            repository_result = normalize_llm_result(
                LLMResult(
                    backend="repository_contract",
                    answer=repository_answer,
                    error=None,
                ),
                task,
                evidence_pack=evidence_pack,
            )
            repository_qc = assess_answer_quality(
                repository_result.answer, evidence_pack, task=task
            )
            if repository_qc.get("passed"):
                attempts = [
                    write_answer_attempt(
                        output_dir=output_dir,
                        attempt_index=0,
                        kind="repository_contract_direct",
                        llm_result=repository_result,
                        qc_report=repository_qc,
                    )
                ]
                return (
                    repository_result,
                    repository_qc,
                    attempts,
                    {
                        "backend_requested": backend,
                        "backend_used": "repository_contract",
                        "model": answer_model,
                        "call_performed": False,
                        "blocked": False,
                        "block_reason": None,
                        "repository_contract_direct_used": True,
                    },
                )
    llm_result, answer_backend_meta = generate_initial_answer(
        question=question,
        task=task,
        backend=backend,
        prompt=initial_prompt,
        evidence_pack=evidence_pack,
        answer_model=answer_model,
        answer_temperature=answer_temperature,
        answer_timeout=answer_timeout,
        evidence_is_sufficient=evidence_is_sufficient,
        force_answer=force_answer,
    )
    llm_result = normalize_llm_result(llm_result, task, evidence_pack=evidence_pack)
    answer_qc = assess_answer_quality(llm_result.answer, evidence_pack, task=task)
    attempts = [
        write_answer_attempt(
            output_dir=output_dir,
            attempt_index=0,
            kind="initial",
            llm_result=llm_result,
            qc_report=answer_qc,
        )
    ]
    if backend in {"none", "template"}:
        return llm_result, answer_qc, attempts, answer_backend_meta

    for repair_index in range(1, max_repair_attempts + 1):
        if answer_qc.get("passed") or not llm_result.answer:
            break
        if "repository_identifier_omitted" in answer_qc.get("warnings", []):


            break
        previous_result = llm_result
        previous_qc = answer_qc
        repair_prompt = build_answer_repair_prompt(
            question=question,
            evidence_pack=evidence_pack,
            task=task,
            previous_answer=llm_result.answer,
            qc_report=answer_qc,
            profile_instruction=profile_instruction,
        )
        repair_prompt_path = output_dir / f"answer_repair_prompt_{repair_index}.md"
        repair_prompt_path.write_text(repair_prompt, encoding="utf-8")
        if backend in {"ollama", "qwen", "deepseek"}:
            backend_result = generate_answer_with_backend(
                repair_prompt,
                backend=backend,
                model=answer_model,
                temperature=answer_temperature,
                timeout=answer_timeout,
                evidence_is_sufficient=evidence_is_sufficient,
                force_answer=force_answer,
            )
            repaired_result = backend_result.llm_result
            answer_backend_meta["call_performed"] = answer_backend_meta.get("call_performed") or backend_result.call_performed
            answer_backend_meta["backend_used"] = backend_result.backend_used
            answer_backend_meta["model"] = backend_result.model
        else:
            repaired_result = generate_answer(repair_prompt, backend=backend)
        repaired_result = normalize_llm_result(repaired_result, task, evidence_pack=evidence_pack)
        repaired_qc = assess_answer_quality(repaired_result.answer, evidence_pack, task=task)
        attempts.append(
            write_answer_attempt(
                output_dir=output_dir,
                attempt_index=repair_index,
                kind="repair",
                llm_result=repaired_result,
                qc_report=repaired_qc,
                prompt_path=repair_prompt_path,
            )
        )
        if not repaired_result.answer:
            answer_backend_meta.setdefault("repair_errors", []).append(
                {
                    "attempt": repair_index,
                    "error": repaired_result.error or "empty_repair_answer",
                    "retained_attempt": repair_index - 1,
                }
            )
            llm_result = previous_result
            answer_qc = previous_qc
            break
        llm_result = repaired_result
        answer_qc = repaired_qc
    fallback_allowed = use_template_fallback and (evidence_is_sufficient or force_answer)
    if "repository_identifier_omitted" in answer_qc.get("warnings", []):
        deterministic_answer = build_data_accessibility_answer(
            evidence_pack, question=question
        )
        if deterministic_answer:
            answer_backend_meta["backend_used"] = "repository_contract"
            answer_backend_meta["repository_contract_fallback_used"] = True
            llm_result = normalize_llm_result(
                LLMResult(
                    backend="repository_contract",
                    answer=deterministic_answer,
                    error="repository_contract_fallback_after_qc_failure",
                ),
                task,
                evidence_pack=evidence_pack,
            )
            answer_qc = assess_answer_quality(llm_result.answer, evidence_pack, task=task)
            attempts.append(
                write_answer_attempt(
                    output_dir=output_dir,
                    attempt_index=len(attempts),
                    kind="repository_contract_fallback",
                    llm_result=llm_result,
                    qc_report=answer_qc,
                )
            )
    if fallback_allowed and backend not in {"none", "template"} and not answer_qc.get("passed"):
        llm_result = normalize_llm_result(
            LLMResult(
                backend="template",
                answer=build_template_answer(
                    question=question, evidence_pack=evidence_pack, task=task
                ),
                error="template_fallback_used_after_qc_failure",
            ),
            task,
            evidence_pack=evidence_pack,
        )
        answer_qc = assess_answer_quality(llm_result.answer, evidence_pack, task=task)
        attempts.append(
            write_answer_attempt(
                output_dir=output_dir,
                attempt_index=len(attempts),
                kind="template_fallback",
                llm_result=llm_result,
                qc_report=answer_qc,
            )
        )
    return llm_result, answer_qc, attempts, answer_backend_meta


def normalize_llm_result(llm_result: LLMResult, task: str, evidence_pack: dict | None = None) -> LLMResult:
    normalized_answer = normalize_answer_format(llm_result.answer, task=task)
    normalized_answer = prune_redundant_secondary_citations(
        normalized_answer, task=task, evidence_pack=evidence_pack
    )
    normalized_answer = label_secondary_citation_roles(
        normalized_answer, task=task, evidence_pack=evidence_pack
    )
    normalized_answer = qualify_evidence_absence_claims(normalized_answer, task=task)
    normalized_answer = normalize_answer_references(normalized_answer, task=task, evidence_pack=evidence_pack)
    if (task or "answer").strip().lower() == "answer":
        normalized_answer = ensure_required_lineage_caveat(
            normalized_answer, evidence_pack or {}
        )
        normalized_answer = normalize_inline_evidence_labels(normalized_answer, evidence_pack or {})
        normalized_answer = annotate_publication_family_relationships(
            normalized_answer, evidence_pack or {}
        )
    if normalized_answer == llm_result.answer:
        return llm_result
    return LLMResult(
        backend=llm_result.backend,
        answer=normalized_answer,
        error=llm_result.error,
        usage=llm_result.usage,
    )


def prune_redundant_secondary_citations(
    answer: str | None, task: str, evidence_pack: dict | None
) -> str | None:
    """Drop a secondary E-ID when the same claim already cites primary evidence.

    This is a citation-role normalization, not a claim rewrite. Claims supported
    only by a secondary source remain unchanged and must label that role or fail
    QC normally.
    """
    if answer is None or (task or "answer").strip().lower() != "answer":
        return answer
    source_type_by_id = {
        str(chunk.get("evidence_id") or ""): str(
            (chunk.get("source_type_assessment") or {}).get("source_type") or ""
        )
        for chunk in (evidence_pack or {}).get("chunks", [])
    }
    secondary_types = {
        "systematic_review_meta_analysis",
        "narrative_review",
        "editorial_commentary",
        "reanalysis_secondary_data",
        "protocol",
    }

    def prune_group(match: re.Match[str]) -> str:
        content = match.group(1)
        evidence_ids = re.findall(r"\bE\d{3}\b", content)
        secondary = {
            evidence_id
            for evidence_id in evidence_ids
            if source_type_by_id.get(evidence_id) in secondary_types
        }
        primary = {
            evidence_id for evidence_id in evidence_ids if evidence_id not in secondary
        }
        if not secondary or not primary:
            return match.group(0)
        pruned = content
        for evidence_id in secondary:
            pruned = re.sub(rf"\b{re.escape(evidence_id)}\b", "", pruned)
        pruned = re.sub(r"\s*;\s*;\s*", "; ", pruned)
        pruned = re.sub(r"^\s*[;,]\s*|\s*[;,]\s*$", "", pruned).strip()
        return f"({pruned})" if pruned else ""

    body, separator, references = answer.partition("## References")
    normalized_body = re.sub(r"\(([^()]*)\)", prune_group, body)
    return normalized_body + (separator + references if separator else "")


def label_secondary_citation_roles(
    answer: str | None, task: str, evidence_pack: dict | None
) -> str | None:
    """Make a secondary-only claim's source role explicit without rewriting it."""
    if answer is None or (task or "answer").strip().lower() != "answer":
        return answer
    secondary_types = {
        "systematic_review_meta_analysis",
        "narrative_review",
        "editorial_commentary",
        "reanalysis_secondary_data",
        "protocol",
    }
    source_type_by_id = {
        str(chunk.get("evidence_id") or ""): str(
            (chunk.get("source_type_assessment") or {}).get("source_type") or ""
        )
        for chunk in (evidence_pack or {}).get("chunks", [])
    }
    role_pattern = re.compile(
        r"\b(?:systematic review|review|meta[- ]analysis|secondary (?:source|analysis|synthesis)|"
        r"reanalysis|protocol|commentary|synthesi[sz](?:es|ed|ing)?)\b",
        flags=re.IGNORECASE,
    )
    body, separator, references = answer.partition("## References")
    output_lines = []
    for line in body.splitlines(keepends=True):
        pieces = re.split(r"(?<!et al\.)(?<=[.!?])(\s+)", line)
        for index in range(0, len(pieces), 2):
            claim = pieces[index]
            evidence_ids = re.findall(r"\bE\d{3}\b", claim)
            secondary_ids = [
                evidence_id
                for evidence_id in evidence_ids
                if source_type_by_id.get(evidence_id) in secondary_types
            ]
            primary_ids = [
                evidence_id for evidence_id in evidence_ids if evidence_id not in secondary_ids
            ]
            if secondary_ids and not primary_ids and not role_pattern.search(claim):
                leading = claim[: len(claim) - len(claim.lstrip())]
                pieces[index] = f"{leading}Secondary synthesis: {claim.lstrip()}"
        output_lines.append("".join(pieces))
    normalized_body = "".join(output_lines)
    return normalized_body + (separator + references if separator else "")


def qualify_evidence_absence_claims(answer: str | None, task: str) -> str | None:
    """Scope retrieval-gap statements to the supplied evidence pack."""
    if answer is None or (task or "answer").strip().lower() != "answer":
        return answer
    body, separator, references = answer.partition("## References")
    normalized = re.sub(
        r"\bNo evidence directly links\b",
        "The retrieved evidence pack contains no direct evidence linking",
        body,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bDirect evidence (?P<object>(?:(?!\.\s+[A-Z])[^\n]){1,500}?) is absent\b",
        r"The retrieved evidence pack does not contain direct evidence \g<object>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bthe evidence lacks\b",
        "the retrieved evidence pack lacks",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bThe absence of (?P<object>[^.\n]{1,300}?) in the evidence\b",
        r"The absence of \g<object> in the retrieved evidence pack",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized + (separator + references if separator else "")


def normalize_answer_references(answer: str | None, task: str, evidence_pack: dict | None) -> str | None:
    if answer is None or (task or "answer").strip().lower() != "answer":
        return answer
    chunks = list((evidence_pack or {}).get("chunks", []))
    if not chunks:
        return answer
    chunk_by_id = {str(chunk.get("evidence_id")): chunk for chunk in chunks if chunk.get("evidence_id")}
    answer_body = re.split(r"(?im)^## References\b", answer, maxsplit=1)[0]
    cited_ids = ordered_evidence_ids(answer_body)
    known_cited_ids = [evidence_id for evidence_id in cited_ids if evidence_id in chunk_by_id]
    if not known_cited_ids:
        return answer

    references = "\n".join(reference_line(chunk_by_id[evidence_id]) for evidence_id in known_cited_ids)
    references_section = f"## References\n{references}"
    match = re.search(r"\n?## References\b.*", answer, flags=re.DOTALL)
    if match:
        body = answer[: match.start()].rstrip()
        return f"{body}\n\n{references_section}".strip()
    return f"{answer.rstrip()}\n\n{references_section}".strip()


def ordered_evidence_ids(answer: str) -> list[str]:
    seen = set()
    evidence_ids = []
    for evidence_id in re.findall(r"\bE\d{3}\b", answer):
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        evidence_ids.append(evidence_id)
    return evidence_ids


def reference_line(chunk: dict) -> str:
    evidence_id = str(chunk.get("evidence_id") or "").strip()
    citation = str(chunk.get("citation_label") or "").strip()
    title = str(chunk.get("title") or "").strip()
    doi = str(chunk.get("doi") or "").strip()
    parts = [part for part in [citation, title] if part]
    if doi:
        parts.append(doi if doi.startswith(("http://", "https://")) else f"https://doi.org/{doi}")
    return f"- {evidence_id}: {' '.join(parts).strip()}"


def generate_initial_answer(
    question: str,
    task: str,
    backend: str,
    prompt: str,
    evidence_pack: dict,
    answer_model: str | None = None,
    answer_temperature: float = 0.0,
    answer_timeout: int = 120,
    evidence_is_sufficient: bool = True,
    force_answer: bool = False,
) -> tuple[LLMResult, dict]:
    backend = (backend or "none").strip().lower()
    if backend != "none" and not evidence_is_sufficient and not force_answer:
        return (
            LLMResult(
                backend="none",
                answer=None,
                error="answer backend blocked because evidence grade is below threshold",
            ),
            {
                "backend_requested": backend,
                "backend_used": "none",
                "model": answer_model,
                "call_performed": False,
                "blocked": True,
                "block_reason": "insufficient_evidence",
            },
        )
    if backend in {"none", "ollama", "qwen", "deepseek"}:
        backend_result = generate_answer_with_backend(
            prompt,
            backend=backend,
            model=answer_model,
            temperature=answer_temperature,
            timeout=answer_timeout,
            evidence_is_sufficient=evidence_is_sufficient,
            force_answer=force_answer,
        )
        return backend_result.llm_result, {
            "backend_requested": backend_result.backend_requested,
            "backend_used": backend_result.backend_used,
            "model": backend_result.model,
            "call_performed": backend_result.call_performed,
            "blocked": backend_result.blocked,
            "block_reason": backend_result.block_reason,
        }
    if backend == "template":
        return (
            LLMResult(
                backend="template",
                answer=build_template_answer(question=question, evidence_pack=evidence_pack, task=task),
                error=None,
            ),
            {
                "backend_requested": backend,
                "backend_used": "template",
                "model": None,
                "call_performed": False,
                "blocked": False,
                "block_reason": None,
            },
        )
    result = generate_answer(prompt, backend=backend)
    return (
        result,
        {
            "backend_requested": backend,
            "backend_used": result.backend,
            "model": answer_model,
            "call_performed": backend not in {"none", "template"},
            "blocked": False,
            "block_reason": None,
        },
    )


def write_answer_attempt(
    output_dir: Path,
    attempt_index: int,
    kind: str,
    llm_result: object,
    qc_report: dict,
    prompt_path: Path | None = None,
) -> dict:
    answer_attempt_path = None
    qc_attempt_path = output_dir / f"answer_qc_attempt_{attempt_index}.json"
    if llm_result.answer:
        answer_attempt_path = output_dir / f"answer_attempt_{attempt_index}.md"
        answer_attempt_path.write_text(llm_result.answer + "\n", encoding="utf-8")
    write_json(qc_attempt_path, qc_report)
    return {
        "attempt": attempt_index,
        "kind": kind,
        "backend": llm_result.backend,
        "error": llm_result.error,
        "usage": llm_result.usage,
        "qc_passed": qc_report.get("passed"),
        "qc_warnings": qc_report.get("warnings", []),
        "answer_path": relative_output(answer_attempt_path) if answer_attempt_path else None,
        "prompt_path": relative_output(prompt_path) if prompt_path else None,
        "qc_path": relative_output(qc_attempt_path),
    }


def aggregate_answer_usage(attempts: list[dict]) -> dict[str, int]:
    keys = (
        "prompt_eval_count",
        "eval_count",
        "prompt_eval_duration",
        "eval_duration",
        "load_duration",
        "total_duration",
    )
    return {
        key: sum(int((attempt.get("usage") or {}).get(key, 0)) for attempt in attempts)
        for key in keys
    }


def resolve_run_settings(args: argparse.Namespace) -> dict:
    task = getattr(args, "task", "answer") or "answer"
    preset_name = getattr(args, "preset", None) or "balanced"
    preset = PRESETS.get(preset_name, PRESETS["balanced"]).get(task, PRESETS["balanced"]["answer"])
    return {
        "preset": preset_name,
        "limit": int(getattr(args, "limit", None) if getattr(args, "limit", None) is not None else preset["limit"]),
        "candidate_multiplier": int(
            getattr(args, "candidate_multiplier", None)
            if getattr(args, "candidate_multiplier", None) is not None
            else preset["candidate_multiplier"]
        ),
        "max_per_paper": int(
            getattr(args, "max_per_paper", None)
            if getattr(args, "max_per_paper", None) is not None
            else preset["max_per_paper"]
        ),
        "max_chars_per_chunk": int(
            getattr(args, "max_chars_per_chunk", None)
            if getattr(args, "max_chars_per_chunk", None) is not None
            else preset["max_chars_per_chunk"]
        ),
    }


def candidate_passage_limit(
    *,
    task: str,
    evidence_selector: str,
    max_per_paper: int,
) -> int:
    """Keep enough within-paper candidates to select the direct passage later.

    ``max_per_paper`` is a final evidence-composition limit. Applying it during
    each retrieval query can discard a lower-scoring passage that co-locates a
    requested entity and phenotype before the lineage-aware selector sees it.
    Answer runs with a downstream selector therefore retain a small bounded
    within-paper pool; the shortlist still contributes only one passage per
    paper to the expensive answer-selection stage.
    """

    configured = max(1, int(max_per_paper))
    if task == "answer" and evidence_selector != "current":
        return max(4, configured)
    return configured


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def validate_corpus_inputs(chunks_dir: Path, papers_tsv: Path) -> None:
    if not chunks_dir.exists():
        raise FileNotFoundError(f"chunks directory does not exist: {chunks_dir}")
    if not chunks_dir.is_dir():
        raise NotADirectoryError(f"chunks path is not a directory: {chunks_dir}")
    if not papers_tsv.exists():
        raise FileNotFoundError(f"paper metadata TSV does not exist: {papers_tsv}")
    if not papers_tsv.is_file():
        raise ValueError(f"paper metadata path is not a file: {papers_tsv}")


def load_document_design_context(
    chunks_dir: Path, paper_id: str, *, max_chars: int = 120_000
) -> str:
    """Read bounded local text for source-design and origin-identifier screening."""
    if max_chars <= 0:
        return ""
    path = chunks_dir / f"{paper_id}.jsonl"
    if not path.is_file():
        return ""
    try:
        stat = path.stat()
        return _cached_document_design_context(
            str(path.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            int(max_chars),
        )
    except (OSError, ValueError):
        return ""


@lru_cache(maxsize=1024)
def _cached_document_design_context(
    path_text: str,
    modified_ns: int,
    size: int,
    max_chars: int,
) -> str:
    """Read and cache only the bounded front text needed for design screening."""

    del modified_ns, size
    parts: list[str] = []
    remaining = max_chars
    with Path(path_text).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSONL in {path_text}:{line_number}: {exc.msg}"
                ) from exc
            text = str(row.get("text") or "")
            if not text:
                continue
            parts.append(text[:remaining])
            remaining -= min(len(text), remaining)
            if remaining <= 0:
                break
    return "\n".join(parts)[:max_chars]


def load_document_lineage_context(chunks_dir: Path, paper_id: str) -> list[dict]:
    """Read all local chunks while preserving evidence locations for lineage screening."""
    path = chunks_dir / f"{paper_id}.jsonl"
    if not path.is_file():
        return []
    try:
        stat = path.stat()
        rows = _cached_document_lineage_context(
            str(path.resolve()), stat.st_mtime_ns, stat.st_size
        )
        return [dict(row) for row in rows]
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1024)
def _cached_document_lineage_context(
    path_text: str, modified_ns: int, size: int
) -> tuple[dict, ...]:
    """Cache immutable-on-disk paper chunks and invalidate on file changes."""
    del modified_ns, size
    return tuple(dict(row) for row in read_jsonl(Path(path_text)))


def document_context_text(chunks: list[dict], *, max_chars: int) -> str:
    """Create bounded prose context without limiting the separate lineage scan."""
    parts: list[str] = []
    size = 0
    for row in chunks:
        text = str(row.get("text") or "")
        if text:
            parts.append(text)
            size += len(text)
        if size >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def runtime_provenance(
    chunks_dir: Path,
    papers_tsv: Path,
    *,
    corpus_state_fingerprint: str | None = None,
) -> dict[str, object]:
    commit = "unknown"
    dirty = False
    git_available = False
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        commit = commit_result.stdout.strip() or "unknown"
        dirty = bool(status_result.stdout.strip())
        git_available = True
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "code_git_commit": commit,
        "code_git_dirty": dirty,
        "code_git_available": git_available,
        "python_version": platform.python_version(),
        "platform": platform.platform(),


        "corpus_state_fingerprint": (
            corpus_state_fingerprint
            if corpus_state_fingerprint is not None
            else corpus_fingerprint(chunks_dir, papers_tsv)
        ),
    }


def make_output_dir(base_dir: Path, question: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return base_dir / f"{stamp}_{slugify(question)}"


def slugify(value: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "question")[:max_length].strip("-")


def write_results_tsv(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for result in results:
            row = {field: result.get(field, "") for field in RESULT_FIELDS}
            row["quality_flags"] = ";".join(result.get("quality_flags") or [])
            writer.writerow(row)


def print_summary(question: str, backend: str, result: dict, output_dir: Path) -> None:
    results = result["results"]
    pack = result["pack"]
    source_counter = Counter(
        " ".join(
            part
            for part in [
                str(row.get("paper_id") or ""),
                str(row.get("author") or ""),
                str(row.get("year") or ""),
            ]
            if part
        )
        for row in pack.get("chunks", [])
    )
    print("OriginRAG ask_corpus")
    print(f"- question: {question}")
    print(f"- backend: {backend}")
    print(f"- task: {pack.get('task', 'answer')}")
    settings = result.get("settings") or {}
    if settings:
        print(f"- preset: {settings.get('preset')}")
        print(
            "- context: "
            f"limit={settings.get('limit')}, "
            f"candidate_multiplier={settings.get('candidate_multiplier')}, "
            f"max_per_paper={settings.get('max_per_paper')}, "
            f"max_chars_per_chunk={settings.get('max_chars_per_chunk')}"
        )
    retrieval_queries = pack.get("retrieval_queries") or []
    if retrieval_queries:
        labels = ", ".join(str(query.get("label") or "query") for query in retrieval_queries)
        print(f"- retrieval queries: {labels}")
    if result["llm_result"].error:
        print(f"- backend warning: {result['llm_result'].error}")
    qc = result.get("answer_qc") or {}
    if result["llm_result"].answer:
        print(f"- answer qc passed: {qc.get('passed')}")
        for warning in qc.get("warnings", []):
            print(f"  - qc warning: {warning}")
    print(f"- retrieved chunks: {pack['n_chunks']}")
    print(f"- candidate chunks inspected: {len(results)}")
    print(f"- unique papers: {pack['n_unique_papers']}")
    robust = pack.get("trace_robust_lineage") or {}
    certificate = pack.get("trace_sufficiency_certificate") or {}
    if robust and certificate:
        print(f"- origin audit: {robust.get('status', 'unknown')}")
        print(
            "- papers vs certificate endpoint lineages: "
            f"{certificate.get('raw_paper_count', pack['n_unique_papers'])} vs "
            f"[{robust.get('lineage_count_lower', 0)}, "
            f"{robust.get('lineage_count_upper', 0)}]"
        )
        print(
            "- unresolved origin pairs: "
            f"{robust.get('missing_pair_count', 0)} missing, "
            f"{robust.get('uncertain_pair_count', 0)} uncertain"
        )
    print("- top source papers:")
    for source, count in source_counter.most_common(5):
        print(f"  - {source}: {count}")
    print(f"- output dir: {relative_output(output_dir)}")
    print(f"- evidence pack: {relative_output(result['pack_path'])}")
    print(f"- evidence table: {relative_output(result['evidence_table_tsv_path'])}")
    print(f"- prompt: {relative_output(result['prompt_path'])}")
    if result["answer_path"]:
        print(f"- answer: {relative_output(result['answer_path'])}")
    if result.get("withheld_answer_path"):
        print(f"- withheld answer: {relative_output(result['withheld_answer_path'])}")
    if result.get("answer_qc_path"):
        print(f"- answer qc: {relative_output(result['answer_qc_path'])}")
    if result.get("answer_grounding_report_path"):
        print(f"- answer grounding: {relative_output(result['answer_grounding_report_path'])}")


def relative_output(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
