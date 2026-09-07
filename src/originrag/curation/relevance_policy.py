from __future__ import annotations

import re
from dataclasses import dataclass


ALLOWED_CORE_TERMS = (
    "adhesion",
    "allelic exclusion",
    "antigenic variation",
    "artemisinin resistance",
    "artemisinin-resistant",
    "bioinformatics",
    "bromodomain",
    "cell annotation",
    "chromatin",
    "cidr",
    "census population size",
    "cytoadherence",
    "dbl",
    "diagnostic escape",
    "domain cassette",
    "epcr",
    "evolution",
    "evolutionary",
    "exported protein",
    "foundation model",
    "gametocyte",
    "gene expression",
    "genetic architecture",
    "genetic diversity",
    "genetic structure",
    "genetics",
    "genome",
    "genomic",
    "genomics",
    "histone",
    "hrp2",
    "hrp3",
    "k13",
    "kelch13",
    "machine learning",
    "methyltransferase",
    "molecular marker",
    "pangenome",
    "parasite biomass",
    "parasite clone",
    "parasite genomics",
    "parasite population size",
    "parasite transcriptomics",
    "pfcrt",
    "pfdhfr",
    "pfdhps",
    "pfemp",
    "pfemp1",
    "pfhrp",
    "pfhrp2",
    "pfhrp3",
    "pfmdr",
    "pipeline",
    "population genetics",
    "population genomics",
    "promoter",
    "recombination",
    "recombinant",
    "recombinants",
    "reference mapping",
    "regulation",
    "rosetting",
    "sequestration",
    "sequence",
    "single cell",
    "single-cell",
    "scrna",
    "structural variant",
    "subtelomeric",
    "transcriptome",
    "transcriptomic",
    "transcriptomics",
    "unaligned sequences",
    "var gene",
    "var genes",
    "var2csa",
    "variant surface antigen",
    "virulence gene",
)

HARD_OFF_TOPIC_TERMS = (
    "covid-19",
    "sars-cov-2",
    "rheumatoid arthritis",
    "colorectal cancer",
    "lung covid",
    "lung cancer",
    "skin cancer",
    "rothko",
)

VACCINE_TERMS = (
    "vaccine",
    "vaccination",
    "vaccinated",
    "immunization",
    "immunisation",
    "rts,s",
    "r21/matrix",
    "pfspz vaccine",
)

MOLECULAR_VACCINE_TERMS = (
    "blood-stage vaccine",
    "merozoite antigen",
    "pfrh5",
    "rh5 vaccine",
    "rh5-based",
    "rh5.1",
    "rh5.2",
    "cyrpa",
    "ripr",
)

VECTOR_CONTROL_TERMS = (
    "anopheles",
    "bed net",
    "bed-net",
    "insecticide",
    "insecticidal",
    "integrated vector management",
    "larviciding",
    "long-lasting insecticide",
    "mosquito control",
    "mosquito vector",
    "vector control",
)

EPIDEMIOLOGY_PUBLIC_HEALTH_TERMS = (
    "acceptability",
    "acceptance",
    "adherence",
    "awareness",
    "burden",
    "caregiver",
    "caregivers",
    "climate",
    "community-based",
    "compliance",
    "control intervention",
    "control interventions",
    "cost-effectiveness",
    "cross-sectional",
    "diagnostic delay",
    "diagnostic delays",
    "elimination",
    "endemicity",
    "epidemiological profile",
    "forecasting malaria incidence",
    "geospatial",
    "health facility",
    "health facilities",
    "healthcare utilization",
    "household survey",
    "incidence",
    "intervention",
    "interventions",
    "knowledge, attitudes",
    "malaria control",
    "malaria incidence",
    "misdiagnosis",
    "operational",
    "policy",
    "prevalence",
    "public awareness",
    "public health",
    "resource allocation",
    "retrospective",
    "risk factors",
    "self-medication",
    "social determinants",
    "surveillance data",
    "treatment outcome",
    "trend analysis",
    "travelers",
    "travellers",
    "uptake",
)

CHEMOPREVENTION_TERMS = (
    "chemoprevention",
    "intermittent preventive",
    "primaquine treatment",
    "radical cure",
    "tafenoquine",
)

CLINICAL_LOW_SIGNAL_TERMS = (
    "case report",
    "case of pancytopenia",
    "rare complication",
    "splenectomy",
)

CORE_RE = re.compile("|".join(re.escape(term) for term in ALLOWED_CORE_TERMS), re.IGNORECASE)


@dataclass(frozen=True)
class RelevanceDecision:
    keep: bool
    reasons: tuple[str, ...]
    rescued_by_core_focus: bool


def relevance_decision(*, title: str, journal: str = "", category: str = "", query: str = "") -> RelevanceDecision:
    """Decide whether a paper fits OriginRAG's current molecular/evolutionary RAG scope."""
    normalized = normalize_scope_text(" ".join([title, journal, category, query]))
    title_normalized = normalize_scope_text(title)
    reasons: list[str] = []
    core_focus = bool(CORE_RE.search(normalized))

    if contains_any(normalized, HARD_OFF_TOPIC_TERMS):
        reasons.append("hard_off_topic")

    if contains_any(normalized, VACCINE_TERMS) and not contains_any(normalized, MOLECULAR_VACCINE_TERMS):
        reasons.append("vaccine_or_vaccine_trial")

    if contains_any(normalized, VECTOR_CONTROL_TERMS) and not has_var_expression_rescue(normalized):
        reasons.append("vector_control")

    if contains_any(normalized, CHEMOPREVENTION_TERMS) and not has_molecular_resistance_focus(normalized):
        reasons.append("chemoprevention_or_treatment_policy")

    if contains_any(normalized, CLINICAL_LOW_SIGNAL_TERMS):
        reasons.append("clinical_case_low_signal")

    if contains_any(title_normalized, EPIDEMIOLOGY_PUBLIC_HEALTH_TERMS) and not core_focus:
        reasons.append("epidemiology_public_health")

    return RelevanceDecision(keep=not reasons, reasons=tuple(reasons), rescued_by_core_focus=core_focus and not reasons)


def normalize_scope_text(value: str) -> str:
    normalized = (value or "").lower()
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def has_molecular_resistance_focus(text: str) -> bool:
    return any(
        term in text
        for term in [
            "artemisinin resistance",
            "kelch13",
            "k13",
            "pfcrt",
            "pfmdr",
            "pfdhfr",
            "pfdhps",
            "pfhrp2",
            "pfhrp3",
            "molecular marker",
            "gene deletion",
            "mutation",
            "genetic",
            "genomic",
        ]
    )


def has_var_expression_rescue(text: str) -> bool:
    return any(
        term in text
        for term in [
            "pfemp",
            "var gene",
            "var genes",
            "variant surface antigen",
            "virulence gene expression",
            "transcriptome",
            "transcriptomics",
            "plasmodium falciparum infections",
        ]
    )
