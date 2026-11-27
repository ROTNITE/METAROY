"""CPU emulation of the actual HLSL arithmetic, not an Unreal/GPU test."""
from pathlib import Path
import argparse
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include "MRSmokeOptics.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <random>
using std::abs; using std::sqrt; using std::exp;
float min(float a,float b){return std::min(a,b);}
float max(float a,float b){return std::max(a,b);}
float saturate(float x){return min(1,max(0,x));}
struct float2 {
    float x,y;
    float2(float a,float b):x(a),y(b){}
    float2 operator+(float b)const{return {x+b,y+b};}
    float2 operator-(float b)const{return {x-b,y-b};}
    float2 operator-(float2 b)const{return {x-b.x,y-b.y};}
    float2 operator*(float b)const{return {x*b,y*b};}
    float2 operator/(float b)const{return {x/b,y/b};}
};
float2 min(float2 a,float2 b){return {min(a.x,b.x),min(a.y,b.y)};}
float2 max(float2 a,float2 b){return {max(a.x,b.x),max(a.y,b.y)};}
float dot(float2 a,float2 b){return a.x*b.x+a.y*b.y;}
struct float3 {
    float x,y,z;
    float3(float a,float b,float c):x(a),y(b),z(c){}
    float3(float2 a,float b):x(a.x),y(a.y),z(b){}
    float2 xy()const{return {x,y};}
    float operator[](int i)const{return i==0?x:i==1?y:z;}
    float3 operator+(float3 b)const{return {x+b.x,y+b.y,z+b.z};}
    float3 operator-(float3 b)const{return {x-b.x,y-b.y,z-b.z};}
    float3 operator*(float b)const{return {x*b,y*b,z*b};}
    float3 operator/(float b)const{return {x/b,y/b,z/b};}
    bool operator>=(float3 b)const{return x>=b.x&&y>=b.y&&z>=b.z;}
    bool operator<=(float3 b)const{return x<=b.x&&y<=b.y&&z<=b.z;}
};
bool all(bool v){return v;}
float length(float3 a){return sqrt(a.x*a.x+a.y*a.y+a.z*a.z);}
float3 lerp(float3 a,float3 b,float t){return a+(b-a)*t;}
float Shader(float3 Source,float3 Wind,float3 Shape,float Rise,float Extinction,float Strength,
             float3 Surface,float3 Camera,float SceneZ,float PixelZ,float Side)
{
'''
MAIN = r'''
}
int main(){
    std::mt19937 rng(2026);
    std::uniform_real_distribution<float> d(-8000,8000),z(-600,5000),w(-400,400);
    double worst=0; int insideCount=0,clippedCount=0;
    for(int i=0;i<1000;++i){
        float3 source(i%2?150000.f:0.f,i%2?150000.f:0.f,340.f);
        float3 wind(w(rng),w(rng),0),shape(4200,650,.28f);
        if(i%5==0)shape=float3(3000,1000,0);
        const float rise=280,extinction=.006f,strength=.9f;
        float3 camera=source+float3(d(rng),d(rng),z(rng));
        if(i%7==0)camera=source+float3(0,0,1000);
        float3 surface=source+float3(d(rng),d(rng),z(rng));
        float fraction=i%3==0?.35f:1.f;
        if(fraction<1)++clippedCount;
        float3 a=camera-source,b=surface-source;
        const float r=shape.y+shape.z*shape.x;
        const float3 top(wind.xy()*shape.x/rise,shape.x);
        const bool inside=all(a>=float3(min(float2(0,0),top.xy())-r,0))&&
                          all(a<=float3(max(float2(0,0),top.xy())+r,shape.x));
        if(inside)++insideCount;
        const float side=inside?-1.f:1.f;
        const float opacity=Shader(source,wind,shape,rise,extinction,strength,surface,camera,fraction,1,side);
        MRSmokeOptics::Field field{shape.x,shape.y,shape.z,wind.x,wind.y,rise,extinction,strength};
        const MRSmokeOptics::Vec start{a.x,a.y,a.z},end{b.x,b.y,b.z};
        const double reference=1-std::exp(-MRSmokeOptics::Integrate(field,start,MRSmokeOptics::Mix(start,end,fraction)));
        const double error=std::abs(reference-opacity);
        worst=std::max(worst,error);
        assert(std::isfinite(opacity)&&opacity>=0&&opacity<=1&&error<.001);
        assert(Shader(source,wind,shape,rise,extinction,strength,surface,camera,fraction,1,-side)==0);
    }
    assert(insideCount>0&&clippedCount>0);
    std::cout<<"1000 HLSL arithmetic comparisons, "<<insideCount<<" inside-camera rays, "<<clippedCount
             <<" depth-clipped rays; maximum opacity error "<<worst<<"\n";
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',default='g++',help='GCC/Clang-compatible C++17 compiler')
    args=parser.parse_args()
    shader=(ROOT/'unreal/tools/shaders/disaster_smoke_v2.hlsl').read_text(encoding='utf-8')
    # Translate only HLSL annotations/swizzles and float-literal types.
    shader=re.sub(r'\[(?:unroll|loop)\]\s*','',shader)
    shader=re.sub(r'\.xy\b','.xy()',shader)
    shader=re.sub(r'(?<![\w.])(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?',lambda m:m.group()+'f',shader)
    with tempfile.TemporaryDirectory(prefix='metarescue-smoke-') as directory:
        source=Path(directory)/'probe.cpp';binary=Path(directory)/'probe'
        source.write_text(PREFIX+shader+MAIN,encoding='utf-8')
        subprocess.run([args.compiler,'-std=c++17','-O2','-Wall','-Wextra','-Werror',
                        '-I',str(ROOT/'unreal/MetaRescue/Source/MetaRescue'),str(source),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)


if __name__=='__main__':main()
