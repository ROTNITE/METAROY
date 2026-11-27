"""Shared source for UE Custom expressions and independent shader compilation."""
from pathlib import Path


def water_source():
    return (Path(__file__).resolve().parent/'shaders/urban_water_v2.hlsl').read_text(encoding='utf-8')


OUTPUTS={
    'color': 'return waterColor;',
    'normal': 'return waterNormal;',
    'roughness': 'return waterRoughness;',
    'remembered': '''float3 eye=normalize(Camera-World+float3(0.,0.,.01));
float grazing=pow(saturate(1.-abs(eye.z)),3.);
return waterColor*.92+float3(.05,.085,.12)*grazing*(.5+.5*ripple);''',
}
