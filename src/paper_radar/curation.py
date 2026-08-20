from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.historical_scoring import (
    historical_relevance_eligible,
    knowledge_map_signals,
    score_historical_papers,
)
from paper_radar.history_models import HistoricalPaper, canonical_paper_id
from paper_radar.models import Paper
from paper_radar.reader_models import DismissalEntry, ReadingPoolEntry, RecommendationEntry
from paper_radar.recommendation_utility import (
    assess_recommendation_utility,
    detect_subtopics,
)
from paper_radar.scoring import robotics_context_gate


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


def _daily_reading_rank(entry: RecommendationEntry) -> tuple[float, float, str, str]:
    """Rank reading value deterministically without using video potential."""
    return (
        entry.recommendation_utility,
        entry.recommendation_base_score,
        entry.paper.updated,
        entry.canonical_paper_id,
    )


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
        self.dismissal_config = profile.dismissals
        self.excluded_terms = [
            _normalise(term) for term in profile.recommendations["excluded_terms"]
        ]
        self.topic_weights = {
            str(topic["id"]): float(topic["weight"])
            for topic in profile.scoring["topics"]
        }

    def _recommendation_entry(
        self,
        *,
        category: str,
        paper: Paper,
        base_score: float,
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        reasons: list[str],
        historical_paper: HistoricalPaper | None = None,
        document_type: str | None = None,
    ) -> RecommendationEntry:
        utility = assess_recommendation_utility(
            paper,
            base_score=base_score,
            history=history,
            target_date=target_date,
            profile=self.profile,
            document_type=document_type,
        )
        return RecommendationEntry(
            category=category,
            paper=paper,
            historical_paper=historical_paper,
            reasons=[*reasons, *utility.reasons],
            core_topics=utility.core_topics,
            subtopics=utility.subtopics,
            document_type=utility.document_type,
            domain_affinity=utility.domain_affinity,
            domain_affinity_adjustment=utility.domain_affinity_adjustment,
            redundancy_penalty=utility.redundancy_penalty,
            recommendation_base_score=utility.recommendation_base_score,
            recommendation_utility=utility.recommendation_utility,
            days_since_same_subtopic=utility.days_since_same_subtopic,
            semantic_suppressed=utility.semantic_suppressed,
        )

    def _primary_topic(self, topics: list[str]) -> str:
        return max(
            [self.profile.canonical_topic_id(topic) for topic in topics],
            key=lambda topic: self.topic_weights.get(topic, 0),
            default="unclassified",
        )

    def _recent_signals(
        self, paper: Paper, config: dict[str, Any]
    ) -> tuple[list[str], list[str]] | None:
        core = list(
            dict.fromkeys(
                canonical
                for topic in paper.matched_topics
                if (canonical := self.profile.canonical_topic_id(topic))
                in self.core_topics
            )
        )
        strong = [
            keyword
            for keyword in paper.matched_keywords
            if _normalise(keyword) not in self.generic_keywords
        ]
        text = _normalise(f"{paper.title} {paper.summary}")
        if any(term in text for term in self.excluded_terms):
            return None
        if not robotics_context_gate(
            paper.title, paper.summary, "", self.profile
        ).eligible:
            return None
        if paper.research_fit < int(config["min_research_fit"]):
            return None
        if len(core) < int(config["min_core_topic_matches"]):
            return None
        if len(strong) < int(config["min_non_generic_keyword_matches"]):
            return None
        return core, strong

    def _within_active_reading_window(self, paper: Paper, target_date: str) -> bool:
        if not paper.published or not paper.published[:4].isdigit():
            return True
        age = date.fromisoformat(target_date).year - int(paper.published[:4])
        return age <= int(self.profile.historical_discovery["max_reading_age_years"])

    @staticmethod
    def _within_recent_days(
        paper: Paper, target_date: str, max_age_days: int
    ) -> bool:
        if not paper.published:
            return False
        try:
            published = date.fromisoformat(paper.published[:10])
        except ValueError:
            return False
        age = (date.fromisoformat(target_date) - published).days
        return 0 <= age <= max_age_days

    def _model_based_signals(
        self, paper: Paper, config: dict[str, Any]
    ) -> tuple[list[str], list[str]] | None:
        text = _normalise(f"{paper.title} {paper.summary}")
        if any(term in text for term in self.excluded_terms):
            return None
        context = robotics_context_gate(
            paper.title, paper.summary, "", self.profile
        )
        if not context.eligible or paper.research_fit < int(config["min_research_fit"]):
            return None
        canonical_topics = {
            self.profile.canonical_topic_id(topic) for topic in paper.matched_topics
        }
        if "robot_control_optimization" not in canonical_topics:
            return None
        allowed = set(config.get("method_subtopics", []))
        methods = [
            subtopic
            for subtopic in detect_subtopics(paper, self.profile)
            if subtopic in allowed
        ]
        if not methods:
            return None
        return methods, context.positive_matches

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
        candidates: list[tuple[RecommendationEntry, str]] = []
        for paper in papers:
            paper.last_considered_at = considered_at
            if paper.dismissed or paper.reading_status not in eligible_statuses:
                continue
            if paper.aliases & self._dismissed_aliases:
                continue
            relevant, relevance_reasons = historical_relevance_eligible(
                paper,
                self.profile,
                as_of_year=date.fromisoformat(target_date).year,
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
            primary = self._primary_topic(paper.matched_topics)
            if primary in self._cooldown_topics:
                continue
            if _cooling(
                paper.aliases,
                history,
                target_date,
                int(config["cooldown_days"]),
            ):
                continue
            reasons = [
                *relevance_reasons,
                f"historical_value_score {paper.historical_value_score:.1f} ≥ {threshold:.1f}",
                *paper.historical_score_reasons,
                f"影响力元数据更新于 {paper.metadata_updated_at}",
            ]
            entry = self._recommendation_entry(
                category=category,
                paper=paper.to_reader_paper(),
                historical_paper=paper,
                base_score=float(paper.historical_value_score),
                history=history,
                target_date=target_date,
                reasons=reasons,
            )
            if entry.recommendation_utility < float(
                config.get("min_recommendation_utility", float("-inf"))
            ):
                continue
            if entry.redundancy_penalty < 0 and entry.recommendation_utility < float(
                config.get("min_redundant_utility", float("-inf"))
            ):
                continue
            if entry.semantic_suppressed:
                continue
            candidates.append((entry, primary))

        selected: list[RecommendationEntry] = []
        topic_counts: dict[str, int] = {}
        for entry, primary in sorted(
            candidates,
            key=lambda item: (
                item[0].recommendation_utility,
                item[0].recommendation_base_score,
                item[0].canonical_paper_id,
            ),
            reverse=True,
        ):
            if category == "high_impact_historical":
                if topic_counts.get(primary, 0) >= int(config["max_same_primary_topic"]):
                    continue
                max_overlap = float(config["max_topic_overlap"])
                if any(
                    _topic_overlap(set(entry.paper.matched_topics), set(other.paper.matched_topics))
                    > max_overlap
                    for other in already_selected + selected
                ):
                    continue
            if any(entry.aliases & other.aliases for other in already_selected + selected):
                continue
            selected.append(entry)
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
        candidates: list[tuple[RecommendationEntry, str]] = []
        for paper in papers:
            if not self._within_active_reading_window(paper, target_date):
                continue
            if knowledge_map_signals(
                paper.title, paper.summary, "", self.profile
            ):
                continue
            signals = self._recent_signals(paper, config)
            aliases = {f"arxiv:{paper.base_id}".casefold()}
            if signals is None or _cooling(
                aliases, history, target_date, int(config["cooldown_days"])
            ):
                continue
            if aliases & self._dismissed_aliases:
                continue
            if any(aliases & entry.aliases for entry in already_selected):
                continue
            core, strong = signals
            primary = self._primary_topic(paper.matched_topics)
            if primary in self._cooldown_topics:
                continue
            entry = self._recommendation_entry(
                category="frontier_recent",
                paper=paper,
                base_score=float(paper.research_fit),
                history=history,
                target_date=target_date,
                reasons=[
                    f"research_fit {paper.research_fit} ≥ {int(config['min_research_fit'])}",
                    f"Core topics matched: {', '.join(core)}",
                    f"Specific keywords matched: {', '.join(strong)}",
                    f"Diversity topic: {primary}",
                ],
            )
            if entry.recommendation_utility < float(
                config.get("min_recommendation_utility", float("-inf"))
            ):
                continue
            if entry.redundancy_penalty < 0 and entry.recommendation_utility < float(
                config.get("min_redundant_utility", float("-inf"))
            ):
                continue
            if entry.semantic_suppressed:
                continue
            candidates.append((entry, primary))

        selected: list[RecommendationEntry] = []
        topic_counts: dict[str, int] = {}
        for entry, primary in sorted(
            candidates,
            key=lambda item: _daily_reading_rank(item[0]),
            reverse=True,
        ):
            if topic_counts.get(primary, 0) >= int(config["max_same_primary_topic"]):
                continue
            if any(
                _topic_overlap(set(entry.paper.matched_topics), set(other.paper.matched_topics))
                > float(config["max_topic_overlap"])
                for other in already_selected + selected
            ):
                continue
            if any(entry.aliases & other.aliases for other in already_selected + selected):
                continue
            selected.append(entry)
            topic_counts[primary] = topic_counts.get(primary, 0) + 1
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def _select_recent_knowledge_maps(
        self,
        papers: list[Paper],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        already_selected: list[RecommendationEntry],
    ) -> list[RecommendationEntry]:
        category_config = self.config["review_knowledge_map"]
        recent_config = self.config["frontier_recent"]
        remaining = max(
            0,
            int(category_config["max_count"])
            - sum(
                entry.category == "review_knowledge_map"
                for entry in already_selected
            ),
        )
        if remaining == 0:
            return []

        candidates: list[RecommendationEntry] = []
        for paper in papers:
            if not self._within_active_reading_window(paper, target_date):
                continue
            document_signals = knowledge_map_signals(
                paper.title, paper.summary, "", self.profile
            )
            if not document_signals:
                continue
            signals = self._recent_signals(paper, recent_config)
            if signals is None:
                continue
            aliases = {f"arxiv:{paper.base_id}".casefold()}
            if aliases & self._dismissed_aliases:
                continue
            if _cooling(
                aliases,
                history,
                target_date,
                int(category_config["cooldown_days"]),
            ):
                continue
            if any(aliases & entry.aliases for entry in already_selected):
                continue
            core, strong = signals
            primary = self._primary_topic(paper.matched_topics)
            if primary in self._cooldown_topics:
                continue
            context = robotics_context_gate(
                paper.title, paper.summary, "", self.profile
            )
            entry = self._recommendation_entry(
                category="review_knowledge_map",
                paper=paper,
                base_score=float(paper.research_fit),
                history=history,
                target_date=target_date,
                reasons=[
                    f"Knowledge-map signals: {', '.join(document_signals)}",
                    f"Robotics context: {', '.join(context.positive_matches)}",
                    f"research_fit {paper.research_fit} ≥ {int(recent_config['min_research_fit'])}",
                    f"Core topics matched: {', '.join(core)}",
                    f"Specific keywords matched: {', '.join(strong)}",
                ],
            )
            if entry.recommendation_utility < float(
                category_config.get("min_recommendation_utility", float("-inf"))
            ):
                continue
            if entry.redundancy_penalty < 0 and entry.recommendation_utility < float(
                category_config.get("min_redundant_utility", float("-inf"))
            ):
                continue
            if entry.semantic_suppressed:
                continue
            candidates.append(entry)

        selected: list[RecommendationEntry] = []
        for entry in sorted(
            candidates,
            key=_daily_reading_rank,
            reverse=True,
        ):
            if any(entry.aliases & other.aliases for other in already_selected + selected):
                continue
            selected.append(entry)
            if len(selected) >= remaining:
                break
        return selected

    def _select_journal(
        self,
        papers: list[HistoricalPaper],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        considered_at: str,
        already_selected: list[RecommendationEntry],
    ) -> list[RecommendationEntry]:
        config = self.config["journal_recent"]
        target = date.fromisoformat(target_date)
        cutoff = target - timedelta(days=int(config.get("recency_days", 60)))
        eligible_statuses = set(config.get("eligible_statuses", ["unread", "queued"]))
        candidates: list[HistoricalPaper] = []
        for paper in papers:
            paper.last_considered_at = considered_at
            if paper.dismissed or paper.reading_status not in eligible_statuses:
                continue
            if paper.aliases & self._dismissed_aliases:
                continue
            if not any(
                source.startswith("journal_search:")
                for source in paper.discovery_source
            ):
                continue
            if paper.is_knowledge_map:
                continue
            if not paper.publication_date:
                continue
            try:
                published = date.fromisoformat(paper.publication_date[:10])
            except ValueError:
                continue
            if not cutoff <= published <= target:
                continue
            reader_paper = paper.to_reader_paper()
            if self._recent_signals(reader_paper, config) is None:
                continue
            candidates.append(paper)
        candidates.sort(
            key=lambda paper: (
                paper.research_fit,
                paper.publication_date or "",
            ),
            reverse=True,
        )

        ranked: list[tuple[RecommendationEntry, str]] = []
        for paper in candidates:
            reader_paper = paper.to_reader_paper()
            core, strong = self._recent_signals(reader_paper, config)
            primary = self._primary_topic(reader_paper.matched_topics)
            if primary in self._cooldown_topics:
                continue
            if _cooling(
                paper.aliases,
                history,
                target_date,
                int(config.get("cooldown_days", 14)),
            ):
                continue
            if any(paper.aliases & entry.aliases for entry in already_selected):
                continue
            journals = sorted(
                {
                    source.split(":", 1)[1]
                    for source in paper.discovery_source
                    if source.startswith("journal_search:")
                }
            )
            reasons = [
                f"期刊新论文: {', '.join(journals)}",
                f"research_fit {reader_paper.research_fit} ≥ {int(config['min_research_fit'])}",
                f"Core topics matched: {', '.join(core)}",
                f"Specific keywords matched: {', '.join(strong)}",
            ]
            entry = self._recommendation_entry(
                category="journal_recent",
                paper=reader_paper,
                historical_paper=paper,
                base_score=float(reader_paper.research_fit),
                history=history,
                target_date=target_date,
                reasons=reasons,
            )
            if entry.recommendation_utility < float(
                config.get("min_recommendation_utility", float("-inf"))
            ):
                continue
            if entry.redundancy_penalty < 0 and entry.recommendation_utility < float(
                config.get("min_redundant_utility", float("-inf"))
            ):
                continue
            if entry.semantic_suppressed:
                continue
            ranked.append((entry, primary))

        selected: list[RecommendationEntry] = []
        topic_counts: dict[str, int] = {}
        for entry, primary in sorted(
            ranked,
            key=lambda item: _daily_reading_rank(item[0]),
            reverse=True,
        ):
            if topic_counts.get(primary, 0) >= int(
                config.get("max_same_primary_topic", 1)
            ):
                continue
            if any(entry.aliases & other.aliases for other in already_selected + selected):
                continue
            selected.append(entry)
            topic_counts[primary] = topic_counts.get(primary, 0) + 1
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def _select_model_based_recent(
        self,
        papers: list[Paper],
        historical_papers: list[HistoricalPaper],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        considered_at: str,
        already_selected: list[RecommendationEntry],
    ) -> list[RecommendationEntry]:
        config = self.config["model_based_recent"]
        eligible_statuses = set(config.get("eligible_statuses", ["unread", "queued"]))
        sources: list[
            tuple[Paper, HistoricalPaper | None, set[str], list[str]]
        ] = [
            (
                paper,
                None,
                {f"arxiv:{paper.base_id}".casefold()},
                [],
            )
            for paper in papers
        ]
        for historical in historical_papers:
            historical.last_considered_at = considered_at
            if historical.dismissed or historical.reading_status not in eligible_statuses:
                continue
            journals = sorted(
                source.split(":", 1)[1]
                for source in historical.discovery_source
                if source.startswith("journal_search:")
            )
            if not journals or historical.is_knowledge_map:
                continue
            sources.append(
                (
                    historical.to_reader_paper(),
                    historical,
                    historical.aliases,
                    [f"Recent journal source: {', '.join(journals)}"],
                )
            )

        candidates: list[RecommendationEntry] = []
        for paper, historical, aliases, source_reasons in sources:
            if not self._within_recent_days(
                paper, target_date, int(config["max_age_days"])
            ):
                continue
            if knowledge_map_signals(
                paper.title, paper.summary, "", self.profile
            ):
                continue
            signal_config = config
            if historical is not None:
                signal_config = {
                    **config,
                    "min_research_fit": config["min_journal_research_fit"],
                }
            signals = self._model_based_signals(paper, signal_config)
            if signals is None or _cooling(
                aliases, history, target_date, int(config["cooldown_days"])
            ):
                continue
            if aliases & self._dismissed_aliases:
                continue
            if any(aliases & entry.aliases for entry in already_selected):
                continue
            method_subtopics, robotics_matches = signals
            source_adjustment = (
                float(config.get("journal_source_adjustment", 0))
                if historical is not None
                else 0.0
            )
            base_score = float(paper.research_fit) + source_adjustment
            primary = self._primary_topic(paper.matched_topics)
            if primary in self._cooldown_topics:
                continue
            entry = self._recommendation_entry(
                category="model_based_recent",
                paper=paper,
                base_score=base_score,
                history=history,
                target_date=target_date,
                historical_paper=historical,
                reasons=[
                    *source_reasons,
                    f"Recent model-based robotics paper: published within {int(config['max_age_days'])} days",
                    f"Robotics context: {', '.join(robotics_matches)}",
                    f"Strong method signals: {', '.join(method_subtopics)}",
                    f"research_fit {paper.research_fit} ≥ {int(signal_config['min_research_fit'])}",
                    *(
                        [
                            "Journal metadata normalization: "
                            f"{source_adjustment:+g} (no arXiv category score)"
                        ]
                        if source_adjustment
                        else []
                    ),
                ],
            )
            if entry.recommendation_utility < float(
                config["min_recommendation_utility"]
            ):
                continue
            if entry.redundancy_penalty < 0 and entry.recommendation_utility < float(
                config["min_redundant_utility"]
            ):
                continue
            if entry.semantic_suppressed:
                continue
            candidates.append(entry)

        selected: list[RecommendationEntry] = []
        for entry in sorted(candidates, key=_daily_reading_rank, reverse=True):
            if any(entry.aliases & other.aliases for other in already_selected + selected):
                continue
            selected.append(entry)
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
        dismissals: list[DismissalEntry] | None = None,
    ) -> CuratedSelectionResult:
        dismissals = dismissals or []
        self._dismissed_aliases = {
            entry.canonical_paper_id.casefold() for entry in dismissals
        }
        window_days = int(self.dismissal_config.get("topic_cooldown_window_days", 30))
        min_dismissals = int(
            self.dismissal_config.get("min_dismissals_for_topic_cooldown", 2)
        )
        cooldown_days = int(
            self.dismissal_config.get("topic_cooldown_days", 14)
        )
        target = date.fromisoformat(target_date)
        topic_feedback_dates: dict[str, list[date]] = {}
        for entry in dismissals:
            if not entry.dismissed_at:
                continue
            dismissed_on = date.fromisoformat(entry.dismissed_at[:10])
            elapsed_days = (target - dismissed_on).days
            if not 0 <= elapsed_days < window_days:
                continue
            for topic in entry.topics:
                canonical_topic = self.profile.canonical_topic_id(topic)
                topic_feedback_dates.setdefault(canonical_topic, []).append(
                    dismissed_on
                )
        self._cooldown_topics = {
            topic
            for topic, feedback_dates in topic_feedback_dates.items()
            if len(feedback_dates) >= min_dismissals
            and (target - max(feedback_dates)).days < cooldown_days
        }
        combined, pool_by_alias = self._combine_historical_sources(
            historical_papers, reading_pool
        )
        ordered_historical = score_historical_papers(
            combined, self.profile, as_of_year=date.fromisoformat(target_date).year
        )
        recent = self._select_recent(
            recent_new,
            history,
            target_date,
            [],
        )
        journal = self._select_journal(
            ordered_historical,
            history,
            target_date,
            considered_at,
            recent,
        )
        model_based = self._select_model_based_recent(
            recent_new,
            ordered_historical,
            history,
            target_date,
            considered_at,
            recent + journal,
        )
        review = self._select_historical(
            ordered_historical,
            category="review_knowledge_map",
            history=history,
            target_date=target_date,
            considered_at=considered_at,
            already_selected=recent + journal + model_based,
        )
        recent_review = self._select_recent_knowledge_maps(
            recent_new,
            history,
            target_date,
            recent + journal + model_based + review,
        )
        impact = self._select_historical(
            ordered_historical,
            category="high_impact_historical",
            history=history,
            target_date=target_date,
            considered_at=considered_at,
            already_selected=recent + journal + model_based + review + recent_review,
        )
        groups = {
            "journal_recent": journal,
            "review_knowledge_map": review + recent_review,
            "high_impact_historical": impact,
            "frontier_recent": recent,
            "model_based_recent": model_based,
        }
        chosen: list[RecommendationEntry] = []
        max_total = min(5, int(self.config["max_total"]))
        recent_categories = {"frontier_recent", "journal_recent"}
        max_recent_total = int(self.config.get("max_recent_total", 3))
        for category in self.config["selection_order"]:
            for entry in groups[category]:
                if len(chosen) >= max_total:
                    break
                if category in recent_categories and sum(
                    existing.category in recent_categories for existing in chosen
                ) >= max_recent_total:
                    continue
                if any(entry.aliases & existing.aliases for existing in chosen):
                    continue
                selected_subtopics = {
                    subtopic for existing in chosen for subtopic in existing.subtopics
                }
                if entry.subtopics and set(entry.subtopics) <= selected_subtopics:
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
