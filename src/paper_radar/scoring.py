from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.models import Paper


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[-–—_/]", " ", value)
    return " ".join(value.split())


def _contains(text: str, term: str) -> bool:
    normalised_text = _normalise(text)
    normalised_term = _normalise(term)
    pattern = r"(?<!\w)" + re.escape(normalised_term).replace(r"\ ", r"\s+") + r"(?!\w)"
    return re.search(pattern, normalised_text) is not None


def _find_keywords(title: str, summary: str, keywords: list[str]) -> tuple[list[str], str | None]:
    title_matches = [keyword for keyword in keywords if _contains(title, keyword)]
    summary_matches = [keyword for keyword in keywords if _contains(summary, keyword)]
    matches = list(dict.fromkeys(title_matches + summary_matches))
    if title_matches:
        return matches, "title"
    if summary_matches:
        return matches, "abstract"
    return [], None


def _clamp_score(value: float) -> int:
    return max(0, min(100, round(value)))


@dataclass(frozen=True)
class RoboticsContextResult:
    eligible: bool
    positive_matches: list[str]
    negative_matches: list[str]
    reason: str


def robotics_context_gate(
    title: str,
    abstract: str,
    topic_text: str,
    profile: ResearchProfile,
) -> RoboticsContextResult:
    config = profile.scoring["robotics_context"]
    text = " ".join(value for value in (title, abstract, topic_text) if value)
    positive = [
        str(term)
        for term in config["positive_terms"]
        if _contains(text, str(term))
    ]
    negative = [
        str(term)
        for term in config.get("negative_terms", [])
        if _contains(text, str(term))
    ]
    if positive:
        reason = f"Explicit robotics context: {', '.join(positive)}"
    elif negative:
        reason = (
            "No explicit robotics context; non-robotics domain signals: "
            + ", ".join(negative)
        )
    else:
        reason = (
            "No explicit robotics context; general control or learning terms alone "
            "do not establish robotics relevance"
        )
    return RoboticsContextResult(
        eligible=bool(positive),
        positive_matches=positive,
        negative_matches=negative,
        reason=reason,
    )


def score_paper(
    paper: Paper,
    profile: ResearchProfile,
    *,
    context_topic_text: str = "",
) -> Paper:
    title = _normalise(paper.title)
    summary = _normalise(paper.summary)
    abstract_multiplier = float(profile.scoring.get("abstract_multiplier", 0.65))
    core_topics = set(profile.recommendations["core_topic_ids"])
    core_title_bonus = float(profile.scoring.get("core_topic_title_bonus", 0))
    weak_keyword_multiplier = float(
        profile.scoring.get("weak_keyword_multiplier", 0.45)
    )
    research_raw = 0.0
    topic_ids: list[str] = []
    matched_keywords: list[str] = []
    research_reasons: list[dict[str, Any]] = []
    context = robotics_context_gate(
        paper.title,
        paper.summary,
        context_topic_text,
        profile,
    )
    allow_topic_scores = (
        context.eligible
        or not bool(profile.scoring.get("topics_require_robotics_context", False))
    )

    for topic in profile.scoring["topics"] if allow_topic_scores else []:
        strong_matches, strong_location = _find_keywords(
            title, summary, list(topic["keywords"])
        )
        weak_matches, weak_location = _find_keywords(
            title, summary, list(topic.get("weak_keywords", []))
        )
        if not strong_matches and not weak_matches:
            continue
        location = strong_location or weak_location
        matches = list(dict.fromkeys(strong_matches + weak_matches))
        if strong_location is not None:
            points = float(topic["weight"]) * (
                1 if strong_location == "title" else abstract_multiplier
            )
        else:
            points = float(topic["weight"]) * weak_keyword_multiplier * (
                1 if weak_location == "title" else abstract_multiplier
            )
        if strong_location == "title" and str(topic["id"]) in core_topics:
            points += core_title_bonus
        research_raw += points
        topic_ids.append(str(topic["id"]))
        matched_keywords.extend(matches)
        research_reasons.append(
            {
                "kind": "topic",
                "label": str(topic["label"]),
                "keywords": matches,
                "location": location,
                "points": round(points, 1),
            }
        )

    category_matches = [category for category in paper.categories if category in profile.categories]
    if category_matches:
        best_category = max(category_matches, key=lambda category: profile.categories[category])
        points = profile.categories[best_category]
        research_raw += points
        research_reasons.append(
            {
                "kind": "category",
                "label": best_category,
                "keywords": [best_category],
                "location": "category",
                "points": round(points, 1),
            }
        )

    for exclusion in profile.scoring.get("exclusions", []):
        term = str(exclusion["term"])
        if _contains(title, term) or _contains(summary, term):
            penalty = float(exclusion["penalty"])
            research_raw -= penalty
            research_reasons.append(
                {
                    "kind": "exclusion",
                    "label": term,
                    "keywords": [term],
                    "location": "title/abstract",
                    "points": -round(penalty, 1),
                }
            )

    research_reasons.append(
        {
            "kind": "robotics_context",
            "label": "Robotics context gate",
            "keywords": context.positive_matches or context.negative_matches,
            "location": "title/abstract/topics",
            "points": 0.0,
            "eligible": context.eligible,
            "positive_matches": context.positive_matches,
            "negative_matches": context.negative_matches,
            "reason": context.reason,
        }
    )

    paper.research_fit = _clamp_score(research_raw)
    paper.matched_topics = topic_ids
    paper.matched_keywords = list(dict.fromkeys(matched_keywords))
    paper.research_reasons = research_reasons

    video_config = profile.video_scoring
    video_raw = float(video_config.get("research_fit_factor", 0.25)) * paper.research_fit
    video_reasons: list[dict[str, Any]] = []
    if video_raw:
        video_reasons.append(
            {
                "kind": "research_fit",
                "label": "Research alignment component",
                "keywords": [],
                "location": "score",
                "points": round(video_raw, 1),
            }
        )
    video_abstract_multiplier = float(video_config.get("abstract_multiplier", 0.65))
    for signal in video_config.get("signals", []):
        matches, location = _find_keywords(title, summary, list(signal["keywords"]))
        if not matches or location is None:
            continue
        points = float(signal["weight"]) * (1 if location == "title" else video_abstract_multiplier)
        video_raw += points
        video_reasons.append(
            {
                "kind": "video_signal",
                "label": str(signal["label"]),
                "keywords": matches,
                "location": location,
                "points": round(points, 1),
            }
        )
    paper.video_potential = _clamp_score(video_raw)
    paper.video_reasons = video_reasons
    return paper


def score_papers(papers: list[Paper], profile: ResearchProfile) -> list[Paper]:
    scored = [score_paper(paper, profile) for paper in papers]
    return sorted(
        scored,
        key=lambda paper: (paper.research_fit, paper.video_potential, paper.updated, paper.base_id),
        reverse=True,
    )
