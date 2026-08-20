from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from paper_radar.config import ResearchProfile
from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.history_models import HistoricalPaper
from paper_radar.history_storage import HistoricalPaperStorage
from paper_radar.reader_models import DailyRecommendations, RecommendationEntry
from paper_radar.reader_storage import (
    CandidateStorage,
    DismissalStorage,
    ReadingPoolStorage,
    RecommendationStorage,
)
from paper_radar.recommendation_utility import (
    detect_document_type,
    detect_subtopics,
    semantic_redundancy_keys,
)
from paper_radar.scoring import robotics_context_gate, score_papers
from paper_radar.storage import StorageError, atomic_write_text


class BacktestError(ValueError):
    """Raised when an offline replay cannot be completed."""


def _dates(start: date, end: date) -> list[date]:
    if end < start:
        raise BacktestError("--to must be on or after --from")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _published_on_or_before(value: str | None, target: date) -> bool:
    if not value:
        return True
    try:
        return date.fromisoformat(value[:10]) <= target
    except ValueError:
        return False


def _event(entry: RecommendationEntry, day: str, generated_at: str) -> dict[str, Any]:
    return {
        "date": day,
        "generated_at": generated_at,
        "category": entry.category,
        "version": entry.paper.version,
        "canonical_paper_id": entry.canonical_paper_id,
        "title": entry.paper.title,
        "summary": entry.paper.summary,
        "matched_topics": entry.paper.matched_topics,
        "matched_keywords": entry.paper.matched_keywords,
        "core_topics": entry.core_topics,
        "subtopics": entry.subtopics,
        "document_type": entry.document_type,
        "domain_affinity": entry.domain_affinity,
        "domain_affinity_adjustment": entry.domain_affinity_adjustment,
        "redundancy_penalty": entry.redundancy_penalty,
        "recommendation_utility": entry.recommendation_utility,
    }


def _append_history(
    history: dict[str, list[dict[str, Any]]],
    entries: list[RecommendationEntry],
    *,
    day: str,
    generated_at: str,
) -> None:
    for entry in entries:
        event = _event(entry, day, generated_at)
        for alias in entry.aliases:
            history.setdefault(alias, []).append(dict(event))


@dataclass
class BacktestDay:
    date: str
    candidate_count: int
    candidate_batch_available: bool
    historical_candidate_count: int
    recommendations: list[RecommendationEntry]
    actual: DailyRecommendations | None = None

    def to_dict(self) -> dict[str, Any]:
        replay_ids = {entry.canonical_paper_id for entry in self.recommendations}
        actual_entries = self.actual.recommendations if self.actual else []
        actual_ids = {entry.canonical_paper_id for entry in actual_entries}
        return {
            "date": self.date,
            "candidate_count": self.candidate_count,
            "candidate_batch_available": self.candidate_batch_available,
            "historical_candidate_count": self.historical_candidate_count,
            "replay_count": len(self.recommendations),
            "replay": [entry.to_dict() for entry in self.recommendations],
            "actual_available": self.actual is not None,
            "actual_count": len(actual_entries) if self.actual else None,
            "actual_titles": [entry.paper.title for entry in actual_entries],
            "replay_titles": [entry.paper.title for entry in self.recommendations],
            "overlap_count": len(replay_ids & actual_ids) if self.actual else None,
            "overlap_ids": sorted(replay_ids & actual_ids) if self.actual else [],
        }


@dataclass
class BacktestResult:
    from_date: str
    to_date: str
    compare_actual: bool
    days: list[BacktestDay]
    metrics: dict[str, Any]
    configuration_snapshot: dict[str, Any]
    warnings: list[str]
    markdown_path: Path | None = None
    json_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "backtest_type": "offline_recommendation_policy_replay",
            "from": self.from_date,
            "to": self.to_date,
            "compare_actual": self.compare_actual,
            "configuration_snapshot": self.configuration_snapshot,
            "metrics": self.metrics,
            "days": [day.to_dict() for day in self.days],
            "warnings": self.warnings,
        }


def _primary_topic(entry: RecommendationEntry, profile: ResearchProfile) -> str:
    weights = {
        str(topic["id"]): float(topic["weight"])
        for topic in profile.scoring["topics"]
    }
    return max(entry.core_topics, key=lambda topic: weights.get(topic, 0), default="unclassified")


def _semantic_metrics(
    records: list[tuple[date, RecommendationEntry]], profile: ResearchProfile
) -> dict[str, Any]:
    seen_papers: set[str] = set()
    repeated_papers = 0
    previous_subtopics: list[tuple[date, set[str], str]] = []
    repeats_3 = 0
    repeats_7 = 0
    survey_repeats = 0
    survey_repeats_14 = 0
    same_subtopic_gaps: list[int] = []

    for day, entry in records:
        if entry.canonical_paper_id in seen_papers:
            repeated_papers += 1
        seen_papers.add(entry.canonical_paper_id)
        scored_paper = score_papers([copy.deepcopy(entry.paper)], profile)[0]
        detected_subtopics = set(
            entry.subtopics or detect_subtopics(scored_paper, profile)
        )
        document_type = (
            entry.document_type
            if entry.subtopics or entry.document_type != "method"
            else detect_document_type(scored_paper, profile)
        )
        current_subtopics = semantic_redundancy_keys(
            subtopics=detected_subtopics,
            document_type=document_type,
            matched_topics=scored_paper.matched_topics,
            profile=profile,
        )
        matching_gaps = [
            (day - previous_day).days
            for previous_day, previous, _ in previous_subtopics
            if (day - previous_day).days > 0 and current_subtopics & previous
        ]
        if matching_gaps:
            gap = min(matching_gaps)
            same_subtopic_gaps.append(gap)
            repeats_3 += int(gap <= 3)
            repeats_7 += int(gap <= 7)
            if document_type == "survey" and any(
                0 < (day - previous_day).days <= 30
                and current_subtopics & previous
                and previous_type == "survey"
                for previous_day, previous, previous_type in previous_subtopics
            ):
                survey_repeats += 1
            if document_type == "survey" and any(
                0 < (day - previous_day).days <= 14
                and current_subtopics & previous
                and previous_type == "survey"
                for previous_day, previous, previous_type in previous_subtopics
            ):
                survey_repeats_14 += 1
        if current_subtopics:
            previous_subtopics.append((day, current_subtopics, document_type))

    total = len(records)

    def rate(value: int) -> float:
        return round(value / total, 4) if total else 0.0

    return {
        "same_paper_repeats": repeated_papers,
        "same_paper_repeat_rate": rate(repeated_papers),
        "same_subtopic_repeat_within_3_days": repeats_3,
        "same_subtopic_repeat_within_3_days_rate": rate(repeats_3),
        "same_subtopic_repeat_within_7_days": repeats_7,
        "same_subtopic_repeat_within_7_days_rate": rate(repeats_7),
        "survey_topic_repeats_within_30_days": survey_repeats,
        "survey_topic_repeats_within_14_days": survey_repeats_14,
        "survey_topic_repeat_rate": rate(survey_repeats),
        "average_days_since_last_same_subtopic": (
            round(mean(same_subtopic_gaps), 2) if same_subtopic_gaps else None
        ),
    }


def _metrics(days: list[BacktestDay], profile: ResearchProfile) -> dict[str, Any]:
    records = [
        (date.fromisoformat(day.date), entry)
        for day in days
        for entry in day.recommendations
    ]
    total = len(records)
    recency = Counter({"≤2 years": 0, "3–5 years": 0, "6–10 years": 0, ">10 years": 0})
    topic_counts = Counter({topic: 0 for topic in profile.recommendations["core_topic_ids"]})
    primary_counts = Counter({topic: 0 for topic in profile.recommendations["core_topic_ids"]})
    affinity = Counter({"preferred": 0, "neutral": 0, "peripheral": 0})
    control_only = 0
    control_with_core = 0
    model_based_recent = 0
    method_only = 0
    method_with_core = 0
    planning = 0
    mpc_wbc = 0
    safety_control = 0
    state_estimation = 0
    generic_control_false_positives = 0
    multi_core = 0
    generic_false_positives = 0
    research_fits: list[float] = []
    utilities: list[float] = []
    configured_method_subtopics = set(
        profile.recommendations["daily_mix"]["model_based_recent"][
            "method_subtopics"
        ]
    )

    for day, entry in records:
        publication = entry.paper.published[:4]
        if publication.isdigit():
            age = day.year - int(publication)
            if age <= 2:
                recency["≤2 years"] += 1
            elif age <= 5:
                recency["3–5 years"] += 1
            elif age <= 10:
                recency["6–10 years"] += 1
            else:
                recency[">10 years"] += 1

        for topic in set(entry.core_topics):
            topic_counts[topic] += 1
        primary = _primary_topic(entry, profile)
        if primary != "unclassified":
            primary_counts[primary] += 1
        canonical_topics = {
            profile.canonical_topic_id(topic) for topic in entry.paper.matched_topics
        }
        has_control = "robot_control_optimization" in canonical_topics
        has_core = bool(set(entry.core_topics))
        control_only += int(has_control and not has_core)
        control_with_core += int(has_control and has_core)
        detected_subtopics = set(entry.subtopics or detect_subtopics(entry.paper, profile))
        detected_methods = detected_subtopics & configured_method_subtopics
        has_method = bool(detected_methods)
        model_based_recent += int(entry.category == "model_based_recent")
        method_only += int(has_method and not has_core)
        method_with_core += int(has_method and has_core)
        planning += int(
            bool(detected_methods & {"motion_planning", "kinodynamic_planning"})
        )
        mpc_wbc += int(
            bool(
                detected_methods
                & {"model_predictive_control", "whole_body_control"}
            )
        )
        safety_control += int("safety_critical_control" in detected_methods)
        state_estimation += int("robot_state_estimation" in detected_methods)
        multi_core += int(len(set(entry.core_topics)) > 1)
        affinity[entry.domain_affinity] += 1
        research_fits.append(float(entry.paper.research_fit))
        utilities.append(float(entry.recommendation_utility))
        robotics_eligible = robotics_context_gate(
            entry.paper.title, entry.paper.summary, "", profile
        ).eligible
        if not robotics_eligible:
            generic_false_positives += 1
            generic_control_false_positives += int(has_method)

    distinct_core_per_day = [
        len({topic for entry in day.recommendations for topic in entry.core_topics})
        for day in days
    ]
    actual_days = [day for day in days if day.actual is not None]
    candidate_days = [day for day in days if day.candidate_batch_available]
    actual_total = sum(len(day.actual.recommendations) for day in actual_days if day.actual)
    actual_overlap = sum(
        len(
            {entry.canonical_paper_id for entry in day.recommendations}
            & {entry.canonical_paper_id for entry in day.actual.recommendations}
        )
        for day in actual_days
        if day.actual
    )
    actual_records = [
        (date.fromisoformat(day.date), entry)
        for day in actual_days
        for entry in (day.actual.recommendations if day.actual else [])
    ]
    comparable_replay_records = [
        (date.fromisoformat(day.date), entry)
        for day in actual_days
        for entry in day.recommendations
    ]

    def rate(value: int, denominator: int = total) -> float:
        return round(value / denominator, 4) if denominator else 0.0

    return {
        "quantity": {
            "simulated_days": len(days),
            "total_recommendations": total,
            "average_recommendations_per_day": round(total / len(days), 2) if days else 0,
            "empty_days": sum(not day.recommendations for day in days),
            "candidate_batch_days_available": len(candidate_days),
            "candidate_batch_from": candidate_days[0].date if candidate_days else None,
            "candidate_batch_to": candidate_days[-1].date if candidate_days else None,
        },
        "recency_distribution": dict(recency),
        "core_topic_distribution": {
            topic: {"count": topic_counts[topic], "share": rate(topic_counts[topic])}
            for topic in profile.recommendations["core_topic_ids"]
        },
        "primary_topic_distribution": dict(primary_counts),
        "traditional_control": {
            "control_only": control_only,
            "control_with_core_topic": control_with_core,
        },
        "model_based_methods": {
            "model_based_recent_recommendations": model_based_recent,
            "method_only_recommendations": method_only,
            "method_with_core_recommendations": method_with_core,
            "planning_count": planning,
            "mpc_wbc_count": mpc_wbc,
            "safety_control_count": safety_control,
            "state_estimation_count": state_estimation,
            "generic_non_robot_control_false_positives": generic_control_false_positives,
        },
        "semantic_redundancy": _semantic_metrics(records, profile),
        "affinity_distribution": dict(affinity),
        "quality_diversity": {
            "average_research_fit": round(mean(research_fits), 2) if research_fits else None,
            "average_recommendation_utility": round(mean(utilities), 2) if utilities else None,
            "average_distinct_core_topics_per_day": (
                round(mean(distinct_core_per_day), 2) if distinct_core_per_day else 0
            ),
            "multi_core_topic_papers": multi_core,
            "generic_ai_false_positives": generic_false_positives,
        },
        "actual_comparison": {
            "actual_days_available": len(actual_days),
            "actual_from": actual_days[0].date if actual_days else None,
            "actual_to": actual_days[-1].date if actual_days else None,
            "actual_recommendations": actual_total,
            "overlap_recommendations": actual_overlap,
            "overlap_rate": rate(actual_overlap, actual_total),
            "actual_semantic_redundancy": _semantic_metrics(actual_records, profile),
            "comparable_replay_semantic_redundancy": _semantic_metrics(
                comparable_replay_records, profile
            ),
        },
    }


def _configuration_snapshot(profile: ResearchProfile) -> dict[str, Any]:
    return {
        "core_topic_ids": profile.recommendations["core_topic_ids"],
        "daily_mix": profile.recommendations["daily_mix"],
        "recommendation_subtopics": profile.recommendations["recommendation_subtopics"],
        "semantic_cooldown": profile.recommendations["semantic_cooldown"],
        "personal_domain_affinity": profile.recommendations["personal_domain_affinity"],
        "historical_max_reading_age_years": profile.historical_discovery[
            "max_reading_age_years"
        ],
    }


def _markdown(result: BacktestResult) -> str:
    metrics = result.metrics
    quantity = metrics["quantity"]
    semantic = metrics["semantic_redundancy"]
    methods = metrics["model_based_methods"]
    lines = [
        f"# Paper Radar offline backtest: {result.from_date} to {result.to_date}",
        "",
        "## Summary",
        "",
        f"- Simulated days: {quantity['simulated_days']}",
        f"- Total recommendations: {quantity['total_recommendations']}",
        f"- Average recommendations/day: {quantity['average_recommendations_per_day']}",
        f"- Empty days: {quantity['empty_days']}",
        f"- Local candidate batches: {quantity['candidate_batch_days_available']} days ({quantity['candidate_batch_from']} to {quantity['candidate_batch_to']})",
        "",
        "This is an offline recommendation-policy replay. It uses only locally persisted candidate metadata and advances an isolated simulated recommendation history one day at a time.",
        "",
        "## Configuration snapshot",
        "",
        "```json",
        json.dumps(result.configuration_snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Aggregate metrics",
        "",
        f"- Average research_fit: {metrics['quality_diversity']['average_research_fit']}",
        f"- Average recommendation utility: {metrics['quality_diversity']['average_recommendation_utility']}",
        f"- Average distinct core topics/day: {metrics['quality_diversity']['average_distinct_core_topics_per_day']}",
        f"- Multi-core-topic papers: {metrics['quality_diversity']['multi_core_topic_papers']}",
        f"- Generic-AI false positives: {metrics['quality_diversity']['generic_ai_false_positives']}",
        f"- Control-only: {metrics['traditional_control']['control_only']}",
        f"- Control + core topic: {metrics['traditional_control']['control_with_core_topic']}",
        f"- Model-based recent lane: {methods['model_based_recent_recommendations']}",
        f"- Method-only recommendations: {methods['method_only_recommendations']}",
        f"- Method + core recommendations: {methods['method_with_core_recommendations']}",
        f"- Planning / MPC-WBC / safety / state estimation: {methods['planning_count']} / {methods['mpc_wbc_count']} / {methods['safety_control_count']} / {methods['state_estimation_count']}",
        f"- Generic non-robot control false positives: {methods['generic_non_robot_control_false_positives']}",
        "",
        "## Topic distribution",
        "",
        "| Core topic | Count | Share | Primary count |",
        "|---|---:|---:|---:|",
    ]
    for topic, values in metrics["core_topic_distribution"].items():
        lines.append(
            f"| {topic} | {values['count']} | {values['share']:.1%} | "
            f"{metrics['primary_topic_distribution'].get(topic, 0)} |"
        )
    lines.extend([
        "",
        "## Recency distribution",
        "",
        "| Age | Count |",
        "|---|---:|",
    ])
    for bucket, count in metrics["recency_distribution"].items():
        lines.append(f"| {bucket} | {count} |")
    lines.extend([
        "",
        "## Semantic redundancy",
        "",
        f"- Same-paper repeat rate: {semantic['same_paper_repeat_rate']:.1%} ({semantic['same_paper_repeats']})",
        f"- Same-subtopic repeat within 3 days: {semantic['same_subtopic_repeat_within_3_days_rate']:.1%} ({semantic['same_subtopic_repeat_within_3_days']})",
        f"- Same-subtopic repeat within 7 days: {semantic['same_subtopic_repeat_within_7_days_rate']:.1%} ({semantic['same_subtopic_repeat_within_7_days']})",
        f"- Survey/topic repeat rate: {semantic['survey_topic_repeat_rate']:.1%} ({semantic['survey_topic_repeats_within_30_days']})",
        f"- Survey/topic repeats within 14 days: {semantic['survey_topic_repeats_within_14_days']}",
        f"- Average days since last same-subtopic recommendation: {semantic['average_days_since_last_same_subtopic']}",
        "",
        "## Affinity distribution",
        "",
    ])
    for affinity, count in metrics["affinity_distribution"].items():
        lines.append(f"- {affinity}: {count}")
    lines.extend(["", "## Daily replay", ""])
    for day in result.days:
        lines.append(f"### {day.date}")
        lines.append("")
        if day.recommendations:
            for entry in day.recommendations:
                subtopics = ", ".join(entry.subtopics) or "none"
                lines.append(
                    f"- **{entry.paper.title}** — {entry.category}; utility "
                    f"{entry.recommendation_utility:.1f}; subtopics: {subtopics}; "
                    f"affinity: {entry.domain_affinity} ({entry.domain_affinity_adjustment:+g}); "
                    f"redundancy: {entry.redundancy_penalty:+g}"
                )
        else:
            lines.append("- No paper cleared the configured gates.")
        if result.compare_actual:
            if day.actual is None:
                lines.append("- Actual archive: unavailable")
            else:
                actual_titles = "; ".join(entry.paper.title for entry in day.actual.recommendations) or "none"
                replay_ids = {entry.canonical_paper_id for entry in day.recommendations}
                actual_ids = {entry.canonical_paper_id for entry in day.actual.recommendations}
                lines.append(f"- Actual ({len(day.actual.recommendations)}): {actual_titles}")
                lines.append(f"- Actual/replay overlap: {len(replay_ids & actual_ids)}")
        lines.append("")
    lines.extend(["## Actual vs replay", ""])
    if result.compare_actual:
        actual_semantic = metrics["actual_comparison"]["actual_semantic_redundancy"]
        replay_semantic = metrics["actual_comparison"][
            "comparable_replay_semantic_redundancy"
        ]
        lines.extend([
            f"- Comparable actual archive range: {metrics['actual_comparison']['actual_from']} to {metrics['actual_comparison']['actual_to']} ({metrics['actual_comparison']['actual_days_available']} days)",
            f"- Comparable actual same-subtopic repeat within 3 days: {actual_semantic['same_subtopic_repeat_within_3_days_rate']:.1%} ({actual_semantic['same_subtopic_repeat_within_3_days']})",
            f"- Comparable replay same-subtopic repeat within 3 days: {replay_semantic['same_subtopic_repeat_within_3_days_rate']:.1%} ({replay_semantic['same_subtopic_repeat_within_3_days']})",
            f"- Comparable actual same-subtopic repeat within 7 days: {actual_semantic['same_subtopic_repeat_within_7_days_rate']:.1%} ({actual_semantic['same_subtopic_repeat_within_7_days']})",
            f"- Comparable replay same-subtopic repeat within 7 days: {replay_semantic['same_subtopic_repeat_within_7_days_rate']:.1%} ({replay_semantic['same_subtopic_repeat_within_7_days']})",
            "",
        ])
        lines.extend([
            "| Date | Actual | Replay | Overlap |",
            "|---|---:|---:|---:|",
        ])
        for day in result.days:
            overlap = None
            if day.actual is not None:
                overlap = len(
                    {entry.canonical_paper_id for entry in day.recommendations}
                    & {entry.canonical_paper_id for entry in day.actual.recommendations}
                )
            lines.append(
                f"| {day.date} | {len(day.actual.recommendations) if day.actual else 'n/a'} | "
                f"{len(day.recommendations)} | {overlap if overlap is not None else 'n/a'} |"
            )
    else:
        lines.append("Actual archive comparison was not requested.")
    lines.extend(["", "## Warnings and limitations", ""])
    lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines) + "\n"


class OfflineBacktester:
    def __init__(self, project_root: Path, profile: ResearchProfile) -> None:
        self.project_root = project_root
        self.profile = profile
        self.data_dir = project_root / "data"

    def run(
        self,
        *,
        from_date: date,
        to_date: date,
        compare_actual: bool = False,
        write_reports: bool = True,
        report_dir: Path | None = None,
    ) -> BacktestResult:
        replay_dates = _dates(from_date, to_date)
        historical_all = HistoricalPaperStorage(self.data_dir).load()
        reading_pool_all = ReadingPoolStorage(self.data_dir).load()
        dismissals_all = DismissalStorage(self.data_dir).load()
        candidate_storage = CandidateStorage(self.data_dir)
        recommendation_storage = RecommendationStorage(self.data_dir)
        simulated_history: dict[str, list[dict[str, Any]]] = {}
        days: list[BacktestDay] = []
        zone = ZoneInfo(self.profile.timezone)

        for target in replay_dates:
            day_string = target.isoformat()
            candidate_path = candidate_storage.path_for(day_string)
            candidate_batch_available = candidate_path.exists()
            if candidate_batch_available:
                batch = candidate_storage.load(day_string)
                if batch.date > day_string or batch.fetched_at[:10] > day_string:
                    recent = []
                else:
                    available = [
                        copy.deepcopy(paper)
                        for paper in batch.papers
                        if _published_on_or_before(paper.published, target)
                        and _published_on_or_before(paper.updated, target)
                    ]
                    recent = score_papers(available, self.profile)
            else:
                recent = []

            historical: list[HistoricalPaper] = [
                copy.deepcopy(paper)
                for paper in historical_all
                if _published_on_or_before(paper.publication_date, target)
                and _published_on_or_before(paper.discovered_at, target)
            ]
            reading_pool = [
                copy.deepcopy(entry)
                for entry in reading_pool_all
                if _published_on_or_before(entry.added_at, target)
            ]
            dismissals = [
                copy.deepcopy(entry)
                for entry in dismissals_all
                if _published_on_or_before(entry.dismissed_at, target)
            ]
            run_at = datetime.combine(target, time(12, 30), tzinfo=zone)
            generated_at = run_at.isoformat(timespec="seconds")
            selection = CuratedRecommendationEngine(self.profile).select(
                recent_new=recent,
                historical_papers=historical,
                reading_pool=reading_pool,
                history=simulated_history,
                target_date=day_string,
                considered_at=generated_at,
                dismissals=dismissals,
            )
            entries = selection.recommendations[:5]
            actual = None
            if compare_actual and recommendation_storage.path_for(day_string).exists():
                actual = recommendation_storage.load(day_string)
            days.append(
                BacktestDay(
                    date=day_string,
                    candidate_count=len(recent),
                    candidate_batch_available=candidate_batch_available,
                    historical_candidate_count=len(historical),
                    recommendations=entries,
                    actual=actual,
                )
            )
            _append_history(
                simulated_history,
                entries,
                day=day_string,
                generated_at=generated_at,
            )

        missing_candidate_dates = [
            day.date for day in days if not day.candidate_batch_available
        ]
        warnings = [
            "Historical influence metadata uses the currently cached snapshot; this is recommendation-policy replay, not a perfect point-in-time reconstruction.",
            "Recent papers are replayed only from a locally persisted candidate batch for the same date; dates without a batch have no recent candidates.",
            "Actual recommendation archives are immutable comparison inputs and never affect simulated cooldown history.",
        ]
        if missing_candidate_dates:
            warnings.append(
                "Missing local candidate batches: " + ", ".join(missing_candidate_dates)
            )
        result = BacktestResult(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            compare_actual=compare_actual,
            days=days,
            metrics=_metrics(days, self.profile),
            configuration_snapshot=_configuration_snapshot(self.profile),
            warnings=warnings,
        )
        if write_reports:
            destination = report_dir or self.project_root / "reports" / "backtests"
            stem = f"{result.from_date}_to_{result.to_date}"
            json_path = destination / f"{stem}.json"
            markdown_path = destination / f"{stem}.md"
            atomic_write_text(
                json_path,
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            atomic_write_text(markdown_path, _markdown(result))
            result.json_path = json_path
            result.markdown_path = markdown_path
        return result
