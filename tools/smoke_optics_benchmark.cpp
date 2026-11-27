// Synthetic CPU comparison; neither Unreal frame time nor GPU throughput.
#include "../unreal/MetaRescue/Source/MetaRescue/MRSmokeOptics.h"
#include <chrono>
#include <iomanip>
#include <iostream>
#include <random>
#include <vector>

using namespace MRSmokeOptics;
struct Ray { Field f; Vec a,b; };

// Previous fixed-distance Simpson policy on already clipped representative rays.
static double Simpson(const Ray& r)
{
    const auto& a=r.a;const auto& b=r.b;const auto& f=r.f;
    const double length=std::sqrt((b.X-a.X)*(b.X-a.X)+(b.Y-a.Y)*(b.Y-a.Y)+(b.Z-a.Z)*(b.Z-a.Z));
    int n=std::clamp(int(std::ceil(length/75)),2,256);n+=n%2;
    double sum=Density(f,a)+Density(f,b);
    for(int i=1;i<n;++i)sum+=(i%2?4.:2.)*Density(f,Mix(a,b,double(i)/n));
    return sum*length/(3*n);
}
int main()
{
    std::mt19937 rng(2026);
    std::uniform_real_distribution<double> wind(-400,400),z(10,4190),xy(-2000,2000);
    std::vector<Ray> rays;rays.reserve(100000);
    for(int i=0;i<100000;++i)
    {
        Field f{4200,650,.28,wind(rng),wind(rng),280,.006,1};
        rays.push_back({f,{xy(rng),xy(rng),z(rng)},{xy(rng),xy(rng),z(rng)}});
    }
    std::vector<double> legacy,revised;
    for(int rep=-1;rep<5;++rep)
    {
        for(int pass=0;pass<2;++pass)
        {
            const bool useNew=((rep+pass+1)%2)==1;
            double sum=0;const auto start=std::chrono::steady_clock::now();
            for(const auto& ray:rays)sum+=useNew?Integrate(ray.f,ray.a,ray.b):Simpson(ray);
            const double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
            if(!std::isfinite(sum)||sum<=0)return 1;
            std::cout<<(useNew?"revised":"simpson")<<" "<<ms<<" ms checksum "<<std::setprecision(12)<<sum<<"\n";
            if(rep>=0)(useNew?revised:legacy).push_back(ms);
        }
    }
    std::sort(legacy.begin(),legacy.end());std::sort(revised.begin(),revised.end());
    std::cout<<"100000 rays, five timed repetitions after warmup: median Simpson "<<legacy[2]
             <<" ms, revised "<<revised[2]<<" ms, ratio "<<legacy[2]/revised[2]<<"x\n";
}
