from __future__ import annotations

from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.models import Paper
from paper_radar.scoring import score_paper


def paper(
    index: int,
    *,
    title: str,
    summary: str,
    category: str = "cs.RO",
    updated: str = "2026-08-19T08:00:00Z",
    video_potential: int | None = None,
) -> Paper:
    base = f"2608.{index:05d}"
    result = Paper(
        arxiv_id=f"{base}v1",
        base_id=base,
        version=1,
        title=title,
        authors=["Synthetic Author"],
        published="2026-08-19T07:00:00Z",
        updated=updated,
        primary_category=category,
        categories=[category],
        summary=summary,
        abs_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}",
    )
    if video_potential is not None:
        result.video_potential = video_potential
    return result


def select(profile, papers: list[Paper]):
    scored = [score_paper(item, profile) for item in papers]
    return CuratedRecommendationEngine(profile).select(
        recent_new=scored,
        historical_papers=[],
        reading_pool=[],
        history={},
        target_date="2026-08-20",
        considered_at="2026-08-20T12:30:00+08:00",
    ).recommendations


def test_six_core_topics_remain_frozen_and_control_stays_support_only(profile) -> None:
    assert profile.recommendations["core_topic_ids"] == [
        "vla_robot_foundation",
        "world_model_embodied_reasoning",
        "humanoid_loco_manipulation",
        "robot_learning_posttraining",
        "dexterous_multimodal_manipulation",
        "robot_data_scaling_sim2real",
    ]
    assert "robot_control_optimization" not in profile.recommendations["core_topic_ids"]


def test_whole_body_mpc_without_ai_core_enters_model_based_lane(profile) -> None:
    candidate = paper(
        1,
        title="Whole-Body MPC for Humanoid Balance",
        summary=(
            "A model predictive control formulation coordinates a robotic system's "
            "contacts and balance under actuator constraints."
        ),
    )

    recommendations = select(profile, [candidate])

    assert candidate.matched_topics == ["robot_control_optimization"]
    assert [entry.category for entry in recommendations] == ["model_based_recent"]
    assert recommendations[0].subtopics == ["model_predictive_control"]


def test_non_robot_building_energy_mpc_never_enters(profile) -> None:
    candidate = paper(
        2,
        title="Robust MPC for Building Energy Management",
        summary="A model predictive control method reduces HVAC power-system cost.",
        category="eess.SY",
    )

    assert select(profile, [candidate]) == []


def test_kinodynamic_motion_planning_for_manipulators_enters(profile) -> None:
    candidate = paper(
        3,
        title="Ultrafast Kinodynamic Motion Planning for Manipulators",
        summary=(
            "A sampling-based motion planning algorithm constructs dynamically feasible "
            "trajectories for high-DOF manipulators."
        ),
    )

    recommendations = select(profile, [candidate])

    assert [entry.category for entry in recommendations] == ["model_based_recent"]
    assert set(recommendations[0].subtopics) == {
        "motion_planning",
        "kinodynamic_planning",
    }


def test_generic_graph_planning_and_optimization_without_robot_context_fails(profile) -> None:
    candidate = paper(
        4,
        title="Graph Planning and Generic Optimization",
        summary="A graph-search framework optimizes decisions in abstract networks.",
        category="cs.AI",
    )

    assert select(profile, [candidate]) == []


def test_model_based_lane_is_capped_not_filled(profile) -> None:
    candidates = [
        paper(
            5,
            title="Contact-Implicit Trajectory Optimization for a Robot Arm",
            summary="A contact-implicit optimization method plans robot interaction.",
        ),
        paper(
            6,
            title="Sampling-Based Motion Planning for a Robot Arm",
            summary="A motion planning method searches collision-free robot trajectories.",
        ),
    ]

    recommendations = select(profile, candidates)

    assert sum(entry.category == "model_based_recent" for entry in recommendations) == 1
    assert select(profile, []) == []


def test_video_potential_does_not_break_daily_reading_tie(profile) -> None:
    high_video = paper(
        7,
        title="Vision-Language-Action Robot Policy Alpha",
        summary="A vision-language-action method for a physical robot.",
        updated="2026-08-19T07:00:00Z",
    )
    low_video = paper(
        8,
        title="Vision-Language-Action Robot Policy Beta",
        summary="A vision-language-action method for a physical robot.",
        updated="2026-08-19T09:00:00Z",
    )
    score_paper(high_video, profile)
    score_paper(low_video, profile)
    high_video.video_potential = 100
    low_video.video_potential = 0

    recommendations = CuratedRecommendationEngine(profile).select(
        recent_new=[high_video, low_video],
        historical_papers=[],
        reading_pool=[],
        history={},
        target_date="2026-08-20",
        considered_at="2026-08-20T12:30:00+08:00",
    ).recommendations

    frontier = [entry for entry in recommendations if entry.category == "frontier_recent"]
    assert len(frontier) == 1
    assert frontier[0].paper.base_id == low_video.base_id

