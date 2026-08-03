from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from paper_radar.fetchers.arxiv import ArxivFetchError, parse_atom
from paper_radar.models import Paper, SeenState
from paper_radar.reader_models import DailyRecommendations, ReadingPoolEntry, RecommendationEntry
from paper_radar.reader_pipeline import execute_reader_historical_run
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import (
    CandidateStorage,
    PoolError,
    ReadingPoolStorage,
    RecommendationStorage,
)
from paper_radar.recommendation import RecommendationEngine
from paper_radar.storage import RadarStorage


def make_paper(
    index: int,
    *,
    fit: int = 80,
    topic: str = "legged_humanoid",
    keyword: str = "quadruped robots",
    version: int = 1,
) -> Paper:
    return Paper(
        arxiv_id=f"2608.{index:05d}v{version}",
        base_id=f"2608.{index:05d}",
        version=version,
        title=f"Specific Robotics Paper {index}: {keyword}",
        authors=["Fixture Author"],
        published="2026-08-03T01:00:00Z",
        updated="2026-08-03T01:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary=f"A physical robot study of {keyword}.",
        abs_url=f"https://arxiv.org/abs/2608.{index:05d}v{version}",
        pdf_url=f"https://arxiv.org/pdf/2608.{index:05d}v{version}",
        research_fit=fit,
        video_potential=60,
        matched_topics=[topic],
        matched_keywords=[keyword],
        research_reasons=[],
        video_reasons=[],
    )


def make_pool_entry(index: int, *, status: str = "unread", dismissed: bool = False) -> ReadingPoolEntry:
    paper = make_paper(index, topic="pose_rescue", keyword="rescue robotics")
    return ReadingPoolEntry(
        base_arxiv_id=paper.base_id,
        title=paper.title,
        source="arXiv API",
        topic_tags=paper.matched_topics,
        recommendation_reason="Manually queued fixture paper",
        added_at="2026-07-01T10:00:00+08:00",
        last_considered_at=None,
        recommended_at=None,
        recommendation_count=0,
        reading_status=status,
        dismissed=dismissed,
        priority=50,
        paper=paper,
    )


def test_all_category_and_total_limits(profile) -> None:
    engine = RecommendationEngine(profile)
    recent = [
        make_paper(1, topic="legged_humanoid", keyword="quadruped robots"),
        make_paper(2, topic="mobile_manipulation", keyword="mobile manipulation"),
        make_paper(3, topic="diffusion_visuomotor", keyword="diffusion policy"),
        make_paper(4, topic="vla_foundation", keyword="vision-language-action"),
    ]
    update = make_paper(10, fit=90, topic="optimal_control", keyword="whole-body control", version=2)
    state = SeenState(
        papers={update.base_id: {"latest_version": 1}},
        last_successful_run_at="2026-08-02T10:00:00+08:00",
    )
    pool = [make_pool_entry(20), make_pool_entry(21), make_pool_entry(22)]

    result = engine.select(
        recent_new=recent,
        version_updates=[update],
        reading_pool=pool,
        history={},
        seen_before=state,
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )
    counts = {
        category: sum(1 for entry in result.recommendations if entry.category == category)
        for category in ("recent_new", "reading_pool", "important_update")
    }

    assert len(result.recommendations) <= 5
    assert counts["recent_new"] <= 3
    assert counts["reading_pool"] <= 2
    assert counts["important_update"] <= 1
    assert counts == {"recent_new": 3, "reading_pool": 1, "important_update": 1}


def test_pool_can_fill_two_slots_without_lowering_recent_threshold(profile) -> None:
    engine = RecommendationEngine(profile)
    eligible = make_paper(1, fit=60, topic="legged_humanoid", keyword="humanoid robots")
    below = make_paper(2, fit=39, topic="mobile_manipulation", keyword="mobile manipulation")
    result = engine.select(
        recent_new=[eligible, below],
        version_updates=[],
        reading_pool=[make_pool_entry(20), make_pool_entry(21)],
        history={},
        seen_before=SeenState(),
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )

    assert [entry.paper.base_id for entry in result.recommendations if entry.category == "recent_new"] == [eligible.base_id]
    assert sum(entry.category == "reading_pool" for entry in result.recommendations) == 2
    assert len(result.recommendations) == 3


def test_zero_allowed_and_generic_keyword_cannot_qualify(profile) -> None:
    generic = make_paper(
        1,
        fit=100,
        topic="robot_learning",
        keyword="reinforcement learning",
    )
    below_threshold = make_paper(
        2,
        fit=39,
        topic="legged_humanoid",
        keyword="quadruped robots",
    )

    result = RecommendationEngine(profile).select(
        recent_new=[generic, below_threshold],
        version_updates=[],
        reading_pool=[],
        history={},
        seen_before=SeenState(),
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )

    assert result.recommendations == []


def test_topic_diversity_prevents_near_duplicate_recommendations(profile) -> None:
    first = make_paper(1, fit=90, topic="diffusion_visuomotor", keyword="diffusion policy")
    duplicate = make_paper(2, fit=89, topic="diffusion_visuomotor", keyword="visuomotor policy")
    diverse = make_paper(3, fit=70, topic="legged_humanoid", keyword="legged robots")

    result = RecommendationEngine(profile).select(
        recent_new=[first, duplicate, diverse],
        version_updates=[],
        reading_pool=[],
        history={},
        seen_before=SeenState(),
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )

    ids = [entry.paper.base_id for entry in result.recommendations]
    assert first.base_id in ids
    assert duplicate.base_id not in ids
    assert diverse.base_id in ids


def test_new_version_reappears_only_when_important_update_threshold_is_met(profile) -> None:
    ordinary_update = make_paper(
        1,
        fit=59,
        topic="optimal_control",
        keyword="whole-body control",
        version=2,
    )
    important_update = make_paper(
        2,
        fit=80,
        topic="optimal_control",
        keyword="whole-body control",
        version=2,
    )
    state = SeenState(
        papers={
            ordinary_update.base_id: {"latest_version": 1},
            important_update.base_id: {"latest_version": 1},
        }
    )

    result = RecommendationEngine(profile).select(
        recent_new=[],
        version_updates=[ordinary_update, important_update],
        reading_pool=[],
        history={},
        seen_before=state,
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )

    assert [entry.paper.base_id for entry in result.recommendations] == [
        important_update.base_id
    ]
    assert result.recommendations[0].category == "important_update"


def test_dismissed_read_and_cooldown_entries_are_not_recommended(profile) -> None:
    dismissed = make_pool_entry(1, dismissed=True)
    read = make_pool_entry(2, status="read")
    cooling = make_pool_entry(3)
    available = make_pool_entry(4)
    history = {
        cooling.base_arxiv_id: [
            {"date": "2026-07-28", "generated_at": "", "category": "reading_pool", "version": 1}
        ]
    }

    result = RecommendationEngine(profile).select(
        recent_new=[],
        version_updates=[],
        reading_pool=[dismissed, read, cooling, available],
        history=history,
        seen_before=SeenState(),
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )

    assert [entry.paper.base_id for entry in result.recommendations] == [available.base_arxiv_id]


def test_reading_pool_crud_and_errors(tmp_path: Path) -> None:
    storage = ReadingPoolStorage(tmp_path / "data")
    entry = make_pool_entry(1)
    storage.add(entry)
    assert storage.get(entry.base_arxiv_id).title == entry.title

    with pytest.raises(PoolError, match="already"):
        storage.add(entry)
    storage.set_status(entry.base_arxiv_id, "queued")
    assert storage.get(entry.base_arxiv_id).reading_status == "queued"
    storage.dismiss(entry.base_arxiv_id)
    assert storage.get(entry.base_arxiv_id).dismissed is True
    with pytest.raises(PoolError, match="Invalid reading status"):
        storage.set_status(entry.base_arxiv_id, "later")
    with pytest.raises(PoolError, match="not in"):
        storage.dismiss("9999.99999")


class HistoricalFetcher:
    def __init__(self, papers):
        self.papers = papers

    def fetch_for_date(self, target_date, timezone_name, categories):
        return self.papers


class FailingFetcher:
    def fetch_for_date(self, target_date, timezone_name, categories):
        raise ArxivFetchError("reader fixture failure")


def test_candidate_metadata_never_leaks_to_reader_page(
    tmp_path: Path, atom_xml: str, profile
) -> None:
    source = parse_atom(atom_xml)[0]
    selected = replace(
        source,
        title="Selected Specific Quadruped Paper",
        research_fit=90,
        video_potential=70,
        matched_topics=["legged_humanoid"],
        matched_keywords=["quadruped robots"],
    )
    hidden = replace(
        parse_atom(atom_xml)[1],
        title="UNSELECTED BACKGROUND CANDIDATE",
        research_fit=10,
        video_potential=10,
        matched_topics=["robot_learning"],
        matched_keywords=["reinforcement learning"],
        summary="A generic machine learning experiment.",
        categories=["cs.LG"],
        primary_category="cs.LG",
        published="2026-08-03T02:00:00Z",
        updated="2026-08-03T02:00:00Z",
    )

    result = execute_reader_historical_run(
        tmp_path,
        datetime(2026, 8, 3).date(),
        profile=profile,
        fetcher=HistoricalFetcher([selected, hidden]),
        now=datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    html = result.archive_path.read_text(encoding="utf-8")
    batch = CandidateStorage(tmp_path / "data").load("2026-08-03")
    daily = RecommendationStorage(tmp_path / "data").load("2026-08-03")

    assert len(batch.papers) == 2
    assert len(daily.recommendations) == 1
    assert "Selected Specific Quadruped Paper" in html
    assert "UNSELECTED BACKGROUND CANDIDATE" not in html
    assert "All Other Papers" not in html


def test_empty_recommendation_page_has_exact_message(tmp_path: Path, profile) -> None:
    storage = RecommendationStorage(tmp_path / "data")
    storage.save(
        DailyRecommendations(
            date="2026-08-03",
            generated_at="2026-08-03T10:15:00+08:00",
            recommendations=[],
            candidate_count=200,
            mode="incremental",
            selection_config=profile.recommendations,
        )
    )
    renderer = RecommendationSiteRenderer(tmp_path / "site", storage, profile)
    index, archive = renderer.render("2026-08-03")
    for path in (index, archive):
        html = path.read_text(encoding="utf-8")
        assert "今日没有发现足够值得推荐的论文。" in html
        assert "200 background candidates" in html
        assert "paper-card" not in html
    assert 'href="assets/reader.css"' in index.read_text(encoding="utf-8")
    assert 'href="../assets/reader.css"' in archive.read_text(encoding="utf-8")


def test_reader_network_failure_preserves_all_three_layers_and_page(
    tmp_path: Path, profile
) -> None:
    paths = {
        tmp_path / "data" / "seen_ids.json": '{"schema_version":2,"last_successful_run_at":null,"papers":{}}',
        tmp_path / "data" / "reading_pool.json": "stable pool",
        tmp_path / "data" / "candidates" / "2026-08-03.json": "stable candidates",
        tmp_path / "data" / "recommendations" / "2026-08-03.json": "stable recommendations",
        tmp_path / "site" / "index.html": "stable page",
    }
    for path, content in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    with pytest.raises(ArxivFetchError, match="reader fixture failure"):
        execute_reader_historical_run(
            tmp_path,
            datetime(2026, 8, 3).date(),
            profile=profile,
            fetcher=FailingFetcher(),
            now=datetime(2026, 8, 3, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    for path, content in paths.items():
        assert path.read_text(encoding="utf-8") == content
