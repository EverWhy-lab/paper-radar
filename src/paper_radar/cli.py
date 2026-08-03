from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_radar.config import ConfigError, load_profile
from paper_radar.fetchers.arxiv import ArxivFetchError
from paper_radar.pipeline import execute_incremental_run, execute_run
from paper_radar.rendering import SiteRenderer
from paper_radar.storage import RadarStorage, StorageError


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
    run_parser = subparsers.add_parser("run", help="fetch, score, save, and render one daily radar")
    run_parser.add_argument("--date", type=_date_argument, help="Asia/Shanghai date (YYYY-MM-DD)")

    serve_parser = subparsers.add_parser("serve", help="serve the generated static site")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8000, type=int)
    return parser


def _run(project_root: Path, requested_date: date | None) -> int:
    try:
        profile = load_profile(project_root / "config" / "research_profile.yaml")
        if requested_date is not None:
            result = execute_run(project_root, requested_date, profile=profile)
        else:
            result = execute_incremental_run(project_root, profile=profile)
    except (ArxivFetchError, ConfigError, StorageError) as exc:
        print(f"Paper Radar could not complete the run: {exc}", file=sys.stderr)
        index_path = project_root / "site" / "index.html"
        if not index_path.exists():
            try:
                profile = load_profile(project_root / "config" / "research_profile.yaml")
                target_date = requested_date or datetime.now(ZoneInfo(profile.timezone)).date()
                generated_at = datetime.now(ZoneInfo(profile.timezone)).isoformat(timespec="seconds")
                renderer = SiteRenderer(
                    project_root / "site",
                    RadarStorage(project_root / "data"),
                    profile,
                )
                renderer.render_unavailable(
                    target_date.isoformat(),
                    generated_at,
                    "The arXiv API is temporarily unavailable. No paper metadata was replaced.",
                )
                print(f"Created an honest offline status page at {index_path}", file=sys.stderr)
            except Exception as render_error:  # pragma: no cover - final safety net
                print(f"Unable to create the offline status page: {render_error}", file=sys.stderr)
        return 1

    print(
        f"Generated radar for {result.date}: "
        f"{result.new_submission_count} new submissions, "
        f"{result.version_update_count} version updates "
        f"from {result.candidate_count} rolling-window candidates"
    )
    print(f"Daily archive now contains {result.paper_count} paper events")
    print(f"Daily data: {result.daily_path}")
    print(f"Site index: {result.index_path}")
    print(f"Archive: {result.archive_path}")
    return 0


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
    return 2
