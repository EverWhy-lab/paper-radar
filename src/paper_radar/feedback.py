from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_radar.history_storage import HistoricalPaperStorage, HistoryStorageError, identifier_key
from paper_radar.reader_models import DismissalEntry, FavoriteEntry
from paper_radar.reader_storage import (
    DismissalStorage,
    FavoriteStorage,
    RecommendationStorage,
)
from paper_radar.rising_storage import RisingCandidateStorage


_LINE = re.compile(r"^(not-interested|favorite)\s+(\S+?)\s*—\s*(.*)$")


@dataclass(frozen=True)
class FeedbackApplyResult:
    applied_dismissals: int
    applied_favorites: int
    skipped: int
    failed: list[str]


def _resolve_metadata(data_dir: Path, canonical: str) -> dict[str, Any] | None:
    papers = HistoricalPaperStorage(data_dir).load() + RisingCandidateStorage(data_dir).load()
    for paper in papers:
        if canonical in paper.aliases:
            return {
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.publication_year,
                "source": paper.source_name or "OpenAlex",
                "abstract": paper.abstract or "",
                "landing": paper.landing_page_url,
                "pdf": paper.pdf_url,
                "openalex": paper.openalex_url,
                "doi": paper.doi,
                "topics": paper.matched_topics,
            }
    recommendation_storage = RecommendationStorage(data_dir)
    for date_string in recommendation_storage.available_dates():
        daily = recommendation_storage.load(date_string)
        for entry in daily.recommendations:
            if canonical == entry.canonical_paper_id or canonical in entry.aliases:
                paper = entry.paper
                year = int(paper.published[:4]) if paper.published else None
                return {
                    "title": paper.title,
                    "authors": paper.authors,
                    "year": year,
                    "source": paper.primary_category or "arXiv",
                    "abstract": paper.summary or "",
                    "landing": paper.abs_url,
                    "pdf": paper.pdf_url,
                    "openalex": None,
                    "doi": None,
                    "topics": paper.matched_topics,
                }
    return None


def apply_feedback_lines(
    lines: list[str],
    data_dir: Path,
    now_iso: str,
) -> FeedbackApplyResult:
    dismissals = DismissalStorage(data_dir)
    favorites = FavoriteStorage(data_dir)
    known_dismissals = {entry.canonical_paper_id for entry in dismissals.load()}
    known_favorites = {entry.canonical_paper_id for entry in favorites.load()}
    applied_dismissals = 0
    applied_favorites = 0
    skipped = 0
    failed: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = _LINE.match(line)
        if match is None:
            failed.append(line[:80])
            continue
        kind, raw_id, title = match.group(1), match.group(2), match.group(3).strip()
        try:
            canonical = identifier_key(raw_id)
        except HistoryStorageError:
            failed.append(line[:80])
            continue
        metadata = _resolve_metadata(data_dir, canonical) or {}
        if kind == "not-interested":
            if canonical in known_dismissals:
                skipped += 1
                continue
            dismissals.add(
                DismissalEntry(
                    canonical_paper_id=canonical,
                    title=metadata.get("title") or title,
                    topics=list(metadata.get("topics") or []),
                    reason="not_interested",
                    dismissed_at=now_iso,
                )
            )
            known_dismissals.add(canonical)
            applied_dismissals += 1
        else:
            if canonical in known_favorites:
                skipped += 1
                continue
            favorites.add(
                FavoriteEntry(
                    canonical_paper_id=canonical,
                    title=metadata.get("title") or title,
                    authors=list(metadata.get("authors") or []),
                    publication_year=metadata.get("year"),
                    source_name=str(metadata.get("source") or ""),
                    abstract=str(metadata.get("abstract") or ""),
                    landing_page_url=metadata.get("landing"),
                    pdf_url=metadata.get("pdf"),
                    openalex_url=metadata.get("openalex"),
                    doi=metadata.get("doi"),
                    saved_at=now_iso,
                )
            )
            known_favorites.add(canonical)
            applied_favorites += 1

    return FeedbackApplyResult(
        applied_dismissals=applied_dismissals,
        applied_favorites=applied_favorites,
        skipped=skipped,
        failed=failed,
    )
