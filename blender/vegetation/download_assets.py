"""Download selected original Poly Haven models and dependencies; stdlib only.

Existing files are verified, never silently replaced. API snapshots make reruns
use the same published checksums even if the upstream library changes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
import urllib.request


ROOT = Path(__file__).resolve().parent
ASSETS = {
    'tree_small_02': 'Лиственное дерево: основной кандидат для дворов и тротуаров.',
    'fir_tree_01': 'Взрослые пихты: сквер, периферия квартала; тяжёлый исходник.',
    'pine_tree_01': 'Взрослые сосны: сквер, периферия; тяжёлый исходник.',
    'pine_sapling_small': 'Молодые сосны: небольшие посадки во дворах.',
    'fir_sapling': 'Молодые пихты: небольшие посадки и варианты силуэта.',
    'fir_sapling_medium': 'Средняя пихта: переход между молодыми и взрослыми деревьями.',
    'tree_stump_01': 'Низкий неровный пень с корнями: остаток повреждённого дерева.',
    'tree_stump_02': 'Другой пень с корнями: вариативность разрушений.',
    'dead_tree_trunk': 'Поваленный ствол с обломанными концами: преграда на дороге.',
    'dead_tree_trunk_02': 'Крупный сломанный ствол: завал в парке или дворе.',
    'dry_branches_medium_01': 'Сломанные сухие ветки: мелкий древесный мусор.',
    'pine_roots': 'Открытые корни: край повреждённой клумбы/грунта; не готовый выворотень.',
}
HEADERS = {'User-Agent': 'MetaRescueAssetResearch/1.0 (selected CC0 models)'}
ENVIRONMENT = 'abandoned_hopper_terminal_02'


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(request, timeout=180)


def snapshot(url, path):
    if not path.exists():
        with fetch(url) as response:
            data = response.read()
        path.write_bytes(data)
    return path.read_bytes()


def hashes(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def get_file(job):
    path, meta, verify_only = job
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if verify_only:
            raise FileNotFoundError(path)
        partial = path.with_name(path.name + '.part')
        for attempt in range(3):
            try:
                with fetch(meta['url']) as response, partial.open('wb') as target:
                    while chunk := response.read(1024 * 1024):
                        target.write(chunk)
                sha, md5 = hashes(partial)
                if partial.stat().st_size != meta['size'] or md5 != meta['md5']:
                    raise ValueError('Upstream size/MD5 mismatch: ' + str(partial))
                # Exclusive rename on Windows preserves an existing final file.
                partial.rename(path)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
    sha, md5 = hashes(path)
    if path.stat().st_size != meta['size'] or md5 != meta['md5']:
        raise ValueError('Existing file differs; preserved: ' + str(path))
    return {'path': path.relative_to(ROOT).as_posix(), 'url': meta['url'],
            'bytes': path.stat().st_size, 'sha256': sha, 'md5': md5}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    catalog = {'retrieved_utc': datetime.now(timezone.utc).isoformat(),
               'license': 'CC0-1.0', 'license_url': 'https://polyhaven.com/license',
               'texture_resolution': '2k', 'unreal_import_verified': False, 'assets': []}
    if not args.verify_only:
        ROOT.mkdir(parents=True, exist_ok=True)
        snapshot(catalog['license_url'], ROOT / 'polyhaven-license.html')
        snapshot('https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt',
                 ROOT / 'CC0-1.0.txt')
    for ident, purpose in ASSETS.items():
        folder = ROOT / 'polyhaven' / ident
        if not args.verify_only:
            folder.mkdir(parents=True, exist_ok=True)
            snapshot('https://api.polyhaven.com/info/' + ident, folder / 'info.json')
            snapshot('https://api.polyhaven.com/files/' + ident, folder / 'files.json')
            snapshot('https://cdn.polyhaven.com/asset_img/thumbs/' + ident +
                     '.png?width=512&height=512', folder / 'preview.png')
        info = json.loads((folder / 'info.json').read_text(encoding='utf-8'))
        files = json.loads((folder / 'files.json').read_text(encoding='utf-8'))
        wanted = {}
        for fmt in ('blend', 'fbx'):
            meta = files[fmt]['2k'][fmt]
            wanted[f'{ident}_2k.{fmt}'] = meta
            for relative, dependency in meta.get('include', {}).items():
                if relative in wanted and wanted[relative]['md5'] != dependency['md5']:
                    raise ValueError('Conflicting dependency: ' + relative)
                wanted[relative] = dependency
        jobs = []
        for relative, meta in wanted.items():
            part = PurePosixPath(relative)
            target = folder.joinpath(*part.parts).resolve()
            if part.is_absolute() or not target.is_relative_to(folder.resolve()):
                raise ValueError('Unsafe dependency path: ' + relative)
            jobs.append((target, meta, args.verify_only))
        print(ident, len(jobs), 'files', round(sum(m['size'] for m in wanted.values()) / 1e6, 1),
              'MB', 'verify' if args.verify_only else 'download/verify', flush=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            downloaded = list(pool.map(get_file, jobs))
        catalog['assets'].append({'id': ident, 'name': info['name'], 'purpose_ru': purpose,
            'source_url': 'https://polyhaven.com/a/' + ident,
            'authors': info.get('authors', {}), 'source_polycount': info.get('polycount'),
            'files': downloaded})
        print('OK', ident, flush=True)
    # The branches .blend retains an absolute World HDRI path from its author.
    # Keep the original intact; provide that background for a separate local copy.
    extras = ROOT / 'extras'
    if not args.verify_only:
        extras.mkdir(parents=True, exist_ok=True)
        snapshot('https://api.polyhaven.com/files/' + ENVIRONMENT, extras / 'hdri-files.json')
        snapshot('https://api.polyhaven.com/info/' + ENVIRONMENT, extras / 'hdri-info.json')
    environment_files = json.loads((extras / 'hdri-files.json').read_text(encoding='utf-8'))
    environment_info = json.loads((extras / 'hdri-info.json').read_text(encoding='utf-8'))
    environment_file = get_file((extras / (ENVIRONMENT + '_1k.hdr'),
                                 environment_files['hdri']['1k']['hdr'], args.verify_only))
    catalog['supplements'] = [{'id': ENVIRONMENT, 'source_url': 'https://polyhaven.com/a/' + ENVIRONMENT,
                               'authors': environment_info.get('authors', {}), 'license': 'CC0-1.0',
                               'purpose': 'Original branches World background; local companion only',
                               'files': [environment_file]}]
    all_files = [f for a in catalog['assets'] + catalog['supplements'] for f in a['files']]
    catalog['file_count'] = len(all_files)
    catalog['bytes'] = sum(f['bytes'] for f in all_files)
    if not args.verify_only:
        (ROOT / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    print('VERIFIED', len(catalog['assets']), 'assets', catalog['file_count'], 'files',
          round(catalog['bytes'] / 1e9, 3), 'GB', flush=True)


if __name__ == '__main__':
    main()
