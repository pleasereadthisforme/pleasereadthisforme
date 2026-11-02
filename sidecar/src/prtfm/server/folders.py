"""Metadata-only navigation under an explicitly selected folder."""
import os
from pathlib import Path

from prtfm.config import data_root
from prtfm.indexing.extract import IMAGE_EXTS, SUPPORTED
from prtfm.indexing.scope import SKIP_DIRS, SKIP_DIRS_CI, ScopeError, _hidden, _is_dir_link


def browse(scope, relative='.', *, directories_only=False):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ScopeError('outside scope')
    current = scope.root
    for part in rel.parts:
        current /= part
        if part.startswith('.') or part in SKIP_DIRS or part.lower() in SKIP_DIRS_CI or _is_dir_link(str(current)):
            raise ScopeError('excluded folder')
    current = scope.resolve(relative)
    if current.is_relative_to(data_root().resolve()) or _hidden(current.stat()):
        raise ScopeError('excluded folder')
    entries, limited = [], False
    with os.scandir(current) as children:
        for item in children:
            if item.name.startswith(('.', '~$')) or item.name in SKIP_DIRS or item.name.lower() in SKIP_DIRS_CI:
                continue
            if _is_dir_link(item.path):
                continue
            try:
                path = Path(item.path)
                if path.resolve().is_relative_to(data_root().resolve()) or _hidden(item.stat(follow_symlinks=False)):
                    continue
                directory = item.is_dir(follow_symlinks=False)
                if not directory and (not item.is_file(follow_symlinks=False) or path.suffix.lower() not in (SUPPORTED | IMAGE_EXTS)):
                    continue
                if directories_only and not directory:
                    continue
                if len(entries) == 1000:
                    limited = True
                    break
                entries.append({'name': item.name, 'relative': str(path.relative_to(scope.root)),
                                'kind': 'folder' if directory else 'file'})
            except OSError:
                continue
    return {'folder': str(current), 'relative': str(current.relative_to(scope.root)),
            'entries': sorted(entries, key=lambda e: (e['kind'] != 'folder', e['name'])), 'limited': limited}


def browse_local(relative='.'):
    """List directories within the configured navigation root, without indexing."""
    from prtfm.indexing.scope import Scope

    root = Path(os.environ.get('PRTFM_BROWSE_ROOT', str(Path.home()))).resolve()
    result = browse(Scope(root), relative, directories_only=True)
    result['entries'] = [entry for entry in result['entries'] if entry['kind'] == 'folder']
    result['display_path'] = str(Path(result['folder']))
    result['parent'] = str(Path(result['relative']).parent) if result['relative'] != '.' else None
    return result
