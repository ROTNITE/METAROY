"""Produce a separate, measured Meshy ruin derivative; never edit the input.

The rectangles were reviewed on the orthographic BVH/UV projections of the
exact prepared v2 Building mesh (source SHA below). Cuts affect only a local
facade depth band. Real aperture rays and unchanged exterior bounds are
checked before export. UE import/cook and close-up acceptance are separate.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import random
import shutil
import statistics
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'blender/prepared/v2/Building/Building.blend'
SOURCE_SHA='ef82bc4979ab5791330b0e0779e082d26316077bcb494f8f130f311e4ebc9244'
NAME='BuildingVariantsR2'

# Pixel extents strictly inside the frames on the measured facade projections.
# x faces: 326x420, y faces: 519x420, viewed in increasing horizontal axis.
# Leave damaged/missing corners and small vents alone.
FACADES=(
    (0,-1,'x-negative',326,[
        (83,71,134,128),(180,71,230,129),
        (84,188,134,238),(179,187,229,245),(179,304,230,363)]),
    (0,1,'x-positive',326,[
        (62,80,78,124),(173,79,194,128),
        (62,196,78,239),(172,196,194,244),
        (63,324,78,365),(173,324,195,364)]),
    (1,-1,'y-negative',519,[
        (50,73,87,120),(141,73,177,120),
        (50,189,87,235),(141,189,177,235),
        (50,303,87,370),(141,303,177,370),(240,329,263,371)]),
    (1,1,'y-positive',519,[
        (51,73,85,120),(141,73,176,120),(239,75,263,119),
        (51,191,85,236),(141,191,176,236),(239,193,263,237),
        (51,303,85,370),(141,303,176,370),(239,329,263,370)])
)

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def point(axis,sign,s,u,z):
    p=Vector((0,0,z));p[axis]=sign*s;p[1-axis]=u
    return p


def ray(tree,axis,sign,s,u,z,distance):
    d=Vector((0,0,0));d[axis]=-sign
    return tree.ray_cast(point(axis,sign,s,u,z),d,distance)[0]


def aperture_specs(mesh):
    bounds=[(min(v.co[i] for v in mesh.vertices),max(v.co[i] for v in mesh.vertices)) for i in range(3)]
    tree=BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices],[tuple(f.vertices) for f in mesh.polygons])
    out=[]
    for axis,sign,label,width,rectangles in FACADES:
        umin,umax=bounds[1-axis][0]-.2,bounds[1-axis][1]+.2
        zmin,zmax=bounds[2][0]-.2,bounds[2][1]+.2
        start=max(sign*bounds[axis][0],sign*bounds[axis][1])+1.
        distance=bounds[axis][1]-bounds[axis][0]+2
        for index,(left,top,right,bottom) in enumerate(rectangles):
            u0=umin+left*(umax-umin)/width;u1=umin+right*(umax-umin)/width
            z0=zmax-bottom*(zmax-zmin)/420;z1=zmax-top*(zmax-zmin)/420
            rings=[]
            for t in (.15,.38,.62,.85):
                u=u0+t*(u1-u0);z=z0+t*(z1-z0)
                for x,y in ((u,z0-.055),(u,z1+.055),(u0-.055,z),(u1+.055,z)):
                    hit=ray(tree,axis,sign,start,x,y,distance)
                    if hit is not None:rings.append(float(sign*hit[axis]))
            clusters=[[q for q in rings if abs(q-p)<.23] for p in rings]
            cluster=max(clusters,key=lambda c:(len(c),statistics.median(c)),default=[])
            row={'id':f'{label}-{index+1:02d}','axis':axis,'sign':sign,
                 'rect_m':[u0,z0,u1,z1],'frame_samples':len(cluster)}
            if len(cluster)<6:
                row['skipped']='no coherent measured facade plane';out.append(row);continue
            front=statistics.median(cluster)
            # Do not cut distant interior or the opposite facade.
            low,high=front-.86,front+.18
            samples=[(u0+(u1-u0)*i/6,z0+(z1-z0)*j/6) for i in range(1,6) for j in range(1,6)]
            blocked=sum(ray(tree,axis,sign,high,u,z,high-low-.002) is not None for u,z in samples)
            row.update(front_m=front,depth_m=[low,high],blocked_before=blocked,
                       samples=samples,style='empty' if (index+axis+(sign>0))%3==0 else 'broken')
            out.append(row)
    return bounds,out


def cut_box(bm,low,high):
    def nearby():
        result=[]
        for f in bm.faces:
            # Early axis rejection; this scan avoids global cuts through other walls.
            if any(max(v.co[i] for v in f.verts)<low[i]-1.e-5 or
                   min(v.co[i] for v in f.verts)>high[i]+1.e-5 for i in range(3)):
                continue
            result.append(f)
        return result
    for axis in range(3):
        for value in (low[axis],high[axis]):
            faces=nearby()
            if not faces:return 0
            geometry=set(faces)
            for f in faces:geometry.update(f.edges);geometry.update(f.verts)
            co=Vector((0,0,0));co[axis]=value
            normal=Vector((0,0,0));normal[axis]=1
            bmesh.ops.bisect_plane(bm,geom=list(geometry),dist=1.e-6,plane_co=co,plane_no=normal,
                                   clear_inner=False,clear_outer=False)
    chosen=[f for f in nearby() if all(low[i]-1.e-5<=v.co[i]<=high[i]+1.e-5 for v in f.verts for i in range(3))
            and all(low[i]+1.e-6<f.calc_center_median()[i]<high[i]-1.e-6 for i in range(3))]
    count=len(chosen)
    if chosen:bmesh.ops.delete(bm,geom=chosen,context='FACES')
    return count


def material(name,color,roughness,glass=False):
    m=bpy.data.materials.new(name);m.use_nodes=True
    shader=m.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value=(*color,1)
    shader.inputs['Roughness'].default_value=roughness
    if glass:
        shader.inputs['Transmission Weight'].default_value=1
        shader.inputs['IOR'].default_value=1.5
    m.diffuse_color=(*color,.25 if glass else 1)
    return m


def add_face(bm,coords,uv,slot,layer):
    face=bm.faces.new([bm.verts.new(p) for p in coords]);face.material_index=slot
    for loop,value in zip(face.loops,uv):loop[layer].uv=value
    return face


def dress(bm,row,glass_slot,reveal_slot,uv_layer):
    axis,sign=row['axis'],row['sign']
    u0,z0,u1,z1=row['rect_m'];s=row['front_m']
    def p(depth,u,v):return point(axis,sign,s-depth,u0+(u1-u0)*u,z0+(z1-z0)*v)
    # A shallow real reveal, not a dark quad across the opening.
    # Preserve the surrounding original textured frame and keep the centre open.
    for a,b in (((0,0),(1,0)),((1,0),(1,1)),((1,1),(0,1)),((0,1),(0,0))):
        add_face(bm,[p(.035,*a),p(.035,*b),p(.30,*b),p(.30,*a)],[(0,0),(1,0),(1,1),(0,1)],reveal_slot,uv_layer)
    if row['style']=='empty':return 0
    rng=random.Random(row['id'])
    # Different retained pieces on every window. Actual central holes and
    # jagged polygon edges; no opaque filler or full-size glass rectangle.
    corners=[((0,0),(1,0),(0,1)),((1,0),(0,0),(1,1)),
             ((1,1),(0,1),(1,0)),((0,1),(1,1),(0,0))]
    count=0
    for c,e,f in corners:
        if rng.random()<.30:continue
        a=rng.uniform(.15,.43);b=rng.uniform(.12,.42)
        lerp=lambda A,B,t:(A[0]+(B[0]-A[0])*t,A[1]+(B[1]-A[1])*t)
        edge=lerp(c,e,a);other=lerp(c,f,b)
        notch=lerp(edge,other,rng.uniform(.37,.63))
        notch=lerp(notch,c,rng.uniform(.08,.38))
        coords=[c,edge,notch,other]
        add_face(bm,[p(.075,*v) for v in coords],coords,glass_slot,uv_layer);count+=1
    if not count:
        coords=[(0,0),(.27,0),(.09,.12),(0,.32)]
        add_face(bm,[p(.075,*v) for v in coords],coords,glass_slot,uv_layer);count=1
    return count


def export_fbx(obj,folder):
    bpy.ops.object.select_all(action='DESELECT')
    obj.hide_set(False);obj.hide_viewport=False;obj.select_set(True);bpy.context.view_layer.objects.active=obj
    target=folder/'parts'/(obj.name+'.fbx')
    bpy.ops.export_scene.fbx(filepath=str(target),use_selection=True,object_types={'MESH'},
        add_leaf_bones=False,apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',
        axis_forward='-Y',axis_up='Z',path_mode='RELATIVE',use_mesh_modifiers=True,mesh_smooth_type='FACE',bake_anim=False)
    obj.data.calc_loop_triangles()
    return {'object':obj.name,'role':'module','triangles':len(obj.data.loop_triangles),
            'location_m':[0,0,0],'rotation_deg':[0,0,0],'parent':None,
            'dimensions_m':list(obj.dimensions),'materials':[m.name for m in obj.data.materials],
            'fbx':'parts/'+target.name}


def render_previews(full,low,folder):
    scene=bpy.context.scene
    low.hide_render=True;low.hide_set(True);full.hide_render=False;full.hide_set(False)
    scene.world=bpy.data.worlds.new('Ruin review world')
    scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.24,.27,.32,1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value=.65
    scene.render.engine='CYCLES';scene.cycles.samples=16
    scene.render.resolution_x=720;scene.render.resolution_y=540;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    def look(obj,target):obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()
    data=bpy.data.lights.new('Soft daylight','AREA');data.energy=2500;data.shape='DISK';data.size=9
    lamp=bpy.data.objects.new('Soft daylight',data);scene.collection.objects.link(lamp);lamp.location=(2,-9,16);look(lamp,(0,0,4))
    data=bpy.data.cameras.new('ReviewCamera');camera=bpy.data.objects.new('ReviewCamera',data)
    scene.collection.objects.link(camera);scene.camera=camera;data.type='ORTHO';data.ortho_scale=16
    for label,pos in [('front',(14,-18,12)),('back',(-15,18,12))]:
        camera.location=pos;look(camera,(0,0,4.7))
        scene.render.filepath=str(folder/(label+'.png'))
        bpy.ops.render.render(write_still=True)


def main(output,preview):
    if digest(SOURCE)!=SOURCE_SHA:raise RuntimeError('Source revision changed; remeasure all four facades')
    folder=output/NAME
    if folder.exists():raise FileExistsError('Refusing to overwrite an existing derivative: '+str(folder))
    folder.mkdir(parents=True);(folder/'parts').mkdir();(folder/'textures').mkdir()
    bpy.ops.wm.open_mainfile(filepath=str(SOURCE),load_ui=False)
    original=bpy.data.objects['Building']
    template=original.data.copy()
    for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
    full=bpy.data.objects.new('ruin_full',template);bpy.context.collection.objects.link(full)
    bounds,rows=aperture_specs(template)
    # Preserve the original material graph and the exact reviewed PBR images.
    base_record=json.loads((ROOT/'blender/prepared/v3/BuildingVariants/asset.json').read_text(encoding='utf-8'))['materials'][0]
    for entry in base_record['images']:
        src=SOURCE.parent/entry['file'];dest=folder/entry['file'];shutil.copy2(src,dest)
        node=template.materials[0].node_tree.nodes.get(entry['node'])
        if node is None:raise RuntimeError('Source material graph changed')
        node.image=bpy.data.images.load(str(dest),check_existing=False)
        node.image.colorspace_settings.name=entry['colorspace'];node.image.pack()
    reveal=material('M_RuinReveal',(.105,.077,.058),.91)
    glass=material('M_RuinGlass_broken',(.82,.89,.87),.15,True)
    glaze=json.loads((ROOT/'blender/prepared/v3/GlazingKit/asset.json').read_text(encoding='utf-8'))
    glass_record=next(m for m in glaze['materials'] if m['name']=='M_GlassV3_broken')
    glass_record['name']=glass.name
    for entry in glass_record['images']:
        src=ROOT/'blender/prepared/v3/GlazingKit'/entry['file'];dest=folder/entry['file'];shutil.copy2(src,dest)
        image=bpy.data.images.load(str(dest),check_existing=False);image.colorspace_settings.name='Non-Color';image.pack()
        nodes=glass.node_tree.nodes;sample=nodes.new('ShaderNodeTexImage');sample.name=entry['node'];sample.image=image
        separate=nodes.new('ShaderNodeSeparateColor');bump=nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value=.28;bump.inputs['Distance'].default_value=.015
        links=glass.node_tree.links;links.new(sample.outputs['Color'],separate.inputs[0])
        links.new(separate.outputs['Red'],bump.inputs['Height'])
        links.new(bump.outputs['Normal'],nodes['Principled BSDF'].inputs['Normal'])
    template.materials.append(reveal);reveal_slot=len(template.materials)-1
    template.materials.append(glass);glass_slot=len(template.materials)-1
    bm=bmesh.new();bm.from_mesh(template)
    for row in rows:
        if 'skipped' in row:continue
        axis,sign=row['axis'],row['sign'];u0,z0,u1,z1=row['rect_m'];lo,hi=row['depth_m']
        a=point(axis,sign,lo,u0,z0);b=point(axis,sign,hi,u1,z1)
        low=Vector(tuple(min(a[i],b[i]) for i in range(3)))
        high=Vector(tuple(max(a[i],b[i]) for i in range(3)))
        row['deleted_faces']=cut_box(bm,low,high) if row['blocked_before'] else 0
    bm.normal_update()
    tree=BVHTree.FromBMesh(bm)
    for row in rows:
        if 'skipped' in row:continue
        lo,hi=row['depth_m']
        row['blocked_after']=sum(ray(tree,row['axis'],row['sign'],hi,u,z,hi-lo-.002) is not None for u,z in row['samples'])
        if row['blocked_after']:
            raise RuntimeError('Opaque aperture still obstructed: '+json.dumps(row))
    del tree
    uv=bm.loops.layers.uv.verify()
    for row in rows:
        if 'skipped' not in row:row['shards']=dress(bm,row,glass_slot,reveal_slot,uv)
    bm.normal_update();bm.to_mesh(template);bm.free();template.update()
    # Do not disturb the original footprint, ground pivot, or roof bounds.
    actual=[(min(v.co[i] for v in template.vertices),max(v.co[i] for v in template.vertices)) for i in range(3)]
    if any(abs(a-b)>.035 for old,new in zip(bounds,actual) for a,b in zip(old,new)):
        raise RuntimeError('Full ruin silhouette bounds changed: '+str((bounds,actual)))
    lower=template.copy();low=bpy.data.objects.new('ruin_low',lower);bpy.context.collection.objects.link(low)
    bm=bmesh.new();bm.from_mesh(lower)
    bmesh.ops.bisect_plane(bm,geom=list(bm.verts)+list(bm.edges)+list(bm.faces),dist=.0001,
        plane_co=Vector((0,0,3.25)),plane_no=Vector((.06,-.04,1)),clear_outer=True,clear_inner=False)
    bm.to_mesh(lower);bm.free();lower.update()
    scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    for obj in (full,low):
        bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(40),keep_sharp_edges=False)
    parts=[export_fbx(obj,folder) for obj in (full,low)]
    if parts[0]['triangles']>155000 or parts[1]['triangles']>55000:
        raise RuntimeError('Candidate exceeds the agreed ruin geometry budget: '+str(parts))
    record={'id':NAME,'source':SOURCE.relative_to(ROOT).as_posix(),'source_sha256':SOURCE_SHA,
            'blend':NAME+'.blend','parts':parts,'materials':[base_record,
                {'name':reveal.name,'glass':False,'inputs':{'Base Color':[.105,.077,.058,1],'Roughness':.91,'Metallic':0},'links':[],'images':[]},
                glass_record],
            'repairs':['Measured UV-preserving local aperture cuts, real reveals, variable empty openings and retained glass shards; source preserved.'],
            'actions':[],'animation_runtime':'Static scenery; UE city glass derivative must have no collision.'}
    (folder/'asset.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    for row in rows:row.pop('samples',None)
    report={'source_sha256':SOURCE_SHA,'source_preserved':digest(SOURCE)==SOURCE_SHA,'bounds_m':actual,
            'apertures':rows,'clear_apertures':sum('blocked_after' in r and r['blocked_after']==0 for r in rows),
            'opaque_samples_removed':sum(r.get('blocked_before',0) for r in rows),
            'shards':sum(r.get('shards',0) for r in rows),'parts':parts,'ue_import_verified':False}
    if not report['source_preserved'] or report['clear_apertures']<20 or report['opaque_samples_removed']<100:
        raise RuntimeError('Not enough measured openings repaired: '+json.dumps(report))
    (folder/'repair-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    low.hide_render=True;low.hide_set(True);full.hide_render=False;full.hide_set(False)
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/(NAME+'.blend')),relative_remap=False)
    print('MR_RUIN_REPAIR '+json.dumps(report,separators=(',',':')),flush=True)
    if preview:render_previews(full,low,folder)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--preview',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    main(args.output.resolve(),args.preview)
