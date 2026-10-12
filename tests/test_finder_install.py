"""Installer contracts use temporary Services and a synthetic runtime."""
import plistlib
import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_previews_then_installs_finder_services(tmp_path):
    repo = tmp_path / "repo with ' quotes"
    repo.mkdir()
    (repo / 'quickaction-helper.sh').write_text('#!/bin/bash\n')
    (repo / 'autorename-pdf.py').write_text('')
    (repo / 'config.yaml').write_text('ai: {}\n')
    runtime = repo / 'venv/bin'
    runtime.mkdir(parents=True)
    (runtime / 'python').symlink_to(sys.executable)
    services = tmp_path / 'Services'
    command = [sys.executable, str(ROOT / 'install-finder-actions.py'), '--repo', str(repo), '--services-dir', str(services)]
    preview = subprocess.run(command, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert not services.exists()
    result = subprocess.run(command + ['--apply'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for name, is_preview in [('Preview AutoRename', True), ('Rename with AutoRename', False)]:
        contents = services / (name + '.workflow') / 'Contents'
        info = plistlib.loads((contents / 'Info.plist').read_bytes())
        service = info['NSServices'][0]
        assert service['NSRequiredContext']['NSApplicationIdentifier'] == 'com.apple.finder'
        assert service['NSSendFileTypes'] == ['com.adobe.pdf', 'public.image']
        workflow = plistlib.loads((contents / 'document.wflow').read_bytes())
        params = workflow['actions'][0]['action']['ActionParameters']
        assert params['inputMethod'] == 1
        assert ('--preview' in params['COMMAND_STRING']) == is_preview
        # Execute the generated shell to prove paths with spaces and quotes survive.
        (repo / 'quickaction-helper.sh').write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        selected = str(tmp_path / 'selected file.pdf')
        run = subprocess.run(['/bin/bash', '-c', params['COMMAND_STRING'], 'action', selected], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert run.stdout.splitlines() == (['--preview', selected] if is_preview else [selected])
        if sys.platform == 'darwin' and shutil.which('automator'):
            live = subprocess.run(['automator', '-i', selected, str(contents.parent)], capture_output=True, text=True, timeout=30)
            assert live.returncode == 0, live.stderr
            assert selected in live.stdout
    # A customized existing action must not be overwritten silently.
    result = subprocess.run(command + ['--apply'], capture_output=True, text=True)
    assert result.returncode != 0

    result = subprocess.run(command + ['--apply', '--replace'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(list(services.glob('*.workflow.backup-*'))) == 2


def test_installer_refuses_incomplete_runtime_without_writing(tmp_path):
    services = tmp_path / 'Services'
    result = subprocess.run([sys.executable, str(ROOT / 'install-finder-actions.py'), '--repo', str(tmp_path), '--services-dir', str(services), '--apply'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Missing runtime file' in result.stderr
    assert not services.exists()
