"""Record already provisioned binaries; never download or install tools."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--root', required=True, type=Path)
parser.add_argument('--gnu-dir', required=True, type=Path)
parser.add_argument('--rg', required=True, type=Path)
args = parser.parse_args()
root = args.root.resolve(strict=True)
commands = {}
for name in ('find', 'rg', 'grep', 'sed', 'cat'):
    target = (args.rg if name == 'rg' else args.gnu_dir / (name + '.exe')).resolve(strict=True)
    with target.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    commands[name] = dict(path=target.relative_to(root).as_posix(), sha256=digest)
(root / 'commands.json').write_text(json.dumps(dict(schema_version=1, commands=commands), indent=2) + '\n', encoding='utf-8')
