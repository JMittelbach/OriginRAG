from __future__ import annotations

import math
import re

from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate


def score_candidate_facets(candidate: TraceCandidate, blueprint: EvidenceBlueprint) -> dict[str, float]:
    text = normalize(" ".join([candidate.text, str(candidate.metadata.get("title", ""))]))
    scores: dict[str, float] = {}
    for facet in blueprint.required_facets:
        terms = [facet.label, *facet.aliases]
        matched = sum(term_match(text, normalize(term)) for term in terms if normalize(term))
        lexical = 1.0 - math.exp(-matched)
        section_bonus = 0.10 if candidate.section in {"results", "discussion", "abstract"} else 0.0
        scores[facet.facet_id] = min(1.0, lexical + section_bonus if matched else 0.0)
    return scores


def infer_scale_scores(candidate: TraceCandidate, blueprint: EvidenceBlueprint) -> dict[str, float]:
    title = str(candidate.metadata.get("title", ""))
    text = normalize(" ".join([title, candidate.text]))
    lexicon = {
        "molecular": ["protein", "gene", "domain", "receptor", "binding", "epcr", "pfemp1", "var"],
        "subcellular": ["nuclear", "membrane", "chromatin", "organelle", "localization"],
        "cellular": ["cell", "erythrocyte", "endothelial", "lymphocyte", "monocyte"],
        "tissue": ["tissue", "brain", "placenta", "microvasculature", "endothelium"],
        "organ": ["brain", "spleen", "liver", "placenta", "organ"],
        "clinical": ["patient", "clinical", "severity", "cerebral malaria", "outcome", "syndrome"],
        "population": ["cohort", "children", "adult", "population", "site", "country"],
        "methodological": ["method", "assay", "dataset", "sequencing", "model", "benchmark"],
    }
    source_type = _candidate_source_type(candidate)
    if _is_secondary_source(title, source_type):




        return {scale: 0.0 for scale in lexicon}
    preferred = set(blueprint.preferred_scales)
    scores = {
        scale: min(1.0, 0.35 * sum(term_match(text, normalize(term)) for term in terms) + (0.05 if scale in preferred else 0.0))
        for scale, terms in lexicon.items()
    }
    roles = infer_direct_evidence_roles(
        title=title,
        text=candidate.text,
        section=candidate.section,
        source_type=source_type,
    )
    if "clinical_association" in roles:
        scores["clinical"] = 1.0
    if "functional_binding" in roles:
        scores["molecular"] = 1.0
    return scores


def infer_evidence_type_scores(candidate: TraceCandidate) -> dict[str, float]:
    title = str(candidate.metadata.get("title", ""))
    text = normalize(" ".join([title, candidate.text]))
    mappings = {
        "structural": ["structure", "crystal", "cryo-em"],
        "biochemical": ["binding assay", "affinity", "biochemical"],
        "functional": ["functional", "inhibition", "knockout", "perturbation"],
        "cellular": ["cell", "cellular", "endothelial"],
        "observational": ["cohort", "associated", "patients"],
        "longitudinal": ["longitudinal", "follow-up", "time point"],
        "interventional": ["trial", "randomized", "intervention", "treatment"],
        "genetic": ["genetic", "genome", "variant", "mutation"],
        "transcriptomic": ["transcript", "rna-seq", "expression"],
        "single_cell": ["single-cell", "scrna", "single cell"],
        "multi_omics": ["multi-omics", "proteomic", "metabolomic"],
        "methodological": ["method", "pipeline", "benchmark", "algorithm"],
        "dataset": ["dataset", "accession", "geo", "sra"],
        "review": ["review", "meta-analysis"],
    }
    source_type = _candidate_source_type(candidate)
    if _is_secondary_source(title, source_type):



        return {
            kind: 1.0 if kind == "review" else 0.0
            for kind in mappings
        }
    scores = {
        kind: min(
            1.0,
            0.5 * sum(term_match(text, normalize(term)) for term in terms),
        )
        for kind, terms in mappings.items()
    }
    roles = infer_direct_evidence_roles(
        title=title,
        text=candidate.text,
        section=candidate.section,
        source_type=source_type,
    )
    if "clinical_association" in roles:
        scores["observational"] = 1.0
    if "functional_binding" in roles:
        scores["biochemical"] = 1.0
    if not any(scores.values()):
        scores["unknown"] = 1.0
    return scores


def infer_direct_evidence_roles(
    *,
    title: str,
    text: str,
    section: str = "",
    source_type: object = "",
) -> set[str]:
    """Infer direct empirical roles from the complete prompt-visible record.

    Titles are part of the evidence record and often carry the study design or
    primary result even when the selected excerpt is a table or discussion
    fragment.  The conjunctive rules below deliberately require result/design
    language; a bare disease or binding mention does not acquire a direct role.
    """

    if _is_secondary_source(title, source_type):
        return set()

    roles: set[str] = set()
    title_text = normalize(title)
    body_text = normalize(text)
    combined = " ".join([title_text, body_text])
    section_name = normalize(section)

    exposure = (
        r"(?:expression|transcripts?|transcription|abundance|profiles?|binding|"
        r"markers?|variants?|genes?|proteins?|parasites?|domains?|cell state|"
        r"repertoire)"
    )
    phenotype = (
        r"(?:patients?|children|cohort|disease|severity|severe|critical|"
        r"cerebral malaria|clinical outcome|mortality|brain swelling|syndrome)"
    )
    relation = (
        r"(?:is |are |was |were |not )?(?:associated with|linked to|"
        r"correlat\w* with|increas\w* with|elevat\w* in|higher in|lower in|"
        r"enrich\w* in|predict\w*)"
    )
    exposure_to_phenotype = re.search(
        rf"\b{exposure}\b.{{0,90}}\b{relation}\b.{{0,90}}\b{phenotype}\b",
        title_text,
    )
    phenotype_to_exposure = re.search(
        rf"\b{phenotype}\b.{{0,70}}\b(?:is |are |was |were )?associated with\b"
        rf".{{0,90}}\b{exposure}\b",
        title_text,
    )
    linking_title = re.search(
        rf"\blinking\b.{{0,90}}\b{exposure}\b.{{0,30}}\bto\b.{{0,50}}"
        rf"\b{phenotype}\b",
        title_text,
    )
    differential_title = re.search(
        rf"\b(?:differential|increased|decreased|elevated|higher|lower)\b"
        rf".{{0,45}}\b{exposure}\b.{{0,45}}\b(?:in|with|between)\b"
        rf".{{0,50}}\b{phenotype}\b",
        title_text,
    )
    modifier_only_association = re.search(
        r"\bassociated with\b.{0,45}\b(?:severe|cerebral) malaria\b"
        r".{0,35}\b(?:bind|binds|interact|adhere)\b",
        title_text,
    )
    body_sentences = [
        normalize(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", str(text or ""))
    ]
    clinical_body_direct = any(
        re.search(rf"\b{exposure}\b", sentence)
        and re.search(rf"\b{phenotype}\b", sentence)
        and re.search(
            r"\b(?:associated with|correlat\w*|compared with|higher|lower|"
            r"elevated|increased|decreased|differential)\b",
            sentence,
        )
        and (
            re.search(
                r"\b(?:our (?:study|data|results)|this study|we (?:found|"
                r"observed|report|show|demonstrate)|data (?:show|indicate)|"
                r"results? (?:show|indicate))\b",
                sentence,
            )
            or (
                section_name in {"results", "abstract"}
                and not re.search(r"\bet al\b|\b(?:19|20)[0-9]{2}\b", sentence)
                and re.search(
                    r"\b(?:n [0-9]+|median|correlation|compared|higher|lower|"
                    r"elevated|increased|decreased|differential)\b",
                    sentence,
                )
            )
        )
        for sentence in body_sentences
    )
    if (
        ((exposure_to_phenotype or phenotype_to_exposure or linking_title or differential_title)
         and not modifier_only_association)
        or clinical_body_direct
    ):
        roles.add("clinical_association")

    binding_predicate = re.compile(
        r"\b(?:binds?|bound)\b(?:\s+to\b)?|\bbinding\b\s+(?:of|to|was|is|by)\b|"
        r"\baffinity\b|\breceptor interaction\b|\bprotein interaction\b"
    )
    active_binding_title = re.search(
        r"\b(?:binds?|bound)\b(?:.{0,55}\b(?:cell|receptor|protein|domain)s?\b)?|"
        r"\ballows\b.{0,50}\bbinding\b|\bbinding by\b|\bbinding affinity\b",
        title_text,
    )
    direct_experiment = re.compile(
        r"\b(?:we (?:tested|show|found|find|demonstrate|measured|compared)|"
        r"here we|our results|binding assay|affinity assay|crystal structure|"
        r"demonstrated by|was generated|was found|binding was compared|"
        r"reversal of binding|inhibit\w* binding|obstructed|induced)\b"
    )
    direct_binding_sentence = any(
        binding_predicate.search(sentence) and direct_experiment.search(sentence)
        for sentence in body_sentences
    )
    if binding_predicate.search(combined) and (
        active_binding_title
        or direct_binding_sentence
        or (
            section_name == "results"
            and re.search(r"\b(?:results?|figure|fig|measured|detected|show|shows)\b", body_text)
        )
    ):
        roles.add("functional_binding")

    return roles


def _candidate_source_type(candidate: TraceCandidate) -> object:
    raw = candidate.metadata.get("raw_result")
    if not isinstance(raw, dict):
        return ""
    return raw.get("source_type_assessment") or raw.get("source_type") or ""


def _is_secondary_source(title: str, source_type: object) -> bool:
    if isinstance(source_type, dict):
        source_text = normalize(
            " ".join(
                str(source_type.get(field) or "")
                for field in ("source_type", "label", "direct_reporting")
            )
        )
    else:
        source_text = normalize(str(source_type))
    title_text = normalize(title)
    if source_text in {
        "review",
        "narrative review",
        "systematic review",
        "systematic review meta analysis",
        "meta analysis",
        "editorial commentary",
        "protocol",
    }:
        return True
    return any(
        marker in f" {source_text} {title_text} "
        for marker in (
            " narrative review ",
            " systematic review ",
            " scoping review ",
            " meta analysis ",
            " source type review ",
            " review article ",
            " editorial commentary ",
            " study protocol ",
        )
    ) or bool(
        re.search(
            r"^(?:a |an )?(?:review of|perspective on|commentary on|editorial on|"
            r"protocol for)\b",
            title_text,
        )
    )


def infer_polarity_scores(candidate: TraceCandidate, reviewed: str | None = None) -> dict[str, float]:
    if reviewed in {"support", "contradiction", "null_result", "limitation"}:
        return {reviewed: 1.0}

    return {"unknown": 1.0}


def facet_coverage(candidates: list[TraceCandidate], facets: list[EvidenceFacet]) -> dict:
    total_weight = sum(facet.importance_weight for facet in facets) or 1.0
    per_facet = {}
    for facet in facets:
        evidence_sum = sum(candidate.facet_scores.get(facet.facet_id, 0.0) for candidate in candidates)
        evidence_units = evidence_sum / max(1, facet.minimum_items)
        per_facet[facet.facet_id] = 1.0 - math.exp(-evidence_units)
    total = sum(facet.importance_weight * per_facet[facet.facet_id] for facet in facets) / total_weight
    unresolved = [facet.facet_id for facet in facets if facet.is_required and per_facet[facet.facet_id] < 0.60]
    return {"total": total, "per_facet": per_facet, "unresolved_required": unresolved}


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def term_match(text: str, term: str) -> int:
    if not term:
        return 0
    return int(term in text)
