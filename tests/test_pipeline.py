from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from paper_radar.fetchers.arxiv import ArxivFetchError, parse_atom
from paper_radar.models import DailyRadar, SeenState
from paper_radar.pipeline import execute_incremental_run, execute_run
from paper_radar.storage import RadarStorage


class FailingHistoricalFetcher:
    def fetch_for_date(self, target_date, timezone_name, categories):
        raise ArxivFetchError("fixture network failure")


class FailingIncrementalFetcher:
    def fetch_recent(self, now, lookback_days, timezone_name, categories):
        raise ArxivFetchError("fixture rolling network failure")


class IncrementalFetcher:
    def __init__(self, papers):
        self.papers = papers
        self.call = None

    def fetch_recent(self, now, lookback_days, timezone_name, categories):
        self.call = (now, lookback_days, timezone_name, categories)
        return self.papers


class HistoricalFetcher:
    def __init__(self, papers):
        self.papers = papers
        self.called_date = None

    def fetch_for_date(self, target_date, timezone_name, categories):
        self.called_date = target_date
        return self.papers

    def fetch_recent(self, *args, **kwargs):
        raise AssertionError("historical --date must not use the rolling fetcher")


def _state_for(paper, seen_at="2026-08-02T10:00:00+08:00") -> SeenState:
    return SeenState(
        last_successful_run_at=seen_at,
        papers={
            paper.base_id: {
                "base_arxiv_id": paper.base_id,
                "latest_version": paper.version,
                "latest_arxiv_id": paper.arxiv_id,
                "published_at": paper.published,
                "updated_at": paper.updated,
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
        },
    )


def test_default_run_finds_new_paper_across_natural_day(tmp_path: Path, atom_xml: str, profile) -> None:
    source = parse_atom(atom_xml)[0]
    previous_day = replace(
        source,
        published="2026-08-02T14:00:00Z",
        updated="2026-08-02T14:00:00Z",
    )
    fetcher = IncrementalFetcher([previous_day])
    now = datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai"))

    result = execute_incremental_run(
        tmp_path, profile=profile, fetcher=fetcher, now=now
    )
    radar = RadarStorage(tmp_path / "data").load_daily("2026-08-03")

    assert result.new_submission_count == 1
    assert result.version_update_count == 0
    assert radar.papers[0].base_id == previous_day.base_id
    assert radar.run_mode == "incremental"
    assert fetcher.call[1] == 7
    assert radar.window_start.startswith("2026-07-27T10:15:00")


def test_default_run_ignores_stale_unknown_candidate_and_preserves_exact_day_archive(
    tmp_path: Path, atom_xml: str, profile
) -> None:
    source = parse_atom(atom_xml)[0]
    stale = replace(
        source,
        published="2026-08-02T14:00:00Z",
        updated="2026-08-02T14:00:00Z",
    )
    storage = RadarStorage(tmp_path / "data")
    storage.save_daily(
        DailyRadar(
            date="2026-08-03",
            generated_at="2026-08-03T09:00:00+08:00",
            papers=[],
            run_mode="historical",
            run_new_submission_count=0,
        )
    )
    original_daily = storage.daily_path("2026-08-03").read_text(encoding="utf-8")
    storage.save_seen(
        SeenState(last_successful_run_at="2026-08-03T09:30:00+08:00")
    )

    result = execute_incremental_run(
        tmp_path,
        profile=profile,
        fetcher=IncrementalFetcher([stale]),
        now=datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.new_submission_count == 0
    assert result.version_update_count == 0
    assert storage.daily_path("2026-08-03").read_text(encoding="utf-8") == original_daily
    state = storage.load_seen()
    assert state.last_run_mode == "incremental"
    assert state.last_run_new_submission_count == 0
    assert stale.base_id in state.papers
    assert "本次运行未发现新论文" in (tmp_path / "site" / "index.html").read_text(
        encoding="utf-8"
    )


def test_higher_version_is_update_and_same_version_is_not_repeated(
    tmp_path: Path, atom_xml: str, profile
) -> None:
    source = parse_atom(atom_xml)[0]
    storage = RadarStorage(tmp_path / "data")
    storage.save_seen(_state_for(source))
    v2 = replace(
        source,
        arxiv_id=f"{source.base_id}v2",
        version=2,
        updated="2026-08-03T01:00:00Z",
    )
    now = datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai"))

    first = execute_incremental_run(
        tmp_path, profile=profile, fetcher=IncrementalFetcher([v2]), now=now
    )
    first_daily = storage.load_daily("2026-08-03")
    assert first.new_submission_count == 0
    assert first.version_update_count == 1
    assert first_daily.papers == []
    assert [paper.version for paper in first_daily.version_updates] == [2]

    repeated = execute_incremental_run(
        tmp_path,
        profile=profile,
        fetcher=IncrementalFetcher([v2]),
        now=now.replace(hour=11),
    )
    repeated_daily = storage.load_daily("2026-08-03")
    assert repeated.new_submission_count == 0
    assert repeated.version_update_count == 0
    assert len(repeated_daily.version_updates) == 1
    assert storage.load_seen().papers[source.base_id]["last_seen_at"].startswith(
        "2026-08-03T11:15:00"
    )


def test_new_and_updated_papers_are_stored_in_separate_sections(
    tmp_path: Path, atom_xml: str, profile
) -> None:
    new_paper, known = parse_atom(atom_xml)
    storage = RadarStorage(tmp_path / "data")
    known_v1 = replace(known, arxiv_id=f"{known.base_id}v1", version=1)
    storage.save_seen(_state_for(known_v1))
    now = datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai"))

    execute_incremental_run(
        tmp_path,
        profile=profile,
        fetcher=IncrementalFetcher([new_paper, known]),
        now=now,
    )
    radar = storage.load_daily("2026-08-03")
    html = (tmp_path / "site" / "archive" / "2026-08-03.html").read_text(encoding="utf-8")

    assert [paper.base_id for paper in radar.papers] == [new_paper.base_id]
    assert [paper.base_id for paper in radar.version_updates] == [known.base_id]
    assert "Paper Updates" in html
    assert "excluded from the main recommendation ranking" in html


def test_date_backfill_keeps_exact_day_path(tmp_path: Path, atom_xml: str, profile) -> None:
    papers = parse_atom(atom_xml)
    fetcher = HistoricalFetcher(papers)

    result = execute_run(
        tmp_path,
        date(2026, 8, 3),
        profile=profile,
        fetcher=fetcher,
    )
    radar = RadarStorage(tmp_path / "data").load_daily("2026-08-03")

    assert fetcher.called_date == date(2026, 8, 3)
    assert result.candidate_count == 2
    assert radar.run_mode == "historical"
    assert radar.window_start == "2026-08-03T00:00:00+08:00"
    assert radar.window_end == "2026-08-04T00:00:00+08:00"


def test_network_failure_preserves_existing_files(tmp_path: Path, profile) -> None:
    index = tmp_path / "site" / "index.html"
    daily = tmp_path / "data" / "daily" / "2026-08-03.json"
    seen = tmp_path / "data" / "seen_ids.json"
    for path, content in ((index, "old site"), (daily, "old daily"), (seen, "old seen")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    with pytest.raises(ArxivFetchError, match="fixture network failure"):
        execute_run(
            tmp_path,
            date(2026, 8, 3),
            profile=profile,
            fetcher=FailingHistoricalFetcher(),
        )

    assert index.read_text(encoding="utf-8") == "old site"
    assert daily.read_text(encoding="utf-8") == "old daily"
    assert seen.read_text(encoding="utf-8") == "old seen"


def test_incremental_network_failure_preserves_home_and_state(tmp_path: Path, profile) -> None:
    index = tmp_path / "site" / "index.html"
    seen = tmp_path / "data" / "seen_ids.json"
    index.parent.mkdir(parents=True)
    seen.parent.mkdir(parents=True)
    index.write_text("stable homepage", encoding="utf-8")
    seen.write_text('{"schema_version":2,"papers":{}}', encoding="utf-8")

    with pytest.raises(ArxivFetchError, match="rolling network failure"):
        execute_incremental_run(
            tmp_path,
            profile=profile,
            fetcher=FailingIncrementalFetcher(),
            now=datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    assert index.read_text(encoding="utf-8") == "stable homepage"
    assert seen.read_text(encoding="utf-8") == '{"schema_version":2,"papers":{}}'
