#include "../unreal/MetaRescue/Source/MetaRescue/MRSmokeOptics.h"
#include <iostream>
#include <random>
#include <iomanip>
#include <limits>
using namespace MRSmokeOptics;
static double Ref(const Field& f,Vec a,Vec b){
    const double L=std::sqrt((b.X-a.X)*(b.X-a.X)+(b.Y-a.Y)*(b.Y-a.Y)+(b.Z-a.Z)*(b.Z-a.Z));
    constexpr int N=20000;
    double sum=0;for(int i=0;i<N;++i)sum+=Density(f,Mix(a,b,(i+.5)/N));return L*sum/N;
}
int main(){
    std::mt19937_64 g(20261127);std::uniform_real_distribution<double> u(0,1),w(-800,800),xy(-2,2),z(-.5,1.5);
    double err=0,rev=0,depthErr=0;int bad=0,nonzero=0;
    for(int i=0;i<6000;i++){
        int type=i%4;double rise=type==0?280:100,maxH=type==0?4200:type==1?900:type==2?2800:3000;
        double radius=type==0?650:type==1?1300:type==2?1800:1000, growth=type==0?.28:.1;
        Field f{std::max(1.,u(g)*maxH),radius,growth,w(g),w(g),rise,type==1?.0018:type==2?.0004:.006,u(g)};
        if(type==1){f.WindX*=.2;f.WindY*=.2;}if(type==2){f.WindX=0;f.WindY=0;}
        auto point=[&](){double h=z(g)*f.Height;double r=f.Radius+f.Growth*std::clamp(h,0.,f.Height);return Vec{f.WindX*h/f.Rise+xy(g)*r,f.WindY*h/f.Rise+xy(g)*r,h};};
        Vec a=point(),b=point();if(i%7==0){const double h=u(g)*f.Height;a={f.WindX*h/f.Rise,f.WindY*h/f.Rise,h};}
        const double actual=Integrate(f,a,b), reference=Ref(f,a,b), e=std::abs(std::exp(-actual)-std::exp(-reference));
        const double r=std::abs(actual-Integrate(f,b,a));err=std::max(err,e);rev=std::max(rev,r);depthErr=std::max(depthErr,std::abs(actual-reference));
        nonzero+=reference>.001;bad+=!std::isfinite(actual)||e>1.e-3||r>1.e-8;
    }
    std::cout<<std::setprecision(12)<<"cases=6000 nonzero="<<nonzero<<" max_transmission_error="<<err<<" max_depth_error="<<depthErr<<" max_reverse_depth_error="<<rev<<" failures="<<bad<<"\n";return bad?1:0;
}
