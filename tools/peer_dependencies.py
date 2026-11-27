"""Prepare pinned dependency inputs without importing or executing partner code.

Only the two JSON manifests of the known immutable archive enter the networked
build. All third-party processes run later with networking disabled. No install
scripts, archive configuration, credentials or application source in the build.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit
import zipfile

from tools.peer_trial import PEERS


MAX_MANIFEST_BYTES=4*1024*1024
REGISTRY='registry.npmjs.org'
SRI=re.compile(r'sha(?:256|384|512)-[A-Za-z0-9+/]+={0,2}\Z')
DEPENDENCY_FIELDS=('dependencies','devDependencies','optionalDependencies','peerDependencies')
# Registry package names and semver ranges only; prohibit aliases, Git, paths,
# tarball URLs, workspaces and arbitrary protocol specifiers.
RANGE=re.compile(r'[0-9A-Za-z.*^~<>=|+ -]+\Z')
NAME=re.compile(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+\Z')


def check_specs(document):
    for field in DEPENDENCY_FIELDS:
        dependencies=document.get(field,{})
        if not isinstance(dependencies,dict):
            raise ValueError('Expected dependency map')
        for name, spec in dependencies.items():
            if (not NAME.fullmatch(name) or not isinstance(spec,str) or not RANGE.fullmatch(spec)
                    or len(spec)>128):
                raise ValueError('Only ordinary registry dependencies are accepted')
    if document.get('workspaces') or document.get('overrides') or document.get('bundleDependencies') or document.get('bundledDependencies'):
        raise ValueError('Unsupported dependency indirection')


def validate_manifests(manifest, lock):
    if not isinstance(manifest,dict) or not isinstance(lock,dict):
        raise ValueError('Expected JSON objects')
    check_specs(manifest)
    if lock.get('lockfileVersion') not in (2,3) or not isinstance(lock.get('packages'),dict):
        raise ValueError('Expected a modern npm lockfile')
    packages=lock['packages']
    if '' not in packages or len(packages)>5000:
        raise ValueError('Lockfile has no root or exceeds package budget')
    for key, entry in packages.items():
        if not isinstance(entry,dict):
            raise ValueError('Invalid lockfile entry')
        check_specs(entry)
        if not key:
            continue
        path=PurePosixPath(key)
        if not key.startswith('node_modules/') or path.is_absolute() or '..' in path.parts or entry.get('link') or entry.get('inBundle'):
            raise ValueError('Unexpected local or linked dependency')
        source=entry.get('resolved')
        if not isinstance(source,str) or not isinstance(entry.get('integrity'),str) or not SRI.fullmatch(entry['integrity']):
            raise ValueError('Dependency must have an exact registry URL and integrity')
        url=urlsplit(source)
        if (url.scheme!='https' or url.netloc!=REGISTRY or url.query or url.fragment
                or not url.path.endswith('.tgz')):
            raise ValueError('Unexpected dependency source')
    return len(packages)-1


def dependency_manifest(original):
    if not isinstance(original,dict):
        raise ValueError('Expected package manifest object')
    check_specs(original)
    # Do not place application scripts, package config or arbitrary metadata
    # in the network-enabled dependency resolution directory.
    result={'name':'stage3-frozen-peer-runtime','version':'1.0.0','private':True}
    for field in DEPENDENCY_FIELDS:
        if field in original:
            result[field]=original[field]
    if 'peerDependenciesMeta' in original:
        result['peerDependenciesMeta']=original['peerDependenciesMeta']
    return result


def validate_directory(output):
    manifest=json.loads((output/'package.json').read_bytes())
    lock_file=output/'package-lock.json'
    if lock_file.stat().st_size>MAX_MANIFEST_BYTES:
        raise ValueError('Generated dependency lock exceeds size limit')
    lock=json.loads(lock_file.read_bytes())
    count=validate_manifests(manifest,lock)
    return {'locked_packages':count,'lock_sha256':hashlib.sha256(lock_file.read_bytes()).hexdigest()}


def prepare(root, output):
    relative,expected,base=PEERS['v9']
    source=root/relative
    if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
        raise ValueError('Partner archive digest mismatch')
    with zipfile.ZipFile(source) as archive:
        def read(name):
            member=archive.getinfo(base+'/'+name)
            if member.file_size>MAX_MANIFEST_BYTES:
                raise ValueError('Dependency manifest exceeds size limit')
            return archive.read(member)
        raw=read('package.json')
        original=json.loads(raw)
        manifest=dependency_manifest(original)
        archived=base+'/package-lock.json' in archive.namelist()
        lock=json.loads(read('package-lock.json')) if archived else None
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Expected a new dependency directory')
    (output/'package.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    result={'archive_manifest_sha256':hashlib.sha256(raw).hexdigest(),
            'lock_source':'archive' if archived else 'generated-required'}
    if archived:
        validate_manifests(manifest,lock)
        (output/'package-lock.json').write_text(json.dumps(lock,ensure_ascii=False),encoding='utf-8')
        result.update(validate_directory(output))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    result=validate_directory(args.output) if args.validate_only else prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps(result))


if __name__=='__main__':
    main()
