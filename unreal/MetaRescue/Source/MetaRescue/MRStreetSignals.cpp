#include "MRWorld.h"
#include "MRCoreRuntime.h"
#include "MRCoreJson.h"
#include "MRCoreValues.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/World.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "Misc/Crc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

using namespace MRCoreValues;

namespace
{
FIntPoint StreetBin(FVector P)
{
    return FIntPoint(FMath::FloorToInt(P.X/3200),FMath::FloorToInt(P.Y/3200));
}
}

void AMRWorld::BuildStreetSignals(const FBox& BaseBox,const TMap<FString,FVector>& Positions)
{
    struct FLink {FVector Outward;double Width,Length;};
    TMap<FString,TArray<FLink>> Links;
    const auto Graph=CityLayout->GetObjectField(TEXT("road_graph"));
    for(const auto& Value:Graph->GetArrayField(TEXT("edges")))
    {
        const auto Edge=Value->AsObject();
        if(Text(Edge,TEXT("street_class"))==TEXT("access"))continue;
        const FString A=Text(Edge,TEXT("from")),B=Text(Edge,TEXT("to"));
        const FVector Delta=Positions.FindChecked(B)-Positions.FindChecked(A);
        const double Width=Edge->GetNumberField(TEXT("width_m"))*100;
        Links.FindOrAdd(A).Add({Delta.GetSafeNormal2D(),Width,Delta.Size2D()});
        Links.FindOrAdd(B).Add({-Delta.GetSafeNormal2D(),Width,Delta.Size2D()});
    }
    StreetSignalOff=LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/RescueAssets/LibraryV3/TrafficSignalV1/Materials/M_TrafficSignalV1_RedOff.M_TrafficSignalV1_RedOff"));
    TArray<FBox> Occupied;
    const auto AddBounds=[&](const TSharedPtr<FJsonObject>& Box)
    {
        FVector Low,High;
        if(MRCoreJson::Point(Field(Box,TEXT("min")),Low)&&MRCoreJson::Point(Field(Box,TEXT("max")),High))
            Occupied.Add(FBox(Low*100,High*100));
    };
    for(const TCHAR* Key:{TEXT("buildings"),TEXT("facilities"),TEXT("fixed_obstacles")})
        for(const auto& Item:CityLayout->GetArrayField(Key))
            AddBounds(Item->AsObject()->GetObjectField(TEXT("bounds")));
    for(const auto& Feature:CityLayout->GetArrayField(TEXT("features")))
        for(const auto& Box:Feature->AsObject()->GetArrayField(TEXT("obstacles")))AddBounds(Box->AsObject());
    for(const auto& Infra:CityLayout->GetArrayField(TEXT("infrastructure")))
    {
        FVector Position;
        if(MRCoreJson::Point(Field(Infra->AsObject(),TEXT("position")),Position))
            Occupied.Emplace(Position*100-FVector(85),Position*100+FVector(85));
    }
    TSet<FIntPoint> Served;
    for(const auto& Value:Graph->GetArrayField(TEXT("nodes")))
    {
        const FString Id=Text(Value->AsObject(),TEXT("id"));
        const auto* Arms=Links.Find(Id);
        if(!Arms||Arms->Num()<3)continue;
        const FVector Center=Positions.FindChecked(Id);
        if(BaseBox.ExpandBy(1200.).IsInsideXY(Center))continue;
        const FIntPoint Block(FMath::FloorToInt(Center.X/8800),FMath::FloorToInt(Center.Y/8800));
        if(Served.Contains(Block))continue;
        double Width=0;
        for(const auto& Arm:*Arms)Width=FMath::Max(Width,Arm.Width);
        const double Crossing=Width*.5+220,Stop=Crossing+240;
        if(Arms->ContainsByPredicate([&](const auto& Arm){return Arm.Length<2*(Stop+240);}))continue;
        const double Size=CityLayout->GetNumberField(TEXT("size_m"))*100;
        if(Center.X<Stop+150||Center.Y<Stop+150||Center.X>Size-Stop-150||Center.Y>Size-Stop-150)continue;
        TArray<FVector> Poles;
        for(const auto& Arm:*Arms)
        {
            const FVector Right(Arm.Outward.Y,-Arm.Outward.X,0);
            bool Placed=false;
            for(double Extra:{105.,175.,25.})
            {
                FVector Pole=Center+Arm.Outward*(Crossing+Extra)+Right*(Arm.Width*.5+175);
                const FBox Envelope(Pole-FVector(45,45,0),Pole+FVector(45,45,350));
                if(Occupied.ContainsByPredicate([&](const FBox& Box){return Box.IntersectXY(Envelope);}))continue;
                Pole.Z=InitialGroundHeightCm(Pole);
                Poles.Add(Pole);Placed=true;break;
            }
            if(!Placed)break;
        }
        if(Poles.Num()!=Arms->Num())continue;
        Served.Add(Block);
        const FVector Main=Arms->FindByPredicate([&](const auto& Arm){return Arm.Width==Width;})->Outward;
        for(int32 ArmIndex=0;ArmIndex<Arms->Num();++ArmIndex)
        {
            const auto& Arm=(*Arms)[ArmIndex];
            const FVector Right(Arm.Outward.Y,-Arm.Outward.X,0);
            const FVector Cross=Center+Arm.Outward*Crossing;
            const FVector Line=Center+Arm.Outward*Stop;
            const FVector Pole=Poles[ArmIndex];
            auto& Signal=StreetSignals.AddDefaulted_GetRef();
            Signal.Center=Center;Signal.Outward=Arm.Outward;Signal.PowerPoint=Pole;
            Signal.LaneWidthCm=Arm.Width*.5;Signal.StopDistanceCm=Stop;
            Signal.Group=FMath::Abs(FVector::DotProduct(Main,Arm.Outward))>.707?0:1;
            Signal.Offset=FCrc::StrCrc32(*Id)%28;
            Signal.Id=FName(*(TEXT("junction/")+Id));
            if(FParse::Param(FCommandLine::Get(),TEXT("MRStreetSignalAudit")))
                UE_LOG(LogTemp,Display,TEXT("MR_STREET_APPROACH %s arm=%d center=(%.3f,%.3f) direction=(%.7f,%.7f) stop=%.3f group=%d offset=%d power=(%.3f,%.3f)"),
                    *Signal.Id.ToString(),ArmIndex,Center.X,Center.Y,Arm.Outward.X,Arm.Outward.Y,Stop,Signal.Group,Signal.Offset,Pole.X,Pole.Y);
            const FVector Region(1150,1150,0);
            const auto Lo=StreetBin(Line-Region),Hi=StreetBin(Line+Region);
            for(int32 X=Lo.X;X<=Hi.X;++X)for(int32 Y=Lo.Y;Y<=Hi.Y;++Y)
                StreetSignalBins.FindOrAdd(FIntPoint(X,Y)).Add(StreetSignals.Num()-1);
            if(!FApp::CanEverRender())continue;
            const FRotator Turn=Arm.Outward.Rotation();
            for(double At=-Arm.Width*.5+45;At<Arm.Width*.5-15;At+=68)
            {
                FVector Stripe=Cross+Right*At;Stripe.Z=InitialGroundHeightCm(Stripe)+2.1;
                auto* Mark=Shape(TEXT("Cube"),Stripe,FVector(158,36,.3),TEXT("MI_Paint"),Turn,false);
                Mark->ComponentTags.Add(TEXT("CityStreetMarking"));
            }
            FVector StopLine=Line+Right*(Arm.Width*.25);StopLine.Z=InitialGroundHeightCm(StopLine)+2.3;
            Shape(TEXT("Cube"),StopLine,FVector(32,Arm.Width*.5-18,.3),TEXT("MI_Paint"),Turn,false)->ComponentTags.Add(TEXT("CityStreetMarking"));
            if(auto* Parts=DistrictAssembly(TEXT("TrafficSignalV1"),Pole,FRotator(0,Turn.Yaw-90,0),FVector::OneVector,false))
                for(USceneComponent* Child:Parts->GetAttachChildren())if(auto* Mesh=Cast<UStaticMeshComponent>(Child))
                {
                    Mesh->ComponentTags.Add(TEXT("CityTrafficSignal"));
                    Mesh->ComponentTags.Remove(TEXT("CityStaticPending"));
                    Mesh->SetMobility(EComponentMobility::Movable);
                    for(int32 Slot=0;Slot<Mesh->GetNumMaterials();++Slot)
                    {
                        auto* Material=Mesh->GetMaterial(Slot);
                        const FString Name=GetNameSafe(Material);
                        if(Name.EndsWith(TEXT("_Red"))||Name.EndsWith(TEXT("_Amber"))||Name.EndsWith(TEXT("_Green")))
                        {
                            Signal.Lenses.Add({Mesh,Material,Slot});
                            if(StreetSignalOff)Mesh->SetMaterial(Slot,StreetSignalOff.Get());
                        }
                    }
                }
        }
    }
    UE_LOG(LogTemp,Display,TEXT("MR_STREET_SIGNALS junctions=%d controlled_approaches=%d"),Served.Num(),StreetSignals.Num());
}

EMRStreetPhase AMRWorld::StreetPhase(const FMRStreetArm& Signal) const
{
    const bool Powered=WaterDepth(Signal.PowerPoint)<15;
    const double At=CoreRuntime?CoreRuntime->Time():GetWorld()->GetTimeSeconds();
    return MRStreetPhase(At,Signal.Offset,Signal.Group,Powered);
}

void AMRWorld::TickStreetSignals()
{
    const bool Render=FApp::CanEverRender()&&StreetSignalOff;
    static const bool Audit=FParse::Param(FCommandLine::Get(),TEXT("MRStreetSignalAudit"));
    if(!Render&&!Audit)return;
    for(int32 Index=0;Index<StreetSignals.Num();++Index)
    {
        auto& Signal=StreetSignals[Index];
        const auto Phase=StreetPhase(Signal);
        if(Phase==Signal.LastPhase)continue;
        Signal.LastPhase=Phase;
        if(Audit)
            UE_LOG(LogTemp,Display,TEXT("MR_STREET_PHASE %s i=%d time=%.3f group=%d phase=%d depth_cm=%.2f"),
                *Signal.Id.ToString(),Index,CoreRuntime?CoreRuntime->Time():GetWorld()->GetTimeSeconds(),
                Signal.Group,int32(Phase),WaterDepth(Signal.PowerPoint));
        if(!Render)continue;
        const FString Suffix=Phase==EMRStreetPhase::Red?TEXT("_Red"):
            Phase==EMRStreetPhase::Amber?TEXT("_Amber"):Phase==EMRStreetPhase::Green?TEXT("_Green"):TEXT("");
        for(const auto& Lens:Signal.Lenses)if(auto* Mesh=Lens.Mesh.Get())
        {
            auto* Material=Lens.Lit.Get();
            Mesh->SetMaterial(Lens.Slot,!Suffix.IsEmpty()&&GetNameSafe(Material).EndsWith(Suffix)?Material:StreetSignalOff.Get());
        }
    }
}

double AMRWorld::StreetSpeedLimit(FVector Position,FVector Direction,double LookAhead,FName& Junction) const
{
    const auto* Candidates=StreetSignalBins.Find(StreetBin(Position));
    if(!Candidates)return MAX_dbl;
    for(int32 Index:*Candidates)
    {
        const auto& Arm=StreetSignals[Index];
        const double Heading=-FVector::DotProduct(Direction,Arm.Outward);
        if(Heading<.63)continue;
        const FVector Offset=Position-Arm.Center;
        const double Along=FVector::DotProduct(Offset,Arm.Outward);
        const double Across=FVector::DotProduct(Offset,FVector(Arm.Outward.Y,-Arm.Outward.X,0));
        const double ToLine=Along-Arm.StopDistanceCm;
        if(ToLine<35||ToLine>LookAhead+180||Across<-.32*Arm.LaneWidthCm||Across>Arm.LaneWidthCm+55)continue;
        const auto Phase=StreetPhase(Arm);
        if(Phase==EMRStreetPhase::Green)continue;
        Junction=Arm.Id;
        if(Phase==EMRStreetPhase::Dark)return 80.;
        return ToLine<=170?0.:FMath::Sqrt((ToLine-170)*350.);
    }
    return MAX_dbl;
}
