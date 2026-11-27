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
    target = effects.FOLDER + '/M_PhysicalSmoke'
    if effects.ASSETS.does_asset_exist(target):
        raise RuntimeError('Revision6 already exists; preserve the asset, use a fresh worktree for a new import')
    effects.ASSETS.make_directory(effects.FOLDER)
    material = effects.build_volume({'SmokeWisps': texture}, shader=shader.name)
    unreal.MRAssetTools.finish_asset_compilation()
    result = {'format': 'metarescue.smoke-v2.import.v1',
              'material': material.get_path_name(), 'source_texture': texture.get_path_name(),
              'shader_sha256': hashlib.sha256(shader.read_bytes()).hexdigest()}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    unreal.log('MR_SMOKE_V2_IMPORTED ' + str(report))


if __name__ == '__main__':
    main()
