"""Generate an exposure-independent rendering of the delivered water surface."""
import json
import os
from pathlib import Path
import sys

import unreal

sys.path.insert(0, os.environ.get('MR_UNREAL_ART_TOOLS', str(Path(__file__).resolve().parent)))
import build_disaster_effects as art
from build_exploration_night import exposure_independent_emissive


def main():
    report = Path(os.environ['MR_EXPLORATION_ART_REPORT']).resolve()
    art.FOLDER = '/Game/EffectsV3/ExplorationWaterR2'
    asset = art.FOLDER+'/M_RememberedFlood'
    if report.exists() or art.ASSETS.does_asset_exist(asset):
        raise ValueError('Use a new asset revision and evidence path')
    art.ASSETS.make_directory(art.FOLDER)
    detail = unreal.load_asset('/Game/EffectsV3/Revision5/Textures/T_SmokeBillow')
    if not isinstance(detail, unreal.Texture2D):
        raise ValueError('The checked CC0 particle detail is required')
    material = art.material('M_RememberedFlood', luminous=True)
    material.set_editor_property('two_sided', True)
    world = art.node(material, unreal.MaterialExpressionWorldPosition, -1200, 0)
    camera = art.node(material, unreal.MaterialExpressionCameraPositionWS, -1200, 190)
    field = art.node(material, unreal.MaterialExpressionVertexColor, -1200, 380)
    stamp = art.node(material, unreal.MaterialExpressionTextureCoordinate, -1200, 530, coordinate_index=3)
    noise = art.node(material, unreal.MaterialExpressionTextureObject, -1200, 740, texture=detail)
    shade = art.custom(material, '''
        float2 flowMetersPerSecond = (Field.rg * 255. - 128.) * (8. / 127.);
        float2 metres = World.xy * .01;
        float2 advected = metres - flowMetersPerSecond * Stamp.x * .38;
        float depth = Field.b * 4.;
        float3 eye = normalize(Camera - World + float3(0., 0., .01));
        float grazing = pow(saturate(1. - abs(eye.z)), 3.);
        float ripple = .5 + .24 * sin(dot(advected, float2(1.48, .91)) + Stamp.x * .72)
                          + .18 * cos(dot(advected, float2(-.56, 2.03)) - Stamp.x * .85);
        float grain = Texture2DSample(Detail, DetailSampler, frac(advected * .23)).a;
        float shore = (1. - smoothstep(.03, .40, depth)) * smoothstep(.4, .73, grain);
        float3 shallow = float3(.067, .082, .072);
        float3 deep = float3(.025, .055, .076);
        float3 body = lerp(shallow, deep, smoothstep(.16, 1.6, depth));
        float3 reflection = float3(.052, .097, .144) * (.4 + .6 * ripple);
        return body * (.89 + .18 * ripple) + reflection * grazing
             + float3(.074, .077, .063) * shore * .38;
    ''', unreal.CustomMaterialOutputType.CMOT_FLOAT3,
        [('World', world, ''), ('Camera', camera, ''), ('Field', field, ''),
         ('Stamp', stamp, ''), ('Detail', noise, '')], -410, 0)
    exposure_independent_emissive(material, shade)
    art.save(material)
    unreal.MRAssetTools.finish_asset_compilation()
    report.write_text(json.dumps({
        'format': 'metarescue.remembered-water-material.v2',
        'asset': material.get_path_name(),
        'detail': detail.get_path_name(),
        'clock': 'observation time in UV3.x',
        'field': 'reported flow and depth per vertex',
        'alters_geometry': False,
    }, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
