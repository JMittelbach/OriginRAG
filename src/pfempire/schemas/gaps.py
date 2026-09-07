from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchGap(BaseModel):
    gap_id: str
    title: str
    description: str
    evidence_basis: list[str] = Field(default_factory=list)
    missing_link: str
    affected_topics: list[str] = Field(default_factory=list)
    required_data: list[str] = Field(default_factory=list)
    possible_analysis: list[str] = Field(default_factory=list)
    risk: str | None = None
