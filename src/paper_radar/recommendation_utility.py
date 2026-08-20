from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from paper_radar.config import ResearchProfile
from paper_radar.models import Paper


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "toward",
    "towards",
    "via",
    "with",
}


def normalize_text(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.casefold()))


def _contains(text: str, phrase: str) -> bool:
    normalized = normalize_text(phrase)
    return bool(normalized) and f" {normalized} " in f" {text} "


def _title_tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(value.casefold())
        if token not in _TITLE_STOPWORDS and len(token) > 1
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def detect_subtopics_from_metadata(
    *,
    title: str,
    summary: str,
    matched_topics: Iterable[str],
    matched_keywords: Iterable[str],
    profile: ResearchProfile,
) -> list[str]:
    # Topic ids constrain which subtopic rules may fire, but are not semantic
    # evidence themselves (for example, robot_data_scaling_sim2real must not
    # automatically label every paper as both data scaling and sim-to-real).
    text = normalize_text(" ".join([title, summary, *matched_keywords]))
    canonical_topics = {
        profile.canonical_topic_id(topic) for topic in matched_topics
    }
    detected: list[str] = []
    for subtopic_id, rule in profile.recommendations.get(
        "recommendation_subtopics", {}
    ).items():
        topic_constraints = rule.get("matched_topics", rule.get("core_topics", []))
        required_topics = {
            profile.canonical_topic_id(str(topic))
            for topic in topic_constraints
        }
        if required_topics and not (required_topics & canonical_topics):
            continue
        if any(_contains(text, str(term)) for term in rule.get("terms", [])):
            detected.append(str(subtopic_id))
    return detected


def detect_subtopics(paper: Paper, profile: ResearchProfile) -> list[str]:
    return detect_subtopics_from_metadata(
        title=paper.title,
        summary=paper.summary,
        matched_topics=paper.matched_topics,
        matched_keywords=paper.matched_keywords,
        profile=profile,
    )


def detect_document_type(paper: Paper, profile: ResearchProfile) -> str:
    text = normalize_text(f"{paper.title} {paper.summary}")
    config = profile.recommendations.get("document_types", {})
    if any(_contains(text, term) for term in config.get("survey_terms", [])):
        return "survey"
    if any(_contains(text, term) for term in config.get("benchmark_terms", [])):
        return "benchmark"
    return "method"


def survey_family_from_topics(
    matched_topics: Iterable[str], profile: ResearchProfile
) -> str | None:
    """Return a broad fallback family only when a survey lacks a specific theme."""
    topic_weights = {
        str(topic["id"]): float(topic["weight"])
        for topic in profile.scoring["topics"]
    }
    canonical_topics = list(
        dict.fromkeys(
            profile.canonical_topic_id(str(topic)) for topic in matched_topics
        )
    )
    primary = max(
        (topic for topic in canonical_topics if topic in topic_weights),
        key=lambda topic: topic_weights[topic],
        default=None,
    )
    return f"survey:{primary}" if primary else None


def semantic_redundancy_keys(
    *,
    subtopics: Iterable[str],
    document_type: str,
    matched_topics: Iterable[str],
    profile: ResearchProfile,
) -> set[str]:
    keys = {str(subtopic) for subtopic in subtopics if subtopic}
    if document_type == "survey" and not keys:
        family = survey_family_from_topics(matched_topics, profile)
        if family:
            keys.add(family)
    return keys


def classify_domain_affinity(
    paper: Paper, profile: ResearchProfile
) -> tuple[str, float, list[str]]:
    config = profile.recommendations.get("personal_domain_affinity", {})
    text = normalize_text(f"{paper.title} {paper.summary}")
    # A specific application domain is more informative than a generic preferred
    # method phrase (for example, "surgical robot foundation model").
    for affinity_class in ("peripheral", "preferred", "neutral"):
        rule = config.get(affinity_class, {})
        matches = [
            str(term)
            for term in rule.get("terms", [])
            if _contains(text, str(term))
        ]
        if matches:
            return affinity_class, float(rule.get("adjustment", 0)), matches
    neutral = config.get("neutral", {})
    return "neutral", float(neutral.get("adjustment", 0)), []


@dataclass(frozen=True)
class RedundancyAssessment:
    penalty: float
    days_since_same_subtopic: int | None
    reasons: list[str]
    suppressed: bool = False


def _history_events(
    history: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for items in history.values():
        for event in items:
            key = (
                str(event.get("date", "")),
                str(event.get("canonical_paper_id", "")),
                str(event.get("title", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    return events


def semantic_redundancy(
    paper: Paper,
    *,
    subtopics: list[str],
    document_type: str,
    history: dict[str, list[dict[str, Any]]],
    target_date: str,
    profile: ResearchProfile,
) -> RedundancyAssessment:
    config = profile.recommendations.get("semantic_cooldown", {})
    target = date.fromisoformat(target_date)
    candidate_subtopics = semantic_redundancy_keys(
        subtopics=subtopics,
        document_type=document_type,
        matched_topics=paper.matched_topics,
        profile=profile,
    )
    scoring_topics = {
        str(topic["id"]) for topic in profile.scoring.get("topics", [])
    }
    candidate_core = {
        profile.canonical_topic_id(topic)
        for topic in paper.matched_topics
        if profile.canonical_topic_id(topic)
        in scoring_topics
    }
    candidate_title_tokens = _title_tokens(paper.title)
    best_penalty = 0.0
    best_reason: list[str] = []
    days_since: int | None = None
    suppressed = False

    for event in _history_events(history):
        try:
            previous = date.fromisoformat(str(event.get("date", ""))[:10])
        except ValueError:
            continue
        elapsed = (target - previous).days
        if elapsed <= 0:
            continue
        event_subtopics = {
            str(value) for value in event.get("subtopics", []) if value
        }
        if not event_subtopics:
            event_subtopics = set(
                detect_subtopics_from_metadata(
                    title=str(event.get("title", "")),
                    summary=str(event.get("summary", "")),
                    matched_topics=event.get("core_topics")
                    or event.get("matched_topics", []),
                    matched_keywords=event.get("matched_keywords", []),
                    profile=profile,
                )
            )
        event_document_type = str(event.get("document_type", "method"))
        event_matched_topics = event.get("matched_topics") or event.get(
            "core_topics", []
        )
        event_subtopics = semantic_redundancy_keys(
            subtopics=event_subtopics,
            document_type=event_document_type,
            matched_topics=event_matched_topics,
            profile=profile,
        )
        shared = candidate_subtopics & event_subtopics
        penalty = 0.0
        reason: list[str] = []

        if shared:
            days_since = elapsed if days_since is None else min(days_since, elapsed)
            new_secondary = candidate_subtopics - event_subtopics
            if document_type == "survey" and event_document_type == "survey":
                survey_window = int(config.get("survey_window_days", 30))
                if elapsed <= survey_window:
                    recent_window = int(config.get("survey_recent_window_days", 14))
                    penalty = float(
                        config.get(
                            "survey_recent_penalty" if elapsed <= recent_window else "survey_late_penalty",
                            18 if elapsed <= recent_window else 12,
                        )
                    )
            else:
                frontier_window = int(config.get("frontier_window_days", 3))
                extended_window = int(config.get("extended_window_days", 7))
                if elapsed <= frontier_window:
                    penalty = float(config.get("frontier_penalty", 8))
                elif elapsed <= extended_window:
                    penalty = float(config.get("extended_penalty", 4))
            if penalty:
                semantic_label = (
                    "Survey family"
                    if all(value.startswith("survey:") for value in shared)
                    else "Subtopic"
                )
                reason = [
                    f"{semantic_label}: {', '.join(sorted(shared))}",
                    f"Recently recommended: {elapsed} day{'s' if elapsed != 1 else ''} ago",
                ]
                if new_secondary:
                    factor = float(config.get("secondary_topic_penalty_factor", 0.35))
                    penalty *= factor
                    reason.append(
                        "Subtopic cooldown bypassed: new secondary topic = "
                        + ", ".join(sorted(new_secondary))
                    )
                elif elapsed <= int(config.get("frontier_hard_cooldown_days", 0)):
                    suppressed = True
                    reason.append(
                        f"Subtopic short cooldown: suppressed within {int(config['frontier_hard_cooldown_days'])} days"
                    )
        elif elapsed <= int(config.get("lexical_window_days", 7)):
            event_core = {
                profile.canonical_topic_id(str(topic))
                for topic in event.get("matched_topics")
                or event.get("core_topics", [])
            }
            similarity = _jaccard(
                candidate_title_tokens, _title_tokens(str(event.get("title", "")))
            )
            if (
                candidate_core & event_core
                and similarity >= float(config.get("lexical_similarity_threshold", 0.55))
            ):
                penalty = float(config.get("lexical_penalty", 4))
                reason = [
                    f"Title/topic overlap: {similarity:.2f}",
                    f"Recently recommended: {elapsed} day{'s' if elapsed != 1 else ''} ago",
                ]

        if penalty > best_penalty:
            best_penalty = penalty
            best_reason = reason

    if best_penalty:
        best_reason.append(f"Redundancy penalty: -{best_penalty:g}")
    return RedundancyAssessment(
        penalty=-round(best_penalty, 1),
        days_since_same_subtopic=days_since,
        reasons=best_reason,
        suppressed=suppressed,
    )


@dataclass(frozen=True)
class RecommendationUtility:
    core_topics: list[str]
    subtopics: list[str]
    document_type: str
    domain_affinity: str
    domain_affinity_adjustment: float
    domain_affinity_terms: list[str]
    redundancy_penalty: float
    recommendation_base_score: float
    recommendation_utility: float
    days_since_same_subtopic: int | None
    semantic_suppressed: bool
    reasons: list[str]


def assess_recommendation_utility(
    paper: Paper,
    *,
    base_score: float,
    history: dict[str, list[dict[str, Any]]],
    target_date: str,
    profile: ResearchProfile,
    document_type: str | None = None,
) -> RecommendationUtility:
    core_topics = list(
        dict.fromkeys(
            canonical
            for topic in paper.matched_topics
            if (canonical := profile.canonical_topic_id(topic))
            in set(profile.recommendations.get("core_topic_ids", []))
        )
    )
    subtopics = detect_subtopics(paper, profile)
    resolved_document_type = document_type or detect_document_type(paper, profile)
    affinity, affinity_adjustment, affinity_terms = classify_domain_affinity(
        paper, profile
    )
    redundancy = semantic_redundancy(
        paper,
        subtopics=subtopics,
        document_type=resolved_document_type,
        history=history,
        target_date=target_date,
        profile=profile,
    )
    utility = round(base_score + affinity_adjustment + redundancy.penalty, 1)
    affinity_detail = (
        f"; matched {', '.join(affinity_terms)}" if affinity_terms else ""
    )
    reasons = [
        f"Detected subtopics: {', '.join(subtopics) if subtopics else 'none'}",
        f"Personal domain affinity: {affinity} ({affinity_adjustment:+g}{affinity_detail})",
        *redundancy.reasons,
        "recommendation_utility "
        f"{utility:.1f} = base {base_score:.1f} + affinity "
        f"{affinity_adjustment:+g} + redundancy {redundancy.penalty:+g}",
    ]
    return RecommendationUtility(
        core_topics=core_topics,
        subtopics=subtopics,
        document_type=resolved_document_type,
        domain_affinity=affinity,
        domain_affinity_adjustment=affinity_adjustment,
        domain_affinity_terms=affinity_terms,
        redundancy_penalty=redundancy.penalty,
        recommendation_base_score=round(base_score, 1),
        recommendation_utility=utility,
        days_since_same_subtopic=redundancy.days_since_same_subtopic,
        semantic_suppressed=redundancy.suppressed,
        reasons=reasons,
    )
