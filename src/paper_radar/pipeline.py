from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from paper_radar.fetchers.arxiv import deduplicate_papers
from paper_radar.models import Paper


class PaperFetcher(Protocol):
    def fetch_for_date(self, target_date: date, timezone_name: str, categories: list[str]) -> list[Paper]: ...

    def fetch_recent(
        self,
        now: datetime,
        lookback_days: int,
        timezone_name: str,
        categories: list[str],
    ) -> list[Paper]: ...

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

