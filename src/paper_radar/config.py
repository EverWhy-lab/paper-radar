from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the research profile is missing or invalid."""


@dataclass(frozen=True)
class FetchConfig:
    endpoint: str
    user_agent: str
    page_size: int
    max_pages: int
    page_delay_seconds: float
    timeout_seconds: float
    retries: int
    lookback_days: int = 7


@dataclass(frozen=True)
class OpenAlexConfig:
    endpoint: str
    user_agent: str
    timeout_seconds: float
    retries: int
    retry_delay_seconds: float
    cache_ttl_days: int
    daily_request_budget: int
    default_per_page: int


@dataclass(frozen=True)
class LLMAnalysisConfig:
    enabled: bool
    provider: str
    endpoint: str
    model: str
    timeout_seconds: float
    retries: int
    retry_delay_seconds: float
    max_tokens: int
    language: str
    abstract_char_limit: int
    reader_profile: dict[str, list[str]]


@dataclass(frozen=True)
class ResearchProfile:
    site_name: str
    timezone: str
    site_github_repo: str
    fetch: FetchConfig
    categories: dict[str, float]
    scoring: dict[str, Any]
    video_scoring: dict[str, Any]
    sections: dict[str, Any]
    recommendations: dict[str, Any]
    openalex: OpenAlexConfig
    llm_analysis: LLMAnalysisConfig
    dismissals: dict[str, Any]
    journals: dict[str, Any]
    historical_discovery: dict[str, Any]
    historical_scoring: dict[str, Any]

    @property
    def topic_aliases(self) -> dict[str, str]:
        return {
            str(old_id): str(new_id)
            for old_id, new_id in self.scoring.get("topic_aliases", {}).items()
        }

    def canonical_topic_id(self, topic_id: str) -> str:
        return self.topic_aliases.get(topic_id, topic_id)

    @property
    def topic_labels(self) -> dict[str, str]:
        labels = {
            str(topic["id"]): str(topic["label"])
            for topic in self.scoring["topics"]
        }
        labels.update(
            {
                old_id: labels.get(new_id, old_id)
                for old_id, new_id in self.topic_aliases.items()
            }
        )
        labels.update(
            {
                str(topic_id): str(label)
                for topic_id, label in self.scoring.get(
                    "legacy_topic_labels", {}
                ).items()
            }
        )
        return labels


def load_profile(path: Path) -> ResearchProfile:
    if not path.exists():
        raise ConfigError(f"Research profile not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    try:
        site = raw["site"]
        fetch_raw = raw["fetch"]
        categories = {str(key): float(value) for key, value in raw["categories"].items()}
        fetch = FetchConfig(
            endpoint=str(fetch_raw["endpoint"]),
            user_agent=str(fetch_raw["user_agent"]),
            page_size=int(fetch_raw["page_size"]),
            max_pages=int(fetch_raw["max_pages"]),
            page_delay_seconds=float(fetch_raw["page_delay_seconds"]),
            timeout_seconds=float(fetch_raw["timeout_seconds"]),
            retries=int(fetch_raw["retries"]),
            lookback_days=int(fetch_raw.get("lookback_days", 7)),
        )
        openalex_raw = raw["openalex"]
        openalex = OpenAlexConfig(
            endpoint=str(openalex_raw["endpoint"]).rstrip("/"),
            user_agent=str(openalex_raw["user_agent"]),
            timeout_seconds=float(openalex_raw["timeout_seconds"]),
            retries=int(openalex_raw["retries"]),
            retry_delay_seconds=float(openalex_raw["retry_delay_seconds"]),
            cache_ttl_days=int(openalex_raw["cache_ttl_days"]),
            daily_request_budget=int(openalex_raw["daily_request_budget"]),
            default_per_page=int(openalex_raw["default_per_page"]),
        )
        llm_raw = raw.get("llm_analysis") or {}
        llm_analysis = LLMAnalysisConfig(
            enabled=bool(llm_raw.get("enabled", False)),
            provider=str(llm_raw.get("provider", "deepseek")),
            endpoint=str(
                llm_raw.get(
                    "endpoint", "https://api.deepseek.com/chat/completions"
                )
            ).rstrip("/"),
            model=str(llm_raw.get("model", "deepseek-chat")),
            timeout_seconds=float(llm_raw.get("timeout_seconds", 60)),
            retries=int(llm_raw.get("retries", 2)),
            retry_delay_seconds=float(llm_raw.get("retry_delay_seconds", 2.0)),
            max_tokens=int(llm_raw.get("max_tokens", 1500)),
            language=str(llm_raw.get("language", "zh")),
            abstract_char_limit=int(llm_raw.get("abstract_char_limit", 3000)),
            reader_profile={
                str(section): [str(item) for item in items]
                for section, items in (llm_raw.get("reader_profile") or {}).items()
            },
        )
        profile = ResearchProfile(
            site_name=str(site["name"]),
            timezone=str(site["timezone"]),
            site_github_repo=str(site.get("github_repo", "")),
            fetch=fetch,
            categories=categories,
            scoring=dict(raw["scoring"]),
            video_scoring=dict(raw["video_scoring"]),
            sections=dict(raw["sections"]),
            recommendations=dict(raw["recommendations"]),
            openalex=openalex,
            llm_analysis=llm_analysis,
            dismissals=dict(raw.get("dismissals", {})),
            journals=dict(raw.get("journals", {})),
            historical_discovery=dict(raw["historical_discovery"]),
            historical_scoring=dict(raw["historical_scoring"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Missing or invalid research profile field: {exc}") from exc

    if not profile.categories:
        raise ConfigError("At least one arXiv category is required")
    if not profile.scoring.get("topics"):
        raise ConfigError("At least one scoring topic is required")
    topic_ids = [str(topic["id"]) for topic in profile.scoring["topics"]]
    if len(topic_ids) != len(set(topic_ids)):
        raise ConfigError("scoring.topics ids must be unique")
    aliases = profile.topic_aliases
    if any(target not in topic_ids for target in aliases.values()):
        raise ConfigError("scoring.topic_aliases must target current topic ids")
    robotics_context = profile.scoring.get("robotics_context", {})
    if not robotics_context.get("positive_terms"):
        raise ConfigError("scoring.robotics_context.positive_terms must not be empty")
    if fetch.page_size < 1 or fetch.max_pages < 1 or fetch.retries < 1 or fetch.lookback_days < 1:
        raise ConfigError("page_size, max_pages, retries, and lookback_days must be positive")
    if fetch.page_delay_seconds < 3:
        raise ConfigError("arXiv API page delay must be at least 3 seconds")
    if (
        openalex.timeout_seconds <= 0
        or openalex.retries < 1
        or openalex.retry_delay_seconds < 0
        or openalex.cache_ttl_days < 1
        or openalex.daily_request_budget < 1
        or not 1 <= openalex.default_per_page <= 200
    ):
        raise ConfigError("OpenAlex timeout, retries, cache, budget, and page size are invalid")
    if llm_analysis.enabled and (
        llm_analysis.timeout_seconds <= 0
        or llm_analysis.retries < 1
        or llm_analysis.max_tokens < 1
        or llm_analysis.abstract_char_limit < 1
        or not llm_analysis.language
    ):
        raise ConfigError(
            "llm_analysis timeout, retries, max_tokens, abstract limit, and language are invalid"
        )
    if llm_analysis.enabled and llm_analysis.provider != "deepseek":
        raise ConfigError("Only the deepseek LLM provider is supported in V0.2")
    if llm_analysis.enabled and any(
        not llm_analysis.reader_profile.get(section)
        for section in ("primary_focus", "secondary_focus")
    ):
        raise ConfigError(
            "llm_analysis.reader_profile must define primary_focus and secondary_focus"
        )
    dismissals = profile.dismissals
    for field in (
        "topic_cooldown_window_days",
        "min_dismissals_for_topic_cooldown",
        "topic_cooldown_days",
    ):
        if int(dismissals.get(field, 0)) < 0:
            raise ConfigError(f"dismissals.{field} must be non-negative")
    recommendation_limits = profile.recommendations
    unknown_core_topics = set(recommendation_limits["core_topic_ids"]) - set(topic_ids)
    if unknown_core_topics:
        raise ConfigError(
            "recommendations.core_topic_ids must reference current scoring topics"
        )
    if not 0 <= int(recommendation_limits.get("max_total", 5)) <= 5:
        raise ConfigError("recommendations.max_total must be between 0 and 5")
    for category, ceiling in (("recent_new", 3), ("reading_pool", 2), ("important_update", 1)):
        value = int(recommendation_limits[category]["max_count"])
        if not 0 <= value <= ceiling:
            raise ConfigError(
                f"recommendations.{category}.max_count must be between 0 and {ceiling}"
            )
    daily_mix = recommendation_limits["daily_mix"]
    if not 0 <= int(daily_mix["max_total"]) <= 5:
        raise ConfigError("recommendations.daily_mix.max_total must be between 0 and 5")
    daily_categories = {
        "frontier_recent",
        "journal_recent",
        "model_based_recent",
        "review_knowledge_map",
        "high_impact_historical",
    }
    selection_order = list(daily_mix.get("selection_order", []))
    if set(selection_order) != daily_categories or len(selection_order) != len(
        daily_categories
    ):
        raise ConfigError(
            "recommendations.daily_mix.selection_order must list every daily category once"
        )
    for category, ceiling in (
        ("frontier_recent", 2),
        ("model_based_recent", 1),
        ("high_impact_historical", 3),
        ("review_knowledge_map", 1),
        ("journal_recent", 2),
    ):
        value = int(daily_mix[category]["max_count"])
        if not 0 <= value <= ceiling:
            raise ConfigError(
                f"recommendations.daily_mix.{category}.max_count must be between 0 and {ceiling}"
            )
    if not 0 <= int(daily_mix.get("max_recent_total", 3)) <= 4:
        raise ConfigError(
            "recommendations.daily_mix.max_recent_total must be between 0 and 4"
        )
    subtopics = recommendation_limits.get("recommendation_subtopics", {})
    if not subtopics:
        raise ConfigError("recommendations.recommendation_subtopics must not be empty")
    for subtopic_id, rule in subtopics.items():
        if not str(subtopic_id).strip() or not rule.get("terms"):
            raise ConfigError("each recommendation subtopic needs an id and terms")
        topic_constraints = rule.get("matched_topics", rule.get("core_topics", []))
        unknown = {
            profile.canonical_topic_id(str(topic))
            for topic in topic_constraints
        } - set(topic_ids)
        if unknown:
            raise ConfigError(
                "recommendation subtopic topic constraints must reference scoring topic ids"
            )
    model_based = daily_mix["model_based_recent"]
    method_subtopics = set(model_based.get("method_subtopics", []))
    if not method_subtopics or method_subtopics - set(subtopics):
        raise ConfigError(
            "daily_mix.model_based_recent.method_subtopics must reference recommendation subtopics"
        )
    if int(model_based.get("max_age_days", 0)) < 1:
        raise ConfigError("daily_mix.model_based_recent.max_age_days must be positive")
    if int(model_based.get("min_journal_research_fit", 0)) < 0:
        raise ConfigError(
            "daily_mix.model_based_recent.min_journal_research_fit must be non-negative"
        )
    if not 0 <= float(model_based.get("journal_source_adjustment", 0)) <= 20:
        raise ConfigError(
            "daily_mix.model_based_recent.journal_source_adjustment must be between 0 and 20"
        )
    affinity = recommendation_limits.get("personal_domain_affinity", {})
    for affinity_class in ("preferred", "neutral", "peripheral"):
        if affinity_class not in affinity:
            raise ConfigError(
                "personal_domain_affinity must define preferred, neutral, and peripheral"
            )
        adjustment = float(affinity[affinity_class].get("adjustment", 0))
        if not -8 <= adjustment <= 8:
            raise ConfigError("personal domain affinity adjustments must be between -8 and 8")
    semantic = recommendation_limits.get("semantic_cooldown", {})
    for field in (
        "frontier_hard_cooldown_days",
        "frontier_window_days",
        "extended_window_days",
        "survey_window_days",
        "lexical_window_days",
    ):
        if int(semantic.get(field, 0)) < 0:
            raise ConfigError(f"semantic_cooldown.{field} must be non-negative")
    journals = profile.journals
    if journals.get("sources"):
        if int(journals.get("recency_days", 60)) < 1 or int(journals.get("per_journal_limit", 15)) < 1:
            raise ConfigError("journals.recency_days and journals.per_journal_limit must be positive")
        for source in journals["sources"]:
            if not str(source.get("source_id", "")).strip():
                raise ConfigError("journals.sources entries need a source_id")
    discovery = profile.historical_discovery
    if int(discovery["expansion_depth"]) != 1:
        raise ConfigError("historical_discovery.expansion_depth must be exactly 1 in V0.2")
    for field in ("per_query_limit", "per_seed_limit", "global_candidate_limit"):
        if int(discovery[field]) < 1:
            raise ConfigError(f"historical_discovery.{field} must be positive")
    preferred_years = int(discovery["preferred_recent_years"])
    max_age_years = int(discovery["max_reading_age_years"])
    if preferred_years < 1 or max_age_years < preferred_years:
        raise ConfigError(
            "historical discovery years must be positive and max_reading_age_years "
            "must be at least preferred_recent_years"
        )
    weights = profile.historical_scoring.get("weights", {})
    if not weights or any(float(value) < 0 for value in weights.values()):
        raise ConfigError("historical_scoring.weights must contain non-negative values")
    expected_historical_components = {
        "research_relevance",
        "recency",
        "normalized_citation",
        "fwci",
        "citation_momentum",
        "seed_provenance",
        "work_signal",
        "metadata_completeness",
    }
    if set(weights) != expected_historical_components:
        raise ConfigError(
            "historical_scoring.weights must define every supported score component"
        )
    return profile
