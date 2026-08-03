from dataclasses import replace

import pytest

from paper_radar.fetchers.arxiv import parse_atom
from paper_radar.models import DailyRadar
from paper_radar.models import SeenState
from paper_radar.storage import RadarStorage, StorageError


def test_json_daily_round_trip_and_seen_versions(tmp_path, atom_xml: str) -> None:
    storage = RadarStorage(tmp_path / "data")
    papers = parse_atom(atom_xml)
    radar = DailyRadar(
        date="2026-08-03",
        generated_at="2026-08-03T20:00:00+08:00",
        papers=papers,
    )

    daily_path = storage.save_daily(radar)
    loaded = storage.load_daily("2026-08-03")
    assert loaded.to_dict() == radar.to_dict()
    assert daily_path.read_text(encoding="utf-8").endswith("\n")
    assert storage.available_dates() == ["2026-08-03"]

    seen = storage.updated_seen(SeenState(), papers, "2026-08-03T20:00:00+08:00")
    storage.save_seen(seen)
    old_seen = storage.load_seen()
    v3 = replace(papers[0], arxiv_id="2608.00001v3", version=3, updated="2026-08-04T00:00:00Z")
    updated = storage.updated_seen(old_seen, [v3], "2026-08-04T12:00:00+08:00")
    assert updated.papers["2608.00001"]["latest_version"] == 3
    assert updated.papers["2608.00002"]["latest_version"] == 2
    assert updated.papers["2608.00001"]["first_seen_at"] == "2026-08-03T20:00:00+08:00"
    assert updated.papers["2608.00001"]["last_seen_at"] == "2026-08-04T12:00:00+08:00"


def test_corrupt_json_has_clear_error(tmp_path) -> None:
    storage = RadarStorage(tmp_path / "data")
    path = storage.daily_path("2026-08-03")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(StorageError, match="Unable to read daily radar"):
        storage.load_daily("2026-08-03")


def test_old_seen_ids_are_migrated_from_daily_metadata(tmp_path, atom_xml: str) -> None:
    storage = RadarStorage(tmp_path / "data")
    papers = parse_atom(atom_xml)
    storage.save_daily(
        DailyRadar(
            date="2026-08-03",
            generated_at="2026-08-03T20:00:00+08:00",
            papers=papers,
        )
    )
    storage.seen_path.write_text(
        '{"schema_version":1,"papers":{"2608.00001":{"version":1,"arxiv_id":"2608.00001v1","updated":"2026-08-03T08:30:00Z"}}}',
        encoding="utf-8",
    )

    migrated = storage.load_seen(migrated_at="2026-08-04T10:00:00+08:00")
    item = migrated.papers["2608.00001"]

    assert migrated.schema_version == 2
    assert item == {
        "base_arxiv_id": "2608.00001",
        "latest_version": 1,
        "latest_arxiv_id": "2608.00001v1",
        "published_at": "2026-08-03T08:30:00Z",
        "updated_at": "2026-08-03T08:30:00Z",
        "first_seen_at": "2026-08-03T20:00:00+08:00",
        "last_seen_at": "2026-08-03T20:00:00+08:00",
    }
