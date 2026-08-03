from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from paper_radar.models import DailyRadar, Paper, SeenState


class StorageError(RuntimeError):
    """Raised when local radar data cannot be read or written."""


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class RadarStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.daily_dir = data_dir / "daily"
        self.seen_path = data_dir / "seen_ids.json"

    def daily_path(self, date_string: str) -> Path:
        return self.daily_dir / f"{date_string}.json"

    def save_daily(self, radar: DailyRadar) -> Path:
        path = self.daily_path(radar.date)
        atomic_write_text(
            path,
            json.dumps(radar.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )
        return path

    def load_daily(self, date_string: str) -> DailyRadar:
        path = self.daily_path(date_string)
        try:
            return DailyRadar.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read daily radar {path}: {exc}") from exc

    def available_dates(self) -> list[str]:
        if not self.daily_dir.exists():
            return []
        return sorted((path.stem for path in self.daily_dir.glob("*.json")), reverse=True)

    def _historical_paper_catalog(self) -> dict[str, dict[str, str]]:
        catalog: dict[str, dict[str, str]] = {}
        if not self.daily_dir.exists():
            return catalog
        for path in sorted(self.daily_dir.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            seen_at = str(value.get("generated_at", ""))
            for paper in value.get("papers", []) + value.get("version_updates", []):
                base_id = str(paper.get("base_id", ""))
                if not base_id:
                    continue
                current = catalog.get(base_id)
                candidate = {
                    "published_at": str(paper.get("published", "")),
                    "updated_at": str(paper.get("updated", "")),
                    "first_seen_at": seen_at,
                    "last_seen_at": seen_at,
                }
                if current is None:
                    catalog[base_id] = candidate
                else:
                    if seen_at and (not current["first_seen_at"] or seen_at < current["first_seen_at"]):
                        current["first_seen_at"] = seen_at
                    if seen_at > current["last_seen_at"]:
                        current["last_seen_at"] = seen_at
                    if candidate["updated_at"] > current["updated_at"]:
                        current["updated_at"] = candidate["updated_at"]
                        current["published_at"] = candidate["published_at"]
        return catalog

    def load_seen(self, *, migrated_at: str | None = None) -> SeenState:
        if not self.seen_path.exists():
            return SeenState()
        try:
            value = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise StorageError(f"Unable to read seen IDs {self.seen_path}: {exc}") from exc

        raw_papers = dict(value.get("papers", value))
        if int(value.get("schema_version", 1)) >= 2:
            return SeenState(
                papers=raw_papers,
                last_successful_run_at=value.get("last_successful_run_at"),
                last_run_mode=value.get("last_run_mode"),
                last_run_date=value.get("last_run_date"),
                last_run_new_submission_count=value.get("last_run_new_submission_count"),
                last_run_version_update_count=value.get("last_run_version_update_count"),
                last_run_candidate_count=value.get("last_run_candidate_count"),
            )

        catalog = self._historical_paper_catalog()
        migrated: dict[str, dict[str, Any]] = {}
        for base_id, current in raw_papers.items():
            historical = catalog.get(base_id, {})
            fallback_seen_at = (
                historical.get("last_seen_at")
                or migrated_at
                or str(current.get("updated", ""))
            )
            migrated[base_id] = {
                "base_arxiv_id": base_id,
                "latest_version": int(current.get("version", 1)),
                "latest_arxiv_id": str(
                    current.get("arxiv_id", f"{base_id}v{int(current.get('version', 1))}")
                ),
                "published_at": historical.get("published_at", ""),
                "updated_at": str(current.get("updated", historical.get("updated_at", ""))),
                "first_seen_at": historical.get("first_seen_at") or fallback_seen_at,
                "last_seen_at": fallback_seen_at,
            }
        return SeenState(
            papers=dict(sorted(migrated.items())),
            last_successful_run_at=max(
                (item["last_seen_at"] for item in migrated.values()),
                default=migrated_at,
            ),
        )

    def updated_seen(self, state: SeenState, papers: list[Paper], seen_at: str) -> SeenState:
        merged = {base_id: dict(value) for base_id, value in state.papers.items()}
        for paper in papers:
            current = merged.get(paper.base_id)
            if current is None:
                merged[paper.base_id] = {
                    "base_arxiv_id": paper.base_id,
                    "latest_version": paper.version,
                    "latest_arxiv_id": paper.arxiv_id,
                    "published_at": paper.published,
                    "updated_at": paper.updated,
                    "first_seen_at": seen_at,
                    "last_seen_at": seen_at,
                }
                continue
            current["last_seen_at"] = seen_at
            if (paper.version, paper.updated) > (
                int(current.get("latest_version", 0)),
                str(current.get("updated_at", "")),
            ):
                current.update(
                    {
                        "base_arxiv_id": paper.base_id,
                        "latest_version": paper.version,
                        "latest_arxiv_id": paper.arxiv_id,
                        "published_at": paper.published,
                        "updated_at": paper.updated,
                    }
                )
        return SeenState(
            papers=dict(sorted(merged.items())),
            last_successful_run_at=seen_at,
            last_run_mode=state.last_run_mode,
            last_run_date=state.last_run_date,
            last_run_new_submission_count=state.last_run_new_submission_count,
            last_run_version_update_count=state.last_run_version_update_count,
            last_run_candidate_count=state.last_run_candidate_count,
        )

    def save_seen(self, seen: SeenState) -> Path:
        atomic_write_text(
            self.seen_path,
            json.dumps(seen.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )
        return self.seen_path
