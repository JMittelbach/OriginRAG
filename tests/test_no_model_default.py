from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ask_corpus  # noqa: E402
import originrag.retrieval.fts_index as fts_index_module  # noqa: E402
from originrag.retrieval.fts_index import build_fts_index  # noqa: E402
from originrag.retrieval.index_cache import (  # noqa: E402
    READ_CACHE_ENV,
    prime_read_index_cache,
    resolve_read_bundle,
)
from originrag.retrieval.query_plan import (  # noqa: E402
    NamedStudyReference,
    RetrievalQuery,
    build_retrieval_queries,
    required_context_anchor_groups,
    required_entity_anchor_groups,
)
from originrag.trace.blueprint import suggest_blueprint  # noqa: E402
from originrag.trace.runtime import prepare_candidates  # noqa: E402


@pytest.mark.parametrize("name", ["ATS", "GFP"])
def test_named_antibody_search_rejects_unrelated_and_background_passages(name: str) -> None:
    question = f"paper which used {name} antibody"
    candidates = [
        {"paper_id": "DIRECT", "title": "A measurement study", "section": "methods",
         "text": f"The {name} antibody was used in this study."},
        {"paper_id": "OTHER", "title": "Another antibody study", "section": "results",
         "text": "The PAM1.4 antibody was tested in this study."},
        {"paper_id": "BACKGROUND", "title": "A review", "section": "discussion",
         "text": f"Previous studies used the {name} antibody [12]."},
        {"paper_id": "PARTIAL", "title": "An unrelated study", "section": "methods",
         "text": f"The {name}2 antibody was used in this study."},
        {"paper_id": "TITLE_ONLY", "title": f"The {name} antibody", "section": "methods",
         "text": "The PAM1.4 antibody was used in this study."},
    ]
    groups = required_entity_anchor_groups(question)
    anchored = ask_corpus.retain_entity_anchored_evidence(
        candidates, groups, question=question, max_chars_per_chunk=500,
    )
    retained = ask_corpus.retain_direct_entity_source_evidence(
        anchored, question=question, entity_anchor_groups=groups, max_chars_per_chunk=500,
    )
    assert [row["paper_id"] for row in retained] == ["DIRECT"]


@pytest.mark.parametrize("has_match", [True, False])
@pytest.mark.parametrize("selector", ["current", "trace_flow"])
@pytest.mark.parametrize("retriever", ["keyword", "bm25"])
def test_named_antibody_paper_request_end_to_end(
    tmp_path: Path, has_match: bool, selector: str, retriever: str,
) -> None:
    from originrag.analysis.chat import resolve_chat_task

    question = "paper which used ATS antibody"
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    documents = [
        ("OTHER", "Antibody measurements", "methods",
         "The PAM1.4 antibody was used in this study."),
        ("REVIEW", "Antibody literature review", "discussion",
         "Previous studies used an ATS antibody [12]."),
    ]
    if has_match:
        documents.append(("DIRECT", "A measurement study", "methods",
                          "The ATS antibody was used in this study."))
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpaper_type\tfulltext_status\n" + "".join(
            f"{pid}\t{title}\t2024\t10.1000/{pid.lower()}\tarticle\tchunked\n"
            for pid, title, _, _ in documents
        ), encoding="utf-8",
    )
    for pid, _, section, text in documents:
        (chunks_dir / f"{pid}.jsonl").write_text(json.dumps({
            "chunk_id": f"{pid}_C1", "paper_id": pid, "section": section, "text": text,
        }) + "\n", encoding="utf-8")
    args = make_args(question, str(chunks_dir), str(papers), "none")
    args.task = resolve_chat_task(question)
    args.evidence_selector = selector
    args.retriever = retriever
    if retriever == "bm25":
        args.retrieval_index = str(tmp_path / "index.sqlite")
        build_fts_index(chunks_dir, papers, Path(args.retrieval_index))
    result = ask_corpus.run_question(args, tmp_path / "run")
    assert [p["paper_id"] for p in result["paper_search_results"]] == (
        ["DIRECT"] if has_match else []
    )
    config = json.loads((tmp_path / "run" / "run_config.json").read_text())
    assert config["task"] == "sources"
    assert config["answer_call_performed"] is False


def test_named_antibody_metadata_noise_does_not_exhaust_result_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = [
        {"paper_id": f"OTHER_{i}", "title": "Antibody measurements", "score": 100 - i}
        for i in range(20)
    ]
    monkeypatch.setattr(ask_corpus, "search_papers", lambda *args, **kwargs: metadata)
    result = ask_corpus.retrieve_papers_for_queries(
        question="paper which used GFP antibody",
        retrieval_queries=[RetrievalQuery("main", "GFP antibody")],
        papers_tsv=tmp_path / "unused.tsv", candidate_limit=30, result_limit=1,
        category=None, task_mode="evidence_qa", paper_search_mode="topical_discovery",
        chunk_results=[{"paper_id": "DIRECT", "text": "This study used GFP antibody.",
                        "score": 1}],
    )
    assert [row["paper_id"] for row in result] == ["DIRECT"]


def test_required_entity_filter_rejects_topical_match_without_entity() -> None:
    candidates = [
        {
            "paper_id": "METHOD",
            "title": "Interpreting single-cell data using training dynamics",
            "text": "We compare annotations and gene signatures across datasets.",
        },
        {
            "paper_id": "DIRECT",
            "title": "Macrophage states in severe disease",
            "text": "SPP1-positive macrophages were expanded in severe disease.",
        },
        {
            "paper_id": "ALIAS",
            "title": "Myeloid activation",
            "text": "Osteopontin marked a distinct myeloid population.",
        },
        {
            "paper_id": "FOOTNOTE",
            "title": "A Plasmodium study",
            "text": "Participants were repeatedly exposed to Plasmodium spp1.",
        },
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(
        candidates,
        [("spp1", "osteopontin", "secreted phosphoprotein 1")],
    )

    assert [candidate["paper_id"] for candidate in retained] == ["DIRECT", "ALIAS"]
    assert all(candidate["required_entity_anchors_verified"] for candidate in retained)


def test_required_entity_filter_rejects_metadata_only_anchor() -> None:
    candidates = [
        {
            "paper_id": "METADATA_ONLY",
            "title": "Macrophages across lungs and synovium",
            "text": "Myeloid states varied across tissues and clinical groups.",
            "topics": "SPP1 osteopontin macrophages",
            "systems": "COVID-19 severe disease",
        },
        {
            "paper_id": "LIAO",
            "title": "Single-cell immune landscape in COVID-19",
            "text": "SPP1+ macrophages were enriched in severe disease.",
        },
    ]
    groups = [
        ("spp1", "osteopontin", "secreted phosphoprotein 1"),
        ("covid 19",),
        ("severe",),
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(
        candidates,
        groups,
        question="Which SPP1 macrophages are reported in severe COVID-19?",
        max_chars_per_chunk=1800,
    )

    assert [candidate["paper_id"] for candidate in retained] == ["LIAO"]


def test_developmental_memory_tcr_anchor_rejects_bcr_and_single_age_papers() -> None:
    question = (
        "How does the memory T cell repertoire change from early childhood "
        "to adolescence?"
    )
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]
    candidates = [
        {
            "paper_id": "BCR_BOTH_AGES",
            "title": "B-cell receptor repertoire from childhood to adolescence",
            "text": "Memory B-cell clonotypes were compared in children and adolescents.",
        },
        {
            "paper_id": "TCR_CHILD_ONLY",
            "title": "Memory T-cell receptor repertoire in children",
            "text": "Pediatric TCR clonotypes were measured during childhood.",
        },
        {
            "paper_id": "DIRECT_TRAJECTORY",
            "title": "Memory T-cell receptor repertoire across childhood and adolescence",
            "text": (
                "Memory TCR clonotypes were measured in children and adolescent "
                "participants."
            ),
        },
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == [
        "DIRECT_TRAJECTORY"
    ]


def test_focused_malaria_t_cell_anchor_rejects_monocyte_only_evidence() -> None:
    question = "How do T cell states change during Plasmodium falciparum malaria?"
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]
    candidates = [
        {
            "paper_id": "MORANGA_LIKE",
            "title": "Single-cell analysis of monocytes in children with malaria",
            "text": (
                "Monocytes showed elevated interferon responses during Plasmodium "
                "falciparum malaria."
            ),
        },
        {
            "paper_id": "DIRECT_T_CELL",
            "title": "T-cell states during falciparum malaria",
            "text": (
                "CD4 T-cell states changed during Plasmodium falciparum malaria."
            ),
        },
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == ["DIRECT_T_CELL"]


def test_rifin_or_stevor_anchor_keeps_either_family_but_rejects_unrelated() -> None:
    question = (
        "Find primary studies on Plasmodium falciparum RIFIN or STEVOR proteins "
        "that address receptor binding, immune evasion, rosetting, host-cell "
        "remodelling, or therapeutic targeting."
    )
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]
    candidates = [
        {
            "paper_id": "DIRECT_RIFIN",
            "title": "A RIFIN receptor-binding study",
            "text": (
                "Plasmodium falciparum RIFIN bound an inhibitory immune receptor "
                "and altered host immune-cell activation."
            ),
        },
        {
            "paper_id": "DIRECT_STEVOR",
            "title": "STEVOR-mediated erythrocyte remodelling",
            "text": (
                "Plasmodium falciparum STEVOR promoted infected-erythrocyte "
                "rosetting and host-cell remodelling."
            ),
        },
        {
            "paper_id": "UNRELATED_PFEMP1",
            "title": "PfEMP1 adhesion in falciparum malaria",
            "text": (
                "Plasmodium falciparum PfEMP1 mediated endothelial adhesion in "
                "severe malaria."
            ),
        },
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == [
        "DIRECT_RIFIN",
        "DIRECT_STEVOR",
    ]


def test_entity_function_source_filter_rejects_background_and_off_intent() -> None:
    question = (
        "Find primary studies on Plasmodium falciparum RIFIN or STEVOR proteins "
        "that address receptor binding, immune evasion, rosetting, host-cell "
        "remodelling, or therapeutic targeting."
    )
    groups = required_entity_anchor_groups(question)
    candidates = [
        {
            "paper_id": "BACKGROUND",
            "title": "PfEMP1 transcription in severe malaria",
            "section": "discussion",
            "text": (
                "Rosetting has also been suggested to be mediated by RIFIN (50) "
                "and STEVOR (51) proteins. This study did not test that model."
            ),
        },
        {
            "paper_id": "DIRECT_GENERIC_TITLE",
            "title": "Variant surface antigens in malaria",
            "section": "abstract",
            "text": (
                "We tested recombinant RIFIN proteins and found that they bound "
                "the inhibitory receptor LILRB1."
            ),
        },
        {
            "paper_id": "OFF_INTENT_METHOD",
            "title": "Models to detect and classify RIFIN and STEVOR proteins",
            "section": "abstract",
            "text": (
                "Models to detect and classify RIFIN and STEVOR proteins\n"
                "Abstract\n"
                "Background: Many parasites use multicopy protein families to "
                "avoid their host's immune system through antigenic variation. "
                "RIFIN and STEVOR are variable antigens.\n"
                "Results: We developed hidden Markov models to classify their "
                "sequences."
            ),
        },
        {
            "paper_id": "DIRECT_STRUCTURED_RESULT",
            "title": "Functional analysis of variant malaria antigens",
            "section": "abstract",
            "text": (
                "Abstract\n"
                "Background\n"
                "RIFIN proteins have been proposed to modulate immunity.\n"
                "Results\n"
                "We found that recombinant RIFIN bound the inhibitory receptor "
                "LILRB1."
            ),
        },
        {
            "paper_id": "DIRECT_TITLE",
            "title": "STEVOR mediates erythrocyte binding and rosetting",
            "section": "discussion",
            "text": "The wider significance of these findings is discussed.",
        },
        {
            "paper_id": "BACKGROUND_ABSTRACT",
            "title": "An antigen library for malaria serology",
            "section": "abstract",
            "text": (
                "STEVOR has been implicated in rosetting (8); here we classify "
                "sequence diversity for a diagnostic antigen library."
            ),
        },
    ]

    retained = ask_corpus.retain_direct_entity_source_evidence(
        candidates,
        question=question,
        entity_anchor_groups=groups,
        max_chars_per_chunk=1800,
    )

    assert [candidate["paper_id"] for candidate in retained] == [
        "DIRECT_GENERIC_TITLE",
        "DIRECT_STRUCTURED_RESULT",
        "DIRECT_TITLE",
    ]
    assert [candidate["entity_source_directness"] for candidate in retained] == [
        "own_study_entity_function_statement",
        "own_study_entity_function_statement",
        "title_entity_function",
    ]


def test_entity_function_source_filter_is_inactive_without_function_constraint() -> None:
    question = "Find primary studies on Plasmodium falciparum RIFIN proteins."
    candidates = [
        {
            "paper_id": "RIFIN_CLASSIFICATION",
            "title": "Classification of RIFIN proteins",
            "section": "results",
            "text": "We classified RIFIN sequences into two groups.",
        }
    ]

    retained = ask_corpus.retain_direct_entity_source_evidence(
        candidates,
        question=question,
        entity_anchor_groups=[("rifin", "rifins")],
    )

    assert retained == candidates


def test_natural_binding_to_receptors_syntax_activates_direct_function_filter() -> None:
    question = (
        "Find primary studies of Plasmodium falciparum RIFIN binding to human "
        "immune receptors including LILRB1, LAIR1, KIR2DL1, or KIR2DS1."
    )
    candidates = [
        {
            "paper_id": "DIRECT_BINDING",
            "title": "RIFINs bind the inhibitory receptor LILRB1",
            "section": "abstract",
            "text": "We found that RIFIN proteins bound LILRB1.",
        },
        {
            "paper_id": "LOCALIZATION_ONLY",
            "title": "Membrane localization of RIFIN proteins",
            "section": "results",
            "text": "We localized RIFIN proteins at the erythrocyte membrane.",
        },
        {
            "paper_id": "ERYTHROCYTE_BINDING_ONLY",
            "title": "RIFIN erythrocyte binding during invasion",
            "section": "results",
            "text": "We found that RIFIN bound erythrocytes during invasion.",
        },
    ]

    retained = ask_corpus.retain_direct_entity_source_evidence(
        candidates,
        question=question,
        entity_anchor_groups=[("rifin", "rifins")],
    )

    assert [candidate["paper_id"] for candidate in retained] == ["DIRECT_BINDING"]


def test_answer_filter_removes_only_attributed_background_match() -> None:
    question = "What evidence links CXCL8 and CCL4 to cerebral malaria mortality?"
    candidates = [
        {
            "paper_id": "BACKGROUND",
            "chunk_id": "BACKGROUND_DISCUSSION",
            "section": "discussion",
            "text": (
                "Several studies have shown that circulating CXCL8 and CCL4 predict "
                "cerebral malaria mortality [13, 34-36]."
            ),
        },
        {
            "paper_id": "DIRECT",
            "chunk_id": "DIRECT_DISCUSSION",
            "section": "discussion",
            "text": (
                "Consistent with earlier work [13], we found that circulating CXCL8 "
                "and CCL4 predicted cerebral malaria mortality in our cohort."
            ),
        },
        {
            "paper_id": "RESULTS",
            "chunk_id": "DIRECT_RESULTS",
            "section": "results",
            "text": "CXCL8 and CCL4 predicted cerebral malaria mortality.",
        },
    ]

    retained = ask_corpus.remove_attribution_only_answer_candidates(
        candidates,
        question=question,
        max_chars_per_chunk=950,
    )

    assert [candidate["paper_id"] for candidate in retained] == [
        "DIRECT",
        "RESULTS",
    ]


def test_background_excerpt_does_not_remove_direct_excerpt_from_same_paper() -> None:
    question = "Which T-cell repertoire changes were observed in adolescents?"
    candidates = [
        {
            "paper_id": "P1",
            "chunk_id": "BACKGROUND",
            "section": "discussion",
            "text": "Previous studies reported T-cell repertoire changes [7].",
        },
        {
            "paper_id": "P1",
            "chunk_id": "DIRECT",
            "section": "results",
            "text": "We observed T-cell repertoire changes in adolescents.",
        },
    ]

    retained = ask_corpus.remove_attribution_only_answer_candidates(
        candidates,
        question=question,
        max_chars_per_chunk=950,
    )

    assert [candidate["chunk_id"] for candidate in retained] == ["DIRECT"]


def test_multi_family_anchor_rejects_host_only_rna_seq() -> None:
    question = (
        "Find papers linking Plasmodium falciparum expression to clinical malaria "
        "phenotypes, including var/PfEMP1, RIFINs, STEVORs, and the parasite "
        "transcriptome."
    )
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]
    candidates = [
        {
            "paper_id": "HOST_ONLY",
            "title": "Host whole-blood RNA-seq in severe malaria",
            "text": (
                "We profiled host gene expression in patients with Plasmodium "
                "falciparum malaria."
            ),
        },
        {
            "paper_id": "PARASITE",
            "title": "Transcriptome analysis of Plasmodium falciparum in severe malaria",
            "text": "Parasite RNA-seq revealed disease-associated expression.",
        },
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == ["PARASITE"]


def test_multi_family_source_diversity_reserves_only_grounded_direct_facets() -> None:
    question = (
        "xFind papers linking Plasmodium falciparum expression to clinical malaria "
        "phenotypes. Focus on var/PfEMP1, RIFINs, STEVORs, and the core parasite "
        "transcriptome, including severe, uncomplicated, and asymptomatic malaria; "
        "report whether developmental stage was adjusted for."
    )
    generic = [
        {
            "paper_id": f"VAR_{index}",
            "title": f"PfEMP1 expression and severe malaria cohort {index}",
            "rank": index,
            "rrf_score": 1 / index,
        }
        for index in range(1, 9)
    ]
    facets = [
        {
            "paper_id": "RIFIN",
            "title": "RIFINs linked to severe malaria risk",
            "rank": 9,
            "rrf_score": 0.09,
        },
        {
            "paper_id": "STEVOR_INDIRECT",
            "title": "STEVOR structure in laboratory parasites",
            "rank": 10,
            "rrf_score": 0.08,
        },
        {
            "paper_id": "DUAL",
            "title": "Dual transcriptome analysis of host-pathogen interactions in severe malaria",
            "rank": 11,
            "rrf_score": 0.07,
        },
        {
            "paper_id": "CORE",
            "title": "The Plasmodium falciparum transcriptome in severe malaria",
            "rank": 12,
            "rrf_score": 0.06,
        },
        {
            "paper_id": "STAGE",
            "title": (
                "Plasmodium falciparum transcription in different clinical presentations "
                "of malaria associates with circulation time"
            ),
            "rank": 13,
            "rrf_score": 0.05,
        },
        {
            "paper_id": "ASYMPTOMATIC",
            "title": (
                "Plasmodium falciparum expresses fewer genes during asymptomatic "
                "malaria than clinical cases"
            ),
            "rank": 14,
            "rrf_score": 0.04,
        },
        {
            "paper_id": "UNANCHORED_CORE",
            "title": "Parasite transcriptome in cerebral malaria",
            "rank": 15,
            "rrf_score": 0.03,
        },
    ]
    papers = [*generic, *facets]
    anchored_ids = {
        paper["paper_id"] for paper in papers if paper["paper_id"] != "UNANCHORED_CORE"
    }
    chunks = [{"paper_id": paper_id} for paper_id in anchored_ids]

    ranked = ask_corpus.diversify_explicit_expression_family_results(
        question,
        papers,
        chunks,
        result_limit=8,
    )
    displayed = {paper["paper_id"] for paper in ranked}

    assert {"RIFIN", "DUAL", "CORE", "STAGE", "ASYMPTOMATIC"} <= displayed
    assert "STEVOR_INDIRECT" not in displayed
    assert "UNANCHORED_CORE" not in displayed
    assert [paper["rank"] for paper in ranked] == list(range(1, 9))


def test_weak_direct_report_can_only_open_trace_bounded_answer_gate() -> None:
    allowed = ask_corpus.provisional_synthesis_is_authorized(
        requested=True,
        task="answer",
        retrieval_evidence_is_sufficient=False,
        bounded_direct_report_supported=True,
        trace_available=True,
        evidence_is_sufficient=False,
    )

    assert allowed is True
    assert not ask_corpus.provisional_synthesis_is_authorized(
        requested=True,
        task="answer",
        retrieval_evidence_is_sufficient=False,
        bounded_direct_report_supported=False,
        trace_available=True,
        evidence_is_sufficient=False,
    )
    assert not ask_corpus.provisional_synthesis_is_authorized(
        requested=True,
        task="hypothesis",
        retrieval_evidence_is_sufficient=False,
        bounded_direct_report_supported=True,
        trace_available=True,
        evidence_is_sufficient=False,
    )


def test_gene_and_disease_context_filter_keeps_only_direct_joint_passage() -> None:
    candidates = [
        {
            "paper_id": "LIAO",
            "title": "Single-cell landscape of immune cells in patients with COVID-19",
            "text": "SPP1+ macrophages were evaluated across disease-severity groups.",
        },
        {
            "paper_id": "FIBROSIS",
            "title": "Macrophages in pulmonary fibrosis",
            "text": "SPP1-positive macrophages were expanded in fibrotic tissue.",
        },
        {
            "paper_id": "COVID_ONLY",
            "title": "Single-cell responses in COVID-19",
            "text": "Macrophage states varied with disease severity.",
        },
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(
        candidates,
        [
            ("spp1", "osteopontin", "secreted phosphoprotein 1"),
            ("covid 19",),
        ],
    )

    assert [candidate["paper_id"] for candidate in retained] == ["LIAO"]


def test_spp1_covid_query_retrieves_direct_paper_not_generic_or_footnote(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tdomain\ttopics\tfulltext_status\n"
        "LIAO\tSingle-cell landscape of immune cells in patients with COVID-19\t"
        "2020\t10.1038/direct\tcovid\tmacrophages\tchunked\n"
        "METHOD\tInterpreting single-cell data using training dynamics\t"
        "2024\t10.1038/method\tmethods\tgene signatures\tchunked\n"
        "MALARIA\tPopulation genomics in malaria\t"
        "2017\t10.1038/malaria\tmalaria\tparasites\tchunked\n",
        encoding="utf-8",
    )
    records = {
        "LIAO": "SPP1+ macrophages were assessed across COVID-19 severity groups. " * 8,
        "METHOD": "We interpret annotations and gene signatures in single-cell data. " * 8,
        "MALARIA": "Children were exposed repeatedly to Plasmodium spp1. " * 8,
    }
    for paper_id, text in records.items():
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "section": "results",
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks_dir, papers, index)
    question = (
        "What SPP1-positive macrophage signatures are reported, and how are they "
        "associated with COVID-19 severity?"
    )
    queries = build_retrieval_queries(question, task="answer", max_queries=4)

    retrieved = ask_corpus.retrieve_chunks_for_queries(
        retrieval_queries=queries,
        chunks_dir=chunks_dir,
        papers_tsv=papers,
        candidate_limit=20,
        max_per_paper=2,
        include_low_quality=False,
        exclude_tables=True,
        exclude_figures=True,
        exclude_supplement=True,
        category=None,
        require_doi=True,
        min_score=0.0,
        query_mode="evidence",
        retriever="bm25",
        retrieval_index=index,
    )
    retained = ask_corpus.retain_entity_anchored_evidence(
        retrieved,
        required_entity_anchor_groups(question),
    )

    assert [candidate["paper_id"] for candidate in retained] == ["LIAO"]


def test_multi_query_bm25_validates_index_once(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tEPCR binding in cerebral malaria\t2024\t10.1/p1\tchunked\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "PfEMP1 binds EPCR in severe cerebral malaria. " * 12,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    index = tmp_path / "retrieval.sqlite"
    build_fts_index(chunks_dir, papers, index)
    queries = [
        RetrievalQuery("Q001", "PfEMP1 EPCR severe malaria", "main"),
        RetrievalQuery("Q002", "EPCR cerebral malaria", "focused"),
    ]
    validation_calls: list[tuple[Path, Path, Path]] = []
    real_ensure_current_index = fts_index_module.ensure_current_index

    def counted_validation(
        index_path: Path, chunk_path: Path, paper_path: Path
    ) -> str:
        validation_calls.append((index_path, chunk_path, paper_path))
        return real_ensure_current_index(index_path, chunk_path, paper_path)

    store_validation_calls: list[Path] = []
    real_store_validation = fts_index_module.index_has_candidate_record_store

    def counted_store_validation(index_path: Path, **kwargs: object) -> bool:
        store_validation_calls.append(index_path)
        return real_store_validation(index_path, **kwargs)

    def unexpected_subquery_store_validation(
        _index_path: Path, **_kwargs: object
    ) -> bool:
        raise AssertionError("candidate-store completeness must be checked once per request")

    monkeypatch.setattr(ask_corpus, "ensure_current_index", counted_validation)
    monkeypatch.setattr(
        fts_index_module,
        "ensure_current_index",
        counted_validation,
    )
    monkeypatch.setattr(
        ask_corpus,
        "index_has_candidate_record_store",
        counted_store_validation,
    )
    monkeypatch.setattr(
        fts_index_module,
        "index_has_candidate_record_store",
        unexpected_subquery_store_validation,
    )

    results = ask_corpus.retrieve_chunks_for_queries(
        retrieval_queries=queries,
        chunks_dir=chunks_dir,
        papers_tsv=papers,
        candidate_limit=4,
        max_per_paper=2,
        include_low_quality=False,
        exclude_tables=False,
        exclude_figures=False,
        exclude_supplement=False,
        category=None,
        require_doi=True,
        min_score=0.0,
        query_mode="evidence",
        retriever="bm25",
        retrieval_index=index,
    )

    assert [result["paper_id"] for result in results] == ["P1"]
    assert validation_calls == [(index, chunks_dir, papers)]
    assert store_validation_calls == [index]


def test_run_question_reuses_prequery_fingerprint_for_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tEPCR binding in cerebral malaria\t2024\t10.1/p1\tchunked\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "PfEMP1 binds EPCR in severe cerebral malaria. " * 12,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    index = tmp_path / "retrieval.sqlite"
    built = build_fts_index(chunks_dir, papers, index)
    fingerprint_calls: list[tuple[Path, Path]] = []
    real_strong_manifest = fts_index_module.strong_corpus_file_manifest

    def counted_fingerprint(chunk_path: Path, paper_path: Path):
        fingerprint_calls.append((chunk_path, paper_path))
        return real_strong_manifest(chunk_path, paper_path)

    def unexpected_provenance_walk(*_args, **_kwargs):
        raise AssertionError("runtime provenance must reuse the prequery fingerprint")

    monkeypatch.setattr(
        fts_index_module, "strong_corpus_file_manifest", counted_fingerprint
    )
    monkeypatch.setattr(ask_corpus, "corpus_fingerprint", unexpected_provenance_walk)
    args = make_args(
        question="What links PfEMP1 EPCR binding to cerebral malaria?",
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="none",
    )
    args.retriever = "bm25"
    args.retrieval_index = str(index)
    args.evidence_selector = "current"
    output_dir = tmp_path / "run"

    ask_corpus.run_question(args, output_dir)
    run_config = json.loads((output_dir / "run_config.json").read_text())

    assert fingerprint_calls == [(chunks_dir, papers)]
    assert run_config["corpus_state_fingerprint"] == built.corpus_content_digest


def test_run_question_reuses_resolved_cache_attestation_without_corpus_walk(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tEPCR binding in cerebral malaria\t2024\t10.1000/p1\tchunked\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "PfEMP1 binds EPCR in severe cerebral malaria. " * 12,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_index = tmp_path / "canonical.sqlite"
    read_cache = tmp_path / "local" / "read-cache.sqlite"
    built = build_fts_index(chunks_dir, papers, source_index)
    prime_read_index_cache(
        source_index,
        chunks_dir,
        papers,
        environ={READ_CACHE_ENV: str(read_cache)},
    )
    read_bundle = resolve_read_bundle(
        source_index,
        chunks_dir,
        papers,
        environ={READ_CACHE_ENV: str(read_cache)},
    )
    validation_calls: list[Path] = []

    def validate_source(index_path: Path, chunk_path: Path, paper_path: Path) -> str:
        validation_calls.append(index_path)
        raise AssertionError("an attested read cache must not repeat the corpus walk")

    monkeypatch.setattr(ask_corpus, "ensure_current_index", validate_source)
    monkeypatch.setenv(READ_CACHE_ENV, str(read_cache))
    monkeypatch.setattr(
        ask_corpus,
        "index_has_candidate_record_store",
        lambda *_args, **_kwargs: pytest.fail(
            "the exact read-cache receipt already validated candidate parity"
        ),
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "UNINDEXED canonical replacement must not enter this request.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tfulltext_status\n"
        "P1\tChanged canonical title\t2025\t10.1000/new\tchunked\n",
        encoding="utf-8",
    )
    args = make_args(
        question=(
            "What does DOI 10.1000/p1 report about PfEMP1 EPCR binding in "
            "cerebral malaria?"
        ),
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="none",
    )
    args.retriever = "bm25"
    args.retrieval_index = str(source_index)
    args.evidence_selector = "current"
    output_dir = tmp_path / "run-cache"

    result = ask_corpus.run_question(args, output_dir)
    run_config = json.loads((output_dir / "run_config.json").read_text())

    assert validation_calls == []
    assert run_config["retrieval_index_source"] == str(source_index)
    assert run_config["retrieval_index_read"] == str(read_bundle.index_path)
    assert run_config["chunks_dir_read"] == str(read_bundle.chunks_dir)
    assert run_config["papers_read"] == str(read_bundle.papers_tsv)
    assert run_config["retrieval_cache_generation"] == read_bundle.cache_generation
    assert run_config["retrieval_cache_receipt_sha256"]
    assert run_config["corpus_state_fingerprint"] == built.corpus_content_digest
    assert result["results"][0]["doi"] == "10.1000/p1"
    assert result["results"][0]["explicit_identifier_anchor"] is True
    assert "UNINDEXED canonical replacement" not in result["results"][0]["text"]
    assert (output_dir / "retrieved_chunks.tsv").read_text(encoding="utf-8").count(
        "P1_C1"
    ) == 1


def test_entity_anchored_paper_search_excludes_metadata_only_matches() -> None:
    papers = [
        {"paper_id": "DIRECT", "title": "SPP1 macrophages in disease"},
        {"paper_id": "GENERIC", "title": "Gene signatures in disease"},
    ]
    anchored_chunks = [
        {
            "paper_id": "DIRECT",
            "text": "SPP1-positive macrophages were expanded.",
        }
    ]
    filtered = ask_corpus.retain_papers_with_anchored_evidence(papers, anchored_chunks)

    assert filtered == [papers[0]]


def test_entity_anchored_paper_search_carries_directness_audit_label() -> None:
    papers = [{"paper_id": "DIRECT", "title": "A direct entity study"}]
    anchored_chunks = [
        {
            "paper_id": "DIRECT",
            "entity_source_directness": "abstract_entity_function_statement",
        }
    ]

    filtered = ask_corpus.retain_papers_with_anchored_evidence(
        papers,
        anchored_chunks,
    )

    assert filtered == [
        {
            **papers[0],
            "entity_source_directness": "abstract_entity_function_statement",
        }
    ]


def test_trace_answer_retrieval_keeps_bounded_within_paper_candidate_pool() -> None:
    assert (
        ask_corpus.candidate_passage_limit(
            task="answer",
            evidence_selector="trace_flow",
            max_per_paper=2,
        )
        == 4
    )
    assert (
        ask_corpus.candidate_passage_limit(
            task="answer",
            evidence_selector="current",
            max_per_paper=2,
        )
        == 2
    )
    assert (
        ask_corpus.candidate_passage_limit(
            task="sources",
            evidence_selector="trace_flow",
            max_per_paper=2,
        )
        == 2
    )


def test_paper_search_uses_ordinal_directness_before_rrf() -> None:
    question = (
        "Find primary papers that directly investigate associations between PfEMP1 "
        "expression and cerebral or severe malaria clinical phenotypes."
    )
    papers = [
        {
            "paper_id": "CHMI",
            "title": "Predictive value of PfEMP1 antibody profiles in controlled human malaria infection",
            "rrf_score": 0.04,
            "score": 140,
        },
        {
            "paper_id": "MECHANISTIC",
            "title": "Neutrophils impose pressure against PfEMP1 variants implicated in cerebral malaria",
            "rrf_score": 0.03,
            "score": 135,
        },
        {
            "paper_id": "TURNER",
            "title": "Severe malaria is associated with parasite binding to endothelial protein C receptor",
            "rrf_score": 0.02,
            "score": 120,
        },
        {
            "paper_id": "NEGATIVE",
            "title": "Clinical evaluation of PfEMP1 domains",
            "rrf_score": 0.018,
            "score": 118,
        },
    ]
    chunks = [
        {
            "paper_id": "CHMI",
            "text": "PfEMP1 antibody breadth was correlated with var expression after controlled infection.",
        },
        {
            "paper_id": "CHMI",
            "text": "Cerebral and severe malaria are important clinical syndromes.",
        },
        {
            "paper_id": "CHMI",
            "text": "Background studies associated PfEMP1 expression with severe malaria.",
        },
        {
            "paper_id": "MECHANISTIC",
            "text": "Here we show that neutrophils kill isolates expressing PfEMP1 implicated in cerebral malaria.",
        },
        {
            "paper_id": "TURNER",
            "text": "Severe malaria is associated with expression of DC8 and DC13 PfEMP1 subtypes.",
        },
        {
            "paper_id": "NEGATIVE",
            "text": "We found no association between PfEMP1 expression and cerebral malaria.",
        },
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        question,
        papers,
        chunks,
    )

    assert [paper["paper_id"] for paper in ranked] == [
        "TURNER",
        "NEGATIVE",
        "MECHANISTIC",
        "CHMI",
    ]
    assert [paper["rank"] for paper in ranked] == [1, 2, 3, 4]
    assert [paper["question_directness_tier"] for paper in ranked] == [2, 2, 1, 0]
    assert ranked[1]["question_directness_label"] == "direct_clinical_report"
    assert ranked[-1]["question_directness_label"] == "background_or_distributed"


def test_genuine_chmi_paper_search_preserves_retrieval_order() -> None:
    papers = [
        {
            "paper_id": "CHMI",
            "title": "Predictive value of PfEMP1 antibody profiles in controlled human malaria infection",
            "rrf_score": 0.01,
        },
        {
            "paper_id": "SEVERE",
            "title": "PfEMP1 expression is associated with severe malaria",
            "rrf_score": 0.05,
        },
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find papers about PfEMP1 antibodies in controlled human malaria infection.",
        papers,
        [],
    )

    assert [paper["paper_id"] for paper in ranked] == ["CHMI", "SEVERE"]
    assert [paper["rank"] for paper in ranked] == [1, 2]
    assert all("question_directness_tier" not in paper for paper in ranked)


def test_parasite_transcriptome_sentinel_supports_directness_tiering() -> None:
    question = (
        "Find papers linking Plasmodium falciparum expression to clinical malaria "
        "phenotypes, including var/PfEMP1, RIFINs, STEVORs, and the core parasite "
        "transcriptome, especially in severe malaria."
    )
    papers = [
        {
            "paper_id": "CORE",
            "title": (
                "Plasmodium falciparum transcriptome expression is associated with "
                "severe malaria"
            ),
            "rrf_score": 0.01,
        }
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(question, papers, [])

    assert ranked[0]["question_directness_tier"] == 2
    assert ranked[0]["question_directness_label"] == "direct_clinical_report"


def test_directness_requires_the_named_disease_not_only_shared_severity() -> None:
    papers = [
        {
            "paper_id": "COVID",
            "title": "Single-cell immune states in COVID-19",
            "rrf_score": 0.05,
        },
        {
            "paper_id": "MALARIA",
            "title": "PfEMP1 expression is associated with severe malaria",
            "rrf_score": 0.01,
        },
    ]
    chunks = [
        {
            "paper_id": "COVID",
            "text": "PfEMP1 was highly expressed in patients with severe or critical infection.",
        },
        {
            "paper_id": "MALARIA",
            "text": "We found PfEMP1 expression was associated with severe malaria.",
        },
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find associations between PfEMP1 expression and severe malaria.",
        papers,
        chunks,
    )

    assert [paper["paper_id"] for paper in ranked] == ["MALARIA", "COVID"]
    assert [paper["question_directness_tier"] for paper in ranked] == [2, 0]


def test_passive_direct_result_can_inherit_named_context_from_title() -> None:
    papers = [
        {
            "paper_id": "LIAO",
            "title": "Single-cell landscape of bronchoalveolar immune cells in COVID-19",
            "rrf_score": 0.01,
        }
    ]
    chunks = [
        {
            "paper_id": "LIAO",
            "text": "SPP1 was highly expressed in patients with severe or critical infection.",
        }
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find associations between SPP1 expression and severe COVID-19.",
        papers,
        chunks,
    )

    assert ranked[0]["question_directness_tier"] == 2
    assert ranked[0]["question_directness_label"] == "direct_clinical_report"


def test_cited_passive_background_does_not_become_direct_result() -> None:
    papers = [
        {
            "paper_id": "BACKGROUND",
            "title": "Immune-cell review in COVID-19",
            "rrf_score": 0.01,
        }
    ]
    chunks = [
        {
            "paper_id": "BACKGROUND",
            "text": "Previous studies showed SPP1 was highly expressed in severe COVID-19 [12].",
        }
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find associations between SPP1 expression and severe COVID-19.",
        papers,
        chunks,
    )

    assert ranked[0]["question_directness_tier"] == 0


def test_general_title_cannot_borrow_requested_entity_from_unrelated_passage() -> None:
    papers = [
        {
            "paper_id": "GENERAL",
            "title": "Gene expression associated with severe COVID-19",
            "rrf_score": 0.01,
        }
    ]
    chunks = [
        {
            "paper_id": "GENERAL",
            "text": "SPP1 was one marker included in our broad expression panel.",
        }
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find associations between SPP1 expression and severe COVID-19.",
        papers,
        chunks,
    )

    assert ranked[0]["question_directness_tier"] == 0


def test_numeric_citation_does_not_turn_prior_relation_into_current_result() -> None:
    papers = [
        {
            "paper_id": "CITED",
            "title": "Macrophage phenotypes in COVID-19",
            "rrf_score": 0.01,
        }
    ]
    chunks = [
        {
            "paper_id": "CITED",
            "text": "SPP1 expression was significantly associated with severe COVID-19 [12].",
        }
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find associations between SPP1 expression and severe COVID-19.",
        papers,
        chunks,
    )

    assert ranked[0]["question_directness_tier"] == 0


def test_cited_clinical_relation_cannot_borrow_later_mechanistic_result() -> None:
    papers = [
        {
            "paper_id": "ZELTER",
            "title": (
                "Neutrophils impose strong immune pressure against PfEMP1 variants "
                "implicated in cerebral malaria"
            ),
            "rrf_score": 0.01,
        }
    ]
    chunks = [
        {
            "paper_id": "ZELTER",
            "text": (
                "PFD1235w is an ICAM-1-binding PfEMP1 associated with severe and "
                "cerebral malaria (Jensen et al, 2004; Joergensen et al, 2010; "
                "Bengtsson et al, 2013; Adams et al, 2021); using selected cells, "
                "we found significantly increased neutrophil killing of "
                "PFD1235w-expressing infected erythrocytes."
            ),
        }
    ]

    ranked = ask_corpus.rerank_direct_clinical_paper_matches(
        "Find associations between PfEMP1 expression and severe or cerebral malaria.",
        papers,
        chunks,
    )

    assert ranked[0]["question_directness_tier"] == 1
    assert ranked[0]["question_directness_label"] == "mechanistic_or_indirect"


def test_disease_context_guard_rejects_non_disease_marker_mentions() -> None:
    candidates = [
        {
            "paper_id": "DISEASE",
            "title": "Macrophage states in pulmonary fibrosis",
            "text": "SPP1-positive macrophages define a fibrotic population.",
        },
        {
            "paper_id": "MARKER_ONLY",
            "title": "Reference mapping of pancreatic cell types",
            "text": "SPP1 and KRT19 were used as ductal-cell markers.",
        },
    ]
    question = "Which papers discuss SPP1 signatures in desease?"
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == ["DISEASE"]


def test_explicit_severity_context_rejects_broad_spp1_covid_match() -> None:
    candidates = [
        {
            "paper_id": "BROAD",
            "title": "SPP1 macrophages in COVID-19",
            "text": "SPP1-positive macrophages were observed during COVID-19.",
        },
        {
            "paper_id": "SEVERE",
            "title": "SPP1 macrophages in severe COVID-19",
            "text": "SPP1-positive macrophages expanded in severe COVID-19.",
        },
    ]
    broad_question = "Which SPP1+ macrophages are reported in COVID-19?"
    severe_question = "Which SPP1+ macrophages are reported in severe COVID-19?"

    broad = ask_corpus.retain_entity_anchored_evidence(
        candidates,
        [
            *required_entity_anchor_groups(broad_question),
            *required_context_anchor_groups(broad_question),
        ],
    )
    severe = ask_corpus.retain_entity_anchored_evidence(
        candidates,
        [
            *required_entity_anchor_groups(severe_question),
            *required_context_anchor_groups(severe_question),
        ],
    )

    assert [candidate["paper_id"] for candidate in broad] == ["BROAD", "SEVERE"]
    assert [candidate["paper_id"] for candidate in severe] == ["SEVERE"]


def test_cerebral_or_severe_anchor_keeps_either_malaria_outcome_only() -> None:
    question = (
        "Which papers report var gene or PfEMP1 expression in cerebral or severe "
        "malaria?"
    )
    candidates = [
        {
            "paper_id": "CEREBRAL",
            "title": "var transcription in cerebral malaria",
            "text": "We measured var gene expression in cerebral-malaria isolates.",
        },
        {
            "paper_id": "SEVERE",
            "title": "PfEMP1 expression in severe malaria",
            "text": "PfEMP1 expression was associated with severe malaria.",
        },
        {
            "paper_id": "BROAD_MALARIA",
            "title": "PfEMP1 expression in malaria",
            "text": "PfEMP1 expression was measured during uncomplicated malaria.",
        },
        {
            "paper_id": "BRAIN_COVID",
            "title": "PfEMP1 and brain pathology in COVID-19",
            "text": "PfEMP1 was measured in severe COVID-19 brain tissue.",
        },
    ]
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == [
        "CEREBRAL",
        "SEVERE",
    ]


def test_cerebral_malaria_anchor_rejects_severe_non_cerebral_report() -> None:
    question = "Which papers report PfEMP1 expression in cerebral malaria?"
    candidates = [
        {
            "paper_id": "CEREBRAL",
            "title": "PfEMP1 expression in cerebral malaria",
            "text": "The cohort comprised children with cerebral malaria.",
        },
        {
            "paper_id": "SEVERE_ONLY",
            "title": "PfEMP1 expression in severe malaria",
            "text": "The cohort comprised children with severe malaria.",
        },
    ]
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == ["CEREBRAL"]


def test_var_only_direct_evidence_survives_disjunctive_pfemp1_filter() -> None:
    question = "Which papers report var gene or PfEMP1 expression in severe malaria?"
    candidates = [
        {
            "paper_id": "VAR_ONLY",
            "title": "var gene transcription in severe malaria",
            "text": "var genes were expressed in children with severe malaria.",
        },
        {
            "paper_id": "PFEMP1_ONLY",
            "title": "PfEMP1 in severe malaria",
            "text": "PfEMP1 expression was associated with severe malaria.",
        },
        {
            "paper_id": "NON_SEVERE",
            "title": "PfEMP1 in uncomplicated malaria",
            "text": "PfEMP1 expression was measured in uncomplicated malaria.",
        },
    ]
    groups = [
        *required_entity_anchor_groups(question),
        *required_context_anchor_groups(question),
    ]

    retained = ask_corpus.retain_entity_anchored_evidence(candidates, groups)

    assert [candidate["paper_id"] for candidate in retained] == [
        "VAR_ONLY",
        "PFEMP1_ONLY",
    ]


def test_paper_search_explanations_are_question_specific_and_keep_ranking() -> None:
    papers = [
        {"paper_id": "P1", "title": "Var transcription in cerebral malaria"},
        {"paper_id": "P2", "title": "EPCR-binding PfEMP1 in severe malaria"},
    ]
    chunks = [
        {
            "paper_id": "P1",
            "text": "The study compared var transcript profiles in cerebral malaria and hyperparasitaemia.",
        },
        {
            "paper_id": "P2",
            "text": "CIDRalpha1 domains were measured in children with severe and uncomplicated malaria.",
        },
    ]

    def generator(_prompt: str, **_kwargs):
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": "P1",
                            "reason": "It directly compares var transcription between cerebral-malaria and hyperparasitaemia presentations.",
                        },
                        {
                            "paper_id": "P2",
                            "reason": "It measures severe-malaria-associated CIDRalpha1 PfEMP1 domains against a clinical comparator group.",
                        },
                    ]
                }
            ),
            model="qwen-test",
            error=None,
        )

    enriched, meta = ask_corpus.explain_paper_search_matches(
        "Which var or PfEMP1 signals are linked to severe-malaria syndromes?",
        papers,
        chunks,
        model="qwen-test",
        generator=generator,
    )

    assert [paper["paper_id"] for paper in enriched] == ["P1", "P2"]
    assert "directly compares var transcription" in enriched[0]["selection_reason"]
    assert enriched[0]["selection_reason_status"] == "qwen_grounded"
    assert meta == {
        "attempted": True,
        "used": True,
        "model": "qwen-test",
        "error": None,
        "prompt_eval_count": 0,
        "eval_count": 0,
        "load_duration": 0,
        "total_duration": 0,
    }


def test_explicit_per_paper_fields_are_extracted_from_supplied_context() -> None:
    question = (
        "For each paper, report the clinical phenotype, parasite feature, direction "
        "of association, cohort, assay, and whether parasite developmental stage "
        "was adjusted for."
    )
    requested_fields = ask_corpus.requested_paper_detail_fields(question)
    assert requested_fields == ask_corpus.PAPER_SEARCH_DETAIL_FIELDS

    papers = [{"paper_id": "P1", "title": "Var expression and severe malaria"}]
    chunks = [
        {
            "paper_id": "P1",
            "section": "results",
            "page_start": 7,
            "text": (
                "Group A var transcripts were higher in cerebral malaria than in "
                "uncomplicated malaria."
            ),
        }
    ]
    document_context = {
        "P1": [
            {
                "paper_id": "P1",
                "chunk_index": 2,
                "section": "methods",
                "page_start": 3,
                "text": (
                    "The cohort comprised 40 children in Ghana. Var transcripts were "
                    "measured with quantitative RT-PCR."
                ),
            },
            {
                "paper_id": "P1",
                "chunk_index": 6,
                "section": "results",
                "page_start": 7,
                "text": (
                    "Group A var transcripts were higher in cerebral malaria than in "
                    "uncomplicated malaria."
                ),
            },
        ]
    }

    def generator(prompt: str, **kwargs):
        required = kwargs["format"]["properties"]["reasons"]["items"]["required"]
        assert required == ["paper_id", "reason", *requested_fields]
        assert "Never write \"not adjusted\"" in prompt
        assert "[section=methods, page=3]" in prompt
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": "P1",
                            "reason": (
                                "It compares var expression between cerebral and "
                                "uncomplicated malaria."
                            ),
                            "clinical_phenotype": (
                                "cerebral malaria versus uncomplicated malaria"
                            ),
                            "parasite_feature": "group A var transcript abundance",
                            "direction_of_association": (
                                "higher in cerebral malaria"
                            ),
                            "cohort": "40 children in Ghana",
                            "assay": "quantitative RT-PCR",
                            "developmental_stage_adjustment": (
                                ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
                            ),
                        }
                    ]
                }
            ),
            model="qwen-test",
            error=None,
        )

    enriched, meta = ask_corpus.explain_paper_search_matches(
        question,
        papers,
        chunks,
        model="qwen-test",
        document_context=document_context,
        generator=generator,
    )

    assert meta["used"] is True
    assert enriched[0]["question_fields"] == {
        "clinical_phenotype": "cerebral malaria versus uncomplicated malaria",
        "parasite_feature": "group A var transcript abundance",
        "direction_of_association": "higher in cerebral malaria",
        "cohort": "40 children in Ghana",
        "assay": "quantitative RT-PCR",
        "developmental_stage_adjustment": (
            ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
        ),
    }
    assert enriched[0]["question_fields_status"] == (
        ask_corpus.PAPER_DETAIL_MODEL_STATUS
    )


def test_per_paper_fields_are_not_inferred_from_an_ordinary_topic_query() -> None:
    assert ask_corpus.requested_paper_detail_fields(
        "Find cohort studies of var expression and developmental-stage adjustment."
    ) == ()


def test_explicit_fields_are_extracted_for_more_than_twelve_papers_in_batches() -> None:
    question = (
        "For each paper, report the clinical phenotype, parasite feature, direction "
        "of association, cohort, assay, and whether parasite developmental stage "
        "was adjusted for."
    )
    papers = [
        {"paper_id": f"P{index:02d}", "title": f"Clinical study {index}", "rank": index}
        for index in range(1, 26)
    ]
    chunks = [
        {
            "paper_id": paper["paper_id"],
            "section": "results",
            "text": "Var transcription was compared across clinical groups.",
        }
        for paper in papers
    ]
    batch_sizes = []

    def generator(_prompt: str, **kwargs):
        paper_ids = kwargs["format"]["properties"]["reasons"]["items"][
            "properties"
        ]["paper_id"]["enum"]
        batch_sizes.append(len(paper_ids))
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": paper_id,
                            "reason": (
                                "It compares parasite transcription across clinical groups."
                            ),
                            **{
                                field: ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
                                for field in ask_corpus.PAPER_SEARCH_DETAIL_FIELDS
                            },
                        }
                        for paper_id in paper_ids
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={
                "prompt_eval_count": len(paper_ids),
                "eval_count": 2 * len(paper_ids),
                "load_duration": 100,
                "total_duration": 200,
            },
        )

    enriched, meta = ask_corpus.explain_paper_search_matches_in_batches(
        question,
        papers,
        chunks,
        model="qwen-test",
        requested_fields=ask_corpus.PAPER_SEARCH_DETAIL_FIELDS,
        max_model_calls=8,
        generator=generator,
    )

    assert batch_sizes == [4, 4, 4, 4, 4, 4, 1]
    assert [paper["paper_id"] for paper in enriched] == [
        paper["paper_id"] for paper in papers
    ]
    assert all(paper.get("question_fields") for paper in enriched)
    assert meta == {
        "attempted": True,
        "used": True,
        "model": "qwen-test",
        "error": None,
        "prompt_eval_count": 25,
        "eval_count": 50,
        "load_duration": 700,
        "total_duration": 1400,
        "batch_count": 7,
        "used_batch_count": 7,
        "complete_batch_count": 7,
        "complete": True,
        "initial_batch_count": 7,
        "retry_call_count": 0,
        "model_extracted_record_count": 25,
        "deterministic_detail_record_count": 0,
        "fallback_detail_record_count": 0,
        "detail_complete_record_count": 25,
        "detail_complete": True,
        "model_call_count": 7,
        "model_call_limit": 8,
        "model_time_budget_seconds": 360,
        "model_budget_exhausted": False,
        "attempt_errors": [],
    }


def test_truncated_detail_batch_retries_only_missing_papers_without_reranking() -> None:
    question = "For each paper, report the cohort and assay."
    fields = ("cohort", "assay")
    papers = [
        {"paper_id": f"P{index}", "title": f"Study {index}", "rank": index}
        for index in range(1, 7)
    ]
    calls: list[list[str]] = []

    def generator(_prompt: str, **kwargs):
        paper_ids = kwargs["format"]["properties"]["reasons"]["items"][
            "properties"
        ]["paper_id"]["enum"]
        calls.append(paper_ids)
        assert kwargs["num_predict"] == min(
            2400, max(700, len(paper_ids) * 500)
        )
        returned_ids = paper_ids[:2] if len(calls) == 1 else paper_ids
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": paper_id,
                            "reason": "It reports a clinical cohort and the assay used.",
                            "cohort": f"cohort for {paper_id}",
                            "assay": f"assay for {paper_id}",
                        }
                        for paper_id in returned_ids
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={
                "prompt_eval_count": len(paper_ids),
                "eval_count": 10 * len(paper_ids),
                "load_duration": 100,
                "total_duration": 200,
            },
        )

    enriched, meta = ask_corpus.explain_paper_search_matches_in_batches(
        question,
        papers,
        [],
        model="qwen-test",
        requested_fields=fields,
        batch_size=4,
        retry_batch_size=2,
        generator=generator,
    )

    assert calls == [["P1", "P2", "P3", "P4"], ["P5", "P6"], ["P3", "P4"]]
    assert [paper["paper_id"] for paper in enriched] == [
        paper["paper_id"] for paper in papers
    ]
    assert [paper["rank"] for paper in enriched] == list(range(1, 7))
    assert all(
        paper["question_fields_status"] == ask_corpus.PAPER_DETAIL_MODEL_STATUS
        for paper in enriched
    )
    assert all(
        set(paper["question_fields"]) == set(fields) for paper in enriched
    )
    assert meta["prompt_eval_count"] == 8
    assert meta["eval_count"] == 80
    assert meta["total_duration"] == 600
    assert meta["initial_batch_count"] == 2
    assert meta["retry_call_count"] == 1
    assert meta["model_extracted_record_count"] == 6
    assert meta["detail_complete_record_count"] == 6
    assert meta["fallback_detail_record_count"] == 0
    assert meta["complete"] is True
    assert meta["detail_complete"] is True
    assert meta["error"] is None
    assert meta["attempt_errors"] == [
        "initial_1: incomplete_match_explanations: 2/4 records"
    ]


def test_unrecovered_detail_records_receive_complete_explicit_fallback() -> None:
    question = "For each paper, report the cohort and assay."
    fields = ("cohort", "assay")
    papers = [
        {"paper_id": f"P{index}", "title": f"Study {index}", "rank": index}
        for index in range(1, 5)
    ]
    calls: list[list[str]] = []

    def generator(_prompt: str, **kwargs):
        paper_ids = kwargs["format"]["properties"]["reasons"]["items"][
            "properties"
        ]["paper_id"]["enum"]
        calls.append(paper_ids)
        paper_id = paper_ids[0]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": paper_id,
                            "reason": "It reports a clinical cohort and the assay used.",
                            "cohort": f"cohort for {paper_id}",
                            "assay": f"assay for {paper_id}",
                        }
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={
                "prompt_eval_count": len(paper_ids),
                "eval_count": 10 * len(paper_ids),
                "load_duration": 100,
                "total_duration": 200,
            },
        )

    enriched, meta = ask_corpus.explain_paper_search_matches_in_batches(
        question,
        papers,
        [],
        model="qwen-test",
        requested_fields=fields,
        batch_size=4,
        retry_batch_size=2,
        max_retry_calls=1,
        generator=generator,
    )

    assert calls == [["P1", "P2", "P3", "P4"], ["P2", "P3"]]
    assert [paper["paper_id"] for paper in enriched] == ["P1", "P2", "P3", "P4"]
    assert [paper["rank"] for paper in enriched] == [1, 2, 3, 4]
    assert [paper["question_fields_status"] for paper in enriched] == [
        ask_corpus.PAPER_DETAIL_MODEL_STATUS,
        ask_corpus.PAPER_DETAIL_MODEL_STATUS,
        "not_reported_fallback",
        "not_reported_fallback",
    ]
    for paper in enriched[2:]:
        assert paper["question_fields"] == {
            field: ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED for field in fields
        }
        assert paper["question_field_statuses"] == {
            field: "not_reported_fallback" for field in fields
        }
    assert meta["prompt_eval_count"] == 6
    assert meta["eval_count"] == 60
    assert meta["total_duration"] == 400
    assert meta["retry_call_count"] == 1
    assert meta["model_extracted_record_count"] == 2
    assert meta["fallback_detail_record_count"] == 2
    assert meta["detail_complete_record_count"] == 4
    assert meta["complete"] is False
    assert meta["detail_complete"] is True
    assert meta["model_budget_exhausted"] is True
    assert "2/4 records" in str(meta["error"])


def test_detail_extraction_has_a_global_call_and_token_budget() -> None:
    question = "For each paper, report the cohort and assay."
    fields = ("cohort", "assay")
    papers = [
        {"paper_id": f"P{index:02d}", "title": f"Study {index}", "rank": index}
        for index in range(1, 25)
    ]
    calls: list[tuple[list[str], int]] = []

    def generator(_prompt: str, **kwargs):
        paper_ids = kwargs["format"]["properties"]["reasons"]["items"][
            "properties"
        ]["paper_id"]["enum"]
        calls.append((paper_ids, kwargs["num_predict"]))
        return SimpleNamespace(
            text=json.dumps({"reasons": []}),
            model="qwen-test",
            error=None,
            metadata={},
        )

    enriched, meta = ask_corpus.explain_paper_search_matches_in_batches(
        question,
        papers,
        [],
        model="qwen-test",
        requested_fields=fields,
        generator=generator,
    )

    assert len(calls) == ask_corpus.PAPER_DETAIL_MAX_MODEL_CALLS == 8
    assert sum(token_budget for _, token_budget in calls) <= 14_000
    assert meta["model_call_count"] == 8
    assert meta["model_call_limit"] == 8
    assert meta["model_budget_exhausted"] is True
    assert meta["retry_call_count"] == 2
    assert meta["complete"] is False
    assert meta["detail_complete"] is True
    assert len(enriched) == 24
    assert all(
        paper["question_fields_status"] == ask_corpus.PAPER_DETAIL_FALLBACK_STATUS
        for paper in enriched
    )
    assert all(
        set(paper["question_fields"]) == set(fields) for paper in enriched
    )


def test_twenty_four_detail_records_retain_two_retry_calls() -> None:
    fields = ("cohort", "assay")
    papers = [
        {"paper_id": f"P{index:02d}", "title": f"Study {index}", "rank": index}
        for index in range(1, 25)
    ]
    calls: list[list[str]] = []

    def generator(_prompt: str, **kwargs):
        paper_ids = kwargs["format"]["properties"]["reasons"]["items"][
            "properties"
        ]["paper_id"]["enum"]
        calls.append(paper_ids)
        if len(calls) <= 4:
            returned_ids = paper_ids[:-1]
        else:
            returned_ids = paper_ids
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": paper_id,
                            "reason": (
                                "This paper directly reports the requested "
                                "cohort and assay."
                            ),
                            "cohort": f"cohort {paper_id}",
                            "assay": f"assay {paper_id}",
                        }
                        for paper_id in returned_ids
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={},
        )

    enriched, meta = ask_corpus.explain_paper_search_matches_in_batches(
        "For each paper, report the cohort and assay.",
        papers,
        [],
        model="qwen-test",
        requested_fields=fields,
        generator=generator,
    )

    assert len(calls) == 8
    assert [len(call) for call in calls] == [4, 4, 4, 4, 4, 4, 2, 2]
    assert meta["initial_batch_count"] == 6
    assert meta["retry_call_count"] == 2
    assert meta["model_call_count"] == 8
    assert meta["model_budget_exhausted"] is False
    assert meta["complete"] is True
    assert all(
        paper["question_fields_status"] == ask_corpus.PAPER_DETAIL_MODEL_STATUS
        for paper in enriched
    )


def test_model_detail_strings_are_never_presented_as_verified_grounding() -> None:
    question = "For each paper, report the cohort and assay."
    fields = ("cohort", "assay")
    papers = [{"paper_id": "P1", "title": "Expression study", "rank": 1}]
    chunks = [
        {
            "paper_id": "P1",
            "section": "abstract",
            "text": "Parasite transcripts differed between clinical groups.",
        }
    ]

    def generator(_prompt: str, **_kwargs):
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": "P1",
                            "reason": "It compares clinical groups.",

                            "cohort": "400 children in Ghana",
                            "assay": "single-cell RNA sequencing",
                        }
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={},
        )

    enriched, _meta = ask_corpus.explain_paper_search_matches(
        question,
        papers,
        chunks,
        model="qwen-test",
        requested_fields=fields,
        generator=generator,
    )

    assert enriched[0]["question_fields"] == {
        "cohort": ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED,
        "assay": "single-cell RNA sequencing",
    }
    assert enriched[0]["question_field_statuses"] == {
        "cohort": ask_corpus.PAPER_DETAIL_FALLBACK_STATUS,
        "assay": ask_corpus.PAPER_DETAIL_MODEL_STATUS,
    }
    assert enriched[0]["question_fields_status"] == "mixed_extraction"
    assert "grounded" not in enriched[0]["question_fields_status"]


def test_structured_batch_rejects_cross_paper_cohort_and_outcome_details() -> None:
    fields = (
        "clinical_phenotype",
        "direction_of_association",
        "cohort",
    )
    papers = [
        {"paper_id": "P_KENYA", "title": "Var expression across severe syndromes"},
        {"paper_id": "P_UGANDA", "title": "PfEMP1 in cerebral malaria"},
    ]
    document_context = {
        "P_KENYA": [
            {
                "paper_id": "P_KENYA",
                "section": "results",
                "text": (
                    "We studied 712 Kenyan children. Var expression was associated "
                    "with impaired consciousness and severe malarial anaemia. Previous "
                    "work reported cerebral malaria in another cohort."
                ),
            }
        ],
        "P_UGANDA": [
            {
                "paper_id": "P_UGANDA",
                "section": "abstract",
                "text": (
                    "We studied Ugandan children. EPCR-binding PfEMP1 transcripts were "
                    "higher in retinopathy-negative cerebral malaria."
                ),
            }
        ],
    }

    def generator(_prompt: str, **_kwargs):
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": "P_KENYA",
                            "reason": (
                                "This paper reports higher var expression in cerebral malaria."
                            ),
                            "clinical_phenotype": "cerebral malaria",
                            "direction_of_association": "higher in cerebral malaria",
                            "cohort": "children in Benin",
                        },
                        {
                            "paper_id": "P_UGANDA",
                            "reason": (
                                "This paper reports higher EPCR-binding PfEMP1 in cerebral malaria."
                            ),
                            "clinical_phenotype": "retinopathy-negative cerebral malaria",
                            "direction_of_association": (
                                "higher in retinopathy-negative cerebral malaria"
                            ),
                            "cohort": "children in Kenya",
                        },
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={},
        )

    enriched, _meta = ask_corpus.explain_paper_search_matches(
        "For each paper, report clinical phenotype, direction, and cohort.",
        papers,
        [],
        model="qwen-test",
        document_context=document_context,
        requested_fields=fields,
        generator=generator,
    )

    kenya, uganda = enriched
    assert kenya["question_fields"] == {
        field: ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED for field in fields
    }
    assert uganda["question_fields"]["clinical_phenotype"] == (
        "retinopathy-negative cerebral malaria"
    )
    assert uganda["question_fields"]["direction_of_association"] == (
        "higher in retinopathy-negative cerebral malaria"
    )
    assert uganda["question_fields"]["cohort"] == (
        ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
    )
    assert all(
        paper["selection_reason_status"] == "deterministic_fallback"
        for paper in enriched
    )


def test_stage_detail_fallback_distinguishes_adjustment_from_age_estimation() -> None:
    adjusted = ask_corpus.infer_developmental_stage_detail(
        [
            "Adjustment for heterogeneity in the mixture of leukocytes and parasite "
            "developmental stages improved segregation. Parasite stage was incorporated "
            "as a covariate in the generalized linear model."
        ]
    )
    modelled = ask_corpus.infer_developmental_stage_detail(
        [
            "We used the full transcriptome to determine expected stage compositions "
            "and estimate average parasite age in hours post invasion."
        ]
    )

    assert adjusted == (
        "yes—parasite developmental stage was adjusted or included as a covariate"
    )
    assert modelled == (
        "stage composition or parasite age was estimated; "
        "no covariate adjustment stated in supplied evidence"
    )


def test_stage_detail_rejects_unrelated_adjustment_in_same_sentence() -> None:
    excerpt = (
        "Models were adjusted for age and parasitaemia, while parasite "
        "developmental stage differed between clinical groups."
    )

    assert not ask_corpus.stage_adjustment_statement_supports(
        excerpt,
        negative=False,
    )
    assert ask_corpus.infer_developmental_stage_detail([excerpt]) is None

    guarded = ask_corpus.guard_developmental_stage_detail(
        {"developmental_stage_adjustment": "adjusted for parasite stage"},
        [excerpt],
    )
    assert guarded["developmental_stage_adjustment"] == (
        ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
    )


def test_stage_detail_rejects_adjustment_attributed_to_previous_studies() -> None:
    excerpt = (
        "Previous studies adjusted gene expression for parasite developmental "
        "stage."
    )

    assert not ask_corpus.stage_adjustment_statement_supports(
        excerpt,
        negative=False,
    )
    assert ask_corpus.infer_developmental_stage_detail([excerpt]) is None


def test_stage_estimation_without_adjustment_remains_separate() -> None:
    inferred = ask_corpus.infer_developmental_stage_detail(
        [
            "Parasite developmental stage was estimated from the transcriptome, "
            "but the association model was adjusted only for age and parasitaemia."
        ]
    )

    assert inferred == (
        "stage composition or parasite age was estimated; "
        "no covariate adjustment stated in supplied evidence"
    )


def test_explicit_stage_adjustment_recovers_with_deterministic_provenance() -> None:
    fields = ("developmental_stage_adjustment",)

    def generator(_prompt: str, **_kwargs):
        return SimpleNamespace(
            text=json.dumps(
                {
                    "reasons": [
                        {
                            "paper_id": "P1",
                            "reason": "The paper analyzes stage-aware parasite expression.",
                            "developmental_stage_adjustment": (
                                ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
                            ),
                        }
                    ]
                }
            ),
            model="qwen-test",
            error=None,
            metadata={},
        )

    enriched, _meta = ask_corpus.explain_paper_search_matches(
        "For each paper, report whether parasite stage was adjusted for.",
        [{"paper_id": "P1", "title": "Stage-aware expression"}],
        [
            {
                "paper_id": "P1",
                "section": "methods",
                "text": (
                    "We estimated the proportions of parasite developmental stages. "
                    "To remove confounding from parasite mixtures, we adjusted gene "
                    "expression values for those proportions."
                ),
            }
        ],
        model="qwen-test",
        requested_fields=fields,
        generator=generator,
    )

    assert enriched[0]["question_fields"]["developmental_stage_adjustment"] == (
        "yes—parasite developmental stage was adjusted or included as a covariate"
    )
    assert enriched[0]["question_field_statuses"] == {
        "developmental_stage_adjustment": (
            ask_corpus.PAPER_DETAIL_DETERMINISTIC_STATUS
        )
    }
    assert enriched[0]["question_fields_status"] == (
        ask_corpus.PAPER_DETAIL_DETERMINISTIC_STATUS
    )


def test_model_reason_drops_speculative_function_tail() -> None:
    assert ask_corpus.sanitize_model_selection_reason(
        "Antibodies correlated with malaria exposure, suggesting a role in immune evasion."
    ) == "Antibodies correlated with malaria exposure"
    assert ask_corpus.sanitize_model_selection_reason(
        "STEVOR was detected in merozoites, indicating a role in host-cell remodelling."
    ) == "STEVOR was detected in merozoites"
    assert ask_corpus.sanitize_model_selection_reason(
        "The study maps RIFIN membrane topology, providing insights into host-cell remodelling."
    ) == "The study maps RIFIN membrane topology"
    assert ask_corpus.sanitize_model_selection_reason(
        "RIFIN displayed on infected erythrocytes bound KIR2DL1, supporting a role in immune evasion."
    ) == "RIFIN displayed on infected erythrocytes bound KIR2DL1"
    assert ask_corpus.sanitize_model_selection_reason(
        "STEVOR was exposed on merozoites, potentially contributing to erythrocyte invasion."
    ) == "STEVOR was exposed on merozoites"


def test_not_adjusted_is_removed_without_explicit_support_in_supplied_excerpt() -> None:
    guarded = ask_corpus.guard_developmental_stage_detail(
        {"developmental_stage_adjustment": "parasite stage was not adjusted for"},
        [
            "The study measured var transcripts in children with severe and "
            "uncomplicated malaria."
        ],
    )

    assert guarded["developmental_stage_adjustment"] == (
        ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
    )


def test_stage_guard_does_not_combine_unrelated_excerpts_or_covariates() -> None:
    guarded = ask_corpus.guard_developmental_stage_detail(
        {"developmental_stage_adjustment": "adjusted for parasite stage"},
        [
            "Parasite developmental stage varied among samples.",
            "Regression models were adjusted for patient age and sex.",
        ],
    )
    same_sentence_guarded = ask_corpus.guard_developmental_stage_detail(
        {"developmental_stage_adjustment": "parasite stage was not adjusted for"},
        [
            "Parasite developmental stage was measured, while models were not "
            "adjusted for patient sex."
        ],
    )

    assert guarded["developmental_stage_adjustment"] == (
        ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
    )
    assert same_sentence_guarded["developmental_stage_adjustment"] == (
        ask_corpus.PAPER_SEARCH_DETAIL_NOT_REPORTED
    )


def test_explicit_stage_adjustment_is_retained() -> None:
    guarded = ask_corpus.guard_developmental_stage_detail(
        {"developmental_stage_adjustment": "adjusted for parasite stage"},
        ["Regression models were adjusted for parasite developmental stage."],
    )

    assert guarded["developmental_stage_adjustment"] == (
        "adjusted for parasite stage"
    )


def test_explicitly_reported_no_stage_adjustment_is_retained() -> None:
    guarded = ask_corpus.guard_developmental_stage_detail(
        {"developmental_stage_adjustment": "parasite stage was not adjusted for"},
        ["The analysis did not adjust for parasite developmental stage."],
    )

    assert guarded["developmental_stage_adjustment"] == (
        "parasite stage was not adjusted for"
    )


def test_legacy_reason_only_json_still_parses_for_a_structured_request() -> None:
    records = ask_corpus.parse_paper_reason_records(
        json.dumps(
            {
                "reasons": [
                    {
                        "paper_id": "P1",
                        "reason": "This paper compares var expression across clinical groups.",
                    }
                ]
            }
        ),
        {"P1"},
        requested_fields=ask_corpus.PAPER_SEARCH_DETAIL_FIELDS,
    )

    assert records == {
        "P1": {"reason": "This paper compares var expression across clinical groups."}
    }


def test_structured_paper_context_is_bounded_and_includes_methods_and_results() -> None:
    filler = " additional bounded context" * 200
    candidates = ask_corpus.paper_reason_candidates(
        [{"paper_id": "P1", "title": "A clinical transcript study"}],
        [
            {
                "paper_id": "P1",
                "section": "results",
                "page_start": 8,
                "text": "Expression was higher in severe malaria." + filler,
            }
        ],
        document_context={
            "P1": [
                {
                    "paper_id": "P1",
                    "chunk_index": 2,
                    "section": "methods",
                    "page_start": 3,
                    "text": "Children were enrolled and RNA was measured by qPCR." + filler,
                },
                {
                    "paper_id": "P1",
                    "chunk_index": 8,
                    "section": "results",
                    "page_start": 8,
                    "text": "Expression was higher in severe malaria." + filler,
                },
            ]
        },
        requested_fields=("cohort", "assay", "direction_of_association"),
    )

    excerpts = candidates[0]["retrieved_excerpts"]
    assert any("section=methods" in excerpt for excerpt in excerpts)
    assert any("section=results" in excerpt for excerpt in excerpts)
    assert sum(len(excerpt) for excerpt in excerpts) <= 3000


def test_paper_search_explanation_failure_is_explicitly_conservative() -> None:
    papers = [{"paper_id": "P1", "title": "A possible clinical malaria paper"}]

    def generator(_prompt: str, **_kwargs):
        return SimpleNamespace(text='{"unexpected": []}', model="qwen-test", error=None)

    enriched, meta = ask_corpus.explain_paper_search_matches(
        "What links parasite genes to cerebral malaria?",
        papers,
        [],
        model="qwen-test",
        generator=generator,
    )

    assert enriched[0]["selection_reason_status"] == "deterministic_fallback"
    assert "did not support a more specific" in enriched[0]["selection_reason"]
    assert meta["used"] is False
    assert str(meta["error"]).startswith("invalid_match_explanations:")


def test_exact_doi_fallback_reason_is_specific_without_a_model_call() -> None:
    enriched = ask_corpus.add_fallback_selection_reasons(
        [
            {
                "paper_id": "P1",
                "title": "A requested paper",
                "rationale": "exact DOI match; title terms: requested",
            }
        ]
    )

    assert enriched[0]["selection_reason"] == (
        "Selected because its DOI exactly matches the requested identifier."
    )


def test_named_report_anchors_prepend_front_summaries_and_disambiguate_preprint(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tauthor\tjournal\tpaper_type\tfulltext_status\n"
        "LAWTON\tDiamonds in the rif: Alignment-free comparative genomics\t2025\t"
        "10.1000/lawton\tLawton\tInfection, Genetics and Evolution\tarticle\tchunked\n"
        "BILLOWS_JOURNAL\tGlobal-scale population genetic analysis\t2026\t"
        "10.1000/billows-journal\tBillows\tNature Communications\tarticle\tchunked\n"
        "BILLOWS_PFPAN\tA Plasmodium falciparum Pangenome Resource\t2026\t"
        "10.1000/billows-pfpan\tBillows\tbioRxiv\tpreprint\tchunked\n",
        encoding="utf-8",
    )
    chunk_rows = {
        "LAWTON": [
            {
                "chunk_id": "LAWTON_C1",
                "chunk_index": 1,
                "paper_id": "LAWTON",
                "text": "Diamonds in the rif: Alignment-free comparative genomics. "
                "Abstract: We analysed 18 genome assemblies.",
            },
            {
                "chunk_id": "LAWTON_C2",
                "chunk_index": 2,
                "paper_id": "LAWTON",
                "section": "discussion",
                "text": "Later discussion.",
            },
        ],
        "BILLOWS_JOURNAL": [
            {
                "chunk_id": "BILLOWS_JOURNAL_C1",
                "chunk_index": 1,
                "paper_id": "BILLOWS_JOURNAL",
                "text": "Global-scale population genetic analysis. Abstract.",
            }
        ],
        "BILLOWS_PFPAN": [
            {
                "chunk_id": "BILLOWS_PFPAN_C1",
                "chunk_index": 1,
                "paper_id": "BILLOWS_PFPAN",
                "quality_flags": ["table_like"],
                "text": "A Plasmodium falciparum Pangenome Resource. Abstract: "
                "Here we present PfPan from 13 reference genomes.",
            },
            {
                "chunk_id": "BILLOWS_PFPAN_C2",
                "chunk_index": 2,
                "paper_id": "BILLOWS_PFPAN",
                "section": "results",
                "text": "PfPan adds 4.7 Mb.",
            },
        ],
    }
    for paper_id, rows in chunk_rows.items():
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    merged = ask_corpus.merge_explicit_named_study_anchors(
        "Compare Lawton et al., 'Diamonds in the rif,' with the Billows et al. "
        "PfPan preprint.",
        [
            {"paper_id": "DISTRACTOR", "chunk_id": "D_C1", "text": "Other."},
            {
                "paper_id": "LAWTON",
                "chunk_id": "LAWTON_C1",
                "text": "Truncated title-only retrieval window.",
                "score": 20.0,
            },
            chunk_rows["BILLOWS_PFPAN"][1],
        ],
        chunks_dir=chunks_dir,
        papers_tsv=papers,
    )

    assert [row["paper_id"] for row in merged[:2]] == [
        "LAWTON",
        "BILLOWS_PFPAN",
    ]
    assert [row["chunk_id"] for row in merged[:2]] == [
        "LAWTON_C1",
        "BILLOWS_PFPAN_C1",
    ]
    assert all(row["explicit_named_study_anchor"] for row in merged[:2])
    assert all(row["section"] == "abstract" for row in merged[:2])
    assert "We analysed 18 genome assemblies" in merged[0]["text"]
    assert "table_like" not in merged[1]["quality_flags"]
    assert "BILLOWS_PFPAN_C2" in {row["chunk_id"] for row in merged}
    assert "BILLOWS_JOURNAL" not in {
        row["paper_id"]
        for row in merged
        if row.get("explicit_named_study_anchor")
    }


def test_single_named_study_rejects_mismatched_quoted_title() -> None:
    metadata = {
        "SMITH_MALARIA": {
            "author": "Smith",
            "year": "2024",
            "title": "Parasite expression in severe malaria",
            "journal": "Malaria Journal",
            "paper_type": "article",
        }
    }

    mismatched = ask_corpus.resolve_named_study_reference(
        NamedStudyReference(
            surname="Smith",
            year="2024",
            context=(
                "Smith et al. (2024), "
                "'Zebrafish fin regeneration after injury'"
            ),
        ),
        metadata,
    )
    bare = ask_corpus.resolve_named_study_reference(
        NamedStudyReference(
            surname="Smith",
            year="2024",
            context="Smith et al. (2024)",
        ),
        metadata,
    )

    assert mismatched is None
    assert bare is not None
    assert bare[0] == "SMITH_MALARIA"

    result_quote = ask_corpus.resolve_named_study_reference(
        NamedStudyReference(
            surname="Smith",
            year="2024",
            context=(
                "Smith et al. (2024), did they report "
                "'higher receptor activation after exposure'?"
            ),
        ),
        metadata,
    )
    assert result_quote is not None
    assert result_quote[0] == "SMITH_MALARIA"


def test_named_report_summary_keeps_own_result_and_stops_before_background() -> None:
    excerpt = ask_corpus.named_study_summary_excerpt(
        "Title and authors. Here we identified KIR2DL1 binding. This interaction "
        "suppresses NK-cell activation. KIR2DS1 binding activates NK cells. "
        "This study demonstrates an activating receptor response. The introduction "
        "then discusses background LILRB1 and LAIR1 reports."
    )

    assert "KIR2DL1" in excerpt
    assert "KIR2DS1" in excerpt
    assert "activates NK cells" in excerpt
    assert "LILRB1" not in excerpt
    assert "LAIR1" not in excerpt


def test_named_report_resolver_injects_one_missing_fulltext_support_chunk(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\tauthor\tyear\ttitle\tjournal\tdoi\n"
        "SHAW\tShaw\t2022\tTranscriptomic complexity revealed by long-read "
        "sequencing\tPLOS ONE\t10.1371/journal.pone.0276956\n",
        encoding="utf-8",
    )
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    rows = [
        {
            "paper_id": "SHAW",
            "chunk_id": "SHAW_C1",
            "chunk_index": 1,
            "text": (
                "Transcriptomic complexity revealed by long-read sequencing. "
                "Abstract: Here we used PacBio and Nanopore and retained "
                "12,495 novel isoforms. Retained introns were common events."
            ),
        },
        {
            "paper_id": "SHAW",
            "chunk_id": "SHAW_C4",
            "chunk_index": 4,
            "text": (
                "In this study, long-read RNA-seq data were generated and made "
                "public under accession GSE186109. Total and nuclear-enriched "
                "RNA were converted to cDNA and sequenced on the two long-read "
                "platforms. The resulting catalogue was deposited so that the "
                "underlying reads and transcript annotations could be reused by "
                "other researchers investigating parasite isoform diversity. "
                "This public record links the experimental libraries to the "
                "reported catalogue and preserves the sample-level sequencing "
                "data needed for an independent computational reanalysis. The "
                "record also documents the total-RNA and nuclear-RNA libraries, "
                "the preparation of capped messenger RNA, and the catalogue "
                "construction steps used to combine non-redundant transcripts "
                "across samples and sequencing technologies."
            ),
        },
        {
            "paper_id": "SHAW",
            "chunk_id": "SHAW_C5",
            "chunk_index": 5,
            "text": "Further results described the same novel isoforms.",
        },
    ]
    (chunks / "SHAW.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    question = (
        "According to Shaw et al. (2022), which sequencing platforms and public "
        "accession were used, how many novel isoforms were retained, and which "
        "isoform events were most common?"
    )

    merged = ask_corpus.merge_explicit_named_study_anchors(
        question,
        [{"paper_id": "SHAW", "chunk_id": "SHAW_C1", "text": "title only"}],
        chunks_dir=chunks,
        papers_tsv=papers,
    )

    assert [row["chunk_id"] for row in merged] == ["SHAW_C1", "SHAW_C4"]
    assert merged[1]["explicit_named_study_support"] is True
    assert merged[1]["named_study_source_text_scope"] == (
        "fulltext_question_support"
    )
    assert "GSE186109" in merged[1]["text"]
    retained = ask_corpus.retain_entity_anchored_evidence(
        merged,
        [("disease", "infection")],
        question=question,
        max_chars_per_chunk=950,
    )
    assert [row["chunk_id"] for row in retained] == ["SHAW_C1", "SHAW_C4"]

    already_present = ask_corpus.merge_explicit_named_study_anchors(
        question,
        [rows[0], rows[1]],
        chunks_dir=chunks,
        papers_tsv=papers,
    )
    assert [row["chunk_id"] for row in already_present].count("SHAW_C4") == 1


def test_named_report_identity_anchor_is_not_replaced_by_background_chunk() -> None:
    anchor = {
        "paper_id": "TARGET",
        "chunk_id": "TARGET_C1",
        "explicit_named_study_anchor": True,
        "text": "The requested report directly states the result.",
    }
    background = {
        "paper_id": "TARGET",
        "chunk_id": "TARGET_C2",
        "required_entity_anchors": [["LILRB1"], ["LAIR1"]],
        "text": "Prior work discussed LILRB1 and LAIR1 in detail.",
    }

    retained = ask_corpus.retain_entity_anchored_evidence(
        [anchor, background],
        [("LILRB1",), ("LAIR1",)],
    )
    shortlisted = ask_corpus.shortlist_answer_candidates(
        retained,
        max_papers=1,
        question="Compare the requested report with LILRB1 and LAIR1 background.",
        max_chars_per_chunk=900,
    )

    assert [row["chunk_id"] for row in shortlisted] == ["TARGET_C1"]
    assert shortlisted[0]["named_study_identity_anchor_verified"] is True


def test_named_report_shortlist_reserves_one_question_focused_support_passage() -> None:
    anchor = {
        "paper_id": "CIUFFREDA",
        "chunk_id": "CIUFFREDA_C1",
        "explicit_named_study_anchor": True,
        "named_study_surname": "Ciuffreda",
        "named_study_year": "2020",
        "named_study_source_text_scope": "front_summary_excerpt",
        "section": "abstract",
        "text": (
            "We developed a three-gene method in patients with uncomplicated "
            "malaria attending two clinics in Ghana."
        ),
    }
    weak_support = {
        "paper_id": "CIUFFREDA",
        "chunk_id": "CIUFFREDA_C2",
        "section": "discussion",
        "score": 80.0,
        "text": "Parasite age was discussed in clinical samples.",
    }
    direct_support = {
        "paper_id": "CIUFFREDA",
        "chunk_id": "CIUFFREDA_C5",
        "section": "results",
        "score": 60.0,
        "text": (
            "A GLMM used three RT-qPCR Ct values as model input. Mixed-stage "
            "samples produced an intermediate parasite-age estimate."
        ),
    }
    distractor = {
        "paper_id": "OTHER",
        "chunk_id": "OTHER_C1",
        "section": "results",
        "score": 50.0,
        "text": "Other evidence.",
    }

    shortlisted = ask_corpus.shortlist_answer_candidates(
        [anchor, weak_support, direct_support, distractor],
        max_papers=3,
        question=(
            "Did Ciuffreda et al. reconstruct parasite-age distributions? State "
            "the model input and limitation for mixed-stage samples."
        ),
        max_chars_per_chunk=400,
    )

    assert [row["chunk_id"] for row in shortlisted] == [
        "CIUFFREDA_C1",
        "CIUFFREDA_C5",
        "OTHER_C1",
    ]
    assert shortlisted[1]["explicit_named_study_support"] is True
    assert shortlisted[1]["named_study_surname"] == "Ciuffreda"


def test_named_report_support_is_reserved_as_noncertifying_prompt_context() -> None:
    question = (
        "Did Ciuffreda et al. reconstruct parasite-age distributions? State the "
        "GLMM input and mixed-stage limitation."
    )
    anchor = {
        "paper_id": "CIUFFREDA",
        "chunk_id": "CIUFFREDA_C1",
        "explicit_named_study_anchor": True,
        "text": "Here we estimated parasite age from a three-gene assay.",
    }
    support = {
        "paper_id": "CIUFFREDA",
        "chunk_id": "CIUFFREDA_C5",
        "explicit_named_study_support": True,
        "score": 50.0,
        "text": (
            "A GLMM used RT-qPCR Ct values as input. Mixed-stage samples "
            "produced an intermediate parasite-age estimate."
        ),
    }
    unrelated = {
        "paper_id": "OTHER",
        "chunk_id": "OTHER_C1",
        "text": "Other evidence about parasite age.",
    }

    selector, context, reserved = ask_corpus.reserve_named_study_prompt_context(
        question,
        [anchor, support, unrelated],
        token_budget=2_000,
    )

    assert [row["chunk_id"] for row in selector] == [
        "CIUFFREDA_C1",
        "OTHER_C1",
    ]
    assert [row["chunk_id"] for row in context] == ["CIUFFREDA_C5"]
    assert context[0]["prompt_context_only"] is True
    assert context[0]["named_study_incremental_question_terms"] == [
        "glmm",
        "mixed",
        "stage",
    ]
    assert reserved > 0

    merged = ask_corpus.merge_selected_named_study_context(
        [anchor, unrelated], context
    )
    assert [row["chunk_id"] for row in merged] == [
        "CIUFFREDA_C1",
        "CIUFFREDA_C5",
        "OTHER_C1",
    ]


def test_named_report_support_is_not_added_without_incremental_question_evidence() -> None:
    anchor = {
        "paper_id": "LAWTON",
        "chunk_id": "LAWTON_C1",
        "explicit_named_study_anchor": True,
        "text": "We analysed 18 genomes using an alignment-free method.",
    }
    redundant_support = {
        "paper_id": "LAWTON",
        "chunk_id": "LAWTON_C2",
        "explicit_named_study_support": True,
        "text": "The study analysed genomes with an alignment-free method.",
    }

    selector, context, reserved = ask_corpus.reserve_named_study_prompt_context(
        "What genome input and method did Lawton et al. use?",
        [anchor, redundant_support],
        token_budget=2_000,
    )

    assert [row["chunk_id"] for row in selector] == ["LAWTON_C1"]
    assert context == []
    assert reserved == 0


def test_named_report_context_is_not_emitted_if_anchor_was_not_selected() -> None:
    support = {
        "paper_id": "TARGET",
        "chunk_id": "TARGET_C2",
        "prompt_context_only": True,
    }

    merged = ask_corpus.merge_selected_named_study_context(
        [{"paper_id": "OTHER", "chunk_id": "OTHER_C1"}],
        [support],
    )

    assert [row["chunk_id"] for row in merged] == ["OTHER_C1"]


def test_tab_separated_paper_reasons_survive_an_incomplete_final_line() -> None:
    reasons = ask_corpus.parse_paper_reason_output(
        "P1\tThis paper directly measures var transcripts in severe malaria.\n"
        "P2\tThis paper compares cerebral and uncomplicated malaria isolates.\n"
        "P3 incomplete",
        {"P1", "P2", "P3"},
    )

    assert set(reasons) == {"P1", "P2"}


def test_paper_reason_candidates_use_full_document_when_no_top_chunk_exists() -> None:
    candidates = ask_corpus.paper_reason_candidates(
        [{"paper_id": "P1", "title": "A clinical study"}],
        [],
        document_context={
            "P1": [
                {
                    "section": "abstract",
                    "text": "We measured var transcripts in cerebral and uncomplicated malaria.",
                }
            ]
        },
    )

    assert candidates[0]["retrieved_excerpts"] == [
        "We measured var transcripts in cerebral and uncomplicated malaria."
    ]


def test_explicit_primary_paper_request_is_detected() -> None:
    assert ask_corpus.requests_primary_research(
        "Find the strongest primary papers about severe malaria."
    )
    assert not ask_corpus.requests_primary_research(
        "Find papers and reviews about severe malaria."
    )
    assert ask_corpus.requests_primary_research(
        "Find and rank the strongest empirical papers about severe malaria."
    )
    assert ask_corpus.requests_primary_research(
        "Finde Primärpaper über var-Genexpression bei asymptomatischer Malaria."
    )
    assert ask_corpus.requests_primary_research(
        "Encuentra artículos primarios sobre PfEMP1 y malaria cerebral."
    )
    assert ask_corpus.requests_primary_research(
        "Trouve les études primaires sur PfEMP1 et le paludisme cérébral."
    )


def test_unknown_plus_symbol_paper_search_rejects_off_topic_results(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        "P1\tMacrophage responses in severe malaria\t2024\t10.1000/malaria\t\t\t"
        "malaria\tmacrophages\t\t\tarticle\tchunked\t\t\t\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "results",
                "text": "Macrophage activation was measured in severe malaria.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for index, symbol in enumerate(("ZZZ999+", "ZZZ999-positive"), start=1):
        args = make_args(
            question=f"list pappers w {symbol} makrophags in malaria, whatevr lol",
            chunks_dir=str(chunks_dir),
            papers=str(papers),
            backend="none",
        )
        args.task = "sources"

        result = ask_corpus.run_question(args, tmp_path / f"unknown-symbol-{index}")

        assert result["pack"]["chunks"] == []
        assert result["paper_search_results"] == []


def test_multilingual_primary_requests_filter_out_reviews(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        "PRIMARY\tA primary PfEMP1 malaria study\t2024\t10.1000/primary\t\t\t"
        "malaria\tPfEMP1\t\t\tresearch article\tchunked\t\t\t\n"
        "REVIEW\tA review of PfEMP1 malaria studies\t2023\t10.1000/review\t\t\t"
        "malaria\tPfEMP1\t\t\treview article\tchunked\t\t\t\n",
        encoding="utf-8",
    )
    records = {
        "PRIMARY": (
            "Materials and methods. We collected patient samples and measured PfEMP1. "
            "Results. PfEMP1 expression was compared across malaria groups."
        ),
        "REVIEW": "This narrative review summarizes PfEMP1 studies in malaria.",
    }
    for paper_id, text in records.items():
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "section": "abstract",
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    questions = (
        "Finde Primärpaper über PfEMP1 und Malaria.",
        "Encuentra artículos primarios sobre PfEMP1 y malaria.",
        "Trouve les études primaires sur PfEMP1 et le paludisme.",
    )
    for index, question in enumerate(questions, start=1):
        args = make_args(
            question=question,
            chunks_dir=str(chunks_dir),
            papers=str(papers),
            backend="none",
        )
        args.task = "sources"

        result = ask_corpus.run_question(args, tmp_path / f"primary-search-{index}")

        assert [paper["paper_id"] for paper in result["paper_search_results"]] == [
            "PRIMARY"
        ]
        assert [chunk["paper_id"] for chunk in result["pack"]["chunks"]] == [
            "PRIMARY"
        ]


def test_every_paper_search_result_gets_full_document_source_type(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "paper_id": "P1",
                "section": "methods",
                "text": "Materials and methods. We collected patient samples and used RNA-seq.",
            }
        )
        + "\n"
        + json.dumps(
            {
                "paper_id": "P1",
                "section": "results",
                "text": "Results. We analyzed parasites from participants with severe malaria.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    annotated, contexts = ask_corpus.annotate_paper_search_records(
        [{"paper_id": "P1", "title": "A clinical parasite-expression study"}],
        chunks_dir,
    )

    assert annotated[0]["source_type_assessment"]["source_type"] == "primary_research"
    assert contexts["P1"]


def test_trace_source_types_use_document_front_text_before_selection(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    documents = {
        "REVIEW": (
            "This review summarizes cohort studies of patients with severe "
            "malaria and parasite expression. We analyzed expression results "
            "reported across the literature. Methods. Results."
        ),
        "PRIMARY": (
            "We recruited 80 patients and collected parasite samples. "
            "Materials and methods. Results."
        ),
    }
    for paper_id, text in documents.items():
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_FRONT",
                    "paper_id": paper_id,
                    "section": "abstract",
                    "text": text,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    shared_excerpt = (
        "Our results show that var expression was higher in patients with "
        "severe malaria."
    )
    inputs = [
        {
            "chunk_id": f"{paper_id}_RESULT",
            "paper_id": paper_id,
            "title": "Parasite expression and clinical malaria",
            "section": "results",
            "text": shared_excerpt,
            "score": 20.0,
        }
        for paper_id in ("REVIEW", "PRIMARY")
    ]

    annotated = ask_corpus.annotate_trace_input_source_types(inputs, chunks_dir)
    blueprint = suggest_blueprint(
        "source_type_trace",
        "What evidence links var expression to severe malaria?",
        "evidence_qa",
    )
    candidates = [
        prepare_candidates([row], blueprint)[0][0]
        for row in annotated
    ]

    assert annotated[0]["source_type_assessment"]["source_type"] == (
        "narrative_review"
    )
    assert annotated[1]["source_type_assessment"]["source_type"] == (
        "primary_research"
    )
    assert candidates[0].evidence_type_scores["observational"] == 0.0
    assert not any(candidates[0].scale_scores.values())
    assert candidates[1].evidence_type_scores["observational"] == 1.0
    assert candidates[1].scale_scores["clinical"] == 1.0


def test_paper_search_collapse_keeps_hidden_version_in_origin_screen(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    title = "Transcriptome analysis of falciparum isolates in cerebral malaria"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        f"PRE\t{title}\t2021\t10.1101/2021.11.08.467248\t\t\tmalaria\t"
        "transcriptome cerebral malaria\tRNA-seq\tparasite\tpreprint\tchunked\t\t\t\n"
        f"JOURNAL\t{title}\t2022\t10.1093/infdis/jiac086\t\t\tmalaria\t"
        "transcriptome cerebral malaria\tRNA-seq\tparasite\tarticle\tchunked\t\t\t\n",
        encoding="utf-8",
    )
    for paper_id in ("PRE", "JOURNAL"):
        (chunks_dir / f"{paper_id}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"{paper_id}_C1",
                    "paper_id": paper_id,
                    "section": "abstract",
                    "text": (
                        "We used RNA-seq to compare the parasite transcriptome "
                        "in cerebral and uncomplicated malaria."
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
    args = make_args(
        question="Find papers about the falciparum transcriptome in cerebral malaria.",
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="none",
    )
    args.task = "sources"

    result = ask_corpus.run_question(args, tmp_path / "paper-search-family")

    assert len(result["paper_search_results"]) == 1
    representative = result["paper_search_results"][0]
    assert representative["paper_id"] == "JOURNAL"
    assert representative["publication_family_report_count"] == 2
    signals = result["pack"]["automatic_lineage_signals"]
    assert any(
        {signal["paper_id_a"], signal["paper_id_b"]} == {"PRE", "JOURNAL"}
        and signal["signal_type"] == "same_preprint_family"
        for signal in signals
    )


def test_document_lineage_context_cache_invalidates_after_change(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    path = chunks_dir / "P1.jsonl"
    path.write_text(
        json.dumps({"paper_id": "P1", "text": "first"}) + "\n",
        encoding="utf-8",
    )

    first = ask_corpus.load_document_lineage_context(chunks_dir, "P1")
    path.write_text(
        json.dumps({"paper_id": "P1", "text": "a longer second value"}) + "\n",
        encoding="utf-8",
    )
    second = ask_corpus.load_document_lineage_context(chunks_dir, "P1")

    assert first[0]["text"] == "first"
    assert second[0]["text"] == "a longer second value"


def test_no_model_default_run_config_records_call_booleans(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\tsystems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\textracted_text_relpath\tnotes\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "run"

    result = ask_corpus.run_question(
        make_args(
            question="What evidence links PfEMP1 to cerebral malaria?",
            chunks_dir=str(chunks_dir),
            papers=str(papers),
            backend="none",
        ),
        out_dir,
    )
    run_config = json.loads((out_dir / "run_config.json").read_text(encoding="utf-8"))

    assert run_config["planner_backend_requested"] == "rule_based"
    assert run_config["planner_call_performed"] is False
    assert run_config["answer_backend_requested"] == "none"
    assert run_config["answer_call_performed"] is False
    assert run_config["code_git_commit"]
    assert isinstance(run_config["code_git_dirty"], bool)
    assert isinstance(run_config["code_git_available"], bool)
    assert run_config["corpus_state_fingerprint"]
    assert run_config["answer_temperature"] == 0.0
    assert result["answer_path"] is None


def test_answer_backend_blocked_when_evidence_grade_too_low(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\tsystems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\textracted_text_relpath\tnotes\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "blocked"

    result = ask_corpus.run_question(
        make_args(
            question="What evidence links PfEMP1 to cerebral malaria?",
            chunks_dir=str(chunks_dir),
            papers=str(papers),
            backend="ollama",
        ),
        out_dir,
    )
    run_config = json.loads((out_dir / "run_config.json").read_text(encoding="utf-8"))

    assert run_config["evidence_grade"] == "bad"
    assert run_config["answer_backend_requested"] == "ollama"
    assert run_config["answer_backend_used"] == "none"
    assert run_config["answer_call_performed"] is False
    assert result["llm_result"].answer is None


def test_targeted_paper_search_uses_metadata_without_any_model_call(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\tsystems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\textracted_text_relpath\tnotes\n"
        "P1\tA metadata-only malaria study\t2024\t10.1000/metadata-only\t12345\t\tmalaria\tomics\t\t\tarticle\tcandidate\t\t\t\n"
        "P2\tA distractor study\t2023\t10.1000/other\t1000\t\tmalaria\tomics\t\t\tarticle\tcandidate\t\t\t\n",
        encoding="utf-8",
    )
    args = make_args(
        question="Find DOI 10.1000/metadata-only",
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="qwen",
    )
    args.task = "sources"
    args.planner_backend = "ollama"

    result = ask_corpus.run_question(args, tmp_path / "paper-search")
    run_config = json.loads(
        (tmp_path / "paper-search" / "run_config.json").read_text(encoding="utf-8")
    )

    assert [row["paper_id"] for row in result["paper_search_results"]] == ["P1"]
    assert result["paper_search"]["mode"] == "targeted_lookup"
    assert result["paper_search"]["generative_answer_used"] is False
    assert result["paper_search"]["generative_match_explanations_used"] is False
    assert result["paper_search"]["match_explanation_model"] is None
    assert run_config["planner_backend_used"] == "rule_based"
    assert run_config["planner_call_performed"] is False
    assert run_config["answer_backend_requested"] == "none"
    assert run_config["answer_call_performed"] is False
    assert result["answer_qc"]["applicable"] is False
    assert result["answer_attempts"] == []
    assert result["answer_path"] is None
    assert result["paper_search"]["topical_relevance_gate"] == {
        "status": "not_applicable",
        "blocked": False,
        "reason": "not_topical_discovery",
    }


@pytest.mark.parametrize(
    ("question", "title", "author", "expected_flag"),
    [
        (
            'Find the paper titled "A sufficiently long identifiable paper title".',
            "A sufficiently long identifiable paper title",
            "Example A",
            None,
        ),
        (
            "Which paper did Ndugwa et al. 2026 publish?",
            "A malaria cohort study",
            "Ndugwa Alice",
            "explicit_named_study_identity_only",
        ),
    ],
)
def test_targeted_title_and_author_year_resolve_metadata_without_local_chunks(
    tmp_path: Path,
    question: str,
    title: str,
    author: str,
    expected_flag: str | None,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\tauthor\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\t"
        "methods\tsystems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        f"P1\t{author}\t{title}\t2026\t10.1000/identity\t\t\tmalaria\t\t"
        "\t\tarticle\tcandidate\t\t\t\n",
        encoding="utf-8",
    )
    args = make_args(question, str(chunks_dir), str(papers), "none")
    args.task = "sources"

    result = ask_corpus.run_question(args, tmp_path / "targeted-metadata")

    assert [row["paper_id"] for row in result["paper_search_results"]] == ["P1"]
    if expected_flag:
        assert result["paper_search_results"][0][expected_flag] is True


def test_prior_turn_doi_and_intervention_intent_cannot_bypass_current_rifin_scope(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        "BLASE\tBLASE bulk linkage analysis\t2026\t"
        "10.1186/s44330-026-00082-7\t\t\tmethods\tsingle cell\t\t\t"
        "article\tchunked\t\t\t\n"
        "RIFIN\tRIFINs bind an inhibitory immune receptor\t2025\t"
        "10.1000/rifin\t\t\tmalaria\tRIFIN receptor binding\t\t\t"
        "article\tchunked\t\t\t\n",
        encoding="utf-8",
    )
    (chunks_dir / "BLASE.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "BLASE_C1",
                "paper_id": "BLASE",
                "section": "abstract",
                "text": "BLASE performs bulk linkage analysis for single-cell experiments.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (chunks_dir / "RIFIN.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "RIFIN_C1",
                "paper_id": "RIFIN",
                "section": "abstract",
                "text": "In this study, we found that RIFINs bind an inhibitory immune receptor.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    current = "And find papers on RIFIN receptor binding?"
    args = make_args(current, str(chunks_dir), str(papers), "none")
    args.task = "sources"
    args.answer_question = current
    args.retrieval_question = (
        "Previous user question: Find primary studies of therapeutic interventions "
        "for EPCR-binding PfEMP1 and DOI 10.1186/s44330-026-00082-7.\n"
        f"Current user question: {current}"
    )

    result = ask_corpus.run_question(args, tmp_path / "cross-turn-source-search")

    assert [row["paper_id"] for row in result["paper_search_results"]] == ["RIFIN"]
    assert all(
        not row.get("explicit_identifier_anchor")
        for row in result["paper_search_results"]
    )


def test_topical_source_relevance_gate_blocks_extreme_cross_domain_overlap() -> None:
    candidates = [
        {
            "paper_id": f"BIO{index}",
            "title": "Primary cell type responses in infection",
            "section": "results",
            "doi": f"10.1000/biology.{index}",
            "text": "We measured primary cell type responses in an infection model.",
        }
        for index in range(1, 4)
    ]

    gate = ask_corpus.assess_topical_source_search_relevance(
        (
            "Find primary studies measuring dark energy with Type Ia supernovae "
            "and baryon acoustic oscillations."
        ),
        candidates,
        paper_search_mode="topical_discovery",
        max_chunks=8,
        max_chars_per_chunk=900,
    )

    assert gate["status"] == "blocked_extreme_topical_mismatch"
    assert gate["blocked"] is True
    assert gate["pre_filter_grade"] == "bad"
    assert "very_low_question_term_coverage" in gate["pre_filter_reasons"]
    assert gate["question_term_coverage"] < 0.25


def test_topical_gate_does_not_treat_generic_energy_overlap_as_domain_evidence() -> None:
    candidates = [
        {
            "paper_id": f"BIO{index}",
            "title": "Primary cell energy metabolism in malaria",
            "section": "results",
            "doi": f"10.1000/energy.{index}",
            "text": (
                "We measured energy metabolism in a primary cell type infection model."
            ),
        }
        for index in range(1, 4)
    ]

    gate = ask_corpus.assess_topical_source_search_relevance(
        (
            "Find primary studies measuring dark energy with Type Ia supernovae "
            "and baryon acoustic oscillations."
        ),
        candidates,
        paper_search_mode="topical_discovery",
        max_chunks=8,
        max_chars_per_chunk=900,
    )

    assert gate["pre_filter_grade"] == "bad"
    assert gate["blocked"] is True
    assert gate["distinctive_question_term_hits"] == []


def test_topical_source_relevance_gate_preserves_broad_multifacet_search() -> None:
    question = (
        "Find papers linking Plasmodium falciparum parasite expression to clinical "
        "malaria phenotypes, including var/PfEMP1, RIFINs, STEVORs, the core "
        "parasite transcriptome, cerebral malaria, severe anaemia, respiratory "
        "distress, uncomplicated malaria, and asymptomatic infection."
    )
    candidates = [
        {
            "paper_id": "PFEMP1",
            "title": "PfEMP1 var gene expression in cerebral malaria",
            "section": "results",
            "doi": "10.1000/pfemp1",
            "text": "PfEMP1 expression was higher in severe cerebral malaria.",
        },
        {
            "paper_id": "RIFIN",
            "title": "RIFIN expression in clinical malaria",
            "section": "results",
            "doi": "10.1000/rifin",
            "text": "Parasite RIFIN expression was measured in clinical infection.",
        },
        {
            "paper_id": "STEVOR",
            "title": "STEVOR expression in uncomplicated malaria",
            "section": "results",
            "doi": "10.1000/stevor",
            "text": "STEVOR transcripts were measured in uncomplicated malaria.",
        },
        {
            "paper_id": "CORE",
            "title": "The parasite transcriptome in asymptomatic infection",
            "section": "results",
            "doi": "10.1000/transcriptome",
            "text": "The core parasite transcriptome differed in asymptomatic infection.",
        },
    ]

    gate = ask_corpus.assess_topical_source_search_relevance(
        question,
        candidates,
        paper_search_mode="topical_discovery",
        max_chunks=8,
        max_chars_per_chunk=900,
    )

    assert gate["status"] == "passed"
    assert gate["blocked"] is False
    assert gate["question_term_coverage"] >= 0.25


def test_topical_source_search_fails_closed_on_cross_domain_lexical_hits(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        "BIO1\tPrimary cell type responses in malaria\t2024\t10.1000/bio1\t\t\t"
        "malaria\timmune cells\tcohort\thuman\tresearch article\tchunked\t\t\t\n"
        "BIO2\tPrimary cell type annotation\t2023\t10.1000/bio2\t\t\t"
        "single cell\tcell types\tRNA-seq\thuman\tresearch article\tchunked\t\t\t\n"
        "BIO3\tType I immune responses in infection\t2022\t10.1000/bio3\t\t\t"
        "immunology\tcell types\tassay\thuman\tresearch article\tchunked\t\t\t\n",
        encoding="utf-8",
    )
    for index in range(1, 4):
        (chunks_dir / f"BIO{index}.jsonl").write_text(
            json.dumps(
                {
                    "chunk_id": f"BIO{index}_C1",
                    "paper_id": f"BIO{index}",
                    "section": "results",
                    "text": (
                        "Materials and methods. We enrolled participants. Results. "
                        "We measured primary cell type responses in infection."
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
    args = make_args(
        question=(
            "Find primary studies measuring dark energy with Type Ia supernovae "
            "and baryon acoustic oscillations."
        ),
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="none",
    )
    args.task = "sources"

    output_dir = tmp_path / "off-domain"
    result = ask_corpus.run_question(args, output_dir)
    run_config = json.loads((output_dir / "run_config.json").read_text())

    assert result["results"]
    assert result["paper_search_results"] == []
    assert result["pack"]["chunks"] == []
    assert result["paper_search"]["n_matches"] == 0
    assert result["paper_search"]["topical_relevance_gate"]["blocked"] is True
    assert run_config["n_candidate_chunks"] > 0
    assert run_config["n_paper_search_matches"] == 0
    assert run_config["paper_search_topical_relevance_gate"]["status"] == (
        "blocked_extreme_topical_mismatch"
    )
    assert json.loads((output_dir / "paper_search_results.json").read_text()) == []


def test_exact_doi_primary_lookup_preserves_uncertain_report_identity(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        "P1\tA requested malaria report\t2025\t10.1000/rifin-kir\t\t\t"
        "malaria\tRIFIN\t\t\t\tchunked\t\t\t\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "text": "This abstract introduces variant antigens in malaria.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    args = make_args(
        question=(
            "Find the primary paper on RIFIN binding KIR2DL1 and KIR2DS1, "
            "DOI 10.1000/rifin-kir."
        ),
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="none",
    )
    args.task = "sources"

    result = ask_corpus.run_question(args, tmp_path / "exact-doi-primary")

    assert [paper["paper_id"] for paper in result["paper_search_results"]] == [
        "P1"
    ]
    source_type = result["paper_search_results"][0]["source_type_assessment"]
    assert source_type["source_type"] == "uncertain"
    assert (
        result["paper_search_results"][0]["entity_source_directness"]
        == "exact_identifier_identity_only"
    )
    assert result["results"][0]["identifier_identity_anchor_verified"] is True
    assert result["results"][0]["required_entity_anchors_verified"] is False


def test_intervention_filter_preserves_exact_identifier_as_identity_only() -> None:
    question = (
        "Find primary studies of therapeutic interventions that disrupt EPCR-binding "
        "PfEMP1 adhesion, DOI 10.1000/requested, and report whether clinical benefit "
        "was tested."
    )
    retained = ask_corpus.retain_direct_intervention_source_evidence(
        [
            {
                "paper_id": "P1",
                "title": "A requested EPCR-binding PfEMP1 report",
                "section": "abstract",
                "text": "This report characterizes receptor binding.",
                "explicit_identifier_anchor": True,
            }
        ],
        question=question,
        max_chars_per_chunk=900,
    )

    assert len(retained) == 1
    assert retained[0]["intervention_evidence_level"] == "identity_only_unverified"
    assert retained[0]["intervention_evidence_route"] == (
        "explicit_identifier_identity_only"
    )
    bounded = ask_corpus.add_intervention_evidence_boundaries(
        question,
        ask_corpus.add_fallback_selection_reasons(retained),
    )
    assert "identifier was requested exactly" in bounded[0]["selection_reason"]
    assert "did not verify intervention testing" in bounded[0]["selection_reason"]


def test_intervention_filter_rejects_mechanism_only_report() -> None:
    question = (
        "Find primary studies of therapeutic interventions that disrupt EPCR-binding "
        "PfEMP1 adhesion and report whether clinical benefit was tested."
    )

    retained = ask_corpus.retain_direct_intervention_source_evidence(
        [
            {
                "paper_id": "MECHANISM",
                "title": "Structural conservation of EPCR-binding PfEMP1",
                "section": "results",
                "text": "We mapped the receptor-binding interface and measured affinity.",
            }
        ],
        question=question,
        max_chars_per_chunk=900,
    )

    assert retained == []


def test_unknown_explicit_doi_returns_no_lexical_near_misses(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text(
        "paper_id\ttitle\tyear\tdoi\tpmid\tpmcid\tdomain\ttopics\tmethods\t"
        "systems\tpaper_type\tfulltext_status\tlocal_pdf_relpath\t"
        "extracted_text_relpath\tnotes\n"
        "P1\tA distracting paper\t2024\t10.1000/real\t\t\tmalaria\tDOI methods\t"
        "\t\tarticle\tcandidate\t\t\t\n",
        encoding="utf-8",
    )
    (chunks_dir / "P1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "P1_C1",
                "paper_id": "P1",
                "section": "abstract",
                "text": "This paper discusses DOI lookup methods.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    args = make_args(
        question="Find DOI 10.9999/definitely.fake.12345 and summarize it",
        chunks_dir=str(chunks_dir),
        papers=str(papers),
        backend="none",
    )
    args.task = "sources"

    result = ask_corpus.run_question(args, tmp_path / "unknown-doi")

    assert result["paper_search"]["mode"] == "targeted_lookup"
    assert result["paper_search_results"] == []
    assert result["pack"]["chunks"] == []


def test_qwen_backend_cannot_bypass_evidence_gate(
    monkeypatch,
) -> None:
    model_calls: list[str] = []

    def fake_generate(prompt: str, backend: str):
        model_calls.append(f"{backend}:{prompt}")
        raise AssertionError("model must not be called")

    monkeypatch.setattr(ask_corpus, "generate_answer", fake_generate)

    result, metadata = ask_corpus.generate_initial_answer(
        question="Question",
        task="answer",
        backend="qwen",
        prompt="Prompt",
        evidence_pack={"chunks": []},
        evidence_is_sufficient=False,
    )

    assert result.answer is None
    assert metadata["blocked"] is True
    assert metadata["call_performed"] is False
    assert model_calls == []


def test_deterministic_template_respects_evidence_gate() -> None:
    result, metadata = ask_corpus.generate_initial_answer(
        question="Question",
        task="answer",
        backend="template",
        prompt="Prompt",
        evidence_pack={"chunks": []},
        evidence_is_sufficient=False,
    )

    assert result.answer is None
    assert metadata["blocked"] is True


def test_run_question_fails_before_writing_when_corpus_inputs_are_missing(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "run"

    try:
        ask_corpus.run_question(
            make_args(
                question="Question",
                chunks_dir=str(tmp_path / "missing_chunks"),
                papers=str(tmp_path / "missing_papers.tsv"),
                backend="none",
            ),
            out_dir,
        )
    except FileNotFoundError as exc:
        assert "chunks directory" in str(exc)
    else:
        raise AssertionError("missing corpus inputs must fail")

    assert not out_dir.exists()


def test_ungrounded_answer_is_quarantined_not_published(tmp_path: Path) -> None:
    answer_path = tmp_path / "answer.md"
    rejected_path = tmp_path / "answer_rejected.md"
    result, report, final_path, final_rejected = ask_corpus.persist_grounded_answer(
        ask_corpus.LLMResult(
            backend="qwen",
            answer=(
                "## Evidence-backed statements\n\n"
                "Aspirin cures pancreatic cancer (E001).\n\n"
                "## References\n\n- E001"
            ),
        ),
        {
            "chunks": [
                {
                    "evidence_id": "E001",
                    "title": "PfEMP1 malaria binding",
                    "text": "PfEMP1 binds EPCR in severe malaria.",
                }
            ]
        },
        task="answer",
        answer_path=answer_path,
        rejected_answer_path=rejected_path,
        report_path=tmp_path / "grounding.md",
        issues_path=tmp_path / "issues.tsv",
        report_json_path=tmp_path / "grounding.json",
    )

    assert report and report["passed"] is False
    assert result.answer is None
    assert final_path is None
    assert final_rejected == rejected_path
    assert rejected_path.exists()
    assert not answer_path.exists()


def test_output_directories_do_not_collide_within_one_second(tmp_path: Path) -> None:
    first = ask_corpus.make_output_dir(tmp_path, "Same question")
    second = ask_corpus.make_output_dir(tmp_path, "Same question")

    assert first != second


def test_empty_repair_retains_nonempty_initial_answer(monkeypatch, tmp_path: Path) -> None:
    initial = ask_corpus.LLMResult(
        backend="qwen",
        answer="## Evidence-backed statements\n\nInitial answer (E001).",
    )

    monkeypatch.setattr(
        ask_corpus,
        "generate_initial_answer",
        lambda **kwargs: (
            initial,
            {
                "backend_requested": "qwen",
                "backend_used": "qwen",
                "model": "qwen3:30b",
                "call_performed": True,
                "blocked": False,
                "block_reason": None,
            },
        ),
    )
    monkeypatch.setattr(
        ask_corpus,
        "generate_answer_with_backend",
        lambda *args, **kwargs: SimpleNamespace(
            llm_result=ask_corpus.LLMResult(
                backend="qwen",
                answer=None,
                error="ollama_returned_empty_response",
            ),
            call_performed=True,
            backend_used="qwen",
            model="qwen3:30b",
        ),
    )
    monkeypatch.setattr(
        ask_corpus,
        "assess_answer_quality",
        lambda *args, **kwargs: {"passed": False, "warnings": ["test warning"]},
    )

    result, qc, attempts, metadata = ask_corpus.generate_with_qc_repair(
        question="Question",
        task="answer",
        backend="qwen",
        initial_prompt="Prompt",
        evidence_pack={"chunks": []},
        output_dir=tmp_path,
        max_repair_attempts=1,
        use_template_fallback=False,
    )

    assert result.answer == initial.answer
    assert result.error is None
    assert qc["passed"] is False
    assert [attempt["kind"] for attempt in attempts] == ["initial", "repair"]
    assert attempts[1]["answer_path"] is None
    assert metadata["repair_errors"] == [
        {
            "attempt": 1,
            "error": "ollama_returned_empty_response",
            "retained_attempt": 0,
        }
    ]


def test_repository_question_uses_valid_contract_without_model(
    monkeypatch, tmp_path: Path
) -> None:
    pack = {
        "question": "Which datasets are accessible for reanalysis?",
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "citation_short": "Example et al., 2024",
                "citation_label": "[Example et al., 2024, Data Availability]",
                "title": "Reusable malaria data",
                "doi": "10.1000/example",
                "text": "Data are deposited under GSE12345.",
            }
        ],
    }
    monkeypatch.setattr(
        ask_corpus,
        "generate_initial_answer",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model was called")),
    )

    result, qc, attempts, metadata = ask_corpus.generate_with_qc_repair(
        question=pack["question"],
        task="answer",
        backend="qwen",
        initial_prompt="unused",
        evidence_pack=pack,
        output_dir=tmp_path,
        max_repair_attempts=1,
        use_template_fallback=False,
        answer_model="qwen3:30b",
    )

    assert qc["passed"] is True
    assert "GSE12345" in result.answer
    assert [attempt["kind"] for attempt in attempts] == ["repository_contract_direct"]
    assert metadata["backend_used"] == "repository_contract"
    assert metadata["call_performed"] is False


def test_study_design_question_with_repository_evidence_still_calls_model(
    monkeypatch, tmp_path: Path
) -> None:
    question = (
        "Did Ciuffreda et al. reconstruct continuous or multimodal parasite-age "
        "distributions from whole-transcriptome RNA-seq? State what they actually "
        "measured, the model input, the clinical cohort, and the limitation observed "
        "for mixed-stage samples."
    )
    pack = {
        "question": question,
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "citation_short": "Other et al., 2024",
                "citation_label": "[Other et al., 2024, Data Availability]",
                "title": "A transcriptome study",
                "text": "RNA-seq data were deposited under GSE12345.",
            }
        ],
    }
    initial = ask_corpus.LLMResult(
        backend="qwen",
        answer="## Evidence-backed statements\n\nThe evidence is limited (E001).",
    )
    calls = []

    def generate_initial(**kwargs):
        calls.append(kwargs)
        return (
            initial,
            {
                "backend_requested": "qwen",
                "backend_used": "qwen",
                "model": "qwen3:30b",
                "call_performed": True,
                "blocked": False,
                "block_reason": None,
            },
        )

    monkeypatch.setattr(ask_corpus, "generate_initial_answer", generate_initial)
    monkeypatch.setattr(
        ask_corpus,
        "assess_answer_quality",
        lambda *args, **kwargs: {"passed": True, "warnings": []},
    )

    result, qc, attempts, metadata = ask_corpus.generate_with_qc_repair(
        question=question,
        task="answer",
        backend="qwen",
        initial_prompt="Prompt",
        evidence_pack=pack,
        output_dir=tmp_path,
        max_repair_attempts=1,
        use_template_fallback=False,
        answer_model="qwen3:30b",
    )

    assert calls
    assert "The evidence is limited (E001)." in result.answer
    assert qc["passed"] is True
    assert [attempt["kind"] for attempt in attempts] == ["initial"]
    assert metadata["backend_used"] == "qwen"
    assert not metadata.get("repository_contract_direct_used", False)


def test_marker_positive_state_question_uses_bounded_contract_without_model(
    monkeypatch, tmp_path: Path
) -> None:
    pack = {
        "question": (
            "What SPP1-positive macrophage states are reported in COVID-19, "
            "and how are they associated with disease severity?"
        ),
        "chunks": [
            {
                "evidence_id": "E001",
                "paper_id": "P1",
                "citation_short": "Liao et al., 2020",
                "citation_label": "[Liao et al., 2020, Results]",
                "text": (
                    "The macrophages included FCN1hi group 1, FCN1loSPP1+ group 2, "
                    "SPP1+ group 3 and FABP4+ group 4. FABP4 was preferentially "
                    "expressed in controls and moderate infection, while FCN1 and "
                    "SPP1 were highly expressed in severe/critical infection."
                ),
            }
        ],
    }
    monkeypatch.setattr(
        ask_corpus,
        "generate_initial_answer",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model was called")),
    )

    result, qc, attempts, metadata = ask_corpus.generate_with_qc_repair(
        question=pack["question"],
        task="answer",
        backend="qwen",
        initial_prompt="unused",
        evidence_pack=pack,
        output_dir=tmp_path,
        max_repair_attempts=2,
        use_template_fallback=True,
        answer_model="qwen3:30b",
    )

    assert qc["passed"] is True, qc
    assert "FCN1loSPP1+ group 2 and SPP1+ group 3" in result.answer
    assert "FABP4+ group 4" not in result.answer
    assert [attempt["kind"] for attempt in attempts] == [
        "bounded_marker_state_direct"
    ]
    assert metadata["backend_used"] == "bounded_evidence_contract"
    assert metadata["call_performed"] is False


def test_trace_withheld_answer_never_promotes_unknown_pairs() -> None:
    answer = ask_corpus.build_trace_withheld_answer(
        {
            "n_unique_papers": 5,
            "trace_sufficiency_certificate": {
                "raw_paper_count": 5,
                "reasons": ["lineage_graph_artifact_developmental"],
            },
            "trace_robust_lineage": {
                "status": "lineage_review_required",
                "missing_pair_count": 3,
                "uncertain_pair_count": 4,
            },
        }
    )

    assert "retrieved 5 reports" in answer
    assert "does not certify an exact independent-origin count" in answer
    assert "3 missing and 4 uncertain" in answer
    assert "never promoted to independent evidence" in answer
    assert "turn the 5 reports" in answer
    assert "five reports" not in answer


def test_trace_withheld_answer_explains_zero_direct_matches() -> None:
    answer = ask_corpus.build_trace_withheld_answer(
        {
            "question": "How does the memory T-cell repertoire change with age?",
            "n_unique_papers": 0,
            "trace_sufficiency_certificate": {
                "question_id": "q1",
                "raw_paper_count": 0,
                "unresolved_facets": [
                    "q1_memory_t_cell_identity",
                    "q1_tcr_repertoire",
                    "q1_developmental_age_span",
                ],
            },
            "trace_robust_lineage": {
                "status": "insufficient_even_optimistically",
            },
        }
    )

    assert "No directly matching evidence retrieved" in answer
    assert "no literature synthesis was generated" in answer
    assert "memory t cell identity" in answer
    assert "tcr repertoire" in answer
    assert "developmental age span" in answer
    assert "corpus-conditional" in answer
    assert "independent origins unresolved" not in answer


def test_trace_withheld_answer_reports_publication_family_collapse() -> None:
    answer = ask_corpus.build_trace_withheld_answer(
        {
            "n_unique_papers": 5,
            "chunks": [
                {"paper_id": "P1", "doi": "10.1101/example"},
                {"paper_id": "P2", "doi": "10.1000/journal"},
                {"paper_id": "P3"}, {"paper_id": "P4"}, {"paper_id": "P5"},
            ],
            "trace_sufficiency_certificate": {"raw_paper_count": 5},
            "trace_robust_lineage": {"status": "lineage_review_required"},
            "trace_lineage_edges": [
                {
                    "paper_id_a": "P1", "paper_id_b": "P2",
                    "relationship_types": ["document_explicit_publication_family"],
                    "origin_review_status": "suggested",
                    "origin_evidence_locator": "The journal names the preprint.",
                }
            ],
        }
    )

    assert "Detected publication-family collapse" in answer
    assert "Publication family identified; independent origins unresolved" in answer
    assert "P1 (DOI 10.1101/example) ↔ P2 (DOI 10.1000/journal)" in answer
    assert "5 reports represent at most 4 publication-family units" in answer
    assert "do not by themselves prove" in answer


def test_qc_withheld_answer_explains_rejected_draft() -> None:
    answer = ask_corpus.build_qc_withheld_answer(
        {"warnings": ["origin_count_ignores_same_study_relation"]}
    )

    assert "quality check failed" in answer
    assert "origin_count_ignores_same_study_relation" in answer
    assert "No failed draft is promoted" in answer


def test_failed_final_qc_quarantines_draft(tmp_path: Path) -> None:
    rejected_path = tmp_path / "answer_rejected_qc.md"

    result, final_rejected_path = ask_corpus.reject_answer_failed_qc(
        ask_corpus.LLMResult(backend="qwen", answer="Unsafe draft"),
        {"passed": False, "warnings": ["semantic_error"]},
        rejected_path,
    )

    assert result.answer is None
    assert result.error == "answer_rejected_by_quality_check"
    assert final_rejected_path == rejected_path
    assert rejected_path.read_text(encoding="utf-8") == "Unsafe draft\n"


def test_aggregate_answer_usage_counts_all_model_attempts() -> None:
    aggregate = ask_corpus.aggregate_answer_usage(
        [
            {"usage": {"prompt_eval_count": 100, "eval_count": 20}},
            {"usage": {"prompt_eval_count": 80, "eval_count": 15}},
            {"usage": None},
        ]
    )

    assert aggregate["prompt_eval_count"] == 180
    assert aggregate["eval_count"] == 35
    assert aggregate["load_duration"] == 0


def test_data_accessibility_query_uses_methods_ranking() -> None:
    query = ask_corpus.RetrievalQuery(
        label="data_accessibility",
        query="GEO repository accession data availability",
    )

    assert ask_corpus.query_mode_for_retrieval(query, "evidence") == "methods"
    assert (
        ask_corpus.query_mode_for_retrieval(
            ask_corpus.RetrievalQuery(label="main", query="clinical result"),
            "evidence",
        )
        == "evidence"
    )


def test_data_accessibility_passage_replaces_first_slot_within_paper() -> None:
    candidates = [
        {
            "paper_id": "P1",
            "chunk_id": "P1_RESULTS",
            "text": "The study found a clinical association.",
            "query_ranks": {"main": 1},
            "score": 20,
        },
        {
            "paper_id": "P2",
            "chunk_id": "P2_RESULTS",
            "text": "A separate result.",
            "query_ranks": {"main": 2},
            "score": 18,
        },
        {
            "paper_id": "P1",
            "chunk_id": "P1_ACCESS",
            "text": "Data availability: RNA-seq data were deposited in GEO under GSE12345.",
            "query_ranks": {"data_accessibility": 4},
            "score": 8,
        },
    ]

    promoted = ask_corpus.promote_intent_passages(
        candidates,
        data_accessibility_query_labels={"data_accessibility"},
    )

    assert [row["chunk_id"] for row in promoted] == [
        "P1_ACCESS",
        "P2_RESULTS",
        "P1_RESULTS",
    ]


def test_multilingual_inventory_runs_as_sourced_discovery_without_changing_certificate_policy(
    tmp_path, monkeypatch,
):
    original = "welceh gen famleien findest du im koprus?"
    normalized = "Which gene families occur in the corpus?"
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    title = "Highly variable Alpha gene families"
    text = "The Alpha gene family is highly variable. " * 12
    (chunks / "P1.jsonl").write_text(json.dumps({
        "paper_id": "P1", "chunk_id": "C1", "title": title,
        "section": "results", "text": text, "doi": "10.1000/example", "year": "2020",
        "author": "Example", "quality_flags": [],
    }) + "\n")
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tyear\tdoi\nP1\t" + title + "\t2020\t10.1000/example\n")
    args = make_args(original, str(chunks), str(papers), "qwen")
    args.understand_question = True
    args.evidence_selector = "trace_flow"
    prepared = {"status": "ready", "original_question": original, "normalized_question": normalized,
                "intent": "corpus_inventory", "language": "de", "model": "test",
                "search_queries": ["highly variable gene families", "Alpha gene family"]}
    monkeypatch.setattr(ask_corpus, "understand_question", lambda *a, **k: prepared)
    extraction = []

    def extract(question, records, retrieved, **kwargs):
        extraction.append((question, records, retrieved))
        return {"scope": "retrieved_reports_only", "status": "verified_mentions",
                "items": [{"name": "Alpha", "paper_id": "P1", "quote": title}]}

    monkeypatch.setattr(ask_corpus, "extract_inventory", extract)
    monkeypatch.setattr(ask_corpus, "run_trace_selection", lambda *a, **k: pytest.fail(
        "A descriptive inventory must not be represented as a sufficiency certificate"))
    result = ask_corpus.run_question(args, tmp_path / "run")
    assert result["resolved_task"] == "sources"
    assert result["pack"]["question"] == original
    assert result["pack"]["normalized_question"] == normalized
    assert result["pack"]["corpus_inventory"]["items"][0]["name"] == "Alpha"
    assert "automatic_lineage_signals" in result["pack"]
    assert result["paper_search_results"]
    assert extraction[0][0] == normalized
    assert args.task == "answer" and args.evidence_selector == "trace_flow"
    saved = json.loads((tmp_path / "run/run_config.json").read_text())
    assert saved["question"] == original and saved["normalized_question"] == normalized
    assert saved["answer_call_performed"] is False


def test_translated_question_reaches_unchanged_trace_selector(tmp_path, monkeypatch):
    original = "Welche Evidenz stützt diese Aussage?"
    normalized = "What evidence supports this statement?"
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    papers = tmp_path / "papers.tsv"
    papers.write_text("paper_id\ttitle\tdoi\n")
    args = make_args(original, str(chunks), str(papers), "qwen")
    args.understand_question = True
    args.evidence_selector = "trace_flow"
    args.trace_token_budget = 2000
    monkeypatch.setattr(ask_corpus, "understand_question", lambda *a, **k: {
        "status": "ready", "original_question": original, "normalized_question": normalized,
        "intent": "question", "language": "de", "model": "test",
        "search_queries": ["empirical evidence statement"],
    })

    class ReachedTrace(Exception):
        pass

    def trace(question, task_mode, *a, **kwargs):
        assert question == normalized
        assert kwargs["selector"] == "trace_flow"
        assert kwargs["token_budget"] == 2000
        raise ReachedTrace

    monkeypatch.setattr(ask_corpus, "run_trace_selection", trace)
    with pytest.raises(ReachedTrace):
        ask_corpus.run_question(args, tmp_path / "run")
    assert args.question == original


def make_args(question: str, chunks_dir: str, papers: str, backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        question=question,
        retrieval_question=question,
        answer_question=question,
        chunks_dir=chunks_dir,
        papers=papers,
        limit=4,
        candidate_multiplier=1,
        max_per_paper=1,
        max_chars_per_chunk=500,
        preset="quick",
        backend=backend,
        adaptive=False,
        task="answer",
        task_mode="evidence_qa",
        planner_backend="rule_based",
        planner_model=None,
        planner_temperature=0.0,
        planner_timeout=120,
        answer_model="qwen3:30b",
        answer_temperature=0.0,
        answer_timeout=120,
        min_evidence_grade="usable",
        force_answer=False,
        out_dir="",
        category=None,
        include_low_quality=False,
        show_context=False,
        query_mode="evidence",
        min_score=0.0,
        exclude_tables=False,
        exclude_figures=False,
        exclude_supplement=False,
        allow_missing_doi_sources=True,
        repair_attempts=0,
        no_template_fallback=True,
        profile_instruction="",
    )
