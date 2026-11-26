import bpy
print('UNPACK', [(e.identifier, e.name) for e in bpy.types.Image.bl_rna.functions['unpack'].parameters['method'].enum_items])
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
obj.keyframe_insert(data_path='rotation_euler', frame=1)
action = obj.animation_data.action
print('ACTION_FCURVES', hasattr(action, 'fcurves'))
print('ACTION_LAYERS', len(action.layers), list(action.layers[0].strips[0].channelbags[0].fcurves))
