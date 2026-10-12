"""Tests for the macOS Finder Quick Action helper."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "quickaction-helper.sh"


def _write_fake_cli(path: Path) -> None:
    path.write_text(
        """\
import json
import sys

paths = [arg for arg in sys.argv[1:] if arg.endswith((\".pdf\", \".png\", \".jpg\"))]
print(json.dumps({
    \"renamed\": len(paths),
    \"skipped\": 0,
    \"failed\": 0,
    \"files\": [{\"status\": \"renamed\", \"file\": path} for path in paths],
}))
""",
        encoding="utf-8",
    )


def test_helper_dispatches_pdf_and_image_files(tmp_path):
    fake_cli = tmp_path / "fake_cli.py"
    config = tmp_path / "config.yaml"
    log = tmp_path / "finder.log"
    pdf = tmp_path / "invoice.pdf"
    image = tmp_path / "receipt.png"
    ignored = tmp_path / "notes.txt"
    _write_fake_cli(fake_cli)
    config.write_text("ai: {}\n", encoding="utf-8")
    pdf.write_bytes(b"pdf")
    image.write_bytes(b"png")
    ignored.write_text("notes", encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "AUTORENAME_PYTHON": sys.executable,
        "AUTORENAME_SCRIPT": str(fake_cli),
        "AUTORENAME_CONFIG": str(config),
        "AUTORENAME_FINDER_LOG": str(log),
        "AUTORENAME_NOTIFY": "0",
    })
    completed = subprocess.run(
        ["/bin/bash", str(HELPER), str(pdf), str(image), str(ignored)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    log_text = log.read_text(encoding="utf-8")
    assert "START Files: 2" in log_text
    assert "2 renamed" in log_text
    assert "unsupported: 1" in log_text


def test_helper_rejects_unsupported_selection(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "finder.log"
    ignored = tmp_path / "notes.txt"
    config.write_text("ai: {}\n", encoding="utf-8")
    ignored.write_text("notes", encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "AUTORENAME_PYTHON": sys.executable,
        "AUTORENAME_SCRIPT": str(ROOT / "autorename-pdf.py"),
        "AUTORENAME_CONFIG": str(config),
        "AUTORENAME_FINDER_LOG": str(log),
        "AUTORENAME_NOTIFY": "0",
    })
    completed = subprocess.run(
        ["/bin/bash", str(HELPER), str(ignored)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Unsupported extension" in log.read_text(encoding="utf-8")


def test_helper_preview_does_not_apply(tmp_path):
    cli = tmp_path / 'cli.py'
    cli.write_text('import json, sys\nassert "--dry-run" in sys.argv\nprint(json.dumps({"renamed": 1, "dry_run": True}))\n')
    config = tmp_path / 'config.yaml'
    config.write_text('ai: {}\n')
    selected = tmp_path / 'invoice.pdf'
    selected.write_bytes(b'pdf')
    log = tmp_path / 'finder.log'
    python = tmp_path / "runtime with ' quotes" / 'python'
    python.parent.mkdir()
    python.symlink_to(sys.executable)
    env = dict(os.environ, AUTORENAME_PYTHON=str(python), AUTORENAME_SCRIPT=str(cli), AUTORENAME_CONFIG=str(config), AUTORENAME_FINDER_LOG=str(log), AUTORENAME_NOTIFY='0')
    result = subprocess.run(['/bin/bash', str(HELPER), '--preview', str(selected)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '1 proposed' in log.read_text()
