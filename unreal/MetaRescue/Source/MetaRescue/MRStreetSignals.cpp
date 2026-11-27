#include "MRWorld.h"
#include "MRCoreRuntime.h"
#include "MRCoreJson.h"
#include "MRCoreValues.h"
#include "MRCityAssets.h"
#include "ProceduralMeshComponent.h"
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
    const bool Revised=Environment.bRevisedDisasters;
    TMap<FIntPoint,TArray<int32>> OccupiedBins;
    const auto IndexBounds=[&](int32 Index)
    {
        const auto Low=StreetBin(Occupied[Index].Min),High=StreetBin(Occupied[Index].Max);
        for(int32 X=Low.X;X<=High.X;++X)for(int32 Y=Low.Y;Y<=High.Y;++Y)
            OccupiedBins.FindOrAdd(FIntPoint(X,Y)).Add(Index);
    };
    if(Revised)for(int32 I=0;I<Occupied.Num();++I)IndexBounds(I);
    const auto Obstructed=[&](const FBox& Envelope)
    {
        if(!Revised)return Occupied.ContainsByPredicate([&](const FBox& Box){return Box.IntersectXY(Envelope);});
        const auto Low=StreetBin(Envelope.Min),High=StreetBin(Envelope.Max);
        for(int32 X=Low.X;X<=High.X;++X)for(int32 Y=Low.Y;Y<=High.Y;++Y)
            if(const auto* Items=OccupiedBins.Find(FIntPoint(X,Y)))for(int32 Index:*Items)
                if(Occupied[Index].IntersectXY(Envelope))return true;
        return false;
    };
    TSet<FIntPoint> Served;
    for(const auto& Value:Graph->GetArrayField(TEXT("nodes")))
    {
        const FString Id=Text(Value->AsObject(),TEXT("id"));
        const auto* Arms=Links.Find(Id);
        if(!Arms||Arms->Num()<3)continue;
        const FVector Center=Positions.FindChecked(Id);
        if(BaseBox.ExpandBy(1200.).IsInsideXY(Center))continue;
        const double Spacing=Revised?6500.:8800.;
        const FIntPoint Block(FMath::FloorToInt(Center.X/Spacing),FMath::FloorToInt(Center.Y/Spacing));
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
                if(Obstructed(Envelope))continue;
                if(Revised&&(Poles.ContainsByPredicate([&](const FVector& P){return FVector::DistSquared2D(P,Pole)<10000;})||
                    Arms->ContainsByPredicate([&](const FLink& Link)
                    {
                        return FVector::DistSquared2D(Pole,FMath::ClosestPointOnSegment(Pole,Center,Center+Link.Outward*Link.Length))<
                            FMath::Square(Link.Width*.5+45);
                    })))continue;
                Pole.Z=InitialGroundHeightCm(Pole);
                Poles.Add(Pole);Placed=true;break;
            }
            if(!Placed)break;
        }
        if(Poles.Num()!=Arms->Num())continue;
        Served.Add(Block);
        if(Revised)for(const auto& Pole:Poles)
            IndexBounds(Occupied.Emplace(Pole-FVector(45,45,0),Pole+FVector(45,45,350)));
        // All markings at this junction share one render-only mesh. Every
        // vertex follows graded ground; terrain damage cuts the same mesh
        // through the existing overlay mechanism and reset restores it.
        TArray<FVector> MarkVertices,MarkNormals;
        TArray<FVector2D> MarkUV;
        TArray<int32> MarkTriangles;
        auto Mark=[&](FVector P,FVector Along,FVector Across,double Length,double Breadth)
        {
            const int32 NX=FMath::Max(1,FMath::CeilToInt(Length/50.)),NY=FMath::Max(1,FMath::CeilToInt(Breadth/50.));
            const int32 First=MarkVertices.Num();
            for(int32 Y=0;Y<=NY;++Y)for(int32 X=0;X<=NX;++X)
            {
                FVector V=P+Along*(Length*(double(X)/NX-.5))+Across*(Breadth*(double(Y)/NY-.5));
                V.Z=InitialGroundHeightCm(V)+3.2;
                MarkVertices.Add(V);MarkNormals.Add(FVector::UpVector);MarkUV.Add(FVector2D(V.X,V.Y)/100.);
            }
            for(int32 Y=0;Y<NY;++Y)for(int32 X=0;X<NX;++X)
            {
                const int32 A=First+Y*(NX+1)+X;
                // Across is the right-hand normal (clockwise from Along).
                MarkTriangles.Append({A,A+1,A+NX+1,A+1,A+NX+2,A+NX+1});
            }
        };
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
                if(Revised)Mark(Stripe,Arm.Outward,Right,158,36);
                else Shape(TEXT("Cube"),Stripe,FVector(158,36,.3),TEXT("MI_Paint"),Turn,false)->ComponentTags.Add(TEXT("CityStreetMarking"));
            }
            FVector StopLine=Line+Right*(Arm.Width*.25);StopLine.Z=InitialGroundHeightCm(StopLine)+2.3;
            if(Revised)Mark(StopLine,Arm.Outward,Right,32,Arm.Width*.5-18);
            else Shape(TEXT("Cube"),StopLine,FVector(32,Arm.Width*.5-18,.3),TEXT("MI_Paint"),Turn,false)->ComponentTags.Add(TEXT("CityStreetMarking"));
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
        if(!MarkVertices.IsEmpty())
        {
            auto* Mesh=NewObject<UProceduralMeshComponent>(this);
            AddInstanceComponent(Mesh);Mesh->SetupAttachment(GetRootComponent());
            Mesh->SetMobility(EComponentMobility::Static);Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
            Mesh->SetCastShadow(false);Mesh->SetAffectDistanceFieldLighting(false);
            Mesh->SetMaterial(0,MRCityMaterial(MRSceneMaterial(LoadObject<UMaterialInterface>(nullptr,
                TEXT("/Game/Materials/MI_Paint.MI_Paint")))));
            Mesh->CreateMeshSection_LinearColor(0,MarkVertices,MarkTriangles,MarkNormals,MarkUV,{},{},false);
            Mesh->ComponentTags={TEXT("CityStreetMarking"),TEXT("NoMissionCollision")};Generated.Add(Mesh);
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
