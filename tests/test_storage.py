from dataclasses import replace

import pytest

from paper_radar.fetchers.arxiv import parse_atom
from paper_radar.models import SeenState
from paper_radar.storage import RadarStorage, StorageError


def test_seen_state_round_trip_and_versions(tmp_path, atom_xml) -> None:
    storage = RadarStorage(tmp_path / "data")
    papers = parse_atom(atom_xml)
    seen = storage.updated_seen(SeenState(), papers, "2026-08-03T20:00:00+08:00")
    storage.save_seen(seen)
    old_seen = storage.load_seen()
    v3 = replace(
        papers[0],
        arxiv_id="2608.00001v3",
        version=3,
        updated="2026-08-04T00:00:00Z",
    )
    updated = storage.updated_seen(old_seen, [v3], "2026-08-04T12:00:00+08:00")

    assert updated.papers["2608.00001"]["latest_version"] == 3
    assert updated.papers["2608.00002"]["latest_version"] == 2
    assert updated.papers["2608.00001"]["first_seen_at"] == "2026-08-03T20:00:00+08:00"
    assert updated.papers["2608.00001"]["last_seen_at"] == "2026-08-04T12:00:00+08:00"


def test_corrupt_seen_json_has_clear_error(tmp_path) -> None:
    storage = RadarStorage(tmp_path / "data")
    storage.seen_path.parent.mkdir(parents=True)
    storage.seen_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(StorageError, match="Unable to read seen IDs"):
        storage.load_seen()


def test_old_v1_seen_ids_are_migrated(tmp_path, atom_xml) -> None:
    import json

    storage = RadarStorage(tmp_path / "data")
    storage.daily_dir.mkdir(parents=True)
    papers = parse_atom(atom_xml)
    (storage.daily_dir / "2026-07-31.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-31T10:00:00+08:00",
                "papers": [
                    {
                        "base_id": paper.base_id,
                        "published": paper.published,
                        "updated": paper.updated,
                    }
                    for paper in papers
                ],
            }
        ),
        encoding="utf-8",
    )
    storage.seen_path.write_text(
        '{"schema_version":1,"papers":{"2608.00001":{"version":1,'
        '"arxiv_id":"2608.00001v1","updated":"2026-08-03T08:30:00Z"}}}',
        encoding="utf-8",
    )

    migrated = storage.load_seen(migrated_at="2026-08-04T10:00:00+08:00")
    item = migrated.papers["2608.00001"]

    assert migrated.schema_version == 2
    assert item["base_arxiv_id"] == "2608.00001"
    assert item["latest_version"] == 1
    assert item["published_at"] == papers[0].published
    assert item["updated_at"] == "2026-08-03T08:30:00Z"
    assert item["first_seen_at"] == "2026-07-31T10:00:00+08:00"
    assert item["last_seen_at"] == "2026-07-31T10:00:00+08:00"
