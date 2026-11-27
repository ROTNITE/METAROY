#pragma once
#include <cmath>

// Bounded native depth prediction in centimetres. Sample the swept centre
// and both hull tracks, not just the commanded endpoint. Collision sweeps
// and the continuous physics integrator remain independently authoritative.
template<class TDepthCheck>
bool MRWatercraftCorridorClear(double X0,double Y0,double X1,double Y1,
    double HalfWidth,TDepthCheck&& Safe)
{
    if(!std::isfinite(X0)||!std::isfinite(Y0)||!std::isfinite(X1)||!std::isfinite(Y1)||
       !std::isfinite(HalfWidth)||HalfWidth<0||HalfWidth>250)return false;
    const double DX=X1-X0,DY=Y1-Y0,Length=std::hypot(DX,DY);
    // Limit local prediction work and fail closed on invalid velocities.
    if(!std::isfinite(Length)||Length>3200)return false;
    if(Length<1.e-6)return Safe(X1,Y1);
    const int Steps=static_cast<int>(std::ceil(Length/50.));
    const double SX=-DY/Length*HalfWidth,SY=DX/Length*HalfWidth;
    for(int I=1;I<=Steps;++I)
    {
        const double T=double(I)/Steps,X=X0+DX*T,Y=Y0+DY*T;
        if(!Safe(X,Y)||!Safe(X+SX,Y+SY)||!Safe(X-SX,Y-SY))return false;
    }
    return true;
}
