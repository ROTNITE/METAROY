// g++ -std=c++17 -O2 tools/smoke_optics_probe.cpp -o smoke_probe
#include "../unreal/MetaRescue/Source/MetaRescue/MRSmokeOptics.h"
#include <cassert>
#include <iostream>
#include <random>
using namespace MRSmokeOptics;

static double Reference(const Field& F,Vec A,Vec B)
{
    const double L=std::sqrt(std::pow(B.X-A.X,2)+std::pow(B.Y-A.Y,2)+std::pow(B.Z-A.Z,2));
    double Sum=0;constexpr int N=40000;
    for(int I=0;I<N;++I)Sum+=Density(F,Mix(A,B,(I+.5)/N));
    return Sum*L/N;
}
int main()
{
    const Field F{3000,1000,0,0,0,150,.003,1};
    const double Exact=.003*1000*16/15.;
    assert(std::abs(Integrate(F,{-1000,0,1500},{1000,0,1500})-Exact)<1.e-10);
    assert(Integrate(F,{-1000000,0,1500},{1000000,0,1500})>3.199999);
    assert(Integrate(F,{-1000,1001,1500},{1000,1001,1500})==0);
    assert(Integrate(F,{0,0,3100},{0,0,3200})==0);
    assert(Integrate(F,{0,0,1500},{0,0,1500})==0);
    assert(std::abs(Integrate(F,{-1000,990,1500},{1000,990,1500})-Reference(F,{-1000,990,1500},{1000,990,1500}))<1.e-7);
    std::mt19937 Rng(2026);std::uniform_real_distribution<double> D(-8000,8000),Z(-600,5000),W(-400,400);
    double MaxTransmissionError=0,MaxDepthError=0;
    for(int I=0;I<500;++I)
    {
        Field P{4200,650,.28,W(Rng),W(Rng),280,.006,1};
        Vec A{D(Rng),D(Rng),Z(Rng)},B{D(Rng),D(Rng),Z(Rng)};
        const double Actual=Integrate(P,A,B),Expected=Reference(P,A,B);
        MaxDepthError=std::max(MaxDepthError,std::abs(Actual-Expected));
        MaxTransmissionError=std::max(MaxTransmissionError,std::abs(std::exp(-Actual)-std::exp(-Expected)));
        assert(std::abs(Actual-Integrate(P,B,A))<1.e-8);
        assert(std::abs(std::exp(-Actual)-std::exp(-Expected))<.001);
    }
    std::cout<<"500 seeded slanted/wind rays + 6 analytical/boundary checks passed; maximum transmission error "<<MaxTransmissionError<<", depth error "<<MaxDepthError<<"\n";
}
