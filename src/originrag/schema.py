from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator


class PaperRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_default=True)

    paper_id: str
    title: str
    year: int | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    domain: str = "malaria"
    topics: str = ""
    methods: str = ""
    systems: str = ""
    paper_type: str | None = None
    fulltext_status: str = "missing"
    local_pdf_relpath: str | None = None
    extracted_text_relpath: str | None = None
    notes: str | None = None

    allowed_fulltext_statuses: ClassVar[set[str]] = {
        "missing",
        "pdf_available",
        "extracted",
        "chunked",
        "failed",
        "excluded_no_doi",
        "duplicate",
        "archived",
    }

    @field_validator(
        "year",
        "doi",
        "pmid",
        "pmcid",
        "paper_type",
        "local_pdf_relpath",
        "extracted_text_relpath",
        "notes",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("paper_id", "title", mode="before")
    @classmethod
    def required_text(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("required text field is missing")
        return value.strip()

    @field_validator("domain", mode="before")
    @classmethod
    def default_domain(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            return "malaria"
        return value.strip()

    @field_validator("topics", "methods", "systems", mode="before")
    @classmethod
    def semicolon_compatible_text(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ";".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()

    @field_validator("fulltext_status", mode="before")
    @classmethod
    def validate_fulltext_status(cls, value: object) -> str:
        status = str(value or "missing").strip()
        if not status:
            status = "missing"
        if status not in cls.allowed_fulltext_statuses:
            allowed = ", ".join(sorted(cls.allowed_fulltext_statuses))
            raise ValueError(f"fulltext_status must be one of: {allowed}")
        return status

    def topic_list(self) -> list[str]:
        return _split_semicolon_text(self.topics)

    def method_list(self) -> list[str]:
        return _split_semicolon_text(self.methods)

    def system_list(self) -> list[str]:
        return _split_semicolon_text(self.systems)


def _split_semicolon_text(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]
