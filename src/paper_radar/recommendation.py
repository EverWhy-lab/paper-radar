from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.models import Paper, SeenState
from paper_radar.reader_models import ReadingPoolEntry, RecommendationEntry
from paper_radar.scoring import robotics_context_gate


@dataclass
class SelectionResult:
    recommendations: list[RecommendationEntry]
    reading_pool: list[ReadingPoolEntry]


def _normalise(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _in_cooldown(
    base_id: str,
    history: dict[str, list[dict[str, Any]]],
    target_date: str,
    cooldown_days: int,
) -> bool:
    current = date.fromisoformat(target_date)
    for item in history.get(base_id, []):
        previous = date.fromisoformat(str(item["date"]))
        difference = (current - previous).days
        if 0 <= difference < cooldown_days:
            return True
    return False


class RecommendationEngine:
    def __init__(self, profile: ResearchProfile) -> None:
        self.profile = profile
        self.config = profile.recommendations
        self.core_topics = set(self.config["core_topic_ids"])
        self.generic_keywords = {_normalise(term) for term in self.config["generic_keywords"]}
        self.excluded_terms = [_normalise(term) for term in self.config["excluded_terms"]]
        self.topic_weights = {
            str(topic["id"]): float(topic["weight"])
            for topic in profile.scoring["topics"]
        }

    def _signals(self, paper: Paper, category_config: dict[str, Any]) -> tuple[list[str], list[str]] | None:
        core = list(
            dict.fromkeys(
                canonical
                for topic in paper.matched_topics
                if (canonical := self.profile.canonical_topic_id(topic))
                in self.core_topics
            )
        )
        strong_keywords = [
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
        if paper.research_fit < int(category_config["min_research_fit"]):
            return None
        if len(core) < int(category_config["min_core_topic_matches"]):
            return None
        if len(strong_keywords) < int(category_config["min_non_generic_keyword_matches"]):
            return None
        return core, strong_keywords

    def _primary_topic(self, paper: Paper) -> str:
        return max(
            [
                self.profile.canonical_topic_id(topic)
                for topic in paper.matched_topics
            ],
            key=lambda topic: self.topic_weights.get(topic, 0),
            default="unclassified",
        )

    def _topic_overlap(self, left: Paper, right: Paper) -> float:
        left_topics = {
            self.profile.canonical_topic_id(topic) for topic in left.matched_topics
        }
        right_topics = {
            self.profile.canonical_topic_id(topic) for topic in right.matched_topics
        }
        union = left_topics | right_topics
        return len(left_topics & right_topics) / len(union) if union else 0.0

    def _select_recent(
        self,
        papers: list[Paper],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
    ) -> list[RecommendationEntry]:
        config = self.config["recent_new"]
        selected: list[RecommendationEntry] = []
        topic_counts: dict[str, int] = {}
        ordered = sorted(
            papers,
            key=lambda paper: (paper.research_fit, paper.video_potential, paper.updated),
            reverse=True,
        )
        for paper in ordered:
            signals = self._signals(paper, config)
            if signals is None or _in_cooldown(
                paper.base_id, history, target_date, int(config["cooldown_days"])
            ):
                continue
            core_topics, strong_keywords = signals
            primary = self._primary_topic(paper)
            if topic_counts.get(primary, 0) >= int(config["max_same_primary_topic"]):
                continue
            if any(
                self._topic_overlap(paper, entry.paper) > float(config["max_topic_overlap"])
                for entry in selected
            ):
                continue
            selected.append(
                RecommendationEntry(
                    category="recent_new",
                    paper=paper,
                    reasons=[
                        f"research_fit {paper.research_fit} ≥ {int(config['min_research_fit'])}",
                        f"Core topics matched: {', '.join(core_topics)}",
                        f"Specific keywords matched: {', '.join(strong_keywords)}",
                        f"Diversity topic: {primary}",
                    ],
                )
            )
            topic_counts[primary] = topic_counts.get(primary, 0) + 1
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def _select_updates(
        self,
        papers: list[Paper],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        seen_before: SeenState,
    ) -> list[RecommendationEntry]:
        config = self.config["important_update"]
        selected: list[RecommendationEntry] = []
        for paper in sorted(
            papers,
            key=lambda item: (item.research_fit, item.video_potential, item.version),
            reverse=True,
        ):
            signals = self._signals(paper, config)
            if signals is None or _in_cooldown(
                paper.base_id, history, target_date, int(config["cooldown_days"])
            ):
                continue
            core_topics, strong_keywords = signals
            previous_version = int(
                seen_before.papers.get(paper.base_id, {}).get(
                    "latest_version", max(1, paper.version - 1)
                )
            )
            selected.append(
                RecommendationEntry(
                    category="important_update",
                    paper=paper,
                    reasons=[
                        f"Version advanced from v{previous_version} to v{paper.version}",
                        f"research_fit {paper.research_fit} ≥ {int(config['min_research_fit'])}",
                        f"Core topics matched: {', '.join(core_topics)}",
                        f"Specific keywords matched: {', '.join(strong_keywords)}",
                    ],
                )
            )
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def _select_pool(
        self,
        entries: list[ReadingPoolEntry],
        history: dict[str, list[dict[str, Any]]],
        target_date: str,
        considered_at: str,
        excluded_ids: set[str],
    ) -> list[RecommendationEntry]:
        config = self.config["reading_pool"]
        eligible_statuses = set(config["eligible_statuses"])
        selected: list[RecommendationEntry] = []
        ordered = sorted(
            entries,
            key=lambda entry: (-entry.priority, entry.last_considered_at or "", entry.added_at),
        )
        for entry in ordered:
            if entry.dismissed or entry.reading_status not in eligible_statuses:
                continue
            entry.last_considered_at = considered_at
            if entry.priority < int(config["min_priority"]):
                continue
            if entry.base_arxiv_id in excluded_ids or _in_cooldown(
                entry.base_arxiv_id, history, target_date, int(config["cooldown_days"])
            ):
                continue
            selected.append(
                RecommendationEntry(
                    category="reading_pool",
                    paper=entry.paper,
                    pool_reason=entry.recommendation_reason,
                    reasons=[
                        "Manually admitted to the reading pool",
                        f"Pool priority {entry.priority} ≥ {int(config['min_priority'])}",
                        f"Reading status: {entry.reading_status}",
                    ],
                )
            )
            if len(selected) >= int(config["max_count"]):
                break
        return selected

    def select(
        self,
        *,
        recent_new: list[Paper],
        version_updates: list[Paper],
        reading_pool: list[ReadingPoolEntry],
        history: dict[str, list[dict[str, Any]]],
        seen_before: SeenState,
        target_date: str,
        considered_at: str,
    ) -> SelectionResult:
        recent = self._select_recent(recent_new, history, target_date)
        important = self._select_updates(
            version_updates, history, target_date, seen_before
        )
        reserved_ids = {entry.paper.base_id for entry in recent + important}
        pool = self._select_pool(
            reading_pool,
            history,
            target_date,
            considered_at,
            reserved_ids,
        )
        groups = {
            "recent_new": recent,
            "important_update": important,
            "reading_pool": pool,
        }
        chosen: list[RecommendationEntry] = []
        chosen_ids: set[str] = set()
        max_total = min(5, int(self.config["max_total"]))
        for category in self.config["selection_order"]:
            for entry in groups[category]:
                if entry.paper.base_id in chosen_ids or len(chosen) >= max_total:
                    continue
                chosen.append(entry)
                chosen_ids.add(entry.paper.base_id)

        return SelectionResult(recommendations=chosen, reading_pool=reading_pool)
