from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from paper_radar.config import ResearchProfile
from paper_radar.models import DailyRadar, Paper
from paper_radar.storage import RadarStorage, atomic_write_text


PACKAGE_DIR = Path(__file__).resolve().parent


def _display_datetime(value: str, timezone_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = parsed.astimezone(ZoneInfo(timezone_name))
        return local.strftime("%Y-%m-%d %H:%M Asia/Shanghai")
    except ValueError:
        return value


def partition_papers(papers: list[Paper], profile: ResearchProfile) -> dict[str, list[Paper]]:
    ordered = sorted(
        papers,
        key=lambda paper: (paper.research_fit, paper.video_potential, paper.updated),
        reverse=True,
    )
    must_read = ordered[:3]
    remaining = ordered[3:]

    high_threshold = int(profile.sections["highly_relevant_min_score"])
    high_min_count = int(profile.sections.get("highly_relevant_min_count", 5))
    high_limit = int(profile.sections["highly_relevant_limit"])
    highly_relevant = [paper for paper in remaining if paper.research_fit >= high_threshold][:high_limit]
    if len(highly_relevant) < high_min_count:
        high_ids = {paper.base_id for paper in highly_relevant}
        backfill = [paper for paper in remaining if paper.base_id not in high_ids]
        highly_relevant.extend(backfill[: high_min_count - len(highly_relevant)])
    selected = {paper.base_id for paper in highly_relevant}
    remaining = [paper for paper in remaining if paper.base_id not in selected]

    expanded_threshold = int(profile.sections["expanded_min_score"])
    expanded_min_count = int(profile.sections.get("expanded_min_count", 3))
    expanded_limit = int(profile.sections["expanded_limit"])
    expanded = [paper for paper in remaining if paper.research_fit >= expanded_threshold][:expanded_limit]
    if len(expanded) < expanded_min_count:
        expanded_ids = {paper.base_id for paper in expanded}
        backfill = [paper for paper in remaining if paper.base_id not in expanded_ids]
        expanded.extend(backfill[: expanded_min_count - len(expanded)])
    selected = {paper.base_id for paper in expanded}
    others = [paper for paper in remaining if paper.base_id not in selected]
    return {
        "must_read": must_read,
        "highly_relevant": highly_relevant,
        "expanded": expanded,
        "others": others,
    }


class SiteRenderer:
    def __init__(self, site_dir: Path, storage: RadarStorage, profile: ResearchProfile) -> None:
        self.site_dir = site_dir
        self.storage = storage
        self.profile = profile
        self.environment = Environment(
            loader=FileSystemLoader(PACKAGE_DIR / "templates"),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["display_datetime"] = lambda value: _display_datetime(
            value, self.profile.timezone
        )

    def _archives(self, current_date: str, prefix: str) -> list[dict[str, str]]:
        return [
            {
                "date": date_string,
                "url": "./" if date_string == current_date else f"{prefix}{date_string}.html",
            }
            for date_string in self.storage.available_dates()
        ]

    def _context(
        self,
        radar: DailyRadar,
        *,
        asset_prefix: str,
        archive_prefix: str,
        status_message: str | None = None,
        run_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sections = partition_papers(radar.papers, self.profile)
        topic_labels = self.profile.topic_labels
        available_topic_ids = sorted(
            {topic for paper in radar.all_papers for topic in paper.matched_topics},
            key=lambda topic: topic_labels.get(topic, topic),
        )
        return {
            "site_name": self.profile.site_name,
            "radar": radar,
            "sections": sections,
            "asset_prefix": asset_prefix,
            "archives": self._archives(radar.date, archive_prefix),
            "topic_labels": topic_labels,
            "available_topics": [
                {"id": topic, "label": topic_labels.get(topic, topic)}
                for topic in available_topic_ids
            ],
            "status_message": status_message,
            "run_status": run_status,
            "total_cards": len(radar.all_papers),
        }

    def _write_assets(self) -> None:
        for filename in ("styles.css", "app.js", "favicon.svg"):
            content = (PACKAGE_DIR / "assets" / filename).read_text(encoding="utf-8")
            atomic_write_text(self.site_dir / "assets" / filename, content)

    def _render_file(
        self,
        radar: DailyRadar,
        destination: Path,
        *,
        index: bool,
        run_status: dict[str, Any] | None = None,
    ) -> None:
        template = self.environment.get_template("index.html")
        context = self._context(
            radar,
            asset_prefix="assets/" if index else "../assets/",
            archive_prefix="archive/" if index else "",
            run_status=run_status,
        )
        atomic_write_text(destination, template.render(**context))

    def _latest_nonempty(self, dates: list[str]) -> DailyRadar | None:
        for date_string in dates:
            radar = self.storage.load_daily(date_string)
            if radar.papers:
                return radar
        return None

    def render(self, target_date: str) -> tuple[Path, Path]:
        self._write_assets()
        target = self.storage.load_daily(target_date)
        archive_path = self.site_dir / "archive" / f"{target_date}.html"
        self._render_file(target, archive_path, index=False)

        dates = self.storage.available_dates()
        latest = self.storage.load_daily(dates[0])
        body = latest if latest.papers else (self._latest_nonempty(dates) or latest)
        run_status = None
        seen = self.storage.load_seen()
        if (
            seen.last_run_mode == "incremental"
            and seen.last_run_new_submission_count == 0
            and seen.last_run_date
        ):
            status_date = seen.last_run_date
            if self.storage.daily_path(status_date).exists():
                empty_archive_url = f"archive/{status_date}.html"
            else:
                empty_archive_url = f"archive/{latest.date}.html"
            run_status = {
                "date": status_date,
                "last_updated": seen.last_successful_run_at or latest.generated_at,
                "empty_archive_url": empty_archive_url,
                "fallback_date": body.date,
                "fallback_archive_url": f"archive/{body.date}.html",
                "using_fallback": body.date != status_date,
                "version_update_count": seen.last_run_version_update_count or 0,
            }
        else:
            run_new_count = latest.run_new_submission_count
            if run_new_count is None:
                run_new_count = len(latest.papers)
            if run_new_count == 0:
                run_status = {
                    "date": latest.date,
                    "last_updated": latest.generated_at,
                    "empty_archive_url": f"archive/{latest.date}.html",
                    "fallback_date": body.date,
                    "fallback_archive_url": f"archive/{body.date}.html",
                    "using_fallback": body.date != latest.date,
                    "version_update_count": latest.run_version_update_count or 0,
                }
        index_path = self.site_dir / "index.html"
        self._render_file(body, index_path, index=True, run_status=run_status)
        return index_path, archive_path

    def render_unavailable(self, target_date: str, generated_at: str, message: str) -> Path:
        """Create a truthful first-run empty state without touching historical data."""
        self._write_assets()
        radar = DailyRadar(date=target_date, generated_at=generated_at, papers=[])
        template = self.environment.get_template("index.html")
        html = template.render(
            **self._context(
                radar,
                asset_prefix="assets/",
                archive_prefix="archive/",
                status_message=message,
            )
        )
        index_path = self.site_dir / "index.html"
        atomic_write_text(index_path, html)
        return index_path
