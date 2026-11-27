"""Frozen optical samples of the same CC0 flame atlas and analytic smoke."""
import json
import os
from pathlib import Path
import sys
import unreal
sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_disaster_effects as art

def main():
    report=Path(os.environ['MR_EXPLORATION_ART_REPORT']).resolve()
    if report.exists():raise ValueError('Preserve prior effect evidence')
    folder='/Game/EffectsV3/ExplorationEffectsR1';art.FOLDER=folder
    unreal.EditorAssetLibrary.make_directory(folder)
    names=('M_PhysicalSmoke','M_BuildingFire','M_RememberedFlood')
    if any(unreal.EditorAssetLibrary.does_asset_exist(folder+'/'+n) for n in names):
        raise ValueError('Use a new revision; these derivatives already exist')
    textures={name:unreal.load_asset('/Game/EffectsV3/Revision5/Textures/T_'+name)
              for name in ('FireFront','FireOverhead','SmokeWisps')}
    if not all(isinstance(x,unreal.Texture2D) for x in textures.values()):raise ValueError('Import checked CC0 art first')
    smoke=art.build_volume(textures)
    opacity=art.EDIT.get_material_property_input_node(smoke,unreal.MaterialProperty.MP_OPACITY)
    inputs=list(opacity.get_editor_property('inputs'))
    for name in ('ClipMin','ClipMax'):
        pin=unreal.CustomInput();pin.set_editor_property('input_name',name);inputs.append(pin)
    opacity.set_editor_property('inputs',inputs)
    lo=art.vector(smoke,'ClipMin',(-1e8,-1e8,-1e8),2100)
    hi=art.vector(smoke,'ClipMax',(1e8,1e8,1e8),2250)
    art.connect(lo,'RGB',opacity,'ClipMin');art.connect(hi,'RGB',opacity,'ClipMax')
    code=opacity.get_editor_property('code')
    marker='float3 Delta = Surface - Camera;'
    assert marker in code
    opacity.set_editor_property('code',code.replace(marker,'Lo=max(Lo,ClipMin); Hi=min(Hi,ClipMax);\n'+marker))
    stamp=art.scalar(smoke,'SnapshotTime',0,2400)
    for expression in art.EDIT.get_material_expressions(smoke):
        if isinstance(expression,unreal.MaterialExpressionCustom):
            if any(str(x.get_editor_property('input_name'))=='Clock' for x in expression.get_editor_property('inputs')):
                art.connect(stamp,'',expression,'Clock')
    smoke.set_editor_property('allow_translucent_custom_depth_writes',True)
    smoke.set_editor_property('opacity_mask_clip_value',.01)
    smoke.set_editor_property('used_with_instanced_static_meshes',True)
    art.save(smoke)
    fire=art.build_fire(textures)
    life=art.scalar(fire,'FlameLife',.5,1050)
    for expression in art.EDIT.get_material_expressions(fire):
        if isinstance(expression,unreal.MaterialExpressionCustom):
            if any(str(x.get_editor_property('input_name'))=='Life' for x in expression.get_editor_property('inputs')):
                art.connect(life,'',expression,'Life')
    fire.set_editor_property('allow_translucent_custom_depth_writes',True)
    fire.set_editor_property('opacity_mask_clip_value',.01)
    fire.set_editor_property('used_with_instanced_static_meshes',True)
    art.save(fire)
    # Use a per-vertex observation timestamp, so one draw can contain water
    # last sampled at different times without animating an unobserved tile.
    water=unreal.EditorAssetLibrary.duplicate_asset('/Game/EffectsV3/Revision5/M_UrbanFlood',folder+'/M_RememberedFlood')
    if not isinstance(water,unreal.Material):raise ValueError('Could not derive remembered water material')
    uv=art.node(water,unreal.MaterialExpressionTextureCoordinate,-1200,1300,coordinate_index=3)
    x=art.node(water,unreal.MaterialExpressionComponentMask,-950,1300,r=True,g=False,b=False,a=False)
    art.connect(uv,'',x,'')
    for expression in art.EDIT.get_material_expressions(water):
        if isinstance(expression,unreal.MaterialExpressionCustom):
            if any(str(pin.get_editor_property('input_name'))=='ModelTime' for pin in expression.get_editor_property('inputs')):
                art.connect(x,'',expression,'ModelTime')
    art.save(water);unreal.MRAssetTools.finish_asset_compilation()
    report.write_text(json.dumps({'format':'metarescue.exploration-effects.v1',
        'materials':[m.get_path_name() for m in (smoke,fire,water)],
        'source_textures':{k:v.get_path_name() for k,v in textures.items()},
        'source_field':'same centimetre extinction and wind as authoritative sensing',
        'unknown_ground_opens':False},indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
