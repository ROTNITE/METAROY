#include "MRStreetGeometry.h"
#include <cassert>
#include <cmath>
#include <iostream>
using namespace MRStreetGeometry;
static bool Near(double A,double B){return std::abs(A-B)<1.e-6;}
int main()
{
    // Crossing, T-junction, base and door path, diagonal road,
    // a road's own index, wide parallel road, negative bins, overlaps.
    Index Roads;
    auto Own=Roads.Add({-1000,0},{1000,0},700);
    Roads.Add({0,-1000},{0,1000},600);
    auto Sidewalk=Roads.Free({-1000,425},{1000,425},70,Own,2);
    assert(Sidewalk.size()==2&&Near(Sidewalk[0].End,698)&&Near(Sidewalk[1].Begin,1302));
    auto Paint=Roads.Free({-1000,0},{1000,0},6,Own,12);
    assert(Paint.size()==2&&Near(Paint[0].End,688)&&Near(Paint[1].Begin,1312));
    auto Door=Roads.Free({-900,-800},{-900,0},93,Index::None,2);
    assert(Door.size()==1&&Near(Door[0].End,448));
    Index Tee;
    Tee.Add({0,0},{1000,0},700);
    auto Free=Tee.Free({-600,0},{600,0},10);
    assert(Free.size()==1&&Near(Free[0].End,600));
    Index Diagonal;
    Diagonal.Add({-1000,-1000},{1000,1000},600);
    auto Skew=Diagonal.Free({-1200,0},{1200,0},70);
    assert(Skew.size()==2&&Near(Skew[0].End,1200-300*std::sqrt(2.)-70));
    Index Parallel;
    auto Self=Parallel.Add({-5000,0},{5000,0},700);
    Parallel.Add({-2000,600},{2000,600},400);
    auto S=Parallel.Free({-5000,425},{5000,425},70,Self);
    assert(S.size()==2&&Near(S[0].End,3000)&&Near(S[1].Begin,7000));
    Index Overlap;
    Overlap.Add({-200,0},{200,0},100);
    Overlap.Add({100,0},{400,0},100);
    auto R=Overlap.Free({-500,0},{500,0},5);
    assert(R.size()==2&&Near(R[0].End,300)&&Near(R[1].Begin,900));
    assert(Overlap.Free({0,0},{1,0},5).empty());
    // An angled blocker whose AABB overlaps but whose actual rectangle
    // doesn't intersect the swept path must not trim that path.
    auto Miss=Diagonal.Free({-1000,900},{-500,900},20);
    assert(Miss.size()==1&&Near(Miss[0].End,500));
    // Check indexed vs exhaustive rectangle intersection across many
    // negative/positive bins and differently oriented paths.
    Index Grid;std::vector<Box> Boxes;
    for(int I=-8;I<=8;++I)for(int J=-8;J<=8;++J)
    {
        Point A{I*3200.+127,J*3200.+81},B{A.X+800+(I+8)*41,A.Y+300+(J+8)*73};
        Grid.Add(A,B,650);Boxes.push_back(Rectangle(A,B,650));
    }
    for(int I=-40;I<40;++I)for(int J=-12;J<12;++J)
    {
        Point A{I*711.,J*1993.},B{A.X+4331,A.Y+2317};
        const double L=Length(B-A);const Point Along=(B-A)*(1/L);
        const auto Spans=Grid.Free(A,B,70,Index::None,2);
        for(int K=0;K<100;++K)
        {
            double At=(K+.37)*L/100;bool Expected=true,Actual=false;
            for(const auto& Box:Boxes){Span Hit;if(BlockedSpan(A,Along,L,70,Box,2,Hit)&&At>Hit.Begin&&At<Hit.End){Expected=false;break;}}
            for(const auto Span:Spans)Actual|=At>Span.Begin&&At<Span.End;
            assert(Expected==Actual);
        }
    }
    std::cout<<"street clipping: 192000 indexed/exhaustive samples and analytic fixtures passed\n";
}
