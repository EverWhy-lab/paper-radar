from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paper_radar.reader_models import (
    CandidateBatch,
    DailyRecommendations,
    DismissalEntry,
    READING_STATUSES,
    ReadingPoolEntry,
)
from paper_radar.storage import StorageError, atomic_write_text


class PoolError(ValueError):
    """Raised for invalid reading-pool operations."""


class CandidateStorage:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "candidates"

    def path_for(self, date_string: str) -> Path:
        return self.directory / f"{date_string}.json"

    def save(self, batch: CandidateBatch) -> Path:
        path = self.path_for(batch.date)
        atomic_write_text(path, json.dumps(batch.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return path

    def load(self, date_string: str) -> CandidateBatch:
        path = self.path_for(date_string)
        try:
            return CandidateBatch.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read candidate metadata {path}: {exc}") from exc


class ReadingPoolStorage:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "reading_pool.json"

    def load(self) -> list[ReadingPoolEntry]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return [ReadingPoolEntry.from_dict(entry) for entry in value.get("entries", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read reading pool {self.path}: {exc}") from exc

    def save(self, entries: list[ReadingPoolEntry]) -> Path:
        ordered = sorted(entries, key=lambda entry: (-entry.priority, entry.added_at, entry.base_arxiv_id))
        atomic_write_text(
            self.path,
            json.dumps(
                {"schema_version": 1, "entries": [entry.to_dict() for entry in ordered]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return self.path

    def get(self, base_arxiv_id: str) -> ReadingPoolEntry:
        entry = next(
            (item for item in self.load() if item.base_arxiv_id == base_arxiv_id),
            None,
        )
        if entry is None:
            raise PoolError(f"Paper {base_arxiv_id} is not in the reading pool")
        return entry

    def add(self, entry: ReadingPoolEntry) -> Path:
        entries = self.load()
        if any(item.base_arxiv_id == entry.base_arxiv_id for item in entries):
            raise PoolError(f"Paper {entry.base_arxiv_id} is already in the reading pool")
        entries.append(entry)
        return self.save(entries)

    def set_status(self, base_arxiv_id: str, status: str) -> Path:
        if status not in READING_STATUSES:
            raise PoolError(
                f"Invalid reading status {status!r}; expected one of {', '.join(sorted(READING_STATUSES))}"
            )
        entries = self.load()
        for entry in entries:
            if entry.base_arxiv_id == base_arxiv_id:
                entry.reading_status = status
                return self.save(entries)
        raise PoolError(f"Paper {base_arxiv_id} is not in the reading pool")

    def dismiss(self, base_arxiv_id: str) -> Path:
        entries = self.load()
        for entry in entries:
            if entry.base_arxiv_id == base_arxiv_id:
                entry.dismissed = True
                return self.save(entries)
        raise PoolError(f"Paper {base_arxiv_id} is not in the reading pool")


class RecommendationStorage:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "recommendations"

    def path_for(self, date_string: str) -> Path:
        return self.directory / f"{date_string}.json"

    def save(self, daily: DailyRecommendations) -> Path:
        path = self.path_for(daily.date)
        atomic_write_text(path, json.dumps(daily.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return path

    def load(self, date_string: str) -> DailyRecommendations:
        path = self.path_for(date_string)
        try:
            return DailyRecommendations.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read recommendations {path}: {exc}") from exc

    def available_dates(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted((path.stem for path in self.directory.glob("*.json")), reverse=True)

    def history(self, *, exclude_date: str | None = None) -> dict[str, list[dict[str, Any]]]:
        history: dict[str, list[dict[str, Any]]] = {}
        for date_string in self.available_dates():
            if date_string == exclude_date:
                continue
            daily = self.load(date_string)
            for recommendation in daily.recommendations:
                event = {
                    "date": daily.date,
                    "generated_at": daily.generated_at,
                    "category": recommendation.category,
                    "version": recommendation.paper.version,
                }
                for identifier in recommendation.aliases:
                    history.setdefault(identifier, []).append(event)
        return history

    def previous_nonempty(self, before_date: str) -> DailyRecommendations | None:
        for date_string in self.available_dates():
            if date_string < before_date:
                daily = self.load(date_string)
                if daily.recommendations:
                    return daily
        return None


class DismissalStorage:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "dismissals.json"

    def load(self) -> list[DismissalEntry]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return [
                DismissalEntry.from_dict(item)
                for item in value.get("entries", [])
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read dismissals {self.path}: {exc}") from exc

    def save(self, entries: list[DismissalEntry]) -> Path:
        ordered = sorted(entries, key=lambda entry: entry.dismissed_at)
        atomic_write_text(
            self.path,
            json.dumps(
                {
                    "schema_version": 1,
                    "entries": [entry.to_dict() for entry in ordered],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return self.path

    def add(self, entry: DismissalEntry) -> Path:
        entries = self.load()
        if any(
            item.canonical_paper_id == entry.canonical_paper_id for item in entries
        ):
            raise PoolError(
                f"Paper {entry.canonical_paper_id} is already dismissed"
            )
        entries.append(entry)
        return self.save(entries)

    def remove(self, canonical_paper_id: str) -> DismissalEntry:
        key = canonical_paper_id.casefold()
        entries = self.load()
        for index, entry in enumerate(entries):
            if entry.canonical_paper_id == key:
                removed = entries.pop(index)
                self.save(entries)
                return removed
        raise PoolError(f"Dismissal not found: {canonical_paper_id}")
