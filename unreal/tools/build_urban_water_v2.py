"""Separate procedural water material revision; never overwrite existing assets."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_disaster_effects as art
from build_exploration_night import exposure_independent_emissive
from water_shader_source import water_source,OUTPUTS


def main():
    report=Path(os.environ['MR_WATER_V2_REPORT']).resolve()
    art.FOLDER='/Game/EffectsV3/WaterR6'
    names=('M_UrbanFlood','M_RememberedFlood')
    if report.exists() or any(art.ASSETS.does_asset_exist(art.FOLDER+'/'+n) for n in names):
        raise ValueError('Preserve previous water materials; use a fresh worktree and evidence directory')
    art.ASSETS.make_directory(art.FOLDER)
    source=water_source()
    live=art.material(names[0])
    live.set_editor_property('shading_model',unreal.MaterialShadingModel.MSM_SINGLE_LAYER_WATER)
    live.set_editor_property('tangent_space_normal',False)
    world=art.node(live,unreal.MaterialExpressionWorldPosition,-1200,0)
    field=art.node(live,unreal.MaterialExpressionVertexColor,-1200,180)
    stamp=art.scalar(live,'ModelTime',0,360)
    inputs=[('World',world,''),('Field',field,''),('Stamp',stamp,'')]
    for index,(key,prop,kind) in enumerate((
        ('color',unreal.MaterialProperty.MP_BASE_COLOR,unreal.CustomMaterialOutputType.CMOT_FLOAT3),
        ('normal',unreal.MaterialProperty.MP_NORMAL,unreal.CustomMaterialOutputType.CMOT_FLOAT3),
        ('roughness',unreal.MaterialProperty.MP_ROUGHNESS,unreal.CustomMaterialOutputType.CMOT_FLOAT1))):
        expression=art.custom(live,source+'\n'+OUTPUTS[key],kind,inputs,-400,index*240)
        art.property_input(expression,'',live,prop)
    for name,value,prop in (('Specular',.4,unreal.MaterialProperty.MP_SPECULAR),
                            ('Opacity',.25,unreal.MaterialProperty.MP_OPACITY)):
        art.property_input(art.scalar(live,name,value,800),'',live,prop)
    volume=art.node(live,unreal.MaterialExpressionSingleLayerWaterMaterialOutput,200,300)
    for name,color,pin in (('Scattering',(.008,.0062,.0035),'ScatteringCoefficients'),
                           ('Absorption',(.018,.022,.033),'AbsorptionCoefficients')):
        art.connect(art.vector(live,name,color,1000),'RGB',volume,pin)
    art.save(live)
    remembered=art.material(names[1],luminous=True)
    remembered.set_editor_property('two_sided',True)
    world=art.node(remembered,unreal.MaterialExpressionWorldPosition,-1200,0)
    field=art.node(remembered,unreal.MaterialExpressionVertexColor,-1200,190)
    camera=art.node(remembered,unreal.MaterialExpressionCameraPositionWS,-1200,380)
    uv=art.node(remembered,unreal.MaterialExpressionTextureCoordinate,-1200,560,coordinate_index=3)
    stamp=art.node(remembered,unreal.MaterialExpressionComponentMask,-900,560,r=True,g=False,b=False,a=False)
    art.connect(uv,'',stamp,'Input')
    shade=art.custom(remembered,source+'\n'+OUTPUTS['remembered'],unreal.CustomMaterialOutputType.CMOT_FLOAT3,
        [('World',world,''),('Field',field,''),('Stamp',stamp,''),('Camera',camera,'')],-400,0)
    exposure_independent_emissive(remembered,shade)
    art.save(remembered)
    unreal.MRAssetTools.finish_asset_compilation()
    with report.open('x',encoding='utf-8') as stream:
        json.dump({'format':'metarescue.water-r6.import.v1','materials':[live.get_path_name(),remembered.get_path_name()],
                   'shader_sha256':hashlib.sha256(source.encode()).hexdigest(),'world_position_offset':False,
                   'inputs':'authoritative depth/current; model time or UV3 observation time; procedural no external texture'},stream,indent=2)
        stream.write('\n')


if __name__=='__main__':main()
