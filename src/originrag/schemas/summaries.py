from __future__ import annotations

from pydantic import BaseModel, Field


class PaperStructuredSummary(BaseModel):
    paper_id: str
    title: str
    year: int | None = None
    summary_short: str | None = None
    background: str | None = None
    methods: str | None = None
    datasets: list[str] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    host_angle: str | None = None
    parasite_angle: str | None = None
    immune_angle: str | None = None
    omics_angle: str | None = None
    computational_angle: str | None = None
    disease_context: list[str] = Field(default_factory=list)
    relevance_to_pfempire: str | None = None
    source_chunks: list[str] = Field(default_factory=list)
