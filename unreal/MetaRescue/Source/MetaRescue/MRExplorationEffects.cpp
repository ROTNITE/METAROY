#include "MRExplorationScene.h"
#include "MRSceneClip.h"
#include "MRDisasterFade.h"
#include "MRWorld.h"
#include "MRAgent.h"
#include "NiagaraComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/Crc.h"

namespace
{
FBox OpticalBox(const FMRExplorationGrid& Grid,FIntVector Cell)
{
    const FVector Center=Grid.Center(FIntPoint(Cell.X,Cell.Y))+FVector(0,0,(Cell.Z+.5)*500);
    return FBox(Center-FVector(250),Center+FVector(250));
}
FString SmokeKey(const FString& Source,FIntVector Cell)
{return TEXT("smoke/")+Source+FString::Printf(TEXT("/%d/%d/%d"),Cell.X,Cell.Y,Cell.Z);}
FString FlameSource(const UNiagaraComponent* C)
{return (C->ComponentTags.IsEmpty()?FString():C->ComponentTags[0].ToString())+TEXT("/")+C->GetComponentTransform().ToString();}
FMRSceneMesh SmokeMesh(const FMRSmokeVolume& Volume,FBox Box,FLinearColor Albedo,double At)
{
    FMRSceneMesh Mesh;const auto Bounds=Volume.Bounds();
    Box=FBox(Box.Min.ComponentMax(Bounds.Min),Box.Max.ComponentMin(Bounds.Max));
    if(Box.GetSize().GetMin()<=.01)return Mesh;
    auto* Material=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/ExplorationEffectsR1/M_PhysicalSmoke.M_PhysicalSmoke"));
    auto* Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Material||!Cube)return Mesh;
    auto* Frozen=UMaterialInstanceDynamic::Create(Material,GetTransientPackage());
    Frozen->SetVectorParameterValue(TEXT("SmokeSource"),FLinearColor(Volume.Source));
    Frozen->SetVectorParameterValue(TEXT("SmokeWind"),FLinearColor(Volume.WindCmPerSecond));
    Frozen->SetVectorParameterValue(TEXT("SmokeShape"),FLinearColor(Volume.HeightCm,Volume.RadiusCm,Volume.RadiusGrowth));
    Frozen->SetScalarParameterValue(TEXT("SmokeRise"),Volume.RiseCmPerSecond);
    Frozen->SetScalarParameterValue(TEXT("SmokeExtinction"),Volume.ExtinctionPerCm);
    Frozen->SetScalarParameterValue(TEXT("SmokeStrength"),Volume.Strength);
    Frozen->SetScalarParameterValue(TEXT("SnapshotTime"),At);
    Frozen->SetVectorParameterValue(TEXT("SmokeAlbedo"),Albedo);
    Frozen->SetVectorParameterValue(TEXT("ClipMin"),FLinearColor(Box.Min));Frozen->SetVectorParameterValue(TEXT("ClipMax"),FLinearColor(Box.Max));
    Mesh.Mesh=TStrongObjectPtr<UStaticMesh>(Cube);Mesh.Materials.Emplace(Frozen);Mesh.Shadow=false;Mesh.Bounds=Box;
    Mesh.Pose=FTransform(FQuat::Identity,Box.GetCenter(),Box.GetSize()/100);return Mesh;
}
FMRSceneMesh FlameMesh(const UNiagaraComponent* Source,FBox Box,double At,float Fade)
{
    FMRSceneMesh Mesh;auto* Material=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/ExplorationEffectsR1/M_BuildingFire.M_BuildingFire"));
    if(!Material)return Mesh;
    auto* Frozen=UMaterialInstanceDynamic::Create(Material,GetTransientPackage());
    // A sampled flame frame uses the same licensed atlas. It has no live
    // reference to Niagara or its future activation/expiry/transform.
    Frozen->SetScalarParameterValue(TEXT("FlameLife"),FMath::Frac(At/1.7+double(FCrc::StrCrc32(*FlameSource(Source))%997)/997));
    Frozen->SetScalarParameterValue(TEXT("Fade"),Fade);
    FMRSceneSection Section;Section.Material=TStrongObjectPtr<UMaterialInterface>(Frozen);
    const FVector Origin=Source->GetComponentLocation(),Scale=Source->GetComponentScale();
    const double Width=200*Scale.X,Height=285*Scale.Z;
    for(FVector Across:{FVector(1,0,0),FVector(0,1,0)})
    {
        FProcMeshVertex V[4];const FVector Points[]={Origin-Across*Width*.5,Origin+Across*Width*.5,
            Origin+Across*Width*.5+FVector(0,0,Height),Origin-Across*Width*.5+FVector(0,0,Height)};
        for(int32 I=0;I<4;++I){V[I].Position=Points[I];V[I].Normal=FVector::CrossProduct(Across,FVector::UpVector);V[I].UV0=FVector2D(I==1||I==2?1:0,I<2?1:0);}
        MRSceneClip::Triangle(V[0],V[2],V[1],Box,Section.Geometry,true);
        MRSceneClip::Triangle(V[0],V[3],V[2],Box,Section.Geometry,true);
    }
    if(!Section.Geometry.ProcIndexBuffer.IsEmpty())Mesh.Procedural.Add(MoveTemp(Section));
    Mesh.Bounds=Box;Mesh.Shadow=false;return Mesh;
}
}

void FMRExplorationScene::CaptureEffects(FMRSceneReport& Report,FVector Eye,double Range,
    TFunctionRef<double(FVector,FVector)> Quality)
{
    FCollisionQueryParams Sight(SCENE_QUERY_STAT(MRKnownOpticalEffect),false);
    for(const auto& A:World->Agents)Sight.AddIgnoredActor(A);
    for(const auto& T:World->Targets)if(T.Visual)Sight.AddIgnoredActor(T.Visual);
    auto CanSee=[&](FVector P)
    {
        if(FVector::DistSquared(Eye,P)>Range*Range||Quality(Eye,P)<.15)return false;
        FHitResult Hit;return !World->GetWorld()->LineTraceSingleByChannel(Hit,Eye,P,ECC_Visibility,Sight)||Hit.Distance+12>=FVector::Dist(Eye,P);
    };
    const auto& Volumes=World->Environment.SmokeVolumes();TMap<FString,const FMRSmokeVolume*> ByEvent;
    for(const auto& Volume:Volumes)ByEvent.Add(Volume.EventId,&Volume);
    auto Color=[&](const FMRSmokeVolume& V)
    {
        const auto* E=World->Environment.FindApplied(V.EventId);
        return E&&E->Kind==EMRDisturbance::LowVisibility?FLinearColor(.55f,.58f,.6f):
            E&&E->Kind==EMRDisturbance::Collapse?FLinearColor(.25f,.21f,.16f):FLinearColor(.045f,.048f,.05f);
    };
    TSet<FString> Captured;
    // Revisit remembered optical samples too. A smoke expiry is visible only
    // after the now-clear space has actually been observed and delivered.
    TArray<FString> Keys;Effects.GetKeys(Keys);Keys.Sort();
    const uint32 Turn=FCrc::StrCrc32(*Report.Source)+uint32(Report.ObservedAt*2)*13;
    for(int32 I=0;I<Keys.Num()&&Report.Effects.Num()<32;++I)
    {
        const auto& Previous=Effects.FindChecked(Keys[(Turn+I)%Keys.Num()]);
        if(!Previous.Smoke||!CanSee(Previous.Focus))continue;
        FMRSceneEffect Now=Previous;Now.ObservedAt=Report.ObservedAt;Now.Visual=FMRSceneMesh();
        const auto* V=ByEvent.FindRef(Previous.Source);
        if(V&&V->Extinction(Previous.Focus)>1.e-6)Now.Visual=SmokeMesh(*V,OpticalBox(Grid,Now.Cell),Color(*V),Report.ObservedAt);
        Report.Effects.Add(MoveTemp(Now));Captured.Add(Previous.Id);
    }
    int32 Added=0;
    for(const auto& Volume:Volumes)
    {
        const auto A=Grid.CellAt(Volume.Bounds().Min),B=Grid.CellAt(Volume.Bounds().Max);
        TArray<FIntPoint> Candidates;
        for(int32 Y=FMath::Max(0,A.Y);Y<=FMath::Min(Grid.GetHeight()-1,B.Y);++Y)
            for(int32 X=FMath::Max(0,A.X);X<=FMath::Min(Grid.GetWidth()-1,B.X);++X)
                if(FVector::DistSquared2D(Eye,Grid.Center(FIntPoint(X,Y)))<=Range*Range)Candidates.Add(FIntPoint(X,Y));
        for(int32 I=0;I<Candidates.Num()&&Added<32;++I)
        {
            const auto Cell=Candidates[(Turn+I)%Candidates.Num()];const auto Center=Grid.Center(Cell);
            for(double Z:{FMath::Clamp(Eye.Z,Volume.Source.Z+60,Volume.Source.Z+Volume.HeightCm-60),
                Volume.Source.Z+Volume.HeightCm*.35,Volume.Source.Z+Volume.HeightCm*.7})
            {
                FVector P(Center.X,Center.Y,Z);if(Volume.Extinction(P)<1.e-6)continue;
                if(Quality(Eye,P)<.2)
                {
                    double Low=0,High=1;
                    for(int32 K=0;K<12;++K){const double Mid=(Low+High)*.5;if(Quality(Eye,FMath::Lerp(Eye,P,Mid))>=.2)Low=Mid;else High=Mid;}
                    P=FMath::Lerp(Eye,P,Low);
                }
                const auto XY=Grid.CellAt(P);if(!Grid.IsValid(XY)||Volume.Extinction(P)<1.e-6||!CanSee(P))continue;
                const FIntVector Optical(XY.X,XY.Y,FMath::FloorToInt(P.Z/500));const FString Key=SmokeKey(Volume.EventId,Optical);
                if(Captured.Contains(Key))continue;
                FMRSceneEffect Effect;Effect.Id=Key;Effect.Source=Volume.EventId;Effect.Cell=Optical;Effect.Focus=P;
                Effect.Smoke=true;Effect.ObservedAt=Report.ObservedAt;
                Effect.Visual=SmokeMesh(Volume,OpticalBox(Grid,Optical),Color(Volume),Report.ObservedAt);
                Report.Effects.Add(MoveTemp(Effect));Captured.Add(Key);++Added;break;
            }
        }
    }
    TArray<UNiagaraComponent*> Emitters;World->GetComponents(Emitters);TMap<FString,UNiagaraComponent*> ActiveFire;
    for(auto* Emitter:Emitters)if(Emitter->ComponentHasTag(TEXT("BuildingFire"))&&Emitter->IsVisible()&&!Emitter->bHiddenInGame&&Emitter->IsActive())
        ActiveFire.Add(FlameSource(Emitter),Emitter);
    for(const auto& Pair:ActiveFire)
    {
        auto* Emitter=Pair.Value;const FVector Origin=Emitter->GetComponentLocation();
        const FVector Focus=Origin+FVector(0,0,120*Emitter->GetComponentScale().Z);if(!CanSee(Focus))continue;
        float Fade=1,Delay=0;
        for(const auto& Tag:Emitter->ComponentTags)
        {const auto Text=Tag.ToString();if(Text.StartsWith(TEXT("IgnitionDelay_")))Delay=FCString::Atof(*Text.RightChop(14));}
        if(!Emitter->ComponentTags.IsEmpty())if(const auto* Event=World->Environment.FindApplied(Emitter->ComponentTags[0].ToString()))
        {
            const double End=World->Environment.ActiveUntil(*Event);
            Fade=MRDisasterFade(float(Report.ObservedAt-Event->Time),Delay,End>=0?float(End-Event->Time):0.f);
        }
        const FVector Scale=Emitter->GetComponentScale();
        const FBox Bounds(Origin-FVector(100*Scale.X,100*Scale.X,0),Origin+FVector(100*Scale.X,100*Scale.X,285*Scale.Z));
        const auto Low=Grid.CellAt(Bounds.Min),High=Grid.CellAt(Bounds.Max);
        for(int32 Y=Low.Y;Y<=High.Y;++Y)for(int32 X=Low.X;X<=High.X;++X)
            for(int32 Z=FMath::FloorToInt(Bounds.Min.Z/500);Z<=FMath::FloorToInt(Bounds.Max.Z/500);++Z)
            {
                if(!Grid.IsValid(FIntPoint(X,Y)))continue;
                const FIntVector Cell(X,Y,Z);const FString Id=TEXT("fire/")+Pair.Key+Cell.ToString();
                FMRSceneEffect Effect;Effect.Id=Id;Effect.Source=Pair.Key;Effect.Cell=Cell;Effect.Focus=Focus;Effect.ObservedAt=Report.ObservedAt;
                Effect.Visual=FlameMesh(Emitter,OpticalBox(Grid,Cell),Report.ObservedAt,Fade);
                Report.Effects.Add(MoveTemp(Effect));Captured.Add(Id);
            }
    }
    for(const auto& Pair:Effects)if(!Pair.Value.Smoke&&!Captured.Contains(Pair.Key)&&!ActiveFire.Contains(Pair.Value.Source)&&CanSee(Pair.Value.Focus))
    {auto Empty=Pair.Value;Empty.Visual=FMRSceneMesh();Empty.ObservedAt=Report.ObservedAt;Report.Effects.Add(MoveTemp(Empty));}
}
