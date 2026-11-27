#pragma once

// Portable revision-2 optical integration; centimetres, extinction/cm.
#include <algorithm>
#include <array>
#include <cmath>

namespace MRSmokeOptics
{
struct Vec { double X,Y,Z; };
struct Field { double Height,Radius,Growth,WindX,WindY,Rise,Extinction,Strength; };
inline Vec Mix(Vec A,Vec B,double T)
{return {A.X+(B.X-A.X)*T,A.Y+(B.Y-A.Y)*T,A.Z+(B.Z-A.Z)*T};}
inline double Density(const Field& F,Vec P)
{
    if(P.Z<=0||P.Z>=F.Height)return 0;
    const double R=F.Radius+F.Growth*P.Z;
    const double X=(P.X-F.WindX*P.Z/F.Rise)/R,Y=(P.Y-F.WindY*P.Z/F.Rise)/R;
    const double Radial=std::max(0.,1-X*X-Y*Y);
    const double Feather=std::min({1.,P.Z/std::min(100.,F.Height*.2),(F.Height-P.Z)/std::min(300.,F.Height*.2)});
    return F.Extinction*F.Strength*Radial*Radial*Feather;
}

// Clip to the actual expanding circular cross-section and its feather bands.
// Each nonempty interval uses eight-point Gauss-Legendre quadrature. This
// avoids sampling clear air and missing very narrow or nearly tangent plumes.
inline double Integrate(const Field& F,Vec Start,Vec End)
{
    if(!(F.Height>0&&F.Radius>0&&F.Growth>=0&&F.Rise>0&&F.Extinction>0&&F.Strength>0))return 0;
    const double DX=End.X-Start.X,DY=End.Y-Start.Y,DZ=End.Z-Start.Z;
    const double Length=std::sqrt(DX*DX+DY*DY+DZ*DZ);
    if(!(Length>1.e-8))return 0;
    double Lo=0,Hi=1;
    if(std::abs(DZ)<1.e-10)
    {if(Start.Z<=0||Start.Z>=F.Height)return 0;}
    else
    {
        const double A=-Start.Z/DZ,B=(F.Height-Start.Z)/DZ;
        Lo=std::max(Lo,std::min(A,B));Hi=std::min(Hi,std::max(A,B));
        if(Hi<=Lo)return 0;
    }
    const Vec P=Mix(Start,End,Lo),Q=Mix(Start,End,Hi);
    const double Span=Length*(Hi-Lo),Scale=F.Radius+F.Growth*F.Height;
    const double X=(P.X-F.WindX*P.Z/F.Rise)/Scale,Y=(P.Y-F.WindY*P.Z/F.Rise)/Scale;
    const double U=(Q.X-F.WindX*Q.Z/F.Rise)/Scale-X,V=(Q.Y-F.WindY*Q.Z/F.Rise)/Scale-Y;
    const double R=(F.Radius+F.Growth*P.Z)/Scale,DR=F.Growth*(Q.Z-P.Z)/Scale;
    double A=U*U+V*V-DR*DR,B=2*(X*U+Y*V-R*DR),C=X*X+Y*Y-R*R;
    const double Coeff=std::max({std::abs(A),std::abs(B),std::abs(C),1.e-12});
    A/=Coeff;B/=Coeff;C/=Coeff;
    std::array<double,6> Cuts{};int Count=0;
    Cuts[Count++]=0;Cuts[Count++]=1;
    const auto Add=[&](double T){if(T>0&&T<1&&Count<int(Cuts.size()))Cuts[Count++]=T;};
    if(std::abs(A)<1.e-7)
    {if(std::abs(B)>=1.e-7)Add(-C/B);}
    else
    {
        const double D=B*B-4*A*C;
        if(D>0)
        {
            const double Root=std::sqrt(D),K=-.5*(B+(B>=0?Root:-Root));
            if(std::abs(K)>1.e-12){Add(K/A);Add(C/K);}
        }
    }
    if(std::abs(Q.Z-P.Z)>1.e-10)
    {
        Add((std::min(100.,F.Height*.2)-P.Z)/(Q.Z-P.Z));
        Add((F.Height-std::min(300.,F.Height*.2)-P.Z)/(Q.Z-P.Z));
    }
    for(int I=1;I<Count;++I)
        for(int J=I;J>0&&Cuts[J]<Cuts[J-1];--J)std::swap(Cuts[J],Cuts[J-1]);
    constexpr std::array<double,4> Nodes={.1834346424956498,.5255324099163290,.7966664774136267,.9602898564975363};
    constexpr std::array<double,4> Weights={.3626837833783620,.3137066458778873,.2223810344533745,.1012285362903763};
    double Depth=0;
    for(int I=1;I<Count;++I)
    {
        const double Mid=(Cuts[I]+Cuts[I-1])*.5,Half=(Cuts[I]-Cuts[I-1])*.5;
        if(Half<=0||(A*Mid+B)*Mid+C>0)continue;
        for(int J=0;J<4;++J)
            Depth+=Span*Half*Weights[J]*(Density(F,Mix(P,Q,Mid-Half*Nodes[J]))+Density(F,Mix(P,Q,Mid+Half*Nodes[J])));
    }
    return std::max(0.,Depth);
}
}
