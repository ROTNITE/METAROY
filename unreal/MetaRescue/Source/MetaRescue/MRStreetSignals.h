#pragma once
#include "CoreMinimal.h"

class UStaticMeshComponent;
class UMaterialInterface;

enum class EMRStreetPhase : int8 { Dark=-1, Red, Amber, Green };

inline EMRStreetPhase MRStreetPhase(double Time,int32 Offset,int32 Group,bool Powered)
{
    if(!Powered)return EMRStreetPhase::Dark;
    const double Turn=FMath::Fmod(FMath::Max(0.,Time)+Offset,28.);
    const double Start=Group*14.;
    if(Turn>=Start&&Turn<Start+10.)return EMRStreetPhase::Green;
    if(Turn>=Start+10.&&Turn<Start+12.)return EMRStreetPhase::Amber;
    return EMRStreetPhase::Red;
}

struct FMRStreetArm
{
    FVector Center,Outward,PowerPoint;
    double StopDistanceCm=0,LaneWidthCm=0;
    int32 Offset=0,Group=0;
    FName Id;
    struct FLens
    {
        TWeakObjectPtr<UStaticMeshComponent> Mesh;
        TWeakObjectPtr<UMaterialInterface> Lit;
        int32 Slot=0;
    };
    TArray<FLens> Lenses;
    EMRStreetPhase LastPhase=EMRStreetPhase::Dark;
};
