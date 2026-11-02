"""Open original documents without conversion, copies, or shell commands."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from prtfm.config import data_root
from prtfm.credentials.process_env import child_environment
from prtfm.indexing.scope import SKIP_DIRS, SKIP_DIRS_CI, Scope, _hidden, _is_dir_link
from prtfm.store.folders import registry

OPEN_EXTENSIONS = frozenset('.pdf .txt .md .docx .pptx .xlsx .csv .json .png .jpg .jpeg .gif .webp .bmp .tif .tiff'.split())


class OpenError(ValueError):
    pass


def validate_document(root, relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 4096
            or any(ord(c) < 32 for c in relative) or ':' in relative
            or '\\' in relative or PureWindowsPath(relative).is_absolute()):
        raise OpenError('outside_scope')
    rel = Path(relative)
    if rel.is_absolute() or any(p in ('.', '..') for p in relative.split('/')):
        raise OpenError('outside_scope')
    if rel.suffix.lower() not in OPEN_EXTENSIONS:
        raise OpenError('unsupported_type')
    try:
        scope = Scope(root)
        current = scope.root
        for part in rel.parts:
            if part.startswith(('.', '~$')) or part in SKIP_DIRS or part.lower() in SKIP_DIRS_CI or part.endswith((' ', '.')):
                raise OpenError('outside_scope')
            current /= part
            if _is_dir_link(str(current)) or _hidden(current.stat()):
                raise OpenError('outside_scope')
        path = scope.resolve(relative)
        if path.is_relative_to(data_root().resolve()):
            raise OpenError('outside_scope')
        if not path.is_file():
            raise OpenError('file_missing')
        return path
    except OSError:
        raise OpenError('file_missing') from None


def dispatch(path):
    bridge = os.environ.get('PRTFM_OPEN_SOCKET')
    if bridge:
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(12)
                client.connect(bridge)
                client.sendall(json.dumps({'path': str(path), 'token': os.environ.get('PRTFM_OPEN_TOKEN', '')}).encode() + b'\n')
                data = b''
                while b'\n' not in data and len(data) < 1024:
                    chunk = client.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                result = json.loads(data)
                if result.get('status') != 'dispatched':
                    raise OpenError('launch_failed')
            return
        except OpenError:
            raise
        except (OSError, ValueError):
            raise OpenError('opener_unavailable') from None
    try:
        if sys.platform == 'win32':
            os.startfile(str(path))
        elif os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
            subprocess.run(['xdg-open', str(path)], env=child_environment(), shell=False, timeout=10,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            raise OpenError('opener_unavailable')
    except (OSError, subprocess.SubprocessError):
        raise OpenError('launch_failed') from None


def open_registered(folder_id, relative):
    with registry() as con:
        row = con.execute('SELECT path FROM folders WHERE id=?', (folder_id,)).fetchone()
    if row is None:
        raise OpenError('folder_disconnected')
    path = validate_document(row[0], relative)
    dispatch(path)
    return {'status': 'dispatched'}
