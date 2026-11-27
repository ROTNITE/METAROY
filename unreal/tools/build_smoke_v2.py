"""Unreal Python commandlet; launch via the common Unreal lock.

Only the new smoke material is authored. The imported revision 5 textures
and every previous package remain untouched.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_disaster_effects as effects
from smoke_shader_source import remembered_source


def main():
    report = Path(os.environ['MR_SMOKE_V2_REPORT']).resolve()
    if report.exists():
        raise FileExistsError(report)
    effects.FOLDER = '/Game/EffectsV3/Revision6'
    source = '/Game/EffectsV3/Revision5/Textures/T_SmokeWisps'
    texture = unreal.load_asset(source)
    if not isinstance(texture, unreal.Texture2D):
        raise RuntimeError('Missing previously verified smoke texture: ' + source)
    shader = Path(__file__).resolve().parent / 'shaders/disaster_smoke_v2.hlsl'
    targets = ('M_PhysicalSmoke', 'M_RememberedSmoke')
    if any(effects.ASSETS.does_asset_exist(effects.FOLDER + '/' + name) for name in targets):
        raise RuntimeError('Revision6 already exists; preserve the asset, use a fresh worktree for a new import')
    effects.ASSETS.make_directory(effects.FOLDER)
    material = effects.build_volume({'SmokeWisps': texture}, shader=shader.name)
    # Frozen observation samples use identical extinction but clip to the
    # delivered optical cell. No live time or neighbouring unseen smoke.
    remembered = effects.build_volume({'SmokeWisps': texture}, shader=shader.name, name='M_RememberedSmoke')
    opacity = effects.EDIT.get_material_property_input_node(remembered, unreal.MaterialProperty.MP_OPACITY)
    inputs = list(opacity.get_editor_property('inputs'))
    for name in ('ClipMin', 'ClipMax'):
        pin = unreal.CustomInput()
        pin.set_editor_property('input_name', name)
        inputs.append(pin)
    opacity.set_editor_property('inputs', inputs)
    effects.connect(effects.vector(remembered, 'ClipMin', (-1e8,-1e8,-1e8), 2100), 'RGB', opacity, 'ClipMin')
    effects.connect(effects.vector(remembered, 'ClipMax', (1e8,1e8,1e8), 2250), 'RGB', opacity, 'ClipMax')
    opacity.set_editor_property('code', remembered_source(opacity.get_editor_property('code')))
    stamp = effects.scalar(remembered, 'SnapshotTime', 0, 2400)
    for expression in effects.EDIT.get_material_expressions(remembered):
        if isinstance(expression, unreal.MaterialExpressionCustom):
            if any(str(pin.get_editor_property('input_name')) == 'Clock' for pin in expression.get_editor_property('inputs')):
                effects.connect(stamp, '', expression, 'Clock')
    remembered.set_editor_property('allow_translucent_custom_depth_writes', True)
    remembered.set_editor_property('opacity_mask_clip_value', .01)
    remembered.set_editor_property('used_with_instanced_static_meshes', True)
    effects.save(remembered)
    unreal.MRAssetTools.finish_asset_compilation()
    result = {'format': 'metarescue.smoke-v2.import.v1',
              'materials': [material.get_path_name(), remembered.get_path_name()], 'source_texture': texture.get_path_name(),
              'shader_sha256': hashlib.sha256(shader.read_bytes()).hexdigest()}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    unreal.log('MR_SMOKE_V2_IMPORTED ' + str(report))


if __name__ == '__main__':
    main()
