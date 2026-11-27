#pragma once
#include <cmath>

// Decorative roof profile in centimetres. Never part of the mission map,
// navmesh, sensors or collision. Shared with the portable geometry test.
struct FMRHorizonRoof
{
    double HalfLength=0,HalfDepth=0,WallTop=0,Rise=0;
    static FMRHorizonRoof Make(bool Revised,int Floors,int Variant,double W,double D)
    {
        if(!Revised||Floors<1||Floors>2||Variant<0||Variant%3==2||
           !std::isfinite(W)||!std::isfinite(D)||W<300||D<300||W>1500||D>1500)return {};
        return {W*.5,D*.5,300.*Floors,145.+25.*(Variant%3)};
    }
    bool Enabled() const {return Rise>0;}
    double RidgeZ() const {return WallTop+Rise;}
    double HeightAt(double Y) const {return RidgeZ()-Rise*std::abs(Y)/HalfDepth;}
    double EaveDepth() const {return HalfDepth+25.;}
    double EaveLength() const {return HalfLength+25.;}
};
