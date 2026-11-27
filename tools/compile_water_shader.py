"""Compile the exact UE Custom HLSL snippets independently, not UE materials."""
from pathlib import Path
import sys
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'unreal/tools'))
from water_shader_source import water_source,OUTPUTS


def main():
    args='float3 World,float4 Field,float Stamp,float3 Camera'
    source=water_source()
    code='\n'.join(f"{'float' if key=='roughness' else 'float3'} {key}({args}) {{\n{source}\n{out}\n}}"
                   for key,out in OUTPUTS.items())
    code+="""
[[vk::binding(0,0)]] RWStructuredBuffer<float4> Samples;
[numthreads(1,1,1)]
void main(uint3 Thread:SV_DispatchThreadID)
{
    uint i=Thread.x*6;
    float3 world=Samples[i].xyz,camera=Samples[i+1].xyz;
    float4 field=Samples[i+2];float stamp=Samples[i+1].w;
    Samples[i+3]=float4(color(world,field,stamp,camera),roughness(world,field,stamp,camera));
    Samples[i+4]=float4(normal(world,field,stamp,camera),1.);
    Samples[i+5]=float4(remembered(world,field,stamp,camera),1.);
}
"""
    with tempfile.TemporaryDirectory(prefix='metarescue-water-shader-') as tmp:
        shader=Path(tmp)/'water.comp.hlsl';binary=Path(tmp)/'water.spv'
        shader.write_text(code,encoding='utf-8')
        subprocess.run(['glslangValidator','-D','-V','--target-env','vulkan1.1','-S','comp',
                        '-e','main','-o',str(binary),str(shader)],check=True)
        subprocess.run(['spirv-val','--target-env','vulkan1.1',str(binary)],check=True)
    print('Water R6: all four authored HLSL outputs compile and validate as SPIR-V (not Unreal rendering)')


if __name__=='__main__':main()
