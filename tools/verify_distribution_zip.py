"""Verify a staged distribution ZIP without extracting or executing it.

Checks archive members and the internal freeze manifest. Does not certify a
build, prove the manifest's authenticity, or run Unreal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import unicodedata
import zipfile
from pathlib import Path

SCENARIOS=('core-scenarios-v1.json','quarter.json','scenario.json','watercraft-hydrostatics-r1.json')
MAX_MANIFEST=16*1024*1024
MAX_MEMBERS=100000
MAX_UNCOMPRESSED=64*1024**3
RESERVED=re.compile(r'^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$',re.I)


def member_name(name):
    parts=name.split('/')
    if not name or any(not part or part in ('.','..') or part.endswith((' ','.'))
        or any(ord(c)<32 or c in '\\:<>"|?*' for c in part)
        or RESERVED.fullmatch(part.split('.')[0].rstrip(' ')) for part in parts):
        raise ValueError('Unsafe Windows ZIP path: '+repr(name))
    return unicodedata.normalize('NFC',name).casefold()


def unique_object(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('Duplicate manifest key: '+key)
        result[key]=value
    return result


def verify_zip(path,*,max_bytes=MAX_UNCOMPRESSED):
    if type(max_bytes) is not int or max_bytes<=0:raise ValueError('Invalid decompression limit')
    with zipfile.ZipFile(path) as archive:
        files={};seen=set();declared=0
        members=archive.infolist()
        if len(members)>MAX_MEMBERS:raise ValueError('Too many ZIP members')
        for info in members:
            if info.orig_filename!=info.filename:raise ValueError('Truncated or normalized ZIP member name')
            name=info.filename[:-1] if info.is_dir() else info.filename
            folded=member_name(name)
            if folded in seen:raise ValueError('Duplicate/case-colliding ZIP member: '+name)
            seen.add(folded)
            mode=(info.external_attr>>16)&0xffff
            kind=stat.S_IFMT(mode)
            if (kind not in (0,stat.S_IFDIR,stat.S_IFREG) or info.flag_bits&1 or
                (info.create_system==0 and info.external_attr&0x400) or
                (info.is_dir() and (kind==stat.S_IFREG or info.file_size)) or
                (not info.is_dir() and kind==stat.S_IFDIR)):
                raise ValueError('Link, special, encrypted or inconsistent ZIP member: '+name)
            if info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
                raise ValueError('Unsupported Windows ZIP compression: '+name)
            declared+=info.file_size
            if declared>max_bytes:raise ValueError('ZIP exceeds decompression limit')
            if not info.is_dir():files[name]=info
        folded_files={member_name(name) for name in files}
        for name in seen:
            pieces=name.split('/')
            if any('/'.join(pieces[:i]) in folded_files for i in range(1,len(pieces))):
                raise ValueError('File/directory collision: '+name)
        manifests=[name for name in files if name in ('SHA256.json','MetaEvolution-Rescue/SHA256.json')]
        if len(manifests)!=1:raise ValueError('Expected exactly one distribution manifest')
        manifest_name=manifests[0];prefix=manifest_name[:-len('SHA256.json')]
        if files[manifest_name].file_size>MAX_MANIFEST:raise ValueError('Oversized manifest')
        manifest=json.loads(archive.read(manifest_name),object_pairs_hook=unique_object)
        if not isinstance(manifest,dict) or not manifest:raise ValueError('Invalid manifest')
        for name,digest in manifest.items():
            member_name(name)
            if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
                raise ValueError('Invalid SHA-256: '+name)
        if set(files)!={manifest_name}|{prefix+name for name in manifest}:
            raise ValueError('ZIP membership differs from frozen manifest')
        required={'Start-MetaRescue.cmd','python/python.exe','tools/run_product.py',
                  'tools/run_mission.py','environment/main.py'}
        missing=required-set(manifest)
        if missing:raise ValueError('Missing runtime files: '+str(sorted(missing)))
        runtimes=['runtime/Windows/MetaRescue','runtime/MetaRescue']
        exe=[root for root in runtimes if root+'/Binaries/Win64/MetaRescue-Win64-Shipping.exe' in manifest]
        if len(exe)!=1:raise ValueError('Expected one Shipping executable')
        for filename in SCENARIOS:
            native=exe[0]+'/Content/Scenario/'+filename
            mirror='unreal/MetaRescue/Content/Scenario/'+filename
            if native not in manifest or mirror not in manifest or manifest[native]!=manifest[mirror]:
                raise ValueError('Missing or divergent scenario copy: '+filename)
        total=0
        for name,digest in manifest.items():
            checksum=hashlib.sha256();size=0;entry=files[prefix+name]
            with archive.open(entry) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    checksum.update(block);total+=len(block);size+=len(block)
                    if total>max_bytes or size>entry.file_size:raise ValueError('ZIP exceeds decompression limit')
            if size!=entry.file_size:raise ValueError('ZIP member size mismatch: '+name)
            if checksum.hexdigest()!=digest:raise ValueError('SHA-256 mismatch: '+name)
        return {'files':len(manifest),'uncompressed_bytes':total,'integrity_verified':True,
                'native_execution_verified':False,'scope':'membership, CRC, SHA-256, runtime and scenario paths'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip',type=Path)
    parser.add_argument('--max-uncompressed-gib',type=int,default=64,
                        help='Maximum declared and read payload size in GiB (default 64)')
    args=parser.parse_args()
    if not 1<=args.max_uncompressed_gib<=512:parser.error('decompression limit must be 1..512 GiB')
    print(json.dumps(verify_zip(args.zip,max_bytes=args.max_uncompressed_gib*1024**3),indent=2))


if __name__=='__main__':main()
