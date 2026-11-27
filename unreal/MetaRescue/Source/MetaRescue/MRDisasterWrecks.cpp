#include "MRWorld.h"
#include "MRCoreJson.h"
#include "MRCoreValues.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"

using namespace MRCoreValues;

void AMRWorld::PlaceDisasterWreck(const FMRDisturbance& Event)
{
    if(!bDisasterCity||Event.bPropagation||!CityLayout->HasTypedField<EJson::Number>(TEXT("street_controls_revision")))return;
    const bool Burned=Event.Kind==EMRDisturbance::Fire;
    if(!Burned&&Event.Kind!=EMRDisturbance::Collapse&&Event.Kind!=EMRDisturbance::RoadDamage)return;
    const FString Asset=Burned?TEXT("Wreck_Burned"):TEXT("Wreck_Crushed");
    const FString Path=FString::Printf(TEXT("/Game/RescueAssets/DisasterVehiclesV1/%s/SM_%s.SM_%s"),*Asset,*Asset,*Asset);
    auto* Mesh=LoadObject<UStaticMesh>(nullptr,*Path);
    if(!Mesh)return;
    const FBox Local=Mesh->GetBoundingBox();
    const double HeadingOffset=Local.GetSize().X>=Local.GetSize().Y?0.:-90.;

    struct FCandidate { FVector Position; FRotator Rotation; double Distance; };
    TArray<FCandidate> Options;
    TMap<FString,FVector> Nodes;
    const auto RoadGraph=CityLayout->GetObjectField(TEXT("road_graph"));
    for(const auto& Value:RoadGraph->GetArrayField(TEXT("nodes")))
    {
        FVector P;
        const auto Node=Value->AsObject();
        if(MRCoreJson::Point(Field(Node,TEXT("position")),P))Nodes.Add(Text(Node,TEXT("id")),P*100);
    }
    for(const auto& Value:RoadGraph->GetArrayField(TEXT("edges")))
    {
        const auto Road=Value->AsObject();
        if(Text(Road,TEXT("street_class"))==TEXT("access"))continue;
        const auto* A=Nodes.Find(Text(Road,TEXT("from")));
        const auto* B=Nodes.Find(Text(Road,TEXT("to")));
        if(!A||!B)continue;
        const FVector Direction=(*B-*A).GetSafeNormal2D();
        const double Length=FVector::Dist2D(*A,*B);
        if(Length<1800)continue;
        const double Width=Road->GetNumberField(TEXT("width_m"))*100;
        const double Room=FMath::Max(700.,Width*.5+210.);
        if(Length<2*Room)continue;
        const double Along=FVector::DotProduct(Event.Source-*A,Direction);
        if(Along< -1300||Along>Length+1300)continue;
        const double CenterAlong=FMath::Clamp(Along,Room,Length-Room);
        const FVector Center=*A+Direction*CenterAlong;
        if(FVector::DistSquared2D(Center,Event.Source)>FMath::Square(1800.))continue;
        const FVector Normal(-Direction.Y,Direction.X,0);
        for(double Side:{1.,-1.})for(double Offset:{0.,-430.,430.})
        {
            if(CenterAlong+Offset<Room||CenterAlong+Offset>Length-Room)continue;
            const FVector P=Center+Normal*(Side*(Width*.5+270.))+Direction*Offset;
            const double Distance=FVector::DistSquared2D(P,Event.Source);
            if(Distance>FMath::Square(2200.)||FVector::DistSquared2D(P,Base)<FMath::Square(5200.))continue;
            const FRotator Turn(0,Direction.Rotation().Yaw+HeadingOffset+(Burned?0:Side*16.),0);
            Options.Add({P,Turn,Distance});
        }
    }
    Options.Sort([](const FCandidate& A,const FCandidate& B){return A.Distance<B.Distance;});
    for(const auto& Option:Options)
    {
        const FVector P=Option.Position;
        FCollisionQueryParams Query(SCENE_QUERY_STAT(MRDisasterWreckSupport),false);
        double Top=-MAX_dbl,Bottom=MAX_dbl;
        const auto Extent=Local.GetExtent();
        for(double SideX:{-.58,.58})for(double SideY:{-.62,.62})
        {
            FVector Point=P+Option.Rotation.RotateVector(FVector(Extent.X*SideX,Extent.Y*SideY,0));
            Point.Z=InitialGroundHeightCm(Point);
            FHitResult Floor;
            if(GetWorld()->LineTraceSingleByChannel(Floor,Point+FVector(0,0,30),Point-FVector(0,0,45),
                ECC_Visibility,Query)&&Floor.ImpactNormal.Z>.94)
            {
                Top=FMath::Max(Top,Floor.ImpactPoint.Z);
                Bottom=FMath::Min(Bottom,Floor.ImpactPoint.Z);
            }
            else Top=MAX_dbl;
        }
        if(Top-Bottom>16)continue;
        const FVector Origin(P.X,P.Y,Top-Local.Min.Z);
        const FVector EnvelopeCenter=Origin+Option.Rotation.RotateVector(Local.GetCenter())+FVector(0,0,14);
        const FVector Half=Local.GetExtent()+FVector(14,14,-2);
        if(Targets.ContainsByPredicate([&](const auto& T)
            {return !T.bRescued&&FVector::DistSquared2D(T.Position,Origin)<FMath::Square(340.);})||
            GetWorld()->OverlapBlockingTestByChannel(EnvelopeCenter,Option.Rotation.Quaternion(),ECC_Visibility,
                FCollisionShape::MakeBox(Half),Query))continue;
        auto* Wreck=NewObject<UStaticMeshComponent>(this);
        AddInstanceComponent(Wreck);Wreck->SetupAttachment(GetRootComponent());
        Wreck->SetMobility(EComponentMobility::Static);
        Wreck->SetStaticMesh(Mesh);Wreck->SetWorldLocationAndRotation(Origin,Option.Rotation);
        Wreck->SetCollisionProfileName(TEXT("BlockAll"));
        Wreck->SetCanEverAffectNavigation(false);
        Wreck->ComponentTags={TEXT("DisasterVehicleWreck"),FName(*Event.Id),FName(*Event.SiteId)};
        Wreck->RegisterComponent();Generated.Add(Wreck);
        UE_LOG(LogTemp,Display,TEXT("MR_DISASTER_VEHICLE event=%s asset=%s location=%s"),
            *Event.Id,*Asset,*Origin.ToCompactString());
        return;
    }
}
