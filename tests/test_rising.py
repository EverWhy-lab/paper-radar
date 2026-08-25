from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.cli import _print_rising_scan
from paper_radar.history_models import HistoricalPaper
from paper_radar.models import Paper
from paper_radar.rising import rising_eligibility, score_rising_paper
from paper_radar.rising_discovery import (
    RisingDiscoveryService,
    _candidate_classification,
)
from paper_radar.rising_storage import CitationSnapshot, CitationSnapshotStorage
from paper_radar.providers.openalex import SourcePaperScan
from paper_radar.reader_models import DailyRecommendations
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import RecommendationStorage


NOW = datetime(2026, 8, 25, 10, 30)


def rising_paper(
    index: int,
    *,
    publication_date: str = "2026-05-25",
    citations: int | None = 20,
    percentile: float | None = 0.90,
    fwci: float | None = 2.5,
    source_id: str = "S144620930",
    source_name: str = "IEEE Transactions on Robotics",
    title: str = "Kinodynamic Motion Planning for Robots",
    abstract: str = "A motion planning method for robotic manipulators and mobile robots.",
    research_fit: int = 12,
    matched_topics: list[str] | None = None,
    score: float | None = 80.0,
    source_type: str = "article",
    retracted: bool = False,
) -> HistoricalPaper:
    return HistoricalPaper(
        canonical_paper_id=f"openalex:W{5000 + index}",
        openalex_id=f"W{5000 + index}",
        base_arxiv_id=None,
        doi=f"10.5555/rising{index}",
        title=title,
        authors=["Rising Fixture Author"],
        abstract=abstract,
        publication_date=publication_date,
        publication_year=int(publication_date[:4]),
        source_name=source_name,
        source_type=source_type,
        topics=[{"id": "T1", "display_name": "Robotics", "score": 0.9}],
        cited_by_count=citations,
        counts_by_year=[],
        fwci=fwci,
        citation_normalized_percentile=percentile,
        referenced_works=[],
        related_works=[],
        discovery_source=[f"rising_search:{source_name}"],
        seed_paper_id=[],
        discovered_at="2026-08-25T10:30:00+08:00",
        last_considered_at=None,
        recommended_at=None,
        recommendation_count=0,
        reading_status="unread",
        dismissed=False,
        metadata_updated_at="2026-08-25T10:30:00+08:00",
        openalex_url=f"https://openalex.org/W{5000 + index}",
        research_fit=research_fit,
        matched_topics=matched_topics or ["robot_control_optimization"],
        matched_keywords=["motion planning"],
        source_id=source_id,
        is_retracted=retracted,
        rising_score=score,
        rising_age_days=(NOW.date() - datetime.fromisoformat(publication_date).date()).days,
        rising_score_reasons=["fixture rising signal"],
    )


def score(paper: HistoricalPaper, profile, snapshots=None) -> HistoricalPaper:
    return score_rising_paper(
        paper, profile, captured_at=NOW, snapshots=snapshots or []
    )


def select(
    profile,
    *,
    rising=None,
    historical=None,
    history=None,
    recent=None,
    target_date="2026-08-25",
):
    return CuratedRecommendationEngine(profile).select(
        recent_new=recent or [],
        historical_papers=historical or [],
        rising_papers=rising or [],
        reading_pool=[],
        history=history or {},
        target_date=target_date,
        considered_at=f"{target_date}T10:30:00+08:00",
    )


def rising_history(*dates: str) -> dict[str, list[dict]]:
    return {
        f"openalex:w{8000 + index}": [
            {
                "date": day,
                "category": "rising_recent",
                "canonical_paper_id": f"openalex:W{8000 + index}",
                "title": f"Prior Rising {index}",
            }
        ]
        for index, day in enumerate(dates)
    }


def recent_paper(
    index: int,
    *,
    title: str,
    summary: str,
    topic: str,
    keyword: str,
    fit: int = 80,
) -> Paper:
    base = f"2608.{90000 + index}"
    return Paper(
        arxiv_id=f"{base}v1",
        base_id=base,
        version=1,
        title=title,
        authors=["Recent Fixture Author"],
        published="2026-08-25T01:00:00Z",
        updated="2026-08-25T01:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary=summary,
        abs_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}",
        research_fit=fit,
        video_potential=0,
        matched_topics=[topic],
        matched_keywords=[keyword],
    )


def test_citation_velocity_is_age_aware_with_a_smoothing_floor(profile) -> None:
    three_months = score(
        rising_paper(1, publication_date="2026-05-25", citations=10), profile
    )
    twelve_months = score(
        rising_paper(2, publication_date="2025-08-25", citations=10), profile
    )
    five_days = score(
        rising_paper(3, publication_date="2026-08-20", citations=10), profile
    )

    assert (
        three_months.rising_score_components["citation_velocity"]
        > twelve_months.rising_score_components["citation_velocity"]
    )
    assert five_days.rising_score_components["citation_velocity"] < 100
    assert five_days.rising_score_components["citation_velocity"] == pytest.approx(
        three_months.rising_score_components["citation_velocity"], abs=2
    )


def test_normalized_percentile_and_fwci_raise_rising_score(profile) -> None:
    low_percentile = score(rising_paper(4, percentile=0.50), profile)
    high_percentile = score(rising_paper(5, percentile=0.98), profile)
    low_fwci = score(rising_paper(6, fwci=1.0), profile)
    high_fwci = score(rising_paper(7, fwci=5.0), profile)

    assert high_percentile.rising_score > low_percentile.rising_score
    assert high_fwci.rising_score > low_fwci.rising_score


def test_missing_fwci_uses_available_component_normalization(profile) -> None:
    missing = score(rising_paper(8, fwci=None), profile)
    explicit_zero = score(rising_paper(9, fwci=0.0), profile)

    assert missing.rising_score_components["fwci"] is None
    assert missing.rising_score > explicit_zero.rising_score


def test_valid_snapshot_growth_raises_score_but_short_interval_is_ignored(profile) -> None:
    current = rising_paper(10, citations=20)
    prior_week = CitationSnapshot.from_paper(
        rising_paper(10, citations=10), captured_at="2026-08-18T10:30:00"
    )
    prior_day = CitationSnapshot.from_paper(
        rising_paper(10, citations=0), captured_at="2026-08-24T10:30:00"
    )

    cold = score(deepcopy(current), profile)
    observed = score(deepcopy(current), profile, [prior_week])
    too_short = score(deepcopy(current), profile, [prior_day])

    assert observed.rising_score_components["observed_growth"] is not None
    assert observed.rising_score > cold.rising_score
    assert observed.rising_observed_growth["recent"]["citation_delta"] == 10
    assert too_short.rising_score_components["observed_growth"] is None
    assert too_short.rising_observed_growth == {}


def test_rising_eligibility_allows_strong_planning_without_core_topic(profile) -> None:
    paper = rising_paper(11, matched_topics=["robot_control_optimization"], score=90)

    eligible, _ = rising_eligibility(paper, profile, as_of=NOW.date())
    result = select(profile, rising=[paper])

    assert eligible
    assert [entry.category for entry in result.recommendations] == ["rising_recent"]
    assert result.recommendations[0].core_topics == []


def test_high_signal_ral_vla_can_enter_rising(profile) -> None:
    paper = rising_paper(
        12,
        source_id="S4210169774",
        source_name="IEEE Robotics and Automation Letters",
        title="Vision-Language-Action Policy for Humanoid Robots",
        abstract="A vision-language-action robot policy for humanoid manipulation.",
        research_fit=60,
        matched_topics=["vla_robot_foundation", "humanoid_loco_manipulation"],
        score=88,
    )

    result = select(profile, rising=[paper])

    assert [entry.category for entry in result.recommendations] == ["rising_recent"]


@pytest.mark.parametrize(
    ("paper", "reason"),
    [
        (rising_paper(13, source_id="S51360982"), "source"),
        (
            rising_paper(
                14,
                title="A Survey of Motion Planning for Robots",
                abstract="A survey and taxonomy of robotic motion planning.",
            ),
            "survey",
        ),
        (rising_paper(15, publication_date="2024-12-01"), "age"),
        (rising_paper(16, retracted=True), "retracted"),
    ],
)
def test_rising_rejects_wrong_source_survey_old_and_retracted(
    profile, paper, reason
) -> None:
    eligible, reasons = rising_eligibility(paper, profile, as_of=NOW.date())

    assert not eligible
    assert reason in " ".join(reasons).casefold()
    assert select(profile, rising=[paper]).recommendations == []


def test_journal_lane_wins_when_same_alias_is_also_rising(profile) -> None:
    journal = rising_paper(
        17,
        publication_date="2026-07-25",
        title="Vision-Language-Action Humanoid Robot Foundation Model",
        abstract=(
            "A vision-language-action robot foundation model for humanoid robot "
            "whole-body manipulation and robot learning."
        ),
        research_fit=80,
        matched_topics=["vla_robot_foundation", "humanoid_loco_manipulation"],
        score=90,
    )
    journal.discovery_source = [f"journal_search:{journal.source_name}"]
    rising = deepcopy(journal)
    rising.discovery_source = [f"rising_search:{rising.source_name}"]

    result = select(profile, historical=[journal], rising=[rising])
    matches = [
        entry
        for entry in result.recommendations
        if entry.canonical_paper_id == journal.canonical_paper_id
    ]

    assert len(matches) == 1
    assert matches[0].category == "journal_recent"


def test_rising_lane_has_no_quota_and_never_lowers_threshold(profile) -> None:
    below = rising_paper(18, score=54.9)

    result = select(profile, rising=[below])

    assert result.recommendations == []


def test_rising_audit_requires_non_generic_keyword_for_core_evidence(profile) -> None:
    auv = rising_paper(
        20,
        title=(
            "Multi-Agent Generative Adversarial Interactive Self-Imitation "
            "Learning for AUV Formation Control and Obstacle Avoidance"
        ),
        abstract="Formation control and obstacle avoidance for underwater robots.",
        matched_topics=["robot_learning_posttraining"],
    )
    auv.matched_keywords = ["imitation learning"]

    audit = _candidate_classification(auv, profile)

    assert audit["raw_core_topics"] == ["robot_learning_posttraining"]
    assert audit["matched_core_topics"] == []
    assert audit["generic_only_core_topics"] == ["robot_learning_posttraining"]
    assert audit["matched_strong_keywords"] == []
    assert audit["matched_generic_keywords"] == ["imitation learning"]
    assert audit["classification"] == "outside_current_core"


def test_rising_audit_classifies_genuine_core_support_and_outside(profile) -> None:
    core = rising_paper(
        21,
        title="Vision-Language-Action Policy for Humanoid Robots",
        abstract="A vision-language-action policy for humanoid manipulation.",
        matched_topics=["vla_robot_foundation", "humanoid_loco_manipulation"],
    )
    core.matched_keywords = ["vision-language-action", "humanoid robot"]
    support = rising_paper(
        22, matched_topics=["robot_control_optimization"]
    )
    support.matched_keywords = ["motion planning"]
    outside = rising_paper(23)
    outside.matched_topics = []
    outside.matched_keywords = []

    core_audit = _candidate_classification(core, profile)
    support_audit = _candidate_classification(support, profile)
    outside_audit = _candidate_classification(outside, profile)

    assert core_audit["classification"] == "core"
    assert core_audit["matched_core_topics"] == [
        "vla_robot_foundation",
        "humanoid_loco_manipulation",
    ]
    assert support_audit["classification"] == "model_based_support_only"
    assert support_audit["matched_support_topics"] == [
        "robot_control_optimization"
    ]
    assert outside_audit["classification"] == "outside_current_core"


def test_strong_outside_core_robotics_paper_can_compete(profile) -> None:
    outside = rising_paper(
        24,
        title="Multi-Robot Formation Coordination for Underwater Robots",
        abstract="A robot coordination method for underwater formation missions.",
        research_fit=60,
        score=88,
    )
    outside.matched_topics = []
    outside.matched_keywords = []

    eligible, _ = rising_eligibility(outside, profile, as_of=NOW.date())
    audit = _candidate_classification(outside, profile)
    result = select(profile, rising=[outside])

    assert eligible
    assert audit["classification"] == "outside_current_core"
    assert [entry.category for entry in result.recommendations] == ["rising_recent"]


def test_rising_rolling_cap_allows_first_and_second_selection(profile) -> None:
    paper = rising_paper(30, score=80)

    first = select(profile, rising=[deepcopy(paper)])
    second = select(
        profile,
        rising=[deepcopy(paper)],
        history=rising_history("2026-08-24"),
    )

    assert [entry.category for entry in first.recommendations] == ["rising_recent"]
    assert [entry.category for entry in second.recommendations] == ["rising_recent"]


def test_rising_rolling_cap_blocks_third_selection_in_seven_days(profile) -> None:
    result = select(
        profile,
        rising=[rising_paper(31, score=90)],
        history=rising_history("2026-08-20", "2026-08-24"),
    )

    assert result.recommendations == []


def test_rising_exactly_outside_seven_day_window_is_eligible(profile) -> None:
    result = select(
        profile,
        rising=[rising_paper(32, score=90)],
        history=rising_history("2026-08-18", "2026-08-17"),
    )

    assert [entry.category for entry in result.recommendations] == ["rising_recent"]


def test_rising_lane_keeps_daily_total_at_five(profile) -> None:
    frontier = [
        recent_paper(
            1,
            title="Vision-Language-Action Robot Foundation Model",
            summary="A vision-language-action policy for robotic manipulation.",
            topic="vla_robot_foundation",
            keyword="vision-language-action",
        ),
        recent_paper(
            2,
            title="Dexterous Manipulation with Tactile Sensing",
            summary="A tactile manipulation method for a dexterous robot hand.",
            topic="dexterous_multimodal_manipulation",
            keyword="dexterous manipulation",
        ),
        recent_paper(
            3,
            title="Kinodynamic Motion Planning for Robots",
            summary="A sampling-based motion planning method for robotic manipulators.",
            topic="robot_control_optimization",
            keyword="motion planning",
            fit=40,
        ),
    ]
    journal = rising_paper(
        33,
        publication_date="2026-08-20",
        title="Vision-Language-Action Robot Data Scaling",
        abstract=(
            "A vision-language-action robot foundation model using large-scale robot "
            "data and cross-embodiment policies."
        ),
        research_fit=80,
        matched_topics=["vla_robot_foundation", "robot_data_scaling_sim2real"],
    )
    journal.discovery_source = [f"journal_search:{journal.source_name}"]
    outside = rising_paper(
        34,
        title="Multi-Robot Formation Control for Underwater Robots",
        abstract="A control system for underwater robots with obstacle avoidance.",
        matched_topics=["robot_control_optimization"],
        score=90,
    )
    outside.matched_keywords = []

    result = select(
        profile,
        recent=frontier,
        historical=[journal],
        rising=[outside],
    )

    assert len(result.recommendations) == 5
    assert sum(entry.category == "rising_recent" for entry in result.recommendations) <= 1


def test_ten_day_rising_simulation_never_exceeds_two_per_rolling_week(
    profile,
) -> None:
    papers = [rising_paper(200 + index, score=80 - index) for index in range(7)]
    history: dict[str, list[dict]] = {}
    start = datetime(2026, 8, 16)
    selected_dates: list[str] = []

    for offset in range(10):
        target_date = (start + timedelta(days=offset)).date().isoformat()
        result = select(
            profile,
            rising=[deepcopy(paper) for paper in papers],
            history=history,
            target_date=target_date,
        )
        for entry in result.recommendations:
            assert entry.category == "rising_recent"
            selected_dates.append(target_date)
            history.setdefault(entry.canonical_paper_id.casefold(), []).append(
                {
                    "date": target_date,
                    "category": entry.category,
                    "canonical_paper_id": entry.canonical_paper_id,
                    "title": entry.paper.title,
                }
            )

    assert selected_dates == [
        "2026-08-16",
        "2026-08-17",
        "2026-08-23",
        "2026-08-24",
    ]
    selected = {datetime.fromisoformat(day).date() for day in selected_dates}
    for offset in range(4):
        window_start = (start + timedelta(days=offset)).date()
        window_end = window_start + timedelta(days=6)
        assert sum(window_start <= day <= window_end for day in selected) <= 2


def test_rising_card_uses_muted_label_and_folded_diagnostics(
    tmp_path: Path, profile
) -> None:
    paper = rising_paper(19, score=82)
    paper.rising_citation_velocity = 3.25
    paper.rising_observed_growth = {"blended_citations_per_day": 0.4}
    recommendation = select(profile, rising=[paper]).recommendations[0]
    storage = RecommendationStorage(tmp_path / "data")
    storage.save(
        DailyRecommendations(
            date="2026-08-25",
            generated_at="2026-08-25T10:30:00+08:00",
            recommendations=[recommendation],
            candidate_count=0,
            mode="fixture",
            selection_config=profile.recommendations["daily_mix"],
        )
    )

    index, _ = RecommendationSiteRenderer(
        tmp_path / "site", storage, profile
    ).render("2026-08-25")
    html = index.read_text(encoding="utf-8")

    assert "近期升温" in html
    assert "Rising score" in html
    assert "Citation percentile" in html
    assert "Observed growth" in html
    assert "HOT" not in html


class FixtureRisingProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.saved = False

    @property
    def run_request_count(self) -> int:
        return self.calls

    @property
    def run_cache_hits(self) -> int:
        return 0

    def scan_source_papers(
        self,
        source_id,
        *,
        limit,
        from_date,
        to_date,
        discovery_source,
    ):
        self.calls += 1
        source_names = {
            "S144620930": "IEEE Transactions on Robotics",
            "S73484101": "The International Journal of Robotics Research",
            "S4210169774": "IEEE Robotics and Automation Letters",
        }
        paper = rising_paper(
            self.calls + 100,
            source_id=source_id,
            source_name=source_names[source_id],
            score=None,
        )
        paper.discovery_source = [discovery_source]
        return SourcePaperScan([paper], 1, False, 1)

    def save_stats(self) -> None:
        self.saved = True


class AuditRisingProvider(FixtureRisingProvider):
    def scan_source_papers(
        self,
        source_id,
        *,
        limit,
        from_date,
        to_date,
        discovery_source,
    ):
        self.calls += 1
        source_names = {
            "S144620930": "IEEE Transactions on Robotics",
            "S73484101": "The International Journal of Robotics Research",
            "S4210169774": "IEEE Robotics and Automation Letters",
        }
        details = {
            "S144620930": (
                "Interactive Self-Imitation Learning for AUV Formation Control",
                "Imitation learning for formation control of underwater robots.",
            ),
            "S73484101": (
                "Vision-Language-Action Policy for Humanoid Robots",
                "A vision-language-action policy for humanoid manipulation.",
            ),
            "S4210169774": (
                "Kinodynamic Motion Planning for Robots",
                "A motion planning method for robotic manipulators.",
            ),
        }
        title, abstract = details[source_id]
        source_offset = {
            "S144620930": 0,
            "S73484101": 100,
            "S4210169774": 200,
        }[source_id]
        papers = [
            rising_paper(
                300 + source_offset + index,
                publication_date=("2026-05-20" if index == 0 else "2026-05-25"),
                source_id=source_id,
                source_name=source_names[source_id],
                title=f"{title} {index}",
                abstract=abstract,
                score=None,
            )
            for index in range(11)
        ]
        for paper in papers:
            paper.discovery_source = [discovery_source]
        return SourcePaperScan(papers, 11, False, 1)


class RecentRisingProvider(FixtureRisingProvider):
    def scan_source_papers(
        self,
        source_id,
        *,
        limit,
        from_date,
        to_date,
        discovery_source,
    ):
        scan = super().scan_source_papers(
            source_id,
            limit=limit,
            from_date=from_date,
            to_date=to_date,
            discovery_source=discovery_source,
        )
        scan.papers[0].publication_date = "2026-08-20"
        scan.papers[0].publication_year = 2026
        return scan


def test_mocked_rising_dry_run_scans_but_writes_nothing(
    tmp_path: Path, profile
) -> None:
    provider = FixtureRisingProvider()
    data_dir = tmp_path / "data"

    result = RisingDiscoveryService(
        data_dir, profile, provider, now=NOW
    ).scan(dry_run=True)

    assert result.scanned_count == 3
    assert result.request_count == 3
    assert provider.saved is False
    assert not data_dir.exists()


def test_recent_date_audit_is_source_agnostic_for_tro_ijrr_and_ral(
    tmp_path: Path, profile
) -> None:
    result = RisingDiscoveryService(
        tmp_path / "data", profile, RecentRisingProvider(), now=NOW
    ).scan(dry_run=True)

    assert [source["recent_60_scanned"] for source in result.per_source] == [1, 1, 1]
    assert [
        source["newest_examples"][0]["publication_date"]
        for source in result.per_source
    ] == ["2026-08-20", "2026-08-20", "2026-08-20"]


def test_top_30_audit_counts_are_exclusive_and_preserve_source_examples(
    tmp_path: Path, profile
) -> None:
    result = RisingDiscoveryService(
        tmp_path / "data", profile, AuditRisingProvider(), now=NOW
    ).scan(dry_run=True)

    assert result.candidate_count == 33
    assert len(result.top_diagnostics) == 30
    assert set(paper["classification"] for paper in result.top_diagnostics) == {
        "core",
        "model_based_support_only",
        "outside_current_core",
    }
    assert sum(
        result.information_bubble[key]
        for key in (
            "core",
            "model_based_support_only",
            "outside_current_core",
        )
    ) == result.information_bubble["sample_size"] == 30
    tro_examples = result.per_source[0]["newest_examples"]
    assert tro_examples[0] == {
        "openalex_id": "W5300",
        "title": "Interactive Self-Imitation Learning for AUV Formation Control 0",
        "publication_date": "2026-05-20",
        "publication_year": 2026,
        "source": "IEEE Transactions on Robotics",
        "doi": "10.5555/rising300",
    }
    assert tro_examples[1]["publication_date"] == "2026-05-25"


def test_top_30_cli_audit_is_json_lines(tmp_path: Path, profile, capsys) -> None:
    result = RisingDiscoveryService(
        tmp_path / "data", profile, AuditRisingProvider(), now=NOW
    ).scan(dry_run=True)

    _print_rising_scan(result, profile.rising_discovery["min_rising_score"])

    lines = capsys.readouterr().out.splitlines()
    start = lines.index(
        "Top 30 Rising signal audit (JSON Lines; attention, not quality):"
    )
    end = lines.index(
        "Information-bubble check (top basic-filtered Rising signals):"
    )
    diagnostics = [json.loads(line) for line in lines[start + 1 : end - 1]]
    assert len(diagnostics) == 30
    assert diagnostics[0]["rank"] == 1
    assert diagnostics[-1]["rank"] == 30
    assert all(
        paper["classification"]
        in {"core", "model_based_support_only", "outside_current_core"}
        for paper in diagnostics
    )


def test_snapshot_store_is_compact_and_keeps_weekly_history(
    tmp_path: Path
) -> None:
    storage = CitationSnapshotStorage(tmp_path / "data")
    paper = rising_paper(120, citations=10)
    first = CitationSnapshot.from_paper(
        paper, captured_at="2026-08-18T10:30:00+08:00"
    )
    paper.cited_by_count = 15
    second = CitationSnapshot.from_paper(
        paper, captured_at="2026-08-25T10:30:00+08:00"
    )

    storage.save(storage.merged([first], max_per_paper=60))
    storage.save(storage.merged([second], max_per_paper=60))
    restored = storage.load()[paper.canonical_paper_id.casefold()]

    assert [snapshot.cited_by_count for snapshot in restored] == [10, 15]
    assert not hasattr(restored[0], "abstract")
    assert not hasattr(restored[0], "authors")


def test_old_historical_json_loads_with_safe_rising_defaults() -> None:
    value = rising_paper(121).to_dict()
    for key in (
        "source_id",
        "is_retracted",
        "rising_score",
        "rising_score_components",
        "rising_score_reasons",
        "rising_updated_at",
        "rising_age_days",
        "rising_citation_velocity",
        "rising_observed_growth",
    ):
        value.pop(key)

    restored = HistoricalPaper.from_dict(value)

    assert restored.rising_score is None
    assert restored.rising_score_components == {}
    assert restored.rising_score_reasons == []
    assert restored.rising_observed_growth == {}
    assert restored.is_retracted is False
