from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class Paper:
    arxiv_id: str
    base_id: str
    version: int
    title: str
    authors: list[str]
    published: str
    updated: str
    primary_category: str
    categories: list[str]
    summary: str
    abs_url: str
    pdf_url: str
    research_fit: int = 0
    video_potential: int = 0
    matched_topics: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    research_reasons: list[dict[str, Any]] = field(default_factory=list)
    video_reasons: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Paper":
        return cls(**value)


@dataclass
class SeenState:
    papers: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_successful_run_at: str | None = None
    last_run_mode: str | None = None
    last_run_date: str | None = None
    last_run_new_submission_count: int | None = None
    last_run_version_update_count: int | None = None
    last_run_candidate_count: int | None = None
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "last_successful_run_at": self.last_successful_run_at,
            "last_run_mode": self.last_run_mode,
            "last_run_date": self.last_run_date,
            "last_run_new_submission_count": self.last_run_new_submission_count,
            "last_run_version_update_count": self.last_run_version_update_count,
            "last_run_candidate_count": self.last_run_candidate_count,
            "papers": dict(sorted(self.papers.items())),
        }
