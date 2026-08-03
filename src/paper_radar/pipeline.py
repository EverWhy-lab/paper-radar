from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from paper_radar.config import ResearchProfile, load_profile
from paper_radar.fetchers.arxiv import ArxivClient, deduplicate_papers
from paper_radar.models import DailyRadar, Paper, SeenState
from paper_radar.rendering import SiteRenderer
from paper_radar.scoring import score_papers
from paper_radar.storage import RadarStorage


class PaperFetcher(Protocol):
    def fetch_for_date(self, target_date: date, timezone_name: str, categories: list[str]) -> list[Paper]: ...

    def fetch_recent(
        self,
        now: datetime,
        lookback_days: int,
        timezone_name: str,
        categories: list[str],
    ) -> list[Paper]: ...


@dataclass(frozen=True)
class RunResult:
    date: str
    paper_count: int
    new_submission_count: int
    version_update_count: int
    candidate_count: int
    index_path: Path
    archive_path: Path
    daily_path: Path


def _as_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _day_window(target_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(target_date, datetime_time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def classify_historical_papers(
    papers: list[Paper],
    target_date: date,
    timezone_name: str,
) -> tuple[list[Paper], list[Paper]]:
    """Classify an exact-day backfill from metadata alone, independent of local state."""
    start, end = _day_window(target_date, timezone_name)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    new_submissions: list[Paper] = []
    version_updates: list[Paper] = []
    for paper in deduplicate_papers(papers):
        if start_utc <= _as_utc(paper.published) < end_utc:
            new_submissions.append(paper)
        else:
            version_updates.append(paper)
    return new_submissions, version_updates


def classify_incremental_papers(
    papers: list[Paper],
    seen: SeenState,
) -> tuple[list[Paper], list[Paper]]:
    """Return only first discoveries and strictly newer arXiv versions."""
    new_submissions: list[Paper] = []
    version_updates: list[Paper] = []
    watermark = (
        datetime.fromisoformat(seen.last_successful_run_at).astimezone(timezone.utc)
        if seen.last_successful_run_at
        else None
    )
    for paper in deduplicate_papers(papers):
        if watermark is not None and _as_utc(paper.updated) <= watermark:
            continue
        current = seen.papers.get(paper.base_id)
        if current is None:
            new_submissions.append(paper)
        elif paper.version > int(current.get("latest_version", 0)):
            version_updates.append(paper)
    return new_submissions, version_updates


def _merge_daily_events(
    existing: DailyRadar | None,
    new_submissions: list[Paper],
    version_updates: list[Paper],
) -> tuple[list[Paper], list[Paper]]:
    new_by_id = {paper.base_id: paper for paper in (existing.papers if existing else [])}
    update_by_id = {
        paper.base_id: paper for paper in (existing.version_updates if existing else [])
    }
    for paper in new_submissions:
        current = new_by_id.get(paper.base_id)
        if current is None or (paper.version, paper.updated) > (current.version, current.updated):
            new_by_id[paper.base_id] = paper
        update_by_id.pop(paper.base_id, None)
    for paper in version_updates:
        if paper.base_id in new_by_id:
            current = new_by_id[paper.base_id]
            if (paper.version, paper.updated) > (current.version, current.updated):
                new_by_id[paper.base_id] = paper
            continue
        current = update_by_id.get(paper.base_id)
        if current is None or (paper.version, paper.updated) > (current.version, current.updated):
            update_by_id[paper.base_id] = paper
    return list(new_by_id.values()), list(update_by_id.values())


def _persist_and_render(
    project_root: Path,
    profile: ResearchProfile,
    storage: RadarStorage,
    radar: DailyRadar,
    state: SeenState,
    candidate_count: int,
    *,
    save_daily: bool = True,
) -> RunResult:
    daily_path = storage.save_daily(radar) if save_daily else storage.daily_path(radar.date)
    storage.save_seen(state)
    renderer = SiteRenderer(project_root / "site", storage, profile)
    index_path, archive_path = renderer.render(radar.date)
    return RunResult(
        date=radar.date,
        paper_count=len(radar.all_papers),
        new_submission_count=(radar.run_new_submission_count or 0),
        version_update_count=(radar.run_version_update_count or 0),
        candidate_count=candidate_count,
        index_path=index_path,
        archive_path=archive_path,
        daily_path=daily_path,
    )


def execute_run(
    project_root: Path,
    target_date: date,
    *,
    profile: ResearchProfile | None = None,
    fetcher: PaperFetcher | None = None,
) -> RunResult:
    """Run deterministic exact-day historical backfill."""
    profile = profile or load_profile(project_root / "config" / "research_profile.yaml")
    storage = RadarStorage(project_root / "data")
    active_fetcher = fetcher or ArxivClient(profile.fetch)

    candidates = active_fetcher.fetch_for_date(
        target_date,
        profile.timezone,
        list(profile.categories),
    )
    new_submissions, version_updates = classify_historical_papers(
        candidates, target_date, profile.timezone
    )
    scored_new = score_papers(new_submissions, profile)
    scored_updates = score_papers(version_updates, profile)
    generated_at = datetime.now(ZoneInfo(profile.timezone)).isoformat(timespec="seconds")
    window_start, window_end = _day_window(target_date, profile.timezone)
    radar = DailyRadar(
        date=target_date.isoformat(),
        generated_at=generated_at,
        papers=scored_new,
        version_updates=scored_updates,
        timezone=profile.timezone,
        run_mode="historical",
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        run_new_submission_count=len(scored_new),
        run_version_update_count=len(scored_updates),
    )
    seen = storage.load_seen(migrated_at=generated_at)
    updated_seen = storage.updated_seen(seen, radar.all_papers, generated_at)
    updated_seen.last_run_mode = "historical"
    updated_seen.last_run_date = radar.date
    updated_seen.last_run_new_submission_count = len(scored_new)
    updated_seen.last_run_version_update_count = len(scored_updates)
    updated_seen.last_run_candidate_count = len(candidates)
    return _persist_and_render(
        project_root, profile, storage, radar, updated_seen, len(candidates)
    )


def execute_incremental_run(
    project_root: Path,
    *,
    profile: ResearchProfile | None = None,
    fetcher: PaperFetcher | None = None,
    now: datetime | None = None,
) -> RunResult:
    """Run the default rolling-window discovery workflow."""
    profile = profile or load_profile(project_root / "config" / "research_profile.yaml")
    zone = ZoneInfo(profile.timezone)
    run_at = (now or datetime.now(zone)).astimezone(zone)
    generated_at = run_at.isoformat(timespec="seconds")
    target_date = run_at.date()
    storage = RadarStorage(project_root / "data")
    seen = storage.load_seen(migrated_at=generated_at)
    active_fetcher = fetcher or ArxivClient(profile.fetch)

    candidates = active_fetcher.fetch_recent(
        run_at,
        profile.fetch.lookback_days,
        profile.timezone,
        list(profile.categories),
    )
    new_submissions, version_updates = classify_incremental_papers(candidates, seen)
    scored_new = score_papers(new_submissions, profile)
    scored_updates = score_papers(version_updates, profile)

    existing: DailyRadar | None = None
    if storage.daily_path(target_date.isoformat()).exists():
        existing = storage.load_daily(target_date.isoformat())
    accumulated_new, accumulated_updates = _merge_daily_events(
        existing, scored_new, scored_updates
    )
    accumulated_new = score_papers(accumulated_new, profile)
    accumulated_updates = score_papers(accumulated_updates, profile)
    window_start = run_at - timedelta(days=profile.fetch.lookback_days)
    radar = DailyRadar(
        date=target_date.isoformat(),
        generated_at=generated_at,
        papers=accumulated_new,
        version_updates=accumulated_updates,
        timezone=profile.timezone,
        run_mode="incremental",
        window_start=window_start.isoformat(),
        window_end=run_at.isoformat(),
        run_new_submission_count=len(scored_new),
        run_version_update_count=len(scored_updates),
    )
    updated_seen = storage.updated_seen(seen, candidates, generated_at)
    updated_seen.last_run_mode = "incremental"
    updated_seen.last_run_date = radar.date
    updated_seen.last_run_new_submission_count = len(scored_new)
    updated_seen.last_run_version_update_count = len(scored_updates)
    updated_seen.last_run_candidate_count = len(candidates)
    preserve_historical_empty = (
        existing is not None
        and existing.run_mode == "historical"
        and not scored_new
        and not scored_updates
    )
    return _persist_and_render(
        project_root,
        profile,
        storage,
        radar,
        updated_seen,
        len(candidates),
        save_daily=not preserve_historical_empty,
    )
