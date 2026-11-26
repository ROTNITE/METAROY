from pathlib import Path
import hashlib, json, os, re, zipfile

root=Path.cwd()
archive=root/'artifacts/MetaEvolution-Rescue-project-2026-11-09.zip'
files={}
excluded_dirs={'__pycache__','.pytest_cache','Saved','Intermediate','DerivedDataCache','.vs','.git'}
roots=['docs','contracts','tests','tools','benchmarks','benchmark','environment','controller','web','unreal','концепция','ТЗ']
for folder in roots:
    for p in (root/folder).rglob('*'):
        if not p.is_file():
            continue
        rel=p.relative_to(root)
        if set(rel.parts)&excluded_dirs:
            continue
        if 'venv' in rel.parts and not (p.name=='README.txt' and p.parent.name=='venv'):
            continue
        if 'Binaries' in rel.parts and p.suffix not in {'.dll','.modules','.target'}:
            continue
        if p.suffix.lower() in {'.pyc','.pdb','.log','.suo','.sln'}:
            continue
        files[rel.as_posix()]=p.read_bytes()
for name in ['AGENTS.md','requirements.txt']:
    files[name]=(root/name).read_bytes()
files['README.md']=(root/'docs/distribution-readme.md').read_bytes()
files['PROJECT-README.md']=(root/'README.md').read_bytes()
for folder in ['artifacts/unreal','artifacts/metarescue-v03','artifacts/benchmark']:
    for p in (root/folder).glob('*'):
        if p.is_file() and p.suffix in {'.png','.json','.jsonl','.csv'}:
            files[p.relative_to(root).as_posix()]=p.read_bytes()
# Only archive copies of authored Markdown links are made portable.
# Imported benchmark releases retain their exact bytes.
prefix=root.as_posix()+'/'
for name, data in list(files.items()):
    if not name.endswith('.md') or name.startswith(('benchmarks/','benchmark/')):
        continue
    content=data.decode('utf-8')
    def replace_link(match):
        target=match.group(1)
        if not target.startswith(prefix):
            return match.group(0)
        project_target=target[len(prefix):]
        relative=os.path.relpath(project_target,Path(name).parent.as_posix()).replace('\\','/')
        return '](<'+relative+'>)'
    content=re.sub(r'\]\(<([^>]+)>\)',replace_link,content)
    files[name]=content.encode('utf-8')
manifest={'created_on':'2026-11-09','baseline':'user MetaRescue v0.3 unchanged','engine':'Unreal 5.8.4','entrypoint':'README.md','excluded':['engine/SDK','PDB','venv runtime','temporary builds','lectures'],'files':[]}
for name,data in sorted(files.items()):
    manifest['files'].append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
files['package-manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')+b'\n'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
    for name,data in sorted(files.items()):
        z.writestr('MetaEvolution-Rescue/'+name,data)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for item in manifest['files']:
        assert hashlib.sha256(z.read('MetaEvolution-Rescue/'+item['path'])).hexdigest()==item['sha256']
    required=['README.md','AGENTS.md','benchmarks/metarescue-v0.3/main.py','environment/world.py','contracts/protocol.py','unreal/MetaRescue/MetaRescue.uproject','unreal/MetaRescue/Content/Maps/RescueDistrict.umap','unreal/tools/verify_scene.py','artifacts/unreal/package-scene-check.json','artifacts/unreal/controls-check.json']
    assert all('MetaEvolution-Rescue/'+name in z.namelist() for name in required)
sha=hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.zip.sha256').write_text(sha+'  '+archive.name+'\n',encoding='utf-8')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(files),'sha256':sha},ensure_ascii=False))
