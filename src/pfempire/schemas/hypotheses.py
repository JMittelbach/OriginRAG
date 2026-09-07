from __future__ import annotations

from pydantic import BaseModel, Field


class HypothesisCard(BaseModel):
    hypothesis_id: str
    title: str
    short_summary: str
    evidence_backed_statements: list[str] = Field(default_factory=list)
    inference: list[str] = Field(default_factory=list)
    speculation: list[str] = Field(default_factory=list)
    testable_prediction: list[str] = Field(default_factory=list)
    required_data: list[str] = Field(default_factory=list)
    analysis_plan: list[str] = Field(default_factory=list)
    risk_plan_b: list[str] = Field(default_factory=list)
    novelty: str | None = None
    impact: str | None = None
    key_references: list[str] = Field(default_factory=list)
