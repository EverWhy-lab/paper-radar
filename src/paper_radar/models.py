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
class DailyRadar:
    date: str
    generated_at: str
    papers: list[Paper]
    version_updates: list[Paper] = field(default_factory=list)
    source: str = "arXiv API"
    timezone: str = "Asia/Shanghai"
    run_mode: str = "historical"
    window_start: str | None = None
    window_end: str | None = None
    run_new_submission_count: int | None = None
    run_version_update_count: int | None = None
    schema_version: int = 2

    @property
    def all_papers(self) -> list[Paper]:
        return self.papers + self.version_updates

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "date": self.date,
            "generated_at": self.generated_at,
            "source": self.source,
            "timezone": self.timezone,
            "run_mode": self.run_mode,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "paper_count": len(self.all_papers),
            "new_submission_count": len(self.papers),
            "version_update_count": len(self.version_updates),
            "run_new_submission_count": (
                len(self.papers)
                if self.run_new_submission_count is None
                else self.run_new_submission_count
            ),
            "run_version_update_count": (
                len(self.version_updates)
                if self.run_version_update_count is None
                else self.run_version_update_count
            ),
            "papers": [paper.to_dict() for paper in self.papers],
            "version_updates": [paper.to_dict() for paper in self.version_updates],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DailyRadar":
        schema_version = int(value.get("schema_version", 1))
        timezone_name = str(value.get("timezone", "Asia/Shanghai"))
        papers = [Paper.from_dict(paper) for paper in value.get("papers", [])]
        version_updates = [
            Paper.from_dict(paper) for paper in value.get("version_updates", [])
        ]
        if schema_version < 2 and "version_updates" not in value:
            target_date = str(value["date"])
            new_submissions: list[Paper] = []
            migrated_updates: list[Paper] = []
            for paper in papers:
                try:
                    published_date = (
                        datetime.fromisoformat(paper.published.replace("Z", "+00:00"))
                        .astimezone(ZoneInfo(timezone_name))
                        .date()
                        .isoformat()
                    )
                except ValueError:
                    published_date = target_date
                if published_date == target_date:
                    new_submissions.append(paper)
                else:
                    migrated_updates.append(paper)
            papers = new_submissions
            version_updates = migrated_updates

        return cls(
            schema_version=2,
            date=str(value["date"]),
            generated_at=str(value["generated_at"]),
            source=str(value.get("source", "arXiv API")),
            timezone=timezone_name,
            run_mode=str(value.get("run_mode", "historical")),
            window_start=(str(value["window_start"]) if value.get("window_start") else None),
            window_end=(str(value["window_end"]) if value.get("window_end") else None),
            run_new_submission_count=(
                int(value["run_new_submission_count"])
                if value.get("run_new_submission_count") is not None
                else None
            ),
            run_version_update_count=(
                int(value["run_version_update_count"])
                if value.get("run_version_update_count") is not None
                else None
            ),
            papers=papers,
            version_updates=version_updates,
        )


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
