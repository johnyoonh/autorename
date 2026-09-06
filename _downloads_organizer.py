"""Conservative organizer for files aging out of Downloads."""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from _organization_index import DestinationDefinition, OrganizationIndex, RootDefinition, sha256_file


PARTIAL_SUFFIXES = {".crdownload", ".download", ".part"}

EXTENSION_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".svg", ".dng"},
    "Video": {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp"},
    "Audio": {".mp3", ".m4a", ".wav", ".aac", ".flac"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"},
    "Installers": {".dmg", ".pkg", ".msi", ".exe", ".xpi"},
    "Books": {".epub", ".mobi", ".azw", ".azw3"},
    "Spreadsheets": {".xlsx", ".xls", ".csv", ".tsv", ".numbers"},
    "General": {".doc", ".docx", ".rtf", ".txt", ".md", ".pages"},
}

PDF_CATEGORY_KEYWORDS = {
    "Financial": {"invoice", "receipt", "tax", "statement", "financial", "bank", "insurance"},
    "Medical": {"medical", "health", "laboratory", "lab", "prescription", "patient"},
    "Education": {"education", "lecture", "exam", "course", "syllabus", "assignment", "school"},
    "Employment": {"resume", "employment", "offer", "payroll", "benefits"},
}


@dataclass
class OrganizeResult:
    source: str
    destination: str | None
    status: str
    category: str | None = None
    reason: str | None = None
    renamed_to: str | None = None


@dataclass
class FileGroup:
    name: str
    files: list[Path]
    tokens: set[str]


_TOKEN_STOPWORDS = {
    "and", "copy", "document", "download", "file", "final", "from", "image",
    "new", "of", "pdf", "scan", "screenshot", "the", "to", "untitled",
}


def semantic_tokens(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]{2,}", value.casefold()))
    return {token for token in tokens if token not in _TOKEN_STOPWORDS and not re.fullmatch(r"\d{6,}", token)}


def collect_eligible_files(source: Path, older_than_days: int, now: dt.datetime | None = None) -> list[Path]:
    """Return safe, top-level files older than the configured threshold."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now.timestamp() - older_than_days * 86400
    eligible: list[Path] = []
    for path in source.iterdir():
        if path.name.startswith(".") or path.is_symlink() or not path.is_file():
            continue
        if path.name.startswith("~$") or path.suffix.lower() in PARTIAL_SUFFIXES:
            continue
        if path.stat().st_mtime <= cutoff:
            eligible.append(path)
    return sorted(eligible, key=lambda p: (p.stat().st_mtime, p.name.lower()))


def collect_eligible_tree_files(
    source: Path,
    older_than_days: int,
    now: dt.datetime | None = None,
    maximum: int = 100,
) -> list[Path]:
    """Collect ordinary files recursively without following hidden paths or symlinks."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now.timestamp() - older_than_days * 86400
    eligible: list[Path] = []
    for current, dirnames, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            name for name in dirnames
            if not name.startswith(".") and not (current_path / name).is_symlink()
            and (current_path / name).suffix.casefold() not in {".app", ".bundle", ".framework"}
        ]
        for name in filenames:
            path = current_path / name
            if name.startswith(".") or name.startswith("~$") or path.is_symlink():
                continue
            if path.suffix.casefold() in PARTIAL_SUFFIXES:
                continue
            try:
                if path.stat().st_mtime <= cutoff:
                    eligible.append(path)
            except OSError:
                continue
            if len(eligible) > maximum:
                raise ValueError(f"unattended organization is limited to {maximum} files")
    return sorted(eligible, key=lambda path: (path.stat().st_mtime, str(path).casefold()))


def group_files(source: Path, files: list[Path]) -> list[FileGroup]:
    """Group downloaded trees and semantically similar loose files."""
    buckets: dict[str, list[Path]] = {}
    loose: list[Path] = []
    for path in files:
        relative = path.relative_to(source)
        if len(relative.parts) > 1:
            buckets.setdefault(relative.parts[0], []).append(path)
        else:
            loose.append(path)

    # Loose files join when they share a meaningful token; otherwise they remain singletons.
    loose_groups: list[list[Path]] = []
    for path in loose:
        tokens = semantic_tokens(path.stem)
        match = next((group for group in loose_groups if tokens & semantic_tokens(" ".join(p.stem for p in group))), None)
        if match is None:
            loose_groups.append([path])
        else:
            match.append(path)
    for group in loose_groups:
        key = _group_name(group[0].stem, group)
        buckets.setdefault(key, []).extend(group)

    return [
        FileGroup(_group_name(name, paths), paths, semantic_tokens(name + " " + " ".join(p.stem for p in paths)))
        for name, paths in sorted(buckets.items())
    ]


def _group_name(seed: str, paths: list[Path]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]+", " ", seed).strip(" ._-")
    if cleaned:
        return cleaned[:80]
    return paths[0].stem[:80] or "Unsorted"


def score_destination(
    group: FileGroup,
    destination: DestinationDefinition,
    existing_names: Iterable[str] = (),
) -> float:
    destination_tokens = semantic_tokens(
        " ".join((destination.id, str(destination.path), *destination.tags, *destination.aliases))
    )
    overlap = group.tokens & destination_tokens
    score = 0.0
    if overlap:
        score = 0.70 + min(0.15, 0.05 * (len(overlap) - 1))
    existing_tokens = semantic_tokens(" ".join(existing_names))
    if group.tokens & existing_tokens:
        score = max(score, 0.80)
    return min(score, 1.0)


def choose_destination(
    group: FileGroup,
    destinations: list[DestinationDefinition],
    index: OrganizationIndex,
) -> tuple[DestinationDefinition | None, float]:
    scored = [
        (score_destination(group, destination, index.destination_names(destination)), destination.id, destination)
        for destination in destinations
    ]
    if not scored:
        return None, 0.0
    score, _destination_id, destination = max(scored, key=lambda item: (item[0], item[1]))
    return destination, score


def _collision_target(target: Path, source: Path) -> Path:
    if not target.exists():
        return target
    parent_token = re.sub(r"[^A-Za-z0-9]+", "-", source.parent.name).strip("-")
    candidate = target.with_name(f"{target.stem} ({parent_token or 'source'}){target.suffix}")
    number = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem} ({parent_token or 'source'}-{number}){target.suffix}")
        number += 1
    return candidate


def _verified_move(source: Path, target: Path) -> None:
    """Move atomically when possible; otherwise copy, verify, rename, and unlink."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing destination: {target}")
    try:
        os.rename(source, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        source_hash = sha256_file(source)
        shutil.copy2(source, temporary)
        if source.stat().st_size != temporary.stat().st_size or sha256_file(temporary) != source_hash:
            raise OSError("cross-volume copy verification failed")
        os.replace(temporary, target)
        source.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_actions(log_path: Path, actions: list[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for action in actions:
            handle.write(json.dumps(action, ensure_ascii=False) + "\n")


def organize_indexed(
    source: Path,
    roots: list[RootDefinition],
    destinations: list[DestinationDefinition],
    index: OrganizationIndex,
    older_than_days: int = 30,
    apply: bool = False,
    maximum: int = 100,
    now: dt.datetime | None = None,
    log_path: Path | None = None,
    pdf_renamer: Callable[[Path, bool], tuple[Path, str | None, str | None]] | None = None,
    recognition_backends: list[str] | None = None,
    recognition_timeout: int = 60,
) -> tuple[str, list[OrganizeResult]]:
    """Deduplicate, flatten, group, and place eligible Downloads content."""
    run_id = uuid.uuid4().hex
    timestamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    files = collect_eligible_tree_files(source, older_than_days, now=now, maximum=maximum)
    results: list[OrganizeResult] = []
    remaining: list[Path] = []

    for path in files:
        canonical = index.exact_match(path, roots)
        if canonical is None:
            remaining.append(path)
            continue
        old_mtime_ns = canonical.stat().st_mtime_ns
        action = {"run_id": run_id, "timestamp": timestamp, "action": "duplicate",
                  "source": str(path), "canonical": str(canonical), "canonical_mtime_ns": old_mtime_ns,
                  "sha256": sha256_file(path), "status": "planned"}
        if log_path:
            _append_actions(log_path, [action])
        if apply:
            path.unlink()
            os.utime(canonical, None)
            if log_path:
                _append_actions(log_path, [{**action, "status": "done"}])
        results.append(OrganizeResult(str(path), str(canonical), "duplicate" if apply else "planned",
                                      reason="exact duplicate; canonical file surfaced"))

    if pdf_renamer:
        renamed_remaining: list[Path] = []
        for path in remaining:
            if path.suffix.casefold() != ".pdf":
                renamed_remaining.append(path)
                continue
            working, _doc_type, error = pdf_renamer(path, apply)
            if error:
                results.append(OrganizeResult(str(path), None, "review", "Review", error))
                continue
            renamed_remaining.append(working)
        remaining = renamed_remaining

    default_root = min(roots, key=lambda root: (root.priority, root.id)) if roots else None
    for group in group_files(source, remaining):
        destination, score = choose_destination(group, destinations, index)
        if destinations and score < 0.65 and recognition_backends:
            from _organizer_recognition import resolve_destination
            agent_choice = resolve_destination(
                group.name,
                group.tokens,
                [{"id": item.id, "tags": list(item.tags), "aliases": list(item.aliases)}
                 for item in destinations],
                recognition_backends,
                recognition_timeout,
            )
            if agent_choice:
                destination_id, score = agent_choice
                destination = next(item for item in destinations if item.id == destination_id)
        if destination is not None and score >= 0.65:
            group_root = destination.path
            category = destination.id
        elif default_root is not None:
            group_root = default_root.managed_inbox / group.name
            category = "managed"
        else:
            group_root = source / "Review" / group.name
            category = "Review"
        for path in group.files:
            target = _collision_target(group_root / path.name, path)
            action = {"run_id": run_id, "timestamp": timestamp, "action": "move",
                      "source": str(path), "destination": str(target), "group": group.name,
                      "score": score, "status": "planned"}
            if log_path:
                _append_actions(log_path, [action])
            if apply:
                _verified_move(path, target)
                if log_path:
                    _append_actions(log_path, [{**action, "status": "done"}])
            results.append(OrganizeResult(str(path), str(target), "moved" if apply else "planned", category))

    if apply:
        directories = sorted(
            {parent for path in files for parent in path.parents if parent != source and source in parent.parents},
            key=lambda item: len(item.parts), reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass
    return run_id, results


def deduplicate_intake(
    source: Path,
    roots: list[RootDefinition],
    index: OrganizationIndex,
    apply: bool = False,
    settle_seconds: int = 60,
    maximum: int = 100,
    log_path: Path | None = None,
) -> tuple[str, list[OrganizeResult]]:
    """Deduplicate files that have settled across two intake scans."""
    run_id = uuid.uuid4().hex
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    results: list[OrganizeResult] = []
    for path in index.settled_files(source, settle_seconds, maximum):
        canonical = index.exact_match(path, roots)
        if canonical is None:
            continue
        old_mtime_ns = canonical.stat().st_mtime_ns
        action = {"run_id": run_id, "timestamp": timestamp, "action": "duplicate",
                  "source": str(path), "canonical": str(canonical),
                  "canonical_mtime_ns": old_mtime_ns, "sha256": sha256_file(path),
                  "status": "planned"}
        if log_path:
            _append_actions(log_path, [action])
        if apply:
            path.unlink()
            os.utime(canonical, None)
            index.forget_observation(path)
            if log_path:
                _append_actions(log_path, [{**action, "status": "done"}])
        results.append(OrganizeResult(str(path), str(canonical), "duplicate" if apply else "planned",
                                      reason="exact duplicate; canonical file surfaced"))
    return run_id, results


def undo_organization(log_path: Path, run_id: str) -> list[str]:
    """Undo completed actions for one indexed-organization run."""
    if not log_path.is_file():
        raise FileNotFoundError(log_path)
    actions = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    messages: list[str] = []
    for action in reversed([item for item in actions if item.get("run_id") == run_id and item.get("status") == "done"]):
        source = Path(action["source"])
        if action["action"] == "move":
            destination = Path(action["destination"])
            if destination.exists() and not source.exists():
                _verified_move(destination, source)
                messages.append(f"restored {source}")
        elif action["action"] == "duplicate":
            canonical = Path(action["canonical"])
            if (canonical.exists() and not source.exists()
                    and sha256_file(canonical) == action.get("sha256")):
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(canonical, source)
                os.utime(canonical, ns=(canonical.stat().st_atime_ns, int(action["canonical_mtime_ns"])))
                messages.append(f"recreated {source}")
    return messages


def classify_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    for category, extensions in EXTENSION_CATEGORIES.items():
        if suffix in extensions:
            return category
    return "Review"


def classify_pdf(doc_type: str | None) -> str:
    if not doc_type:
        return "Review"
    words = doc_type.casefold()
    for category, keywords in PDF_CATEGORY_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(keyword)}\b", words) for keyword in keywords):
            return category
    return "General"


def destination_for(root: Path, category: str, path: Path, timestamp_source: Path | None = None) -> Path:
    timestamp_source = timestamp_source or path
    year = dt.datetime.fromtimestamp(timestamp_source.stat().st_mtime).strftime("%Y")
    if category in {"Images", "Video", "Audio"}:
        return root / "Media" / category / year / path.name
    if category in {"Financial", "Medical", "Education", "Employment", "General", "Spreadsheets"}:
        return root / "Documents" / category / year / path.name
    if category == "Review":
        return root / "Review" / year / path.name
    return root / category / year / path.name


def organize_files(
    source: Path,
    destination: Path,
    older_than_days: int = 30,
    apply: bool = False,
    pdf_renamer: Callable[[Path, bool], tuple[Path, str | None, str | None]] | None = None,
    now: dt.datetime | None = None,
) -> list[OrganizeResult]:
    """Plan or apply renames and moves. PDF renamer returns path, doc type, error."""
    results: list[OrganizeResult] = []
    for original in collect_eligible_files(source, older_than_days, now=now):
        working = original
        renamed_to = None
        error = None
        doc_type = None
        if original.suffix.lower() == ".pdf" and pdf_renamer:
            working, doc_type, error = pdf_renamer(original, apply)
            if working.name != original.name:
                renamed_to = working.name
        category = classify_pdf(doc_type) if original.suffix.lower() == ".pdf" else classify_extension(original)
        if error:
            category = "Review"

        target = destination_for(destination, category, working, timestamp_source=original)
        if target.exists() and target.resolve() != working.resolve():
            results.append(OrganizeResult(str(original), None, "review", "Review", "destination collision", renamed_to))
            continue

        status = "planned"
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(working), str(target))
            status = "moved"
        results.append(OrganizeResult(str(original), str(target), status, category, error, renamed_to))
    return results


def append_audit_log(path: Path, results: Iterable[OrganizeResult], apply: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps({"timestamp": stamp, "apply": apply, **asdict(result)}, ensure_ascii=False) + "\n")
