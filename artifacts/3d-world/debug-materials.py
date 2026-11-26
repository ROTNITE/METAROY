from pathlib import Path
import sys
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'unreal/tools'))
from audit_asset_library import preview
import prepare_mechanized_assets as rig
root=Path(__file__).resolve().parents[2]
name='Van'
source, body = rig.load_body(name)
for mat in bpy.data.materials:
 if mat.use_nodes:
  shader=next((n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
  if shader:
   for link in list(shader.inputs['Normal'].links): mat.node_tree.links.remove(link)
preview({'id':'DebugBeforeEdit_'+name})
