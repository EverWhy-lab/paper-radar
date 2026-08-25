from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_radar.backtest import BacktestError, OfflineBacktester
from paper_radar.config import ConfigError, load_profile
from paper_radar.environment import load_project_dotenv
from paper_radar.fetchers.arxiv import ArxivClient, ArxivFetchError, split_arxiv_id
from paper_radar.feedback import _resolve_metadata, apply_feedback_lines
from paper_radar.history_discovery import HistoricalDiscoveryService
from paper_radar.history_storage import (
    HistoricalPaperStorage,
    HistoryStorageError,
    SeedStorage,
    identifier_key,
)
from paper_radar.providers.base import HistoricalProviderError
from paper_radar.providers.openalex import OpenAlexProvider
from paper_radar.rising_discovery import RisingDiscoveryService, RisingScanResult
from paper_radar.rising_storage import RisingCandidateStorage
from paper_radar.reader_models import (
    DismissalEntry,
    FavoriteEntry,
    READING_STATUSES,
    ReadingPoolEntry,
)
from paper_radar.reader_pipeline import (
    execute_reader_historical_run,
    execute_reader_incremental_run,
)
from paper_radar.reader_storage import (
    DismissalStorage,
    FavoriteStorage,
    PoolError,
    ReadingPoolStorage,
)
from paper_radar.scoring import score_paper
from paper_radar.storage import StorageError


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m paper_radar",
        description="Build the local Paper Radar site.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser(
        "run", help="fetch candidates and render a zero-to-five-paper shortlist"
    )
    run_parser.add_argument("--date", type=_date_argument, help="Asia/Shanghai date (YYYY-MM-DD)")

    serve_parser = subparsers.add_parser("serve", help="serve the generated static site")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8000, type=int)

    pool_parser = subparsers.add_parser("pool", help="manage the manual historical reading pool")
    pool_subparsers = pool_parser.add_subparsers(dest="pool_command", required=True)
    pool_add = pool_subparsers.add_parser("add", help="fetch and add one arXiv paper")
    pool_add.add_argument("arxiv_id")
    pool_subparsers.add_parser("list", help="list reading-pool entries")
    pool_status = pool_subparsers.add_parser("status", help="change reading status")
    pool_status.add_argument("arxiv_id")
    pool_status.add_argument("status", choices=sorted(READING_STATUSES))
    pool_dismiss = pool_subparsers.add_parser("dismiss", help="exclude a pool paper")
    pool_dismiss.add_argument("arxiv_id")

    history_parser = subparsers.add_parser(
        "history", help="discover and inspect historical paper candidates"
    )
    history_subparsers = history_parser.add_subparsers(
        dest="history_command", required=True
    )
    seed_parser = history_subparsers.add_parser("seed", help="manage seed papers")
    seed_subparsers = seed_parser.add_subparsers(dest="seed_command", required=True)
    seed_add = seed_subparsers.add_parser("add", help="add an arXiv, DOI, or OpenAlex seed")
    seed_add.add_argument("identifier")
    seed_subparsers.add_parser("list", help="list seed papers")
    seed_remove = seed_subparsers.add_parser("remove", help="remove a seed")
    seed_remove.add_argument("identifier")
    discover = history_subparsers.add_parser(
        "discover", help="discover topic, knowledge-map, and one-hop seed candidates"
    )
    discover.add_argument("--dry-run", action="store_true")
    discover.add_argument("--limit", type=int)
    rising = history_subparsers.add_parser(
        "rising", help="scan recent robotics-core journals for rising papers"
    )
    rising.add_argument("--dry-run", action="store_true")
    history_list = history_subparsers.add_parser(
        "list", help="list top scored historical candidates"
    )
    history_list.add_argument("--top", type=int, default=20)
    refresh = history_subparsers.add_parser(
        "refresh", help="refresh one historical candidate"
    )
    refresh.add_argument("identifier", nargs="?")
    refresh.add_argument(
        "--all",
        action="store_true",
        help="refresh every paper in the historical discovery pool",
    )

    dismiss_parser = subparsers.add_parser(
        "dismiss", help="manage papers marked not interested"
    )
    dismiss_subparsers = dismiss_parser.add_subparsers(
        dest="dismiss_command", required=True
    )
    dismiss_add = dismiss_subparsers.add_parser(
        "add", help="mark a paper as not interested"
    )
    dismiss_add.add_argument("identifier")
    dismiss_add.add_argument(
        "--reason", default="not_interested", help="feedback reason"
    )
    dismiss_subparsers.add_parser("list", help="list dismissed papers")
    dismiss_remove = dismiss_subparsers.add_parser(
        "remove", help="remove a dismissal"
    )
    dismiss_remove.add_argument("identifier")

    favorite_parser = subparsers.add_parser(
        "favorite", help="manage saved papers"
    )
    favorite_subparsers = favorite_parser.add_subparsers(
        dest="favorite_command", required=True
    )
    favorite_add = favorite_subparsers.add_parser(
        "add", help="save a paper to favorites"
    )
    favorite_add.add_argument("identifier")
    favorite_subparsers.add_parser("list", help="list favorite papers")
    favorite_remove = favorite_subparsers.add_parser(
        "remove", help="remove a favorite"
    )
    favorite_remove.add_argument("identifier")

    candidates_parser = subparsers.add_parser(
        "candidates", help="manage candidate metadata files"
    )
    candidates_subparsers = candidates_parser.add_subparsers(
        dest="candidates_command", required=True
    )
    prune = candidates_subparsers.add_parser(
        "prune", help="remove candidate files older than N days"
    )
    prune.add_argument("--older-than", type=int, default=30)

    feedback_parser = subparsers.add_parser(
        "feedback", help="apply feedback captured as a GitHub issue"
    )
    feedback_subparsers = feedback_parser.add_subparsers(
        dest="feedback_command", required=True
    )
    feedback_apply = feedback_subparsers.add_parser(
        "apply", help="apply not-interested/favorite lines from a file"
    )
    feedback_apply.add_argument("file")

    backtest_parser = subparsers.add_parser(
        "backtest", help="replay recommendation policy from local data only"
    )
    backtest_parser.add_argument("--from", dest="from_date", type=_date_argument, required=True)
    backtest_parser.add_argument("--to", dest="to_date", type=_date_argument, required=True)
    backtest_parser.add_argument(
        "--compare-actual",
        action="store_true",
        help="compare replay selections with immutable daily recommendation archives",
    )
    return parser


def _dismiss(project_root: Path, args: argparse.Namespace) -> int:
    data_dir = project_root / "data"
    storage = DismissalStorage(data_dir)
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    if args.dismiss_command == "list":
        entries = storage.load()
        if not entries:
            print("No dismissed papers.")
            return 0
        for entry in entries:
            print(
                f"{entry.canonical_paper_id}\t{entry.dismissed_at}\t"
                f"{entry.reason}\t{entry.title}"
            )
        return 0

    try:
        canonical = identifier_key(args.identifier)
    except HistoryStorageError as exc:
        print(f"Dismissal command failed: {exc}", file=sys.stderr)
        return 1

    if args.dismiss_command == "remove":
        try:
            removed = storage.remove(canonical)
        except PoolError as exc:
            print(f"Dismissal command failed: {exc}", file=sys.stderr)
            return 1
        print(f"Removed dismissal: {removed.canonical_paper_id} ({removed.title})")
        return 0

    title, topics = _dismissal_metadata(data_dir, canonical)
    try:
        storage.add(
            DismissalEntry(
                canonical_paper_id=canonical,
                title=title,
                topics=topics,
                reason=args.reason,
                dismissed_at=now,
            )
        )
    except PoolError as exc:
        print(f"Dismissal command failed: {exc}", file=sys.stderr)
        return 1
    print(f"Marked not interested: {canonical} ({title or 'title unknown'})")
    return 0


def _dismissal_metadata(data_dir: Path, canonical: str) -> tuple[str, list[str]]:
    papers = HistoricalPaperStorage(data_dir).load() + RisingCandidateStorage(data_dir).load()
    for paper in papers:
        if canonical in paper.aliases:
            return paper.title, paper.matched_topics
    recommendation_storage = RecommendationStorage(data_dir)
    for date_string in recommendation_storage.available_dates():
        daily = recommendation_storage.load(date_string)
        for entry in daily.recommendations:
            if canonical in entry.aliases or canonical == entry.canonical_paper_id:
                return entry.paper.title, entry.paper.matched_topics
    return "", []


def _favorite(project_root: Path, args: argparse.Namespace) -> int:
    data_dir = project_root / "data"
    storage = FavoriteStorage(data_dir)
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    if args.favorite_command == "list":
        entries = storage.load()
        if not entries:
            print("No favorite papers.")
            return 0
        for entry in entries:
            print(
                f"{entry.canonical_paper_id}\t{entry.saved_at}\t"
                f"{entry.title}"
            )
        return 0

    try:
        canonical = identifier_key(args.identifier)
    except HistoryStorageError as exc:
        print(f"Favorite command failed: {exc}", file=sys.stderr)
        return 1

    if args.favorite_command == "remove":
        try:
            removed = storage.remove(canonical)
        except PoolError as exc:
            print(f"Favorite command failed: {exc}", file=sys.stderr)
            return 1
        print(f"Removed favorite: {removed.canonical_paper_id} ({removed.title})")
        return 0

    metadata = _resolve_metadata(data_dir, canonical) or {}
    try:
        storage.add(
            FavoriteEntry(
                canonical_paper_id=canonical,
                title=metadata.get("title") or "",
                authors=list(metadata.get("authors") or []),
                publication_year=metadata.get("year"),
                source_name=str(metadata.get("source") or ""),
                abstract=str(metadata.get("abstract") or ""),
                landing_page_url=metadata.get("landing"),
                pdf_url=metadata.get("pdf"),
                openalex_url=metadata.get("openalex"),
                doi=metadata.get("doi"),
                saved_at=now,
            )
        )
    except PoolError as exc:
        print(f"Favorite command failed: {exc}", file=sys.stderr)
        return 1
    print(f"Saved to favorites: {canonical} ({metadata.get('title') or 'title unknown'})")
    return 0


def _candidates(project_root: Path, args: argparse.Namespace) -> int:
    if args.candidates_command != "prune":
        return 2
    candidate_dir = project_root / "data" / "candidates"
    cutoff = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(
        days=args.older_than
    )
    removed: list[str] = []
    if candidate_dir.exists():
        for path in sorted(candidate_dir.glob("*.json")):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
                removed.append(path.name)
    print(f"Removed {len(removed)} candidate file(s) older than {args.older_than} days")
    for name in removed:
        print(f" - {name}")
    return 0


def _feedback(project_root: Path, args: argparse.Namespace) -> int:
    if args.feedback_command != "apply":
        return 2
    path = Path(args.file)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"Unable to read feedback file {path}: {exc}", file=sys.stderr)
        return 1
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    result = apply_feedback_lines(lines, project_root / "data", now)
    print(f"Applied dismissals: {result.applied_dismissals}")
    print(f"Applied favorites: {result.applied_favorites}")
    print(f"Skipped (already recorded): {result.skipped}")
    if result.failed:
        print(f"Failed lines ({len(result.failed)}):")
        for line in result.failed:
            print(f" - {line}")
    return 0


def _backtest(project_root: Path, args: argparse.Namespace) -> int:
    try:
        profile = load_profile(project_root / "config" / "research_profile.yaml")
        result = OfflineBacktester(project_root, profile).run(
            from_date=args.from_date,
            to_date=args.to_date,
            compare_actual=args.compare_actual,
        )
    except (BacktestError, ConfigError, StorageError) as exc:
        print(f"Offline backtest failed: {exc}", file=sys.stderr)
        print("Production data, recommendation archives, and pages were not modified.", file=sys.stderr)
        return 1
    quantity = result.metrics["quantity"]
    print(
        f"Replayed {quantity['simulated_days']} days with "
        f"{quantity['total_recommendations']} recommendations "
        f"({quantity['average_recommendations_per_day']}/day)"
    )
    print(f"Markdown report: {result.markdown_path}")
    print(f"JSON report: {result.json_path}")
    return 0


def _run(project_root: Path, requested_date: date | None) -> int:
    try:
        profile = load_profile(project_root / "config" / "research_profile.yaml")
        if requested_date is not None:
            result = execute_reader_historical_run(
                project_root, requested_date, profile=profile
            )
        else:
            result = execute_reader_incremental_run(project_root, profile=profile)
    except (ArxivFetchError, ConfigError, StorageError) as exc:
        print(f"Paper Radar could not complete the run: {exc}", file=sys.stderr)
        print("Existing candidate state, reading pool, and pages were preserved.", file=sys.stderr)
        return 1

    print(f"Generated personal shortlist for {result.date}: {result.recommendation_count} recommendations")
    print(f"Background candidates scanned: {result.candidate_count}")
    print(f"Historical candidates considered: {result.historical_candidate_count}")
    print(f"Rising candidates considered: {result.rising_candidate_count}")
    if result.llm_analysis_count:
        print(f"LLM daily guide: {result.llm_analysis_count} papers analyzed")
    else:
        print(
            "LLM daily guide: not generated "
            "(no papers selected, feature disabled, or DEEPSEEK_API_KEY missing)"
        )
    print(f"Candidate metadata: {result.candidate_path}")
    print(f"Daily recommendations: {result.recommendation_path}")
    print(f"Site index: {result.index_path}")
    print(f"Archive: {result.archive_path}")
    return 0


def _pool(project_root: Path, args: argparse.Namespace) -> int:
    try:
        profile = load_profile(project_root / "config" / "research_profile.yaml")
        storage = ReadingPoolStorage(project_root / "data")
        base_id, _ = split_arxiv_id(args.arxiv_id) if hasattr(args, "arxiv_id") else ("", 1)
        if args.pool_command == "add":
            paper = ArxivClient(profile.fetch).fetch_by_id(base_id)
            score_paper(paper, profile)
            now = datetime.now(ZoneInfo(profile.timezone)).isoformat(timespec="seconds")
            entry = ReadingPoolEntry(
                base_arxiv_id=paper.base_id,
                title=paper.title,
                source="arXiv API",
                topic_tags=paper.matched_topics,
                recommendation_reason="Manually added via `pool add`",
                added_at=now,
                last_considered_at=None,
                recommended_at=None,
                recommendation_count=0,
                reading_status="unread",
                dismissed=False,
                priority=int(profile.recommendations["reading_pool"]["default_priority"]),
                paper=paper,
            )
            storage.add(entry)
            print(f"Added {paper.base_id}: {paper.title}")
            return 0
        if args.pool_command == "list":
            entries = storage.load()
            if not entries:
                print("Reading pool is empty.")
                return 0
            for entry in entries:
                marker = "dismissed" if entry.dismissed else entry.reading_status
                print(
                    f"{entry.base_arxiv_id}\t{marker}\tpriority={entry.priority}\t"
                    f"recommended={entry.recommendation_count}\t{entry.title}"
                )
            return 0
        if args.pool_command == "status":
            storage.set_status(base_id, args.status)
            print(f"Updated {base_id} to {args.status}")
            return 0
        if args.pool_command == "dismiss":
            storage.dismiss(base_id)
            print(f"Dismissed {base_id}")
            return 0
    except (ArxivFetchError, ConfigError, StorageError, PoolError) as exc:
        print(f"Reading pool command failed: {exc}", file=sys.stderr)
        return 1
    return 2


def _history(project_root: Path, args: argparse.Namespace) -> int:
    try:
        profile = load_profile(project_root / "config" / "research_profile.yaml")
        data_dir = project_root / "data"
        seed_storage = SeedStorage(data_dir)
        paper_storage = HistoricalPaperStorage(data_dir)
        now = datetime.now(ZoneInfo(profile.timezone))

        if args.history_command == "seed":
            if args.seed_command == "add":
                seed = seed_storage.add(
                    args.identifier, now.isoformat(timespec="seconds")
                )
                print(f"Added seed: {seed.identifier}")
                return 0
            if args.seed_command == "list":
                seeds = seed_storage.load()
                if not seeds:
                    print("Historical seed list is empty.")
                    return 0
                for seed in seeds:
                    title = f"\t{seed.title}" if seed.title else ""
                    print(f"{seed.identifier}\tadded={seed.added_at}{title}")
                return 0
            if args.seed_command == "remove":
                removed = seed_storage.remove(args.identifier)
                print(f"Removed seed: {removed.identifier}")
                return 0

        if args.history_command == "list":
            if args.top < 0:
                raise HistoryStorageError("--top must be non-negative")
            papers = paper_storage.top(args.top)
            if not papers:
                print("Historical discovery pool is empty.")
                return 0
            for paper in papers:
                score = (
                    f"{paper.historical_value_score:.1f}"
                    if paper.historical_value_score is not None
                    else "unknown"
                )
                citations = (
                    str(paper.cited_by_count)
                    if paper.cited_by_count is not None
                    else "unknown"
                )
                print(
                    f"{paper.canonical_paper_id}\tscore={score}\t"
                    f"citations={citations}\t{paper.title}"
                )
            return 0

        if args.history_command == "discover" and args.limit is not None and args.limit < 1:
            raise HistoryStorageError("--limit must be positive")
        if args.history_command == "discover" and args.dry_run:
            service = HistoricalDiscoveryService(data_dir, profile, None, now=now)
            plan = service.plan(limit=args.limit)
            print("Dry run only; no API calls or files were modified.")
            print(f"Topic searches: {plan.topic_query_count}")
            print(f"Knowledge-map searches: {plan.knowledge_map_query_count}")
            print(f"Seeds: {plan.seed_count}; expansion depth: {plan.expansion_depth}")
            print(f"Candidate limit: {plan.candidate_limit}")
            print(f"Estimated OpenAlex requests: {plan.estimated_request_count}")
            return 0

        if args.history_command == "rising":
            provider = OpenAlexProvider(
                profile.openalex,
                data_dir,
                read_only=bool(args.dry_run),
            )
            result = RisingDiscoveryService(
                data_dir, profile, provider, now=now
            ).scan(dry_run=bool(args.dry_run))
            _print_rising_scan(result, profile.rising_discovery["min_rising_score"])
            return 0

        provider = OpenAlexProvider(profile.openalex, data_dir)
        service = HistoricalDiscoveryService(data_dir, profile, provider, now=now)
        if args.history_command == "refresh" and args.all:
            result = service.refresh_all()
            print(f"Refreshed {result.refreshed_count} papers; {result.failed_count} failed")
            print(f"Historical discovery pool: {result.pool_count}")
            print(f"OpenAlex requests: {provider.run_request_count}")
            print(f"OpenAlex cache hits: {provider.run_cache_hits}")
            print(f"Remaining configured call budget: {result.remaining_call_budget}")
            return 0
        if args.history_command == "discover":
            result = service.discover(limit=args.limit)
            print(f"Historical candidates discovered this run: {result.discovered_count}")
            print(f"Historical discovery pool: {result.pool_count}")
            print(f"OpenAlex requests: {result.request_count}")
            print(f"OpenAlex cache hits: {result.cache_hits}")
            print(f"Remaining configured call budget: {result.remaining_call_budget}")
            if result.failed_seed_ids:
                print(
                    "Seeds that could not be resolved: "
                    + ", ".join(result.failed_seed_ids)
                )
            print(f"Saved: {result.pool_path}")
            return 0
        if args.history_command == "refresh":
            if not args.identifier:
                raise HistoryStorageError("history refresh requires an identifier or --all")
            paper = service.refresh(args.identifier)
            print(f"Refreshed {paper.canonical_paper_id}: {paper.title}")
            print(f"OpenAlex requests: {provider.run_request_count}")
            print(f"OpenAlex cache hits: {provider.run_cache_hits}")
            return 0
    except (
        ConfigError,
        StorageError,
        HistoryStorageError,
        HistoricalProviderError,
        ValueError,
    ) as exc:
        print(f"Historical discovery command failed: {exc}", file=sys.stderr)
        print("Existing historical data, recommendation state, and pages were preserved.", file=sys.stderr)
        return 1
    return 2


def _print_rising_scan(result: RisingScanResult, threshold: float) -> None:
    mode = "DRY RUN (read-only)" if result.dry_run else "WEEKLY UPDATE"
    print(f"Rising Papers scan — {mode}")
    print(f"Scan date: {result.scan_date}")
    print(f"Date window: {result.from_date} to {result.to_date}")
    print(f"Works scanned: {result.scanned_count}")
    print(f"After basic filters: {result.candidate_count}")
    print(f"Eligible at rising_score ≥ {float(threshold):.1f}: {result.eligible_count}")
    quantiles = result.quantiles
    print(
        "Rising score distribution: "
        + " / ".join(
            f"{key.upper()}={value if value is not None else 'unavailable'}"
            for key, value in quantiles.items()
        )
    )
    print("\nPer-source audit:")
    for source in result.per_source:
        reported = (
            str(source["reported_total"])
            if source["reported_total"] is not None
            else "unknown"
        )
        print(
            f"- {source['name']} ({source['source_id']}): "
            f"scanned={source['works_scanned']}, reported={reported}, "
            f"pages={source['pages']}, limit={source['scan_limit']}, "
            f"truncated={source['truncated']}, basic={source['after_basic_filters']}, "
            f"rising_eligible={source['eligible_rising']}"
        )
        print(
            f"  recent 60 days: scanned={source['recent_60_scanned']}, "
            f"journal_recent eligible={source['recent_60_journal_eligible']}"
        )
        for paper in source["recent_60_top_research_fit"]:
            print(
                f"  · fit={paper['research_fit']} {paper['publication_date']} — "
                f"{paper['title']}"
            )
        print("  newest source records:")
        for paper in source["newest_examples"]:
            print(
                f"  · {paper['openalex_id'] or 'unknown'} | "
                f"{paper['publication_date'] or 'unknown'} "
                f"({paper['publication_year'] if paper['publication_year'] is not None else 'unknown'}) | "
                f"{paper['source'] or 'unknown'} | DOI={paper['doi'] or 'unknown'} | "
                f"{paper['title']}"
            )
    print("\nTop 10 Rising signals (attention audit, not a quality ranking):")
    for index, paper in enumerate(result.top_candidates, 1):
        observed = paper["observed_growth"] or {}
        observed_rate = observed.get("blended_citations_per_day")
        print(
            f"{index}. {paper['title']}\n"
            f"   source={paper['source']}; published={paper['publication_date']}; "
            f"age={paper['age_days']}d; citations={paper['cited_by_count'] if paper['cited_by_count'] is not None else 'unknown'}; "
            f"FWCI={paper['fwci'] if paper['fwci'] is not None else 'unknown'}; "
            f"percentile={paper['citation_normalized_percentile'] if paper['citation_normalized_percentile'] is not None else 'unknown'}; "
            f"velocity={paper['smoothed_citation_velocity_per_month'] if paper['smoothed_citation_velocity_per_month'] is not None else 'unknown'}/month; "
            f"observed={observed_rate if observed_rate is not None else 'cold-start'}; "
            f"research_fit={paper['research_fit']}; rising_score={paper['rising_score']}; "
            f"threshold_eligible={paper['threshold_eligible']}"
        )
    print("\nTop 30 Rising signal audit (JSON Lines; attention, not quality):")
    diagnostic_fields = (
        "title",
        "source",
        "rising_score",
        "research_fit",
        "raw_core_topics",
        "matched_core_topics",
        "generic_only_core_topics",
        "matched_support_topics",
        "matched_strong_keywords",
        "matched_generic_keywords",
        "classification",
        "domain_affinity",
        "domain_affinity_adjustment",
        "threshold_eligible",
    )
    for index, paper in enumerate(result.top_diagnostics, 1):
        print(
            json.dumps(
                {"rank": index, **{key: paper[key] for key in diagnostic_fields}},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    bubble = result.information_bubble
    print("\nInformation-bubble check (top basic-filtered Rising signals):")
    print(
        f"sample={bubble['sample_size']}; core={bubble['core']}; "
        f"model-based-support-only={bubble['model_based_support_only']}; "
        f"outside-current-core={bubble['outside_current_core']}"
    )
    print(f"OpenAlex requests: {result.request_count}; cache hits: {result.cache_hits}")
    if result.dry_run:
        print("No snapshots, candidate state, provider stats, or cache files were written.")
    else:
        print(f"Citation snapshots: {result.snapshot_path}")
        print(f"Rising candidate pool: {result.candidate_path}")


def _serve(project_root: Path, host: str, port: int) -> int:
    site_dir = project_root / "site"
    if not (site_dir / "index.html").exists():
        print("site/index.html does not exist; run `python -m paper_radar run` first.", file=sys.stderr)
        return 1
    handler = partial(SimpleHTTPRequestHandler, directory=site_dir)
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"Unable to start preview server on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    print(f"Paper Radar is available at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview server stopped.")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    project_root = Path.cwd()
    load_project_dotenv(project_root)
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(project_root, args.date)
    if args.command == "serve":
        return _serve(project_root, args.host, args.port)
    if args.command == "pool":
        return _pool(project_root, args)
    if args.command == "history":
        return _history(project_root, args)
    if args.command == "dismiss":
        return _dismiss(project_root, args)
    if args.command == "favorite":
        return _favorite(project_root, args)
    if args.command == "candidates":
        return _candidates(project_root, args)
    if args.command == "feedback":
        return _feedback(project_root, args)
    if args.command == "backtest":
        return _backtest(project_root, args)
    return 2
