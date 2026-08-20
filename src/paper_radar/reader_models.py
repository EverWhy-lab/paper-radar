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
    "journal_recent",
    "frontier_recent",
    "model_based_recent",
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
    core_topics: list[str] = field(default_factory=list)
    subtopics: list[str] = field(default_factory=list)
    document_type: str = "method"
    domain_affinity: str = "neutral"
    domain_affinity_adjustment: float = 0.0
    redundancy_penalty: float = 0.0
    recommendation_base_score: float = 0.0
    recommendation_utility: float = 0.0
    days_since_same_subtopic: int | None = None
    semantic_suppressed: bool = False

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
            "core_topics": self.core_topics,
            "subtopics": self.subtopics,
            "document_type": self.document_type,
            "domain_affinity": self.domain_affinity,
            "domain_affinity_adjustment": self.domain_affinity_adjustment,
            "redundancy_penalty": self.redundancy_penalty,
            "recommendation_base_score": self.recommendation_base_score,
            "recommendation_utility": self.recommendation_utility,
            "days_since_same_subtopic": self.days_since_same_subtopic,
            "semantic_suppressed": self.semantic_suppressed,
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
            core_topics=list(value.get("core_topics", [])),
            subtopics=list(value.get("subtopics", [])),
            document_type=str(value.get("document_type", "method")),
            domain_affinity=str(value.get("domain_affinity", "neutral")),
            domain_affinity_adjustment=float(
                value.get("domain_affinity_adjustment", 0)
            ),
            redundancy_penalty=float(value.get("redundancy_penalty", 0)),
            recommendation_base_score=float(
                value.get("recommendation_base_score", 0)
            ),
            recommendation_utility=float(value.get("recommendation_utility", 0)),
            days_since_same_subtopic=(
                int(value["days_since_same_subtopic"])
                if value.get("days_since_same_subtopic") is not None
                else None
            ),
            semantic_suppressed=bool(value.get("semantic_suppressed", False)),
            paper=Paper.from_dict(value["paper"]),
        )


@dataclass
class LLMAnalysis:
    canonical_paper_id: str
    title: str
    takeaway: str
    generated_at: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_paper_id": self.canonical_paper_id,
            "title": self.title,
            "takeaway": self.takeaway,
            "generated_at": self.generated_at,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LLMAnalysis":
        takeaway = str(value.get("takeaway", "")).strip()
        if not takeaway:
            # Backward compatibility with the previous three-part guide.
            parts = [
                str(value.get("summary", "")).strip(),
                str(value.get("why_relevant", "")).strip(),
                str(value.get("one_line_verdict", "")).strip(),
            ]
            takeaway = " ".join(part for part in parts if part)
        return cls(
            canonical_paper_id=str(value["canonical_paper_id"]),
            title=str(value.get("title", "")),
            takeaway=takeaway,
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
    schema_version: int = 4

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


@dataclass
class DismissalEntry:
    canonical_paper_id: str
    title: str
    topics: list[str]
    reason: str
    dismissed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_paper_id": self.canonical_paper_id,
            "title": self.title,
            "topics": self.topics,
            "reason": self.reason,
            "dismissed_at": self.dismissed_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DismissalEntry":
        return cls(
            canonical_paper_id=str(value["canonical_paper_id"]).casefold(),
            title=str(value.get("title", "")),
            topics=list(value.get("topics", [])),
            reason=str(value.get("reason", "not_interested")),
            dismissed_at=str(value.get("dismissed_at", "")),
        )


@dataclass
class FavoriteEntry:
    canonical_paper_id: str
    title: str
    authors: list[str]
    publication_year: int | None
    source_name: str
    abstract: str
    landing_page_url: str | None
    pdf_url: str | None
    openalex_url: str | None
    doi: str | None
    saved_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_paper_id": self.canonical_paper_id,
            "title": self.title,
            "authors": self.authors,
            "publication_year": self.publication_year,
            "source_name": self.source_name,
            "abstract": self.abstract,
            "landing_page_url": self.landing_page_url,
            "pdf_url": self.pdf_url,
            "openalex_url": self.openalex_url,
            "doi": self.doi,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FavoriteEntry":
        return cls(
            canonical_paper_id=str(value["canonical_paper_id"]).casefold(),
            title=str(value.get("title", "")),
            authors=list(value.get("authors", [])),
            publication_year=(
                int(value["publication_year"])
                if value.get("publication_year") is not None
                else None
            ),
            source_name=str(value.get("source_name", "")),
            abstract=str(value.get("abstract", "")),
            landing_page_url=value.get("landing_page_url"),
            pdf_url=value.get("pdf_url"),
            openalex_url=value.get("openalex_url"),
            doi=value.get("doi"),
            saved_at=str(value.get("saved_at", "")),
        )
