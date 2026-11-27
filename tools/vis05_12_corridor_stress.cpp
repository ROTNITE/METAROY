// Portable production-header stress; no Unreal stubs or game FPS claims.
#include "../unreal/MetaRescue/Source/MetaRescue/MRWatercraftCorridor.h"
#include "../unreal/MetaRescue/Source/MetaRescue/MRHorizonRoof.h"
#include <cassert>
#include <iostream>
#include <limits>
#include <random>
#include <chrono>

int main(){
 std::mt19937_64 random(271828);
 std::uniform_real_distribution<double> angle(-3.141592653589793,3.141592653589793),distance(5,3199),offset(-100000,100000);
 int blocked=0; long long samples=0; auto start=std::chrono::steady_clock::now();
 for(int i=0;i<20000;++i){
   double a=angle(random),l=distance(random),x=offset(random),y=offset(random),dx=std::cos(a),dy=std::sin(a);
   int calls=0;
   assert(MRWatercraftCorridorClear(x,y,x+dx*l,y+dy*l,94,[&](double X,double Y){++calls; assert(std::isfinite(X)&&std::isfinite(Y));return true;}));
   assert(calls<=192 && calls>=3); samples+=calls;
   // A central shallow strip with width >=100 cm at a sampled interior location.
   if(l>250){assert(!MRWatercraftCorridorClear(x,y,x+dx*l,y+dy*l,94,[&](double X,double Y){double t=(X-x)*dx+(Y-y)*dy;return std::abs(t-l*.5)>50;}));++blocked;}
   // Either hull track, including reversed/rotated trajectories.
   assert(!MRWatercraftCorridorClear(x,y,x+dx*l,y+dy*l,94,[&](double X,double Y){return (Y-y)*dx-(X-x)*dy<93.;}));
   assert(!MRWatercraftCorridorClear(x,y,x+dx*l,y+dy*l,94,[&](double X,double Y){return (Y-y)*dx-(X-x)*dy> -93.;}));
 }
 assert(!MRWatercraftCorridorClear(0,0,3201,0,94,[](double,double){return true;}));
 assert(!MRWatercraftCorridorClear(0,0,std::numeric_limits<double>::quiet_NaN(),0,94,[](double,double){return true;}));
 int roofs=0;
 for(int f=1;f<=5;++f)for(int v=0;v<9;++v){double w=v%2?740:890,d=v<2?720:870;assert(!FMRHorizonRoof::Make(false,f,v,w,d).Enabled());auto r=FMRHorizonRoof::Make(true,f,v,w,d);if(!r.Enabled())continue;++roofs;assert(r.RidgeZ()>r.WallTop);assert(std::abs(r.HeightAt(r.HalfDepth)-r.WallTop)<1e-9);assert(r.HeightAt(r.EaveDepth())<r.WallTop && r.HeightAt(r.EaveDepth())>r.WallTop-20);}
 assert(roofs==12);
 std::cout << "20,000 randomized clear corridors, "<<blocked<<" intermediate bars, 40,000 side shallows, "<<samples<<" clear samples; 45 roof variants, 12 pitched; seconds="<<std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<<'\n';
}
