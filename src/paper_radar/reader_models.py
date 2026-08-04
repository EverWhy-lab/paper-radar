from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from paper_radar.models import Paper
from paper_radar.history_models import HistoricalPaper


READING_STATUSES = {"unread", "queued", "reading", "read"}
RECOMMENDATION_CATEGORIES = {
    "recent_new",
    "reading_pool",
    "important_update",
    "frontier_recent",
    "high_impact_historical",
    "review_knowledge_map",
}


@dataclass
class CandidateBatch:
    date: str
    fetched_at: str
    mode: str
    window_start: str
    window_end: str
    papers: list[Paper]
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "date": self.date,
            "fetched_at": self.fetched_at,
            "mode": self.mode,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "candidate_count": len(self.papers),
            "papers": [paper.to_dict() for paper in self.papers],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CandidateBatch":
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            date=str(value["date"]),
            fetched_at=str(value["fetched_at"]),
            mode=str(value["mode"]),
            window_start=str(value["window_start"]),
            window_end=str(value["window_end"]),
            papers=[Paper.from_dict(paper) for paper in value.get("papers", [])],
        )


@dataclass
class ReadingPoolEntry:
    base_arxiv_id: str
    title: str
    source: str
    topic_tags: list[str]
    recommendation_reason: str
    added_at: str
    last_considered_at: str | None
    recommended_at: str | None
    recommendation_count: int
    reading_status: str
    dismissed: bool
    priority: int
    paper: Paper

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_arxiv_id": self.base_arxiv_id,
            "title": self.title,
            "source": self.source,
            "topic_tags": self.topic_tags,
            "recommendation_reason": self.recommendation_reason,
            "added_at": self.added_at,
            "last_considered_at": self.last_considered_at,
            "recommended_at": self.recommended_at,
            "recommendation_count": self.recommendation_count,
            "reading_status": self.reading_status,
            "dismissed": self.dismissed,
            "priority": self.priority,
            "paper": self.paper.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReadingPoolEntry":
        return cls(
            base_arxiv_id=str(value["base_arxiv_id"]),
            title=str(value["title"]),
            source=str(value.get("source", "arXiv API")),
            topic_tags=list(value.get("topic_tags", [])),
            recommendation_reason=str(value.get("recommendation_reason", "Manually added")),
            added_at=str(value["added_at"]),
            last_considered_at=value.get("last_considered_at"),
            recommended_at=value.get("recommended_at"),
            recommendation_count=int(value.get("recommendation_count", 0)),
            reading_status=str(value.get("reading_status", "unread")),
            dismissed=bool(value.get("dismissed", False)),
            priority=int(value.get("priority", 50)),
            paper=Paper.from_dict(value["paper"]),
        )


@dataclass
class RecommendationEntry:
    category: str
    paper: Paper
    reasons: list[str] = field(default_factory=list)
    pool_reason: str | None = None
    historical_paper: HistoricalPaper | None = None

    @property
    def canonical_paper_id(self) -> str:
        if self.historical_paper is not None:
            return self.historical_paper.canonical_paper_id
        return f"arxiv:{self.paper.base_id}"

    @property
    def aliases(self) -> set[str]:
        if self.historical_paper is not None:
            return self.historical_paper.aliases
        return {self.canonical_paper_id.casefold()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "reasons": self.reasons,
            "pool_reason": self.pool_reason,
            "canonical_paper_id": self.canonical_paper_id,
            "historical_paper": (
                self.historical_paper.to_dict() if self.historical_paper else None
            ),
            "paper": self.paper.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RecommendationEntry":
        return cls(
            category=str(value["category"]),
            reasons=list(value.get("reasons", [])),
            pool_reason=value.get("pool_reason"),
            historical_paper=(
                HistoricalPaper.from_dict(value["historical_paper"])
                if value.get("historical_paper")
                else None
            ),
            paper=Paper.from_dict(value["paper"]),
        )


@dataclass
class LLMAnalysis:
    canonical_paper_id: str
    title: str
    summary: str
    why_relevant: str
    one_line_verdict: str
    generated_at: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_paper_id": self.canonical_paper_id,
            "title": self.title,
            "summary": self.summary,
            "why_relevant": self.why_relevant,
            "one_line_verdict": self.one_line_verdict,
            "generated_at": self.generated_at,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LLMAnalysis":
        return cls(
            canonical_paper_id=str(value["canonical_paper_id"]),
            title=str(value.get("title", "")),
            summary=str(value.get("summary", "")),
            why_relevant=str(value.get("why_relevant", "")),
            one_line_verdict=str(value.get("one_line_verdict", "")),
            generated_at=str(value.get("generated_at", "")),
            model=str(value.get("model", "")),
        )


@dataclass
class DailyRecommendations:
    date: str
    generated_at: str
    recommendations: list[RecommendationEntry]
    candidate_count: int
    mode: str
    selection_config: dict[str, Any]
    historical_candidate_count: int = 0
    llm_analysis: list[LLMAnalysis] | None = None
    schema_version: int = 3

    def to_dict(self) -> dict[str, Any]:
        configured_categories = set(self.selection_config.get("selection_order", []))
        actual_categories = {entry.category for entry in self.recommendations}
        categories = configured_categories | actual_categories
        counts = {
            category: sum(1 for entry in self.recommendations if entry.category == category)
            for category in sorted(categories or RECOMMENDATION_CATEGORIES)
        }
        return {
            "schema_version": self.schema_version,
            "date": self.date,
            "generated_at": self.generated_at,
            "mode": self.mode,
            "candidate_count": self.candidate_count,
            "historical_candidate_count": self.historical_candidate_count,
            "recommendation_count": len(self.recommendations),
            "category_counts": counts,
            "selection_config": self.selection_config,
            "llm_analysis": (
                [analysis.to_dict() for analysis in self.llm_analysis]
                if self.llm_analysis
                else None
            ),
            "recommendations": [entry.to_dict() for entry in self.recommendations],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DailyRecommendations":
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            date=str(value["date"]),
            generated_at=str(value["generated_at"]),
            mode=str(value.get("mode", "incremental")),
            candidate_count=int(value.get("candidate_count", 0)),
            historical_candidate_count=int(value.get("historical_candidate_count", 0)),
            selection_config=dict(value.get("selection_config", {})),
            llm_analysis=(
                [LLMAnalysis.from_dict(item) for item in value.get("llm_analysis") or []]
                or None
            ),
            recommendations=[
                RecommendationEntry.from_dict(entry)
                for entry in value.get("recommendations", [])
            ],
        )
