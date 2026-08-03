from __future__ import annotations

from pathlib import Path
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.models import DailyRadar, Paper
from paper_radar.rendering import partition_papers
from paper_radar.storage import atomic_write_text


SECTION_LABELS = {
    "must_read": "今日必读 / Must Read Today",
    "highly_relevant": "高度相关 / Highly Relevant",
    "expanded": "扩展视野 / Broaden the View",
}


def _keywords(reason: dict[str, Any]) -> str:
    values = [f"`{keyword}`" for keyword in reason.get("keywords", [])]
    return ", ".join(values) if values else "no keyword"


def _research_lines(paper: Paper) -> list[str]:
    topic_reasons = [reason for reason in paper.research_reasons if reason["kind"] == "topic"]
    if not topic_reasons:
        return ["  - No configured research keyword contribution."]
    return [
        (
            f"  - {_keywords(reason)} → **+{reason['points']}** total for "
            f"{reason['label']} ({reason['location']}; the topic contributes once even when "
            "multiple configured aliases match)."
        )
        for reason in topic_reasons
    ]


def _research_reason_lines(paper: Paper, kind: str) -> list[str]:
    values = [reason for reason in paper.research_reasons if reason["kind"] == kind]
    if not values:
        return ["  - None."]
    lines: list[str] = []
    for reason in values:
        sign = "+" if reason["points"] > 0 else ""
        lines.append(
            f"  - {reason['label']} ({reason['kind']}) → **{sign}{reason['points']}**."
        )
    return lines


def _video_lines(paper: Paper) -> list[str]:
    if not paper.video_reasons:
        return ["  - No video-potential contribution."]
    lines: list[str] = []
    for reason in paper.video_reasons:
        keyword_text = f" via {_keywords(reason)}" if reason.get("keywords") else ""
        lines.append(
            f"  - {reason['label']}{keyword_text} → **+{reason['points']}** ({reason['location']})."
        )
    return lines


def build_relevance_audit(radar: DailyRadar, profile: ResearchProfile) -> str:
    sections = partition_papers(radar.papers, profile)
    ordered = sorted(
        radar.papers,
        key=lambda paper: (paper.research_fit, paper.video_potential, paper.updated),
        reverse=True,
    )
    ranks = {paper.base_id: index for index, paper in enumerate(ordered, start=1)}
    high_threshold = int(profile.sections["highly_relevant_min_score"])
    expanded_threshold = int(profile.sections["expanded_min_score"])
    lines = [
        f"# Relevance Audit — {radar.date}",
        "",
        "This report is generated entirely from the stored arXiv metadata and the configured V0.1.1 scoring rules. It uses no AI-generated summary or interpretation.",
        "",
        f"- New submissions ranked: {len(radar.papers)}",
        f"- Version updates excluded from recommendation ranking: {len(radar.version_updates)}",
        "- Ordering: `research_fit`, then `video_potential`, then arXiv `updated` timestamp, all descending.",
        "",
    ]

    for section_name in ("must_read", "highly_relevant", "expanded"):
        lines.extend([f"## {SECTION_LABELS[section_name]}", ""])
        for index, paper in enumerate(sections[section_name], start=1):
            rank = ranks[paper.base_id]
            if section_name == "must_read":
                why = f"Overall new-submission rank #{rank}; the first three ranked papers form Must Read Today."
            elif section_name == "highly_relevant" and paper.research_fit >= high_threshold:
                why = (
                    f"Overall new-submission rank #{rank}; research_fit meets the {high_threshold}-point "
                    "Highly Relevant threshold."
                )
            elif section_name == "highly_relevant":
                why = (
                    f"Overall new-submission rank #{rank}; selected by deterministic rank backfill to keep "
                    "at least five Highly Relevant papers after threshold matches."
                )
            elif paper.research_fit >= expanded_threshold:
                why = (
                    f"Overall new-submission rank #{rank}; remained after earlier sections and meets the "
                    f"{expanded_threshold}-point expanded-view threshold."
                )
            else:
                why = (
                    f"Overall new-submission rank #{rank}; selected by deterministic rank backfill to keep "
                    "at least three expanded-view papers."
                )

            lines.extend(
                [
                    f"### {index}. {paper.title}",
                    "",
                    f"- **Section:** {SECTION_LABELS[section_name]}",
                    f"- **research_fit:** {paper.research_fit}",
                    f"- **video_potential:** {paper.video_potential}",
                    f"- **Matched keywords:** {', '.join(f'`{keyword}`' for keyword in paper.matched_keywords) or 'None'}",
                    "- **Research keyword contributions:**",
                    *_research_lines(paper),
                    "- **Category contributions:**",
                    *_research_reason_lines(paper, "category"),
                    "- **Exclusions or down-weight items:**",
                    *_research_reason_lines(paper, "exclusion"),
                    "- **Video-potential contributions:**",
                    *_video_lines(paper),
                    f"- **Why this section:** {why}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def write_relevance_audit(
    radar: DailyRadar,
    profile: ResearchProfile,
    destination: Path,
) -> Path:
    atomic_write_text(destination, build_relevance_audit(radar, profile))
    return destination
