from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from paper_radar.history_models import HistoricalPaper
from paper_radar.history_storage import deduplicate_historical
from paper_radar.storage import StorageError, atomic_write_text


@dataclass(frozen=True)
class CitationSnapshot:
    canonical_paper_id: str
    openalex_id: str | None
    title: str
    source: str | None
    publication_date: str | None
    cited_by_count: int | None
    fwci: float | None
    citation_normalized_percentile: float | None
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CitationSnapshot":
        return cls(
            canonical_paper_id=str(value["canonical_paper_id"]),
            openalex_id=value.get("openalex_id"),
            title=str(value.get("title", "")),
            source=value.get("source"),
            publication_date=value.get("publication_date"),
            cited_by_count=(
                int(value["cited_by_count"])
                if value.get("cited_by_count") is not None
                else None
            ),
            fwci=(float(value["fwci"]) if value.get("fwci") is not None else None),
            citation_normalized_percentile=(
                float(value["citation_normalized_percentile"])
                if value.get("citation_normalized_percentile") is not None
                else None
            ),
            captured_at=str(value["captured_at"]),
        )

    @classmethod
    def from_paper(
        cls, paper: HistoricalPaper, *, captured_at: str
    ) -> "CitationSnapshot":
        return cls(
            canonical_paper_id=paper.canonical_paper_id,
            openalex_id=paper.openalex_id,
            title=paper.title,
            source=paper.source_name,
            publication_date=paper.publication_date,
            cited_by_count=paper.cited_by_count,
            fwci=paper.fwci,
            citation_normalized_percentile=paper.citation_normalized_percentile,
            captured_at=captured_at,
        )


class CitationSnapshotStorage:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "rising" / "citation_snapshots.json"

    def load(self) -> dict[str, list[CitationSnapshot]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            histories: dict[str, list[CitationSnapshot]] = {}
            for item in value.get("papers", []):
                canonical = str(item["canonical_paper_id"]).casefold()
                histories[canonical] = sorted(
                    [
                        CitationSnapshot.from_dict(snapshot)
                        for snapshot in item.get("snapshots", [])
                    ],
                    key=lambda snapshot: snapshot.captured_at,
                )
            return histories
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"Unable to read rising citation snapshots {self.path}: {exc}"
            ) from exc

    def save(
        self, histories: dict[str, list[CitationSnapshot]]
    ) -> Path:
        papers = []
        for canonical, snapshots in sorted(histories.items()):
            ordered = sorted(snapshots, key=lambda snapshot: snapshot.captured_at)
            papers.append(
                {
                    "canonical_paper_id": canonical,
                    "snapshots": [snapshot.to_dict() for snapshot in ordered],
                }
            )
        atomic_write_text(
            self.path,
            json.dumps(
                {"schema_version": 1, "paper_count": len(papers), "papers": papers},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return self.path

    def merged(
        self,
        snapshots: list[CitationSnapshot],
        *,
        max_per_paper: int,
    ) -> dict[str, list[CitationSnapshot]]:
        histories = self.load()
        for snapshot in snapshots:
            canonical = snapshot.canonical_paper_id.casefold()
            by_time = {
                existing.captured_at: existing
                for existing in histories.get(canonical, [])
            }
            by_time[snapshot.captured_at] = snapshot
            histories[canonical] = sorted(
                by_time.values(), key=lambda item: item.captured_at
            )[-max_per_paper:]
        return histories


class RisingCandidateStorage:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "rising" / "candidates.json"

    def load(self) -> list[HistoricalPaper]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return [
                HistoricalPaper.from_dict(item) for item in value.get("papers", [])
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"Unable to read rising candidate pool {self.path}: {exc}"
            ) from exc

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

    def preserve_workflow_state(
        self, incoming: list[HistoricalPaper]
    ) -> list[HistoricalPaper]:
        existing_by_alias = {
            alias: paper for paper in self.load() for alias in paper.aliases
        }
        for paper in incoming:
            existing = next(
                (
                    existing_by_alias[alias]
                    for alias in paper.aliases
                    if alias in existing_by_alias
                ),
                None,
            )
            if existing is None:
                continue
            paper.discovered_at = min(existing.discovered_at, paper.discovered_at)
            paper.last_considered_at = existing.last_considered_at
            paper.recommended_at = existing.recommended_at
            paper.recommendation_count = existing.recommendation_count
            paper.reading_status = existing.reading_status
            paper.dismissed = existing.dismissed
        return deduplicate_historical(incoming)
