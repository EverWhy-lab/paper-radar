from __future__ import annotations

from copy import deepcopy

from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.models import Paper
from paper_radar.recommendation_utility import assess_recommendation_utility


def paper(
    index: int,
    *,
    title: str,
    summary: str,
    topics: list[str],
    keywords: list[str],
    research_fit: int = 70,
) -> Paper:
    base_id = f"2608.{index:05d}"
    return Paper(
        arxiv_id=f"{base_id}v1",
        base_id=base_id,
        version=1,
        title=title,
        authors=["Fixture Author"],
        published="2026-08-01T00:00:00Z",
        updated="2026-08-01T00:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary=summary,
        abs_url=f"https://arxiv.org/abs/{base_id}",
        pdf_url=f"https://arxiv.org/pdf/{base_id}",
        research_fit=research_fit,
        video_potential=50,
        matched_topics=topics,
        matched_keywords=keywords,
    )


def event(
    *,
    day: str,
    title: str,
    subtopics: list[str],
    document_type: str = "method",
    core_topics: list[str] | None = None,
) -> dict:
    return {
        "date": day,
        "canonical_paper_id": f"arxiv:event-{day}-{title}",
        "title": title,
        "summary": "",
        "subtopics": subtopics,
        "document_type": document_type,
        "core_topics": core_topics or [],
    }


def utility(candidate: Paper, profile, history: dict, day: str = "2026-08-10"):
    return assess_recommendation_utility(
        candidate,
        base_score=float(candidate.research_fit),
        history=history,
        target_date=day,
        profile=profile,
    )


def test_different_papers_same_subtopic_receive_cross_day_penalty(profile) -> None:
    candidate = paper(
        1,
        title="A New Sim-to-Real Method for Robot Manipulation",
        summary="A physical robot sim-to-real transfer method.",
        topics=["robot_data_scaling_sim2real"],
        keywords=["sim-to-real", "robot data"],
    )
    history = {
        "arxiv:old": [
            event(day="2026-08-09", title="Earlier Sim-to-Real Method", subtopics=["sim_to_real"])
        ]
    }

    assessed = utility(candidate, profile, history)

    assert assessed.redundancy_penalty == -8
    assert assessed.days_since_same_subtopic == 1
    assert any("Subtopic: sim_to_real" in reason for reason in assessed.reasons)


def test_same_core_topic_different_subtopic_is_not_semantically_cooled(profile) -> None:
    candidate = paper(
        2,
        title="Large-Scale Robot Data Engine for Manipulation",
        summary="A physical robot data scaling system.",
        topics=["robot_data_scaling_sim2real"],
        keywords=["robot data scaling"],
    )
    history = {
        "arxiv:old": [
            event(
                day="2026-08-09",
                title="Earlier Sim-to-Real Method",
                subtopics=["sim_to_real"],
                core_topics=["robot_data_scaling_sim2real"],
            )
        ]
    }

    assert utility(candidate, profile, history).redundancy_penalty == 0


def test_survey_repetition_is_penalized_more_than_fast_frontier(profile) -> None:
    survey = paper(
        3,
        title="A Survey of Sim-to-Real Robot Learning",
        summary="A systematic survey of sim-to-real for physical robots.",
        topics=["robot_data_scaling_sim2real"],
        keywords=["sim-to-real"],
    )
    frontier = deepcopy(survey)
    frontier.title = "Fast Sim-to-Real Robot Learning"
    frontier.summary = "A physical robot sim-to-real method."
    history = {
        "arxiv:old": [
            event(
                day="2026-08-09",
                title="Earlier Sim-to-Real Survey",
                subtopics=["sim_to_real"],
                document_type="survey",
            )
        ]
    }

    assert utility(survey, profile, history).redundancy_penalty == -18
    assert utility(frontier, profile, history).redundancy_penalty == -8


def test_generic_same_core_surveys_use_fallback_family(profile) -> None:
    candidate = paper(
        30,
        title="A Review of Vision-Language-Action Systems for Embodied Robots",
        summary="A broad review of vision-language-action research in robotics.",
        topics=["vla_robot_foundation"],
        keywords=["vision-language-action"],
    )
    history = {
        "arxiv:earlier-survey": [
            event(
                day="2026-08-09",
                title="A Survey of General Vision-Language-Action Robotics",
                subtopics=[],
                document_type="survey",
                core_topics=["vla_robot_foundation"],
            )
        ]
    }

    assessed = utility(candidate, profile, history)

    assert assessed.redundancy_penalty == -18
    assert assessed.semantic_suppressed is True
    assert any(
        "Survey family: survey:vla_robot_foundation" in reason
        for reason in assessed.reasons
    )


def test_surveys_with_distinct_secondary_themes_keep_bypass(profile) -> None:
    candidate = paper(
        31,
        title="Robot Data Scaling for Vision-Language-Action: A Survey",
        summary=(
            "A survey synthesizing large-scale robot data mixtures for "
            "vision-language-action policies."
        ),
        topics=["vla_robot_foundation", "robot_data_scaling_sim2real"],
        keywords=["vision-language-action", "robot data scaling"],
    )
    history = {
        "arxiv:posttraining-survey": [
            event(
                day="2026-08-09",
                title="A Survey of VLA Post-Training",
                subtopics=["vla_posttraining"],
                document_type="survey",
                core_topics=["vla_robot_foundation", "robot_learning_posttraining"],
            )
        ]
    }

    assessed = utility(candidate, profile, history)

    assert assessed.subtopics == ["robot_data_scaling"]
    assert assessed.redundancy_penalty == 0
    assert assessed.semantic_suppressed is False


def test_fast_frontier_subtopic_uses_short_decaying_cooldown(profile) -> None:
    candidate = paper(
        4,
        title="Robot World Model for Physical Manipulation",
        summary="An embodied world model for robot manipulation.",
        topics=["world_model_embodied_reasoning"],
        keywords=["robot world model"],
    )

    recent = {"arxiv:old": [event(day="2026-08-07", title="Old", subtopics=["robot_world_model"])]}
    extended = {"arxiv:old": [event(day="2026-08-05", title="Old", subtopics=["robot_world_model"])]}
    expired = {"arxiv:old": [event(day="2026-08-02", title="Old", subtopics=["robot_world_model"])]}

    assert utility(candidate, profile, recent).redundancy_penalty == -8
    assert utility(candidate, profile, recent).semantic_suppressed is True
    assert utility(candidate, profile, extended).redundancy_penalty == -12
    assert utility(candidate, profile, extended).semantic_suppressed is False
    assert utility(candidate, profile, expired).redundancy_penalty == 0


def test_new_secondary_subtopic_reduces_suppression(profile) -> None:
    candidate = paper(
        5,
        title="Scaling Robot Data for Sim-to-Real Manipulation",
        summary="A robot data scaling engine for sim-to-real physical robots.",
        topics=["robot_data_scaling_sim2real"],
        keywords=["robot data scaling", "sim-to-real"],
    )
    history = {
        "arxiv:old": [
            event(day="2026-08-09", title="Old Sim-to-Real", subtopics=["sim_to_real"])
        ]
    }

    assessed = utility(candidate, profile, history)

    assert assessed.redundancy_penalty == -2.8
    assert assessed.semantic_suppressed is False
    assert any("new secondary topic = robot_data_scaling" in reason for reason in assessed.reasons)


def test_same_paper_cooldown_remains_a_hard_gate(profile) -> None:
    candidate = paper(
        6,
        title="Vision-Language-Action Robot Foundation Model",
        summary="A physical robot vision-language-action foundation model.",
        topics=["vla_robot_foundation"],
        keywords=["vision-language-action"],
    )
    history = {
        f"arxiv:{candidate.base_id}": [
            {"date": "2026-08-09", "category": "frontier_recent"}
        ]
    }

    result = CuratedRecommendationEngine(profile).select(
        recent_new=[candidate],
        historical_papers=[],
        reading_pool=[],
        history=history,
        target_date="2026-08-10",
        considered_at="2026-08-10T12:30:00+08:00",
    )

    assert result.recommendations == []


def test_semantic_history_advances_across_multiple_simulated_days(profile) -> None:
    candidate = paper(
        7,
        title="Sim-to-Real Robot Manipulation Method",
        summary="A physical robot sim-to-real transfer method.",
        topics=["robot_data_scaling_sim2real"],
        keywords=["sim-to-real"],
    )
    history: dict = {}
    first = utility(candidate, profile, history, "2026-08-08")
    history["arxiv:day-one"] = [
        event(day="2026-08-08", title=candidate.title, subtopics=first.subtopics)
    ]
    second = utility(candidate, profile, history, "2026-08-09")
    history["arxiv:day-two"] = [
        event(day="2026-08-09", title="Another Sim-to-Real", subtopics=second.subtopics)
    ]
    third = utility(candidate, profile, history, "2026-08-10")

    assert first.redundancy_penalty == 0
    assert second.redundancy_penalty == -8
    assert third.redundancy_penalty == -8
    assert third.days_since_same_subtopic == 1


def test_preferred_beats_neutral_when_scientific_scores_are_close(profile) -> None:
    preferred = paper(
        8,
        title="Humanoid Robot World Model",
        summary="A physical robot world model.",
        topics=["world_model_embodied_reasoning"],
        keywords=["robot world model"],
        research_fit=70,
    )
    neutral = paper(
        9,
        title="Robot World Model for Navigation",
        summary="A physical robot navigation world model.",
        topics=["world_model_embodied_reasoning"],
        keywords=["robot world model"],
        research_fit=72,
    )

    assert utility(preferred, profile, {}).recommendation_utility > utility(neutral, profile, {}).recommendation_utility


def test_neutral_beats_peripheral_when_scientific_scores_are_close(profile) -> None:
    neutral = paper(
        10,
        title="Robot World Model for Navigation",
        summary="A physical robot navigation world model.",
        topics=["world_model_embodied_reasoning"],
        keywords=["robot world model"],
        research_fit=70,
    )
    peripheral = paper(
        11,
        title="Surgical Robot World Model",
        summary="A surgical robot world model.",
        topics=["world_model_embodied_reasoning"],
        keywords=["robot world model"],
        research_fit=72,
    )

    assert utility(neutral, profile, {}).recommendation_utility > utility(peripheral, profile, {}).recommendation_utility


def test_strong_peripheral_breakthrough_can_outrank_weak_preferred(profile) -> None:
    peripheral = paper(
        12,
        title="Surgical Robot World-Action Model",
        summary="A general world-action model validated on a surgical robot.",
        topics=["world_model_embodied_reasoning"],
        keywords=["world action model"],
        research_fit=90,
    )
    preferred = paper(
        13,
        title="Humanoid Robot World Model",
        summary="A physical humanoid robot world model.",
        topics=["world_model_embodied_reasoning"],
        keywords=["robot world model"],
        research_fit=70,
    )

    assert utility(peripheral, profile, {}).recommendation_utility > utility(preferred, profile, {}).recommendation_utility


def test_affinity_does_not_mutate_research_fit(profile) -> None:
    candidate = paper(
        14,
        title="Humanoid Robot Foundation Model",
        summary="A physical humanoid robot foundation model.",
        topics=["vla_robot_foundation"],
        keywords=["robot foundation model"],
    )
    original = candidate.research_fit

    assessed = utility(candidate, profile, {})

    assert assessed.domain_affinity_adjustment == 6
    assert candidate.research_fit == original


def test_excluded_terms_behavior_is_unchanged(profile) -> None:
    excluded = paper(
        15,
        title="Vision-Language-Action for Autonomous Driving",
        summary="A vehicle system described as a robot foundation model.",
        topics=["vla_robot_foundation"],
        keywords=["vision-language-action"],
        research_fit=100,
    )

    result = CuratedRecommendationEngine(profile).select(
        recent_new=[excluded],
        historical_papers=[],
        reading_pool=[],
        history={},
        target_date="2026-08-10",
        considered_at="2026-08-10T12:30:00+08:00",
    )

    assert result.recommendations == []
