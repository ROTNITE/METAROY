#include "MRWorld.h"
#include "MRAgent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Engine/World.h"
#include "NiagaraComponent.h"
#include "NiagaraFunctionLibrary.h"
#include "NiagaraSystem.h"

float AMRWorld::WaterDepth(const FVector& P) const
{
    if(!bRescueQuarter)return 0;
    if(FMath::Abs(P.X-12850)<900&&FMath::Abs(P.Y-13000)<1050)return 60;
    if(bFloodActive&&FMath::Abs(P.X-14500)<430&&FMath::Abs(P.Y-12800)<750)
        return FMath::Min(42.f,(GetWorld()->GetTimeSeconds()-50.f)*.8f);
    return 0;
}

float AMRWorld::GroundSpeedFactor(const FVector& P) const
{
    if(!bRescueQuarter)return 1;
    const float Mud=FMath::Abs(P.X-5500)<410&&FMath::Abs(P.Y-8300)<600?.46f:1.f;
    return FMath::Min(Mud,WaterDepth(P)>4?.5f:1.f);
}

bool AMRWorld::IsUnsafeGround(const FVector& P) const
{
    return bRescueQuarter&&(WaterDepth(P)>25||FVector::Dist2D(P,FVector(10900,7950,0))<440);
}

bool AMRWorld::HasRadio(const FVector& P) const
{
    return !bRescueQuarter||FVector::Dist2D(P,Base)<7600||
        (bRelayOnline&&FVector::Dist2D(P,FVector(6000,9700,0))<8800);
}

float AMRWorld::SensorRange(const FVector& P) const
{
    if(!bRescueQuarter)return 3600;
    return bDustActive&&FVector::Dist2D(P,FVector(7800,5950,0))<2100?1400:3600;
}

int32 AMRWorld::PendingReports() const
{
    int32 Count=0;for(const auto& T:Targets)Count+=T.ObservedAt>=0&&!T.bDiscovered;return Count;
}

void AMRWorld::TickDistrict(float Dt)
{
    const float Time=GetWorld()->GetTimeSeconds();
    // Explicit validation mode only: physical vehicles cross soft ground and
    // approach deep water. They are excluded from the mission dispatcher.
    if(bMechanicsProbe&&!bProbeSpawned&&Time>=90)
    {
        bProbeSpawned=true;
        const FVector Starts[]={FVector(14500,11400,60),FVector(5500,7000,60)};
        const FVector Ends[]={FVector(14500,14500,48),FVector(5500,9400,48)};
        for(int32 I=0;I<2;I++)
        {
            auto* Probe=GetWorld()->SpawnActor<AMRAgent>(Starts[I],FRotator(0,90,0));
            Probe->Initialize(this,100+I,false,TArray<FVector>{Ends[I]});MechanicsProbes.Add(Probe);
        }
        AppendEvent(TEXT("mechanics_probes_started"));
    }
    const bool Online=Time<35||Time>=65;
    if(Online!=bRelayOnline){bRelayOnline=Online;AppendEvent(Online?TEXT("relay_restored"):TEXT("relay_link_lost"));}
    if(!bFloodActive&&Time>=50)
    {
        bFloodActive=true;AppendEvent(TEXT("water_leak_started"));
        if(auto* Leak=LoadObject<UNiagaraSystem>(nullptr,TEXT("/Game/Effects/NS_PipeLeak.NS_PipeLeak")))
            LeakEffect=UNiagaraFunctionLibrary::SpawnSystemAtLocation(this,Leak,FVector(13800,12800,90),FRotator(0,0,-65),FVector(.35f),false);
    }
    bDustActive=bCollapseReleased&&Time<42;
    if(DustEffect&&!bDustActive)DustEffect->Deactivate();
    const float DoorAlpha=FMath::SmoothStep(9.f,13.f,Time);
    if(ServiceVan)ServiceVan->SetRelativeLocation(VanPark+FVector(0,650*(1-FMath::SmoothStep(0.f,8.f,Time)),0));
    for(auto& Part:DistrictMechanisms)
    {
        if(!Part.Mesh)continue;
        if(Part.Role==TEXT("sensor_head"))
            Part.Mesh->SetRelativeRotation(FRotator(0,FMath::Sin(Time*.45f+Part.Rest.GetLocation().X*.001f)*50,0));
        else if(Part.Role==TEXT("case_lid"))
            Part.Mesh->SetRelativeRotation(Part.Rest.GetRotation()*FQuat(FVector::ForwardVector,
                FMath::DegreesToRadians(100*(FMath::SmoothStep(12.f,15.f,Time)-FMath::SmoothStep(75.f,78.f,Time)))));
        else if(Part.Role==TEXT("van_door"))
            Part.Mesh->SetRelativeRotation(Part.Rest.GetRotation()*FQuat(FVector::UpVector,
                FMath::DegreesToRadians(-Part.Side*88*DoorAlpha)));
        else if(Part.Role==TEXT("wheel"))
        {
            const float A=FMath::Clamp(Time/8,0.f,1.f);
            const float Travel=650*(A*A*(3-2*A));
            Part.Mesh->SetRelativeRotation(Part.Rest.GetRotation()*FQuat(FVector(0,1,0),-Travel/FMath::Max(1.f,Part.Radius)));
        }
        else if(Part.Role==TEXT("beacon"))
        {
            if(auto* Mat=Cast<UMaterialInstanceDynamic>(Part.Mesh->GetMaterial(0)))
                Mat->SetScalarParameterValue(TEXT("Intensity"),FMath::Fmod(Time*2+Part.Side*.25f,1.f)<.18f?20:1);
        }
        else if(Part.Role==TEXT("generator_engine"))
            Part.Mesh->SetRelativeLocation(Part.Rest.GetLocation()+FVector(0,0,FMath::Sin(Time*47)*.06f));
    }
    TArray<UPointLightComponent*> Lamps;GetComponents(Lamps);
    for(auto* Lamp:Lamps)if(Lamp->ComponentHasTag(TEXT("RelayStatus")))
        Lamp->SetLightColor(bRelayOnline?FLinearColor(.12,1,.3):FLinearColor(1,.18,.02));
    TArray<UStaticMeshComponent*> Meshes;GetComponents(Meshes);
    for(auto* Mesh:Meshes)if(Mesh->ComponentHasTag(TEXT("FloodSurface")))
    {
        Mesh->SetVisibility(bFloodActive);
        FVector P=Mesh->GetRelativeLocation();P.Z=4+WaterDepth(FVector(14500,12800,0));Mesh->SetRelativeLocation(P);
    }
}
