#include "MRMudContour.h"
#include <cassert>
#include <iostream>
using namespace MRMudContour;

int main()
{
    const auto Single=Bit(0,0);
    assert(MaskAt(Single,250,250,250,250)>.99);
    for(int I=0;I<=500;++I)
    {
        assert(MaskAt(Single,0,I,0,I)==0);
        assert(MaskAt(Single,500,I,500,I)==0);
        assert(MaskAt(Single,I,0,I,0)==0);
        assert(MaskAt(Single,I,500,I,500)==0);
    }
    // Enumerate all 256 local neighbourhood patterns. Same world-space
    // field on shared cell edges, including L-shaped concave corners.
    int Count=0;
    for(unsigned Bits=0;Bits<512;++Bits)
    {
        const auto Mask=std::uint16_t(Bits|Single);
        for(int I=0;I<=10;++I)for(int J=0;J<=10;++J)
        {
            const double X=I*50.,Y=J*50.;
            const double A=MaskAt(Mask,X,Y,X-15000,Y+5000);
            assert(std::isfinite(A)&&A>=0&&A<=1);
            assert(A==MaskAt(Mask,X,Y,X-15000,Y+5000));
            ++Count;
        }
    }
    // Compare the same physical seam from each cell, also with extra
    // neighbours on either side outside the blur support.
    for(unsigned Bits=0;Bits<4096;++Bits)
    {
        const unsigned Global=Bits|(1u<<5)|(1u<<6);
        std::uint16_t Left=0,Right=0;
        for(int J=-1;J<=1;++J)for(int I=-1;I<=1;++I)
        {
            if(Global&(1u<<((J+1)*4+I+1)))Left|=Bit(I,J);
            if(Global&(1u<<((J+1)*4+I+2)))Right|=Bit(I,J);
        }
        for(int Y=0;Y<=500;Y+=25)
        {
            const double A=MaskAt(Left,500,Y,-2500.,Y+5000);
            const double B=MaskAt(Right,0,Y,-2500.,Y+5000);
            assert(std::abs(A-B)<1.e-10);
            ++Count;
        }
    }
    double Fraction=0;
    for(int X=0;X<500;X+=5)for(int Y=0;Y<500;Y+=5)
        Fraction+=MaskAt(Single,X+2.5,Y+2.5,X+2.5,Y+2.5)>=Core;
    assert(Fraction>4500&&Fraction<9000);
    // Straight edges are not merely inset rectangles.
    int Low=500,High=0;
    for(int Y=100;Y<=400;Y+=5)
    {
        int X=0;while(X<250&&MaskAt(Single,X,Y,X,Y)<Core)++X;
        Low=std::min(Low,X);High=std::max(High,X);
    }
    assert(High-Low>8);
    Sample A{0,0,0,0,0,1,0},B{100,0,10,0,0,1,1},C{0,100,20,0,0,1,1};
    const auto P=Clip(Triangle(A,B,C),.5,true);
    assert(P.Count==4);
    for(int I=0;I<P.Count;++I)assert(P.Points[I].Mask>=.5-1.e-12);
    const auto Rim=Clip(Clip(Triangle(A,B,C),Outer,true),Core,false);
    assert(Rim.Count>=3&&Rim.Count<=5);
    for(int I=0;I<Rim.Count;++I)assert(Rim.Points[I].Mask>=Outer-1.e-12&&Rim.Points[I].Mask<=Core+1.e-12);
    std::cout<<"mud contour: "<<Count<<" finite, deterministic and seamless samples; single-cell core "<<Fraction/100<<"%; irregular boundary range "<<Low<<".."<<High<<" cm\n";
}
