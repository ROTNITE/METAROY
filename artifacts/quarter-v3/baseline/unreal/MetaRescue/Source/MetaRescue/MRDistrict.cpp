#include "MRWorld.h"
#include "Components/StaticMeshComponent.h"
#include "Components/TextRenderComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInterface.h"
#include "ProceduralMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"

USceneComponent* AMRWorld::DistrictAssembly(const FString& Name,const FVector& P,const FRotator& R,
    const FVector& S,bool Collision,bool Movable,const FString& Module)
{
    TArray<FMRAssemblyPart> Parts;
    auto* Root=MRBuildAssembly(this,GetRootComponent(),Name,P,R,S,Collision,Movable,Parts,Module);
    if(Root)Generated.Add(Root);
    for(auto& Part:Parts){Generated.Add(Part.Mesh);if(Movable)DistrictMechanisms.Add(Part);}
    return Root;
}

void AMRWorld::Apartment(const FVector& P,int32 Floors,int32 Variant,bool Damaged)
{
    // Three 3 m bays per side; real floor plates and internal partitions are
    // behind the glazing. Missing upper bays expose rooms in damaged variants.
    for(int32 Level=0;Level<Floors;Level++)
    {
        const float Z=Level*300;
        Shape("Cube",P+FVector(0,0,Z+8),FVector(900,900,16),"MI_Concrete");
        Shape("Cube",P+FVector(0,0,Z+148),FVector(15,870,280),"MI_Plaster");
        for(int32 Side=0;Side<4;Side++)for(int32 Bay=0;Bay<3;Bay++)
        {
            if(Damaged&&Level==Floors-1&&((Side+Bay+Variant)%4==0))continue;
            const FRotator Turn(0,Side*90,0);
            const FVector At=P+Turn.RotateVector(FVector((Bay-1)*300,-450,Z+16));
            const bool Door=Level==0&&Bay==1&&Side==0;
            const FString Wall=Door?TEXT("wall_door_centered_small_01"):
                FString::Printf(TEXT("wall_window_centered_large_0%d"),1+(Bay+Side+Variant)%3);
            DistrictAssembly("UrbanKit",At,Turn,FVector::OneVector,true,false,Wall);
            const FString Insert=Door?TEXT("door_centered_small_01"):
                FString::Printf(TEXT("window_centered_large_0%d"),1+(Bay+Side+Variant)%3);
            // Inserts use the same catalog ground datum as their matching wall.
            DistrictAssembly("UrbanKit",At+Turn.RotateVector(FVector(0,-4,0)),Turn,FVector::OneVector,true,false,Insert);
            DistrictAssembly("UrbanKit",P+Turn.RotateVector(FVector((Bay-1)*300,-457,Z+288)),Turn,
                FVector::OneVector,false,false,"cornice_standard_standard_01");
            if(Level==0)DistrictAssembly("UrbanKit",At,Turn,FVector::OneVector,true,false,"base_standard_01");
        }
    }
    if(!Damaged)Shape("Cube",P+FVector(0,0,Floors*300+12),FVector(945,945,24),"MI_Dark");
    else DistrictAssembly("Slab",P+FVector(240,100,Floors*300-50),FRotator(8,25,11),FVector(.8));
    // Roof infrastructure differs by block; no random props in evacuation lanes.
    if(!Damaged&&Variant%2==0)DistrictAssembly("UtilityBox",P+FVector(220,150,Floors*300+24),FRotator::ZeroRotator,FVector(.8));
}

UStaticMeshComponent* AMRWorld::DistrictMesh(const FString& Name, const FVector& P,
    const FRotator& R, const FVector& Scale, bool Collision)
{
    static const TSet<FString> Library={"Building","WindowWall","Stretcher","Medical","Dock","Relay","Sensor","Barrier","Slab","Doorway","Rubble","Streetlight","UtilityBox","Manhole","Generator","Van","Case","MannequinStatic"};
    if(Library.Contains(Name))
    {
        const bool Moving=Name=="Sensor"||Name=="Generator"||Name=="Case"||Name=="Van";
        auto* Root=DistrictAssembly(Name,P,R,Scale,Collision,Moving);
        if(Name=="Van"){ServiceVan=Root;VanPark=P;}
        if(Root)for(USceneComponent* Child:Root->GetAttachChildren())if(auto* Mesh=Cast<UStaticMeshComponent>(Child))return Mesh;
        return nullptr;
    }
    if(Name=="Lamp")return DistrictMesh("Streetlight",P,R,Scale,Collision);
    if(Name=="Crate")return DistrictMesh("Case",P,R,Scale,Collision);
    if(Name=="Fence")
    {
        auto* Root=DistrictAssembly("FenceKit",P,R,Scale,Collision,false,"modular_chainlink_fence_double");
        DistrictAssembly("FenceKit",P+R.RotateVector(FVector(-100,0,0)),R,Scale,Collision,false,"modular_chainlink_fence_post");
        if(Root)for(USceneComponent* Child:Root->GetAttachChildren())if(auto* Mesh=Cast<UStaticMeshComponent>(Child))return Mesh;
        return nullptr;
    }
    const bool User=Name=="Building"||Name=="WindowWall"||Name=="Rover"||Name=="Drone"||Name=="Stretcher";
    const FString Path=FString::Printf(TEXT("/Game/RescueAssets/%s/%s/SM_%s.SM_%s"),
        User?TEXT("User"):TEXT("Authored"),*Name,*Name,*Name);
    auto* Mesh=LoadObject<UStaticMesh>(nullptr,*Path);
    if(!Mesh){UE_LOG(LogTemp,Error,TEXT("MR missing prepared district asset: %s"),*Path);return nullptr;}
    auto* C=NewObject<UStaticMeshComponent>(this);
    AddInstanceComponent(C);C->SetupAttachment(GetRootComponent());C->SetStaticMesh(Mesh);
    C->SetMobility(EComponentMobility::Static);C->SetRelativeLocation(P);C->SetRelativeRotation(R);C->SetRelativeScale3D(Scale);
    C->SetCollisionEnabled(Collision?ECollisionEnabled::QueryAndPhysics:ECollisionEnabled::NoCollision);
    C->SetCollisionProfileName(Collision?TEXT("BlockAll"):TEXT("NoCollision"));
    C->RegisterComponent();Generated.Add(C);return C;
}

void AMRWorld::DistrictLabel(const FString& Text,const FVector& P,float Size)
{
    auto* C=NewObject<UTextRenderComponent>(this);AddInstanceComponent(C);C->SetupAttachment(GetRootComponent());
    C->SetMobility(EComponentMobility::Static);C->SetRelativeLocation(P);C->SetRelativeRotation(FRotator(0,-135,0));
    C->SetText(FText::FromString(Text));C->SetWorldSize(Size);C->SetHorizontalAlignment(EHTA_Center);
    if(Text.StartsWith(TEXT("ELECTRICAL")))C->SetRelativeRotation(FRotator(0,45,0));
    C->SetTextRenderColor(FColor(220,232,215));C->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    C->RegisterComponent();Generated.Add(C);
}

void AMRWorld::BuildDistrict()
{
    FRandomStream Rng(Seed);
    // Continuous physical ground, with a large quiet surround instead of a floating board edge.
    Shape("Cube",FVector(7500,7500,-70),FVector(150000,150000,120),"MI_Ground");
    Shape("Cube",FVector(7500,7500,-15),FVector(22000,22000,28),"MI_Concrete");
    const TArray<float> Streets={1000,5500,10000,14500};
    for(float Center:Streets)
    {
        Shape("Cube",FVector(Center,7500,-2),FVector(850,19000,12),"M_QuarterAsphalt");
        float SegmentStart=-2000;
        for(int32 Segment=0;Segment<=Streets.Num();Segment++)
        {
            const float SegmentEnd=Segment<Streets.Num()?Streets[Segment]-425:17000;
            if(SegmentEnd>SegmentStart)Shape("Cube",FVector((SegmentStart+SegmentEnd)*.5,Center,-2),
                FVector(SegmentEnd-SegmentStart,850,12),"M_QuarterAsphalt");
            if(Segment<Streets.Num())SegmentStart=Streets[Segment]+425;
        }
        for(int32 I=0;I<48;I++)
        {
            const float Along=-1600+I*400;
            bool Intersection=false;for(float Cross:Streets)Intersection|=FMath::Abs(Along-Cross)<650;
            if(!Intersection)
            {
                Shape("Cube",FVector(Center,Along,6),FVector(9,170,2),"MI_Paint",FRotator::ZeroRotator,false);
                Shape("Cube",FVector(Along,Center,7),FVector(170,9,2),"MI_Paint",FRotator::ZeroRotator,false);
            }
        }
    }
    // Urban blocks are filled, while wide real streets remain clear for evacuation.
    for(int32 BX=0;BX<3;BX++)for(int32 BY=0;BY<3;BY++)
    {
        const FVector Middle((Streets[BX]+Streets[BX+1])*.5,(Streets[BY]+Streets[BY+1])*.5,0);
        Shape("Cube",Middle+FVector(0,0,8),FVector(3480,3480,30),"M_QuarterPaving");
        for(int32 Side:{-1,1})
        {
            Shape("Cube",Middle+FVector(Side*1740,0,10),FVector(18,3480,34),"MI_Plaster");
            Shape("Cube",Middle+FVector(0,Side*1740,10),FVector(3480,18,34),"MI_Plaster");
        }
        for(int32 PX:{-1,1})for(int32 PY:{-1,1})
        {
            // Keep one open courtyard with an accessible ruined facade and a utility passage.
            if((BX==1&&BY==0&&PX==1)||(BX==2&&BY==2)||(BX==2&&BY==0))continue;
            // Reserve an open substation yard: its fire and maintenance access
            // must not be buried inside the footprint of a facade kit.
            if(BX==2&&BY==1&&PX==-1&&PY==1)continue;
            FVector Site=Middle+FVector(PX*810,PY*810,24);
            const float S=Rng.FRandRange(.88f,1.1f);
            const float Yaw=((BX+BY+PX+PY+4)%4)*90.f;
            const int32 Style=(BX*7+BY*3+(PX+1)+(PY+1)*2)%5;
            if(Style==0||Style==3)Apartment(Site,Style==0?3:4,BX+BY,false);
            else if(Style==2)Apartment(Site,2+(BX%2),BX+BY,true);
            else DistrictMesh("Building",Site,FRotator(0,Yaw,0),FVector(S,S,Style==1?.72f:1.08f));
            for(int32 K=0;K<(Style==0||Style==3?0:3);K++)
                DistrictMesh("Rubble",Site+FVector(Rng.FRandRange(-650,650),Rng.FRandRange(-600,600),28),
                    FRotator(0,Rng.FRandRange(-180,180),0),FVector(Rng.FRandRange(.8,1.4)));
            for(int32 K=0;K<(Style==0||Style==3?0:5);K++)
            {
                FVector Debris=Site+FVector(Rng.FRandRange(-680,680),Rng.FRandRange(-610,610),34);
                Chunk(Rng,Debris,FVector(Rng.FRandRange(70,170),Rng.FRandRange(55,140),Rng.FRandRange(25,60)),false);
            }
        }
        // Crosswalks terminate on pavements; no tall collision strips across the carriageway.
        for(int32 Stripe=0;Stripe<7;Stripe++)
        {
            Shape("Cube",FVector(Streets[BX]+600,Streets[BY]-290+Stripe*95,8),FVector(250,40,2),"MI_White",FRotator::ZeroRotator,false);
            Shape("Cube",FVector(Streets[BX]-290+Stripe*95,Streets[BY]+600,9),FVector(40,250,2),"MI_White",FRotator::ZeroRotator,false);
        }
    }
    // Ground-floor training courtyard assembled from the supplied reusable wall.
    for(int32 I=0;I<4;I++)
    {
        DistrictMesh("WindowWall",FVector(7600+I*300,2200,24));
        if(I!=1)DistrictMesh("WindowWall",FVector(7600+I*300,3900,24),FRotator(0,180,0));
    }
    DistrictMesh("Doorway",FVector(7900,3900,24));
    for(int32 I=0;I<3;I++)DistrictMesh("Slab",FVector(7900+I*330,2900,24),FRotator(0,I*67,0),FVector(.7));
    DistrictMesh("Beam",FVector(8400,3100,40),FRotator(0,25,0));

    // Medical staging area: real open container, dock lanes, supplied stretchers and equipment.
    Shape("Cube",FVector(-1100,1700,1),FVector(3400,3900,20),"M_QuarterAsphalt");
    Shape("Cube",FVector(-300,-900,-2),FVector(5200,1800,12),"M_QuarterAsphalt");
    for(int32 Lane=0;Lane<4;Lane++)for(int32 Dash=0;Dash<8;Dash++)
    {
        const float StartX=-1100+Lane*330;
        const float X=FMath::Lerp(StartX,800.f,Dash/7.f);
        Shape("Cube",FVector(X,-600-Lane*320,6),FVector(70,6,2),"MI_Paint",FRotator::ZeroRotator,false);
    }
    DistrictMesh("Medical",FVector(-1600,2400,13));
    DistrictMesh("Van",FVector(-650,2100,14),FRotator(0,90,0));
    DistrictMesh("Generator",FVector(-2350,1900,13));
    DistrictMesh("UtilityBox",FVector(-2350,2150,13));
    DistrictMesh("MannequinStatic",FVector(-1870,2010,13),FRotator(0,-90,0),FVector::OneVector,false);
    for(int32 I=0;I<10;I++)DistrictAssembly("CableKit",FVector(-2338,1940+I*20,35),FRotator(90,0,0),
        FVector::OneVector,false,false,"cable_double");
    DistrictLabel("MEDICAL / EVACUATION",FVector(-1320,2040,365),37);
    for(int32 I=0;I<4;I++)
    {
        const FVector Dock=FVector(-1100+I*330,200,12);
        DistrictMesh("Dock",Dock,FRotator(0,90,0));
        for(int32 Side:{-1,1})Shape("Cube",Dock+FVector(Side*145,0,2),FVector(6,360,2),"MI_Paint",FRotator::ZeroRotator,false);
    }
    for(int32 I=0;I<5;I++)DistrictMesh("Crate",FVector(-1800+I*105,1800,13),FRotator(0,Rng.FRandRange(-5,5),0));
    DistrictMesh("Stretcher",FVector(-1000,2800,14),FRotator(0,90,0),FVector::OneVector,false);
    DistrictMesh("Relay",FVector(-2200,1250,13));
    for(int32 I=0;I<3;I++)
    {
        const FVector Pad=Base+FVector(-900,-900+I*450,13);
        Shape("Cylinder",Pad,FVector(210,210,2),"MI_Blue",FRotator::ZeroRotator,false);
        Shape("Cube",Pad+FVector(0,0,2),FVector(90,10,1),"MI_White",FRotator::ZeroRotator,false);
        Shape("Cube",Pad+FVector(0,0,2),FVector(10,90,1),"MI_White",FRotator::ZeroRotator,false);
    }
    DistrictMesh("Sensor",FVector(-650,3600,13),FRotator(0,-30,0));
    for(int32 I=0;I<6;I++)
    {
        const FVector P(-2600,0+I*570,0);
        DistrictMesh("Fence",P,FRotator(0,90,0));
        if(I<5)DistrictMesh("Barrier",P+FVector(160,-180,0),FRotator(0,90,0));
    }
    Shape("Cube",Base+FVector(0,0,8),FVector(650,650,3),"MI_Zone",FRotator::ZeroRotator,false);
    DistrictLabel("RESCUE BASE",Base+FVector(-200,0,80),45);

    // Street-level detail makes scale and access legible without filling every route with clutter.
    for(int32 A=0;A<4;A++)for(int32 I=0;I<5;I++)
    {
        const FVector P(Streets[A]+550,-1200+I*3600,24);
        DistrictMesh("Lamp",P,FRotator(0,180,0));
        auto* Lamp=NewObject<USpotLightComponent>(this);AddInstanceComponent(Lamp);Lamp->SetupAttachment(GetRootComponent());
        Lamp->SetMobility(EComponentMobility::Movable);Lamp->SetRelativeLocation(P+FVector(0,90,490));
        Lamp->SetRelativeRotation(FRotator(-90,0,0));Lamp->SetIntensity(2200);Lamp->SetAttenuationRadius(1150);
        Lamp->SetInnerConeAngle(35);Lamp->SetOuterConeAngle(65);Lamp->SetLightColor(FLinearColor(.88,.94,1));
        Lamp->SetCastShadows(false);Lamp->RegisterComponent();Generated.Add(Lamp);
        if(I%2==0)DistrictMesh("Barrier",P+FVector(90,450,0),FRotator(0,90,0));
    }
    DistrictMesh("Relay",FVector(6000,9700,25));
    for(const FVector P:{FVector(-2200,1250,300),FVector(6000,9700,310)})
    {
        auto* Lamp=NewObject<UPointLightComponent>(this);AddInstanceComponent(Lamp);Lamp->SetupAttachment(GetRootComponent());
        Lamp->SetMobility(EComponentMobility::Movable);Lamp->SetRelativeLocation(P);Lamp->SetIntensity(1800);
        Lamp->SetAttenuationRadius(220);Lamp->SetCastShadows(false);Lamp->ComponentTags.Add(TEXT("RelayStatus"));
        Lamp->RegisterComponent();Generated.Add(Lamp);
    }
    DistrictMesh("Sensor",FVector(10450,5600,25),FRotator(0,110,0));
    for(int32 I=0;I<5;I++)DistrictMesh("Fence",FVector(12100,11200+I*300,24),FRotator(0,90,0));

    // A shallow above-ground retention basin: the rim is physical, depth is explicit.
    const FVector Basin(12850,13000,24);
    Shape("Cube",Basin+FVector(0,0,5),FVector(1900,2200,10),"MI_Dark");
    Shape("Plane",Basin+FVector(0,0,70),FVector(1820,2120,100),"M_QuarterWater",FRotator::ZeroRotator,false);
    for(int32 S:{-1,1})
    {
        Shape("Cube",Basin+FVector(S*950,0,45),FVector(60,2260,90),"MI_Concrete");
        Shape("Cube",Basin+FVector(0,S*1100,45),FVector(1960,60,90),"MI_Concrete");
    }
    Shape("Cube",Basin+FVector(-900,-100,90),FVector(420,1600,35),"MI_Metal");
    DistrictLabel("FLOOD / WATER ZONE",Basin+FVector(0,-1130,150),40);
    DistrictMesh("Sensor",Basin+FVector(-1100,-800,0),FRotator(0,45,0));
    auto* Spill=Shape("Plane",FVector(14500,12800,4),FVector(850,1500,100),"M_QuarterWater",FRotator::ZeroRotator,false);
    Spill->SetMobility(EComponentMobility::Movable);Spill->SetVisibility(false);Spill->ComponentTags.Add(TEXT("FloodSurface"));
    Shape("Cube",FVector(5500,8300,10),FVector(800,1200,5),"M_QuarterMud",FRotator::ZeroRotator,false);
    DistrictLabel("SOFT GROUND / LOW TRACTION",FVector(6000,8300,90),30);
    DistrictLabel("LEAK / DEPTH SENSOR",FVector(14000,11950,100),32);
    // A leaking outlet connects the retention basin to the road spill.
    DistrictAssembly("PipeKit",Basin+FVector(930,-200,50),FRotator(0,90,-90),FVector::OneVector,
        false,false,"modular_industrial_pipes_01_pipe02");
    DistrictMesh("UtilityBox",FVector(10900,7950,24),FRotator(0,90,0));
    for(int32 I=0;I<3;I++)DistrictMesh("Barrier",FVector(10450+I*310,7450,24));
    DistrictLabel("ELECTRICAL FIRE / NO ENTRY",FVector(10900,7450,160),32);

    // Enclosed utility passage under a raised embankment; the clear interior is usable geometry.
    const FVector Tunnel(12400,3050,24);
    DistrictMesh("UtilityTunnel",Tunnel);
    DistrictMesh("Sensor",Tunnel+FVector(-800,-500,0));
    for(int32 I=0;I<6;I++)
    {
        DistrictAssembly("PipeKit",Tunnel+FVector(-490+I*195,260,210),FRotator(90,0,0),
            FVector::OneVector,false,false,"modular_industrial_pipes_01_pipe02");
        DistrictAssembly("CableKit",Tunnel+FVector(-490+I*60,-270,190),FRotator(90,0,0),
            FVector::OneVector,false,false,"cable_straight_long");
    }
    DistrictMesh("Manhole",Tunnel+FVector(-900,400,1));
    // One hydrant variant is assembled around its original datum; the second
    // source variant stays in the editable library rather than overlapping it.
    DistrictAssembly("Hydrant",FVector(10490,5700,24),FRotator::ZeroRotator,FVector::OneVector,true,false,"fire_hydrant");
    DistrictAssembly("Hydrant",FVector(10490,5687,55),FRotator::ZeroRotator,FVector::OneVector,true,false,"fire_hydrant_cap_01");
    DistrictAssembly("Hydrant",FVector(10500,5696,76),FRotator::ZeroRotator,FVector::OneVector,true,false,"fire_hydrant_cap_02");
    DistrictAssembly("Hydrant",FVector(10479,5696,75),FRotator::ZeroRotator,FVector::OneVector,true,false,"fire_hydrant_cap_03");
    DistrictAssembly("Hydrant",FVector(10490,5691,44),FRotator::ZeroRotator,FVector::OneVector,false,false,"fire_hydrant_chain");
    DistrictLabel("UTILITY TUNNEL",Tunnel+FVector(-720,-330,330),38);

    // Local dynamic debris are real Chaos bodies; the surrounding district remains stable.
    const FVector Collapse(7800,5950,0);
    for(int32 I=0;I<18;I++)Chunk(Rng,Collapse+FVector(Rng.FRandRange(-450,450),Rng.FRandRange(-260,260),70+I*27),
        FVector(Rng.FRandRange(90,240),Rng.FRandRange(80,190),Rng.FRandRange(35,85)),true);
    for(int32 I=0;I<5;I++)Chunk(Rng,Collapse+FVector(-400+I*190,40,850+I*65),FVector(300,390,55),true,true);
    DistrictLabel("UNSTABLE STRUCTURE",Collapse+FVector(0,-540,170),40);
    for(int32 I=0;I<4;I++)DistrictMesh("Barrier",Collapse+FVector(-600+I*410,-600,0));
    // Background silhouettes carry the district beyond the playable area.
    for(int32 I=0;I<9;I++)
    {
        DistrictMesh("Building",FVector(-1800+I*2400,18500,0),FRotator(0,180,0),FVector(1.4,1.3,1.4+I%3*.2));
        if(I<7)DistrictMesh("Building",FVector(18500,-1200+I*2600,0),FRotator(0,90,0),FVector(1.25));
    }
    UE_LOG(LogTemp,Display,TEXT("MR_RESCUE_QUARTER_BUILT: %d components, %d dynamic pieces"),Generated.Num(),PhysicsPieces.Num());
}
