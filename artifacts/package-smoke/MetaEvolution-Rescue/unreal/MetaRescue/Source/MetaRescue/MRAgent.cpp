#include "MRAgent.h"
#include "MRWorld.h"
#include "Components/BoxComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInterface.h"
#include "Dom/JsonObject.h"

AMRAgent::AMRAgent()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PrePhysics;
    Body = CreateDefaultSubobject<UBoxComponent>(TEXT("PhysicsBody"));
    SetRootComponent(Body);
    Body->SetBoxExtent(FVector(95,66,43));
    Body->SetCollisionProfileName(TEXT("PhysicsActor"));
    Body->SetLinearDamping(.25f);
    Body->SetAngularDamping(4.f);
}

UStaticMeshComponent* AMRAgent::Part(const FString& Mesh, FVector Position, FVector Size,
    const FString& Material, FRotator Rotation)
{
    auto* C = NewObject<UStaticMeshComponent>(this);
    AddInstanceComponent(C); C->SetupAttachment(Body);
    C->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, *(TEXT("/Engine/BasicShapes/")+Mesh+TEXT(".")+Mesh)));
    C->SetMaterial(0, LoadObject<UMaterialInterface>(nullptr, *(TEXT("/Game/Materials/")+Material+TEXT(".")+Material)));
    C->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    C->SetRelativeLocation(Position); C->SetRelativeRotation(Rotation);
    C->SetRelativeScale3D(Size/100.f); C->RegisterComponent();
    Parts.Add(C); return C;
}

void AMRAgent::Initialize(AMRWorld* InWorld, int32 InId, bool Aerial, const TArray<FVector>& InitialPath)
{
    Mission=InWorld; AgentId=InId; bAerial=Aerial; Dock=GetActorLocation(); Previous=Dock;
    Body->SetBoxExtent(Aerial?FVector(38,38,18):FVector(95,66,43));
    Body->SetMassOverrideInKg(NAME_None,Aerial?12.f:240.f);
    Body->SetEnableGravity(!Aerial);
    if(Aerial)
    {
        Energy=120;MaxSpeed=1800;
        Part("Sphere",FVector::ZeroVector,FVector(85,62,28),"MI_Blue");
        Part("Cube",FVector(12,0,-24),FVector(25,25,25),"MI_Dark");
        for(int32 X:{-1,1}) for(int32 Y:{-1,1})
        {
            Part("Cube",FVector(X*33,Y*33,0),FVector(100,8,7),"MI_Metal",FRotator(0,X*Y*45,0));
            Part("Cylinder",FVector(X*65,Y*65,4),FVector(21,21,15),"MI_Dark");
            Part("Cylinder",FVector(X*65,Y*65,13),FVector(67,67,1.5),"MI_Rotor");
            Part("Sphere",FVector(X*65,Y*65,-4),FVector(10,10,7),"MI_CyanGlow");
        }
        Part("Sphere",FVector(35,0,-26),FVector(16,16,16),"MI_Glass");
    }
    else
    {
        Energy=Mission->Scenario->GetNumberField(TEXT("ground_battery_Wh"));
        Part("Cube",FVector(0,0,-6),FVector(180,128,62),"MI_Dark");
        Part("Cube",FVector(0,0,22),FVector(174,106,42),"MI_Orange");
        Part("Cube",FVector(-5,0,50),FVector(125,82,12),"MI_White");
        Part("Cube",FVector(-5,0,59),FVector(105,61,9),"MI_Teal");
        for(int32 Side:{-1,1}) for(int32 Wheel=0;Wheel<5;Wheel++)
        {
            Part("Cylinder",FVector(-69+Wheel*34,Side*65,-10),FVector(49,49,15),"MI_Rubber",FRotator(0,0,90));
            Part("Cylinder",FVector(-69+Wheel*34,Side*74,-10),FVector(26,26,3),"MI_Metal",FRotator(0,0,90));
        }
        Part("Cylinder",FVector(-63,0,83),FVector(8,8,63),"MI_Metal");
        Part("Cube",FVector(-63,0,120),FVector(33,33,21),"MI_Dark");
        Part("Sphere",FVector(-49,0,120),FVector(7,21,9),"MI_CyanGlow");
        for(int32 Y:{-1,1})
        {
            Part("Cube",FVector(89,Y*34,25),FVector(4,29,10),"MI_WhiteGlow");
            Part("Cube",FVector(0,Y*46,65),FVector(139,5,5),"MI_Metal");
        }
    }
    Body->SetSimulatePhysics(true);
    SetWaypoints(InitialPath);
}

void AMRAgent::SetWaypoints(const TArray<FVector>& Waypoints)
{
    Path=Waypoints;PathIndex=0;
    if(bAerial) State=Path.IsEmpty()?"idle":"surveying";
}

void AMRAgent::StartRescue(int32 Index)
{
    TargetIndex=Index;State="outbound";
    auto& T=Mission->Targets[Index];T.bAssigned=true;
    TArray<FVector> Points=T.Path;
    const float Offset=(AgentId-1.5f)*150.f;
    for(int32 I=0;I<Points.Num()-1;I++) Points[I]+=FVector(Offset,Offset,0);
    for(auto& P:Points) P.Z=48;
    SetWaypoints(Points);
    Mission->AppendEvent(TEXT("demo_rescue_assigned"),T.Id);
}

void AMRAgent::Arrived()
{
    if(bAerial){State="idle";return;}
    if(State=="outbound"){State="loading";ServiceRemaining=5;}
    else if(State=="inbound"){State="unloading";ServiceRemaining=3;}
}

void AMRAgent::Tick(float Dt)
{
    Super::Tick(Dt);
    if(!Mission||!Body->IsSimulatingPhysics())return;
    const FVector Current=GetActorLocation();
    const float Travel=FVector::Dist(Current,Previous);Previous=Current;
    DistanceTravelled+=Travel;
    Energy=FMath::Max(0.f,Energy-Travel/1000.f*(bAerial?.69f:State=="inbound"?1.18f:.82f));
    if(State=="loading"||State=="unloading")
    {
        ServiceRemaining-=Dt;
        if(ServiceRemaining<=0&&TargetIndex>=0)
        {
            auto& T=Mission->Targets[TargetIndex];
            if(State=="loading")
            {
                if(T.Visual){T.Visual->AttachToActor(this,FAttachmentTransformRules::KeepWorldTransform);T.Visual->SetActorRelativeLocation(FVector(0,0,87));}
                State="inbound";TArray<FVector> Return;
                for(int32 I=T.Path.Num()-2;I>=0;I--){FVector P=T.Path[I];P.Z=48;P+=FVector((AgentId-1.5f)*150,(AgentId-1.5f)*150,0);Return.Add(P);}
                Return.Add(Dock);SetWaypoints(Return);Mission->AppendEvent(TEXT("person_loaded"),T.Id);
            }
            else
            {
                T.bRescued=true;if(T.Visual)T.Visual->SetActorHiddenInGame(true);
                Mission->AppendEvent(TEXT("person_delivered"),T.Id);TargetIndex=-1;State="idle";
            }
        }
    }
    FVector Desired=FVector::ZeroVector;
    if(PathIndex<Path.Num()&&Energy>0&&State!="loading"&&State!="unloading")
    {
        FVector Delta=Path[PathIndex]-Current;
        if(!bAerial)Delta.Z=0;
        if(Delta.Size()<(bAerial?180:95))
        {
            PathIndex++;
            if(PathIndex==Path.Num())Arrived();
        }
        // A contact box has static friction. Do not let the proportional speed
        // taper below breakaway traction before entering the arrival radius.
        else Desired=Delta.GetSafeNormal()*(bAerial?FMath::Min(MaxSpeed,Delta.Size()*2):FMath::Clamp(Delta.Size()*2,300.,double(MaxSpeed)));
    }
    FVector V=Body->GetPhysicsLinearVelocity();
    FVector Accel=(Desired-V)*3;
    if(!bAerial)Accel.Z=0;
    Accel=Accel.GetClampedToMaxSize(bAerial?3800:2400);
    Body->AddForce(Accel,NAME_None,true);
    const FVector Angular=Body->GetPhysicsAngularVelocityInRadians();
    FVector Torque=FVector::CrossProduct(GetActorUpVector(),FVector::UpVector)*50-Angular*6;
    if(Desired.Size2D()>20)
        Torque.Z+=FMath::DegreesToRadians(FMath::FindDeltaAngleDegrees(GetActorRotation().Yaw,Desired.Rotation().Yaw))*8;
    Body->AddTorqueInRadians(Torque,NAME_None,true);
}
