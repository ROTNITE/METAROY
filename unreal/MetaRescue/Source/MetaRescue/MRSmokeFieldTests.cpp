#include "MRSmokeField.h"
#include "MREnvironment.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMRSmokeOptics,"MetaRescue.Environment.SmokeOptics",
    EAutomationTestFlags::EditorContext|EAutomationTestFlags::EngineFilter)
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMRSmokeLifetime,"MetaRescue.Environment.SmokeLifetime",
    EAutomationTestFlags::EditorContext|EAutomationTestFlags::EngineFilter)
bool FMRSmokeOptics::RunTest(const FString& Parameters)
{
    FMRSmokeVolume Volume;Volume.RadiusCm=1000;Volume.RadiusGrowth=0;Volume.ExtinctionPerCm=.003;
    const FVector A(-1000,0,1500),B(1000,0,1500);
    // Exact integral of sigma * (1 - (x/R)^2)^2 across a full diameter.
    const double Expected=Volume.ExtinctionPerCm*Volume.RadiusCm*16./15.;
    TestTrue(TEXT("optical depth agrees with the analytic radial integral"),
        FMath::Abs(Volume.OpticalDepth(A,B)-Expected)<1.e-4);
    TestTrue(TEXT("Beer-Lambert transmittance uses the integrated path length"),
        FMath::Abs(FMRSmokeVolume::Transmittance({Volume},A,B)-FMath::Exp(-Expected))<1.e-5);
    TestEqual(TEXT("above the plume is clear"),Volume.Extinction(FVector(0,0,4000)),0.);
    TestEqual(TEXT("outside the radius is clear"),Volume.Extinction(FVector(1100,0,1500)),0.);
    TestEqual(TEXT("a zero-length segment has unit transmission"),FMRSmokeVolume::Transmittance({Volume},A,A),1.);
    const double One=FMRSmokeVolume::Transmittance({Volume},A,B);
    TestTrue(TEXT("overlapping plumes add optical depth"),
        FMath::Abs(FMRSmokeVolume::Transmittance({Volume,Volume},A,B)-One*One)<1.e-8);
    TestTrue(TEXT("a long approach cannot sample past the plume"),
        FMath::Abs(Volume.OpticalDepth(FVector(-1000000,0,1500),FVector(1000000,0,1500))-Expected)<1.e-4);

    FMRSmokeVolume Dense;Dense.RadiusGrowth=0;
    TestTrue(TEXT("ten metres through the dense core transmits under five percent"),
        FMRSmokeVolume::Transmittance({Dense},FVector(-500,0,1500),FVector(500,0,1500))<.05);
    Dense.WindCmPerSecond=FVector(300,0,0);
    const FVector Advected=Dense.CenterAt(1500);
    TestEqual(TEXT("higher smoke travels with wind and rise time"),Advected.X,3000.);
    TestTrue(TEXT("advected core retains its physical extinction"),Dense.Extinction(Advected)>0);
    TestEqual(TEXT("the former core is no longer dense after advection"),Dense.Extinction(FVector(0,0,1500)),0.);
    return true;
}

bool FMRSmokeLifetime::RunTest(const FString& Parameters)
{
    FMRDistrictEnvironment Model;Model.bDetailedHazards=true;Model.WindCmPerSecond=FVector(300,0,0);
    FMRDisturbance Fire;Fire.Id=TEXT("roof-fire");Fire.Kind=EMRDisturbance::Fire;
    Fire.Time=10;Fire.ExpiresAt=190;Fire.VisibilityExpiresAt=235;
    Fire.Source=FVector(5000,3000,5);Fire.SmokeEmissionHeightCm=1500;
    Fire.Cells={FMRDistrictEnvironment::Cell(Fire.Source)};
    Model.Tape={Fire};Model.Advance(12);
    const double EarlyHeight=Model.SmokeVolumes()[0].HeightCm;
    Model.Advance(20);
    const auto Volume=Model.SmokeVolumes()[0];
    TestTrue(TEXT("cache follows model time even without a discrete revision"),Volume.HeightCm>EarlyHeight);
    TestEqual(TEXT("the fire plume starts at the actual roof height"),Volume.Source.Z,1500.);
    TestTrue(TEXT("wind-leaned plume history is observable outside the original surface cell"),
        Model.HasHistoryAt(FMRDistrictEnvironment::Cell(Volume.CenterAt(2000))));
    const auto Core=Volume.CenterAt(1400);
    TestTrue(TEXT("surface quality is transmission through ten metres of the same density field"),
        FMath::Abs(Model.Visibility(Core)-FMath::Exp(-1000*Volume.Extinction(Core)))<1.e-6);
    Model.Advance(200);
    TestFalse(TEXT("fire hazard expires before the remaining smoke"),Model.IsActive(Model.Applied[0]));
    TestEqual(TEXT("residual smoke remains after the fire stops"),Model.SmokeVolumes().Num(),1);
    Model.Advance(232);
    TestTrue(TEXT("smoke disperses towards its explicit expiry"),Model.SmokeVolumes()[0].Strength<.31);
    Model.Advance(235);
    TestTrue(TEXT("smoke clears at its explicit boundary"),Model.SmokeVolumes().IsEmpty());
    Model.ResetApplied();
    TestTrue(TEXT("reset clears cached truth"),Model.SmokeVolumes().IsEmpty());
    Model.bDetailedHazards=false;Model.Advance(20);
    TestTrue(TEXT("old profiles do not acquire the new optical physics"),Model.SmokeVolumes().IsEmpty());
    return true;
}
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMRClippedSmokeOptics,"MetaRescue.Environment.ClippedSmokeOptics",
    EAutomationTestFlags::EditorContext|EAutomationTestFlags::EngineFilter)
bool FMRClippedSmokeOptics::RunTest(const FString&)
{
    FMRSmokeVolume Volume;Volume.bClippedQuadrature=true;Volume.RadiusCm=1000;
    Volume.RadiusGrowth=0;Volume.ExtinctionPerCm=.003;
    const FVector A(-1000000,990,1500),B(1000000,990,1500);
    const double Depth=Volume.OpticalDepth(A,B);
    TestTrue(TEXT("long near-tangent ray retains positive extinction"),Depth>0);
    TestTrue(TEXT("ray reversal preserves optical depth"),FMath::Abs(Depth-Volume.OpticalDepth(B,A))<1.e-8);
    TestTrue(TEXT("tangent outside the plume is clear"),Volume.OpticalDepth(FVector(-1000,1001,1500),FVector(1000,1001,1500))==0);
    TestTrue(TEXT("full central diameter matches analytic integral"),
        FMath::Abs(Volume.OpticalDepth(FVector(-1000,0,1500),FVector(1000,0,1500))-3.2)<1.e-10);
    Volume.Source=FVector(150000,150000,340);
    TestTrue(TEXT("large-city translation preserves integration"),
        FMath::Abs(Volume.OpticalDepth(A+Volume.Source,B+Volume.Source)-Depth)<1.e-8);
    FMRDistrictEnvironment Model;Model.bDetailedHazards=true;Model.bRevisedDisasters=true;
    FMRDisturbance Smoke;Smoke.Id=TEXT("revised-smoke");Smoke.Kind=EMRDisturbance::Smoke;Smoke.Time=1.;
    Smoke.Cells={FIntPoint(20,20)};Model.Tape={Smoke};Model.Advance(10.);
    if(!TestEqual(TEXT("one active plume"),Model.SmokeVolumes().Num(),1))return false;
    TestTrue(TEXT("environment selects the same revised field"),Model.SmokeVolumes()[0].bClippedQuadrature);
    Model.ResetApplied();
    TestTrue(TEXT("reset clears revised plumes"),Model.SmokeVolumes().IsEmpty());
    Model.bRevisedDisasters=false;Model.Advance(10.);
    if(!TestEqual(TEXT("one legacy plume"),Model.SmokeVolumes().Num(),1))return false;
    TestFalse(TEXT("old tape keeps legacy integrator"),Model.SmokeVolumes()[0].bClippedQuadrature);
    return true;
}
#endif
