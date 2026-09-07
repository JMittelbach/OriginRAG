from __future__ import annotations

import pytest

from pfempire.trace.schemas import EvidenceBlueprint, EvidenceFacet, TraceCandidate


@pytest.fixture
def blueprint() -> EvidenceBlueprint:
    facets = [
        EvidenceFacet(facet_id="f1", question_id="q1", label="EPCR binding", description="binding", facet_type="molecular_interaction", biological_scale="molecular", evidence_type="biochemical", polarity_requirement="support", importance_weight=1.0, is_required=True, aliases=["epcr"]),
        EvidenceFacet(facet_id="f2", question_id="q1", label="clinical severity", description="severity", facet_type="clinical_phenotype", biological_scale="clinical", evidence_type="observational", polarity_requirement="any", importance_weight=1.0, is_required=True, aliases=["severe malaria"]),
    ]
    return EvidenceBlueprint(question_id="q1", question="EPCR and severe malaria", task_mode="mechanism_mapping", required_facets=facets, preferred_scales=["molecular", "clinical"], required_polarities=["support"], minimum_independent_lineages=2)


def make_candidate(index: int, *, paper: str | None = None, family: str | None = None, facets: dict | None = None, scales: dict | None = None, polarity: dict | None = None, score: float = 20.0, tokens: int = 100) -> TraceCandidate:
    paper_id = paper or f"P{index}"
    return TraceCandidate(chunk_id=f"C{index}", paper_id=paper_id, text=f"candidate {index} EPCR severe malaria", token_cost=tokens, retrieval_score=score, section="results", section_quality=1.0, facet_scores=facets or {"f1": 1.0}, scale_scores=scales or {"molecular": 1.0}, evidence_type_scores={"biochemical": 1.0}, polarity_scores=polarity or {"support": 1.0}, publication_family_id=family or paper_id, study_lineage_id=paper_id, metadata={"raw_result":{"chunk_id":f"C{index}","paper_id":paper_id,"score":score}})
