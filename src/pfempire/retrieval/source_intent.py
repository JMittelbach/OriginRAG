from __future__ import annotations

import re
from dataclasses import dataclass

from pfempire.retrieval.keyword import contains_normalized_term, normalize_text


@dataclass(frozen=True)
class InterventionEvidenceAssessment:
    """Deterministic scope label for a directly tested intervention passage."""

    evidence_level: str
    clinical_benefit_status: str
    route: str


EPCR_PFEMP1_INTERVENTION_QUERY_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "epcr_pfemp1_adhesion_intervention",
        "EPCR PfEMP1 soluble receptor decoy adhesion blockade protein C barrier",
        "tested_intervention",
    ),
    (
        "epcr_pfemp1_antibody_intervention",
        "EPCR-binding PfEMP1 broadly inhibitory monoclonal antibodies cytoadhesion",
        "tested_intervention",
    ),
    (
        "vascular_consequence_intervention",
        "severe malaria downstream vascular consequences therapeutic intervention "
        "endothelial apoptosis barrier",
        "tested_intervention",
    ),
)


_DIRECT_SECTION_NAMES = {
    "abstract",
    "findings",
    "materials and methods",
    "methods",
    "results",
    "summary",
}

_DIRECT_STUDY_PATTERNS = (
    r"\b(?:here,? we|in this study|the present study|this study)\b",
    r"\bwe (?:administered|assessed|evaluated|examined|found|measured|tested|treated)\b",
    r"\b(?:our|these) (?:data|experiments?|findings|results)\b",
    r"\b(?:was|were) (?:administered|assessed|evaluated|tested|treated)\b",
)

_BACKGROUND_ATTRIBUTION_PATTERNS = (
    r"\b(?:previous|prior|earlier|other|several) (?:studies|study|reports?|work)\b",
    r"\b(?:studies|reports?) (?:have |has )?(?:shown|reported|found|suggested)\b",
    r"\b(?:has|have|had) been (?:shown|reported|suggested|proposed)\b",
    r"\b(?:according to|reported by|suggested by|proposed by)\b",
    r"\[[0-9,;\-–—\s]+\]",
    r"\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.?)?[,]?\s+(?:19|20)\d{2}\)",
)

_AGENT_PATTERNS = (
    r"\b(?:soluble\s+)?epcr (?:decoy|derivative|variant)\b",
    r"\bdecoy (?:protein|receptor|strategy|variant)\b",
    r"\b(?:broadly\s+)?inhibitory (?:anti[- ]?[a-z0-9]+\s+)?antibod\w*\b",
    r"\bmonoclonal antibod\w*\b",
    r"\bnanobod\w*\b",
    r"\b(?:rho )?kinase inhibit\w*\b",
    r"\bfasudil\b",
    r"\b(?:a|an|the|this|candidate|experimental) "
    r"(?:compound|drug|inhibitor|peptide)\b",
    r"\badjunct(?:ive)? (?:therapy|treatment)\b",
)

_ACTION_PATTERNS = (
    r"\bblock\w*\b",
    r"\bdisrupt\w*\b",
    r"\binhibit\w*\b",
    r"\boverc(?:ame|ome|oming)\b",
    r"\bprevent\w*\b",
    r"\bprotect\w*\b",
    r"\breduc\w*\b",
    r"\brescu\w*\b",
    r"\brestor\w*\b",
    r"\brevers\w*\b",
)

_PRECLINICAL_PATTERNS = (
    r"\bin vitro\b",
    r"\bex vivo\b",
    r"\bcocultur\w*\b",
    r"\bcell(?:ular)? (?:assay|culture|line|model)s?\b",
    r"\bbinding assay\b",
    r"\brecombinant\b",
    r"\bfield isolates?\b",
)

_ANIMAL_INTERVENTION_PATTERNS = (
    r"\b(?:mouse|mice|murine|rat|rats|rodents?|animals?)\b.{0,80}"
    r"\b(?:administered|dosed|injected|received|treated)\b",
    r"\b(?:administered|dosed|injected|treated)\b.{0,80}"
    r"\b(?:mouse|mice|murine|rat|rats|rodents?|animals?)\b",
)

_HUMAN_INTERVENTION_PATTERNS = (
    r"\bwe (?:conducted|performed) (?:a )?randomi[sz]ed(?: controlled)? "
    r"(?:clinical )?trial\b",
    r"\bparticipants? (?:were )?(?:assigned|randomi[sz]ed|treated)\b",
    r"\bpatients? (?:received|were administered|were treated with)\b",
)

_CLINICAL_OUTCOME_PATTERNS = (
    r"\bclinical benefit\b",
    r"\bclinical outcome\w*\b",
    r"\bmortality\b",
    r"\bsurvival\b",
    r"\bneurologic\w* outcome\w*\b",
    r"\badverse events?\b",
    r"\bsafety\b",
)


def is_epcr_pfemp1_intervention_source_request(question: str) -> bool:
    """Detect a source request for tested EPCR/PfEMP1 or vascular interventions.

    This deliberately does not activate for a broad list that merely names
    ``therapeutic targeting`` as one possible biological function.
    """

    normalized = normalize_text(question)
    source_request = bool(
        re.search(
            r"\b(?:find|identify|list|show|which|what|are there)\b.{0,100}"
            r"\b(?:papers?|studies|articles?|research)\b",
            normalized,
        )
    )
    target_scope = _has_epcr_pfemp1_or_vascular_scope(normalized)
    intervention = bool(
        re.search(
            r"\b(?:intervention|therapy|therapeutic|treatment|drug)\w*\b",
            normalized,
        )
    )
    evaluation = bool(
        re.search(
            r"\b(?:clinical benefit|clinical outcome\w*|clinical trial\w*|"
            r"efficacy|tested|evaluated|disrupt\w*|block\w*|inhibit\w*)\b",
            normalized,
        )
    )
    return source_request and target_scope and intervention and evaluation


def assess_intervention_evidence(
    *,
    title: str,
    text: str,
    section: str,
) -> InterventionEvidenceAssessment | None:
    """Classify direct intervention testing without inferring clinical benefit.

    A therapeutic implication, target association, or naturally acquired
    antibody measurement is not enough. The supplied title or one direct-study
    statement must identify an intervention agent and an observed perturbation.
    """

    normalized_title = normalize_text(title)
    normalized_text = normalize_text(text)
    title_match = _has_direct_intervention_statement(normalized_title)
    sentences = _sentences(text)
    human_clinical_test = _human_clinical_benefit_test_identified(
        normalized_title,
        sentences,
        section=section,
    )
    animal_intervention = _animal_intervention_identified(
        normalized_title,
        sentences,
        section=section,
    )
    direct_statement = False
    for statement in sentences:
        normalized_statement = normalize_text(statement)
        scoped_statement = f"{normalized_title} {normalized_statement}".strip()




        if not (
            _has_epcr_pfemp1_or_vascular_scope(scoped_statement)
            and _matches_any(normalized_statement, _AGENT_PATTERNS)
            and _matches_any(normalized_statement, _ACTION_PATTERNS)
        ):
            continue
        own_study = any(
            re.search(pattern, statement, flags=re.IGNORECASE)
            for pattern in _DIRECT_STUDY_PATTERNS
        )
        attributed_background = any(
            re.search(pattern, statement, flags=re.IGNORECASE)
            for pattern in _BACKGROUND_ATTRIBUTION_PATTERNS
        )
        if own_study or (
            normalize_text(section) in _DIRECT_SECTION_NAMES
            and not attributed_background
        ):
            direct_statement = True
            break
    if not title_match and not direct_statement and not human_clinical_test:
        return None

    searchable = f"{normalized_title} {normalized_text}".strip()
    if human_clinical_test:
        evidence_level = "human_interventional_outcome"
        clinical_benefit_status = "human_clinical_benefit_test_identified"
    elif animal_intervention:
        evidence_level = "animal_intervention"
        clinical_benefit_status = "not_identified_in_retained_evidence"
    elif _matches_any(searchable, _PRECLINICAL_PATTERNS):
        evidence_level = "preclinical_functional_intervention"
        clinical_benefit_status = "not_identified_in_retained_evidence"
    else:
        evidence_level = "experimental_intervention_scope"
        clinical_benefit_status = "not_identified_in_retained_evidence"
    return InterventionEvidenceAssessment(
        evidence_level=evidence_level,
        clinical_benefit_status=clinical_benefit_status,
        route="title" if title_match else "direct_study_statement",
    )


def _has_direct_intervention_statement(normalized_text: str) -> bool:
    return (
        _has_epcr_pfemp1_or_vascular_scope(normalized_text)
        and _matches_any(normalized_text, _AGENT_PATTERNS)
        and _matches_any(normalized_text, _ACTION_PATTERNS)
    )


def _has_epcr_pfemp1_or_vascular_scope(normalized_text: str) -> bool:
    direct_target = any(
        contains_normalized_term(normalized_text, term)
        for term in (
            "pfemp1",
            "plasmodium falciparum erythrocyte membrane protein 1",
            "cidralpha1",
            "cidr alpha1",
            "cidrα1",
        )
    )
    epcr_target = any(
        contains_normalized_term(normalized_text, term)
        for term in ("epcr", "endothelial protein c receptor")
    )
    malaria_context = any(
        contains_normalized_term(normalized_text, term)
        for term in ("malaria", "plasmodium falciparum", "infected erythrocyte")
    )
    downstream_vascular_consequence = any(
        contains_normalized_term(normalized_text, term)
        for term in (
            "endothelial",
            "endothelia",
            "vascular",
            "barrier",
            "apoptosis",
            "protein c",
            "coagulation",
            "thrombomodulin",
        )
    )
    return (direct_target and epcr_target) or (
        malaria_context and downstream_vascular_consequence
    )


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _human_clinical_benefit_test_identified(
    normalized_title: str,
    sentences: list[str],
    *,
    section: str,
) -> bool:
    """Require a linked treated-human design and endpoint in a bounded window."""

    normalized_section = normalize_text(section)
    for index, sentence in enumerate(sentences):
        normalized_sentence = normalize_text(sentence)
        if not _matches_any(normalized_sentence, _HUMAN_INTERVENTION_PATTERNS):
            continue


        if not _matches_any(normalized_sentence, _AGENT_PATTERNS):
            continue
        window_sentences = sentences[max(0, index - 1) : index + 2]
        normalized_window = normalize_text(" ".join(window_sentences))
        if re.search(
            r"\b(?:future|needed|proposed|recommended|warrant\w*|should)\b.{0,80}"
            r"\bclinical trial\w*\b",
            normalized_window,
            flags=re.IGNORECASE,
        ):
            continue
        if _matches_any(normalized_window, _BACKGROUND_ATTRIBUTION_PATTERNS):
            continue
        if not _matches_any(normalized_window, _CLINICAL_OUTCOME_PATTERNS):
            continue
        if not _matches_any(normalized_window, _ACTION_PATTERNS):
            continue
        if not _has_epcr_pfemp1_or_vascular_scope(
            f"{normalized_title} {normalized_window}"
        ):
            continue
        own_study = _matches_any(normalized_window, _DIRECT_STUDY_PATTERNS)
        if own_study or normalized_section in _DIRECT_SECTION_NAMES:
            return True
    return False


def _animal_intervention_identified(
    normalized_title: str,
    sentences: list[str],
    *,
    section: str,
) -> bool:
    """Require the intervention agent to be administered to the animal."""

    normalized_section = normalize_text(section)
    for index, sentence in enumerate(sentences):
        normalized_sentence = normalize_text(sentence)
        if not _matches_any(normalized_sentence, _ANIMAL_INTERVENTION_PATTERNS):
            continue
        if not _matches_any(normalized_sentence, _AGENT_PATTERNS):
            continue
        window = normalize_text(" ".join(sentences[index : index + 2]))
        if not _matches_any(window, _ACTION_PATTERNS):
            continue
        if _matches_any(window, _BACKGROUND_ATTRIBUTION_PATTERNS):
            continue
        if not _has_epcr_pfemp1_or_vascular_scope(
            f"{normalized_title} {window}"
        ):
            continue
        own_study = _matches_any(window, _DIRECT_STUDY_PATTERNS)
        if own_study or normalized_section in _DIRECT_SECTION_NAMES:
            return True
    return False


def _sentences(text: str) -> list[str]:
    compact = " ".join(str(text or "").split())
    return [
        statement.strip()
        for statement in re.split(r"(?<=[.!?;])\s+", compact)
        if statement.strip()
    ]
