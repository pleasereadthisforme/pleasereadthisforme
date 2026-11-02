"""Select pinned search binaries; preserve all non-binary support and legal files."""
import argparse
import hashlib
import json
from pathlib import Path


def plan(root, manifest):
    expected = json.loads(manifest.read_text(encoding='utf-8'))['binaries']
    for name, checksum in expected.items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing regular search dependency: {name}')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                raise ValueError(f'Search dependency changed; review imports first: {name}')
    keep, exclude = [], []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Search tool symlinks require review')
        if path.is_file():
            name = path.relative_to(root).as_posix()
            (exclude if path.suffix.lower() in {'.exe', '.dll'} and name not in expected else keep).append(name)
    return {'keep': keep, 'exclude': exclude}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(plan(args.root, Path(__file__).resolve().parents[1] / 'desktop/search-tools.json')))
