"""Inspect the actual prepared Meshy ruin, without modifying its source.

Run in background Blender, not Unreal. The orthographic ray map samples the
original base-colour UVs and prints exact local metre coordinates. The PNGs
are diagnostic projections, not rendered UE evidence.
"""
from pathlib import Path
import argparse
import base64
import json
import math
import struct
import sys
import zlib

import bpy
from array import array
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT=Path(__file__).resolve().parents[2]


def png_bytes(rgb,width,height):
    def chunk(tag,data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    scan=b''.join(b'\0'+rgb[row*width*3:(row+1)*width*3] for row in range(height))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+
            chunk(b'IDAT',zlib.compress(scan,6))+chunk(b'IEND',b''))


def inspect(source,output,log_preview=False):
    with source.open('rb') as stream:
        if stream.read(8)!=b'BLENDER '[:8]:
            # Recent Blender files may be compressed or have a changed header;
            # only reject a literal LFS pointer before bpy.open_mainfile.
            stream.seek(0)
            if stream.read(128).startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise RuntimeError('Building.blend is still an LFS pointer; fetch the actual object')
    bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False)
    mesh=bpy.data.objects['Building'].data
    mesh.calc_loop_triangles()
    vertices=[v.co.copy() for v in mesh.vertices]
    triangles=[tuple(t.vertices) for t in mesh.loop_triangles]
    uv=[tuple(item.uv) for item in mesh.uv_layers.active.data]
    loops=[tuple(t.loops) for t in mesh.loop_triangles]
    tree=BVHTree.FromPolygons(vertices,triangles,all_triangles=True)
    tex=bpy.data.images.get('T_Building_BaseColor')
    if tex is None:raise RuntimeError('Expected baked facade BaseColor texture missing')
    if not tex.size[0] or not tex.size[1]:
        # The source records a path on the original workstation. Resolve
        # the reviewed exported texture relative to the actual checkout.
        exported=source.parent/'textures/T_0_T_Building_BaseColor.png'
        if not exported.is_file():raise RuntimeError('Prepared facade texture missing: '+str(exported))
        tex=bpy.data.images.load(str(exported),check_existing=False)
    tw,th=tex.size
    if tw<1 or th<1:raise RuntimeError('Facade texture did not decode')
    pixels=array('f',[0])*(tw*th*4);tex.pixels.foreach_get(pixels)
    bounds=[[min(v[i] for v in vertices),max(v[i] for v in vertices)] for i in range(3)]
    output.mkdir(parents=True,exist_ok=True)
    print('MR_RUIN_BOUNDS '+json.dumps({'bounds_m':bounds,'triangles':len(triangles),
                                     'texture':[tw,th]}),flush=True)
    for axis,sign,label in [(0,-1,'x-negative'),(0,1,'x-positive'),(1,-1,'y-negative'),(1,1,'y-positive')]:
        u=1-axis;v=2
        u0,u1=bounds[u][0]-.2,bounds[u][1]+.2
        v0,v1=bounds[v][0]-.2,bounds[v][1]+.2
        w=max(1,round((u1-u0)*42));h=max(1,round((v1-v0)*42))
        rgb=bytearray([27,31,37])*(h*w)
        depth=array('f',[float('nan')])*(h*w)
        ray=Vector((0,0,0));ray[axis]=-sign
        near=bounds[axis][int(sign>0)]+sign
        for row in range(h):
            z=v1-(row+.5)*(v1-v0)/h
            for col in range(w):
                along=u0+(col+.5)*(u1-u0)/w
                origin=Vector((0,0,0));origin[axis]=near;origin[u]=along;origin[v]=z
                hit,normal,index,distance=tree.ray_cast(origin,ray,abs(bounds[axis][1]-bounds[axis][0])+2)
                if hit is None:continue
                depth[row*w+col]=hit[axis]
                a,b,c=(vertices[i] for i in triangles[index])
                ab,ac,ap=b-a,c-a,hit-a
                d00,d01,d11=ab.dot(ab),ab.dot(ac),ac.dot(ac)
                den=d00*d11-d01*d01
                if abs(den)<1e-14:continue
                s=(d11*ap.dot(ab)-d01*ap.dot(ac))/den
                t=(d00*ap.dot(ac)-d01*ap.dot(ab))/den
                x,y=uv[loops[index][0]]
                x2,y2=uv[loops[index][1]];x3,y3=uv[loops[index][2]]
                x=(x*(1-s-t)+x2*s+x3*t)%1
                y=(y*(1-s-t)+y2*s+y3*t)%1
                texel=4*(min(th-1,int(y*th))*tw+min(tw-1,int(x*tw)))
                color=[min(1,max(0,pixels[texel+i])) for i in range(3)]
                # Retain the source texture; use a mild normal cue only.
                shade=.68+.32*abs(normal[axis])
                at=3*(row*w+col)
                rgb[at:at+3]=bytes(min(255,max(0,int(c**(1/2.2)*shade*255))) for c in color)
        data=png_bytes(rgb,w,h)
        (output/(label+'.png')).write_bytes(data)
        with (output/(label+'-depth.f32')).open('wb') as stream:depth.tofile(stream)
        info={'side':label,'axis':axis,'sign':sign,'u_axis':u,'u_min':u0,'u_max':u1,
              'z_min':v0,'z_max':v1,'width':w,'height':h}
        (output/(label+'.json')).write_text(json.dumps(info,indent=2),encoding='utf-8')
        print('MR_RUIN_VIEW '+json.dumps(info),flush=True)
        if log_preview:
            encoded=base64.b64encode(data).decode('ascii')
            for i in range(0,len(encoded),8000):
                print('MR_RUIN_PNG '+label+' '+str(i//8000)+' '+encoded[i:i+8000],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'blender/prepared/v2/Building/Building.blend')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--log-preview',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    inspect(args.source,args.output,args.log_preview)
