from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from paper_radar.config import ResearchProfile
from paper_radar.reader_models import DailyRecommendations
from paper_radar.reader_storage import RecommendationStorage
from paper_radar.storage import atomic_write_text


PACKAGE_DIR = Path(__file__).resolve().parent
CATEGORY_LABELS = {
    "recent_new": "近期新论文",
    "reading_pool": "历史阅读",
    "important_update": "重要更新",
    "frontier_recent": "前沿新论文",
    "high_impact_historical": "领域内高影响力",
    "review_knowledge_map": "综述 / 知识地图",
}


class RecommendationSiteRenderer:
    def __init__(
        self,
        site_dir: Path,
        storage: RecommendationStorage,
        profile: ResearchProfile,
    ) -> None:
        self.site_dir = site_dir
        self.storage = storage
        self.profile = profile
        self.environment = Environment(
            loader=FileSystemLoader(PACKAGE_DIR / "templates"),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["display_datetime"] = self._display_datetime

    def _display_datetime(self, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(ZoneInfo(self.profile.timezone)).strftime(
                "%Y-%m-%d %H:%M Asia/Shanghai"
            )
        except ValueError:
            return value

    def _write_assets(self) -> None:
        for filename in ("reader.css", "favicon.svg"):
            content = (PACKAGE_DIR / "assets" / filename).read_text(encoding="utf-8")
            atomic_write_text(self.site_dir / "assets" / filename, content)

    def _context(
        self,
        daily: DailyRecommendations,
        *,
        asset_prefix: str,
        archive_prefix: str,
        demo_label: str | None = None,
    ) -> dict[str, Any]:
        previous = self.storage.previous_nonempty(daily.date) if demo_label is None else None
        archive_dates = self.storage.available_dates() if demo_label is None else []
        config = self.profile.recommendations
        daily_mix = config["daily_mix"]
        return {
            "site_name": self.profile.site_name,
            "daily": daily,
            "asset_prefix": asset_prefix,
            "category_labels": CATEGORY_LABELS,
            "topic_labels": self.profile.topic_labels,
            "previous": (
                {
                    "date": previous.date,
                    "url": f"{archive_prefix}{previous.date}.html",
                }
                if previous
                else None
            ),
            "archives": [
                {"date": date_string, "url": f"{archive_prefix}{date_string}.html"}
                for date_string in archive_dates
            ],
            "recent_threshold": int(daily_mix["frontier_recent"]["min_research_fit"]),
            "historical_threshold": float(
                daily_mix["high_impact_historical"]["min_historical_value_score"]
            ),
            "max_total": int(daily_mix["max_total"]),
            "demo_label": demo_label,
        }

    def _render_document(
        self,
        daily: DailyRecommendations,
        destination: Path,
        *,
        asset_prefix: str,
        archive_prefix: str,
        demo_label: str | None = None,
    ) -> Path:
        template = self.environment.get_template("reader.html")
        atomic_write_text(
            destination,
            template.render(
                **self._context(
                    daily,
                    asset_prefix=asset_prefix,
                    archive_prefix=archive_prefix,
                    demo_label=demo_label,
                )
            ),
        )
        return destination

    def render(self, target_date: str) -> tuple[Path, Path]:
        self._write_assets()
        target = self.storage.load(target_date)
        archive_path = self.site_dir / "recommendations" / f"{target_date}.html"
        self._render_document(
            target,
            archive_path,
            asset_prefix="../assets/",
            archive_prefix="",
        )
        latest_date = self.storage.available_dates()[0]
        latest = self.storage.load(latest_date)
        index_path = self.site_dir / "index.html"
        self._render_document(
            latest,
            index_path,
            asset_prefix="assets/",
            archive_prefix="recommendations/",
        )
        return index_path, archive_path

    def render_demo(
        self,
        daily: DailyRecommendations,
        filename: str,
        label: str,
    ) -> Path:
        self._write_assets()
        return self._render_document(
            daily,
            self.site_dir / "demo" / filename,
            asset_prefix="../assets/",
            archive_prefix="../recommendations/",
            demo_label=label,
        )
