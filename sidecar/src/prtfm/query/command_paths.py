"""Select packaged commands explicitly; PATH is a Linux development fallback."""
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath

NAMES = ('find', 'rg', 'grep', 'sed', 'cat')


class CommandUnavailable(ValueError):
    """Required execution environment is missing or differs from its manifest."""


def resolve_commands():
    configured = os.environ.get('PRTFM_COMMAND_MANIFEST')
    if configured is None:
        if os.name == 'nt':
            raise CommandUnavailable('Windows requires PRTFM_COMMAND_MANIFEST')
        result = {}
        for name in NAMES:
            binary = shutil.which(name)
            if binary is None:
                raise CommandUnavailable(f'required executable missing: {name}')
            result[name] = str(Path(binary).resolve())
        return result
    try:
        manifest = Path(configured)
        if not configured or not manifest.is_absolute():
            raise ValueError('command manifest must be an absolute path')
        data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if data['schema_version'] != 1 or set(data['commands']) != set(NAMES):
            raise ValueError('unsupported command manifest')
        root = manifest.resolve().parent
        result = {}
        for name in NAMES:
            entry = data['commands'][name]
            relative = PurePosixPath(entry['path'])
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry['path']:
                raise ValueError('invalid command path')
            target = (root / relative).resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError('command outside bundle')
            if not os.access(target, os.X_OK):
                raise ValueError('command is not executable')
            with target.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            if digest != entry['sha256']:
                raise ValueError(f'command digest mismatch: {name}')
            result[name] = str(target)
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommandUnavailable(f'command bundle unavailable: {exc}') from exc
