"""Exact packaged test directories only; never match arbitrary 'test' names."""
import argparse
import json
from pathlib import Path


def plan(root, config):
    directories = json.loads(config.read_text(encoding='utf-8'))['directories']
    excluded = []
    for name in directories:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not name.startswith('python/Lib/site-packages/') or relative.name != 'tests':
            raise ValueError('Invalid packaged test directory')
        folder = root / relative
        if any(p.is_symlink() for p in [folder, *folder.parents] if p != root.parent):
            raise ValueError('Symlink in test path')
        if folder.exists():
            for path in sorted(folder.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Symlink in test directory')
                if path.is_file():
                    excluded.append(path.relative_to(root).as_posix())
    return {'directories': directories, 'exclude': excluded}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(plan(args.root, Path(__file__).resolve().parents[1] / 'desktop/python-test-exclusions.json')))
