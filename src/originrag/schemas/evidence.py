from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    evidence_id: str
    paper_id: str
    chunk_id: str
    source_label: str
    author: str | None = None
    year: int | None = None
    title: str | None = None
    journal: str | None = None
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    text: str
    quality_flags: list[str] = Field(default_factory=list)
    retrieval_score: float | None = None
    keyword_score: float | None = None
    semantic_score: float | None = None
    final_score: float | None = None


class EvidencePack(BaseModel):
    question: str
    task_mode: str
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    n_chunks: int
    n_unique_papers: int
    corpus_version: str | None = None
    run_id: str | None = None
