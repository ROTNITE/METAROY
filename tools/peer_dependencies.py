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


def prepare(root, output):
    relative,expected,base=PEERS['v9']
    source=root/relative
    if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
        raise ValueError('Partner archive digest mismatch')
    with zipfile.ZipFile(source) as archive:
        data={}
        for name in ('package.json','package-lock.json'):
            member=archive.getinfo(base+'/'+name)
            if member.file_size>MAX_MANIFEST_BYTES:
                raise ValueError('Dependency manifest exceeds size limit')
            data[name]=json.loads(archive.read(member))
    count=validate_manifests(data['package.json'],data['package-lock.json'])
    output.mkdir(parents=True,exist_ok=True)
    for name,value in data.items():
        (output/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    return {'locked_packages':count,'lock_sha256':hashlib.sha256((output/'package-lock.json').read_bytes()).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1],args.output)))


if __name__=='__main__':
    main()
