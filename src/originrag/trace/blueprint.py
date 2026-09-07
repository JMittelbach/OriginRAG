from __future__ import annotations

import re

from originrag.trace.schemas import EvidenceBlueprint, EvidenceFacet


SCALE_BY_FACET_TYPE = {
    "molecular_entity": "molecular",
    "molecular_expression": "molecular",
    "molecular_interaction": "molecular",
    "cellular_process": "cellular",
    "tissue_process": "tissue",
    "clinical_phenotype": "clinical",
    "population_context": "population",
    "methodological_context": "methodological",
    "confounder": "methodological",
    "counterevidence": "clinical",
    "limitation": "methodological",
    "dataset_requirement": "methodological",
}

METHOD_QUERY_TERMS = ("algorithm", "assay", "benchmark", "method", "pipeline", "protocol")
CLINICAL_QUERY_TERMS = (
    "clinical", "critical", "disease", "mortality", "outcome", "patient",
    "phenotype", "severe", "severity", "survival", "syndrome",
)
CELLULAR_QUERY_TERMS = (
    "cell state", "cell type", "cellular", "cluster", "fibroblast", "lymphocyte",
    "macrophage", "monocyte", "neutrophil", "subcluster", "subtype",
)
EXPRESSION_QUERY_TERMS = (
    "expression", "expressed", "gene signature", "marker", "positive cell",
    "positive macrophage", "transcript",
)
DIRECT_ALIAS_STOPWORDS = {
    "about", "and", "are", "associated", "association", "between", "does", "evidence",
    "find", "for", "from", "have", "how", "information", "reported", "reports", "show",
    "shows", "that", "than", "the", "their", "these", "they", "this", "using", "was",
    "were", "what", "where", "which", "with",
}

DISEASE_CONTEXTS = (
    ("covid", "COVID-19 context", ("covid 19", "covid", "sars cov 2")),
    ("malaria", "Malaria context", ("malaria",)),
    ("fibrosis", "Fibrosis context", ("fibrosis", "fibrotic")),
    ("arthritis", "Arthritis context", ("arthritis",)),
    ("cancer", "Cancer context", ("cancer", "tumor", "tumour")),
)
MARKER_ANCHOR_EXCLUSIONS = {
    "covid", "rna", "sars", "cell", "cells", "patient", "patients",
    "macrophage", "macrophages", "positive",
}


def suggest_blueprint(question_id: str, question: str, task_mode: str) -> EvidenceBlueprint:
    lowered = question.lower()
    facets: list[tuple[str, str, str, list[str], bool, float]] = []
    memory_t_cell_facets = memory_t_cell_life_course_facets(lowered)
    concepts = concept_facets(lowered)
    if memory_t_cell_facets:



        concepts = [facet for facet in concepts if facet[0] != "immune_state"]
        facets.extend(memory_t_cell_facets)
    facets.extend(concepts)
    facets.extend(direct_biological_facets(question, task_mode))
    if any(term in lowered for term in ["bind", "binding", "interaction", "receptor"]):
        facets.append(("interaction", "Molecular interaction", "molecular_interaction", ["binding", "interaction", "receptor"], True, 1.2))
    if contains_any(lowered, ("cerebral", *CLINICAL_QUERY_TERMS)):
        facets.extend(clinical_question_facets(lowered))
    if task_mode == "mechanism_mapping" or any(term in lowered for term in ["mechanism", "mechanistically", "pathogenesis"]):
        facets.append(("cellular_mechanism", "Cellular or tissue mechanism", "cellular_process", ["endothelial", "sequestration", "cytoadhesion", "pathogenesis"], True, 1.1))
    if task_mode == "dataset_finder":
        facets.append(("dataset", "Dataset and cohort availability", "dataset_requirement", ["dataset", "cohort", "accession", "samples"], True, 1.2))
    if task_mode == "method_comparison":
        facets.append(("methods", "Compared methods and evaluation", "methodological_context", ["method", "assay", "benchmark", "performance"], True, 1.2))
    if task_mode == "contradiction_finder":
        facets.append(("counterevidence", "Contradictory or context-limiting evidence", "counterevidence", ["contradict", "however", "not explain", "limitation"], True, 1.3))
    if task_mode == "gap_finder":
        facets.append(("missing_evidence", "Missing linked or paired evidence", "limitation", ["missing", "paired", "gap", "limitation"], True, 1.2))
    causal_or_therapeutic = requests_causal_or_interventional_outcomes(lowered)
    if causal_or_therapeutic:
        facets.append(
            (
                "interventional_evidence",
                "Direct causal or approved interventional outcome evidence",
                "clinical_phenotype",
                ["approved drug improves clinical outcomes", "randomized clinical trial", "prospective intervention outcome", "causal perturbation evidence"],
                True,
                1.5,
            )
        )
    if not any(required for _, _, _, _, required, _ in facets):
        facets.append(
            (
                "direct_answer_evidence",
                "Direct evidence addressing the question",
                direct_answer_facet_type(lowered, task_mode),
                direct_answer_aliases(question),
                True,
                1.0,
            )
        )
    facets.append(("limitations", "Limitations and uncertainty", "limitation", ["limitation", "uncertain", "confound", "cannot"], task_mode in {"contradiction_finder", "gap_finder"}, 0.8))
    unique = []
    seen = set()
    for key, label, kind, aliases, required, weight in facets:
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            EvidenceFacet(
                facet_id=f"{question_id}_{key}",
                question_id=question_id,
                label=label,
                description=f"Evidence addressing {label.lower()}.",
                facet_type=kind,
                biological_scale=SCALE_BY_FACET_TYPE[kind],
                evidence_type=evidence_type_for(kind),
                polarity_requirement="contradiction" if kind == "counterevidence" else "any",
                importance_weight=weight,
                minimum_items=1,
                is_required=required,
                aliases=aliases,
                notes="Deterministic suggestion; requires human review before becoming gold.",
            )
        )
    scales = list(dict.fromkeys(facet.biological_scale for facet in unique if facet.is_required))
    polarities = ["support"]
    if task_mode == "contradiction_finder":
        polarities.append("contradiction")
    return EvidenceBlueprint(
        question_id=question_id,
        question=question,
        task_mode=task_mode,
        required_facets=unique,
        preferred_scales=scales,
        preferred_evidence_types=list(dict.fromkeys(f.evidence_type for f in unique if f.evidence_type)),
        required_polarities=polarities,
        context_variables=context_variables(lowered),
        minimum_independent_lineages=3 if task_mode in {"mechanism_mapping", "contradiction_finder", "gap_finder"} else 2,
        allowed_claim_strength_if_complete=(
            "direct_fact" if "approved drug" in lowered
            else "mechanistic_inference" if task_mode == "mechanism_mapping"
            else "association"
        ),
        notes="Rule-based TRACE blueprint suggestion.",
        review_status="suggested",
    )


def concept_facets(question: str) -> list[tuple[str, str, str, list[str], bool, float]]:
    lexicon = [
        ("pfemp1", "PfEMP1 or var molecular identity", ["pfemp1", "var gene", "cidralpha1", "cidrα1"]),
        ("epcr", "EPCR receptor context", ["epcr", "endothelial protein c receptor"]),
        ("icam1", "ICAM1 receptor context", ["icam1", "icam-1"]),
        ("var_expression", "var-expression programme", ["var expression", "var-expression", "transcript"]),
        ("sex", "Biological sex context", ["sex", "female", "male", "gender"]),
    ]
    facets = [
        (key, label, "molecular_entity" if key in {"pfemp1", "epcr", "icam1", "var_expression"} else "cellular_process", aliases, True, 1.2)
        for key, label, aliases in lexicon
        if any(alias in question for alias in aliases)
    ]
    immune_aliases = matched_immune_state_aliases(question)
    if immune_aliases:
        facets.append(
            (
                "immune_state",
                "Host immune-cell state",
                "cellular_process",
                immune_aliases,
                True,
                1.2,
            )
        )
    return facets


def matched_immune_state_aliases(question: str) -> list[str]:
    """Keep a named immune-cell target distinct from other immune lineages."""

    t_cell_aliases = [
        "t cell",
        "t cells",
        "t lymphocyte",
        "t lymphocytes",
        "cd4 t cell",
        "cd4 t cells",
        "cd8 t cell",
        "cd8 t cells",
    ]
    if any(contains_token_phrase(question, alias) for alias in t_cell_aliases):
        return t_cell_aliases
    if any(
        contains_token_phrase(question, alias)
        for alias in ("immune state", "immune-cell", "single-cell")
    ):
        return ["immune state", "immune-cell", "single-cell"]
    return []


def contains_token_phrase(question: str, phrase: str) -> bool:
    """Match a phrase without allowing cross-token suffix substrings."""

    normalized_question = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    normalized_phrase = " ".join(re.findall(r"[a-z0-9]+", phrase.lower()))
    return bool(
        normalized_phrase
        and f" {normalized_phrase} " in f" {normalized_question} "
    )


def requests_causal_or_interventional_outcomes(question: str) -> bool:
    """Require causal/interventional evidence only when the outcome is asked.

    A source-list phrase such as ``therapeutic targeting`` names one possible
    molecular function. It does not ask every returned paper to contain an
    approved therapy or a clinical trial.
    """

    causal = bool(
        re.search(r"\b(?:cause|causes|caused|causal|causality)\b", question)
    )
    approved = bool(
        re.search(
            r"\bapproved (?:drug|therapy|treatment|intervention)\b",
            question,
        )
    )
    intervention = bool(
        re.search(
            r"\b(?:drug|intervention|therapy|therapeutic|treatment)\w*\b",
            question,
        )
    )
    outcome = bool(
        re.search(
            r"\b(?:clinical (?:benefit|efficacy|outcome|outcomes|trial|trials)|"
            r"efficacy|effective|effectiveness|improve\w* (?:clinical )?outcomes?|"
            r"randomi[sz]ed|reduce\w* mortality|trial outcomes?)\b",
            question,
        )
    )
    return causal or approved or (intervention and outcome)


def memory_t_cell_life_course_facets(
    question: str,
) -> list[tuple[str, str, str, list[str], bool, float]]:
    """Preserve the conjunction in memory-T-cell repertoire age questions.

    Facet aliases are disjunctive downstream.  Consequently, putting ``t cell``,
    ``monocyte``, ``repertoire`` and age terms in one generic facet would allow
    any one of them to satisfy the whole request.  This narrow decomposition is
    activated only when memory T cells, their receptor repertoire, and an age
    trajectory or age range are all explicit in the question.
    """

    normalized = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    padded = f" {normalized} "

    def has_phrase(*terms: str) -> bool:
        return any(
            f" {' '.join(re.findall(r'[a-z0-9]+', term.lower()))} " in padded
            for term in terms
        )

    has_memory_t_cell = has_phrase(
        "memory t cell",
        "memory t cells",
        "memory cd4 t cell",
        "memory cd4 t cells",
        "memory cd8 t cell",
        "memory cd8 t cells",
        "memory t lymphocyte",
        "memory t lymphocytes",
        "t cell memory",
    )
    has_repertoire = has_phrase(
        "repertoire",
        "t cell receptor",
        "t cell receptors",
        "tcr",
        "clonotype",
        "clonotypes",
    )
    has_early_stage = has_phrase(
        "infancy",
        "infant",
        "infants",
        "early childhood",
        "childhood",
        "child",
        "children",
        "pediatric",
        "paediatric",
    )
    has_later_stage = has_phrase(
        "adolescence",
        "adolescent",
        "adolescents",
        "teenage",
        "teenager",
        "teenagers",
        "adulthood",
        "adult",
        "adults",
    )
    has_age_trajectory = has_phrase(
        "across age",
        "with age",
        "age dependent",
        "age related",
        "age trajectory",
        "developmental trajectory",
        "life course",
        "lifecourse",
        "life span",
        "lifespan",
    )
    if not (
        has_memory_t_cell
        and has_repertoire
        and ((has_early_stage and has_later_stage) or has_age_trajectory)
    ):
        return []

    return [
        (
            "memory_t_cell_identity",
            "Memory T-cell identity",
            "cellular_process",
            [
                "memory t cell",
                "memory cd4 t cell",
                "memory cd8 t cell",
                "memory t lymphocyte",
                "t cell memory",
            ],
            True,
            1.3,
        ),
        (
            "tcr_repertoire",
            "T-cell receptor repertoire",
            "cellular_process",
            [
                "t cell receptor repertoire",
                "t cell receptor beta repertoire",
                "tcr repertoire",
                "t cell repertoire",
                "t cell receptor sequencing",
                "tcr sequencing",
                "t cell clonotype",
                "tcr clonotype",
            ],
            True,
            1.3,
        ),
        (
            "developmental_age_span",
            "Developmental age span",
            "population_context",
            [
                "early childhood to adolescence",
                "childhood to adolescence",
                "childhood and adolescence",
                "children and adolescents",
                "child and adolescent",
                "t cell repertoire across age",
                "t cell repertoire with age",
                "age dependent t cell repertoire",
                "age related t cell repertoire",
                "developmental t cell repertoire",
                "longitudinal t cell repertoire",
            ],
            True,
            1.3,
        ),
    ]


def direct_biological_facets(
    question: str,
    task_mode: str,
) -> list[tuple[str, str, str, list[str], bool, float]]:
    """Split biological targets and cell contexts into conjunctive requirements.

    A single direct-answer facet makes all aliases alternatives.  For a question
    such as ``SPP1-positive macrophages in severe COVID-19``, that would allow a
    passage mentioning only ``severe`` to receive credit for the whole question.
    Separate facets preserve the scientific conjunction without changing the
    downstream facet-scoring contract.
    """

    lowered = question.lower()
    if task_mode == "method_comparison" or contains_any(lowered, METHOD_QUERY_TERMS):
        return []

    facets: list[tuple[str, str, str, list[str], bool, float]] = []
    markers = marker_anchors(question)
    if markers:
        kind = (
            "molecular_expression"
            if contains_any(lowered, (*EXPRESSION_QUERY_TERMS, "positive"))
            or re.search(r"(?:[- ]positive|\+)", lowered)
            else "molecular_entity"
        )
        facets.append(
            (
                "molecular_target",
                "Molecular target or marker",
                kind,
                markers,
                True,
                1.2,
            )
        )

    cellular_aliases = matched_cellular_aliases(lowered)
    if cellular_aliases:
        facets.append(
            (
                "cellular_context",
                "Cell type or state",
                "cellular_process",
                cellular_aliases,
                True,
                1.2,
            )
        )
    return facets


def marker_anchors(question: str) -> list[str]:
    """Extract explicit marker-positive or gene-like anchors conservatively."""

    anchors = [
        match.group(1).lower()
        for match in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9]{1,15})(?:[- ]positive|\+)",
            question,
            flags=re.IGNORECASE,
        )
    ]
    anchors.extend(
        token.lower()
        for token in re.findall(r"\b[A-Z][A-Z0-9]{1,11}\b", question)
        if any(character.isdigit() for character in token)
    )
    return list(
        dict.fromkeys(
            anchor
            for anchor in anchors
            if anchor not in MARKER_ANCHOR_EXCLUSIONS
        )
    )


def matched_cellular_aliases(question: str) -> list[str]:
    aliases: list[str] = []
    for term in CELLULAR_QUERY_TERMS:
        if contains_any(question, (term,)):
            aliases.append(term)
            if term in {
                "macrophage",
                "monocyte",
                "neutrophil",
                "fibroblast",
                "lymphocyte",
            }:
                aliases.append(f"{term}s")
    if contains_any(question, ("state", "states")):
        aliases.extend(["cell state", "cell states", "subcluster"])
    return list(dict.fromkeys(aliases))


def clinical_question_facets(
    question: str,
) -> list[tuple[str, str, str, list[str], bool, float]]:
    """Represent disease context and outcome as separate required facets."""

    facets: list[tuple[str, str, str, list[str], bool, float]] = []
    for key, label, aliases in DISEASE_CONTEXTS:
        if contains_any(question, aliases):
            facets.append(
                (
                    f"disease_{key}",
                    label,
                    "clinical_phenotype",
                    list(aliases),
                    True,
                    1.1,
                )
            )

    outcome_aliases: list[str] = []
    if contains_any(question, ("severe", "severity", "critical")):
        outcome_aliases.extend(["severity", "severe", "critical"])
    if contains_any(question, ("cerebral",)):
        outcome_aliases.extend(["cerebral malaria", "cerebral"])
    if contains_any(question, ("mortality", "death")):
        outcome_aliases.extend(["mortality", "death", "fatal"])
    if contains_any(question, ("survival",)):
        outcome_aliases.extend(["survival", "survived"])
    if contains_any(question, ("outcome", "phenotype", "syndrome")):
        outcome_aliases.extend(["outcome", "phenotype", "syndrome"])
    if outcome_aliases:
        facets.append(
            (
                "clinical_outcome",
                "Clinical phenotype or outcome",
                "clinical_phenotype",
                list(dict.fromkeys(outcome_aliases)),
                True,
                1.2,
            )
        )

    if not facets:
        fallback_aliases = [
            alias
            for alias in direct_answer_aliases(question)
            if alias in CLINICAL_QUERY_TERMS or alias in {"cerebral", "death"}
        ]
        facets.append(
            (
                "clinical_outcome",
                "Clinical phenotype or outcome",
                "clinical_phenotype",
                fallback_aliases or ["clinical", "patient", "disease"],
                True,
                1.2,
            )
        )
    return facets


def evidence_type_for(facet_type: str) -> str:
    return {
        "molecular_entity": "biochemical",
        "molecular_expression": "transcriptomic",
        "molecular_interaction": "biochemical",
        "cellular_process": "cellular",
        "clinical_phenotype": "observational",
        "dataset_requirement": "dataset",
        "methodological_context": "methodological",
    }.get(facet_type, "unknown")


def direct_answer_facet_type(question: str, task_mode: str) -> str:
    """Infer the scientific role of a generic direct-answer requirement."""

    if task_mode == "method_comparison" or contains_any(question, METHOD_QUERY_TERMS):
        return "methodological_context"
    if contains_any(question, CLINICAL_QUERY_TERMS):
        return "clinical_phenotype"
    if contains_any(question, CELLULAR_QUERY_TERMS):
        return "cellular_process"
    if contains_any(question, EXPRESSION_QUERY_TERMS):
        return "molecular_expression"
    return "methodological_context"


def direct_answer_aliases(question: str) -> list[str]:
    """Keep scientific content words and discard question-function words."""

    normalized = re.sub(r"[-_/]+", " ", question.lower())
    aliases = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9]+", normalized)
        if len(token) >= 3 and token not in DIRECT_ALIAS_STOPWORDS
    ]
    if "severity" in aliases:
        aliases.extend(["severe", "critical"])
    if "severe" in aliases or "critical" in aliases:
        aliases.append("severity")
    return list(dict.fromkeys(aliases))[:16]


def contains_any(question: str, terms: tuple[str, ...]) -> bool:
    normalized = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    return any(" ".join(re.findall(r"[a-z0-9]+", term)) in normalized for term in terms)


def context_variables(question: str) -> list[str]:
    variables = []
    for term in ["age", "sex", "parasite biomass", "parasite density", "site", "cohort", "stage"]:
        if term in question:
            variables.append(term)
    if "cerebral" in question or "severe" in question:
        variables.extend(["disease definition", "parasite biomass"])
    return list(dict.fromkeys(variables))


def safe_question_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return clean or "TRACE_QUESTION"
