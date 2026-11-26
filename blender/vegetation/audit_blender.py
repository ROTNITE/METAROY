"""Read a downloaded .blend without saving it; execute in Blender --background.

Usage: blender --background --factory-startup --disable-autoexec
  --python-exit-code 1 --python audit_blender.py -- MODEL.blend REPORT.json
"""
from array import array
import hashlib
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


args = sys.argv[sys.argv.index('--') + 1:]
source, output = (Path(p).resolve() for p in args)
before = sha256(source)
bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
objects = []
for obj in bpy.data.objects:
    row = {'name': obj.name, 'type': obj.type, 'hidden_render': obj.hide_render}
    if obj.type == 'MESH':
        mesh = obj.data
        lengths = array('i', [0]) * len(mesh.polygons)
        mesh.polygons.foreach_get('loop_total', lengths)
        corners = [obj.matrix_world @ Vector(p) for p in obj.bound_box]
        row.update(vertices=len(mesh.vertices), polygons=len(mesh.polygons),
                   source_triangle_estimate=sum(lengths) - 2 * len(lengths),
                   uv_layers=list(mesh.uv_layers.keys()),
                   bounds_min=[min(p[i] for p in corners) for i in range(3)],
                   bounds_max=[max(p[i] for p in corners) for i in range(3)],
                   materials=[s.material.name if s.material else None for s in obj.material_slots],
                   modifiers=[{'name': m.name, 'type': m.type,
                               'show_viewport': m.show_viewport, 'show_render': m.show_render}
                              for m in obj.modifiers])
    objects.append(row)
images = []
for img in bpy.data.images:
    if img.type in {'RENDER_RESULT', 'COMPOSITING'}:
        continue
    path = Path(bpy.path.abspath(img.filepath))
    images.append({'name': img.name, 'users': img.users, 'path': img.filepath,
                   'resolved_path': str(path), 'exists': bool(img.packed_file) or path.is_file(),
                   'packed': bool(img.packed_file), 'size': list(img.size)})
report = {'source': str(source), 'sha256': before, 'blender': bpy.app.version_string,
          'objects': objects, 'images': images,
          'scene_units': {'system': bpy.context.scene.unit_settings.system,
                          'scale_length': bpy.context.scene.unit_settings.scale_length},
          'source_unchanged': before == sha256(source),
          'evaluated_geometry_verified': False, 'unreal_import_verified': False}
meshes = [o for o in objects if o['type'] == 'MESH']
report['mesh_objects'] = len(meshes)
report['source_triangle_estimate'] = sum(o['source_triangle_estimate'] for o in meshes)
report['missing_images'] = [i['name'] for i in images if not i['exists']]
report['unreadable_images'] = [i['name'] for i in images if i['exists'] and min(i['size']) == 0]
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
assert meshes, 'No source mesh objects'
assert report['source_unchanged'], 'Original file changed'
assert not report['missing_images'], report['missing_images']
assert not report['unreadable_images'], report['unreadable_images']
print('MR_VEGETATION_AUDIT', source.stem, len(meshes), 'meshes',
      report['source_triangle_estimate'], 'source triangles (before modifiers)',
      len(images), 'images, OK', flush=True)
