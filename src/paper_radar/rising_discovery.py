from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from paper_radar.config import ResearchProfile
from paper_radar.historical_scoring import score_historical_papers
from paper_radar.history_models import HistoricalPaper
from paper_radar.providers.openalex import SourcePaperScan
from paper_radar.recommendation_utility import classify_domain_affinity
from paper_radar.rising import rising_eligibility, score_quantiles, score_rising_paper
from paper_radar.rising_storage import (
    CitationSnapshot,
    CitationSnapshotStorage,
    RisingCandidateStorage,
)


class RisingProvider(Protocol):
    @property
    def run_request_count(self) -> int: ...

    @property
    def run_cache_hits(self) -> int: ...

    def scan_source_papers(
        self,
        source_id: str,
        *,
        limit: int,
        from_date: str,
        to_date: str | None,
        discovery_source: str,
    ) -> SourcePaperScan: ...

    def save_stats(self) -> None: ...


@dataclass(frozen=True)
class RisingScanResult:
    scan_date: str
    from_date: str
    to_date: str
    scanned_count: int
    candidate_count: int
    eligible_count: int
    request_count: int
    cache_hits: int
    quantiles: dict[str, float | None]
    per_source: list[dict[str, Any]]
    top_candidates: list[dict[str, Any]]
    top_diagnostics: list[dict[str, Any]]
    information_bubble: dict[str, int]
    snapshot_path: Path | None
    candidate_path: Path | None
    dry_run: bool


def _normalise_keyword(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _candidate_classification(
    paper: HistoricalPaper, profile: ResearchProfile
) -> dict[str, Any]:
    core_ids = {
        profile.canonical_topic_id(str(topic))
        for topic in profile.recommendations["core_topic_ids"]
    }
    model_based = profile.recommendations["daily_mix"]["model_based_recent"]
    subtopic_rules = profile.recommendations.get("recommendation_subtopics", {})
    support_ids = {
        profile.canonical_topic_id(str(topic))
        for subtopic in model_based.get("method_subtopics", [])
        for topic in subtopic_rules.get(str(subtopic), {}).get(
            "matched_topics",
            subtopic_rules.get(str(subtopic), {}).get("core_topics", []),
        )
    }
    raw_topics = list(
        dict.fromkeys(
            profile.canonical_topic_id(str(topic)) for topic in paper.matched_topics
        )
    )
    raw_core_topics = [topic for topic in raw_topics if topic in core_ids]
    matched_support_topics = [topic for topic in raw_topics if topic in support_ids]
    generic = {
        _normalise_keyword(str(keyword))
        for keyword in profile.recommendations["generic_keywords"]
    }
    matched_by_normalised = {
        _normalise_keyword(str(keyword)): str(keyword)
        for keyword in paper.matched_keywords
    }
    topic_strong_keywords: dict[str, list[str]] = {}
    for topic in profile.scoring["topics"]:
        topic_id = profile.canonical_topic_id(str(topic["id"]))
        topic_strong_keywords[topic_id] = [
            matched_by_normalised[normalised]
            for keyword in topic.get("keywords", [])
            if (normalised := _normalise_keyword(str(keyword)))
            in matched_by_normalised
            and normalised not in generic
        ]
    matched_core_topics = [
        topic for topic in raw_core_topics if topic_strong_keywords.get(topic)
    ]
    generic_only_core_topics = [
        topic for topic in raw_core_topics if topic not in matched_core_topics
    ]
    matched_strong_keywords = list(
        dict.fromkeys(
            keyword
            for topic in raw_topics
            for keyword in topic_strong_keywords.get(topic, [])
        )
    )
    matched_generic_keywords = list(
        dict.fromkeys(
            keyword
            for normalised, keyword in matched_by_normalised.items()
            if normalised in generic
        )
    )
    if matched_core_topics:
        classification = "core"
    elif matched_support_topics:
        classification = "model_based_support_only"
    else:
        classification = "outside_current_core"
    affinity, affinity_adjustment, affinity_terms = classify_domain_affinity(
        paper.to_reader_paper(), profile
    )
    return {
        "raw_core_topics": raw_core_topics,
        "matched_core_topics": matched_core_topics,
        "generic_only_core_topics": generic_only_core_topics,
        "matched_support_topics": matched_support_topics,
        "matched_strong_keywords": matched_strong_keywords,
        "matched_generic_keywords": matched_generic_keywords,
        "classification": classification,
        "domain_affinity": affinity,
        "domain_affinity_adjustment": affinity_adjustment,
        "domain_affinity_terms": affinity_terms,
    }


def _candidate_audit(
    paper: HistoricalPaper, profile: ResearchProfile, *, threshold: float
) -> dict[str, Any]:
    return {
        "canonical_paper_id": paper.canonical_paper_id,
        "title": paper.title,
        "source": paper.source_name,
        "publication_date": paper.publication_date,
        "age_days": paper.rising_age_days,
        "cited_by_count": paper.cited_by_count,
        "fwci": paper.fwci,
        "citation_normalized_percentile": paper.citation_normalized_percentile,
        "smoothed_citation_velocity_per_month": paper.rising_citation_velocity,
        "observed_growth": paper.rising_observed_growth or None,
        "research_fit": paper.research_fit,
        "matched_topics": paper.matched_topics,
        "rising_score": paper.rising_score,
        "threshold_eligible": float(paper.rising_score or 0) >= threshold,
        "rising_score_components": paper.rising_score_components,
        "eligibility_reasons": paper.rising_score_reasons,
        **_candidate_classification(paper, profile),
    }


def _journal_recent_eligible(
    paper: HistoricalPaper, profile: ResearchProfile
) -> bool:
    config = profile.recommendations["daily_mix"]["journal_recent"]
    core = {
        profile.canonical_topic_id(topic)
        for topic in paper.matched_topics
        if profile.canonical_topic_id(topic)
        in set(profile.recommendations["core_topic_ids"])
    }
    generic = {
        " ".join(str(term).casefold().replace("-", " ").split())
        for term in profile.recommendations["generic_keywords"]
    }
    strong = {
        keyword
        for keyword in paper.matched_keywords
        if " ".join(keyword.casefold().replace("-", " ").split()) not in generic
    }
    return (
        not paper.is_retracted
        and not paper.is_knowledge_map
        and paper.research_fit >= int(config["min_research_fit"])
        and len(core) >= int(config["min_core_topic_matches"])
        and len(strong) >= int(config["min_non_generic_keyword_matches"])
    )


class RisingDiscoveryService:
    def __init__(
        self,
        data_dir: Path,
        profile: ResearchProfile,
        provider: RisingProvider,
        *,
        now: datetime | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.profile = profile
        self.provider = provider
        self.zone = ZoneInfo(profile.timezone)
        self.now = (now or datetime.now(self.zone)).astimezone(self.zone)
        self.snapshot_storage = CitationSnapshotStorage(data_dir)
        self.candidate_storage = RisingCandidateStorage(data_dir)

    def scan(self, *, dry_run: bool = False) -> RisingScanResult:
        config = self.profile.rising_discovery
        if not config.get("enabled", False):
            raise ValueError("rising_discovery is disabled")
        lookback_days = int(config["lookback_days"])
        from_date = (self.now.date() - timedelta(days=lookback_days)).isoformat()
        to_date = self.now.date().isoformat()
        captured_at = self.now.isoformat(timespec="seconds")
        histories = self.snapshot_storage.load()
        candidates: list[HistoricalPaper] = []
        snapshots: list[CitationSnapshot] = []
        per_source: list[dict[str, Any]] = []
        scanned_count = 0
        threshold = float(config["min_rising_score"])
        recent_cutoff = self.now.date() - timedelta(
            days=int(config.get("recent_audit_days", 60))
        )

        try:
            for source in self.profile.journals.get("sources", []):
                if source.get("group") != "robotics_core":
                    continue
                source_name = str(source["name"])
                source_id = str(source["source_id"]).upper()
                scan = self.provider.scan_source_papers(
                    source_id,
                    limit=int(source["rising_scan_limit"]),
                    from_date=from_date,
                    to_date=to_date,
                    discovery_source=f"rising_search:{source_name}",
                )
                scanned_count += len(scan.papers)
                for paper in scan.papers:
                    paper.source_id = source_id
                    paper.source_name = paper.source_name or source_name
                newest_examples = [
                    {
                        "openalex_id": paper.openalex_id,
                        "title": paper.title,
                        "publication_date": paper.publication_date,
                        "publication_year": paper.publication_year,
                        "source": paper.source_name,
                        "doi": paper.doi,
                    }
                    for paper in scan.papers[:5]
                ]
                scored = score_historical_papers(
                    scan.papers, self.profile, as_of_year=self.now.year
                )
                source_candidates: list[HistoricalPaper] = []
                source_eligible: list[HistoricalPaper] = []
                recent_papers: list[HistoricalPaper] = []
                for paper in scored:
                    if paper.publication_date:
                        try:
                            if recent_cutoff <= datetime.fromisoformat(
                                paper.publication_date[:10]
                            ).date() <= self.now.date():
                                recent_papers.append(paper)
                        except ValueError:
                            pass
                    eligible, eligibility_reasons = rising_eligibility(
                        paper, self.profile, as_of=self.now.date()
                    )
                    if not eligible:
                        continue
                    prior = histories.get(paper.canonical_paper_id.casefold(), [])
                    score_rising_paper(
                        paper,
                        self.profile,
                        captured_at=self.now,
                        snapshots=prior,
                    )
                    paper.rising_score_reasons = [
                        *eligibility_reasons,
                        *paper.rising_score_reasons,
                    ]
                    source_candidates.append(paper)
                    snapshots.append(
                        CitationSnapshot.from_paper(
                            paper, captured_at=captured_at
                        )
                    )
                    if float(paper.rising_score or 0) >= threshold:
                        source_eligible.append(paper)
                candidates.extend(source_candidates)
                per_source.append(
                    {
                        "name": source_name,
                        "source_id": source_id,
                        "scan_limit": int(source["rising_scan_limit"]),
                        "reported_total": scan.reported_total,
                        "works_scanned": len(scan.papers),
                        "pages": scan.page_count,
                        "truncated": scan.truncated,
                        "after_basic_filters": len(source_candidates),
                        "eligible_rising": len(source_eligible),
                        "recent_60_scanned": len(recent_papers),
                        "recent_60_journal_eligible": sum(
                            _journal_recent_eligible(paper, self.profile)
                            for paper in recent_papers
                        ),
                        "recent_60_top_research_fit": [
                            {
                                "title": paper.title,
                                "research_fit": paper.research_fit,
                                "publication_date": paper.publication_date,
                            }
                            for paper in sorted(
                                recent_papers,
                                key=lambda item: (
                                    item.research_fit,
                                    item.publication_date or "",
                                ),
                                reverse=True,
                            )[:3]
                        ],
                        "newest_examples": newest_examples,
                    }
                )
        except Exception:
            self.provider.save_stats()
            raise

        candidates.sort(
            key=lambda paper: (
                paper.rising_score or 0,
                paper.research_fit,
                paper.publication_date or "",
                paper.canonical_paper_id,
            ),
            reverse=True,
        )
        eligible = [
            paper
            for paper in candidates
            if float(paper.rising_score or 0) >= threshold
        ]
        top_audit_count = int(config.get("top_audit_count", 30))
        top_diagnostics = [
            _candidate_audit(paper, self.profile, threshold=threshold)
            for paper in candidates[:top_audit_count]
        ]
        bubble = {
            "sample_size": len(top_diagnostics),
            "core": sum(
                paper["classification"] == "core" for paper in top_diagnostics
            ),
            "model_based_support_only": sum(
                paper["classification"] == "model_based_support_only"
                for paper in top_diagnostics
            ),
            "outside_current_core": sum(
                paper["classification"] == "outside_current_core"
                for paper in top_diagnostics
            ),
        }

        snapshot_path: Path | None = None
        candidate_path: Path | None = None
        if not dry_run:
            preserved = self.candidate_storage.preserve_workflow_state(candidates)
            merged_snapshots = self.snapshot_storage.merged(
                snapshots,
                max_per_paper=int(config["max_snapshots_per_paper"]),
            )
            # Every OpenAlex request and score completes before production state is replaced.
            snapshot_path = self.snapshot_storage.save(merged_snapshots)
            candidate_path = self.candidate_storage.save(preserved)
            self.provider.save_stats()

        return RisingScanResult(
            scan_date=self.now.date().isoformat(),
            from_date=from_date,
            to_date=to_date,
            scanned_count=scanned_count,
            candidate_count=len(candidates),
            eligible_count=len(eligible),
            request_count=self.provider.run_request_count,
            cache_hits=self.provider.run_cache_hits,
            quantiles=score_quantiles(
                [float(paper.rising_score or 0) for paper in candidates]
            ),
            per_source=per_source,
            top_candidates=top_diagnostics[:10],
            top_diagnostics=top_diagnostics,
            information_bubble=bubble,
            snapshot_path=snapshot_path,
            candidate_path=candidate_path,
            dry_run=dry_run,
        )
