from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from paper_radar.backtest import OfflineBacktester
from paper_radar.models import Paper
from paper_radar.reader_models import CandidateBatch, DailyRecommendations, RecommendationEntry
from paper_radar.reader_storage import CandidateStorage, RecommendationStorage


def raw_paper(
    index: int,
    *,
    title: str | None = None,
    summary: str | None = None,
    published: str = "2026-08-01T00:00:00Z",
) -> Paper:
    base_id = f"2608.{index:05d}"
    return Paper(
        arxiv_id=f"{base_id}v1",
        base_id=base_id,
        version=1,
        title=title or f"Vision-Language-Action Robot Foundation Model {index}",
        authors=["Backtest Fixture"],
        published=published,
        updated="2026-08-01T00:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary=summary
        or "A vision-language-action robot foundation model for physical robot manipulation.",
        abs_url=f"https://arxiv.org/abs/{base_id}",
        pdf_url=f"https://arxiv.org/pdf/{base_id}",
    )


def save_batch(root: Path, day: str, papers: list[Paper]) -> None:
    CandidateStorage(root / "data").save(
        CandidateBatch(
            date=day,
            fetched_at=f"{day}T12:30:00+08:00",
            mode="fixture",
            window_start=f"{day}T00:00:00+08:00",
            window_end=f"{day}T12:30:00+08:00",
            papers=papers,
        )
    )


def file_snapshot(root: Path) -> dict[str, bytes]:
    data = root / "data"
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(data.rglob("*"))
        if path.is_file()
    }


def test_backtest_filters_future_and_over_ten_year_papers_and_caps_daily(
    tmp_path: Path, profile
) -> None:
    papers = [raw_paper(index) for index in range(1, 9)]
    future = raw_paper(20, published="2026-08-05T00:00:00Z")
    old = raw_paper(21, published="2015-01-01T00:00:00Z")
    save_batch(tmp_path, "2026-08-04", [*papers, future, old])

    result = OfflineBacktester(tmp_path, profile).run(
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 4),
        write_reports=False,
    )
    selected = result.days[0].recommendations

    assert len(selected) <= 5
    assert future.base_id not in {entry.paper.base_id for entry in selected}
    assert old.base_id not in {entry.paper.base_id for entry in selected}
    assert result.metrics["recency_distribution"][">10 years"] == 0


def test_backtest_advances_isolated_semantic_history_without_production_writes(
    tmp_path: Path, profile
) -> None:
    first = raw_paper(
        30,
        title="A Survey of Sim-to-Real Robot Learning",
        summary="A systematic survey of sim-to-real transfer using synthetic robot data for physical robot manipulation.",
    )
    second = raw_paper(
        31,
        title="A Review of Sim-to-Real Transfer for Robot Manipulation",
        summary="A review of sim-to-real and synthetic robot data for physical robots.",
    )
    save_batch(tmp_path, "2026-08-04", [first])
    save_batch(tmp_path, "2026-08-05", [second])
    before = file_snapshot(tmp_path)
    replay_profile = deepcopy(profile)
    replay_profile.recommendations["daily_mix"]["review_knowledge_map"][
        "min_recommendation_utility"
    ] = 0

    result = OfflineBacktester(tmp_path, replay_profile).run(
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 5),
        write_reports=False,
    )

    assert result.days[0].recommendations
    assert result.days[1].recommendations == []
    assert result.metrics["quantity"]["total_recommendations"] == 1
    assert file_snapshot(tmp_path) == before
    assert not (tmp_path / "site").exists()
    assert not (tmp_path / "reports").exists()


def test_backtest_actual_comparison_and_output_are_deterministic(
    tmp_path: Path, profile
) -> None:
    candidate = raw_paper(40)
    save_batch(tmp_path, "2026-08-04", [candidate])
    actual_entry = RecommendationEntry(
        category="frontier_recent",
        paper=candidate,
        reasons=["immutable fixture"],
    )
    RecommendationStorage(tmp_path / "data").save(
        DailyRecommendations(
            date="2026-08-04",
            generated_at="2026-08-04T09:30:00+08:00",
            recommendations=[actual_entry],
            candidate_count=1,
            mode="fixture",
            selection_config=profile.recommendations["daily_mix"],
        )
    )
    before = file_snapshot(tmp_path)
    backtester = OfflineBacktester(tmp_path, profile)

    first = backtester.run(
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 5),
        compare_actual=True,
        write_reports=False,
    )
    second = backtester.run(
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 5),
        compare_actual=True,
        write_reports=False,
    )

    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )
    assert first.days[0].actual is not None
    assert first.days[0].to_dict()["actual_count"] == 1
    assert first.days[1].actual is None
    assert file_snapshot(tmp_path) == before
