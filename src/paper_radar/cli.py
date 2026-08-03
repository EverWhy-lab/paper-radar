from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_radar.config import ConfigError, load_profile
from paper_radar.fetchers.arxiv import ArxivClient, ArxivFetchError, split_arxiv_id
from paper_radar.reader_models import READING_STATUSES, ReadingPoolEntry
from paper_radar.reader_pipeline import (
    execute_reader_historical_run,
    execute_reader_incremental_run,
)
from paper_radar.reader_storage import PoolError, ReadingPoolStorage
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
        description="Build the local EverWhy Paper Radar site.",
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
    return parser


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
    print(f"EverWhy Paper Radar is available at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview server stopped.")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path.cwd()
    if args.command == "run":
        return _run(project_root, args.date)
    if args.command == "serve":
        return _serve(project_root, args.host, args.port)
    if args.command == "pool":
        return _pool(project_root, args)
    return 2
