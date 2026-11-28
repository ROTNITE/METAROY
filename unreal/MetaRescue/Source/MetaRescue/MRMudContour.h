#pragma once
// Visual-only continuous mask over the existing five-metre occupancy cells.
// No mission, physics, RNG, renderer or Unreal dependency.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>

namespace MRMudContour
{
constexpr double Cell=500.,Blur=115.,Core=.79,Outer=.035;
inline double Smooth(double A,double B,double X)
{
    const double T=std::clamp((X-A)/(B-A),0.,1.);
    return T*T*(3.-2.*T);
}
inline std::uint16_t Bit(int X,int Y){return std::uint16_t(1u<<((Y+1)*3+X+1));}
inline bool Has(std::uint16_t Mask,int X,int Y){return (Mask&Bit(X,Y))!=0;}
inline double Weight(double A,double B,double P)
{
    return Smooth(-Blur,Blur,B-P)-Smooth(-Blur,Blur,A-P);
}
inline double MaskAt(std::uint16_t Neighbors,double X,double Y,double WorldX,double WorldY)
{
    if(!Has(Neighbors,0,0))return 0.;
    double Coverage=0,Distance=Cell;
    for(int J=-1;J<=1;++J)for(int I=-1;I<=1;++I)
    {
        const double A=I*Cell,B=J*Cell;
        if(Has(Neighbors,I,J))
            Coverage+=Weight(A,A+Cell,X)*Weight(B,B+Cell,Y);
        else
        {
            const double DX=std::max({A-X,0.,X-A-Cell}),DY=std::max({B-Y,0.,Y-B-Cell});
            Distance=std::min(Distance,std::hypot(DX,DY));
        }
    }
    // Continuous world-space harmonics, not cell-local random seeds.
    // The asymmetric wavelengths keep convex corners rounded and give
    // exposed sides broad lobes and small irregular wet fingers.
    const double U=WorldX*.01,V=WorldY*.01;
    const double Noise=.070*std::sin(2.07*U+.91*V+.7*std::sin(V*1.31))+
        .045*std::sin(-.83*U+3.13*V+.5)+.018*std::sin(U*8.6+V*6.7);
    const double Irregular=Coverage+Noise*(1.-Smooth(.78,1.,Coverage));
    // The visible rim never spills into physically unaffected cells.
    // At shared occupied edges the field is identical on both sides.
    return Smooth(.39,.92,Irregular)*Smooth(0.,38.,Distance);
}

struct Sample
{
    double X=0,Y=0,Z=0,NX=0,NY=0,NZ=1,Mask=0;
};
inline Sample Interpolate(const Sample& A,const Sample& B,double T)
{
    const auto L=[&](double X,double Y){return X+(Y-X)*T;};
    return {L(A.X,B.X),L(A.Y,B.Y),L(A.Z,B.Z),L(A.NX,B.NX),L(A.NY,B.NY),L(A.NZ,B.NZ),L(A.Mask,B.Mask)};
}
struct Polygon{std::array<Sample,6> Points{};int Count=0;};
inline Polygon Clip(const Polygon& Input,double Threshold,bool Above)
{
    Polygon Result;
    for(int I=0;I<Input.Count;++I)
    {
        const auto& A=Input.Points[I];const auto& B=Input.Points[(I+1)%Input.Count];
        const bool InA=Above?A.Mask>=Threshold:A.Mask<=Threshold;
        const bool InB=Above?B.Mask>=Threshold:B.Mask<=Threshold;
        if(InA)Result.Points[Result.Count++]=A;
        if(InA!=InB)Result.Points[Result.Count++]=Interpolate(A,B,(Threshold-A.Mask)/(B.Mask-A.Mask));
    }
    return Result;
}
inline Polygon Triangle(Sample A,Sample B,Sample C)
{
    Polygon P;P.Points[0]=A;P.Points[1]=B;P.Points[2]=C;P.Count=3;return P;
}
}
