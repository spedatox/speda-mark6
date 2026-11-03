"""Build a credential-free release context, including uncommitted application work."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[1]
FILES = ('Dockerfile', '.dockerignore', 'docker-compose.yml', 'docker-compose.production.yml', 'pyproject.toml', 'uv.lock', 'LICENSE')
TREES = ('app', 'forge', 'scripts', 'tests', 'docs', 'packages/solus')
EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', 'out', 'dist', 'build'}


def package(destination):
    destination = Path(destination).resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError('Release artifacts must be outside the source checkout')
    destination.mkdir(parents=True, exist_ok=False)
    paths = [ROOT / name for name in FILES]
    for tree in TREES:
        paths.extend(p for p in (ROOT / tree).rglob('*') if p.is_file())
    entries = {}
    archive = destination / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in sorted(paths):
            relative = path.relative_to(ROOT)
            if EXCLUDED.intersection(relative.parts):
                continue
            if path.is_symlink() or any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                                        for p in (path, *path.parents) if p.is_relative_to(ROOT)):
                raise ValueError(f'Linked release entry: {relative}')
            if (path.name.startswith('.env') or path.suffix in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.pyc', '.log'}
                    or '.db-' in path.name or path.name.startswith('id_')):
                continue
            raw = path.read_bytes()
            entries[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
            tar.add(path, arcname=relative.as_posix(), recursive=False)
    manifest = {'files': entries, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    manifest['source_sha256'] = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return {'archive': str(archive), 'files': len(entries), 'source_sha256': manifest['source_sha256'],
            'archive_sha256': manifest['archive_sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    print(json.dumps(package(parser.parse_args().destination)))
