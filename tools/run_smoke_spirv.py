"""Run the actual authored opacity HLSL on a software Vulkan compute device.

This is not the Unreal material compiler, raster pipeline, or GPU FPS.
Requires glslang-tools, spirv-tools, libvulkan-dev and Mesa lavapipe.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'unreal/tools'))
from smoke_shader_source import remembered_source

ARGUMENTS='float3 Source,float3 Wind,float3 Shape,float Rise,float Extinction,float Strength,float3 Surface,float3 Camera,float SceneZ,float PixelZ,float Side'
MAIN=r'''
[[vk::binding(0,0)]] RWStructuredBuffer<float4> Samples;
[numthreads(64,1,1)]
void main(uint3 Thread:SV_DispatchThreadID)
{
    uint p=Thread.x*9;
    float4 a=Samples[p],b=Samples[p+1],c=Samples[p+2],d=Samples[p+3];
    float3 surface=Samples[p+4].xyz,camera=Samples[p+5].xyz;
    float3 low=Samples[p+6].xyz,high=Samples[p+7].xyz;
    Samples[p+8]=float4(
        Live(a.xyz,b.xyz,c.xyz,d.x,d.y,d.z,surface,camera,d.w,1,a.w),
        Live(a.xyz,b.xyz,c.xyz,d.x,d.y,d.z,surface,camera,d.w,1,-a.w),
        Remembered(a.xyz,b.xyz,c.xyz,d.x,d.y,d.z,surface,camera,d.w,1,b.w,low,high),
        Remembered(a.xyz,b.xyz,c.xyz,d.x,d.y,d.z,surface,camera,d.w,1,-b.w,low,high));
}
'''

def main():
    source=(ROOT/'unreal/tools/shaders/disaster_smoke_v2.hlsl').read_text(encoding='utf-8')
    code=f'float Live({ARGUMENTS}) {{\n{source}\n}}\n'
    code+=f'float Remembered({ARGUMENTS},float3 ClipMin,float3 ClipMax) {{\n{remembered_source(source)}\n}}\n'+MAIN
    icds=sorted(Path('/usr/share/vulkan/icd.d').glob('lvp_icd*.json'))
    if not icds:raise RuntimeError('No lavapipe ICD installed')
    with tempfile.TemporaryDirectory(prefix='metarescue-spirv-') as tmp:
        tmp=Path(tmp);hlsl=tmp/'smoke.comp.hlsl';spv=tmp/'smoke.spv';exe=tmp/'smoke-probe'
        hlsl.write_text(code,encoding='utf-8')
        subprocess.run(['glslangValidator','-D','-V','--target-env','vulkan1.1','-S','comp',
                        '-e','main','-o',str(spv),str(hlsl)],check=True)
        subprocess.run(['spirv-val','--target-env','vulkan1.1',str(spv)],check=True)
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror',
                        str(ROOT/'tools/smoke_vulkan_probe.cpp'),'-lvulkan','-o',str(exe)],check=True)
        subprocess.run([str(exe),str(spv)],check=True,
                        env={**os.environ,'VK_ICD_FILENAMES':str(icds[0])})

if __name__=='__main__':main()
