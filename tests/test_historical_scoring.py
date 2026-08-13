from __future__ import annotations

from copy import deepcopy

from paper_radar.historical_scoring import (
    historical_relevance_eligible,
    score_historical_paper,
)
from paper_radar.providers.openalex import parse_openalex_work


def make_historical(openalex_payload, index: int):
    return parse_openalex_work(
        deepcopy(openalex_payload["results"][index]),
        discovered_at="2026-08-03T10:15:00+08:00",
        discovery_source="fixture",
    )


def test_highly_cited_but_irrelevant_work_is_ineligible(profile, openalex_payload) -> None:
    paper = score_historical_paper(
        make_historical(openalex_payload, 2), profile, as_of_year=2026
    )
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert paper.cited_by_count == 5000
    assert eligible is False
    assert paper.research_fit == 0
    assert reasons


def test_relevant_moderate_citation_work_survives_impact_fallback(
    profile, openalex_payload
) -> None:
    paper = make_historical(openalex_payload, 4)
    paper.title = "Humanoid Whole-Body Loco-Manipulation Policy"
    paper.abstract = "A whole-body reinforcement learning policy for a humanoid robot."
    paper = score_historical_paper(paper, profile, as_of_year=2026)
    eligible, _ = historical_relevance_eligible(paper, profile, as_of_year=2026)

    assert eligible is True
    assert paper.fwci is None
    assert paper.citation_normalized_percentile is None
    assert paper.historical_value_score >= profile.recommendations["daily_mix"][
        "high_impact_historical"
    ]["min_historical_value_score"]


def test_different_years_use_age_adjusted_signal_not_raw_citations(
    profile, openalex_payload
) -> None:
    old = make_historical(openalex_payload, 1)
    newer = make_historical(openalex_payload, 1)
    old.publication_year = 2008
    old.publication_date = "2008-01-01"
    old.cited_by_count = 200
    old.citation_normalized_percentile = None
    old.fwci = None
    old.counts_by_year = []
    newer.publication_year = 2025
    newer.publication_date = "2025-01-01"
    newer.cited_by_count = 100
    newer.citation_normalized_percentile = None
    newer.fwci = None
    newer.counts_by_year = []

    score_historical_paper(old, profile, as_of_year=2026)
    score_historical_paper(newer, profile, as_of_year=2026)

    assert old.cited_by_count > newer.cited_by_count
    assert old.historical_score_components["normalized_citation"] < newer.historical_score_components["normalized_citation"]
    assert old.historical_value_score < newer.historical_value_score


def test_missing_percentile_and_fwci_use_available_components(profile, openalex_payload) -> None:
    paper = make_historical(openalex_payload, 4)
    score_historical_paper(paper, profile, as_of_year=2026)

    assert paper.historical_score_components["fwci"] is None
    assert paper.historical_score_components["normalized_citation"] is not None
    assert paper.historical_value_score is not None
    assert any("降级" in reason for reason in paper.historical_score_reasons)


def test_missing_citation_data_remains_unknown_not_zero(profile, openalex_payload) -> None:
    paper = make_historical(openalex_payload, 4)
    paper.cited_by_count = None
    paper.counts_by_year = []
    paper.fwci = None
    paper.citation_normalized_percentile = None
    score_historical_paper(paper, profile, as_of_year=2026)

    assert paper.cited_by_count is None
    assert paper.historical_score_components["normalized_citation"] is None
    assert paper.historical_score_components["citation_momentum"] is None
    assert any("未知处理" in reason for reason in paper.historical_score_reasons)


def test_high_impact_building_mpc_cannot_bypass_robotics_context_gate(
    profile, openalex_payload
) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "RL-MPC for building energy management"
    paper.abstract = (
        "Reinforcement learning and model predictive control reduce HVAC "
        "energy use in a smart grid."
    )
    paper.topics = [{"display_name": "Building energy systems"}]
    paper.cited_by_count = 50_000
    paper.fwci = 100.0
    paper.citation_normalized_percentile = 0.9999

    score_historical_paper(paper, profile, as_of_year=2026)
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert eligible is False
    assert any("机器人语境门槛" in reason for reason in reasons)
    assert any(
        reason["kind"] == "robotics_context" and reason["eligible"] is False
        for reason in paper.research_reasons
    )


def test_robot_mpc_remains_eligible(profile, openalex_payload) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "Model Predictive Control for a Humanoid Robot"
    paper.abstract = (
        "A whole-body control method coordinates humanoid robot locomotion "
        "and mobile manipulation."
    )
    paper.topics = [{"display_name": "Humanoid robotics"}]

    score_historical_paper(paper, profile, as_of_year=2026)
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert eligible is True
    assert any("机器人语境" in reason for reason in reasons)


def test_historical_topic_metadata_can_establish_robotics_context(
    profile, openalex_payload
) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "Model Predictive Control for Constrained Systems"
    paper.abstract = "A model predictive control method with stability guarantees."
    paper.topics = [{"display_name": "Humanoid robotics"}]

    score_historical_paper(paper, profile, as_of_year=2026)
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert eligible is True
    assert any("robotics" in reason.casefold() for reason in reasons)


def test_recency_component_uses_reading_age_bands(profile, openalex_payload) -> None:
    expected = {
        2026: 100.0,
        2023: 90.0,
        2020: 70.0,
        2017: 50.0,
        2015: 0.0,
    }

    for publication_year, expected_score in expected.items():
        paper = make_historical(openalex_payload, 1)
        paper.publication_year = publication_year
        paper.publication_date = f"{publication_year}-01-01"
        score_historical_paper(paper, profile, as_of_year=2026)
        assert paper.historical_score_components["recency"] == expected_score


def test_paper_over_ten_years_is_background_only_for_daily_reading(
    profile, openalex_payload
) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "Humanoid Whole-Body Loco-Manipulation"
    paper.abstract = "A humanoid robot policy for whole-body manipulation."
    paper.publication_year = 2015
    paper.publication_date = "2015-01-01"
    paper.cited_by_count = 100_000
    paper.fwci = 100.0
    paper.citation_normalized_percentile = 0.9999
    score_historical_paper(paper, profile, as_of_year=2026)

    eligible, reasons = historical_relevance_eligible(
        paper, profile, as_of_year=2026
    )

    assert paper.historical_score_components["recency"] == 0.0
    assert eligible is False
    assert any("背景谱系" in reason for reason in reasons)


def test_recent_relevant_work_can_outrank_old_citation_heavy_work(
    profile, openalex_payload
) -> None:
    recent = make_historical(openalex_payload, 1)
    old = make_historical(openalex_payload, 1)
    for paper in (recent, old):
        paper.title = "Robot World Model for Long-Horizon Manipulation"
        paper.abstract = "An embodied world model plans robot manipulation tasks."
        paper.counts_by_year = []
        paper.fwci = None
        paper.citation_normalized_percentile = None
    recent.publication_year = 2024
    recent.publication_date = "2024-01-01"
    recent.cited_by_count = 80
    old.publication_year = 2008
    old.publication_date = "2008-01-01"
    old.cited_by_count = 1_000

    score_historical_paper(recent, profile, as_of_year=2026)
    score_historical_paper(old, profile, as_of_year=2026)

    assert recent.cited_by_count < old.cited_by_count
    assert recent.historical_value_score > old.historical_value_score
