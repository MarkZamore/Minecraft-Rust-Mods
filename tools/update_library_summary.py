"""Prepare a summary-only library release from the pinned public delivery.

Public verification uses verify_public_delivery.py before and after manifest upload.
No runtime is rebuilt or extracted. Existing payload archives remain identical.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

from inspect_mods import read_https
from native_build import ROOT, load

BASE = 'https://github.com/MarkZamore/Minecraft-Rust-Mods/releases/download/pack-latest/'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--previous-manifest-sha256', required=True)
    args = parser.parse_args()
    out = args.out.absolute()
    if out.exists() or not any((p / 'RUN.json').is_file() and p.parent.name == 'agent-runs' for p in out.parents):
        raise ValueError('Use a fresh output inside a registered agent run')
    git = ['git', '-C', str(ROOT)]
    if subprocess.check_output([*git, 'status', '--porcelain'], text=True).strip():
        raise ValueError('Use a clean source checkout')
    revision = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
    raw = read_https(BASE + 'pack-manifest.json', 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != args.previous_manifest_sha256:
        raise ValueError('The public manifest changed; review it before publishing')
    manifest = json.loads(raw)
    locked = load(ROOT / 'tools/distribution-lock.json')
    if {r['path']: {k: r[k] for k in ('sizeBytes', 'sha256')} for r in manifest['files']} != locked['files']:
        raise ValueError('Runtime changed; use full release acceptance')
    source = load(ROOT / 'library/content.json')
    out.mkdir()
    assets = out / 'assets'
    assets.mkdir()
    old_library = None
    for asset in [*manifest['assets'], manifest['library']]:
        name = asset['name']
        if Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Invalid archive name')
        data = read_https(BASE + name, 96 * 1024 * 1024)
        if len(data) != asset['sizeBytes'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise ValueError('Public archive size/SHA mismatch')
        if asset is manifest['library']:
            old_library = data
        else:
            (assets / name).write_bytes(data)
    with zipfile.ZipFile(io.BytesIO(old_library)) as archive:
        if sorted(archive.namelist()) != ['library.json', 'preview.jpg']:
            raise ValueError('Unexpected library members')
        previous = json.loads(archive.read('library.json'))
        preview = archive.read('preview.jpg')
    for field in ('id', 'name', 'relativePath', 'description', 'nativeProject', 'showNativeProject'):
        if previous.get(field) != source.get(field):
            raise ValueError('This operation only changes summary: ' + field)
    if preview != (ROOT / 'library/preview.jpg').read_bytes():
        raise ValueError('Preview changed')
    current = dict(previous, revision=revision, summary=source['summary'])
    if source['summary'] == source['description'] or source['summary'].count('.') != 1:
        raise ValueError('Summary must be one distinct sentence')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in [('library.json', (json.dumps(current, ensure_ascii=False, indent=2) + '\n').encode()), ('preview.jpg', preview)]:
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 0
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    descriptor = dict(name='library-' + digest + '.zip', sizeBytes=len(payload), sha256=digest, revision=revision)
    (assets / descriptor['name']).write_bytes(payload)
    updated = dict(manifest, revision=revision, library=descriptor)
    assert updated['files'] == manifest['files'] and updated['assets'] == manifest['assets']
    assert {k: v for k, v in current.items() if k not in ('revision', 'summary')} == {k: v for k, v in previous.items() if k not in ('revision', 'summary')}
    (out / 'pack-manifest.json').write_bytes((json.dumps(updated, ensure_ascii=False, indent=2) + '\n').encode())
    (out / 'previous-manifest.json').write_bytes(raw)
    print(json.dumps({'revision': revision, 'library': descriptor, 'runtimeUnchanged': True, 'descriptionUnchanged': True}))


if __name__ == '__main__':
    main()
