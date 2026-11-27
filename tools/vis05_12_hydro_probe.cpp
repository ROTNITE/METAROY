#include "../unreal/MetaRescue/Source/MetaRescue/MRWatercraftCorridor.h"
// Compile the actual production numeric solver and signal phase function,
// using only the explicitly test-only value/container adapter.
#ifndef MR_HYDROLOGY_HEADER
#define MR_HYDROLOGY_HEADER "../unreal/MetaRescue/Source/MetaRescue/MRHydrology.h"
#endif
#include MR_HYDROLOGY_HEADER
#include "../unreal/MetaRescue/Source/MetaRescue/MRStreetSignals.h"
#include <chrono>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

void require(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
void balance(const FMRHydrology& grid)
{
    require(std::abs(grid.BalanceErrorM3())<1.e-5+grid.VolumeM3()*1.e-10,"water ledger");
}
std::uint64_t hash_grid(const FMRHydrology& grid)
{
    std::uint64_t h=14695981039346656037ull;
    for(int32 y=0;y<grid.GetHeight();++y)for(int32 x=0;x<grid.GetWidth();++x)
    {
        const auto i=grid.Index(x,y);const auto current=grid.CurrentMPerSecond(i);
        for(double value:{grid.DepthM(i),grid.StageM(i),current.X,current.Y})
        {
            // Canonicalize signed zero only; all other double bits matter.
            if(value==0)value=0;
            std::uint64_t bits=0;std::memcpy(&bits,&value,sizeof(bits));h^=bits;h*=1099511628211ull;
        }
    }
    return h;
}
void closed_and_barrier()
{
    FMRHydrology g;TArray<double> bed={0,.4,.8,1.2,1.6},water={1.6,1.2,.8,.4,0};TBitArray<> solid(false,5);
    require(g.Configure(5,1,5,bed,solid,water),"lake configure");
    require(g.Advance(10),"lake advance");
    for(int i=0;i<5;++i)require(std::abs(g.DepthM(i)-water[i])<1.e-7,"lake rest");
    bed={0,0,0,0,0};water={2,2,0,0,0};
    require(g.Configure(5,1,5,bed,solid,water),"bank configure");
    require(g.SetCrestX(1,0,3.4,true),"crest");
    require(g.Advance(15)&&g.DepthM(2)==0,"closed bank");
    require(g.SetCrestX(1,0,0)&&g.Advance(15)&&g.DepthM(2)>.05,"opened bank");
    balance(g);g.Reset();
    require(g.Advance(15)&&g.DepthM(2)==0,"reset restores bank");
    solid[2]=true;require(g.Configure(5,1,5,bed,solid,water)&&g.Advance(20),"solid advance");
    require(g.DepthM(2)==0&&g.DepthM(3)==0,"solid cannot transmit");
    require(!g.SetBoundaryStage(0,std::numeric_limits<double>::quiet_NaN()),"reject NaN");
    require(!g.SetBoundaryStage(2,2),"reject solid boundary");
    require(!g.Advance(-1),"reject negative time");
    const double volume=g.VolumeM3();require(g.SetBed(1,-1)&&g.VolumeM3()==volume,"subsidence preserves water");
    std::cout<<"closed, equilibrium, crest, solid, reset, invalid data and subsidence passed\n";
}
void open_field(int size,int seconds,bool repeat)
{
    const int32 w=2*size+21,h=2*size,n=w*h;
    TArray<double> bed,water;bed.Init(0,n);water.Init(0,n);TBitArray<> solid(false,n);
    // Synthetic receiving street: one retention basin and an impermeable
    // building island. Same resolution/extent as the real city, not its map.
    for(int y=5;y<13;++y)for(int x=2;x<9;++x){bed[y*w+x]=-7.2;water[y*w+x]=7.25;}
    for(int y=14;y<20;++y)for(int x=22;x<26;++x)solid[y*w+x]=true;
    FMRHydrology g;require(g.Configure(w,h,5,bed,solid,water),"large field configure");
    auto run=[&]()
    {
        for(int y=5;y<13;++y)require(g.SetCrestX(8,y,.05,true),"basin crest");
        require(g.SetDrain(g.Index(26,10),.025),"drain");
        for(int i=1;i<=seconds*10;++i)
        {
            const double level=.05+2.75*std::min(1.,i*.1/70.);
            for(int y=6;y<12;++y)require(g.SetBoundaryStage(g.Index(2,y),level),"source");
            for(int y=0;y<h;++y)require(g.SetBoundaryStage(g.Index(w-1,y),0),"outlet");
            require(g.Advance(.1),"finite hydraulic step");
            if(i%100==0)balance(g);
        }
        balance(g);
        int wet=0,street=0;
        for(int y=0;y<h;++y)for(int x=0;x<w;++x)
        {
            const double d=g.DepthM(g.Index(x,y));
            require(std::isfinite(d)&&d>=0&&(!solid[y*w+x]||d==0),"finite nonnegative dry solids");
            if(d>.001){++wet;if(x>=21)++street;}
        }
        require(street>0&&g.AddedM3()>0&&g.RemovedM3()>0,"street inundation and drainage");
        std::cout<<"size="<<size<<" cells="<<n<<" time="<<g.Time()<<" wet="<<wet<<" street="<<street
                 <<" added="<<g.AddedM3()<<" removed="<<g.RemovedM3()<<" balance="<<g.BalanceErrorM3()
                 <<" checksum="<<std::hex<<hash_grid(g)<<std::dec<<"\n";
        return hash_grid(g);
    };
    const auto start=std::chrono::steady_clock::now();auto hash=run();
    if(repeat){g.Reset();require(g.Time()==0&&g.AddedM3()==0,"clean reset");require(run()==hash,"exact replay");}
    std::cout<<"size="<<size<<" elapsed_wall_s="<<std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<<"\n";
}
void watercraft_corridor()
{
    auto safe=[](double,double){return true;};
    require(MRWatercraftCorridorClear(0,0,800,0,94,safe),"clear native corridor");
    require(!MRWatercraftCorridorClear(0,0,800,0,94,[](double x,double){return x<300||x>400;}),
        "intermediate shallow bar");
    require(!MRWatercraftCorridorClear(0,0,800,0,94,[](double x,double y){return x<250||x>500||y<90;}),
        "shallow starboard track");
    require(!MRWatercraftCorridorClear(0,0,800,0,94,[](double x,double y){return x<250||x>500||y>-90;}),
        "shallow port track");
    require(!MRWatercraftCorridorClear(800,0,0,0,94,[](double x,double){return x>500;}),
        "reverse drift meets domain edge");
    require(!MRWatercraftCorridorClear(0,0,3201,0,94,safe),"prediction budget");
    require(!MRWatercraftCorridorClear(0,0,std::numeric_limits<double>::quiet_NaN(),0,94,safe),"nonfinite prediction");
    int calls=0;
    require(MRWatercraftCorridorClear(0,0,3200,0,94,[&](double,double){++calls;return true;})&&calls==192,
        "bounded sample count");
    std::cout<<"native watercraft corridor: both tracks, intervening shallows and bounds passed\n";
}
void wet_fringe()
{
    FMRHydrology g;TArray<double> bed={0,0,.4,0,0,0};TArray<double> depth={.2,0,0,0,0,0};
    TBitArray<> solid(false,6);solid[3]=true;
    require(g.Configure(3,2,5,bed,solid,depth),"fringe configure");
    const double volume=g.VolumeM3();
    const double width=g.WetFringeWidthCm(0,1);
    require(width>35&&width<=120,"adjacent wet fringe");
    require(g.WetFringeWidthCm(0,3)==0,"no wet fringe in solid");
    require(g.WetFringeWidthCm(0,4)==0,"no diagonal fringe");
    require(g.WetFringeWidthCm(-1,1)==0&&g.WetFringeWidthCm(0,6)==0,"fringe bounds");
    require(g.WetFringeWidthCm(1,0)==0,"fringe direction");
    require(g.SetCrestX(0,0,.5,true)&&g.WetFringeWidthCm(0,1)==0,"fringe blocked by crest");
    require(g.SetCrestX(0,0,0,true)&&g.WetFringeWidthCm(0,1)==width,"fringe reopened");
    require(g.SetBed(1,.5)&&g.WetFringeWidthCm(0,1)==0,"fringe high bank");
    require(g.SetBed(1,-.5)&&g.WetFringeWidthCm(0,1)==0,"fringe deep dry hole");
    require(g.VolumeM3()==volume,"fringe has no water mass");
    g.Reset();require(g.WetFringeWidthCm(0,1)==width,"fringe reset");
    std::cout<<"wet fringe: dry-neighbor, barrier, footprint, height, direction, reset and mass checks passed\n";
}
int main(int argc,char** argv)
{
    std::cout<<std::setprecision(12);
    closed_and_barrier();wet_fringe();watercraft_corridor();
    for(int offset=0;offset<28;++offset)for(int t=0;t<2800;++t)
    {
        const auto a=MRStreetPhase(t*.1,offset,0,true),b=MRStreetPhase(t*.1,offset,1,true);
        require(!(a==EMRStreetPhase::Green&&b==EMRStreetPhase::Green),"conflicting green");
        require(!(a==EMRStreetPhase::Green&&b==EMRStreetPhase::Amber)&&
                !(b==EMRStreetPhase::Green&&a==EMRStreetPhase::Amber),"conflicting transition");
        require(MRStreetPhase(t*.1,offset,0,false)==EMRStreetPhase::Dark,"power failure");
    }
    std::cout<<"78400 signal phase pairs and outage checks passed\n";
    open_field(25,150,true);
    if(argc>1&&std::string(argv[1])=="--full"){open_field(75,480,false);open_field(150,480,false);}
    return 0;
}
