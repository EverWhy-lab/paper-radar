from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_radar.config import ResearchProfile, load_profile
from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.fetchers.arxiv import ArxivClient
from paper_radar.pipeline import (
    PaperFetcher,
    _day_window,
    classify_historical_papers,
    classify_incremental_papers,
)
from paper_radar.reader_models import (
    CandidateBatch,
    DailyRecommendations,
    LLMAnalysis,
    RecommendationEntry,
)
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import CandidateStorage, ReadingPoolStorage, RecommendationStorage
from paper_radar.history_storage import HistoricalPaperStorage
from paper_radar.providers.base import LLMAnalysisProvider
from paper_radar.providers.deepseek import DeepSeekClient
from paper_radar.scoring import score_papers
from paper_radar.storage import RadarStorage


@dataclass(frozen=True)
class ReaderRunResult:
    date: str
    candidate_count: int
    recommendation_count: int
    recommendation_path: Path
    candidate_path: Path
    index_path: Path
    archive_path: Path
    historical_candidate_count: int = 0
    llm_analysis_count: int = 0


def _merge_recommendations(
    existing: DailyRecommendations | None,
    additions: list[RecommendationEntry],
    config: dict,
) -> list[RecommendationEntry]:
    existing_entries = existing.recommendations if existing else []
    groups: dict[str, list[RecommendationEntry]] = {
        category: [] for category in config["selection_order"]
    }
    for entry in existing_entries + additions:
        if entry.category not in groups:
            continue
        if any(item.aliases & entry.aliases for item in groups[entry.category]):
            continue
        groups[entry.category].append(entry)
    for category in groups:
        groups[category] = groups[category][: int(config[category]["max_count"])]

    final: list[RecommendationEntry] = []
    used_aliases: set[str] = set()
    for category in config["selection_order"]:
        for entry in groups[category]:
            if len(final) >= min(5, int(config["max_total"])):
                break
            if not (entry.aliases & used_aliases):
                final.append(entry)
                used_aliases.update(entry.aliases)
    return final


def _run_reader(
    project_root: Path,
    *,
    target_date: date,
    run_at: datetime,
    mode: str,
    profile: ResearchProfile,
    fetcher: PaperFetcher,
    llm_provider: LLMAnalysisProvider | None,
) -> ReaderRunResult:
    data_dir = project_root / "data"
    state_storage = RadarStorage(data_dir)
    candidate_storage = CandidateStorage(data_dir)
    pool_storage = ReadingPoolStorage(data_dir)
    recommendation_storage = RecommendationStorage(data_dir)
    historical_storage = HistoricalPaperStorage(data_dir)
    seen_before = state_storage.load_seen(migrated_at=run_at.isoformat(timespec="seconds"))

    if mode == "historical":
        candidates = fetcher.fetch_for_date(
            target_date, profile.timezone, list(profile.categories)
        )
        window_start, window_end = _day_window(target_date, profile.timezone)
        recent_new, version_updates = classify_historical_papers(
            candidates, target_date, profile.timezone
        )
    else:
        candidates = fetcher.fetch_recent(
            run_at,
            profile.fetch.lookback_days,
            profile.timezone,
            list(profile.categories),
        )
        window_start = run_at - timedelta(days=profile.fetch.lookback_days)
        window_end = run_at
        recent_new, version_updates = classify_incremental_papers(candidates, seen_before)

    scored_candidates = score_papers(candidates, profile)
    scored_by_id = {paper.base_id: paper for paper in scored_candidates}
    scored_new = [scored_by_id[paper.base_id] for paper in recent_new]
    scored_updates = [scored_by_id[paper.base_id] for paper in version_updates]
    generated_at = run_at.isoformat(timespec="seconds")

    batch = CandidateBatch(
        date=target_date.isoformat(),
        fetched_at=generated_at,
        mode=mode,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        papers=scored_candidates,
    )
    pool_entries = pool_storage.load()
    historical_papers = historical_storage.load()
    history = recommendation_storage.history(exclude_date=target_date.isoformat())
    selection = CuratedRecommendationEngine(profile).select(
        recent_new=scored_new,
        historical_papers=historical_papers,
        reading_pool=pool_entries,
        history=history,
        target_date=target_date.isoformat(),
        considered_at=generated_at,
    )

    existing = (
        recommendation_storage.load(target_date.isoformat())
        if recommendation_storage.path_for(target_date.isoformat()).exists()
        else None
    )
    final_entries = _merge_recommendations(
        existing if mode == "incremental" else None,
        selection.recommendations,
        profile.recommendations["daily_mix"],
    )
    existing_aliases = {
        alias
        for entry in (existing.recommendations if existing else [])
        for alias in entry.aliases
    }
    newly_selected = [
        entry for entry in final_entries if not (entry.aliases & existing_aliases)
    ]
    newly_selected_aliases = {
        alias for entry in newly_selected for alias in entry.aliases
    }
    for paper in selection.historical_papers:
        if paper.aliases & newly_selected_aliases:
            paper.recommended_at = generated_at
            paper.recommendation_count += 1
    for entry in selection.reading_pool:
        if f"arxiv:{entry.base_arxiv_id}".casefold() in newly_selected_aliases:
            entry.recommended_at = generated_at
            entry.recommendation_count += 1

    llm_analyses: list[LLMAnalysis] | None = None
    if llm_provider is not None:
        try:
            llm_analyses = llm_provider.analyze_recommendations(final_entries)
        except Exception as exc:  # optional enrichment; never block the daily page
            print(f"LLM daily guide skipped: {exc}", file=sys.stderr)
            llm_analyses = None

    daily = DailyRecommendations(
        date=target_date.isoformat(),
        generated_at=generated_at,
        recommendations=final_entries,
        candidate_count=len(scored_candidates),
        mode=mode,
        selection_config=profile.recommendations["daily_mix"],
        historical_candidate_count=len(historical_papers),
        llm_analysis=llm_analyses,
    )
    updated_seen = state_storage.updated_seen(seen_before, scored_candidates, generated_at)
    updated_seen.last_run_mode = f"reader_{mode}"
    updated_seen.last_run_date = target_date.isoformat()
    updated_seen.last_run_new_submission_count = len(scored_new)
    updated_seen.last_run_version_update_count = len(scored_updates)
    updated_seen.last_run_candidate_count = len(scored_candidates)

    # All network work and recommendation decisions complete before any target is replaced.
    candidate_path = candidate_storage.save(batch)
    recommendation_path = recommendation_storage.save(daily)
    pool_storage.save(selection.reading_pool)
    historical_storage.save(selection.historical_papers)
    state_storage.save_seen(updated_seen)
    renderer = RecommendationSiteRenderer(
        project_root / "site", recommendation_storage, profile
    )
    index_path, archive_path = renderer.render(daily.date)
    return ReaderRunResult(
        date=daily.date,
        candidate_count=len(scored_candidates),
        recommendation_count=len(final_entries),
        recommendation_path=recommendation_path,
        candidate_path=candidate_path,
        index_path=index_path,
        archive_path=archive_path,
        historical_candidate_count=len(historical_papers),
        llm_analysis_count=len(llm_analyses or []),
    )


def _default_llm_provider(profile: ResearchProfile) -> DeepSeekClient | None:
    if not profile.llm_analysis.enabled:
        return None
    return DeepSeekClient(profile.llm_analysis)


def execute_reader_incremental_run(
    project_root: Path,
    *,
    profile: ResearchProfile | None = None,
    fetcher: PaperFetcher | None = None,
    now: datetime | None = None,
    llm_provider: LLMAnalysisProvider | None = None,
) -> ReaderRunResult:
    profile = profile or load_profile(project_root / "config" / "research_profile.yaml")
    zone = ZoneInfo(profile.timezone)
    run_at = (now or datetime.now(zone)).astimezone(zone)
    return _run_reader(
        project_root,
        target_date=run_at.date(),
        run_at=run_at,
        mode="incremental",
        profile=profile,
        fetcher=fetcher or ArxivClient(profile.fetch),
        llm_provider=(
            llm_provider if llm_provider is not None else _default_llm_provider(profile)
        ),
    )


def execute_reader_historical_run(
    project_root: Path,
    target_date: date,
    *,
    profile: ResearchProfile | None = None,
    fetcher: PaperFetcher | None = None,
    now: datetime | None = None,
    llm_provider: LLMAnalysisProvider | None = None,
) -> ReaderRunResult:
    profile = profile or load_profile(project_root / "config" / "research_profile.yaml")
    zone = ZoneInfo(profile.timezone)
    run_at = (now or datetime.now(zone)).astimezone(zone)
    return _run_reader(
        project_root,
        target_date=target_date,
        run_at=run_at,
        mode="historical",
        profile=profile,
        fetcher=fetcher or ArxivClient(profile.fetch),
        llm_provider=(
            llm_provider if llm_provider is not None else _default_llm_provider(profile)
        ),
    )
