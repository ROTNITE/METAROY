#pragma once

// Pure planar geometry, shared by the runtime and the portable regression.
// Distances are centimetres. A swept cross-section of a path is clipped
// against the actual oriented rectangles used by the road support meshes.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <map>
#include <utility>
#include <vector>

namespace MRStreetGeometry
{
struct Point
{
    double X=0,Y=0;
    Point operator+(Point B)const{return {X+B.X,Y+B.Y};}
    Point operator-(Point B)const{return {X-B.X,Y-B.Y};}
    Point operator*(double S)const{return {X*S,Y*S};}
};
inline double Dot(Point A,Point B){return A.X*B.X+A.Y*B.Y;}
inline double Length(Point A){return std::hypot(A.X,A.Y);}
inline Point Normal(Point A){return {-A.Y,A.X};}
struct Span{double Begin,End;};
struct Box
{
    Point Center,Along,Across;
    double HalfLength,HalfWidth;
};
inline Box Rectangle(Point A,Point B,double Width)
{
    const double L=Length(B-A);
    const Point Along=L>0?(B-A)*(1/L):Point{1,0};
    return {(A+B)*.5,Along,Normal(Along),L*.5,Width*.5};
}

// The separating axes of a rectangle and a segment are the two
// rectangle axes and the segment's normal. For each axis constrain the
// moving cross-section's centre to the interval in which projections meet.
inline bool BlockedSpan(Point Start,Point Along,double L,double HalfWidth,
                        const Box& B,double Margin,Span& Hit)
{
    Hit={0,L};
    const Point Across=Normal(Along);
    for(Point Axis:{B.Along,B.Across,Along})
    {
        const double Radius=(B.HalfLength+Margin)*std::abs(Dot(B.Along,Axis))+
            (B.HalfWidth+Margin)*std::abs(Dot(B.Across,Axis))+
            HalfWidth*std::abs(Dot(Across,Axis));
        const double Offset=Dot(Start-B.Center,Axis),Speed=Dot(Along,Axis);
        if(std::abs(Speed)<1.e-10)
        {
            if(std::abs(Offset)>Radius)return false;
            continue;
        }
        double A=(-Radius-Offset)/Speed,C=(Radius-Offset)/Speed;
        if(A>C)std::swap(A,C);
        Hit.Begin=std::max(Hit.Begin,A);Hit.End=std::min(Hit.End,C);
        if(Hit.End<=Hit.Begin)return false;
    }
    return true;
}

class Index
{
    static constexpr double Cell=3200;
    std::vector<Box> Boxes;
    std::map<std::pair<int,int>,std::vector<std::size_t>> Bins;
    static int Bin(double V){return static_cast<int>(std::floor(V/Cell));}
public:
    static constexpr std::size_t None=std::numeric_limits<std::size_t>::max();
    std::size_t Add(Point A,Point B,double Width)
    {
        const Box Item=Rectangle(A,B,Width);
        const std::size_t Id=Boxes.size();Boxes.push_back(Item);
        const double X=std::abs(Item.Along.X)*Item.HalfLength+std::abs(Item.Across.X)*Item.HalfWidth;
        const double Y=std::abs(Item.Along.Y)*Item.HalfLength+std::abs(Item.Across.Y)*Item.HalfWidth;
        for(int I=Bin(Item.Center.X-X);I<=Bin(Item.Center.X+X);++I)
            for(int J=Bin(Item.Center.Y-Y);J<=Bin(Item.Center.Y+Y);++J)Bins[{I,J}].push_back(Id);
        return Id;
    }
    std::vector<Span> Free(Point A,Point B,double HalfWidth,std::size_t Skip=None,double Margin=0)const
    {
        const double L=Length(B-A);
        if(L<2)return {};
        const Point Along=(B-A)*(1/L);
        const double Reach=HalfWidth+2*Margin+1;
        std::vector<std::size_t> Candidates;
        for(int I=Bin(std::min(A.X,B.X)-Reach);I<=Bin(std::max(A.X,B.X)+Reach);++I)
            for(int J=Bin(std::min(A.Y,B.Y)-Reach);J<=Bin(std::max(A.Y,B.Y)+Reach);++J)
            {
                const auto Found=Bins.find({I,J});
                if(Found!=Bins.end())Candidates.insert(Candidates.end(),Found->second.begin(),Found->second.end());
            }
        std::sort(Candidates.begin(),Candidates.end());
        Candidates.erase(std::unique(Candidates.begin(),Candidates.end()),Candidates.end());
        std::vector<Span> Hits;
        for(const auto Id:Candidates)
        {
            Span Hit;
            if(Id!=Skip&&BlockedSpan(A,Along,L,HalfWidth,Boxes[Id],Margin,Hit))Hits.push_back(Hit);
        }
        std::sort(Hits.begin(),Hits.end(),[](Span A,Span B){return A.Begin<B.Begin;});
        std::vector<Span> FreeSpans;
        double At=0;
        for(const auto Hit:Hits)
        {
            if(Hit.Begin>At+1.e-6)FreeSpans.push_back({At,Hit.Begin});
            At=std::max(At,Hit.End);
        }
        if(L>At+1.e-6)FreeSpans.push_back({At,L});
        return FreeSpans;
    }
};
}
