"""Create a portable branches companion; keep the downloaded original unchanged.

Run in Blender with --background --factory-startup --disable-autoexec
--python-exit-code 1 --python blender/vegetation/prepare_branches_copy.py
"""
import hashlib
import json
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'polyhaven/dry_branches_medium_01/dry_branches_medium_01_2k.blend'
HDRI = ROOT / 'extras/abandoned_hopper_terminal_02_1k.hdr'
OUT = ROOT / 'prepared/dry_branches_medium_01_2k.blend'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


assert HDRI.is_file(), 'Run download_assets.py first'
assert not OUT.exists(), 'Preserve an existing prepared copy; choose a new version explicitly'
before = digest(SOURCE)
bpy.ops.wm.open_mainfile(filepath=str(SOURCE), load_ui=False, use_scripts=False)
image = bpy.data.images.get(HDRI.name)
assert image is not None, 'Expected original World HDRI reference'
assert not Path(bpy.path.abspath(image.filepath)).is_file(), 'Unexpected already valid reference'
image.filepath = str(HDRI)
image.reload()
assert min(image.size) > 0
for image in bpy.data.images:
    if image.type not in {'RENDER_RESULT', 'COMPOSITING'}:
        assert image.packed_file or Path(bpy.path.abspath(image.filepath)).is_file(), image.filepath
        image.pack()
OUT.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT))
assert before == digest(SOURCE), 'Original changed'
report = {'source': str(SOURCE), 'source_sha256': before, 'output': str(OUT),
          'output_sha256': digest(OUT), 'hdr_source': str(HDRI), 'hdr_sha256': digest(HDRI),
          'operation': 'Relink missing World HDRI; pack all four images. No mesh/material edits.',
          'blender': bpy.app.version_string, 'source_unchanged': True}
(OUT.parent / 'branches-copy.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('MR_VEGETATION_PORTABLE_BRANCHES', OUT, flush=True)
