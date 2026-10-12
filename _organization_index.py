"""Cached catalog of approved locally synced organization roots."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RootDefinition:
    id: str
    path: Path
    managed_inbox: Path
    priority: int = 100
    protected: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DestinationDefinition:
    id: str
    root_id: str
    path: Path
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


def _expand_path(value: str, base: Path | None = None) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute() and base is not None:
        expanded = base / expanded
    return expanded.resolve()


def load_root_structure(path: Path) -> tuple[list[RootDefinition], list[DestinationDefinition]]:
    """Load and validate an allowlisted cloud-root definition file."""
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if payload.get("version") != 1 or not isinstance(payload.get("roots"), list):
        raise ValueError("root structure must contain version: 1 and a roots list")

    roots: list[RootDefinition] = []
    destinations: list[DestinationDefinition] = []
    seen_roots: set[str] = set()
    seen_destinations: set[str] = set()
    for raw in payload["roots"]:
        root_id = str(raw.get("id", "")).strip()
        if not root_id or root_id in seen_roots:
            raise ValueError(f"root id is empty or duplicated: {root_id!r}")
        seen_roots.add(root_id)
        raw_root_path = str(raw.get("path", "")).strip()
        if not raw_root_path:
            raise ValueError(f"root {root_id!r} requires a path")
        root_path = _expand_path(raw_root_path)
        inbox = _expand_path(str(raw.get("managed_inbox", "Filed")), root_path)
        protected = tuple(_expand_path(str(item), root_path) for item in raw.get("protected", []))
        for label, candidate in (("managed inbox", inbox), *(("protected path", item) for item in protected)):
            try:
                candidate.relative_to(root_path)
            except ValueError as exc:
                raise ValueError(f"{label} is outside root {root_id!r}") from exc
        root = RootDefinition(root_id, root_path, inbox, int(raw.get("priority", 100)), protected)
        roots.append(root)

        for raw_destination in raw.get("destinations", []):
            destination_id = str(raw_destination.get("id", "")).strip()
            if not destination_id or destination_id in seen_destinations:
                raise ValueError(f"destination id is empty or duplicated: {destination_id!r}")
            seen_destinations.add(destination_id)
            raw_destination_path = str(raw_destination.get("path", "")).strip()
            if not raw_destination_path:
                raise ValueError(f"destination {destination_id!r} requires a path")
            destination_path = _expand_path(raw_destination_path, root_path)
            try:
                destination_path.relative_to(root_path)
            except ValueError as exc:
                raise ValueError(f"destination {destination_id!r} is outside root {root_id!r}") from exc
            destinations.append(DestinationDefinition(
                destination_id,
                root_id,
                destination_path,
                tuple(str(tag) for tag in raw_destination.get("tags", [])),
                tuple(str(alias) for alias in raw_destination.get("aliases", [])),
            ))
    return sorted(roots, key=lambda root: (root.priority, root.id)), destinations


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class OrganizationIndex:
    """SQLite metadata cache; file content hashes are populated lazily."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS entries (
                path TEXT PRIMARY KEY,
                root_id TEXT NOT NULL,
                parent TEXT NOT NULL,
                name TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT,
                is_dir INTEGER NOT NULL,
                last_seen INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS entries_size ON entries(size, is_dir);
            CREATE INDEX IF NOT EXISTS entries_root ON entries(root_id, parent);
            CREATE TABLE IF NOT EXISTS refreshes (
                root_id TEXT PRIMARY KEY,
                refreshed_at INTEGER NOT NULL,
                entry_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "OrganizationIndex":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _protected(path: Path, protected: tuple[Path, ...]) -> bool:
        for item in protected:
            if path == item or item in path.parents:
                return True
        return False

    def refresh(self, roots: list[RootDefinition], root_ids: set[str] | None = None) -> dict[str, int]:
        """Refresh selected roots while retaining hashes for unchanged files."""
        result: dict[str, int] = {}
        now = time.time_ns()
        for root in roots:
            if root_ids and root.id not in root_ids:
                continue
            if not root.path.is_dir():
                result[root.id] = -1
                continue
            count = 0
            for current, dirnames, filenames in os.walk(root.path, followlinks=False):
                current_path = Path(current)
                dirnames[:] = [
                    name for name in dirnames
                    if not name.startswith(".")
                    and not (current_path / name).is_symlink()
                    and not self._protected(current_path / name, root.protected)
                ]
                paths = [current_path / name for name in dirnames]
                paths.extend(current_path / name for name in filenames if not name.startswith("."))
                for entry in paths:
                    if entry.is_symlink() or self._protected(entry, root.protected):
                        continue
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    is_dir = int(entry.is_dir())
                    size = 0 if is_dir else stat.st_size
                    old = self.connection.execute(
                        "SELECT size, mtime_ns, sha256 FROM entries WHERE path = ?", (str(entry),)
                    ).fetchone()
                    cached_hash = (
                        old["sha256"] if old and old["size"] == size and old["mtime_ns"] == stat.st_mtime_ns
                        else None
                    )
                    self.connection.execute(
                        """INSERT INTO entries(path, root_id, parent, name, size, mtime_ns, sha256, is_dir, last_seen)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(path) DO UPDATE SET root_id=excluded.root_id, parent=excluded.parent,
                           name=excluded.name, size=excluded.size, mtime_ns=excluded.mtime_ns,
                           sha256=excluded.sha256, is_dir=excluded.is_dir, last_seen=excluded.last_seen""",
                        (str(entry), root.id, str(entry.parent), entry.name, size, stat.st_mtime_ns,
                         cached_hash, is_dir, now),
                    )
                    count += 1
            self.connection.execute("DELETE FROM entries WHERE root_id = ? AND last_seen != ?", (root.id, now))
            self.connection.execute(
                """INSERT INTO refreshes(root_id, refreshed_at, entry_count) VALUES(?, ?, ?)
                   ON CONFLICT(root_id) DO UPDATE SET refreshed_at=excluded.refreshed_at,
                   entry_count=excluded.entry_count""",
                (root.id, int(time.time()), count),
            )
            result[root.id] = count
        self.connection.commit()
        return result

    def exact_match(self, incoming: Path, roots: list[RootDefinition]) -> Path | None:
        """Return a deterministic canonical file after full-hash verification."""
        stat = incoming.stat()
        rows = self.connection.execute(
            "SELECT * FROM entries WHERE is_dir = 0 AND size = ? ORDER BY path", (stat.st_size,)
        ).fetchall()
        if not rows:
            return None
        incoming_hash = sha256_file(incoming)
        priority = {root.id: root.priority for root in roots}
        matches: list[tuple[int, int, str, Path]] = []
        for row in rows:
            candidate = Path(row["path"])
            if not candidate.is_file():
                continue
            if candidate.resolve() == incoming.resolve():
                continue
            candidate_stat = candidate.stat()
            cached = row["sha256"]
            if not cached or row["mtime_ns"] != candidate_stat.st_mtime_ns or row["size"] != candidate_stat.st_size:
                cached = sha256_file(candidate)
                self.connection.execute(
                    "UPDATE entries SET size = ?, mtime_ns = ?, sha256 = ? WHERE path = ?",
                    (candidate_stat.st_size, candidate_stat.st_mtime_ns, cached, str(candidate)),
                )
            if cached == incoming_hash:
                matches.append((priority.get(row["root_id"], 100), len(candidate.parts), str(candidate), candidate))
        self.connection.commit()
        return min(matches)[3] if matches else None

    def destination_names(self, destination: DestinationDefinition) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM entries WHERE parent = ? AND is_dir = 0 LIMIT 200",
            (str(destination.path),),
        ).fetchall()
        return [row["name"] for row in rows]

    def settled_files(self, source: Path, settle_seconds: int = 60, maximum: int = 100) -> list[Path]:
        """Return files unchanged across two observations for the settling interval."""
        now = int(time.time())
        settled: list[Path] = []
        present: set[str] = set()
        for current, dirnames, filenames in os.walk(source, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [name for name in dirnames if not name.startswith(".") and not (current_path / name).is_symlink()]
            for name in filenames:
                path = current_path / name
                if name.startswith(".") or name.startswith("~$") or path.is_symlink():
                    continue
                if path.suffix.casefold() in {".crdownload", ".download", ".part"}:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                present.add(str(path))
                old = self.connection.execute(
                    "SELECT size, mtime_ns, first_seen FROM observations WHERE path = ?", (str(path),)
                ).fetchone()
                if old and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns:
                    if now - old["first_seen"] >= settle_seconds:
                        settled.append(path)
                else:
                    self.connection.execute(
                        """INSERT INTO observations(path, size, mtime_ns, first_seen, last_seen)
                           VALUES(?, ?, ?, ?, ?)
                           ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns,
                           first_seen=excluded.first_seen, last_seen=excluded.last_seen""",
                        (str(path), stat.st_size, stat.st_mtime_ns, now, now),
                    )
                self.connection.execute("UPDATE observations SET last_seen = ? WHERE path = ?", (now, str(path)))
        prefix = str(source.resolve()) + os.sep + "%"
        rows = self.connection.execute("SELECT path FROM observations WHERE path LIKE ?", (prefix,)).fetchall()
        for row in rows:
            if row["path"] not in present:
                self.connection.execute("DELETE FROM observations WHERE path = ?", (row["path"],))
        self.connection.commit()
        if len(settled) > maximum:
            raise ValueError(f"unattended intake is limited to {maximum} files")
        return sorted(settled, key=lambda path: str(path).casefold())

    def forget_observation(self, path: Path) -> None:
        self.connection.execute("DELETE FROM observations WHERE path = ?", (str(path),))
        self.connection.commit()

    def status(self) -> dict:
        rows = self.connection.execute(
            "SELECT root_id, refreshed_at, entry_count FROM refreshes ORDER BY root_id"
        ).fetchall()
        return {
            "index": str(self.path),
            "roots": [dict(row) for row in rows],
            "entries": self.connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
        }

    def dump_status(self) -> str:
        return json.dumps(self.status(), indent=2)
