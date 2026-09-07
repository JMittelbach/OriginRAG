from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceTypeAssessment:
    """Conservative, automatic document-design classification.

    This describes the design of a document. Whether it directly supports a
    particular claim is a separate, claim-relative question.
    """

    source_type: str
    label: str
    confidence: str
    direct_reporting: str
    signals: tuple[str, ...]
    automatic: bool = True

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["signals"] = list(self.signals)
        return payload


_LABELS = {
    "primary_research": "Primary research",
    "systematic_review_meta_analysis": "Systematic review / meta-analysis",
    "narrative_review": "Narrative review",
    "reanalysis_secondary_data": "Reanalysis / secondary data",
    "protocol": "Protocol",
    "editorial_commentary": "Editorial / commentary",
    "uncertain": "Source type uncertain",
}


def classify_source_document(record: dict, text: str = "") -> dict[str, object]:
    """Classify document design using high-precision bibliographic/text signals.

    The classifier intentionally abstains when evidence is ambiguous. In
    particular, a generic ``journal article`` label or first-person prose is
    insufficient to call a paper primary research.
    """

    title = _norm(record.get("title"))
    journal = _norm(record.get("journal"))
    declared = _norm(record.get("paper_type") or record.get("publication_type"))
    front = _norm(text)[:24_000]
    joined = " ".join(part for part in (title, declared, front) if part)

    if _matches(title, r"\b(?:study |review )?protocol\b", r"\bprotocol for (?:a )?systematic review\b"):
        return _assessment("protocol", "high", "no", "protocol stated in title")

    if _matches(
        title + " " + declared,
        r"\bsystematic review\b",
        r"\bmeta[ -]analysis\b",
        r"\bscoping review\b",
        r"\boverview of reviews\b",
    ):
        return _assessment(
            "systematic_review_meta_analysis",
            "high",
            "no",
            "evidence synthesis stated in title or article type",
        )

    explicit_review_self_description = _has_unattributed_review_self_description(
        front[:8_000]
    )
    if explicit_review_self_description:
        return _assessment(
            "narrative_review",
            "high",
            "no",
            "document explicitly identifies itself as a review",
        )

    explicit_secondary_design = _matches(
        title + " " + declared,
        r"\bsecondary analys(?:is|es)\b",
        r"\bre[ -]?analys(?:is|ed|es|ing)\b",
    ) or _matches(
        front,
        r"\bthis (?:study|article|report) (?:is|was) (?:a )?secondary analysis\b",
        r"\bthis was (?:a )?secondary analysis\b",
        r"\b(?:study design|design)\s*:?[ ]*(?:a )?secondary analysis\b",
        r"\bwe (?:conducted|performed|undertook) (?:a )?secondary analys(?:is|es)\b",
        r"\bthis (?:study|article|report) (?:is|was) (?:a )?re[ -]?analysis\b",
    )
    existing_data_reuse = _matches(
        front,
        r"\breusing (?:an? |the )?(?:existing|published) dataset\b",
        r"\b(?:analy[sz]ed|used|reused|compared)\b.{0,80}"
        r"\b(?:publicly available|existing|published)\b.{0,50}"
        r"\b(?:data(?:set)?s?|profiles?)\b",
        r"\bpublished\b.{0,60}\b(?:data(?:set)?s?|profiles?)\b.{0,240}\b(?:were|was) used for this study\b",




        r"\b(?:data(?:set)?s?|(?:expression |transcript(?:ion|omic) )?profiles?)\b.{0,140}\bpreviously described\b",
    )
    original_method_signal = _matches(
        front,
        r"\bwe present (?:a |the )?new (?:computational )?method\b",
        r"\bhere,? we present (?:this |a |the )?novel (?:tool|method|algorithm|framework|pipeline)\b",
        r"\bwe (?:developed|introduce|introduced) (?:a |the )?(?:new |novel )?"
        r"(?:tool|method|algorithm|framework|pipeline)\b",
    )
    if explicit_secondary_design or (existing_data_reuse and not original_method_signal):
        return _assessment(
            "reanalysis_secondary_data",
            "high",
            "no",
            "secondary analysis or reanalysis stated explicitly",
        )

    if _matches(
        title + " " + declared,
        r"\bnarrative review\b",
        r"\breview article\b",
        r"\bperspective\b",
        r"\bcommentary\b",
    ) or _matches(title, r"^(?:a |an )?review of\b"):
        kind = "editorial_commentary" if _matches(joined, r"\b(?:perspective|commentary|editorial)\b") else "narrative_review"
        return _assessment(kind, "high", "no", "review/commentary stated in title or article type")

    if journal in {
        "microbiology and molecular biology reviews",
        "advances in parasitology",
    }:
        return _assessment("narrative_review", "high", "no", "review-only publication venue")

    primary_signals: list[str] = []
    if original_method_signal:
        primary_signals.append("original computational method stated")
    if _matches(
        joined,
        r"\bwe (?:enrolled|recruited|randomi[sz]ed|collected|sampled)\b",
        r"\bparticipants? (?:were|was) (?:enrolled|recruited|randomi[sz]ed)\b",
        r"\bpatients?\b.{0,100}\bin this study\b.{0,60}\bwere (?:enrolled|recruited|randomi[sz]ed)\b",
        r"\bparticipants? provided written informed consent for sample collection\b",
    ):
        primary_signals.append("original recruitment or sample collection")
    if _matches(
        joined,
        r"\b(?:we|the authors) (?:performed|conducted|carried out) (?:an? )?(?:experiment|assay|survey|trial|study)\b",
        r"\bprospective cohort\b",
        r"\brandomi[sz]ed controlled trial\b",
        r"\bin this study,? we (?:selected|measured|tested|examined|investigated|analysed|analyzed|evaluated|compared)\b",
        r"\bhere,? we (?:identify|identified|show|demonstrate|report|test|measure|characteri[sz]e|determine)\b",
        r"\bhere,? we (?:used|combined|investigated|analysed|analyzed)\b",
        r"\bhere,? we performed (?:single[- ]cell )?(?:rna sequencing|rna[- ]?seq|sequencing|profiling)\b",
        r"\bwe investigated\b.{0,160}\b(?:patients?|participants?|samples?|regions?|expression|distribution)\b",
    ):
        primary_signals.append("original study or experiment stated")
    if _matches(
        joined,
        r"\bwe analy[sz]ed (?:parasites?|participants?|patients?|samples?|specimens?|clinical data|transcriptomes?|genomes?)\b",
    ):
        primary_signals.append("analysis of study samples or participants stated")
    if _matches(
        joined,
        r"\bwe used (?:q?rt[- ]?pcr|rt[- ]?qpcr|rna[- ]?seq|sequencing|microscopy|flow cytometry|elisa)\b",
    ):
        primary_signals.append("laboratory or sequencing assay stated")
    if _matches(joined, r"\b(?:materials and methods|patients and methods)\b") and _matches(
        joined, r"\bresults\b"
    ):
        primary_signals.append("methods and results sections detected")
    if _matches(front[:8_000], r"\bresearch article\b"):
        primary_signals.append("research article header detected")
    if _matches(front[:12_000], r"\bmethods?\s*[:.]", r"\bmethodology\s*[:.]") and _matches(
        front[:12_000], r"\bresults?\s*[:.]"
    ):
        primary_signals.append("structured methods and results detected")
    if declared in {"primary research", "primary research article", "primary_research", "research article", "research_article"}:
        primary_signals.append("primary/research article type declared")

    if len(primary_signals) >= 2:
        return SourceTypeAssessment(
            source_type="primary_research",
            label=_LABELS["primary_research"],
            confidence="high",
            direct_reporting="likely",
            signals=tuple(primary_signals),
        ).to_dict()
    if primary_signals:
        return SourceTypeAssessment(
            source_type="primary_research",
            label=_LABELS["primary_research"],
            confidence="medium",
            direct_reporting="possible",
            signals=tuple(primary_signals),
        ).to_dict()

    if _matches(front[:8_000], r"\bthis (?:systematic |scoping |narrative )?review\b", r"\bwe reviewed the literature\b"):
        return _assessment("narrative_review", "medium", "no", "review wording in article text")

    return _assessment(
        "uncertain",
        "low",
        "uncertain",
        "no high-precision design signal found",
    )


def _assessment(source_type: str, confidence: str, direct_reporting: str, signal: str) -> dict[str, object]:
    return SourceTypeAssessment(
        source_type=source_type,
        label=_LABELS[source_type],
        confidence=confidence,
        direct_reporting=direct_reporting,
        signals=(signal,),
    ).to_dict()


def _norm(value: object) -> str:
    return " ".join(str(value or "").lower().replace("\u00ad", "").split())


def _matches(value: str, *patterns: str) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


_REVIEW_SELF_DESCRIPTION_PATTERN = re.compile(
    r"\b(?:in )?this (?:narrative )?review\b.{0,160}"
    r"\b(?:summari[sz]|review|examin|discuss|assess|synthesi[sz]|"
    r"evaluat|focus|literature|evidence|studies)\w*\b",
    flags=re.IGNORECASE,
)
_REVIEW_ATTRIBUTION_PATTERN = re.compile(
    r"(?:\baccording to\b|\bet al\.?\b|\breview by\b|"
    r"\b(?:authors?|researchers?|investigators?)\b.{0,50}"
    r"\b(?:published|wrote|reported|described|noted|concluded|stated)\b|"
    r"\b(?:published|wrote|reported|described|noted|concluded|stated)\b"
    r".{0,50}\b(?:review|authors?|researchers?|investigators?)\b|"
    r"\[[0-9][0-9,; -]*\]|\([^)]*(?:19|20)\d{2}[^)]*\))",
    flags=re.IGNORECASE,
)


def _has_unattributed_review_self_description(front: str) -> bool:
    """Reject review wording that is attributed to another publication."""

    for match in _REVIEW_SELF_DESCRIPTION_PATTERN.finditer(front):
        same_sentence = front[max(0, match.start() - 220) : match.start()]
        same_sentence = re.split(r"[.!?]", same_sentence)[-1]
        if _REVIEW_ATTRIBUTION_PATTERN.search(same_sentence):
            continue

        before = front[: match.start()].rstrip()
        if before.endswith((".", "!", "?")):
            previous_text = before[:-1].rstrip()
            previous_sentence = re.split(r"[.!?]", previous_text)[-1]
            if "review" in previous_sentence and _REVIEW_ATTRIBUTION_PATTERN.search(
                previous_sentence
            ):
                continue
        return True
    return False
