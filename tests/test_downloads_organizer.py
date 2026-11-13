import datetime as dt
import os
from pathlib import Path

from _downloads_organizer import (
    classify_extension,
    classify_pdf,
    collect_eligible_files,
    organize_files,
)


NOW = dt.datetime(2026, 7, 12, tzinfo=dt.timezone.utc)


def age(path: Path, days: int) -> None:
    stamp = NOW.timestamp() - days * 86400
    os.utime(path, (stamp, stamp))


def test_collects_only_safe_old_top_level_files(tmp_path):
    old = tmp_path / "old.txt"
    old.write_text("old")
    age(old, 31)
    young = tmp_path / "young.txt"
    young.write_text("young")
    age(young, 29)
    partial = tmp_path / "file.crdownload"
    partial.write_text("partial")
    age(partial, 90)
    hidden = tmp_path / ".hidden"
    hidden.write_text("hidden")
    age(hidden, 90)
    nested = tmp_path / "folder"
    nested.mkdir()
    nested_file = nested / "nested.txt"
    nested_file.write_text("nested")
    age(nested_file, 90)

    assert collect_eligible_files(tmp_path, 30, NOW) == [old]


def test_classification_is_conservative():
    assert classify_extension(Path("photo.heic")) == "Images"
    assert classify_extension(Path("unknown.xyz")) == "Review"
    assert classify_pdf("Laboratory Report") == "Medical"
    assert classify_pdf("Course Syllabus") == "Education"
    assert classify_pdf("Meeting Notes") == "General"
    assert classify_pdf(None) == "Review"


def test_preview_does_not_change_files(tmp_path):
    source = tmp_path / "Downloads"
    destination = tmp_path / "Archive"
    source.mkdir()
    photo = source / "photo.jpg"
    photo.write_bytes(b"image")
    age(photo, 40)

    results = organize_files(source, destination, 30, apply=False, now=NOW)

    assert photo.exists()
    assert not destination.exists()
    assert results[0].status == "planned"
    assert Path(results[0].destination) == destination / "Media" / "Images" / "2026" / "photo.jpg"


def test_apply_moves_file_into_hierarchy(tmp_path):
    source = tmp_path / "Downloads"
    destination = tmp_path / "Archive"
    source.mkdir()
    sheet = source / "budget.xlsx"
    sheet.write_bytes(b"sheet")
    age(sheet, 40)

    results = organize_files(source, destination, 30, apply=True, now=NOW)

    target = destination / "Documents" / "Spreadsheets" / "2026" / "budget.xlsx"
    assert target.read_bytes() == b"sheet"
    assert not sheet.exists()
    assert results[0].status == "moved"


def test_pdf_rename_and_document_classification(tmp_path):
    source = tmp_path / "Downloads"
    destination = tmp_path / "Archive"
    source.mkdir()
    pdf = source / "scan.pdf"
    pdf.write_bytes(b"pdf")
    age(pdf, 40)

    def renamer(path, apply):
        return path.with_name("20250101 Clinic Laboratory Report.pdf"), "Laboratory Report", None

    results = organize_files(source, destination, 30, apply=False, pdf_renamer=renamer, now=NOW)

    assert results[0].category == "Medical"
    assert results[0].renamed_to == "20250101 Clinic Laboratory Report.pdf"
    assert Path(results[0].destination) == (
        destination / "Documents" / "Medical" / "2026"
        / "20250101 Clinic Laboratory Report.pdf"
    )


def test_collision_is_sent_to_review_without_overwrite(tmp_path):
    source = tmp_path / "Downloads"
    destination = tmp_path / "Archive"
    source.mkdir()
    image = source / "same.jpg"
    image.write_bytes(b"new")
    age(image, 40)
    target = destination / "Media" / "Images" / "2026" / "same.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")

    results = organize_files(source, destination, 30, apply=True, now=NOW)

    assert results[0].status == "review"
    assert image.exists()
    assert target.read_bytes() == b"existing"


def test_classifies_canonical_pdf_without_renamer(tmp_path):
    source = tmp_path / "Downloads"
    destination = tmp_path / "Archive"
    source.mkdir()
    pdf = source / "20260301 ACME Invoice.pdf"
    pdf.write_bytes(b"pdf")
    age(pdf, 40)

    results = organize_files(source, destination, 30, apply=False, pdf_renamer=None, now=NOW)

    assert results[0].category == "Financial"
    assert Path(results[0].destination) == destination / "Documents" / "Financial" / "2026" / "20260301 ACME Invoice.pdf"
