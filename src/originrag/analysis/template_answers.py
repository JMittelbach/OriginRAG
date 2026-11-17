from __future__ import annotations

import re

from originrag.analysis.answer_qc import marker_positive_group_labels
from originrag.analysis.comparison_coverage import (
    comparison_text_has_null,
    direct_comparison_candidate_groups,
    direct_comparison_result_sentence,
    eligible_direct_comparison_candidate_chunks,
    explicit_evidence_strength_question,
)
from originrag.analysis.evidence_pack_grading import (
    epistemic_causal_comparison_question,
)
from originrag.trace.facets import infer_direct_evidence_roles


EVIDENCE_KEYWORDS = [
    "asymptomatic",
    "antibod",
    "breadth",
    "clinical",
    "immunity",
    "pfemp1",
    "protect",
    "recognition",
    "severe",
    "var ",
]

REPOSITORY_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:GSE\d+|PRJNA\d+|PRJEB\d+|ERP\d+|PXD\d+|MTBLS\d+)\b",
    flags=re.IGNORECASE,
)

_DATA_ACCESS_OBJECT_PATTERN = re.compile(
    r"\b(?:data|datasets?|files?|reads?|rna[- ]?seq|transcriptome|"
    r"daten|datens(?:atz|atze|ätze)|dateien|transkriptom(?:daten)?|"
    r"zugangsnummern?)\b",
    flags=re.IGNORECASE,
)
_DATA_ACCESS_ACTION_PATTERN = re.compile(
    r"\b(?:access(?:ible|ibility)?|available|availability|download(?:able|ed|ing)?|"
    r"obtain|reuse|reanaly[sz](?:e|ed|ing)|reanalysis|deposited|"
    r"zuganglich|zugänglich|verfugbar|verfügbar|herunterladen|abrufen|"
    r"zugreifen|beziehen|nachnutzen|reanaly(?:se|siert)|hinterlegt)\b",
    flags=re.IGNORECASE,
)
_DATA_ACCESSION_PATTERN = re.compile(
    r"\b(?:accessions?|zugangsnummern?|GSE\d+|PRJNA\d+|PRJEB\d+|ERP\d+|"
    r"PXD\d+|MTBLS\d+)\b",
    flags=re.IGNORECASE,
)
_DATA_REPOSITORY_PATTERN = re.compile(
    r"\b(?:repository|repositories|repositor(?:y|ies)|repositorium|datenbank)\b",
    flags=re.IGNORECASE,
)
_DATA_REPOSITORY_INTENT_PATTERN = re.compile(
    r"\b(?:which|what|where|find|located|deposited|available|"
    r"welch(?:e|er|es|em|en)|wo|hinterlegt|verfugbar|verfügbar)\b",
    flags=re.IGNORECASE,
)


def question_requests_data_accessibility(question: str) -> bool:
    """Return whether the user explicitly asks about obtaining or reusing data.

    Repository identifiers often occur incidentally in otherwise relevant evidence.
    Their presence must not turn a study-design or results question into a data-access
    inventory.
    """

    normalized = " ".join(str(question or "").split())
    if _DATA_ACCESSION_PATTERN.search(normalized):
        return True
    has_data_object = bool(_DATA_ACCESS_OBJECT_PATTERN.search(normalized))
    if has_data_object and _DATA_ACCESS_ACTION_PATTERN.search(normalized):
        return True
    return bool(
        has_data_object
        and _DATA_REPOSITORY_PATTERN.search(normalized)
        and _DATA_REPOSITORY_INTENT_PATTERN.search(normalized)
    )


def build_data_accessibility_answer(
    evidence_pack: dict, *, question: str | None = None
) -> str:
    """Return a conservative answer only for an explicit data-access request."""

    effective_question = (
        str(evidence_pack.get("question") or "") if question is None else question
    )
    if not question_requests_data_accessibility(effective_question):
        return ""
    records = []
    for chunk in evidence_pack.get("chunks", []):
        identifiers = list(
            dict.fromkeys(
                match.group(0).upper()
                for match in REPOSITORY_IDENTIFIER_PATTERN.finditer(
                    str(chunk.get("text") or "")
                )
            )
        )
        if not identifiers:
            continue
        records.append((chunk, identifiers))
    if not records:
        return ""

    statements = []
    references = []
    for chunk, identifiers in records:
        evidence_id = str(chunk.get("evidence_id") or "")
        author_year = str(chunk.get("citation_short") or "").strip()
        label = author_year or str(chunk.get("title") or chunk.get("paper_id") or "Source")
        statements.append(
            f"{label} reports deposited data under {', '.join(identifiers)} ({evidence_id})."
        )
        citation = str(chunk.get("citation_label") or "").strip()
        title = str(chunk.get("title") or "").strip()
        doi = str(chunk.get("doi") or "").strip()
        reference = f"- {evidence_id}: {citation} {title}".rstrip()
        if doi:
            reference += f" https://doi.org/{doi}"
        references.append(reference)

    return "\n\n".join(
        [
            "## Evidence-backed statements\n" + " ".join(statements),
            "## Cautious inference\nRepository deposition supports data access, but it does "
            "not by itself prove that every required patient-level phenotype or data dictionary "
            "is present.",
            "## Limitations / uncertainty\nThe listed records must still be checked for sample-level "
            "clinical variables, identifiers, formats, and reuse restrictions before reanalysis.",
            "## Missing evidence\nThe retrieved passages do not establish a harmonized metadata "
            "schema or equal reanalysis readiness across these datasets.",
            "## References\n" + "\n".join(references),
        ]
    )

TRANSCRIPT_CLINICAL_PROFILE = {
    "name": "parasite transcription and clinical malaria syndromes",
    "tokens": ["parasite", "transcript", "expression", "clinical", "syndrome", "gene"],
    "prediction": (
        "Reported parasite-expression associations will differ after harmonizing parasite stage, "
        "assay platform, and syndrome definitions"
    ),
    "required_data": (
        "Parasite RNA measurements and granular clinical phenotypes from the same patients, "
        "with parasite-stage, biomass, age, geography, and assay metadata."
    ),
    "analysis": [
        "Compare syndrome-specific associations after correcting for parasite developmental stage and biomass.",
        "Harmonize clinical phenotype definitions and expression measurements across study platforms.",
        "Test whether reported transcript or domain-level associations replicate in an independent matched cohort.",
    ],
    "gap": (
        "Syndrome-associated signals are reported, but their functional meaning, cross-platform comparability, "
        "and replication after stage and cohort adjustment remain unresolved."
    ),
    "question": (
        "Which reported parasite-expression associations remain syndrome-specific after harmonized "
        "phenotyping and parasite-stage adjustment?"
    ),
}


TOPIC_PROFILES = [
    TRANSCRIPT_CLINICAL_PROFILE,
    {
        "name": "antimalarial resistance, diagnostic escape, and surveillance",
        "tokens": [
            "resistance",
            "artemisinin",
            "diagnostic",
            "surveillance",
            "nanopore",
            "genomic",
            "pfhrp",
            "drug",
        ],
        "prediction": (
            "Molecular surveillance features will identify settings where resistance "
            "or diagnostic escape signals are present before they are visible in routine case data"
        ),
        "required_data": (
            "Matched treatment, diagnostic, molecular-marker, genomic-surveillance, "
            "location, and time-series data from the same transmission settings."
        ),
        "analysis": [
            "Link molecular markers, diagnostic outcomes, and geospatial surveillance metadata.",
            "Model whether genomic or diagnostic escape signals precede changes in clinical or routine surveillance indicators.",
            "Check robustness by stratifying by location, year, assay type, and treatment policy.",
        ],
        "gap": (
            "Resistance, diagnostic escape, and surveillance signals are often measured "
            "in separate workflows rather than integrated in one evaluation framework."
        ),
        "question": (
            "Do molecular resistance and diagnostic escape markers improve prediction of "
            "surveillance changes beyond routine case and prevalence data?"
        ),
    },
    {
        "name": "sex differences, host immunity, and malaria outcomes",
        "tokens": [
            "sex",
            "female",
            "male",
            "gender",
            "testosterone",
            "estrogen",
            "host immunity",
            "immune",
            "severe",
        ],
        "prediction": (
            "Biological sex will modify immune state associations with parasite burden "
            "or severe malaria phenotype"
        ),
        "required_data": (
            "Matched sex, age, clinical phenotype, parasite burden, immune marker, "
            "and exposure data from the same cohort or harmonized cohorts."
        ),
        "analysis": [
            "Stratify outcomes by sex, age group, exposure level, and disease severity.",
            "Test sex by immune marker interactions instead of using sex only as an adjustment covariate.",
            "Compare whether the same immune markers associate with protection or pathology in males and females.",
        ],
        "gap": (
            "Sex specific immune effects are not consistently integrated with severe malaria "
            "phenotyping and parasite measurements."
        ),
        "question": (
            "Which immune markers have different associations with severe malaria outcomes "
            "in males versus females after accounting for age and exposure?"
        ),
    },
    {
        "name": "PfEMP1, var expression, asymptomatic infection, and severe malaria",
        "tokens": [
            "pfemp1",
            "var",
            "asymptomatic",
            "antibody",
            "breadth",
            "vsa",
            "epcr",
            "cd36",
            "severe",
        ],
        "prediction": (
            "Broader PfEMP1 or VSA antibody recognition will associate with asymptomatic "
            "carriage and lower severe malaria risk after exposure adjustment"
        ),
        "required_data": (
            "Matched clinical phenotype, age, exposure history, PfEMP1 antibody breadth, "
            "parasite density, and var/PfEMP1 expression data from the same individuals."
        ),
        "analysis": [
            "Stratify samples by asymptomatic, uncomplicated, and severe malaria phenotype.",
            "Quantify PfEMP1 antibody breadth and var/PfEMP1 expression per individual.",
            "Model phenotype as a function of antibody breadth and var/PfEMP1 expression while adjusting for age and exposure.",
        ],
        "gap": (
            "Causal direction between PfEMP1 antibody breadth, parasite antigenic expression, "
            "and protection remains unresolved."
        ),
        "question": (
            "Does PfEMP1 antibody breadth predict asymptomatic carriage or reduced severe malaria "
            "risk after adjustment for age and exposure?"
        ),
    },
]


def build_template_answer(question: str, evidence_pack: dict, task: str) -> str:
    normalized_task = (task or "answer").strip().lower()
    if normalized_task == "hypothesis":
        return build_hypothesis_template(question, evidence_pack)
    if normalized_task == "gaps":
        return build_gaps_template(question, evidence_pack)
    if normalized_task == "sources":
        return build_sources_template(question, evidence_pack)
    return build_answer_template(question, evidence_pack)


def build_hypothesis_template(question: str, evidence_pack: dict) -> str:
    chunks = list(evidence_pack.get("chunks", []))
    key_ids = [chunk.get("evidence_id", "") for chunk in chunks[:4] if chunk.get("evidence_id")]
    cited = ", ".join(key_ids) or "retrieved evidence"
    profile = infer_topic_profile(question, chunks)
    title = hypothesis_title(question)
    statements = "\n".join(f"- {format_cited_chunk_summary(chunk)}" for chunk in chunks[:5])
    references = "\n".join(
        f"- {chunk.get('evidence_id')}: {chunk.get('citation_label')}."
        for chunk in chunks[:6]
    )
    return f"""## Hypothesis Card

### hypothesis_id
TEMP_HYP_001

### title
{title}

### short_summary
The retrieved evidence supports a cautious hypothesis about {profile["name"]}. This should be treated as a testable working model, not as established causality ({cited}).

### evidence_backed_statements
{statements or "- Not supported by retrieved evidence."}

### inference
- If these evidence strands are considered together, they suggest a measurable relationship among the concepts in the question rather than a settled causal mechanism ({cited}).
- Working prediction: {profile["prediction"]}. Confounding and measurement differences must be tested directly ({cited}).

### speculation
- It remains speculative whether this relationship is causal rather than reflecting exposure history, sampling design, geography, age, clinical phenotype, or assay differences ({cited}).

### testable_prediction
- {profile["prediction"]} ({cited}).
- The association should weaken or change direction if the apparent signal is mainly driven by confounding, duplicate measurement of the same phenomenon, or study design differences ({cited}).

### required_data
- {profile["required_data"]}

### analysis_plan
{format_bullets(profile["analysis"])}
- Repeat the analysis with sensitivity checks using only the strongest matched records.

### risk_plan_b
- If the main association is weak, test whether it mainly captures age, geography, exposure, assay platform, or cohort design.
- If measurements are too heterogeneous, restrict analysis to studies with comparable metadata and clearly matched samples.

### novelty
Not supported by retrieved evidence. Novelty must be checked against broader literature before claiming it.

### impact
If supported, this hypothesis could turn a broad literature pattern into a testable prioritization rule for malaria research.

### key_references
{references or "- Not supported by retrieved evidence."}
"""


def build_gaps_template(question: str, evidence_pack: dict) -> str:
    chunks = list(evidence_pack.get("chunks", []))
    profile = infer_topic_profile(question, chunks)
    if profile["name"] == "sex differences, host immunity, and malaria outcomes":
        return build_sex_difference_gaps_template(question, chunks)

    if profile is TRANSCRIPT_CLINICAL_PROFILE:
        relevant_chunks = [
            chunk
            for chunk in chunks
            if any(
                marker in " ".join(
                    str(chunk.get(field) or "") for field in ("title", "text")
                ).lower()
                for marker in (
                    "parasite transcript",
                    "schizont stage transcriptome",
                    "var gene",
                    "pfemp1",
                    "mitochondria-associated gene",
                )
            )
        ][:5]
        chunks = relevant_chunks or chunks
    key_ids = [chunk.get("evidence_id", "") for chunk in chunks[:5] if chunk.get("evidence_id")]
    cited = ", ".join(key_ids) or "retrieved evidence"
    references = "\n".join(
        f"- {chunk.get('evidence_id')}: {chunk.get('citation_label')}."
        for chunk in chunks[:5]
    )
    return f"""## Short Answer
The retrieved evidence suggests a research gap around {profile["name"]}. The main issue is not that no papers exist, but that the measurements needed to connect mechanisms, phenotypes, and outcomes are not yet integrated in a controlled way ({cited}).

## Supported Research Gaps
- gap: {profile["gap"]}
  why it matters: It determines whether the observed pattern can support a testable hypothesis or is only a broad literature association.
  supporting evidence labels: {cited}
  what evidence is still missing: Matched longitudinal datasets or carefully paired sample sets that measure the relevant variables in the same samples.
- gap: Retrieved sources tend to emphasize different parts of the problem rather than a single linked analysis.
  why it matters: Without paired measurements, mechanisms remain indirect and hard to prioritize for later testing.
  supporting evidence labels: {cited}
  what evidence is still missing: Harmonized metadata, shared outcomes, and comparable assay definitions across the cited papers.

## Candidate Testable Questions
- {profile["question"]}
- Which part of the relationship remains after adjusting for age, exposure, geography, phenotype definitions, and assay platform?
- Are the same signals visible when analysis is restricted to the strongest matched evidence rather than broad literature overlap?

## Best Next Data / Analysis
{format_bullets(profile["analysis"])}
- Build a small evidence table from the cited papers before treating this as a strong hypothesis.

## Key Sources
{references or "- Not supported by retrieved evidence."}
"""


def build_sex_difference_gaps_template(question: str, chunks: list[dict]) -> str:
    cited = evidence_ids(chunks[:8])
    references = format_references(chunks[:8])
    evidence_table = format_evidence_table(chunks[:8])
    return f"""## Short Answer
The strongest retrieved gap is not simply "sex differences in malaria"; it is the missing bridge between sex-disaggregated epidemiology, biological sex mechanisms, immune readouts, parasite-stage measurements, and severe-malaria phenotypes ({cited}). Current evidence points to real signals, but the signals are fragmented across study types rather than tested in one matched design ({cited}).

## Supported Research Gaps
- gap: Sex-disaggregated malaria patterns are reported, but biological mechanisms remain under-tested in matched human datasets.
  why it matters: A sex difference in burden or parasite positivity is not enough to identify whether immunity, exposure, parasite biology, or care-seeking behavior explains the difference.
  supporting evidence labels: {evidence_ids(chunks[:5])}
  what evidence is still missing: Matched sex, age, exposure, parasite density, immune marker, and clinical severity measurements from the same individuals.
- gap: Liver-stage duration, parasite replication, parasite density, and severe-malaria outcomes are not yet integrated as one sex-stratified causal chain.
  why it matters: Without stage-specific and outcome-specific measurements, a sex effect can look biological while actually reflecting exposure, age, or phenotype definitions.
  supporting evidence labels: {evidence_ids(chunks[:6])}
  what evidence is still missing: Studies that connect liver-stage or parasite-growth measures to downstream immunity and clinical severity by sex.
- gap: Sex and gender are often mixed conceptually, while biological and social explanations require different analyses.
  why it matters: Sex-specific immune biology and gender-linked exposure or access-to-care effects can point in different directions.
  supporting evidence labels: {evidence_ids(chunks[:8])}
  what evidence is still missing: Explicit separation of biological sex variables from gendered exposure, behavior, care-seeking, and diagnostic-access variables.
- gap: Mechanistic immune findings are not consistently connected to human severe-malaria phenotypes.
  why it matters: Immune dimorphism may be real, but it is not yet enough to prioritize a hypothesis unless it predicts severe, uncomplicated, or asymptomatic outcomes.
  supporting evidence labels: {evidence_ids(chunks[:8])}
  what evidence is still missing: Cohorts with sex-stratified immune assays, parasite measures, and harmonized severe-malaria endpoints.

## Candidate Testable Questions
- Do sex by immune-marker interactions predict severe malaria after adjusting for age, exposure, and parasite density?
- Are male-biased parasite positivity patterns explained by exposure and care-seeking, or do they persist after matched biological adjustment?
- Does estrogen-linked or gonadal-steroid-linked immune modulation predict parasite clearance, parasite density, or severe outcomes in human datasets?
- Are sex effects stronger at specific parasite stages or clinical phenotypes rather than across malaria generally?
- Which studies confuse sex and gender, and does separating them change the inferred research gap?

## Best Next Data / Analysis
- Build an evidence table with columns for sex variable, gender variable, age, exposure, parasite density, immune readout, clinical phenotype, and assay platform.
- Re-analyze any available cohort with sex by immune-marker interaction terms rather than using sex only as a covariate.
- Stratify by age group, exposure intensity, pregnancy status where relevant, and severe-malaria phenotype.
- Compare biological outcomes separately from care-seeking or facility-attendance outcomes.
- Treat rodent or mechanistic evidence as hypothesis-generating unless the same immune readout is available in human malaria data.

## Evidence Map
{evidence_table}

## Key Sources
{references or "- Not supported by retrieved evidence."}
"""


def build_answer_template(question: str, evidence_pack: dict) -> str:
    chunks = list(evidence_pack.get("chunks", []))
    overlap_answer = build_explicit_publication_overlap_answer(question, chunks, evidence_pack)
    if overlap_answer:
        return overlap_answer
    marker_state_answer = build_marker_positive_state_answer(question, chunks)
    if marker_state_answer:
        return marker_state_answer
    direct_comparison_answer = build_direct_comparison_strength_answer(
        question, evidence_pack
    )
    if direct_comparison_answer:
        return direct_comparison_answer
    has_explicit_named_study = any(
        chunk.get("explicit_named_study_anchor")
        or chunk.get("explicit_named_study_support")
        for chunk in chunks
    )
    if not has_explicit_named_study and is_cerebral_malaria_gene_question(question):
        specialized = build_cerebral_malaria_gene_answer(question, chunks, evidence_pack)
        if specialized:
            return specialized
    # Resolve restricted scopes before dropping unreadable excerpts: an empty
    # named/bounded scope must not fall through to unrelated context papers.
    scoped_chunks = bounded_comparison_citation_chunks(evidence_pack, chunks)
    if not scoped_chunks and has_explicit_named_study:
        scoped_chunks = select_fallback_citation_chunks(chunks, question=question)
    candidates = scoped_chunks if scoped_chunks or has_explicit_named_study else chunks
    excerpts = {
        id(chunk): complete_fallback_excerpt(str(chunk.get("text") or ""))
        for chunk in candidates
    }
    readable_chunks = [
        chunk for chunk in candidates
        if str(chunk.get("evidence_id") or "").strip() and excerpts[id(chunk)]
    ]
    cited_chunks = (
        readable_chunks if scoped_chunks or has_explicit_named_study
        else select_fallback_citation_chunks(
            readable_chunks,
            question=question,
            evidence_pack={**evidence_pack, "chunks": readable_chunks},
        )
    )
    summaries = [
        f"{chunk['evidence_id']}{chunk_locator(chunk)} reports that {excerpts[id(chunk)]}"
        for chunk in cited_chunks
    ]
    statements = "\n".join(f"- {summary}" for summary in summaries) if wants_list_answer(question) else " ".join(summaries)
    references = "\n".join(
        f"- {chunk.get('evidence_id')}: {chunk.get('citation_label')}."
        for chunk in cited_chunks
    )
    german = is_german_question(question)
    answer = statements or ("Nicht durch die abgerufene Evidenz gestützt." if german else "Not supported by retrieved evidence.")
    if statements:
        answer = (
            "Quellenauszüge (keine Synthese):\n" if german
            else "Source excerpts (not a synthesis):\n"
        ) + answer
    cautious = (
        "Die abgerufenen Arbeiten werden nur durch Quellenauszüge wiedergegeben; "
        "dieser Fallback liefert keine zusammenfassende Antwort auf die Frage."
        if german
        else "The retrieved papers are represented by source excerpts only; "
        "this fallback does not synthesize an answer to the question."
    )
    if epistemic_causal_comparison_question(question) and len(cited_chunks) == 2:
        cited_ids = "; ".join(
            str(chunk.get("evidence_id") or "") for chunk in cited_chunks
        )
        cautious = (
            "Neither paper demonstrates that the mechanism in the question itself "
            f"causes the requested clinical outcome ({cited_ids}). This bounded "
            "evidence limit does not prove that a causal relationship is absent."
        )
    limitations = (
        "Der Fallback wählt vollständige Quellensätze aus, bewertet aber nicht, "
        "wie gut sie die Frage beantworten."
        if german
        else "The fallback selects complete source sentences but does not assess "
        "how well they answer the question."
    )
    missing = (
        "Dieser Fallback hat nicht geprüft, ob zusätzliche Evidenz benötigt wird."
        if german
        else "This fallback has not assessed whether additional evidence is needed."
    )
    return f"""## Evidence-backed statements
{answer}

## Cautious inference
{cautious}

## Limitations / uncertainty
{limitations}

## Missing evidence
{missing}

## References
{references or "- Not supported by retrieved evidence."}
"""


def build_direct_comparison_strength_answer(question: str, evidence_pack: dict) -> str:
    """Summarize exact two-group results without padding with topical papers."""
    if not explicit_evidence_strength_question(question):
        return ""

    candidates = eligible_direct_comparison_candidate_chunks(question, evidence_pack)
    groups = direct_comparison_candidate_groups(question, evidence_pack, strict=True)
    if not candidates or not 1 <= len(groups) <= 6:
        return ""

    by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in candidates
        if str(chunk.get("evidence_id") or "")
    }
    selected = []
    for group in groups:
        selected_chunk = next(
            (
                by_id[evidence_id]
                for evidence_id in group.get("evidence_ids", [])
                if evidence_id in by_id
            ),
            None,
        )
        if selected_chunk is not None:
            selected.append(selected_chunk)
    if not selected:
        return ""

    result_lines = []
    result_sentences = []
    for chunk in selected:
        evidence_id = str(chunk.get("evidence_id") or "")
        result = direct_comparison_result_sentence(question, chunk)
        if not evidence_id or not result:
            continue
        result_sentences.append(result)
        source_type = str(
            (chunk.get("source_type_assessment") or {}).get("source_type") or ""
        )
        role_prefix = (
            "In a secondary analysis, "
            if source_type == "reanalysis_secondary_data"
            else ""
        )
        result_lines.append(
            f"- {role_prefix}{evidence_id}{chunk_locator(chunk)} reports: "
            f"{third_person_result_sentence(result)}"
        )
    if not result_lines:
        return ""

    has_null = any(comparison_text_has_null(result) for result in result_sentences)
    if has_null and len(result_sentences) > 1:
        inference = (
            "The report-level evidence is mixed: at least one direct comparison is "
            "null, while other reports describe directional differences. It supports "
            "a cautious descriptive pattern, not one uniform change across studies."
        )
    else:
        inference = (
            "The direct comparisons support a report-level descriptive pattern, but "
            "they do not by themselves establish one uniform change across studies."
        )

    report_count = len(selected)
    count_label = number_word(report_count).capitalize()
    limitations = (
        f"{count_label} cited reports are not automatically {number_word(report_count)} "
        "independent studies. Publication-family, cohort, and dataset relations have "
        "not been fully reviewed, so the independent study-origin count remains "
        "unresolved."
    )
    missing = (
        "A stronger conclusion requires comparable assays and group definitions, "
        "adjustment for major measured confounders, and reviewed links among "
        "publication records, cohorts, and reused datasets."
    )
    return f"""## Evidence-backed statements
{chr(10).join(result_lines)}

## Cautious inference
{inference}

## Limitations / uncertainty
{limitations}

## Missing evidence
{missing}

## References
{format_references(selected)}
"""


def third_person_result_sentence(sentence: str) -> str:
    """Make an extracted source sentence grammatical after a report label."""
    result = clean_evidence_text(sentence)
    result = re.sub(
        r"^Nevertheless,\s+we\s+could\b.*?\band\s+found\b",
        "The authors found",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"^Here,\s+we\s+further\s+show\s+that\s+",
        "",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"\bwe\b", "the authors", result, flags=re.IGNORECASE)
    result = re.sub(r"\bour\b", "the authors'", result, flags=re.IGNORECASE)
    return result if result.endswith((".", "!", "?")) else f"{result}."


def build_marker_positive_state_answer(question: str, chunks: list[dict]) -> str:
    """Build a bounded answer from explicit marker-bearing cell-state labels.

    Generative models can accidentally spread a shared ``MARKER-positive`` heading
    over adjacent contrast groups.  For an explicit "which marker-positive states"
    question, the evidence already contains the safer answer: retain only labels
    that carry the marker themselves and keep their group/state number attached.
    """

    normalized_question = " ".join(str(question or "").split())
    marker_match = re.search(
        r"(?P<marker>[A-Za-z][A-Za-z0-9]{1,15})"
        r"(?:\+|[- ]positive|[- ]positiv(?:e|en|er|es)?)",
        normalized_question,
        flags=re.IGNORECASE,
    )
    asks_for_states = bool(
        re.search(
            r"\b(?:states?|groups?|subtypes?|clusters?|signatures?|cells?|"
            r"zustande|zustände|gruppen?|subtypen?|signaturen?|zellen?)\b",
            normalized_question,
            flags=re.IGNORECASE,
        )
    )
    if not marker_match or not asks_for_states:
        return ""

    marker = marker_match.group("marker")
    label_sources: dict[str, list[dict]] = {}
    for chunk in chunks:
        for label in marker_positive_group_labels(str(chunk.get("text") or ""), marker):
            label_sources.setdefault(label, []).append(chunk)
    if not label_sources:
        return ""

    selected = unique_chunks(
        [source for sources in label_sources.values() for source in sources]
    )
    labels = list(label_sources)
    label_text = join_readable(labels)
    source_label = source_description(selected)
    report_verb = "reports" if len(selected) == 1 else "report"
    citations = inline_citations(selected)
    severity_statements = [
        statement
        for chunk in selected
        if (statement := marker_severity_statement(marker, chunk))
    ]
    severity_text = " ".join(dict.fromkeys(severity_statements))
    if not severity_text:
        severity_text = (
            f"The retrieved passage names these {marker}-positive states but does not "
            f"provide a direct severity comparison {citations}."
        )

    count_word = number_word(len(labels))
    return f"""## Evidence-backed statements
{source_label} {report_verb} {count_word} explicitly {marker}-positive states: {label_text} {citations}. {severity_text}

## Cautious inference
The retrieved evidence supports a within-study association between the marker-defined states and the reported clinical severity groups {citations}.

## Limitations / uncertainty
This is a descriptive comparison from {number_word(len(selected))} directly reporting paper{'s' if len(selected) != 1 else ''}; it does not establish temporal order or independent replication.

## Missing evidence
The retrieved passages do not provide longitudinal or perturbation evidence, and the publication origins require separate checking before counting replications.

## References
{format_references(selected)}
"""


def marker_severity_statement(marker: str, chunk: dict) -> str:
    """Return a conservative marker/severity comparison when it is explicit."""

    evidence_id = str(chunk.get("evidence_id") or "")
    text = clean_evidence_text(str(chunk.get("text") or ""))
    marker_pattern = re.escape(marker)
    for sentence in split_sentences(text) or [text]:
        lowered = sentence.lower()
        if not re.search(rf"\b{marker_pattern}\b", sentence, flags=re.IGNORECASE):
            continue
        if not re.search(r"\b(?:severe|critical|severity)\b", lowered):
            continue
        if not re.search(
            r"\b(?:express(?:ed|ion)|higher|elevated|enriched|associated)\b",
            lowered,
        ):
            continue
        citation = f" ({evidence_id})" if evidence_id else ""
        if "severe/critical" in lowered and "moderate" in lowered:
            return (
                f"In the same paper, {marker} was more highly expressed in "
                f"severe/critical than moderate infection{citation}."
            )
        if "severe/critical" in lowered:
            return (
                f"In the same paper, {marker} was reported as highly expressed in "
                f"severe/critical infection{citation}."
            )
        return (
            f"The same paper reports a severity-associated difference in {marker} "
            f"expression{citation}."
        )
    return ""


def join_readable(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def source_description(chunks: list[dict]) -> str:
    if len(chunks) != 1:
        return "The directly reporting papers"
    chunk = chunks[0]
    citation = str(chunk.get("citation_short") or "").strip()
    if citation:
        citation = re.sub(r",\s*(\d{4}[a-z]?)$", r" (\1)", citation)
        return f"The directly reporting paper by {citation}"
    author = str(chunk.get("author") or "").strip()
    year = str(chunk.get("year") or "").strip()
    if author:
        return f"The directly reporting paper by {author}{f' ({year})' if year else ''}"
    return "The directly reporting paper"


def number_word(value: int) -> str:
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    return words.get(value, str(value))


def build_explicit_publication_overlap_answer(
    question: str,
    chunks: list[dict],
    evidence_pack: dict,
) -> str:
    """Answer an explicit two-report overlap query from deterministic signals."""
    dois = {
        value.rstrip(".,;:)]}").lower()
        for value in re.findall(r"10\.\d{4,9}/[^\s<>\"]+", str(question), flags=re.IGNORECASE)
    }
    if len(dois) < 2 or not any(
        marker in question.lower()
        for marker in ("independent", "preprint", "same work", "same study", "unabhängig")
    ):
        return ""

    by_paper: dict[str, dict] = {}
    for chunk in chunks:
        paper_id = str(chunk.get("paper_id") or "")
        chunk_doi = str(chunk.get("doi") or "").removeprefix("https://doi.org/").lower()
        if paper_id and chunk_doi in dois and paper_id not in by_paper:
            by_paper[paper_id] = chunk

    supported_signals = []
    for signal in evidence_pack.get("automatic_lineage_signals", []):
        pair = {str(signal.get("paper_id_a") or ""), str(signal.get("paper_id_b") or "")}
        if len(pair) == 2 and pair.issubset(by_paper):
            supported_signals.append(signal)
    if not supported_signals:
        return ""

    selected = [by_paper[paper_id] for paper_id in sorted(by_paper)]
    citations = inline_citations(selected)
    signal_types = {str(signal.get("signal_type") or "") for signal in supported_signals}
    reasons = []
    if "same_preprint_family" in signal_types:
        reasons.append("matching publication title and preprint/journal metadata")
    if "same_dataset_accession" in signal_types:
        shared = sorted(
            {
                str(value)
                for signal in supported_signals
                if signal.get("signal_type") == "same_dataset_accession"
                for value in (signal.get("details") or {}).get("shared", [])
            }
        )
        reasons.append(
            f"the shared dataset accession {', '.join(shared)}" if shared else "a shared dataset accession"
        )
    reason_text = " and ".join(reasons) or "matching publication metadata"
    return f"""## Evidence-backed statements
OriginRAG flags these two reports as the preprint and journal version of the same publication family, based on {reason_text} {citations}.

## Cautious inference
Count them as at most one publication-family unit, not as two independent pieces of evidence.

## Limitations / uncertainty
This is an automatic metadata-based overlap signal. It establishes one publication family; cohort and dataset relations remain unresolved at finer granularity.

## Missing evidence
Cohort- and sample-level origin metadata are still required before reporting an exact independent-study count.

## References
{format_references(selected)}
"""


def is_cerebral_malaria_gene_question(question: str) -> bool:
    normalized = " ".join(str(question or "").lower().split())
    return "cerebral malaria" in normalized and any(
        token in normalized for token in ("gene", "genes", "transcript", "pfemp1", "var")
    )


def build_cerebral_malaria_gene_answer(
    question: str,
    chunks: list[dict],
    evidence_pack: dict,
) -> str:
    """Safe fallback for a central OriginRAG use case when the LLM is unavailable."""

    relevant_chunks = [
        chunk
        for chunk in chunks
        if re.search(
            r"\b(?:cerebral|severe) malaria\b|\bbrain swelling\b",
            _chunk_searchable_text(chunk),
            flags=re.IGNORECASE,
        )
        and re.search(
            r"\b(?:epcr|endothelial protein c receptor|pfemp1|var genes?|dc8|dc13|"
            r"group a|b/a|icam[- ]?1)\b",
            _chunk_searchable_text(chunk),
            flags=re.IGNORECASE,
        )
    ]
    clinical_chunks: list[dict] = []
    binding_chunks: list[dict] = []
    icam_null_chunks: list[dict] = []
    for chunk in relevant_chunks:
        title = str(chunk.get("title") or "")
        text = str(chunk.get("text") or "")
        section = str(chunk.get("section") or "")
        source_type = chunk.get("source_type_assessment") or chunk.get("source_type") or ""
        roles = infer_direct_evidence_roles(
            title=title,
            text=text,
            section=section,
            source_type=source_type,
        )
        excerpt_roles = infer_direct_evidence_roles(
            title="",
            text=text,
            section=section,
            source_type=source_type,
        )
        if _is_direct_primary_icam_null_result(chunk):
            icam_null_chunks.append(chunk)
            continue
        if _has_negative_clinical_direction(chunk):


            continue



        binding_excerpt_only = (
            "functional_binding" in excerpt_roles
            and "clinical_association" not in excerpt_roles
        )
        if "clinical_association" in roles and not binding_excerpt_only:
            clinical_chunks.append(chunk)
        elif "functional_binding" in roles:
            binding_chunks.append(chunk)

    transcriptome_chunks = matching_chunks(
        chunks,
        required=(r"\bcerebral malaria\b", r"\btranscriptome|gene expression"),
        excluded=(r"\bnot associated\b", r"\bno association\b"),
        limit=1,
    )
    meta_chunks = matching_chunks(
        chunks,
        required=(r"\bmeta[- ]anal", r"\bvar\b|pfemp1"),
        limit=1,
    )
    clinical_chunks = unique_chunks(clinical_chunks)[:4]
    binding_chunks = unique_chunks(binding_chunks)[:4]
    icam_null_chunks = unique_chunks(icam_null_chunks)[:1]
    selected = unique_chunks(
        clinical_chunks
        + binding_chunks
        + icam_null_chunks
        + transcriptome_chunks
        + meta_chunks
    )[:10]
    if len(selected) < 2:
        return ""

    clinical_cite = inline_citations(clinical_chunks)
    binding_cite = inline_citations(binding_chunks)
    transcriptome_cite = inline_citations(transcriptome_chunks)
    icam_null_cite = inline_citations(icam_null_chunks)
    meta_cite = inline_citations(meta_chunks)
    lineage_note = (
        " Report count is not independent-study count; study origins remain unverified."
        if evidence_pack.get("provisional_synthesis_authorized")
        else ""
    )
    references = format_references(selected)
    normalized_question = " ".join(str(question or "").lower().split())
    asks_for_gene_list = any(
        marker in normalized_question
        for marker in (
            "core gene",
            "individual gene",
            "which gene",
            "what gene",
            "gene list",
            "list of genes",
            "universal list",
        )
    )
    scope_sentence = (
        "There is no supported universal list of individual “core genes” for cerebral malaria. "
        if asks_for_gene_list
        else ""
    )
    return f"""## Evidence-backed statements
{scope_sentence}{f'Clinical studies associate EPCR-binding PfEMP1 expression or profiles with severe or cerebral malaria {clinical_cite}.' if clinical_cite else ''}{f' Separate functional binding or endothelial-cell experiments support receptor engagement or adhesion; these experiments do not by themselves establish a clinical association {binding_cite}.' if binding_cite else ''}{f' A cerebral-malaria transcriptome study reports a broader expression signal {transcriptome_cite}.' if transcriptome_cite else ''}{f' A distinct null result concerns A-type ICAM-1-binding domains, not EPCR-binding variants: those ICAM-1-binding domains were not associated with cerebral malaria in that cohort {icam_null_cite}.' if icam_null_cite else ''}{f' A meta-analysis also reports increased transcription of EPCR-binding variants {meta_cite}; this is secondary synthesis, not a separate cohort.' if meta_cite else ''}

## Cautious inference
The supported candidate set is at the family or domain level: Group A or B/A `var`, DC8/DC13, and dual-receptor or EPCR-binding PfEMP1.{lineage_note}

## Limitations / uncertainty
The studies measure different endpoints, and association does not establish causality.

## Missing evidence
Independent replication requires reviewed links between publication families, cohorts, and reused datasets.

## References
{references}
"""


def _chunk_searchable_text(chunk: dict) -> str:
    return " ".join(str(chunk.get(field) or "") for field in ("title", "text", "section"))


def _is_direct_primary_icam_null_result(chunk: dict) -> bool:
    text = str(chunk.get("text") or "")
    searchable = _chunk_searchable_text(chunk)
    section = str(chunk.get("section") or "").strip().lower()
    source_type = chunk.get("source_type_assessment") or chunk.get("source_type") or ""
    if isinstance(source_type, dict):
        source_type = source_type.get("source_type") or ""
    normalized_source_type = " ".join(
        re.findall(r"[a-z0-9]+", str(source_type).lower())
    )
    if normalized_source_type != "primary research":
        return False
    own_result_text = bool(
        re.search(
            r"\b(?:we (?:found|observed|report|show)|our (?:study|data|results)|"
            r"this study|data (?:show|indicate)|results? (?:show|indicate)|"
            r"no (?:statistically )?significant difference (?:was|were) found|"
            r"no difference (?:was|were) found)\b",
            text,
            flags=re.IGNORECASE,
        )
        or (
            section in {"results", "abstract"}
            and not re.search(r"\bet al\b|\b(?:19|20)[0-9]{2}\b", text, flags=re.IGNORECASE)
        )
    )
    return bool(
        own_result_text
        and re.search(r"\bicam[- ]?1(?:-binding)?\b", text, flags=re.IGNORECASE)
        and re.search(
            r"\b(?:not associated|no association|no (?:statistically )?significant "
            r"(?:association|difference)|no difference)\b",
            text,
            flags=re.IGNORECASE,
        )
        and re.search(r"\bcerebral malaria\b", searchable, flags=re.IGNORECASE)
    )


def _has_negative_clinical_direction(chunk: dict) -> bool:
    polarity = str(chunk.get("trace_polarity") or "").strip().lower()
    if polarity in {"contradiction", "null_result", "limitation"}:
        return True
    searchable = _chunk_searchable_text(chunk)
    return bool(
        re.search(
            r"\b(?:not associated|no association|lack(?:ed|s|ing)? (?:of )?association|"
            r"failed to (?:associate|correlate)|no (?:statistically )?significant "
            r"(?:association|correlation|difference|increase|elevation|enrichment)|"
            r"no difference|did not (?:differ|increase|correlate|associate)|"
            r"(?:was|were|is|are) not (?:associated|correlated|increased|elevated|"
            r"enriched|higher)|lower|decreased|reduced)\b",
            searchable,
            flags=re.IGNORECASE,
        )
    )


def first_matching_chunk(
    chunks: list[dict],
    required: tuple[str, ...],
    excluded: tuple[str, ...] = (),
) -> dict | None:
    for chunk in chunks:
        searchable = " ".join(
            str(chunk.get(field) or "") for field in ("title", "text", "section")
        ).lower()
        if any(re.search(pattern, searchable, flags=re.IGNORECASE) for pattern in excluded):
            continue
        if all(re.search(pattern, searchable, flags=re.IGNORECASE) for pattern in required):
            return chunk
    return None


def matching_chunks(
    chunks: list[dict],
    required: tuple[str, ...],
    excluded: tuple[str, ...] = (),
    *,
    required_title: str | None = None,
    limit: int = 3,
) -> list[dict]:
    """Return distinct matching papers while preserving retrieval order."""
    matches = []
    seen_papers = set()
    for chunk in chunks:
        title = str(chunk.get("title") or "").lower()
        searchable = " ".join(
            str(chunk.get(field) or "") for field in ("title", "text", "section")
        ).lower()
        if any(re.search(pattern, searchable, flags=re.IGNORECASE) for pattern in excluded):
            continue
        if not all(re.search(pattern, searchable, flags=re.IGNORECASE) for pattern in required):
            continue
        if required_title and not re.search(required_title, title, flags=re.IGNORECASE):
            continue
        paper_id = str(chunk.get("paper_id") or chunk.get("evidence_id") or "")
        if not paper_id or paper_id in seen_papers:
            continue
        seen_papers.add(paper_id)
        matches.append(chunk)
    source_priority = {
        "primary_research": 0,
        "uncertain": 1,
        "reanalysis_secondary_data": 2,
        "systematic_review_meta_analysis": 3,
        "narrative_review": 3,
        "editorial_commentary": 3,
        "protocol": 4,
    }
    matches.sort(
        key=lambda chunk: source_priority.get(
            str((chunk.get("source_type_assessment") or {}).get("source_type") or "uncertain"),
            1,
        )
    )
    return matches[:limit]


def unique_chunks(chunks: list[dict | None]) -> list[dict]:
    selected: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        if not chunk:
            continue
        key = str(chunk.get("paper_id") or chunk.get("evidence_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(chunk)
    return selected


def inline_citations(chunks: list[dict]) -> str:
    labels = []
    for chunk in chunks:
        evidence_id = str(chunk.get("evidence_id") or "")
        if evidence_id:
            labels.append(evidence_id)
    return f"({'; '.join(labels)})" if labels else ""


def select_fallback_citation_chunks(
    chunks: list[dict],
    *,
    question: str = "",
    evidence_pack: dict | None = None,
) -> list[dict]:
    """Represent the supplied papers with one citeable excerpt per paper.

    Retrieval already bounds the evidence pack; a QC minimum is not a source
    budget. Keep direct-comparison and clean-excerpt priority without imposing
    another paper-count cap on the generic fallback.
    """
    named_chunks = [
        chunk for chunk in chunks if chunk.get("explicit_named_study_anchor")
    ] + [
        chunk
        for chunk in chunks
        if chunk.get("explicit_named_study_support")
        and not chunk.get("explicit_named_study_anchor")
    ]
    if named_chunks:
        selected_named: list[dict] = []
        seen_named: set[str] = set()
        for index, chunk in enumerate(named_chunks):
            key = str(
                chunk.get("evidence_id")
                or chunk.get("chunk_id")
                or f"named-{index}"
            )
            if key in seen_named:
                continue
            seen_named.add(key)
            selected_named.append(chunk)
        return selected_named

    selected: list[dict] = []
    seen_papers: set[tuple[str, str]] = set()
    seen_evidence: set[str] = set()
    clean = [chunk for chunk in chunks if not chunk.get("quality_flags")]
    prioritized: list[dict] = []
    if explicit_evidence_strength_question(question):
        comparison_pack = (
            evidence_pack if evidence_pack is not None else {"chunks": chunks}
        )
        prioritized = eligible_direct_comparison_candidate_chunks(
            question,
            comparison_pack,
        )
    ordered = prioritized + clean + [
        chunk for chunk in chunks if chunk.get("quality_flags")
    ]
    for chunk in ordered:
        evidence_id = str(chunk.get("evidence_id") or "").strip()
        if not evidence_id or evidence_id in seen_evidence:
            continue
        paper_id = str(chunk.get("paper_id") or "").strip()
        paper_key = ("paper", paper_id) if paper_id else ("evidence", evidence_id)
        if paper_key in seen_papers:
            continue
        selected.append(chunk)
        seen_papers.add(paper_key)
        seen_evidence.add(evidence_id)
    return selected


def bounded_comparison_citation_chunks(
    evidence_pack: dict, chunks: list[dict]
) -> list[dict]:
    """Use only the two direct reports certified for a bounded comparison."""

    bounded = evidence_pack.get("bounded_direct_report") or {}
    if not bounded.get("supported") or bounded.get("scope") not in {
        "named_report_comparison_only",
        "resolved_report_comparison_only",
    }:
        return []
    evidence_ids = [
        str(item.get("evidence_id") or "")
        for item in bounded.get("evidence", [])
        if str(item.get("evidence_id") or "")
    ]
    by_id = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in chunks
        if str(chunk.get("evidence_id") or "")
    }
    selected = [by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in by_id]
    return selected if len(selected) == len(evidence_ids) == 2 else []


def wants_list_answer(question: str) -> bool:
    normalized = " ".join(str(question or "").lower().split())
    return any(
        marker in normalized
        for marker in ["list", "liste", "auflist", "übersicht", "uebersicht", "übersichtlich", "alle korrel"]
    )


def is_german_question(question: str) -> bool:
    normalized = f" {' '.join(str(question or '').lower().split())} "
    return any(marker in normalized for marker in [" gib ", " geb ", " mir ", " alle ", " welche ", " warum ", " klinisc"])


def build_sources_template(question: str, evidence_pack: dict) -> str:
    chunks = list(evidence_pack.get("chunks", []))
    if not chunks:
        return """## Best match
Not found in the retrieved corpus.

## Why this looks like the source
Not supported by retrieved evidence.

## Other possible matches
Not supported by retrieved evidence.
"""
    source_chunks = source_lookup_chunks(question, chunks) or chunks
    best = source_chunks[0]
    other_matches = "\n".join(format_source_bullet(chunk) for chunk in source_chunks[1:4])
    return f"""## Best match
{format_source_bullet(best)}

## Why this looks like the source
- The retrieved text matched the user's source lookup and points to this paper as the strongest candidate.
- Relevant excerpt: {source_lookup_sentence(question, str(best.get('text') or ''))}

## Other possible matches
{other_matches or "- No strong alternative source was retrieved."}
"""


def source_lookup_chunks(question: str, chunks: list[dict]) -> list[dict]:
    query_terms = source_lookup_terms(question)
    sample_threshold = source_lookup_sample_threshold(question)
    ranked = []
    for index, chunk in enumerate(chunks):
        searchable = " ".join(
            str(chunk.get(field) or "")
            for field in ["title", "journal", "author", "year", "doi", "pmid", "pmcid", "text", "snippet"]
        )
        score, _ = source_lookup_sentence_score(searchable, query_terms, sample_threshold=sample_threshold)
        if score <= 0:
            continue
        ranked.append((index, score, chunk))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    deduped = []
    seen = set()
    for _, _, chunk in ranked:
        key = (
            str(chunk.get("paper_id") or ""),
            str(chunk.get("author") or ""),
            str(chunk.get("year") or ""),
            str(chunk.get("title") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def hypothesis_title(question: str) -> str:
    lowered = question.lower()
    if "pfemp1" in lowered and ("host immun" in lowered or "immunity" in lowered) and "severe" in lowered:
        return "Host immunity may shape PfEMP1 expression patterns linked to severe malaria."
    if ("var expression" in lowered or "var gene" in lowered) and ("immunity" in lowered or "host" in lowered):
        return "Host immunity may modify var expression toward lower severe-malaria risk."
    if "asymptomatic" in lowered and "pfemp1" in lowered:
        return "PfEMP1 antibody breadth may mark asymptomatic control and reduced severe malaria risk."
    if any(token in lowered for token in ["resistance", "diagnostic", "surveillance"]):
        return "Integrated surveillance may expose coupled resistance and diagnostic escape signals."
    if "sex" in lowered and ("immun" in lowered or "severe" in lowered):
        return "Sex specific immune states may modify severe malaria risk."
    return "Retrieved evidence supports a testable malaria literature hypothesis."


def infer_topic_profile(question: str, chunks: list[dict]) -> dict:
    normalized_question = question.lower()
    if (
        any(stem in normalized_question for stem in ("transcri", "express", "gene"))
        and any(stem in normalized_question for stem in ("clinic", "syndrom", "phenotyp"))
        and "parasite" in normalized_question
    ):
        return TRANSCRIPT_CLINICAL_PROFILE
    text = " ".join(
        [
            question,
            *[
                " ".join(
                    str(chunk.get(field) or "")
                    for field in ["title", "journal", "category", "section", "text"]
                )
                for chunk in chunks[:8]
            ],
        ]
    ).lower()
    ranked = sorted(
        TOPIC_PROFILES,
        key=lambda profile: sum(1 for token in profile["tokens"] if token in text),
        reverse=True,
    )
    if ranked and sum(1 for token in ranked[0]["tokens"] if token in text) >= 2:
        return ranked[0]
    return generic_profile(question)


def generic_profile(question: str) -> dict:
    concise_question = re.sub(r"\s+", " ", question.strip()).strip(" ?.") or "the requested malaria topic"
    return {
        "name": concise_question,
        "prediction": "the retrieved literature pattern will remain measurable in matched, higher-quality data",
        "required_data": (
            "Matched metadata, clinical or experimental outcomes, exposure variables, and the main measurements named in the question."
        ),
        "analysis": [
            "Convert the retrieved evidence into a paper by variable evidence table.",
            "Identify which variables are measured together and which are only connected indirectly.",
            "Test the core association in the subset with matched metadata and comparable outcome definitions.",
        ],
        "gap": (
            "The retrieved papers support parts of the question, but do not yet establish an integrated evidence chain."
        ),
        "question": f"Which matched data would directly test: {concise_question}?",
    }


def format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def evidence_ids(chunks: list[dict]) -> str:
    ids = [str(chunk.get("evidence_id") or "") for chunk in chunks if chunk.get("evidence_id")]
    return ", ".join(ids) or "retrieved evidence"


def format_references(chunks: list[dict]) -> str:
    return "\n".join(
        f"- {chunk.get('evidence_id')}: {chunk.get('citation_label')}."
        for chunk in chunks
        if chunk.get("evidence_id")
    )


def format_source_bullet(chunk: dict) -> str:
    author = str(chunk.get("author") or "Unknown")
    year = str(chunk.get("year") or "n.d.")
    title = str(chunk.get("title") or "Untitled")
    journal = str(chunk.get("journal") or "Unknown journal")
    link = source_url(chunk)
    link_text = f" {link}" if link else ""
    return f"- {author} {year}. {title}. {journal}.{link_text}"


def source_url(chunk: dict) -> str:
    doi = str(chunk.get("doi") or "").strip()
    if doi:
        return doi if doi.startswith(("http://", "https://")) else f"https://doi.org/{doi}"
    pmcid = str(chunk.get("pmcid") or "").strip()
    if pmcid:
        normalized_pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{normalized_pmcid}/"
    pmid = str(chunk.get("pmid") or "").strip()
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return ""


def format_evidence_table(chunks: list[dict]) -> str:
    if not chunks:
        return "- Not supported by retrieved evidence."
    rows = []
    for chunk in chunks:
        rows.append(
            "- "
            f"{chunk.get('evidence_id')}: "
            f"{chunk.get('author', 'Unknown')} {chunk.get('year', '')}; "
            f"section={chunk.get('section', 'unknown')}; "
            f"contribution={evidence_contribution(chunk)}"
        )
    return "\n".join(rows)


def evidence_contribution(chunk: dict) -> str:
    text = clean_evidence_text(str(chunk.get("text") or ""))
    lowered = text.lower()
    if "little known about sex-based biological differences" in lowered:
        return "identifies limited mechanistic knowledge despite observed sex differences."
    if "liver-stage duration" in lowered or "parasite replication" in lowered:
        return "points to under-tested parasite-stage and replication questions."
    if "sex and gender interchangeably" in lowered:
        return "shows a measurement problem separating sex from gender."
    if "respond differently to malaria infection" in lowered or "regulatory t-cell" in lowered:
        return "supports immune-mechanism hypotheses that still need human phenotype linkage."
    if "postadolescent males" in lowered or "male bias" in lowered:
        return "adds epidemiological evidence that needs exposure and care-seeking adjustment."
    if "estrogen" in lowered:
        return "supports hormone-linked immune modulation as a hypothesis-generating mechanism."
    if "cohort study from western kenya" in lowered and "asymptomatic parasitaemia" in lowered:
        return (
            "adds human cohort evidence that sex-specific differences may affect symptomatic risk "
            "after asymptomatic parasitaemia."
        )
    if "haemoglobin" in lowered or "hemoglobin" in lowered:
        return "adds sex-stratified clinical measurement evidence that needs phenotype-specific follow-up."
    if "estradiol" in lowered or "sexual dimorphism" in lowered:
        return "adds hormone and sexual-dimorphism context that should be linked to human malaria endpoints."
    return best_evidence_sentence(text, max_chars=180) or "retrieved as relevant supporting evidence."


def format_cited_chunk_summary(chunk: dict) -> str:
    """Format one fallback claim with one source marker and its locator.

    The evidence ID is deliberately the only inline source marker. The public
    renderer replaces it with a linked author-year label. Emitting the display
    citation here as well would show the same paper twice after rendering.
    """
    evidence_id = str(chunk.get("evidence_id") or "Evidence")
    locator = chunk_locator(chunk)
    text = curated_evidence_statement(str(chunk.get("text") or "")) or best_evidence_sentence(
        str(chunk.get("text") or "")
    )
    if not text:
        title = str(chunk.get("title") or "the retrieved source")
        text = f"the source was retrieved as relevant evidence from {title}"
    claim = f"{evidence_id}{locator} reports that {text}".rstrip()
    return claim if claim.endswith((".", "!", "?")) else f"{claim}."


def chunk_locator(chunk: dict) -> str:
    """Return a compact section/page locator without repeating author-year."""
    citation_label = str(chunk.get("citation_label") or "").strip().strip("[]")
    label_match = re.search(
        r"(?:\b(?:19|20)\d{2}[a-z]?\b|\bn\.?\s*d\.?\b)\s*,\s*(?P<locator>.+)$",
        citation_label,
        flags=re.IGNORECASE,
    )
    if label_match:
        locator = label_match.group("locator").strip().rstrip(".")
        if locator:
            return f" ({locator})"

    parts = []
    section = str(chunk.get("section") or "").strip()
    if section and section.lower() != "unknown":
        parts.append(section.replace("_", " ").title())
    page_start = chunk.get("page_start")
    page_end = chunk.get("page_end")
    if page_start not in ("", None) or page_end not in ("", None):
        if page_start in ("", None):
            pages = str(page_end)
        elif page_end in ("", None) or page_start == page_end:
            pages = str(page_start)
        else:
            pages = f"{page_start}-{page_end}"
        parts.append(f"pages {pages}")
    return f" ({', '.join(parts)})" if parts else ""


def curated_evidence_statement(text: str) -> str:
    clean = clean_evidence_text(text)
    lowered = clean.lower()
    if "broader recognition" in lowered and "asymptomatic" in lowered:
        return (
            "asymptomatic dry season carriers had higher VSA surface recognition and broader "
            "recognition of CD36, EPCR, and group A PfEMP1 binders than clinical malaria cases."
        )
    if "conflicting reports" in lowered and "protection from clinical and severe malaria" in lowered:
        return (
            "PfEMP1 antibody associations with protection from clinical and severe malaria require "
            "careful antigen choice and adjustment for individual exposure."
        )
    if "role ofantibodies to pfemp1" in lowered or "role of antibodies to pfemp1" in lowered:
        return (
            "the source explicitly frames antibodies to PfEMP1 as relevant to protection against "
            "severe malaria."
        )
    if "anti-pfemp1 antibodies in clinical protection" in lowered:
        return "anti-PfEMP1 antibodies are presented as important evidence for clinical protection."
    if "children with asymptomatic infections had the highest igg levels" in lowered:
        return (
            "children with asymptomatic infections had the highest IgG levels, while causal "
            "relationships between antibodies and protection remained difficult to establish."
        )
    if "reduction in var expression in response to host antibodies" in lowered:
        return (
            "asymptomatic infection evidence is consistent with host antibodies shaping or reducing "
            "var/PfEMP1 expression."
        )
    return ""


def best_evidence_sentence(text: str, max_chars: int = 260) -> str:
    clean = clean_evidence_text(text)
    if not clean:
        return ""
    sentences = split_sentences(clean)
    if not sentences:
        sentence = clean
    else:
        sentence = max(sentences, key=sentence_score)
    sentence = sentence.strip()
    if len(sentence) > max_chars:
        sentence = truncate_at_word(sentence, max_chars=max_chars)
    return sentence


def complete_fallback_excerpt(text: str, max_chars: int = 512) -> str:
    """Select a whole source sentence, never repair or clip its scientific text.

    This conservative path is only for the generic answer fallback. In particular,
    do not use the shared cleaner, which can hide source truncation markers.
    """
    clean = re.sub(r"\s+", " ", text).strip()
    sentences: list[str] = []
    start = scanned = 0
    stack: list[str] = []
    invalid = False
    pairs = {")": "(", "]": "[", "}": "{"}
    contrast = re.compile(
        r"^(?:However|But|Yet|Nevertheless|Nonetheless|In contrast|"
        r"Aber|Jedoch|Dennoch|Im Gegensatz)\b", flags=re.IGNORECASE,
    )
    for boundary in re.finditer(r"[.!?][)\]\}\"'’”]*(?=\s|$)", clean):
        end = boundary.end()
        for character in clean[scanned:end]:
            if character in "([{":
                stack.append(character)
            elif character in pairs:
                if stack and stack[-1] == pairs[character]:
                    stack.pop()
                else:
                    invalid = True
        scanned = end
        # Periods in citations, initials and figure references are not endings.
        if clean[boundary.start()] == "." and re.search(
            r"\b(?:figs?|eqs?|refs?|dr|mr|mrs|ms|prof|vs|no|nos|approx|"
            r"et al|e\.g|i\.e|[A-Za-z])\.$",
            clean[max(start, boundary.start() - 16):boundary.start() + 1],
            flags=re.IGNORECASE,
        ):
            continue
        if stack:
            continue
        sentence = clean[start:end].strip()
        start = end
        if invalid:
            if sentences and contrast.match(sentence):
                sentences.pop()
            invalid = False
            continue
        # Keep an immediately following contrast with the statement it qualifies.
        if contrast.match(sentence):
            if sentences:
                sentences[-1] += " " + sentence
        else:
            sentences.append(sentence)
    if sentences and contrast.match(clean[start:].strip()):
        # The qualifier is truncated, so its preceding statement is unsafe too.
        sentences.pop()

    usable = []
    for sentence in sentences:
        words = re.findall(r"\b\w+\b", sentence)
        first_word = re.match(r"^[('\"‘“\[]*([^\W\d_]\w*)", sentence)
        if (
            not 1 <= len(sentence) <= max_chars
            or not first_word
            or not any(character.isupper() for character in first_word.group(1))
            or not re.search(r"\b[a-zäöüß]{2,}\b", sentence)
            or sum(any(character.isalpha() for character in word) for word in words) < 3
            or sum(word.isdigit() for word in words) > len(words) / 3
            or re.search(r"\.{2,}|…|https?://|www\.|\bdoi\b|[|\x00-\x1f]", sentence,
                         flags=re.IGNORECASE)
        ):
            continue
        usable.append(sentence)
    return max(usable, key=sentence_score, default="")


def source_lookup_sentence(question: str, text: str, max_chars: int = 260) -> str:
    clean = clean_evidence_text(text)
    if not clean:
        return "No readable excerpt was available."
    query_terms = source_lookup_terms(question)
    sample_threshold = source_lookup_sample_threshold(question)
    sentences = split_sentences(clean) or [clean]
    sentence = max(
        sentences,
        key=lambda value: source_lookup_sentence_score(value, query_terms, sample_threshold=sample_threshold),
    )
    sentence = sentence.strip()
    if len(sentence) > max_chars:
        sentence = truncate_at_word(sentence, max_chars=max_chars)
    return sentence


def truncate_at_word(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = max(0, max_chars - 3)
    truncated = text[:cutoff].rstrip()
    word_boundary = truncated.rfind(" ")
    if word_boundary >= max(40, cutoff // 2):
        truncated = truncated[:word_boundary].rstrip()
    return truncated.rstrip(" ;,:") + "..."


def source_lookup_terms(question: str) -> set[str]:
    lowered = question.lower()
    terms = set(re.findall(r"[a-zäöüß]+", lowered))
    if "kenya" in terms:
        terms.update({"kenyan", "kilifi", "chonyi", "western"})
    if "kohorte" in terms:
        terms.add("cohort")
    stop_terms = {
        "paper",
        "papers",
        "quelle",
        "quellen",
        "source",
        "sources",
        "reference",
        "references",
        "hieß",
        "hiess",
        "welche",
        "welches",
        "with",
        "that",
        "about",
    }
    return {term for term in terms if len(term) >= 4 and term not in stop_terms}


def source_lookup_sentence_score(
    sentence: str,
    query_terms: set[str],
    sample_threshold: int | None = None,
) -> tuple[int, int]:
    lowered = sentence.lower()
    query_score = sum(1 for term in query_terms if term in lowered)
    source_score = sum(
        1
        for term in ["cohort", "kenya", "kenyan", "kilifi", "chonyi", "western"]
        if term in lowered
    )
    sample_score = 0
    if sample_threshold:
        sample_score = source_lookup_sample_score(lowered, sample_threshold)
    return query_score + source_score + sample_score, min(len(sentence), 260)


def source_lookup_sample_threshold(question: str) -> int | None:
    lowered = question.lower()
    patterns = [
        r"(?:over|more than|above|at least|about|around|ca|circa)\s+(\d{2,5})",
        r"(\d{2,5})\s*(?:\+|plus)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return int(match.group(1))
    return None


def source_lookup_sample_score(text: str, threshold: int) -> int:
    numbers = [
        int(match.group(0))
        for match in re.finditer(r"\b\d{2,5}\b", text)
        if not 1900 <= int(match.group(0)) <= 2100
    ]
    if any(abs(number - threshold) <= 75 and sample_number_context(text, str(number)) for number in numbers):
        return 12
    sample_numbers = [number for number in numbers if sample_number_context(text, str(number))]
    if any(number >= threshold for number in sample_numbers):
        return 10
    if len(sample_numbers) >= 2 and sum(sample_numbers[:4]) >= threshold:
        return 8
    return 0


def sample_number_context(text: str, number_text: str) -> bool:
    match = re.search(rf"\b{re.escape(number_text)}\b", text)
    if not match:
        return False
    window = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
    return any(
        term in window
        for term in [
            "case",
            "cases",
            "child",
            "children",
            "cohort",
            "participant",
            "participants",
            "patient",
            "patients",
            "sample",
            "samples",
            "severe",
            "non-severe",
            "non severe",
        ]
    )


def clean_evidence_text(text: str) -> str:
    clean = text.replace("[...]", " ")
    clean = re.sub(r"^\s*[\].,;:\-–—]+", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    replacements = {
        "IFN-\x03": "IFN-gamma",
        "Malarianaive": "Malaria-naive",
        "malarianaive": "malaria-naive",
        "asymp tomatic": "asymptomatic",
        "exter nal": "external",
        "ofantibodies": "of antibodies",
        "meth ods": "methods",
    }
    for source, target in replacements.items():
        clean = clean.replace(source, target)
    clean = re.sub(r"\ban ohort study", "a cohort study", clean)
    clean = re.sub(r"(?<![A-Za-z])ohort study", "cohort study", clean)
    clean = re.sub(r"^\d+\s+(?=[A-Z])", "", clean)
    clean = re.sub(r"(?:[;,:]\s*)?\b[A-Za-z]{1,4}\.\.\.$", ".", clean).rstrip()
    clean = re.sub(r"\b(?:was|were|is|are|be|been)\s+\.$", ".", clean)
    clean = clean.replace("PfEMP1s", "PfEMP1 variants")
    return clean.strip(" []").strip()


def split_sentences(text: str) -> list[str]:
    candidates = re.split(r"(?<=[.!?])\s+", text)
    return [sentence.strip() for sentence in candidates if len(sentence.strip()) >= 40]


def sentence_score(sentence: str) -> tuple[int, int]:
    lowered = sentence.lower()
    keyword_score = sum(1 for keyword in EVIDENCE_KEYWORDS if keyword in lowered)
    if lowered.startswith(("however", "still", "although")):
        keyword_score -= 1
    if "doi" in lowered or "http" in lowered:
        keyword_score -= 2
    return keyword_score, min(len(sentence), 260)
