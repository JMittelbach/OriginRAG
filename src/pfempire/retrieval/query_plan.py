from __future__ import annotations

import re
from dataclasses import dataclass

from pfempire.retrieval.keyword import normalize_text
from pfempire.retrieval.source_intent import (
    EPCR_PFEMP1_INTERVENTION_QUERY_SPECS,
    is_epcr_pfemp1_intervention_source_request,
)


@dataclass(frozen=True)
class RetrievalQuery:
    label: str
    query: str
    intent: str | None = None


@dataclass(frozen=True)
class NamedStudyReference:
    """One explicit ``Surname et al.`` reference in the current question."""

    surname: str
    year: str | None
    context: str


VIRULENCE_QUERY = "PfEMP1 var gene cytoadherence sequestration endothelial binding EPCR ICAM CD36 rosetting antigenic variation severe malaria"
HOST_INTERACTION_QUERY = "host immunity antibody selection antigenic variation persistent infection parasite virulence Plasmodium falciparum"
PAPER_SEARCH_MODES = {"targeted_lookup", "topical_discovery"}
RULED_EXPANSIONS = [
    (
        "disease_signature_context",
        ["disease", "diseases", "desease", "deseases", "diseae", "deseae"],
        "gene signature disease severity fibrosis infection cancer clinical phenotype macrophage",
    ),
    (
        "mosquito_passage_var_reset",
        ["mosquito passage", "var-expression shift", "var expression shift", "epigenetic virulence"],
        "mosquito passage changes var gene expression controlled human malaria infection epigenetic reset memory switching blood stage virulence",
    ),
    (
        "host_immunity_var_selection",
        ["host immunity", "naturally acquired immunity", "immune selection"],
        "naturally acquired immunity modifies var gene virulence expression CHMI semi-immune volunteers clonal selection mosquito passage",
    ),
    (
        "dual_receptor_brain_endothelium",
        ["dual receptor", "epcr and icam1", "epcr icam1", "dual epcr"],
        "dual receptor binding PfEMP1 EPCR ICAM1 DBLbeta brain endothelial cells group A DC8 DC13 var genes",
    ),
    (
        "pfemp1_epcr_severe",
        ["pfemp1", "epcr", "cidr", "dc8", "dc13", "endothelial protein c receptor"],
        "PfEMP1 EPCR CIDRalpha1 DC8 DC13 endothelial protein C receptor severe malaria cerebral malaria",
    ),
    (
        "cerebral_malaria_endothelium",
        ["cerebral malaria", "retinopathy", "brain malaria"],
        "PfEMP1 DC8 DC13 CIDRalpha1 EPCR ICAM1 brain endothelial cytoadherence cerebral malaria",
    ),
    (
        "cerebral_malaria_var_expression",
        ["cerebral malaria", "retinopathy", "brain malaria"],
        "cerebral malaria Group A B/A var gene transcription PfEMP1 expression primary cohort",
    ),
    (
        "var_expression_phenotype",
        ["var expression", "var gene expression", "virulence gene expression", "transcriptome"],
        "var gene expression PfEMP1 transcriptome severe malaria asymptomatic infection clinical phenotype",
    ),
    (
        "clinical_var_switching",
        ["switching rate", "switching rates", "switch rate"],
        "long-term in vitro cultures switching rates about 2% or less per generation initial stage infection approximated up to 18% Bachmann",
    ),
    (
        "var_switching_rates",
        ["switching rate", "switching rates", "switch rate", "on rate", "off rate"],
        "var gene switching rates percent per generation 1% 2% 18% on off rate central subtelomeric chromosomal position Frank",
    ),
    (
        "cytoadherence_sequestration",
        ["cytoadherence", "cytoadhesion", "sequestration", "rosetting", "endothelial"],
        "cytoadherence sequestration rosetting endothelial binding ICAM1 CD36 EPCR severe malaria",
    ),
    (
        "host_single_cell_immunity",
        ["scrna", "single cell", "single-cell", "immune state", "t cell", "monocyte"],
        "host scRNA-seq immune state T cells monocytes malaria severity Plasmodium infection",
    ),
    (
        "sex_specific_immunity",
        ["sex", "gender", "male", "female", "estrogen", "testosterone"],
        "sex differences malaria immunity male female estrogen testosterone Plasmodium clearance",
    ),
    (
        "data_accessibility",
        [
            "accessible",
            "reanalysis",
            "data availability",
            "accession",
            "repository",
            "downloadable",
        ],
        "RNA-seq data deposited Gene Expression Omnibus GEO repository accession "
        "data availability",
    ),
    (
        "dataset_discovery",
        ["dataset", "accession", "data availability", "repository", "downloadable"],
        "dataset cohort accession clinical phenotype parasite transcriptomics host transcriptomics malaria",
    ),
    (
        "method_tools",
        ["method", "tool", "classify", "annotation", "motif", "upstream"],
        "Varia upsML MEME FIMO var genes upstream group motif scGPT Geneformer CellTypist scVI",
    ),
    (
        "artemisinin_resistance",
        ["artemisinin", "kelch13", "k13"],
        "Plasmodium falciparum artemisinin resistance kelch13 k13 genomic "
        "background emergence evolution Southeast Asia East Africa",
    ),
    (
        "diagnostic_escape",
        ["diagnostic escape", "pfhrp", "pfhrp2", "pfhrp3"],
        "Plasmodium falciparum diagnostic escape pfhrp2 pfhrp3 deletion rapid "
        "diagnostic test",
    ),
    (
        "antimalarial_drug_resistance",
        [
            "drug resistance",
            "antimalarial resistance",
            "multidrug resistant",
            "molecular resistance",
        ],
        "Plasmodium falciparum antimalarial drug resistance genomic epidemiology "
        "pfcrt pfmdr1 dhfr dhps kelch13 treatment failure",
    ),
    (
        "chloroquine_resistance",
        ["chloroquine", "pfcrt"],
        "Plasmodium falciparum chloroquine resistance pfcrt transporter "
        "genomic epidemiology treatment failure",
    ),
    (
        "antifolate_resistance",
        ["antifolate", "pyrimethamine", "sulfadoxine", "dhfr", "dhps"],
        "Plasmodium falciparum antifolate sulfadoxine pyrimethamine resistance "
        "dhfr dhps genomic epidemiology treatment failure",
    ),
    (
        "molecular_targets",
        ["target", "targets", "receptor", "interaction", "intervention", "invasion", "antibody", "antibodies"],
        "PfEMP1 EPCR CD36 VAR2CSA PfRH5 invasion antibodies vaccine receptor interaction malaria intervention",
    ),
    (
        "vaccine_clinical_development",
        ["vaccine development", "vaccine candidate", "clinical trial", "how advanced", "development stage"],
        "PfRH5 blood-stage malaria vaccine clinical trial phase 1 phase 2 efficacy children RH5.1 Matrix-M RH5.2 VLP",
    ),
    (
        "vaccine_development_barriers",
        ["vaccine", "vaccination", "immunization", "immunisation"],
        "PfRH5 blood-stage malaria vaccine barriers durable immunity antibody concentration polymorphism adjuvant delivery manufacturing deployment",
    ),
]


ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "spp1": (
        "spp1",
        "osteopontin",
        "ostéopontine",
        "osteopontine",
        "osteopontina",
        "secreted phosphoprotein 1",
    ),
}

VAR_PFEMP1_ALTERNATIVE_ALIASES = ("var gene", "var genes", "var", "pfemp1")
RIFIN_STEVOR_ALTERNATIVE_ALIASES = ("rifin", "rifins", "stevor", "stevors")
IMMUNE_RECEPTOR_BINDING_ALIASES = (
    "immune receptor",
    "immune receptors",
    "inhibitory receptor",
    "inhibitory receptors",
    "lilrb1",
    "lair1",
    "kir2dl1",
    "kir2ds1",
)
PARASITE_OMICS_ANCHOR = "__parasite_omics__"

SOURCE_FUNCTION_INTENTS: tuple[
    tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str], ...
] = (
    (
        ("receptor binding", "receptor interaction", "bind receptor"),
        (
            "receptor binding",
            "receptor interaction",
            "bind",
            "binds",
            "bound",
            "binding",
            "bind receptor",
            "binds receptor",
            "bound receptor",
            "binding to",
            "ligand",
            "adhesion",
            "adhesin",
            "erythrocyte binding",
            "red blood cell binding",
        ),
        (
            "receptor binding",
            "receptor interaction",
            "bind",
            "binds",
            "bound",
            "binding protein",
            "ligand",
            "adhesion",
            "adhesin",
            "erythrocyte binding",
            "red blood cell binding",
        ),
        "receptor binding",
    ),
    (
        ("immune evasion", "immune escape", "evade immunity"),
        (
            "immune evasion",
            "immune escape",
            "evade immunity",
            "evade the immune",
            "escape immune",
            "immune suppression",
            "suppress immune",
            "inhibit immune",
            "antigenic variation",
        ),
        (
            "immune evasion",
            "immune escape",
            "antigenic variation",
            "immune suppression",
        ),
        "immune evasion",
    ),
    (
        ("rosetting", "rosette", "rosettes"),
        ("rosetting", "rosette", "rosettes"),
        ("rosetting", "rosette", "rosettes"),
        "rosetting",
    ),
    (
        (
            "host cell remodelling",
            "host cell remodeling",
            "infected erythrocyte remodelling",
            "infected erythrocyte remodeling",
        ),
        (
            "host cell remodelling",
            "host cell remodeling",
            "infected erythrocyte surface",
            "infected red blood cell surface",
            "infected erythrocyte membrane",
            "infected red blood cell membrane",
            "erythrocyte membrane",
            "red blood cell membrane",
            "membrane topology",
            "surface membrane",
            "surface membranes",
            "maurer cleft",
            "maurer s cleft",
            "surface expressed",
            "surface exposed",
        ),
        (
            "host cell remodelling",
            "host cell remodeling",
            "localization",
            "localisation",
            "membrane topology",
            "surface membrane of infected erythrocytes",
            "surface membranes of infected erythrocytes",
            "surface membrane of infected red blood cells",
            "surface membranes of infected red blood cells",
            "erythrocyte rigidity",
        ),
        "infected erythrocyte membrane",
    ),
    (
        (
            "therapeutic targeting",
            "therapeutic target",
            "drug target",
            "vaccine target",
        ),
        (
            "therapeutic targeting",
            "therapeutic target",
            "drug target",
            "vaccine target",
            "vaccine candidate",
            "anti adhesion",
            "binding blockade",
            "block binding",
            "blocked binding",
            "inhibit binding",
            "inhibited binding",
            "inhibition of binding",
        ),
        (
            "therapeutic targeting",
            "therapeutic target",
            "drug target",
            "vaccine target",
            "vaccine candidate",
            "anti adhesion",
            "binding blockade",
        ),
        "therapeutic target",
    ),
)

PARASITE_EXPRESSION_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "var_pfemp1": VAR_PFEMP1_ALTERNATIVE_ALIASES,
    "rifin": ("rifin", "rifins"),
    "stevor": ("stevor", "stevors"),



    "transcriptome": (PARASITE_OMICS_ANCHOR,),
}

T_CELL_REPERTOIRE_ALIASES = (
    "t cell receptor",
    "t cell receptors",
    "t cell repertoire",
    "t cell repertoires",
    "t lymphocyte receptor",
    "t lymphocyte repertoire",
    "tcr",
    "tcr repertoire",
    "tcr repertoires",
)
T_CELL_IDENTITY_ALIASES = (
    "t cell",
    "t cells",
    "t lymphocyte",
    "t lymphocytes",
    "cd4 t cell",
    "cd4 t cells",
    "cd8 t cell",
    "cd8 t cells",
)
MEMORY_T_CELL_ALIASES = (
    "memory t cell",
    "memory t cells",
    "memory cd4 t cell",
    "memory cd4 t cells",
    "memory cd8 t cell",
    "memory cd8 t cells",
    "t cell memory",
)
EARLY_CHILDHOOD_ALIASES = (
    "early childhood",
    "childhood",
    "child",
    "children",
    "pediatric",
    "paediatric",
)
ADOLESCENCE_ALIASES = (
    "adolescence",
    "adolescent",
    "adolescents",
    "teenage",
    "teenager",
    "youth",
)

ENTITY_STATE_SUFFIXES = (
    "positive",
    "negative",
    "expressing",
    "high",
    "low",
    "pos",
    "neg",
)

ENTITY_TOKEN_EXCLUSIONS = {
    "and",
    "are",
    "covid",
    "deg",
    "degs",
    "does",
    "doi",
    "find",
    "how",
    "http",
    "https",
    "pmcid",
    "pmid",
    "pfemp1re",
    "reported",
    "sars",
    "ipf",
    "what",
    "which",
}

DISEASE_CONTEXT_ALIASES = (
    "disease",
    "diseases",
    "fibrosis",
    "fibrotic",
    "ipf",
    "cancer",
    "tumor",
    "tumour",
    "covid 19",
    "sars cov 2",
    "infection",
    "infectious",
    "severity",
    "severe",
    "pathology",
    "pathogenesis",
    "syndrome",
)

NAMED_DISEASE_CONTEXTS: dict[str, tuple[str, ...]] = {
    "covid": ("covid 19", "covid", "sars cov 2"),
    "fibrosis": ("fibrosis", "fibrotic", "ipf"),
    "malaria": ("malaria", "plasmodium"),
    "cancer": ("cancer", "tumor", "tumour", "neoplasm"),
}

SEVERITY_CONTEXT_ALIASES = ("severity", "severe", "critical")
CEREBRAL_CONTEXT_ALIASES = (
    "cerebral malaria",
    "cerebral",
    "brain malaria",
    "brain",
    "retinopathy",
)

CLINICAL_PHENOTYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "cerebral": CEREBRAL_CONTEXT_ALIASES,
    "severe": SEVERITY_CONTEXT_ALIASES,
    "anemia": ("severe anemia", "severe anaemia", "anemia", "anaemia"),
    "metabolic": (
        "hyperlactatemia",
        "hyperlactataemia",
        "lactate",
        "acidosis",
    ),
    "respiratory": ("respiratory distress",),
    "uncomplicated": ("uncomplicated malaria", "uncomplicated"),
    "asymptomatic": ("asymptomatic infection", "asymptomatic malaria", "asymptomatic"),
}


def required_entity_anchor_groups(question: str) -> list[tuple[str, ...]]:
    """Return explicit high-specificity entities that retrieved evidence must carry.

    This is deliberately conservative. A digit-bearing biomedical token is
    treated as required only when the question also asks about genes, proteins,
    expression, or signatures. Known aliases keep exact-symbol filtering from
    discarding passages that use a spelled-out name instead.
    """
    current = current_question_from_context(question)
    normalized = normalize_text(current)
    has_entity_language = contains_any(
        normalized,
        ["gene", "protein", "expression", "expressed", "signature", "marker"],
    )
    has_symbol_lookup_language = bool(
        re.search(r"\b(?:paper|papers|study|studies)\s+(?:about|on)\b", normalized)
    )
    has_cell_context = contains_any(
        normalized,
        ["macroph", "makrof", "macrof", "cell", "cells", "zell"],
    )
    symbolic_state_tokens = {
        match.group(1)
        for match in re.finditer(
            r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,20})\s*[+-](?![A-Za-z0-9])",
            current,
        )
    }

    groups: list[tuple[str, ...]] = []
    if developmental_t_cell_repertoire_request(normalized):



        groups.extend((T_CELL_REPERTOIRE_ALIASES, MEMORY_T_CELL_ALIASES))
    elif explicit_t_cell_request(normalized) and not re.search(
        r"\bcd(?:4|8)\b", normalized
    ):




        groups.append(T_CELL_IDENTITY_ALIASES)
    expression_family_group = (
        ()
        if stage_composition_method_source_request(normalized)
        else requested_expression_family_alternatives(normalized)
    )
    if expression_family_group:




        groups.append(expression_family_group)
    elif requested_rifin_stevor_alternatives(normalized):



        groups.append(RIFIN_STEVOR_ALTERNATIVE_ALIASES)
    else:




        family_lookup_language = (
            has_entity_language
            or has_symbol_lookup_language
            or requested_rifin_or_stevor_source_search(normalized)
        )
        if family_lookup_language and contains_any(normalized, ["rifin", "rifins"]):
            groups.append(("rifin", "rifins"))
        if (
            family_lookup_language
            or contains_any(
                normalized,
                ["vaccine", "vaccination", "immunization", "immunisation"],
            )
        ) and contains_any(normalized, ["stevor", "stevors"]):
            groups.append(("stevor", "stevors"))
    for aliases in ENTITY_ALIASES.values():
        if any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalize_text(alias))}(?![a-z0-9])",
                normalized,
            )
            for alias in aliases
        ):
            groups.append(aliases)

    if not expression_family_group and re.search(
        r"\b(?:var genes?|pfemp1)\s+or\s+(?:var genes?|pfemp1)\b",
        normalized,
    ):
        groups.append(VAR_PFEMP1_ALTERNATIVE_ALIASES)

    known = {alias for group in groups for alias in group}
    disease_entities = {
        normalize_text(alias)
        for aliases in NAMED_DISEASE_CONTEXTS.values()
        for alias in aliases
    }
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9-]{1,20}\b", current):
        entity_token = strip_entity_state_suffix(token)
        has_state_suffix = entity_token != token
        has_symbolic_state = token in symbolic_state_tokens
        if not (
            has_entity_language
            or has_symbol_lookup_language
            or has_state_suffix
            or has_symbolic_state
            or has_cell_context
        ):
            continue
        has_digit = any(character.isdigit() for character in entity_token)
        if entity_token.islower() and not (has_cell_context and has_digit):
            continue
        candidate = normalize_text(entity_token)
        if not candidate or candidate in ENTITY_TOKEN_EXCLUSIONS:
            continue
        if candidate in disease_entities:
            continue
        has_digit = any(character.isdigit() for character in candidate)
        if not has_digit and not entity_token.isupper():
            continue
        if candidate.isdigit() or re.fullmatch(r"(?:19|20)\d{2}", candidate):
            continue
        if candidate in known or any(
            candidate == normalize_text(alias) for alias in known
        ):
            continue
        groups.append((candidate,))
        known.add(candidate)
    return groups


def requested_expression_family_alternatives(
    normalized_question: str,
) -> tuple[str, ...]:
    """Return one anchor group for the expression families in a source request.

    Families in a paper-discovery request are alternatives at passage level:
    a RIFIN-only report remains relevant to a request for RIFIN and STEVOR
    papers.  A single requested family still needs an anchor, while several
    families are flattened into one OR group.  The source-search and
    expression guards keep ordinary mechanistic comparisons unchanged.
    """

    requested_families = requested_expression_families(normalized_question)
    if not requested_families:
        return ()
    requested = [
        PARASITE_EXPRESSION_FAMILY_ALIASES[family]
        for family in requested_families
    ]
    return tuple(dict.fromkeys(alias for family in requested for alias in family))


def requested_expression_families(normalized_question: str) -> tuple[str, ...]:
    """Return explicitly named parasite-expression family keys."""

    if not re.search(
        r"\b(?:x?find|identify|list|show|which|what|are there)\b.{0,100}\b(?:papers?|studies|articles?)\b",
        normalized_question,
    ):
        return ()
    if not contains_any(
        normalized_question,
        ["expression", "expressed", "transcript", "transcriptome"],
    ):
        return ()

    requested: list[str] = []
    family_triggers = {
        "var_pfemp1": ("var", "pfemp1", "domain cassette"),
        "rifin": ("rifin", "rifins"),
        "stevor": ("stevor", "stevors"),
        "transcriptome": ("transcriptome",),
    }
    for family, triggers in family_triggers.items():
        if any(
            contains_normalized_phrase(normalized_question, trigger)
            for trigger in triggers
        ):
            requested.append(family)
    return tuple(requested)


def requested_rifin_stevor_alternatives(normalized_question: str) -> bool:
    """Detect an explicit RIFIN-or-STEVOR union in a source request."""

    if not re.search(
        r"\b(?:x?find|identify|list|show|which|what|are there)\b.{0,100}"
        r"\b(?:papers?|studies|articles?)\b",
        normalized_question,
    ):
        return False
    return bool(
        re.search(
            r"\brifins?\s+(?:(?:and(?: or)?|or)\s+)?stevors?\b"
            r"|\bstevors?\s+(?:(?:and(?: or)?|or)\s+)?rifins?\b",
            normalized_question,
        )
    )


def requested_rifin_or_stevor_source_search(normalized_question: str) -> bool:
    """Detect a paper search naming either variant-antigen family."""

    return bool(
        re.search(
            r"\b(?:x?find|identify|list|show|which|what|are there)\b.{0,100}"
            r"\b(?:papers?|studies|articles?)\b",
            normalized_question,
        )
        and contains_any(normalized_question, ["rifin", "rifins", "stevor", "stevors"])
    )


def requested_source_function_alternatives(
    normalized_question: str,
) -> tuple[str, ...]:
    """Return one OR group for explicitly requested biological functions.

    The vocabulary is deliberately limited to named relation classes. It is
    used only for source discovery, where a paper about an entity is not
    automatically a paper about every function listed in the question.
    """

    requested = [
        evidence_aliases
        for _, evidence_aliases, _, _ in requested_source_function_intents(
            normalized_question
        )
    ]
    return tuple(
        dict.fromkeys(alias for intent in requested for alias in intent)
    )


def requested_source_function_title_alternatives(
    normalized_question: str,
) -> tuple[str, ...]:
    """Return strong title-level aliases for requested biological functions."""

    requested = [
        title_aliases
        for _, _, title_aliases, _ in requested_source_function_intents(
            normalized_question
        )
    ]
    return tuple(
        dict.fromkeys(alias for intent in requested for alias in intent)
    )


def source_function_query_terms(normalized_question: str) -> tuple[str, ...]:
    """Return compact retrieval terms for the requested function union."""

    return tuple(
        query_term
        for _, _, _, query_term in requested_source_function_intents(
            normalized_question
        )
    )


def requested_source_function_intents(
    normalized_question: str,
) -> tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str], ...]:
    """Resolve named function intents, including natural binding syntax."""

    requested = [
        intent
        for intent in SOURCE_FUNCTION_INTENTS
        if any(
            contains_normalized_phrase(normalized_question, alias)
            for alias in intent[0]
        )
    ]
    receptor_binding = bool(
        re.search(
            r"\b(?:bind|binds|binding|bound)\b.{0,60}\breceptors?\b"
            r"|\breceptors?\b.{0,60}\b(?:bind|binds|binding|bound)\b",
            normalized_question,
        )
    )
    receptor_intent = SOURCE_FUNCTION_INTENTS[0]
    if receptor_binding and receptor_intent not in requested:
        requested.insert(0, receptor_intent)
    return tuple(requested)


def requested_immune_receptor_binding(normalized_question: str) -> bool:
    """Detect binding explicitly scoped to human immune receptors."""

    return bool(
        requested_source_function_intents(normalized_question)
        and any(
            contains_normalized_phrase(normalized_question, alias)
            for alias in IMMUNE_RECEPTOR_BINDING_ALIASES
        )
        and re.search(
            r"\b(?:bind|binds|binding|bound)\b.{0,80}\breceptors?\b"
            r"|\breceptors?\b.{0,80}\b(?:bind|binds|binding|bound)\b"
            r"|\b(?:lilrb1|lair1|kir2dl1|kir2ds1)\b",
            normalized_question,
        )
    )


def required_context_anchor_groups(question: str) -> list[tuple[str, ...]]:
    """Return broad context constraints explicitly requested by the user."""
    normalized = normalize_text(current_question_from_context(question))
    groups: list[tuple[str, ...]] = []
    if developmental_t_cell_repertoire_request(normalized):




        groups.extend((EARLY_CHILDHOOD_ALIASES, ADOLESCENCE_ALIASES))
    requested_contexts = [
        aliases
        for aliases in NAMED_DISEASE_CONTEXTS.values()
        if any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalize_text(alias))}(?![a-z0-9])",
                normalized,
            )
            for alias in aliases
        )
    ]
    if requested_contexts:




        groups.append(
            tuple(
                dict.fromkeys(
                    alias for group in requested_contexts for alias in group
                )
            )
        )


    elif contains_any(
        normalized,
        ["disease", "diseases", "desease", "deseases", "diseae", "deseae"],
    ):
        groups.append(DISEASE_CONTEXT_ALIASES)
    stage_method_search = stage_composition_method_source_request(normalized)
    phenotype_alternatives = (
        () if stage_method_search else requested_clinical_phenotype_alternatives(normalized)
    )
    if phenotype_alternatives:
        groups.append(phenotype_alternatives)

    requests_severity = any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalize_text(alias))}(?![a-z0-9])",
            normalized,
        )
        for alias in SEVERITY_CONTEXT_ALIASES
    )
    requests_cerebral = any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalize_text(alias))}(?![a-z0-9])",
            normalized,
        )
        for alias in CEREBRAL_CONTEXT_ALIASES
    )





    cerebral_or_severe = bool(
        re.search(
            r"\b(?:cerebral(?: malaria)?|brain(?: malaria)?|retinopathy)\s+"
            r"(?:or|and or)\s+(?:severity|severe|critical)(?: malaria)?\b",
            normalized,
        )
        or re.search(
            r"\b(?:severity|severe|critical)(?: malaria)?\s+(?:or|and or)\s+"
            r"(?:cerebral(?: malaria)?|brain(?: malaria)?|retinopathy)\b",
            normalized,
        )
    )
    if stage_method_search:




        pass
    elif phenotype_alternatives:
        pass
    elif requests_cerebral and requests_severity and cerebral_or_severe:
        groups.append(
            tuple(dict.fromkeys((*CEREBRAL_CONTEXT_ALIASES, *SEVERITY_CONTEXT_ALIASES)))
        )
    else:
        if requests_cerebral:
            groups.append(CEREBRAL_CONTEXT_ALIASES)
        if requests_severity:
            groups.append(SEVERITY_CONTEXT_ALIASES)
    return groups


def requested_clinical_phenotype_alternatives(
    normalized_question: str,
) -> tuple[str, ...]:
    """Return one anchor group for phenotypes in an expression source query.

    A source can be relevant when it addresses any one requested phenotype,
    so several named outcomes form one OR group.  A single named outcome is
    still returned as a mandatory anchor.  ``Severe anaemia`` is treated as
    the anaemia phenotype rather than also activating generic severity.
    """

    if not requested_expression_family_alternatives(normalized_question):
        return ()
    triggers = {
        "cerebral": ("cerebral", "brain malaria", "retinopathy"),
        "anemia": ("anemia", "anaemia"),
        "metabolic": ("hyperlactatemia", "hyperlactataemia", "acidosis", "lactate"),
        "respiratory": ("respiratory distress",),
        "uncomplicated": ("uncomplicated",),
        "asymptomatic": ("asymptomatic",),
    }
    requested = [
        CLINICAL_PHENOTYPE_ALIASES[phenotype]
        for phenotype, aliases in triggers.items()
        if any(
            contains_normalized_phrase(normalized_question, alias)
            for alias in aliases
        )
    ]
    requests_generic_severity = any(
        contains_normalized_phrase(normalized_question, alias)
        for alias in ("severity", "critical", "severe malaria")
    ) or bool(
        re.search(
            r"(?<![a-z0-9])severe(?![a-z0-9])"
            r"(?!\s+(?:anemia|anaemia))",
            normalized_question,
        )
    )
    if requests_generic_severity:
        requested.insert(1 if requested else 0, CLINICAL_PHENOTYPE_ALIASES["severe"])
    if not requested:
        return ()
    return tuple(dict.fromkeys(alias for phenotype in requested for alias in phenotype))


def strip_entity_state_suffix(token: str) -> str:
    """Reduce ``SPP1-positive``/``CD8high`` to the searchable entity symbol."""
    value = str(token or "")
    hyphenated = re.fullmatch(
        rf"(.+?)-(?:{'|'.join(ENTITY_STATE_SUFFIXES)})",
        value,
        flags=re.IGNORECASE,
    )
    if hyphenated:
        return hyphenated.group(1)
    compact = re.fullmatch(
        rf"([A-Za-z]+\d+[A-Za-z0-9]*?)(?:{'|'.join(ENTITY_STATE_SUFFIXES)})",
        value,
        flags=re.IGNORECASE,
    )
    return compact.group(1) if compact else value


def entity_anchor_query(group: tuple[str, ...]) -> str:





    if group and normalize_text(group[0]) == "spp1":
        return "spp1 osteopontin"
    compact_aliases = [
        alias
        for alias in group
        if alias
        and alias != PARASITE_OMICS_ANCHOR
        and len(normalize_text(alias).split()) == 1
    ]
    if PARASITE_OMICS_ANCHOR in group:
        compact_aliases.append("transcriptome")
    return " ".join(dict.fromkeys(compact_aliases or [group[0]]))


NAMED_STUDY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<surname>[A-ZÀ-Þ][A-Za-zÀ-ÿ'’.-]{1,40})"
    r"\s+et\s+al\.?"
)

NAMED_STUDY_QUERY_STOPWORDS = {
    "and",
    "according",
    "against",
    "also",
    "analyse",
    "analyze",
    "answer",
    "compare",
    "did",
    "each",
    "either",
    "from",
    "for",
    "into",
    "the",
    "keep",
    "main",
    "paper",
    "report",
    "reported",
    "separate",
    "study",
    "their",
    "these",
    "those",
    "which",
    "with",
    "without",
}


def explicit_named_study_references(question: str) -> list[NamedStudyReference]:
    """Extract high-confidence ``Surname et al.`` references and nearby hints.

    The surrounding context is retained because a surname can identify several
    local papers.  A nearby short title, corpus nickname, journal, or the word
    ``preprint`` can then disambiguate the intended report without a model call.
    """

    current = current_question_from_context(question)
    matches = list(NAMED_STUDY_PATTERN.finditer(current))
    references: list[NamedStudyReference] = []
    seen: set[tuple[str, str | None, str]] = set()
    for index, match in enumerate(matches):
        right = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else min(len(current), match.end() + 220)
        )
        context = compact(current[match.start() : right])
        tail = current[match.end() : min(len(current), match.end() + 48)]
        year_match = re.match(
            r"\s*(?:,\s*)?(?:\([^)]*?\b((?:19|20)\d{2})\b[^)]*\)|"
            r"((?:19|20)\d{2})\b)",
            tail,
        )
        year = next(
            (
                group
                for group in (year_match.groups() if year_match else ())
                if group
            ),
            None,
        )
        reference = NamedStudyReference(
            surname=match.group("surname"),
            year=year,
            context=context,
        )
        key = (
            normalize_text(reference.surname),
            reference.year,
            normalize_text(reference.context),
        )
        if key in seen:
            continue
        seen.add(key)
        references.append(reference)
    return references


def named_study_lookup_query(
    reference: NamedStudyReference,
    question: str,
) -> str:
    """Build one compact retrieval view for an explicitly named report."""

    surname = normalize_text(reference.surname)
    year = str(reference.year or "")
    named_surnames = {
        normalize_text(item.surname)
        for item in explicit_named_study_references(question)
    }
    excluded = {
        *NAMED_STUDY_QUERY_STOPWORDS,
        *named_surnames,
        surname,
        "et",
        "al",
        year,
    }

    def distinctive_tokens(text: str) -> list[str]:
        return [
            token
            for token in normalize_text(text).split()
            if len(token) >= 3
            and token not in excluded
            and not re.fullmatch(r"(?:19|20)\d{2}", token)
        ]

    context_tokens = distinctive_tokens(reference.context)
    if len(context_tokens) < 3:
        context_tokens.extend(distinctive_tokens(current_question_from_context(question)))
    terms = list(
        dict.fromkeys(
            [reference.surname, *([year] if year else []), *context_tokens[:10]]
        )
    )
    return compact(" ".join(terms))


def build_retrieval_queries(question: str, task: str = "answer", max_queries: int = 4) -> list[RetrievalQuery]:
    """Build deterministic search fan-out queries for broad or context-heavy chat turns."""
    raw_question = question or ""
    clean_question = compact(raw_question)
    if not clean_question:
        return []

    current_question = current_question_from_context(clean_question)
    wrapped_question = unwrap_explicit_query_instruction(clean_question)
    main_question = current_question if wrapped_question != clean_question else clean_question
    queries = [RetrievalQuery("main", main_question)]
    previous_titles = previous_titles_from_context(raw_question)
    normalized_current = normalize_text(current_question or clean_question)

    if current_question and current_question != clean_question:
        queries.append(RetrievalQuery("current", current_question))
    for index, reference in enumerate(
        explicit_named_study_references(current_question or clean_question),
        start=1,
    ):
        queries.append(
            RetrievalQuery(
                f"named_study_{index}",
                named_study_lookup_query(reference, current_question or clean_question),
                intent="named_source",
            )
        )
    for index, title in enumerate(
        explicit_quoted_work_titles(current_question or clean_question),
        start=1,
    ):
        queries.append(
            RetrievalQuery(
                f"quoted_title_{index}",
                title,
                intent="named_source",
            )
        )
    if previous_titles:
        queries.append(RetrievalQuery("followup_sources", compact(f"{current_question or clean_question} {' '.join(previous_titles)}")))
    curated_expansions = rule_based_expansions(normalized_current, task=task)


    queries.extend(
        expansion
        for expansion in curated_expansions
        if expansion.label == "data_accessibility"
    )
    entity_groups = required_entity_anchor_groups(current_question)
    for index, group in enumerate(entity_groups, start=1):
        queries.append(
            RetrievalQuery(
                f"required_entity_{index}",
                entity_anchor_query(group),
                intent="required_entity",
            )
        )
    function_terms = source_function_query_terms(normalized_current)
    if task == "sources" and entity_groups and function_terms:
        entity_terms = " ".join(
            dict.fromkeys(
                term
                for group in entity_groups
                for term in entity_anchor_query(group).split()
            )
        )
        queries.append(
            RetrievalQuery(
                "entity_function_intent",
                " ".join((entity_terms, *function_terms)),
                intent="required_source_intent",
            )
        )
    if task == "sources" and requested_rifin_or_stevor_source_search(
        normalized_current
    ):
        queries.extend(rifin_stevor_source_function_queries(normalized_current))
    if task == "sources" and is_epcr_pfemp1_intervention_source_request(
        current_question
    ):
        queries.extend(
            RetrievalQuery(label, query, intent=intent)
            for label, query, intent in EPCR_PFEMP1_INTERVENTION_QUERY_SPECS
        )


    queries.extend(
        expansion
        for expansion in curated_expansions
        if expansion.label != "data_accessibility"
    )
    if looks_like_virulence_question(normalized_current):
        queries.append(RetrievalQuery("virulence_mechanisms", VIRULENCE_QUERY))
    if looks_like_broad_malaria_question(normalized_current):
        queries.append(RetrievalQuery("host_parasite_context", HOST_INTERACTION_QUERY))
    if task == "sources":
        queries.append(RetrievalQuery("paper_lookup", source_lookup_query(current_question or clean_question)))

    return dedupe_queries(queries)[: max(1, max_queries)]


def rifin_stevor_source_function_queries(
    normalized_question: str,
) -> list[RetrievalQuery]:
    """Reserve direct experimental axes in a broad RIFIN/STEVOR search.

    One combined bag-of-words view was insufficient: high-frequency family
    reviews and localization reports exhausted the result budget before direct
    RIFIN-receptor and STEVOR-rosetting experiments. These bounded views use
    relation-class vocabulary only and do not encode report identities.
    """

    function_terms = set(source_function_query_terms(normalized_question))
    queries: list[RetrievalQuery] = []
    receptor_binding_requested = bool(
        function_terms.intersection({"receptor binding", "immune evasion"})
        or (
            contains_any(normalized_question, ["bind", "binding"])
            and contains_any(normalized_question, ["receptor", "receptors"])
        )
    )
    if receptor_binding_requested:
        queries.append(
            RetrievalQuery(
                "rifin_receptor_binding",
                "RIFIN RIFINs immune receptor binding KIR LILRB1 LAIR1",
                intent="required_source_facet",
            )
        )
    if "rosetting" in function_terms:
        queries.append(
            RetrievalQuery(
                "stevor_binding_rosetting",
                "STEVOR STEVORs erythrocyte binding rosetting glycophorin C "
                "merozoite invasion",
                intent="required_source_facet",
            )
        )
    if "infected erythrocyte membrane" in function_terms:
        queries.append(
            RetrievalQuery(
                "rifin_stevor_membrane",
                "RIFIN STEVOR infected erythrocyte membrane surface localization "
                "topology rigidity",
                intent="required_source_facet",
            )
        )
    return queries


def rule_based_expansions(normalized_question: str, task: str = "answer") -> list[RetrievalQuery]:
    expansions = []
    stevor_binding_mechanism_request = (
        contains_normalized_phrase(normalized_question, "stevor")
        and any(
            contains_normalized_phrase(normalized_question, term)
            for term in (
                "glycophorin c",
                "gpc",
                "merozoite invasion",
                "pfemp1 independent rosetting",
            )
        )
    )
    rifin_chromatin_rosetting_request = (
        contains_normalized_phrase(normalized_question, "rifin")
        and any(
            contains_normalized_phrase(normalized_question, term)
            for term in ("pfcdp", "h3k9me3", "chromodomain", "conditional knockout")
        )
    )
    stevor_vaccine_request = (
        contains_normalized_phrase(normalized_question, "stevor")
        and any(
            contains_normalized_phrase(normalized_question, term)
            for term in ("vaccine", "vaccination", "immunization", "immunisation")
        )
    )
    if stevor_binding_mechanism_request:
        expansions.append(
            RetrievalQuery(
                "stevor_glycophorin_rosetting",
                "STEVOR glycophorin C merozoite invasion PfEMP1-independent "
                "rosetting",
            )
        )
    if rifin_chromatin_rosetting_request:
        expansions.append(
            RetrievalQuery(
                "rifin_chromatin_rosetting",
                "PfCDP chromodomain H3K9me3 conditional knockout RIFIN expression "
                "rosette formation",
            )
        )
    if stevor_vaccine_request:
        expansions.extend(
            [
                RetrievalQuery(
                    "stevor_vaccine_preclinical",
                    "STEVOR vaccine human serum reactivity mouse immunogenicity "
                    "in-silico prediction",
                ),
                RetrievalQuery(
                    "stevor_vaccine_clinical_efficacy",
                    "STEVOR vaccine human interventional clinical trial efficacy severe "
                    "malaria",
                ),
            ]
        )
    if developmental_t_cell_repertoire_request(normalized_question):
        expansions.append(
            RetrievalQuery(
                "developmental_t_cell_repertoire",
                "memory T cell TCR repertoire childhood adolescence pediatric "
                "longitudinal age trajectory",
            )
        )
    stage_source_request = task == "sources" and stage_composition_method_source_request(
        normalized_question
    )
    if stage_source_request:
        expansions.extend(
            [
                RetrievalQuery(
                    "clinical_stage_composition_confounding",
                    "Plasmodium falciparum developmental stage life cycle composition "
                    "clinical transcriptome confounding patient isolates RNA-seq microarray",
                ),
                RetrievalQuery(
                    "stage_estimation_deconvolution_methods",
                    "BLASE deconvolution pseudotime parasite age synchrony stage "
                    "composition bulk RNA-seq single-cell reference Plasmodium falciparum",
                ),
                RetrievalQuery(
                    "stage_adjusted_clinical_transcriptomics",
                    "clinical malaria transcriptome developmental stage adjustment "
                    "differential gene expression host parasite association severe "
                    "asymptomatic uncomplicated",
                ),
            ]
        )
    requested_families = requested_expression_families(normalized_question)
    if task == "sources" and requested_families:


        if "var_pfemp1" in requested_families:
            expansions.append(
                RetrievalQuery(
                    "var_pfemp1_clinical_expression",
                    "var PfEMP1 domain cassette CIDR DBL expression severe cerebral "
                    "malaria anemia respiratory distress clinical cohort qPCR",
                )
            )
        if {"rifin", "stevor"}.intersection(requested_families):
            expansions.append(
                RetrievalQuery(
                    "rifin_stevor_clinical_expression",
                    "RIFIN STEVOR expression clinical malaria severe uncomplicated "
                    "asymptomatic patient cohort transcript",
                )
            )
        if "transcriptome" in requested_families:
            expansions.append(
                RetrievalQuery(
                    "core_transcriptome_clinical_expression",
                    "Plasmodium falciparum core parasite transcriptome RNA-seq clinical "
                    "severe uncomplicated asymptomatic malaria gene expression",
                )
            )
        if contains_any(
            normalized_question,
            ["developmental stage", "parasite stage", "stage adjusted", "stage adjustment"],
        ):
            expansions.append(
                RetrievalQuery(
                    "clinical_expression_stage_adjustment",
                    "parasite developmental stage age adjustment circulation time "
                    "transcriptome var expression cerebral malaria severe anemia "
                    "hyperlactatemia acidosis respiratory distress",
                )
            )
    if task == "gaps" and contains_stem_pair(
        normalized_question,
        left=("transcri", "express", "gene"),
        right=("clinic", "syndrom", "phenotyp", "disease"),
    ):
        expansions.extend(
            [
                RetrievalQuery(
                    "parasite_transcript_clinical_phenotypes",
                    "Plasmodium falciparum parasite transcriptome gene expression clinical syndrome cerebral malaria severe anemia respiratory distress uncomplicated malaria",
                ),
                RetrievalQuery(
                    "syndrome_specific_expression_limits",
                    "parasite gene expression syndrome-specific association cohort sample size geography clinical phenotype limitations replication",
                ),
                RetrievalQuery(
                    "transcript_measurement_comparability",
                    "parasite transcript expression RNA-seq qRT-PCR microarray stage composition parasite biomass clinical isolate comparability confounding",
                ),
            ]
        )
    for label, triggers, query in RULED_EXPANSIONS:
        if label == "artemisinin_resistance" and not (
            contains_any(normalized_question, ["artemisinin", "kelch13"])
            or (
                contains_normalized_phrase(normalized_question, "k13")
                and contains_any(
                    normalized_question,
                    ["malaria", "plasmodium", "falciparum", "resistance"],
                )
            )
        ):


            continue
        if label == "antifolate_resistance" and not contains_any(
            normalized_question,
            [
                "antifolate",
                "pyrimethamine",
                "sulfadoxine",
                "malaria",
                "plasmodium",
                "falciparum",
                "drug resistance",
                "antimalarial resistance",
            ],
        ):

            continue
        if (
            (stevor_binding_mechanism_request or rifin_chromatin_rosetting_request)
            and label
            in {
                "pfemp1_epcr_severe",
                "cytoadherence_sequestration",
                "molecular_targets",
            }
        ):



            continue
        if stevor_vaccine_request and label in {
            "molecular_targets",
            "vaccine_clinical_development",
            "vaccine_development_barriers",
        }:



            continue
        if stage_source_request and label in {
            "disease_signature_context",
            "dataset_discovery",
            "method_tools",
            "molecular_targets",
            "var_expression_phenotype",
        }:




            continue
        if label == "host_single_cell_immunity":
            explicit_t_cell = explicit_t_cell_request(normalized_question)
            trigger_matches = explicit_t_cell or any(
                contains_normalized_phrase(normalized_question, trigger)
                for trigger in triggers
                if normalize_text(trigger) != "t cell"
            )
            if not trigger_matches:


                continue
            if not contains_any(
                normalized_question,
                ["malaria", "plasmodium", "falciparum", "pfemp1", "var gene"],
            ):




                continue
            explicit_monocyte = any(
                contains_normalized_phrase(normalized_question, alias)
                for alias in ("monocyte", "monocytes", "macrophage", "macrophages")
            )
            if explicit_t_cell and not explicit_monocyte:
                query = (
                    "host scRNA-seq T cell state CD4 CD8 malaria severity "
                    "Plasmodium infection"
                )
            elif explicit_monocyte and not explicit_t_cell:
                query = (
                    "host scRNA-seq monocyte macrophage state malaria severity "
                    "Plasmodium infection"
                )
        if contains_any(normalized_question, triggers):
            expansions.append(RetrievalQuery(label, query))
    if task == "gaps" and contains_any(normalized_question, ["var", "pfemp", "immune", "host"]):
        expansions.append(
            RetrievalQuery(
                "paired_gap_context",
                "paired parasite var expression host immune state scRNA-seq clinical severity sex age cohort",
            )
        )
    if task == "hypothesis" and contains_any(normalized_question, ["var", "pfemp", "severe", "asymptomatic"]):
        expansions.append(
            RetrievalQuery(
                "hypothesis_mechanism_context",
                "testable hypothesis PfEMP1 var expression host immunity clinical phenotype severe asymptomatic malaria",
            )
        )
    return sorted(
        expansions,
        key=lambda query: 0 if query.label == "data_accessibility" else 1,
    )


def has_sufficient_curated_source_expansions(question: str, minimum: int = 2) -> bool:
    """Return whether curated domain expansions already cover a paper search.

    A topical source search normally benefits from LLM query translation.  For
    well-covered PfEMP1/malaria questions, however, invoking the same large
    model before the later paper-explanation call only repeats work.  Requiring
    multiple distinct curated expansions keeps ambiguous and non-English
    searches on the LLM planner path.
    """
    normalized = normalize_text(current_question_from_context(question))
    labels = {
        expansion.label
        for expansion in rule_based_expansions(normalized, task="sources")
    }
    return len(labels) >= max(1, int(minimum))


def contains_stem_pair(
    normalized_question: str,
    *,
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> bool:
    tokens = tuple(normalized_question.split())
    return any(any(token.startswith(stem) for token in tokens) for stem in left) and any(
        any(token.startswith(stem) for token in tokens) for stem in right
    )


def current_question_from_context(question: str) -> str:
    matches = re.findall(r"Current user question:\s*(.+)", question)
    selected = compact(matches[-1]) if matches else compact(question)
    return unwrap_explicit_query_instruction(selected)


def unwrap_explicit_query_instruction(question: str) -> str:
    """Remove a UI/prompt wrapper while preserving the scientific query."""

    clean = compact(question)
    unwrapped = re.sub(
        r"^(?:use|run)\s+this\s+as\s+(?:the\s+)?(?:(?:originrag|pfemp1re)\s+)?query\s*:\s*>?\s*",
        "",
        clean,
        count=1,
        flags=re.IGNORECASE,
    )
    return compact(unwrapped) or clean


def stage_composition_method_source_request(normalized_question: str) -> bool:
    """Detect broad paper searches about stage-composition confounding."""

    return bool(
        re.search(
            r"\b(?:x?find|identify|list|show|which|what|are there)\b.{0,100}\b(?:papers?|studies|articles?)\b",
            normalized_question,
        )
        and contains_any(normalized_question, ["malaria", "plasmodium", "falciparum"])
        and contains_any(
            normalized_question,
            [
                "stage composition",
                "life cycle composition",
                "lifecycle composition",
                "intraerythrocytic life cycle",
                "stage confound",
                "confound",
                "deconvol",
            ],
        )
        and contains_any(
            normalized_question,
            ["confound", "adjust", "deconvol", "control", "estimate", "analysis"],
        )
    )


def developmental_t_cell_repertoire_request(normalized_question: str) -> bool:
    """Detect an explicit developmental T-cell-repertoire literature question."""

    has_t_cell = any(
        contains_normalized_phrase(normalized_question, alias)
        for alias in ("t cell", "t cells", "t lymphocyte", "tcr")
    )
    has_repertoire = contains_any(
        normalized_question,
        ["repertoire", "repertoires", "t cell receptor", "tcr"],
    )
    has_memory = contains_any(normalized_question, ["memory", "memory t cell"])
    has_childhood = contains_any(
        normalized_question,
        ["early childhood", "childhood", "child", "children", "pediatric", "paediatric"],
    )
    has_adolescence = contains_any(
        normalized_question,
        ["adolescence", "adolescent", "teenage", "youth"],
    )
    return (
        has_memory
        and has_t_cell
        and has_repertoire
        and has_childhood
        and has_adolescence
    )


def explicit_t_cell_request(normalized_question: str) -> bool:
    """Return whether the current question names T cells as an evidence target."""

    return any(
        contains_normalized_phrase(normalized_question, alias)
        for alias in T_CELL_IDENTITY_ALIASES
    )


def contains_normalized_phrase(normalized_text: str, phrase: str) -> bool:
    """Match a normalized phrase at alphanumeric token boundaries."""

    needle = normalize_text(phrase)
    if not needle:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
            normalized_text,
        )
    )


def previous_titles_from_context(question: str) -> list[str]:
    titles: list[str] = []
    for raw_line in question.splitlines():
        if not raw_line.startswith("Previous cited paper titles:"):
            continue
        raw_titles = raw_line.removeprefix("Previous cited paper titles:")
        for title in raw_titles.split(";"):
            clean_title = compact(title)
            if clean_title:
                titles.append(clean_title)
    return titles[:6]


def looks_like_virulence_question(normalized_question: str) -> bool:
    return any(
        term in normalized_question
        for term in ["virulent", "virulence", "pathogenic", "pathogenesis", "severe disease", "why is plasmodium"]
    )


def looks_like_broad_malaria_question(normalized_question: str) -> bool:
    return any(term in normalized_question for term in ["why", "warum", "overview", "broad", "mechanism", "mechanismus"]) and any(
        term in normalized_question for term in ["malaria", "plasmodium", "falciparum"]
    )


def contains_any(normalized_question: str, terms: list[str]) -> bool:
    return any(normalize_text(term) in normalized_question for term in terms)


def source_lookup_query(question: str) -> str:
    clean = compact(question)
    lookup_noise = [
        "which paper",
        "what paper",
        "which source",
        "what source",
        "welches paper",
        "welche quelle",
        "papers",
        "quelle",
        "sources",
        "paper",
    ]
    lowered = clean.lower()
    for phrase in lookup_noise:
        lowered = re.sub(rf"\b{re.escape(phrase)}\b", " ", lowered)
    return compact(lowered) or clean


def classify_paper_search_mode(question: str) -> str:
    """Separate identifiable-paper lookups from open-ended topic discovery."""
    current = current_question_from_context(question)
    normalized = normalize_text(current)
    if not normalized:
        return "targeted_lookup"

    identifier_patterns = [
        r"\b10\.\d{4,9}/\S+",
        r"\bpm(?:id|cid)\s*[:#]?\s*(?:pmc)?\d+\b",
    ]
    if any(re.search(pattern, current, flags=re.IGNORECASE) for pattern in identifier_patterns):
        return "targeted_lookup"

    if explicit_quoted_work_titles(current):
        return "targeted_lookup"

    if re.search(
        r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'’.-]{2,}\s+et\s+al\.?[,]?\s*"
        r"(?:19|20)\d{2}\b",
        current,
    ):
        return "targeted_lookup"




    targeted_cue_patterns = [
        r"\b(?:which|what)\s+(?:paper|study|source|reference)\s+"
        r"(?:was|is)\s+(?:that|this|it)\b",
        r"\b(?:which|what)\s+(?:paper|study|source|reference)\b.{0,50}"
        r"\b(?:this|that|it)\b",
        r"\bwelches\s+paper\b",
        r"\bwelche\s+(?:quelle|referenz|studie)\b",
        r"\b(?:paper|study)\s+by\b",
        r"\b(?:paper|studie)\s+von\b",
        r"\btitle\s+of\b",
        r"\bexact\s+title\b",
        r"\btitled\b",
        r"\b(?:hieß|hiess)\s+das\s+paper\b",
        r"\bbib\s*key\b",
        r"\bcitation\s+key\b",
        r"\bpaper\s+id\b",
    ]
    if any(re.search(pattern, normalized) for pattern in targeted_cue_patterns):
        return "targeted_lookup"
    return "topical_discovery"


def explicit_quoted_work_titles(question: str) -> tuple[str, ...]:
    """Return long quoted titles only when the surrounding text names a work."""

    if not re.search(
        r"\b(?:exact title|titled|paper|article)\b.{0,50}[\"“]",
        question,
        flags=re.IGNORECASE,
    ):
        return ()
    spans = re.findall(r'["“”]([^"“”]+)["“”]', question)
    return tuple(
        compact(span)
        for span in spans
        if len(span.split()) >= 4 and len(span) >= 20
    )


def dedupe_queries(queries: list[RetrievalQuery]) -> list[RetrievalQuery]:
    kept: list[RetrievalQuery] = []
    seen = set()
    for query in queries:
        normalized = normalize_text(query.query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        kept.append(query)
    return kept


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
