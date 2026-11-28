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
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT=Path(__file__).resolve().parents[2]


def png_bytes(rgb):
    height,width,_=rgb.shape
    def chunk(tag,data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    scan=b''.join(b'\0'+row.tobytes() for row in rgb)
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
    tw,th=tex.size
    pixels=np.empty(tw*th*4,dtype=np.float32);tex.pixels.foreach_get(pixels)
    pixels=pixels.reshape(th,tw,4)
    bounds=[[min(v[i] for v in vertices),max(v[i] for v in vertices)] for i in range(3)]
    output.mkdir(parents=True,exist_ok=True)
    print('MR_RUIN_BOUNDS '+json.dumps({'bounds_m':bounds,'triangles':len(triangles),
                                     'texture':[tw,th]}),flush=True)
    for axis,sign,label in [(0,-1,'x-negative'),(0,1,'x-positive'),(1,-1,'y-negative'),(1,1,'y-positive')]:
        u=1-axis;v=2
        u0,u1=bounds[u][0]-.2,bounds[u][1]+.2
        v0,v1=bounds[v][0]-.2,bounds[v][1]+.2
        w=max(1,round((u1-u0)*42));h=max(1,round((v1-v0)*42))
        rgb=np.full((h,w,3),[27,31,37],dtype=np.uint8)
        depth=np.full((h,w),np.nan,dtype=np.float32)
        ray=Vector((0,0,0));ray[axis]=-sign
        near=bounds[axis][int(sign>0)]+sign
        for row in range(h):
            z=v1-(row+.5)*(v1-v0)/h
            for col in range(w):
                along=u0+(col+.5)*(u1-u0)/w
                origin=Vector((0,0,0));origin[axis]=near;origin[u]=along;origin[v]=z
                hit,normal,index,distance=tree.ray_cast(origin,ray,abs(bounds[axis][1]-bounds[axis][0])+2)
                if hit is None:continue
                depth[row,col]=hit[axis]
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
                color=np.clip(pixels[min(th-1,int(y*th)),min(tw-1,int(x*tw)),:3],0,1)
                # Retain the source texture; use a mild normal cue only.
                shade=.68+.32*abs(normal[axis])
                rgb[row,col]=np.clip(color**(1/2.2)*shade*255,0,255).astype(np.uint8)
        data=png_bytes(rgb)
        (output/(label+'.png')).write_bytes(data)
        np.save(output/(label+'-depth.npy'),depth)
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
