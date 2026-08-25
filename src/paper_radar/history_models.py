from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from paper_radar.fetchers.arxiv import split_arxiv_id
from paper_radar.models import Paper


def normalize_openalex_id(value: str | None) -> str | None:
    if not value:
        return None
    identifier = value.rstrip("/").rsplit("/", 1)[-1].upper()
    return identifier if re.fullmatch(r"W\d+", identifier) else None


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.rstrip("/.,") or None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "arxiv:",
    ):
        if normalized.casefold().startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    normalized = normalized.removesuffix(".pdf")
    base_id, _ = split_arxiv_id(normalized)
    if re.fullmatch(r"\d{4}\.\d{4,5}", base_id):
        return base_id
    if re.fullmatch(r"[a-z-]+(?:\.[a-z-]+)?/\d{7}", base_id, flags=re.IGNORECASE):
        return base_id
    return None


def canonical_paper_id(
    *, openalex_id: str | None, doi: str | None, base_arxiv_id: str | None
) -> str:
    openalex_id = normalize_openalex_id(openalex_id)
    doi = normalize_doi(doi)
    base_arxiv_id = normalize_arxiv_id(base_arxiv_id)
    if openalex_id:
        return f"openalex:{openalex_id}"
    if doi:
        return f"doi:{doi}"
    if base_arxiv_id:
        return f"arxiv:{base_arxiv_id}"
    raise ValueError("A historical paper needs an OpenAlex, DOI, or arXiv identifier")


@dataclass
class HistoricalPaper:
    canonical_paper_id: str
    openalex_id: str | None
    base_arxiv_id: str | None
    doi: str | None
    title: str
    authors: list[str]
    abstract: str | None
    publication_date: str | None
    publication_year: int | None
    source_name: str | None
    source_type: str | None
    topics: list[dict[str, Any]]
    cited_by_count: int | None
    counts_by_year: list[dict[str, Any]]
    fwci: float | None
    citation_normalized_percentile: float | None
    referenced_works: list[str]
    related_works: list[str]
    discovery_source: list[str]
    seed_paper_id: list[str]
    discovered_at: str
    last_considered_at: str | None
    recommended_at: str | None
    recommendation_count: int
    reading_status: str
    dismissed: bool
    metadata_updated_at: str
    openalex_url: str | None = None
    landing_page_url: str | None = None
    open_access_url: str | None = None
    pdf_url: str | None = None
    research_fit: int = 0
    video_potential: int = 0
    matched_topics: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    research_reasons: list[dict[str, Any]] = field(default_factory=list)
    historical_value_score: float | None = None
    historical_score_components: dict[str, float | None] = field(default_factory=dict)
    historical_score_reasons: list[str] = field(default_factory=list)
    is_knowledge_map: bool = False
    source_id: str | None = None
    is_retracted: bool = False
    rising_score: float | None = None
    rising_score_components: dict[str, float | None] = field(default_factory=dict)
    rising_score_reasons: list[str] = field(default_factory=list)
    rising_updated_at: str | None = None
    rising_age_days: int | None = None
    rising_citation_velocity: float | None = None
    rising_observed_growth: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HistoricalPaper":
        normalized = dict(value)
        normalized["openalex_id"] = normalize_openalex_id(value.get("openalex_id"))
        normalized["doi"] = normalize_doi(value.get("doi"))
        normalized["base_arxiv_id"] = normalize_arxiv_id(value.get("base_arxiv_id"))
        normalized["discovery_source"] = list(value.get("discovery_source", []))
        normalized["seed_paper_id"] = list(value.get("seed_paper_id", []))
        normalized["topics"] = list(value.get("topics", []))
        normalized["counts_by_year"] = list(value.get("counts_by_year", []))
        normalized["referenced_works"] = list(value.get("referenced_works", []))
        normalized["related_works"] = list(value.get("related_works", []))
        normalized["authors"] = list(value.get("authors", []))
        normalized["matched_topics"] = list(value.get("matched_topics", []))
        normalized["matched_keywords"] = list(value.get("matched_keywords", []))
        normalized["research_reasons"] = list(value.get("research_reasons", []))
        normalized["historical_score_components"] = dict(
            value.get("historical_score_components", {})
        )
        normalized["historical_score_reasons"] = list(
            value.get("historical_score_reasons", [])
        )
        normalized["rising_score_components"] = dict(
            value.get("rising_score_components", {})
        )
        normalized["rising_score_reasons"] = list(
            value.get("rising_score_reasons", [])
        )
        normalized["rising_observed_growth"] = dict(
            value.get("rising_observed_growth", {})
        )
        defaults: dict[str, Any] = {
            "openalex_url": None,
            "landing_page_url": None,
            "open_access_url": None,
            "pdf_url": None,
            "research_fit": 0,
            "video_potential": 0,
            "matched_topics": [],
            "matched_keywords": [],
            "research_reasons": [],
            "historical_value_score": None,
            "historical_score_components": {},
            "historical_score_reasons": [],
            "is_knowledge_map": False,
            "source_id": None,
            "is_retracted": False,
            "rising_score": None,
            "rising_score_components": {},
            "rising_score_reasons": [],
            "rising_updated_at": None,
            "rising_age_days": None,
            "rising_citation_velocity": None,
            "rising_observed_growth": {},
        }
        for key, default in defaults.items():
            normalized.setdefault(key, default)
        return cls(**normalized)

    @property
    def aliases(self) -> set[str]:
        aliases = {self.canonical_paper_id.casefold()}
        if self.openalex_id:
            aliases.add(f"openalex:{self.openalex_id}".casefold())
        if self.doi:
            aliases.add(f"doi:{self.doi}".casefold())
        if self.base_arxiv_id:
            aliases.add(f"arxiv:{self.base_arxiv_id}".casefold())
        return aliases

    @property
    def primary_topic(self) -> str:
        return self.matched_topics[0] if self.matched_topics else "unclassified"

    def to_reader_paper(self) -> Paper:
        identifier = self.base_arxiv_id or self.canonical_paper_id
        published = (
            f"{self.publication_date}T00:00:00Z"
            if self.publication_date and "T" not in self.publication_date
            else (self.publication_date or "")
        )
        arxiv_url = (
            f"https://arxiv.org/abs/{self.base_arxiv_id}"
            if self.base_arxiv_id
            else (self.landing_page_url or self.openalex_url or "")
        )
        pdf_url = self.pdf_url or (
            f"https://arxiv.org/pdf/{self.base_arxiv_id}"
            if self.base_arxiv_id
            else ""
        )
        return Paper(
            arxiv_id=identifier,
            base_id=identifier,
            version=1,
            title=self.title,
            authors=self.authors,
            published=published,
            updated=published,
            primary_category="",
            categories=[],
            summary=self.abstract or "",
            abs_url=arxiv_url,
            pdf_url=pdf_url,
            research_fit=self.research_fit,
            video_potential=self.video_potential,
            matched_topics=self.matched_topics,
            matched_keywords=self.matched_keywords,
            research_reasons=self.research_reasons,
            video_reasons=[],
        )


@dataclass
class SeedPaper:
    identifier: str
    added_at: str
    openalex_id: str | None = None
    base_arxiv_id: str | None = None
    doi: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SeedPaper":
        return cls(
            identifier=str(value["identifier"]),
            added_at=str(value["added_at"]),
            openalex_id=normalize_openalex_id(value.get("openalex_id")),
            base_arxiv_id=normalize_arxiv_id(value.get("base_arxiv_id")),
            doi=normalize_doi(value.get("doi")),
            title=value.get("title"),
        )
