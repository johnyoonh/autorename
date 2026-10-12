#!/usr/bin/env python3
"""Preview or install Finder services without embedding personal paths in Git."""
import argparse
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import tempfile
import uuid


def workflow(name, command):
    action = {
        'ActionBundlePath': '/System/Library/Automator/Run Shell Script.action',
        'ActionName': 'Run Shell Script',
        'BundleIdentifier': 'com.apple.RunShellScript',
        'AMActionVersion': '2.0.3',
        'AMApplication': ['Automator'],
        'AMAccepts': {'Container': 'List', 'Optional': True, 'Types': ['com.apple.cocoa.path']},
        'AMProvides': {'Container': 'List', 'Types': ['com.apple.cocoa.path']},
        'ActionParameters': {'COMMAND_STRING': command, 'inputMethod': 1, 'shell': '/bin/bash', 'source': ''},
        'Class Name': 'RunShellScriptAction',
        'UUID': str(uuid.uuid4()),
    }
    return {
        'AMApplicationBuild': '534', 'AMApplicationVersion': '2.10', 'AMDocumentVersion': '2',
        'actions': [{'action': action}], 'connectors': {},
        'workflowMetaData': {
            'workflowTypeIdentifier': 'com.apple.Automator.servicesMenu',
            'inputTypeIdentifier': 'com.apple.Automator.fileSystemObject',
            'outputTypeIdentifier': 'com.apple.Automator.nothing',
            'serviceInputTypeIdentifier': 'com.apple.Automator.fileSystemObject',
            'serviceOutputTypeIdentifier': 'com.apple.Automator.nothing',
            'presentationMode': 15, 'processesInput': False, 'serviceProcessesInput': False,
            'useAutomaticInputType': False, 'systemImageName': 'NSActionTemplate',
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent,
                        help='Stable runtime checkout (not a temporary task worktree)')
    parser.add_argument('--services-dir', type=Path, default=Path.home() / 'Library/Services')
    parser.add_argument('--apply', action='store_true', help='Install the previewed services')
    parser.add_argument('--replace', action='store_true', help='Back up and replace existing actions')
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    services = args.services_dir.expanduser().resolve()
    for filename in ('quickaction-helper.sh', 'autorename-pdf.py', 'config.yaml'):
        if not (repo / filename).is_file():
            parser.error(f'Missing runtime file: {repo / filename}')
    python = next((repo / directory / 'bin/python' for directory in ('.venv', 'venv')
                   if os.access(repo / directory / 'bin/python', os.X_OK)), None)
    if python is None:
        parser.error('Run setup.sh first: no .venv/bin/python or venv/bin/python found')
    prefix = f'export AUTORENAME_PYTHON={shlex.quote(str(python))}\n/bin/bash {shlex.quote(str(repo / "quickaction-helper.sh"))}'
    actions = [('Preview AutoRename', prefix + ' --preview "$@"'),
               ('Rename with AutoRename', prefix + ' "$@"')]
    for name, command in actions:
        print(f'{services / (name + ".workflow")}\n{command}')
    if not args.apply:
        print('Preview only. Add --apply to install; --replace backs up existing actions.')
        return
    for name, _ in actions:
        target = services / (name + '.workflow')
        if target.exists() and not args.replace:
            parser.error(f'Action already exists: {target}; use --replace to back it up')
    services.mkdir(parents=True, exist_ok=True)
    for name, command in actions:
        target = services / (name + '.workflow')
        stage = Path(tempfile.mkdtemp(prefix='.autorename-', dir=services))
        backup = None
        try:
            contents = stage / 'Contents'
            contents.mkdir()
            info = {'NSServices': [{
                'NSMenuItem': {'default': name}, 'NSMessage': 'runWorkflowAsService',
                'NSRequiredContext': {'NSApplicationIdentifier': 'com.apple.finder'},
                'NSSendFileTypes': ['com.adobe.pdf', 'public.image'],
                'NSIconName': 'NSActionTemplate',
            }]}
            for filename, data in [('Info.plist', info), ('document.wflow', workflow(name, command))]:
                (contents / filename).write_bytes(plistlib.dumps(data))
            if target.exists():
                backup = services / (name + '.workflow.backup-' + uuid.uuid4().hex)
                target.rename(backup)
                print(f'Backup: {backup}')
            try:
                stage.rename(target)
            except OSError:
                if backup is not None:
                    backup.rename(target)
                raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print('Installed. Select PDFs or images in Finder > Quick Actions (or Services).')


if __name__ == '__main__':
    main()
