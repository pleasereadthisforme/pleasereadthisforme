"""Assemble an unsigned Windows directory bundle from explicit runtime components."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DUPLICATE_PARSER = 'python/Lib/site-packages/opendataloader_pdf/jar/opendataloader-pdf-cli.jar'


def install_reviewed_notices(output: Path, notices: Path) -> None:
    """Attach version-specific notices only to the reviewed binaries."""
    manifest = json.loads((notices / 'manifest.json').read_text(encoding='utf-8'))
    for relative, key in (
        ('runtime/parser.jar', 'parser_sha256'),
        ('tools/git/usr/bin/msys-2.0.dll', 'msys_dll_sha256'),
    ):
        with (output / relative).open('rb') as handle:
            actual = hashlib.file_digest(handle, 'sha256').hexdigest()
        if actual != manifest[key]:
            raise ValueError(f'Review third-party notices for changed binary: {relative}')
    shutil.copytree(notices, output / 'runtime/legal/prtfm-reviewed')


def validate_static_extension_notices(output: Path, notices: Path) -> None:
    fingerprints = json.loads((notices / 'binaries.json').read_text(encoding='utf-8'))
    for relative, expected in fingerprints.items():
        with (output / relative).open('rb') as handle:
            actual = hashlib.file_digest(handle, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError(f'Review Python static dependency notices for changed binary: {relative}')


def install_application_notices(output: Path, repo: Path) -> None:
    """Copy only public licensing documents into the installed runtime."""
    rust_manifest = json.loads((repo / 'runtime/reviewed-notices/rust/manifest.json').read_text(encoding='utf-8'))
    lock_hash = hashlib.sha256((repo / 'desktop/src-tauri/Cargo.lock').read_bytes()).hexdigest()
    if lock_hash != rust_manifest['cargo_lock_sha256']:
        raise ValueError('Review Rust notices for changed Cargo.lock')
    names = ('LICENSE', 'LICENSING.md', 'BRAND_USAGE.md', 'PRIVACY.md', 'TERMS.md')
    # Validate all inputs before copying any notice.
    contents = {name: (repo / name).read_bytes() for name in names}
    legal = output / 'runtime/legal'
    legal.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        if name == 'LICENSING.md':
            data = data.replace(b'runtime/reviewed-notices/README.md', b'prtfm-reviewed/README.md')
        (legal / name).write_bytes(data)
    (legal / 'README.txt').write_text(
        'Please read this for me - licenses and notices\n\n'
        'LICENSE: application AGPL-3.0-only license.\n'
        'LICENSING.md: application and asset license scope.\n'
        'BRAND_USAGE.md: official identity and brand guidance.\n'
        'PRIVACY.md / TERMS.md: draft data handling and Preview use conditions.\n'
        'prtfm-reviewed/README.md: MSYS and PDF parser notice supplement, sources and hashes.\n\n'
        'Other bundled components retain their original licenses and notices in their distribution directories.\n'
        'This supplement is not a complete inventory of all bundled components.\n', encoding='utf-8')


def remove_duplicate_parser(output: Path) -> int:
    """Remove only the redundant JAR from a newly assembled distribution."""
    duplicate = output / DUPLICATE_PARSER
    canonical = output / 'runtime/parser.jar'
    manifest = json.loads((output / 'runtime/manifest.json').read_text(encoding='utf-8'))
    def digest(path):
        with path.open('rb') as handle:
            return hashlib.file_digest(handle, 'sha256').hexdigest()
    expected = manifest['jar_sha256']
    if digest(canonical) != expected:
        raise ValueError('canonical parser fingerprint mismatch')
    if not duplicate.exists():
        return 0
    if digest(duplicate) != expected:
        raise ValueError('duplicate parser differs; refusing to remove it')
    size = duplicate.stat().st_size
    duplicate.unlink()
    return size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', required=True, type=Path)
    parser.add_argument('--python-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--site-packages', required=True, type=Path)
    parser.add_argument('--parser-runtime', required=True, type=Path)
    args = parser.parse_args()
    root, output = args.runtime_root.resolve(), args.output.resolve()
    python_root = args.python_root.resolve()
    for relative in ('python.exe', 'pythonw.exe', 'Lib/encodings/__init__.py'):
        if not (python_root / relative).is_file():
            parser.error(f'--python-root requires a full Windows Python distribution; missing {relative}')
    for label, directory in (('--site-packages', args.site_packages), ('--parser-runtime', args.parser_runtime)):
        if not directory.is_dir():
            parser.error(f'{label} must be an existing directory')
    if output.exists():
        raise ValueError('output must not exist; preserve previous bundles')
    output.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(args.python_root, output / 'python', ignore=ignore)
    site = output / 'python/Lib/site-packages'
    if site.exists():
        shutil.rmtree(site)  # Only the newly created bundle's standard-library site directory.
    shutil.copytree(args.site_packages.resolve(), site, ignore=ignore)
    test_plan = json.loads(subprocess.check_output([
        sys.executable, str(repo / 'scripts/python-test-plan.py'), '--root', str(output),
    ], text=True))
    for name in test_plan['exclude']:
        (output / name).unlink()
    for name in ('_virtualenv.py', '_virtualenv.pth'):
        (site / name).unlink(missing_ok=True)
    # Ignore host PYTHONHOME/PYTHONPATH, registry and user site-packages.
    (output / 'python/python312._pth').write_text('.\nDLLs\nLib\nLib/site-packages\nimport site\n', encoding='ascii')
    tool_plan = json.loads(subprocess.check_output([
        sys.executable, str(repo / 'scripts/search-tools-plan.py'), '--root', str(root / 'git'),
    ], text=True))
    for name in tool_plan['keep']:
        dest = output / 'tools/git' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / 'git' / name, dest)
    shutil.copytree(root / 'rg', output / 'tools/rg')
    shutil.copytree(args.parser_runtime.resolve(), output / 'runtime')
    remove_duplicate_parser(output)
    validate_static_extension_notices(output, repo / 'runtime/reviewed-notices/python-static')
    install_reviewed_notices(output, repo / 'runtime/reviewed-notices')
    install_application_notices(output, repo)
    commands = json.loads((root / 'commands.json').read_text())
    for entry in commands['commands'].values():
        entry['path'] = 'tools/' + entry['path']
        file = output / entry['path']
        with file.open('rb') as handle:
            assert hashlib.file_digest(handle, 'sha256').hexdigest() == entry['sha256']
    (output / 'commands.json').write_text(json.dumps(commands, indent=2), encoding='utf-8')
    shutil.copy2(repo / 'sidecar/uv.lock', output / 'uv.lock')
    env = dict(os.environ, TIKTOKEN_CACHE_DIR=str(output / 'tokenizer-data'))
    subprocess.run([str(output / 'python/python.exe'), '-c',
                    'from prtfm.llm.embeddings import token_count; assert token_count("bundle check") > 0'],
                   env=env, check=True, timeout=120)
    # The tokenizer cache is generated above; verify all reviewed data only after it exists.
    validate_static_extension_notices(output, repo / 'runtime/reviewed-notices/data-vendor')
    (output / 'bundle.json').write_text(json.dumps({
        'schema_version': 1, 'platform': 'Windows', 'architecture': 'AMD64',
        'python': 'python/python.exe', 'commands': 'commands.json', 'parser_runtime': 'runtime',
        'signed': False, 'release_ready': False,
    }, indent=2), encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
