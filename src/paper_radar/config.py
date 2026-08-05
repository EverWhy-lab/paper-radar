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
    def topic_labels(self) -> dict[str, str]:
        return {
            str(topic["id"]): str(topic["label"])
            for topic in self.scoring["topics"]
        }


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
        or not llm_analysis.language
    ):
        raise ConfigError("llm_analysis timeout, retries, max_tokens, and language are invalid")
    if llm_analysis.enabled and llm_analysis.provider != "deepseek":
        raise ConfigError("Only the deepseek LLM provider is supported in V0.2")
    dismissals = profile.dismissals
    for field in (
        "topic_cooldown_window_days",
        "min_dismissals_for_topic_cooldown",
        "topic_cooldown_days",
    ):
        if int(dismissals.get(field, 0)) < 0:
            raise ConfigError(f"dismissals.{field} must be non-negative")
    recommendation_limits = profile.recommendations
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
    for category, ceiling in (
        ("frontier_recent", 2),
        ("high_impact_historical", 3),
        ("review_knowledge_map", 1),
        ("journal_recent", 2),
    ):
        value = int(daily_mix[category]["max_count"])
        if not 0 <= value <= ceiling:
            raise ConfigError(
                f"recommendations.daily_mix.{category}.max_count must be between 0 and {ceiling}"
            )
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
    weights = profile.historical_scoring.get("weights", {})
    if not weights or any(float(value) < 0 for value in weights.values()):
        raise ConfigError("historical_scoring.weights must contain non-negative values")
    return profile
