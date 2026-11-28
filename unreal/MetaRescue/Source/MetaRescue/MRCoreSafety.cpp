#include "MRCoreRuntime.h"
#include "MRAgent.h"
#include "MRCoreJson.h"
#include "MRCoreValues.h"
#include "MRWorld.h"
#include "Components/BoxComponent.h"

using namespace MRCoreValues;

void FMRCoreRuntime::NoteVehicleContact(const AMRAgent* A,const AMRAgent* B)
{
    if(!ActiveStep||A==B)return;
    FString First=A->AgentKey(),Second=B->AgentKey();
    if(Second<First)Swap(First,Second);
    const FString Key=First+TEXT("|")+Second;
    const double* Last=VehicleContactAt.Find(Key);
    const bool NewContact=!Last||Time()-*Last>=.25;
    VehicleContactAt.Add(Key,Time());
    if(!NewContact)return;
    ++SafetyViolations;
    Record(TEXT("safety_violation"),Obj({{TEXT("kind"),Str(TEXT("vehicle_contact"))},
        {TEXT("agents"),Arr({Str(First),Str(Second)})},{TEXT("total"),Num(SafetyViolations)}}),false);
}

void FMRCoreRuntime::TrackSafetyOccupancies()
{
    TSet<FString> Current;
    auto Enter=[&](const FString& Kind,const FString& Boundary,const AMRAgent* Agent)
    {
        const FString Key=Kind+TEXT("|")+Boundary+TEXT("|")+Agent->AgentKey();
        Current.Add(Key);
        if(SafetyOccupancies.Contains(Key))return;
        ++SafetyViolations;
        Record(TEXT("safety_violation"),Obj({{TEXT("kind"),Str(Kind)},{TEXT("boundary"),Str(Boundary)},
            {TEXT("agent_id"),Str(Agent->AgentKey())},{TEXT("position_m"),Point(Agent->GetActorLocation()/100.)},
            {TEXT("total"),Num(SafetyViolations)}}),false);
    };
    FBox WorldBounds(ForceInit);
    if(World->bCityLayout)
    {
        const auto Bounds=World->CityLayout->GetObjectField(TEXT("bounds"));
        FVector Min,Max;
        if(MRCoreJson::Point(Field(Bounds,TEXT("min")),Min)&&MRCoreJson::Point(Field(Bounds,TEXT("max")),Max))
            WorldBounds=FBox(Min*100.,Max*100.);
    }
    for(const AMRAgent* Agent:World->Agents)
    {
        if(!Agent->CanMove()||!Agent->IsSpatial())continue;
        const FVector Position=Agent->GetActorLocation();
        const FVector Half=Agent->Body->GetScaledBoxExtent();
        if(Agent->MediumLimits.IsValid&&!Agent->MediumLimits.IsInsideOrOn(Position))
            Enter(TEXT("outside_motion_medium"),Agent->Kind(),Agent);
        else if(WorldBounds.IsValid&&!WorldBounds.IsInsideOrOnXY(Position))
            Enter(TEXT("outside_mission_bounds"),TEXT("map"),Agent);
        for(const auto& Pair:World->CoreRestrictions)
        {
            const auto& Region=Pair.Value;
            if(!Region.AllowedKinds.Contains(Agent->Kind())&&
               Region.Bounds.ExpandBy(FVector(Agent->SafetyRadius(),Agent->SafetyRadius(),Half.Z)).IsInside(Position))
                Enter(TEXT("restricted_volume_occupancy"),Pair.Key,Agent);
        }
    }
    SafetyOccupancies=MoveTemp(Current);
}
