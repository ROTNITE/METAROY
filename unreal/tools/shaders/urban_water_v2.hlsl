// Input: World in centimetres; Field.rg linear quantized current,
// Field.b depth / 4 metres, Stamp model seconds (delivered seconds in memory).
// Appearance only: no wave displacement or extra information from hidden cells.
float2 flow=(Field.rg*255.0-128.0)*(8.0/127.0);
float speed=min(length(flow),8.0);
float depth=saturate(Field.b)*4.0;
float2 metres=World.xy*.01;
float2 advected=metres-flow*Stamp*.38;
float p=dot(advected,float2(2.17,1.12))+Stamp*.91;
float q=dot(advected,float2(-1.36,3.54))-Stamp*1.17;
float r=dot(advected,float2(5.43,-2.61))+Stamp*1.65;
float ripple=.5+.24*sin(p)+.15*cos(q)+.08*sin(r);
float amp=smoothstep(.015,.23,depth)*(.55+.45*saturate(speed/2.5));
float2 slope=amp*(float2(2.17,1.12)*.024*cos(p)
                -float2(-1.36,3.54)*.012*sin(q)
                +float2(5.43,-2.61)*.004*cos(r));
float3 waterNormal=normalize(float3(-slope,1.0));
float shore=1.0-smoothstep(.035,.32,depth);
float wetEdge=1.0-smoothstep(.008,.11,depth);
float foam=shore*smoothstep(.62,.86,ripple)*(.2+.8*saturate(speed/.85));
float3 silt=lerp(float3(.080,.064,.037),float3(.025,.055,.061),smoothstep(.15,1.7,depth));
float3 wetColor=lerp(silt,float3(.028,.029,.023),wetEdge*.72);
float3 waterColor=lerp(wetColor*(.93+.14*ripple),float3(.27,.29,.26),foam*.62);
float waterRoughness=lerp(.19,.57,saturate(foam+wetEdge*.60));
