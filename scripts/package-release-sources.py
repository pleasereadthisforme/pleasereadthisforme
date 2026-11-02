"""Create local source release candidates; never stage, commit, push or publish."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def app_files(repo):
    raw = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'])
    result = []
    for name in sorted(set(raw.decode().split('\0')) - {''}):
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or p.parts[0] in {'dev', '.git'} or any(x == '.env' or x.startswith('.env.') for x in p.parts):
            raise ValueError('Private or unsafe source path: ' + name)
        source = repo / p
        if any(parent.is_symlink() for parent in [source, *source.parents] if parent != repo.parent):
            raise ValueError('Source symlink: ' + name)
        if not source.is_file():
            raise ValueError('Missing source file: ' + name)
        result.append((name, source))
    if not result:
        raise ValueError('No public source files')
    return result


def pack(path, entries):
    with tarfile.open(path, 'x:gz') as archive:
        for name, source in entries:
            archive.add(source, arcname=name, recursive=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-map', required=True, type=Path, help='Local paths plus expected hashes for every source-catalog entry')
    parser.add_argument('--output', required=True, type=Path, help='New, nonexistent artifact directory')
    args = parser.parse_args()
    repo = args.repo.resolve()
    sources = json.loads((repo / 'runtime/reviewed-notices/source-catalog.json').read_text())['sources']
    local = {r['sha256']: Path(r['local']) for r in json.loads(args.source_map.read_text())}
    archive_entries = []
    for sha in sorted({r['sha256'] for r in sources}):
        source = local.get(sha)
        if source is None or not source.is_file() or digest(source) != sha:
            raise ValueError('Missing/mismatched corresponding source: ' + sha)
        archive_entries.append(('archives/' + sha + '/' + source.name, source))
    files = app_files(repo)
    manifest = [{'path': name, 'sha256': digest(p), 'bytes': p.stat().st_size} for name, p in files]
    # All required files are checked before output creation. A partial output is never a release.
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'source-files.json').write_text(json.dumps(manifest, indent=2) + '\n')
    pack(args.output / 'application-source.tar.gz', [('application/' + name, p) for name, p in files])
    pack(args.output / 'third-party-sources.tar.gz', archive_entries + [('source-catalog.json', repo / 'runtime/reviewed-notices/source-catalog.json')])
    (args.output / 'README.txt').write_text('Local source release candidate. Not published.\n'
        'application-source.tar.gz includes build scripts, lockfiles, required assets and notices.\n'
        'third-party-sources.tar.gz contains hash-verified reviewed upstream archives.\n'
        'Build the final installer from this application snapshot and record its hash alongside these files.\n'
        'Do not label this snapshot as source for an earlier installer without comparing its build inputs.\n'
        'Publish both source archives and checksums beside the binary release; local preparation alone is not public source provision.\n')
    hashes = {p.name: digest(p) for p in args.output.iterdir() if p.is_file()}
    (args.output / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in sorted(hashes.items())))
    print(json.dumps({'application_files': len(files), 'upstream_archives': len(archive_entries), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
