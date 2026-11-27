// Revision 6 opacity. Keep the density, cuts and quadrature in sync with
// MRSmokeOptics.h. All lengths are cm. Opaque scene depth truncates the ray.
if (Shape.x <= 0 || Shape.y <= 0 || Rise <= 0 || Strength <= 0 || Extinction <= 0) return 0;
float3 CameraLocal = Camera - Source;
float3 Top = float3(Wind.xy * Shape.x / Rise, Shape.x);
float Radius = Shape.y + Shape.z * Shape.x;
float3 Lo = float3(min(float2(0,0), Top.xy) - Radius, 0);
float3 Hi = float3(max(float2(0,0), Top.xy) + Radius, Shape.x);
float3 Delta = Surface - Camera;
float SurfaceDistance = length(Delta);
if (SurfaceDistance < .001) return 0;
float3 Ray = Delta / SurfaceDistance;
bool Inside = all(CameraLocal >= Lo) && all(CameraLocal <= Hi);
if ((Inside && Side > 0) || (!Inside && Side < 0)) return 0;
float Start = 0;
float Finish = SceneZ * SurfaceDistance / max(PixelZ, .001);
[unroll] for (int Axis = 0; Axis < 3; ++Axis)
{
    if (abs(Ray[Axis]) < .0000001)
    {
        if (CameraLocal[Axis] < Lo[Axis] || CameraLocal[Axis] > Hi[Axis]) return 0;
    }
    else
    {
        float A = (Lo[Axis] - CameraLocal[Axis]) / Ray[Axis];
        float B = (Hi[Axis] - CameraLocal[Axis]) / Ray[Axis];
        Start = max(Start, min(A,B));
        Finish = min(Finish, max(A,B));
    }
}
if (Finish <= Start) return 0;
float3 P = CameraLocal + Ray * Start, Q = CameraLocal + Ray * Finish;
float Span = Finish - Start;
float2 X = (P.xy - Wind.xy * P.z / Rise) / Radius;
float2 U = (Q.xy - Wind.xy * Q.z / Rise) / Radius - X;
float R = (Shape.y + Shape.z * P.z) / Radius;
float DR = Shape.z * (Q.z - P.z) / Radius;
float A = dot(U,U) - DR*DR, B = 2*(dot(X,U)-R*DR), C = dot(X,X)-R*R;
float Coeff = max(max(abs(A), abs(B)), max(abs(C), 1.e-12));
A /= Coeff; B /= Coeff; C /= Coeff;
float Cuts[6] = {0, 1, 1, 1, 1, 1};
if (abs(A) < 1.e-7)
{
    if (abs(B) >= 1.e-7) Cuts[2] = saturate(-C/B);
}
else
{
    float D = B*B-4*A*C;
    if (D > 0)
    {
        float Root = sqrt(D), K = -.5*(B+(B>=0?Root:-Root));
        if (abs(K) > 1.e-12) { Cuts[2] = saturate(K/A); Cuts[3] = saturate(C/K); }
    }
}
float BottomFeather = min(100, Shape.x*.2), TopFeather = min(300, Shape.x*.2);
if (abs(Q.z-P.z)>1.e-6)
{
    Cuts[4] = saturate((BottomFeather-P.z)/(Q.z-P.z));
    Cuts[5] = saturate((Shape.x-TopFeather-P.z)/(Q.z-P.z));
}
[unroll] for (int I=0; I<5; ++I)
    [unroll] for (int J=0; J<5; ++J)
    {
        float L = min(Cuts[J], Cuts[J+1]), H = max(Cuts[J], Cuts[J+1]);
        Cuts[J] = L; Cuts[J+1] = H;
    }
const float Nodes[4] = {.1834346424956498,.5255324099163290,.7966664774136267,.9602898564975363};
const float Weights[4] = {.3626837833783620,.3137066458778873,.2223810344533745,.1012285362903763};
float Depth=0;
[loop] for (int I=1; I<6; ++I)
{
    float Mid=(Cuts[I]+Cuts[I-1])*.5, Half=(Cuts[I]-Cuts[I-1])*.5;
    if (Half<=0 || (A*Mid+B)*Mid+C>0) continue;
    [unroll] for (int J=0; J<4; ++J)
        [unroll] for (int Sign=-1; Sign<=1; Sign+=2)
        {
            float3 S=lerp(P,Q,Mid+Sign*Half*Nodes[J]);
            float LocalRadius=Shape.y+Shape.z*S.z;
            float2 XY=(S.xy-Wind.xy*S.z/Rise)/max(LocalRadius,1.e-6);
            float Radial=saturate(1-dot(XY,XY));
            float Feather=saturate(min(S.z/BottomFeather,(Shape.x-S.z)/TopFeather));
            Depth+=Span*Half*Weights[J]*Extinction*Strength*Radial*Radial*Feather;
        }
}
return saturate(1-exp(-max(0,Depth)));
