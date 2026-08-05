from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paper_radar.history_models import (
    HistoricalPaper,
    SeedPaper,
    normalize_arxiv_id,
    normalize_doi,
    normalize_openalex_id,
)
from paper_radar.storage import StorageError, atomic_write_text


class HistoryStorageError(ValueError):
    """Raised for invalid historical pool or seed operations."""


def identifier_key(identifier: str) -> str:
    raw = identifier.strip()
    lowered = raw.casefold()
    if lowered.startswith("openalex:"):
        raw = raw.split(":", 1)[1]
    if value := normalize_openalex_id(raw):
        return f"openalex:{value}".casefold()
    if lowered.startswith("10.") or lowered.startswith("doi:") or "doi.org/" in lowered:
        if value := normalize_doi(raw):
            return f"doi:{value}".casefold()
    if value := normalize_arxiv_id(raw):
        return f"arxiv:{value}".casefold()
    raise HistoryStorageError(f"Unsupported paper identifier: {identifier}")


def _merge_values(existing: HistoricalPaper, incoming: HistoricalPaper) -> HistoricalPaper:
    # Preserve local workflow state while refreshing provider metadata.
    lifecycle = {
        "canonical_paper_id": existing.canonical_paper_id,
        "discovered_at": min(existing.discovered_at, incoming.discovered_at),
        "last_considered_at": existing.last_considered_at,
        "recommended_at": existing.recommended_at,
        "recommendation_count": existing.recommendation_count,
        "reading_status": existing.reading_status,
        "dismissed": existing.dismissed,
    }
    merged = existing.to_dict()
    for key, value in incoming.to_dict().items():
        if value not in (None, "", [], {}):
            merged[key] = value
    merged.update(lifecycle)
    for field in (
        "discovery_source",
        "seed_paper_id",
        "referenced_works",
        "related_works",
    ):
        merged[field] = sorted(
            set(getattr(existing, field)) | set(getattr(incoming, field))
        )
    return HistoricalPaper.from_dict(merged)


def deduplicate_historical(
    papers: list[HistoricalPaper],
) -> list[HistoricalPaper]:
    merged: list[HistoricalPaper] = []
    aliases: dict[str, int] = {}
    for paper in papers:
        matches = {aliases[alias] for alias in paper.aliases if alias in aliases}
        if not matches:
            index = len(merged)
            merged.append(paper)
        else:
            index = min(matches)
            merged[index] = _merge_values(merged[index], paper)
            for duplicate_index in sorted(matches - {index}, reverse=True):
                merged[index] = _merge_values(merged[index], merged[duplicate_index])
                merged.pop(duplicate_index)
                aliases = {
                    alias: (value - 1 if value > duplicate_index else value)
                    for alias, value in aliases.items()
                    if value != duplicate_index
                }
        for alias in merged[index].aliases:
            aliases[alias] = index
    return sorted(merged, key=lambda paper: paper.canonical_paper_id)


class HistoricalPaperStorage:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "history" / "discovery_pool.json"

    def load(self) -> list[HistoricalPaper]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return [
                HistoricalPaper.from_dict(item)
                for item in value.get("papers", [])
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read historical discovery pool {self.path}: {exc}") from exc

    def save(self, papers: list[HistoricalPaper]) -> Path:
        unique = deduplicate_historical(papers)
        atomic_write_text(
            self.path,
            json.dumps(
                {
                    "schema_version": 1,
                    "paper_count": len(unique),
                    "papers": [paper.to_dict() for paper in unique],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return self.path

    def merge(self, incoming: list[HistoricalPaper]) -> list[HistoricalPaper]:
        return deduplicate_historical(self.load() + incoming)

    def get(self, identifier: str) -> HistoricalPaper:
        key = identifier_key(identifier)
        for paper in self.load():
            if key in paper.aliases:
                return paper
        raise HistoryStorageError(f"Historical paper not found: {identifier}")

    def top(self, limit: int) -> list[HistoricalPaper]:
        return sorted(
            self.load(),
            key=lambda paper: (
                paper.historical_value_score is not None,
                paper.historical_value_score or 0,
                paper.research_fit,
                paper.publication_date or "",
            ),
            reverse=True,
        )[: max(0, limit)]


class SeedStorage:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "history" / "seeds.json"

    def load(self) -> list[SeedPaper]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return [SeedPaper.from_dict(item) for item in value.get("seeds", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"Unable to read historical seeds {self.path}: {exc}") from exc

    def save(self, seeds: list[SeedPaper]) -> Path:
        ordered = sorted(seeds, key=lambda seed: identifier_key(seed.identifier))
        atomic_write_text(
            self.path,
            json.dumps(
                {"schema_version": 1, "seeds": [seed.to_dict() for seed in ordered]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return self.path

    def add(self, identifier: str, added_at: str) -> SeedPaper:
        key = identifier_key(identifier)
        seeds = self.load()
        if any(identifier_key(seed.identifier) == key for seed in seeds):
            raise HistoryStorageError(f"Seed already exists: {identifier}")
        seed = SeedPaper(
            identifier=identifier.strip(),
            added_at=added_at,
            openalex_id=(key.split(":", 1)[1].upper() if key.startswith("openalex:") else None),
            doi=(key.split(":", 1)[1] if key.startswith("doi:") else None),
            base_arxiv_id=(key.split(":", 1)[1] if key.startswith("arxiv:") else None),
        )
        seeds.append(seed)
        self.save(seeds)
        return seed

    def remove(self, identifier: str) -> SeedPaper:
        key = identifier_key(identifier)
        seeds = self.load()
        for index, seed in enumerate(seeds):
            if identifier_key(seed.identifier) == key:
                removed = seeds.pop(index)
                self.save(seeds)
                return removed
        raise HistoryStorageError(f"Seed not found: {identifier}")
