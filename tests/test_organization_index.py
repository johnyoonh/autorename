import datetime as dt
import json
import os
from pathlib import Path

from _downloads_organizer import deduplicate_intake, organize_indexed, undo_organization
from _organization_index import OrganizationIndex, load_root_structure


NOW = dt.datetime(2026, 9, 6, tzinfo=dt.timezone.utc)


def _old(path: Path, days: int = 31) -> None:
    stamp = NOW.timestamp() - days * 86400
    os.utime(path, (stamp, stamp))


def _structure(tmp_path: Path, cloud: Path) -> Path:
    path = tmp_path / "root-structure.yaml"
    path.write_text(
        "\n".join([
            "version: 1",
            "roots:",
            "  - id: cloud",
            f"    path: {json.dumps(str(cloud))}",
            "    priority: 10",
            "    managed_inbox: Filed",
            "    destinations:",
            "      - id: finance",
            "        path: Documents/Finance",
            "        tags: [tax, invoice, receipt]",
        ]),
        encoding="utf-8",
    )
    return path


def test_index_refresh_retains_hash_for_unchanged_file(tmp_path):
    cloud = tmp_path / "Cloud"
    finance = cloud / "Documents" / "Finance"
    finance.mkdir(parents=True)
    existing = finance / "receipt.pdf"
    existing.write_bytes(b"same")
    roots, _destinations = load_root_structure(_structure(tmp_path, cloud))
    index_path = tmp_path / "index.sqlite"

    with OrganizationIndex(index_path) as index:
        index.refresh(roots)
        incoming = tmp_path / "incoming.pdf"
        incoming.write_bytes(b"same")
        assert index.exact_match(incoming, roots) == existing
        assert index.refresh(roots) == {"cloud": 3}
        assert index.exact_match(incoming, roots) == existing


def test_flatten_group_place_and_undo(tmp_path):
    cloud = tmp_path / "Cloud"
    (cloud / "Documents" / "Finance").mkdir(parents=True)
    downloads = tmp_path / "Downloads"
    nested = downloads / "Tax Packet" / "Original Folder"
    nested.mkdir(parents=True)
    source = nested / "tax invoice.txt"
    source.write_text("invoice", encoding="utf-8")
    _old(source)
    roots, destinations = load_root_structure(_structure(tmp_path, cloud))
    log = tmp_path / "actions.jsonl"

    with OrganizationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(roots)
        run_id, results = organize_indexed(
            downloads, roots, destinations, index, apply=True, now=NOW, log_path=log
        )

    target = cloud / "Documents" / "Finance" / "tax invoice.txt"
    assert results[0].status == "moved"
    assert target.read_text(encoding="utf-8") == "invoice"
    assert not (downloads / "Tax Packet").exists()
    assert undo_organization(log, run_id) == [f"restored {source}"]
    assert source.read_text(encoding="utf-8") == "invoice"


def test_exact_duplicate_is_deleted_touched_and_recreatable(tmp_path):
    cloud = tmp_path / "Cloud"
    finance = cloud / "Documents" / "Finance"
    finance.mkdir(parents=True)
    canonical = finance / "receipt.txt"
    canonical.write_bytes(b"identical")
    old_mtime = int(NOW.timestamp()) - 100000
    os.utime(canonical, (old_mtime, old_mtime))
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    incoming = downloads / "receipt copy.txt"
    incoming.write_bytes(b"identical")
    _old(incoming)
    roots, destinations = load_root_structure(_structure(tmp_path, cloud))
    log = tmp_path / "actions.jsonl"

    with OrganizationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(roots)
        run_id, results = organize_indexed(
            downloads, roots, destinations, index, apply=True, now=NOW, log_path=log
        )

    assert results[0].status == "duplicate"
    assert not incoming.exists()
    assert canonical.stat().st_mtime > old_mtime
    undo_organization(log, run_id)
    assert incoming.read_bytes() == b"identical"
    assert canonical.stat().st_mtime_ns == old_mtime * 1_000_000_000


def test_preview_never_changes_duplicate_or_nested_source(tmp_path):
    cloud = tmp_path / "Cloud"
    cloud.mkdir()
    downloads = tmp_path / "Downloads"
    nested = downloads / "Bundle" / "Deep"
    nested.mkdir(parents=True)
    source = nested / "notes.txt"
    source.write_text("notes", encoding="utf-8")
    _old(source)
    roots, destinations = load_root_structure(_structure(tmp_path, cloud))

    with OrganizationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(roots)
        _run_id, results = organize_indexed(downloads, roots, destinations, index, now=NOW)

    assert results[0].status == "planned"
    assert source.exists()
    assert not (cloud / "Filed").exists()


def test_intake_requires_two_observations_before_deduplication(tmp_path):
    cloud = tmp_path / "Cloud"
    cloud.mkdir()
    canonical = cloud / "guide.txt"
    canonical.write_bytes(b"guide")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    incoming = downloads / "guide copy.txt"
    incoming.write_bytes(b"guide")
    roots, _destinations = load_root_structure(_structure(tmp_path, cloud))

    with OrganizationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(roots)
        _run_id, first = deduplicate_intake(downloads, roots, index, apply=True, settle_seconds=0)
        assert first == []
        assert incoming.exists()
        _run_id, second = deduplicate_intake(downloads, roots, index, apply=True, settle_seconds=0)

    assert second[0].status == "duplicate"
    assert not incoming.exists()
