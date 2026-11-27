"""Build the isolated command-view materials for city 2.3."""
import json
import os
from pathlib import Path
import sys

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_disaster_effects import ASSETS, EDIT, TOOLS, connect, custom, node, property_input, save, scalar, texture_object, vector


FOLDER = '/Game/EffectsV3/ExplorationBackdropR2'


def create(name, *, sky=False, ground=False, post=False):
    if ASSETS.does_asset_exist(FOLDER + '/' + name):
        raise ValueError('Use a fresh night-art revision to preserve existing evidence')
    material = TOOLS.create_asset(name, FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    if post:
        material.set_editor_property('material_domain', unreal.MaterialDomain.MD_POST_PROCESS)
        material.set_editor_property('blendable_location', unreal.BlendableLocation.BL_SCENE_COLOR_AFTER_TONEMAPPING)
    else:
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property('two_sided', sky)
        material.set_editor_property('used_with_instanced_static_meshes', True)
    if ground:
        material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
    return material


def world_position(material):
    return node(material, unreal.MaterialExpressionWorldPosition, -1100, -220)


def exposure_independent_emissive(material, color):
    corrected = node(material, unreal.MaterialExpressionEyeAdaptationInverse, 10, -240)
    light_value, _ = EDIT.get_material_expression_input_names(corrected)
    connect(color, '', corrected, light_value)
    property_input(corrected, '', material, unreal.MaterialProperty.MP_EMISSIVE_COLOR)


def reveal_inputs(material, world):
    default = unreal.load_asset('/Engine/EngineResources/DefaultTexture')
    if default is None:
        raise ValueError('No default texture for the delivered mask parameter')
    mask = texture_object(material, default, 'DeliveredMask', 50)
    return [
        ('World', world, ''),
        ('Mask', mask, ''),
        ('Origin', vector(material, 'GridOrigin', (0., 0., 0.), 220), ''),
        ('Cells', vector(material, 'GridCells', (50., 50., 0.), 360), ''),
        ('CellCm', scalar(material, 'CellSizeCm', 500., 510), ''),
    ]


def night_sky():
    material = create('M_NightSky', sky=True)
    center = node(material, unreal.MaterialExpressionObjectPositionWS, -500, 0)
    color = custom(material, """
        float3 normal = normalize(World - Center);
        float altitude = smoothstep(0., .86, abs(normal.z));
        return lerp(float3(.026,.036,.055), float3(.006,.010,.024), altitude);
    """, unreal.CustomMaterialOutputType.CMOT_FLOAT3, [('World', world_position(material), ''), ('Center', center, '')])
    exposure_independent_emissive(material, color)
    save(material)
    return material


def night_ground():
    material = create('M_NightGround', ground=True)
    world = world_position(material)
    tint = vector(material, 'NightTint', (.025, .035, .045), -400)
    color = custom(material, """
        float2 meters = World.xy * .01;
        float broadVariation = sin(meters.x * .31 + meters.y * .47) *
            sin(meters.x * .58 - meters.y * .34);
        float grains = frac(sin(dot(floor(meters * 18.), float2(33.3, 91.7))) * 7219.13);
        return Tint.rgb * (.81 + .13 * broadVariation + .12 * grains);
    """, unreal.CustomMaterialOutputType.CMOT_FLOAT3, [('World', world, ''), ('Tint', tint, '')], -450, -220)
    exposure_independent_emissive(material, color)
    opacity = custom(material, """
        float2 grid = (World.xy - Origin.xy) / CellCm;
        if (any(grid < 0.) || any(grid >= Cells.xy)) return 1.;
        float solid = Texture2DSampleLevel(Mask, MaskSampler, (floor(grid) + .5) / Cells.xy, 0).r;
        float neighbours = Texture2DSampleLevel(Mask, MaskSampler, grid / Cells.xy, 0).r;
        float cover = step(.5, solid) * smoothstep(.48, .92, neighbours);
        float grain = frac(sin(dot(floor(World.xy / 13.), float2(17.7, 75.3))) * 125.61);
        return step(cover, grain);
    """, unreal.CustomMaterialOutputType.CMOT_FLOAT1, reveal_inputs(material, world), -450, 330)
    property_input(opacity, '', material, unreal.MaterialProperty.MP_OPACITY_MASK)
    save(material)
    return material


def knowledge_grade():
    material = create('M_KnowledgeGrade', post=True)
    textures = [
        ('Scene', unreal.SceneTextureId.PPI_POST_PROCESS_INPUT0),
        ('Depth', unreal.SceneTextureId.PPI_SCENE_DEPTH),
        ('RememberedDepth', unreal.SceneTextureId.PPI_CUSTOM_DEPTH),
        ('Stencil', unreal.SceneTextureId.PPI_CUSTOM_STENCIL),
    ]
    inputs = [(name, node(material, unreal.MaterialExpressionSceneTexture, -1400, i * 220,
                          scene_texture_id=kind), 'Color') for i, (name, kind) in enumerate(textures)]
    inputs += reveal_inputs(material, world_position(material))
    color = custom(material, """
        float matched = step(abs(Depth.r - RememberedDepth.r), max(1.5, Depth.r * .0003));
        float known = matched * step(.5, Stencil.r) * (1. - step(1.5, Stencil.r));
        float entity = matched * step(1.5, Stencil.r);
        float2 grid = (World.xy - Origin.xy) / CellCm;
        float2 readings = Texture2DSampleLevel(Mask, MaskSampler, grid / Cells.xy, 0).gb;
        float present = saturate((readings.y - .27) / .67);
        float recent = readings.x;
        float3 faded = lerp(Scene.rgb, dot(Scene.rgb, float3(.22,.62,.16)) * float3(.72,.89,1.11), .37);
        float3 memory = lerp(faded * .80, Scene.rgb * 1.17, recent);
        return lerp(Scene.rgb, memory, known * lerp(.42, 1., present)) * (1. + .12 * entity);
    """, unreal.CustomMaterialOutputType.CMOT_FLOAT3, inputs, -250, -20)
    property_input(color, '', material, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    save(material)
    return material


def main():
    report = Path(os.environ['MR_EXPLORATION_ART_REPORT']).resolve()
    if report.exists():
        raise ValueError('Use a new evidence path')
    ASSETS.make_directory(FOLDER)
    assets = [night_sky(), night_ground(), knowledge_grade()]
    unreal.MRAssetTools.finish_asset_compilation()
    report.write_text(json.dumps({
        'format': 'metarescue.exploration-backdrop.v2',
        'materials': [a.get_path_name() for a in assets],
        'basis': 'initial_mapped_city_and_delivered_replacements',
        'mask_channels': {'r': 'remembered_solid_support', 'g': 'freshness', 'b': 'remembered_surface'},
    }, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
