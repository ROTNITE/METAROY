#include "MRCoreRuntime.h"
#include "MRAgent.h"
#include "MRCoreJson.h"
#include "MRCoreValues.h"
#include "MRWorld.h"
#include "Components/BoxComponent.h"

using namespace MRCoreValues;

namespace
{
bool IsNewContact(TMap<FString,double>& ContactTimes,const FString& Pair,double Now)
{
    const double* Last=ContactTimes.Find(Pair);
    const bool NewContact=!Last||Now-*Last>=.25;
    ContactTimes.Add(Pair,Now);
    return NewContact;
}
}

void FMRCoreRuntime::NoteVehicleContact(const AMRAgent* A,const AMRAgent* B)
{
    if(!ActiveStep||A==B)return;
    FString First=A->AgentKey(),Second=B->AgentKey();
    if(Second<First)Swap(First,Second);
    const FString Key=First+TEXT("|")+Second;
    if(!IsNewContact(VehicleContactAt,Key,Time()))return;
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

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMRCoreSafetyContactTest,"MetaRescue.Core.TournamentSafetyContacts",
    EAutomationTestFlags::EditorContext|EAutomationTestFlags::EngineFilter)

bool FMRCoreSafetyContactTest::RunTest(const FString& Parameters)
{
    TMap<FString,double> Contacts;
    TestTrue(TEXT("First physical contact is counted"),IsNewContact(Contacts,TEXT("a|b"),1.));
    TestFalse(TEXT("Second physics callback for the pair is deduplicated"),IsNewContact(Contacts,TEXT("a|b"),1.));
    TestFalse(TEXT("Continuous contact is deduplicated"),IsNewContact(Contacts,TEXT("a|b"),1.125));
    TestTrue(TEXT("Other pairs are independent"),IsNewContact(Contacts,TEXT("a|c"),1.125));
    TestTrue(TEXT("A new impact after separation is counted"),IsNewContact(Contacts,TEXT("a|b"),1.375));
    Contacts.Reset();
    TestTrue(TEXT("The same pair is counted in a new episode"),IsNewContact(Contacts,TEXT("a|b"),0.));
    return true;
}
#endif
