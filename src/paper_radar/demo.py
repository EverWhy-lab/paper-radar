from __future__ import annotations

from pathlib import Path

from paper_radar.config import load_profile
from paper_radar.models import SeenState
from paper_radar.reader_models import DailyRecommendations, ReadingPoolEntry
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import RecommendationStorage
from paper_radar.recommendation import RecommendationEngine
from paper_radar.storage import RadarStorage


def build_fixture_demo_pages(project_root: Path) -> list[Path]:
    """Build three clearly labelled demos from stored arXiv metadata."""
    profile = load_profile(project_root / "config" / "research_profile.yaml")
    legacy = RadarStorage(project_root / "data").load_daily("2026-07-31")
    pool_paper = legacy.papers[-1]
    pool = [
        ReadingPoolEntry(
            base_arxiv_id=pool_paper.base_id,
            title=pool_paper.title,
            source="Stored arXiv fixture metadata",
            topic_tags=pool_paper.matched_topics,
            recommendation_reason="Fixture entry manually admitted to the reading pool",
            added_at="2026-07-01T10:00:00+08:00",
            last_considered_at=None,
            recommended_at=None,
            recommendation_count=0,
            reading_status="unread",
            dismissed=False,
            priority=50,
            paper=pool_paper,
        )
    ]
    seen_before = SeenState(
        papers={
            paper.base_id: {"latest_version": max(1, paper.version - 1)}
            for paper in legacy.version_updates
        }
    )
    selected = RecommendationEngine(profile).select(
        recent_new=legacy.papers,
        version_updates=legacy.version_updates,
        reading_pool=pool,
        history={},
        seen_before=seen_before,
        target_date="2026-07-31",
        considered_at="2026-07-31T10:15:00+08:00",
    ).recommendations
    if len(selected) != 5:
        raise RuntimeError(f"Expected the stored fixture to produce 5 recommendations, got {len(selected)}")

    storage = RecommendationStorage(project_root / "data")
    renderer = RecommendationSiteRenderer(project_root / "site", storage, profile)
    scenarios = [
        (
            DailyRecommendations(
                date="fixture-5",
                generated_at="2026-07-31T10:15:00+08:00",
                recommendations=selected,
                candidate_count=len(legacy.all_papers),
                mode="fixture",
                selection_config=profile.recommendations,
            ),
            "recommendations-5.html",
            "5 selected papers",
        ),
        (
            DailyRecommendations(
                date="fixture-partial",
                generated_at="2026-07-31T10:15:00+08:00",
                recommendations=selected[:2],
                candidate_count=len(legacy.all_papers),
                mode="fixture",
                selection_config=profile.recommendations,
            ),
            "recommendations-partial.html",
            "2 selected papers; thresholds are not lowered",
        ),
        (
            DailyRecommendations(
                date="fixture-0",
                generated_at="2026-07-31T10:15:00+08:00",
                recommendations=[],
                candidate_count=len(legacy.all_papers),
                mode="fixture",
                selection_config=profile.recommendations,
            ),
            "recommendations-0.html",
            "0 selected papers",
        ),
    ]
    return [
        renderer.render_demo(daily, filename, label)
        for daily, filename, label in scenarios
    ]
