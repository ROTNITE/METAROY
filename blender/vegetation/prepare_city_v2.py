"""Prepare metre-scale Poly Haven geometry for the irregular district.

Run with Blender 4.5 in background, --disable-autoexec. Source files are read
and hashed, and every derivative is written to a fresh directory on D:.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from statistics import median

import bpy
from mathutils import Matrix

ROOT=Path(__file__).resolve().parent
VERSION='metarescue.polyhaven-foliage.v2'
OUTPUT=Path('D:/MetaRescue-output/city-vegetation')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
OUTPUT.mkdir(parents=True,exist_ok=False)

# One source LOD or named object per placement asset. The tree and sapling
# retain their separate masked leaf slots and branch material slots.
SPECS=(
    ('street_linden','tree_small_02','tree_small_02_LOD1','living_tree',72000),
    ('pine_sapling','pine_sapling_small','pine_sapling_small_a','living_tree',36000),
    ('fallen_trunk','dead_tree_trunk','dead_tree_trunk','fallen',22000),
    ('fallen_crown','dead_tree_trunk_02','dead_tree_trunk_02_LOD2','fallen',22000),
    ('stump_low','tree_stump_01','tree_stump_01','stump',14500),
    ('stump_split','tree_stump_02','tree_stump_02','stump',16500),
    ('dry_branches','dry_branches_medium_01','dry_branches_medium_01_a','branches',6500),
)


def sha256(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def maps(asset,slot,folder):
    base=slot
    if asset=='tree_small_02':
        base={'tree_small_02_trunk':asset,'tree_small_02_branches':asset+'_branch','tree_small_02_leaves':asset+'_leaves'}[slot]
    selected={}
    for usage,suffix in (('base_color','diff'),('normal','nor_gl'),('roughness','rough'),('opacity','alpha')):
        matches=sorted(folder.glob(f'{base}_{suffix}_2k.*'))
        if not matches:
            if usage=='opacity':continue
            raise FileNotFoundError(f'{asset} material {slot}: {base}_{suffix}')
        if len(matches)!=1:raise ValueError(f'Ambiguous map for {slot} {usage}')
        selected[usage]={'path':str(matches[0]),'sha256':sha256(matches[0])}
    return selected


def triangle_count(mesh):
    return sum(len(p.vertices)-2 for p in mesh.polygons)


def prepare(key,asset,object_name,kind,target):
    folder=ROOT/'polyhaven'/asset
    source=folder/(asset+'_2k.blend')
    original=sha256(source)
    bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
    assert abs(bpy.context.scene.unit_settings.scale_length-1)<1.e-8,asset
    selected=bpy.data.objects.get(object_name)
    assert selected and selected.type=='MESH' and len(selected.data.uv_layers),object_name
    source_triangles=triangle_count(selected.data)
    # Discard the source scene placement and keep its actual shape and metres.
    derived=bpy.data.objects.new('SM_'+key,selected.data.copy())
    transform=selected.matrix_world.to_3x3().to_4x4()
    derived.data.transform(transform)
    bpy.context.scene.collection.objects.link(derived)
    derived.matrix_world=Matrix.Identity(4)
    bpy.ops.object.select_all(action='DESELECT')
    derived.select_set(True);bpy.context.view_layer.objects.active=derived
    if source_triangles>target:
        modifier=derived.modifiers.new('Game triangle budget','DECIMATE')
        modifier.ratio=target/source_triangles;modifier.use_collapse_triangulate=True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    mesh=derived.data
    assert len(mesh.uv_layers)>0 and 0<triangle_count(mesh)<=target*1.015,(key,triangle_count(mesh))
    lowest=min(v.co.z for v in mesh.vertices)
    if kind=='living_tree':
        roots=[v.co for v in mesh.vertices if v.co.z<lowest+.15]
        origin=[median(v[axis] for v in roots) for axis in (0,1)]
    else:
        origin=[(min(v.co[i] for v in mesh.vertices)+max(v.co[i] for v in mesh.vertices))/2 for i in (0,1)]
    for vertex in mesh.vertices:
        vertex.co.x-=origin[0];vertex.co.y-=origin[1];vertex.co.z-=lowest
    lower=[float(min(v.co[i] for v in mesh.vertices)) for i in range(3)]
    upper=[float(max(v.co[i] for v in mesh.vertices)) for i in range(3)]
    assert abs(lower[2])<1.e-5 and upper[2]>0
    slots=[{'name':mat.name,'maps':maps(asset,mat.name,folder/'textures')} for mat in mesh.materials]
    assert slots and all(mesh.polygons[i].material_index<len(slots) for i in range(len(mesh.polygons)))
    target_fbx=OUTPUT/('SM_'+key+'.fbx')
    bpy.ops.export_scene.fbx(filepath=str(target_fbx),use_selection=True,object_types={'MESH'},add_leaf_bones=False,
        apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',axis_forward='-Y',axis_up='Z',use_mesh_modifiers=True)
    assert sha256(source)==original
    print('MR_VEGETATION_PREPARED',key,triangle_count(mesh),flush=True)
    return {'id':key,'asset':asset,'source_object':object_name,'kind':kind,'source':str(source),
            'source_sha256':original,'source_triangles':source_triangles,'triangles':triangle_count(mesh),
            'fbx':str(target_fbx),'fbx_sha256':sha256(target_fbx),'bounds_m':{'min':lower,'max':upper},
            'materials':slots,'license':'CC0-1.0','source_url':'https://polyhaven.com/a/'+asset}


models=[prepare(*spec) for spec in SPECS]
manifest={'format':VERSION,'blender':bpy.app.version_string,'models':models,
          'script_sha256':sha256(Path(__file__)),'source_catalog_sha256':sha256(ROOT/'catalog.json')}
(OUTPUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
print('MR_VEGETATION_MANIFEST',OUTPUT,flush=True)
