"""Black unknown volumes from the published building bounds, without light leaks."""
import json
import os
from pathlib import Path
import sys
import unreal
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_disaster_effects import node,property_input,save,EDIT

def main():
    report=Path(os.environ['MR_EXPLORATION_ART_REPORT']).resolve()
    folder='/Game/EffectsV3/ExplorationBackdropR1';assets=unreal.EditorAssetLibrary
    if report.exists() or assets.does_asset_exist(folder+'/M_Unseen'):raise ValueError('Preserve prior backdrop assets/evidence')
    assets.make_directory(folder)
    black=unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_Unseen',folder,unreal.Material,unreal.MaterialFactoryNew())
    black.set_editor_property('shading_model',unreal.MaterialShadingModel.MSM_UNLIT);black.set_editor_property('two_sided',True)
    black.set_editor_property('used_with_instanced_static_meshes',True)
    color=node(black,unreal.MaterialExpressionConstant3Vector,-300,0,constant=unreal.LinearColor(0,0,0,1))
    property_input(color,'',black,unreal.MaterialProperty.MP_EMISSIVE_COLOR);save(black)
    matte=assets.duplicate_asset('/Game/EffectsV3/ExplorationR1/M_KnowledgeMatte',folder+'/M_KnowledgeMatte')
    output=EDIT.get_material_property_input_node(matte,unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    if not isinstance(output,unreal.MaterialExpressionCustom):raise ValueError('Unexpected matte graph')
    output.set_editor_property('code','return Scene.rgb * step(.5, Stencil.r);');save(matte)
    unreal.MRAssetTools.finish_asset_compilation()
    report.write_text(json.dumps({'format':'metarescue.exploration-backdrop.v1','materials':[black.get_path_name(),matte.get_path_name()],
        'private_geometry':False,'basis':'published_initial_building_bounds_and_delivered_surface_heights'},indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
