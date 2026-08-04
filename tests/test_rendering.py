from dataclasses import replace
from pathlib import Path

from paper_radar.fetchers.arxiv import parse_atom
from paper_radar.models import DailyRadar
from paper_radar.rendering import SiteRenderer, partition_papers
from paper_radar.reporting import build_relevance_audit
from paper_radar.scoring import score_papers
from paper_radar.storage import RadarStorage


def test_partition_has_no_duplicates(atom_xml: str, profile) -> None:
    papers = score_papers(parse_atom(atom_xml), profile)
    sections = partition_papers(papers, profile)
    ids = [paper.base_id for values in sections.values() for paper in values]
    assert len(ids) == len(set(ids)) == 2


def test_partition_backfills_requested_section_sizes(atom_xml: str, profile) -> None:
    source = parse_atom(atom_xml)[0]
    papers = [
        replace(
            source,
            arxiv_id=f"2608.{index:05d}v1",
            base_id=f"2608.{index:05d}",
            research_fit=max(0, 70 - index * 4),
            video_potential=index,
        )
        for index in range(12)
    ]

    sections = partition_papers(papers, profile)

    assert len(sections["must_read"]) == 3
    assert 5 <= len(sections["highly_relevant"]) <= 10
    assert 3 <= len(sections["expanded"]) <= 5
    ids = [paper.base_id for values in sections.values() for paper in values]
    assert len(ids) == len(set(ids)) == 12


def test_html_index_and_archive_generation(tmp_path: Path, atom_xml: str, profile) -> None:
    storage = RadarStorage(tmp_path / "data")
    papers = score_papers(parse_atom(atom_xml), profile)
    papers[0].title = "Whole-Body <Control> & Safety"
    storage.save_daily(
        DailyRadar(
            date="2026-08-03",
            generated_at="2026-08-03T20:00:00+08:00",
            papers=papers,
        )
    )
    renderer = SiteRenderer(tmp_path / "site", storage, profile)

    index_path, archive_path = renderer.render("2026-08-03")
    index = index_path.read_text(encoding="utf-8")
    archive = archive_path.read_text(encoding="utf-8")

    assert "Paper Radar" in index
    assert "Whole-Body &lt;Control&gt; &amp; Safety" in index
    assert "Original abstract" in index
    assert "Why these scores?" in index
    assert "research fit" in index
    assert "video potential" in index
    assert "Asia/Shanghai" in index
    assert "https://arxiv.org/bibtex/" in index
    assert 'href="assets/styles.css"' in index
    assert 'href="assets/favicon.svg"' in index
    assert 'href="../assets/styles.css"' in archive
    assert (tmp_path / "site" / "assets" / "styles.css").exists()
    assert (tmp_path / "site" / "assets" / "app.js").exists()
    assert (tmp_path / "site" / "assets" / "favicon.svg").exists()

    styles = (tmp_path / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
    assert "@media (max-width: 780px)" in styles
    assert "@media (max-width: 480px)" in styles
    assert "overflow-wrap: anywhere" in styles
    app = (tmp_path / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "searchableText.set" in app
    assert "requestAnimationFrame" in app


def test_empty_page_is_truthful(tmp_path: Path, profile) -> None:
    storage = RadarStorage(tmp_path / "data")
    renderer = SiteRenderer(tmp_path / "site", storage, profile)

    path = renderer.render_unavailable(
        "2026-08-03",
        "2026-08-03T20:00:00+08:00",
        "API unavailable",
    )
    html = path.read_text(encoding="utf-8")
    assert "API unavailable" in html
    assert "never invents papers" in html
    assert not storage.daily_path("2026-08-03").exists()


def test_empty_latest_day_keeps_recent_nonempty_radar_on_homepage(
    tmp_path: Path, atom_xml: str, profile
) -> None:
    storage = RadarStorage(tmp_path / "data")
    papers = score_papers(parse_atom(atom_xml), profile)
    storage.save_daily(
        DailyRadar(
            date="2026-07-31",
            generated_at="2026-07-31T10:15:00+08:00",
            papers=papers,
            run_mode="historical",
        )
    )
    storage.save_daily(
        DailyRadar(
            date="2026-08-03",
            generated_at="2026-08-03T10:15:00+08:00",
            papers=[],
            run_mode="incremental",
            run_new_submission_count=0,
            run_version_update_count=0,
        )
    )

    renderer = SiteRenderer(tmp_path / "site", storage, profile)
    index_path, empty_archive_path = renderer.render("2026-08-03")
    index = index_path.read_text(encoding="utf-8")
    empty_archive = empty_archive_path.read_text(encoding="utf-8")

    assert "本次运行未发现新论文" in index
    assert papers[0].title in index
    assert 'href="archive/2026-08-03.html"' in index
    assert 'href="archive/2026-07-31.html"' in index
    assert papers[0].title not in empty_archive
    assert "0 new submissions" in empty_archive


def test_relevance_audit_contains_only_rule_derived_sections(atom_xml: str, profile) -> None:
    papers = score_papers(parse_atom(atom_xml), profile)
    radar = DailyRadar(
        date="2026-08-03",
        generated_at="2026-08-03T20:00:00+08:00",
        papers=papers,
    )

    report = build_relevance_audit(radar, profile)

    assert "Relevance Audit — 2026-08-03" in report
    assert papers[0].title in report
    assert "Research keyword contributions" in report
    assert "Why this section" in report
    assert "uses no AI-generated summary" in report
