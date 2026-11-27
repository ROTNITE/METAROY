"""Inventory immutable partner archives without executing or extracting them.

The LFS pointer in the same commit is the expected SHA-256 and length.
Only metadata of candidate launch files is printed; no archive content.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import stat
import subprocess
import sys
import zipfile


PACKAGES = (
    "независимые пары/metaroy_lab.zip",
    "независимые пары/metaroy_portable_v0_1.zip",
    "независимые пары/Atlas_Probe_0.2.zip",
    "controller_team2/metaroi-controller-portable-v9.zip",
)
MAX_UNPACKED = 200 * 1024 * 1024
MAX_FILES = 10000


def inventory(root, relative):
    pointer = subprocess.check_output(
        ['git','show','HEAD:'+relative],cwd=root,text=True,encoding='utf-8')
    fields = dict(line.split(' ',1) for line in pointer.splitlines() if ' ' in line)
    expected = fields['oid'].removeprefix('sha256:')
    path = root / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=expected or path.stat().st_size!=int(fields['size']):
        raise ValueError(f'Archive does not match Git LFS pointer: {relative}')
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members)>MAX_FILES or sum(m.file_size for m in members)>MAX_UNPACKED:
            raise ValueError(f'Archive exceeds inspection budget: {relative}')
        seen=set()
        candidate=[]
        for member in members:
            name=PurePosixPath(member.filename)
            key=member.filename.rstrip('/').casefold()
            if (name.is_absolute() or '..' in name.parts or not key or key in seen
                    or '\\' in member.filename or ':' in name.parts[0]
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError(f'Unsafe archive member in {relative}')
            seen.add(key)
            if member.is_dir():
                continue
            base=name.name.lower()
            if (base.startswith('readme') or base in ('pyproject.toml','package.json','requirements.txt',
                    'dockerfile','main.py','server.py','app.py','controller.py','environment.py','__main__.py')
                    or base.endswith(('.mjs','.sh','.ps1'))):
                candidate.append(str(name))
        bad=archive.testzip()
        if bad:
            raise ValueError(f'Archive CRC verification failed: {relative}')
    return {'path':relative,'sha256':digest,'bytes':path.stat().st_size,
            'members':len(members),'launch_candidates':sorted(candidate)[:120],
            'crc_verified':True,'executed':False}


def main():
    root=Path(__file__).resolve().parents[1]
    result=[]
    failed=False
    for relative in PACKAGES:
        try:
            result.append(inventory(root,relative))
        except (OSError,ValueError,KeyError,zipfile.BadZipFile,RuntimeError,subprocess.CalledProcessError) as error:
            failed=True
            result.append({'path':relative,'error':str(error),'executed':False})
    print(json.dumps({'packages':result,'passed':not failed},ensure_ascii=False,indent=2))
    return int(failed)


if __name__=='__main__':
    sys.exit(main())
