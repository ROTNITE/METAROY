"""UNEXECUTED candidate: non-destructive distance-field authoring for Quarter.

Review before promotion to unreal/tools. Run first after library import, then
build_quarter_v3.py, then run with -MRLightingBakeScene for the saved map.
The latter bakes every tagged interior box in every house, never a camera subset.
It intentionally leaves large ground / road HISM untouched pending DF diagnosis.
"""
from pathlib import Path
import hashlib
import json
import math
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[1]
MANIFEST = ROOT / 'unreal/MetaRescue/Content/Scenario/library-v3.json'
LIB = unreal.EditorAssetLibrary
MESHES = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
if MESHES is None:
    # Same stateless commandlet fallback already used by import_rescue_assets.
    MESHES = unreal.new_object(unreal.StaticMeshEditorSubsystem)
VERSION = 'quarter-lighting-v1-df3-cards24'
DEST = '/Game/RescueAssets/LibraryV3/Lighting'


def xyz(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


def package_file(path):
    package = path.split('.')[0]
    if package.startswith('/Game/'):
        return ROOT / 'unreal/MetaRescue/Content' / (package[6:] + '.uasset')
    if package.startswith('/Engine/'):
        return Path(unreal.Paths.engine_content_dir()) / (package[8:] + '.uasset')
    raise RuntimeError('Unexpected package root: ' + package)


def source_fingerprint(source, scale):
    digest = hashlib.sha256()
    digest.update(json.dumps({'version': VERSION, 'source': source.get_path_name(),
                              'baked_component_scale': scale}, sort_keys=True).encode())
    digest.update(package_file(source.get_path_name()).read_bytes())
    pending = [slot.get_editor_property('material_interface')
               for slot in source.get_editor_property('static_materials')]
    seen = set()
    while pending:
        material = pending.pop()
        if material is None or material.get_path_name() in seen:
            continue
        key = material.get_path_name(); seen.add(key)
        digest.update(key.encode()); digest.update(package_file(key).read_bytes())
        if isinstance(material, unreal.MaterialInstance):
            pending.append(material.get_editor_property('parent'))
    return digest.hexdigest()[:16]


def clone_for_lighting(source, scale=None):
    """Preserve geometry/material originals and invalidate path-only caches."""
    factor = scale or [1.0, 1.0, 1.0]
    assert all(math.isfinite(v) and v > 0 for v in factor), factor
    path = DEST + '/' + source.get_name() + '_' + source_fingerprint(source, factor)
    copy = unreal.load_asset(path)
    if copy is None:
        copy = LIB.duplicate_asset(source.get_path_name(), path)
        if copy is None:
            raise RuntimeError('Cannot duplicate mesh: ' + source.get_path_name())
        for lod in range(MESHES.get_lod_count(copy)):
            settings = MESHES.get_lod_build_settings(copy, lod)
            old = xyz(settings.get_editor_property('build_scale3d'))
            settings.set_editor_property('build_scale3d', unreal.Vector(*[a*b for a,b in zip(old, factor)]))
            settings.set_editor_property('distance_field_resolution_scale', 3.0)
            settings.set_editor_property('max_lumen_mesh_cards', 24)
            # Installed StaticMeshEditorSubsystem.cpp calls PostEditChange here,
            # explicitly requesting a rebuild after the correct materials exist.
            MESHES.set_lod_build_settings(copy, lod, settings)
        if scale is not None:
            # Engine Cube simple collision would otherwise remain its old size.
            # These are static enclosing surfaces, so use the rebuilt triangles.
            body = copy.get_editor_property('body_setup')
            body.set_editor_property('collision_trace_flag', unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        if not LIB.save_loaded_asset(copy):
            raise RuntimeError('Cannot save derived lighting mesh: ' + path)
    source_size = xyz(source.get_bounds().box_extent)
    copy_size = xyz(copy.get_bounds().box_extent)
    expected = [a*b for a,b in zip(source_size, factor)]
    assert all(abs(a-b) <= max(.05, abs(b)*.0001) for a,b in zip(copy_size, expected)), (path, copy_size, expected)
    settings = MESHES.get_lod_build_settings(copy, 0)
    assert settings.get_editor_property('distance_field_resolution_scale') == 3.0
    assert settings.get_editor_property('max_lumen_mesh_cards') == 24
    return copy


def prepare_library():
    record = json.loads(MANIFEST.read_text(encoding='utf-8'))
    changes = []
    LIB.make_directory(DEST)
    for asset in record['assets']:
        if asset['id'] not in {'GlazingKit', 'UrbanKit'}:
            continue
        for part in asset['parts']:
            if asset['id'] == 'UrbanKit' and not part['object'].startswith('wall_'):
                continue
            source_path = part.get('lighting_source_mesh', part['mesh'])
            source = unreal.load_asset(source_path)
            assert isinstance(source, unreal.StaticMesh), source_path
            derived = clone_for_lighting(source)
            part['lighting_source_mesh'] = source_path
            part['mesh'] = derived.get_path_name()
            changes.append({'asset': asset['id'], 'part': part['object'],
                            'source': source_path, 'derived': part['mesh']})
    record['lighting_revision'] = VERSION
    MANIFEST.write_text(json.dumps(record, indent=2), encoding='utf-8')
    return changes


def bake_scene_interiors():
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    assert level.load_level('/Game/Maps/RescueQuarter')
    cls = unreal.load_class(None, '/Script/MetaRescue.MRWorld')
    world = next(a for a in actors.get_all_level_actors() if a.get_class() == cls)
    changes = []; modified = 0
    for component in world.get_components_by_class(unreal.StaticMeshComponent):
        tags = {str(t) for t in component.get_editor_property('component_tags')}
        if 'InteriorLightingSurface' not in tags:
            continue
        assert not isinstance(component, unreal.InstancedStaticMeshComponent), component.get_name()
        mesh = component.get_editor_property('static_mesh')
        if mesh.get_path_name().startswith(DEST + '/'):
            assert xyz(component.get_world_scale()) == [1.0, 1.0, 1.0]
            assert mesh.get_editor_property('body_setup').get_editor_property('collision_trace_flag') == unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
            settings = MESHES.get_lod_build_settings(mesh, 0)
            assert settings.get_editor_property('distance_field_resolution_scale') == 3.0
            assert settings.get_editor_property('max_lumen_mesh_cards') == 24
            changes.append({'component': component.get_name(), 'derived': mesh.get_path_name(),
                            'action': 'verified_existing_no_change'})
            continue
        assert mesh.get_path_name().split('.')[0] == '/Engine/BasicShapes/Cube', mesh.get_path_name()
        scale = xyz(component.get_world_scale())
        before_location = xyz(component.get_world_location())
        before_rotation = component.get_world_rotation()
        materials = [component.get_material(i) for i in range(component.get_num_materials())]
        collision = component.get_editor_property('body_instance').get_editor_property('collision_enabled')
        baked = clone_for_lighting(mesh, scale)
        component.set_static_mesh(baked)
        component.set_world_scale3d(unreal.Vector(1, 1, 1))
        for i, material in enumerate(materials):
            component.set_material(i, material)
        assert xyz(component.get_world_location()) == before_location
        assert component.get_world_rotation() == before_rotation
        assert component.get_editor_property('body_instance').get_editor_property('collision_enabled') == collision
        changes.append({'component': component.get_name(), 'source': mesh.get_path_name(),
                        'derived': baked.get_path_name(), 'previous_scale': scale,
                        'preserved_location_cm': before_location, 'action': 'baked_component_scale'})
        modified += 1
    assert changes, 'No tagged interior surfaces found; inspect map/revision before saving'
    if modified and not level.save_current_level():
        raise RuntimeError('Cannot save Quarter lighting derivative map')
    return changes


if __name__ == '__main__':
    bake = '-MRLightingBakeScene' in unreal.SystemLibrary.get_command_line()
    rows = bake_scene_interiors() if bake else prepare_library()
    report = {'revision': VERSION, 'mode': 'bake_scene' if bake else 'prepare_library',
              'changes': rows, 'runtime_lighting_verified': False}
    target = ROOT / 'artifacts/quarter-v3' / ('lighting-baked-boxes.json' if bake else 'lighting-mesh-clones.json')
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    unreal.log('MR_LIGHTING_CANDIDATE_AUTHORED ' + str(len(rows)))
