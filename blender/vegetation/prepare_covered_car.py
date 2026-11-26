"""Assemble the original car body and wheels into a grounded game prop."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare(folder, output):
    original=json.loads((folder/'source-manifest.json').read_bytes())
    original_hashes={r['path']:r['sha256'] for r in original['files']}
    assert all(sha256(folder/path)==digest for path,digest in original_hashes.items())
    source=folder/'covered_car_2k.blend'
    bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
    assert bpy.context.scene.unit_settings.scale_length==1.
    names=['covered_car',*(f'covered_car_wheel_{i:02}' for i in range(1,5))]
    parts=[bpy.context.scene.objects[name] for name in names]
    assert all(part.type=='MESH' and part.data.uv_layers for part in parts)
    bpy.ops.object.select_all(action='DESELECT')
    for part in parts:
        part.hide_set(False);part.select_set(True)
    bpy.context.view_layer.objects.active=parts[0]
    bpy.ops.object.convert(target='MESH')
    bpy.ops.object.join()
    prop=bpy.context.object
    prop.name='SM_covered_car'
    bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
    minimum=Vector([min(v.co[i] for v in prop.data.vertices) for i in range(3)])
    maximum=Vector([max(v.co[i] for v in prop.data.vertices) for i in range(3)])
    pivot=Vector(((minimum.x+maximum.x)/2,(minimum.y+maximum.y)/2,minimum.z))
    for vertex in prop.data.vertices:
        vertex.co-=pivot
    materials=list(prop.data.materials)
    assert len(materials)==1 and materials[0].name=='covered_car'
    maps={}
    for usage,suffix in (('base_color','diff_2k.jpg'),('normal','nor_gl_2k.exr'),
                         ('roughness','rough_2k.exr'),('metallic','metal_2k.exr')):
        path='textures/covered_car_'+suffix
        maps[usage]={'path':str(folder/path),'sha256':original_hashes[path]}
    fbx=output/'SM_covered_car.fbx'
    bpy.ops.export_scene.fbx(filepath=str(fbx),use_selection=True,object_types={'MESH'},
        add_leaf_bones=False,apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',
        axis_forward='-Y',axis_up='Z',use_mesh_modifiers=True)
    assert all(sha256(folder/path)==digest for path,digest in original_hashes.items())
    row={'id':'covered_car','asset':'covered_car','source_object':names,'kind':'parked_vehicle',
         'source':str(source),'source_sha256':original_hashes[source.name],
         'triangles':sum(len(p.vertices)-2 for p in prop.data.polygons),
         'fbx':str(fbx),'fbx_sha256':sha256(fbx),
         'bounds_m':{'min':list(minimum-pivot),'max':list(maximum-pivot)},
         'materials':[{'name':'covered_car','maps':maps}],
         'source_url':original['source_url'],'license':original['license']}
    return {'format':'metarescue.polyhaven-city-prop.v1','blender':bpy.app.version_string,'models':[row],
            'script_sha256':sha256(Path(__file__)),'source_catalog_sha256':sha256(folder/'source-manifest.json')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=Path('D:/MetaRescue-output/city-props/covered-car')/stamp
    output.mkdir(parents=True,exist_ok=False)
    manifest=prepare(args.source.resolve(strict=True),output)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('MR_CITY_PROP_PREPARED',output/'manifest.json',flush=True)
