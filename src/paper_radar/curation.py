from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.historical_scoring import (
    historical_relevance_eligible,
    score_historical_papers,
)
from paper_radar.history_models import HistoricalPaper, canonical_paper_id
from paper_radar.models import Paper
from paper_radar.reader_models import ReadingPoolEntry, RecommendationEntry


def _normalise(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _cooling(
    aliases: set[str],
    history: dict[str, list[dict[str, Any]]],
    target_date: str,
    cooldown_days: int,
) -> bool:
    current = date.fromisoformat(target_date)
    normalized_aliases = {alias.casefold() for alias in aliases}
    for identifier, events in history.items():
        normalized_identifier = identifier.casefold()
        if ":" not in normalized_identifier:
            normalized_identifier = f"arxiv:{normalized_identifier}"
        if normalized_identifier not in normalized_aliases:
            continue
        for event in events:
            previous = date.fromisoformat(str(event["date"]))
            difference = (current - previous).days
            if 0 <= difference < cooldown_days:
                return True
    return False


def _topic_overlap(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


@dataclass
class CuratedSelectionResult:
    recommendations: list[RecommendationEntry]
    historical_papers: list[HistoricalPaper]
    reading_pool: list[ReadingPoolEntry]


class CuratedRecommendationEngine:
    def __init__(self, profile: ResearchProfile) -> None:
        self.profile = profile
        self.config = profile.recommendations["daily_mix"]
        self.core_topics = set(profile.recommendations["core_topic_ids"])
        self.generic_keywords = {
            _normalise(term) for term in profile.recommendations["generic_keywords"]
        }
        self.excluded_terms = [
            _normalise(term) for term in profile.recommendations["excluded_terms"]
        ]
        self.topic_weights = {
            str(topic["id"]): float(topic["weight"])
            for topic in profile.scoring["topics"]
        }

    def _primary_topic(self, topics: list[str]) -> str:
        return max(
            topics,
            key=lambda topic: self.topic_weights.get(topic, 0),
            default="unclassified",
        )

    def _recent_signals(
        self, paper: Paper, config: dict[str, Any]
    ) -> tuple[list[str], list[str]] | None:
        core = [topic for topic in paper.matched_topics if topic in self.core_topics]
        strong = [
            keyword
            for keyword in paper.matched_keywords
            if _normalise(keyword) not in self.generic_keywords
        ]
        text = _normalise(f"{paper.title} {paper.summary}")
        if any(term in text for term in self.excluded_terms):
            return None
        if paper.research_fit < int(config["min_research_fit"]):
            return None
        if len(core) < int(config["min_core_topic_matches"]):
            return None
        if len(strong) < int(config["min_non_generic_keyword_matches"]):
            return None
        return core, strong

    def _manual_history(self, entry: ReadingPoolEntry) -> HistoricalPaper:
        paper = entry.paper
        publication_date = paper.published[:10] if paper.published else None
        publication_year = (
            int(publication_date[:4]) if publication_date and len(publication_date) >= 4 else None
        )
        return HistoricalPaper(
            canonical_paper_id=canonical_paper_id(
                openalex_id=None, doi=None, base_arxiv_id=entry.base_arxiv_id
            ),
            openalex_id=None,
            base_arxiv_id=entry.base_arxiv_id,
            doi=None,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.summary or None,
            publication_date=publication_date,
            publication_year=publication_year,
            source_name=entry.source,
            source_type="preprint",
            topics=[
                {
                    "id": topic,
                    "display_name": self.profile.topic_labels.get(topic, topic),
                    "score": None,
                }
                for topic in entry.topic_tags
            ],
            cited_by_count=None,
            counts_by_year=[],
            fwci=None,
            citation_normalized_percentile=None,
            referenced_works=[],
            related_works=[],
            discovery_source=["manual_reading_pool"],
            seed_paper_id=[],
            discovered_at=entry.added_at,
            last_considered_at=entry.last_considered_at,
            recommended_at=entry.recommended_at,
            recommendation_count=entry.recommendation_count,
            reading_status=entry.reading_status,
            dismissed=entry.dismissed,
            metadata_updated_at=paper.updated or entry.added_at,
            landing_page_url=paper.abs_url,
            pdf_url=paper.pdf_url,
        )

    def _combine_historical_sources(
        self,
        historical: list[HistoricalPaper],
        reading_pool: list[ReadingPoolEntry],
    ) -> tuple[list[HistoricalPaper], dict[str, ReadingPoolEntry]]:
        combined = list(historical)
        pool_by_alias: dict[str, ReadingPoolEntry] = {}
        existing_aliases = {
            alias: paper for paper in combined for alias in paper.aliases
        }
        for entry in reading_pool:
            alias = f"arxiv:{entry.base_arxiv_id}".casefold()
            pool_by_alias[alias] = entry
            matched = existing_aliases.get(alias)
            if matched is not None:
                matched.reading_status = entry.reading_status
                matched.dismissed = entry.dismissed
                continue
            proxy = self._manual_history(entry)
            combined.append(proxy)
            for proxy_alias in proxy.aliases:
                existing_aliases[proxy_alias] = proxy
        return combined, pool_by_alias

    def _select_historical(
        self,
        papers: list[HistoricalPaper],
        *,
        category: str,
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        considered_at: str,
        already_selected: list[RecommendationEntry],
    ) -> list[RecommendationEntry]:
        config = self.config[category]
        eligible_statuses = set(config["eligible_statuses"])
        selected: list[RecommendationEntry] = []
        topic_counts: dict[str, int] = {}
        for paper in papers:
            paper.last_considered_at = considered_at
            if paper.dismissed or paper.reading_status not in eligible_statuses:
                continue
            relevant, relevance_reasons = historical_relevance_eligible(
                paper, self.profile
            )
            if not relevant:
                continue
            if category == "review_knowledge_map" and not paper.is_knowledge_map:
                continue
            if category == "high_impact_historical" and paper.is_knowledge_map:
                continue
            threshold = float(config["min_historical_value_score"])
            if paper.historical_value_score is None or paper.historical_value_score < threshold:
                continue
            if _cooling(
                paper.aliases,
                history,
                target_date,
                int(config["cooldown_days"]),
            ):
                continue
            if any(paper.aliases & entry.aliases for entry in already_selected + selected):
                continue
            primary = self._primary_topic(paper.matched_topics)
            if category == "high_impact_historical":
                if topic_counts.get(primary, 0) >= int(config["max_same_primary_topic"]):
                    continue
                max_overlap = float(config["max_topic_overlap"])
                if any(
                    _topic_overlap(set(paper.matched_topics), set(entry.paper.matched_topics))
                    > max_overlap
                    for entry in already_selected + selected
                ):
                    continue
            reasons = [
                *relevance_reasons,
                f"historical_value_score {paper.historical_value_score:.1f} ≥ {threshold:.1f}",
                *paper.historical_score_reasons,
                f"影响力元数据更新于 {paper.metadata_updated_at}",
            ]
            selected.append(
                RecommendationEntry(
                    category=category,
                    paper=paper.to_reader_paper(),
                    historical_paper=paper,
                    reasons=reasons,
                )
            )
            topic_counts[primary] = topic_counts.get(primary, 0) + 1
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def _select_recent(
        self,
        papers: list[Paper],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        already_selected: list[RecommendationEntry],
    ) -> list[RecommendationEntry]:
        config = self.config["frontier_recent"]
        selected: list[RecommendationEntry] = []
        topic_counts: dict[str, int] = {}
        for paper in sorted(
            papers,
            key=lambda item: (item.research_fit, item.video_potential, item.updated),
            reverse=True,
        ):
            signals = self._recent_signals(paper, config)
            aliases = {f"arxiv:{paper.base_id}".casefold()}
            if signals is None or _cooling(
                aliases, history, target_date, int(config["cooldown_days"])
            ):
                continue
            if any(aliases & entry.aliases for entry in already_selected + selected):
                continue
            core, strong = signals
            primary = self._primary_topic(paper.matched_topics)
            if topic_counts.get(primary, 0) >= int(config["max_same_primary_topic"]):
                continue
            if any(
                _topic_overlap(set(paper.matched_topics), set(entry.paper.matched_topics))
                > float(config["max_topic_overlap"])
                for entry in already_selected + selected
            ):
                continue
            selected.append(
                RecommendationEntry(
                    category="frontier_recent",
                    paper=paper,
                    reasons=[
                        f"research_fit {paper.research_fit} ≥ {int(config['min_research_fit'])}",
                        f"Core topics matched: {', '.join(core)}",
                        f"Specific keywords matched: {', '.join(strong)}",
                        f"Diversity topic: {primary}",
                    ],
                )
            )
            topic_counts[primary] = topic_counts.get(primary, 0) + 1
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def select(
        self,
        *,
        recent_new: list[Paper],
        historical_papers: list[HistoricalPaper],
        reading_pool: list[ReadingPoolEntry],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        considered_at: str,
    ) -> CuratedSelectionResult:
        combined, pool_by_alias = self._combine_historical_sources(
            historical_papers, reading_pool
        )
        ordered_historical = score_historical_papers(
            combined, self.profile, as_of_year=date.fromisoformat(target_date).year
        )
        review = self._select_historical(
            ordered_historical,
            category="review_knowledge_map",
            history=history,
            target_date=target_date,
            considered_at=considered_at,
            already_selected=[],
        )
        impact = self._select_historical(
            ordered_historical,
            category="high_impact_historical",
            history=history,
            target_date=target_date,
            considered_at=considered_at,
            already_selected=review,
        )
        recent = self._select_recent(
            recent_new,
            history,
            target_date,
            review + impact,
        )
        groups = {
            "review_knowledge_map": review,
            "high_impact_historical": impact,
            "frontier_recent": recent,
        }
        chosen: list[RecommendationEntry] = []
        max_total = min(5, int(self.config["max_total"]))
        for category in self.config["selection_order"]:
            for entry in groups[category]:
                if len(chosen) >= max_total:
                    break
                if any(entry.aliases & existing.aliases for existing in chosen):
                    continue
                chosen.append(entry)

        for entry in pool_by_alias.values():
            entry.last_considered_at = considered_at
        for recommendation in chosen:
            for alias, entry in pool_by_alias.items():
                if alias in recommendation.aliases:
                    recommendation.pool_reason = entry.recommendation_reason
                    break
        return CuratedSelectionResult(
            recommendations=chosen,
            historical_papers=historical_papers,
            reading_pool=reading_pool,
        )
