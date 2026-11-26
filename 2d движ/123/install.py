"""Install the research-only files; refuses to overwrite different files."""
import argparse,hashlib,json
from pathlib import Path

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
root=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path);args=p.parse_args();target=args.project.resolve()
m=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
for rel,expected in m['dependencies'].items():
    path=target/rel
    if not path.is_file() or digest(path)!=expected:raise SystemExit('Missing or changed prerequisite: '+rel)
for rel,expected in m['files'].items():
    src=root/'overlay'/rel;dest=target/rel
    if digest(src)!=expected:raise SystemExit('Damaged package: '+rel)
    if dest.exists() and digest(dest)!=expected:raise SystemExit('Refusing to overwrite: '+rel)
for rel,expected in m['files'].items():
    dest=target/rel;dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():dest.write_bytes((root/'overlay'/rel).read_bytes())
print('Research files verified and installed; production unchanged.')
