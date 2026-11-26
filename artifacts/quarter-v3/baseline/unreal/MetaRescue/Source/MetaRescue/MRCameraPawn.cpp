#include "MRCameraPawn.h"
#include "MRWorld.h"
#include "MRAgent.h"
#include "Camera/CameraComponent.h"
#include "GameFramework/SpringArmComponent.h"
#include "GameFramework/PlayerController.h"
#include "Components/InputComponent.h"
#include "Kismet/GameplayStatics.h"
#include "EngineUtils.h"
#include "UnrealClient.h"
#include "Misc/Paths.h"

AMRCameraPawn::AMRCameraPawn()
{
    PrimaryActorTick.bCanEverTick=true;PrimaryActorTick.bTickEvenWhenPaused=true;
    SetRootComponent(CreateDefaultSubobject<USceneComponent>(TEXT("CameraFocus")));
    Arm=CreateDefaultSubobject<USpringArmComponent>(TEXT("OrbitArm"));Arm->SetupAttachment(GetRootComponent());
    Arm->PrimaryComponentTick.bTickEvenWhenPaused=true;
    Arm->TargetArmLength=6500;Arm->bDoCollisionTest=false;Arm->SetRelativeRotation(FRotator(Pitch,Yaw,0));
    Camera=CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));Camera->SetupAttachment(Arm);
    Camera->PrimaryComponentTick.bTickEvenWhenPaused=true;
    Camera->FieldOfView=48;
}

void AMRCameraPawn::BeginPlay()
{
    Super::BeginPlay();SetActorLocation(FVector(1500,2000,0));
    for(TActorIterator<AMRWorld> It(GetWorld());It;++It)if(It->bRescueQuarter)
    {SetActorLocation(FVector(2600,2500,120));Arm->TargetArmLength=10500;break;}
    if(auto* PC=Cast<APlayerController>(GetController()))
    {PC->bShowMouseCursor=true;PC->SetInputMode(FInputModeGameAndUI());}
}

void AMRCameraPawn::SetupPlayerInputComponent(UInputComponent* Input)
{
    Super::SetupPlayerInputComponent(Input);
    Input->BindKey(EKeys::Tab,IE_Pressed,this,&AMRCameraPawn::Overview).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::MouseScrollUp,IE_Pressed,this,&AMRCameraPawn::ZoomIn).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::MouseScrollDown,IE_Pressed,this,&AMRCameraPawn::ZoomOut).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::F,IE_Pressed,this,&AMRCameraPawn::Collapse);
    Input->BindKey(EKeys::SpaceBar,IE_Pressed,this,&AMRCameraPawn::Pause).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::F9,IE_Pressed,this,&AMRCameraPawn::Screenshot).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::One,IE_Pressed,this,&AMRCameraPawn::BaseView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Two,IE_Pressed,this,&AMRCameraPawn::QuarterView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Three,IE_Pressed,this,&AMRCameraPawn::CollapseView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Four,IE_Pressed,this,&AMRCameraPawn::WaterView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Five,IE_Pressed,this,&AMRCameraPawn::TunnelView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Six,IE_Pressed,this,&AMRCameraPawn::FireView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Seven,IE_Pressed,this,&AMRCameraPawn::StreetView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Eight,IE_Pressed,this,&AMRCameraPawn::VehicleView).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::I,IE_Pressed,this,&AMRCameraPawn::ToggleInspector).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Left,IE_Pressed,this,&AMRCameraPawn::PreviousAgent).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::Right,IE_Pressed,this,&AMRCameraPawn::NextAgent).bExecuteWhenPaused=true;
    Input->BindKey(EKeys::R,IE_Pressed,this,&AMRCameraPawn::ToggleFollow).bExecuteWhenPaused=true;
}

void AMRCameraPawn::ZoomIn(){Arm->TargetArmLength=FMath::Max(900.f,Arm->TargetArmLength*.85f);}
void AMRCameraPawn::ZoomOut(){Arm->TargetArmLength=FMath::Min(50000.f,Arm->TargetArmLength/ .85f);}
void AMRCameraPawn::Overview()
{
    bOverview=!bOverview;
    for(TActorIterator<AMRWorld> It(GetWorld());It;++It)
    {
        if(It->bRescueQuarter)
        {
            SetActorLocation(bOverview?FVector(7200,7200,0):FVector(2600,2500,120));
            Arm->TargetArmLength=bOverview?26500:10500;break;
        }
        SetActorLocation(bOverview?FVector((It->GridSize-1)*500,(It->GridSize-1)*500,0):FVector(1500,2000,0));
        Arm->TargetArmLength=bOverview?It->GridSize*1300:6500;break;
    }
}
void AMRCameraPawn::Collapse(){for(TActorIterator<AMRWorld> It(GetWorld());It;++It)It->ReleaseCollapse();}
void AMRCameraPawn::Pause(){UGameplayStatics::SetGamePaused(GetWorld(),!UGameplayStatics::IsGamePaused(GetWorld()));}
void AMRCameraPawn::Screenshot(){FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir()/TEXT("Screenshots/manual.png"),true,true);}
void AMRCameraPawn::BaseView(){SetPreset(0);}
void AMRCameraPawn::QuarterView(){SetPreset(1);}
void AMRCameraPawn::CollapseView(){SetPreset(2);}
void AMRCameraPawn::WaterView(){SetPreset(3);}
void AMRCameraPawn::TunnelView(){SetPreset(4);}
void AMRCameraPawn::FireView(){SetPreset(5);}
void AMRCameraPawn::StreetView(){SetPreset(6);}
void AMRCameraPawn::VehicleView(){SetPreset(7);}
void AMRCameraPawn::SetPreset(int32 Index)
{
    const FVector Locations[]={FVector(-1100,1400,80),FVector(6500,6500,0),FVector(7800,5950,80),FVector(13600,13000,100),FVector(12400,3050,100),FVector(10900,7950,100),FVector(2450,2450,400),FVector(-750,200,80)};
    const float Distances[]={4200,22000,5900,6500,5000,2900,3500,1500};
    bFollowing=false;Index=FMath::Clamp(Index,0,7);SetActorLocation(Locations[Index]);Arm->TargetArmLength=Distances[Index];
    Yaw=Index==0?160:45;Pitch=Index==1?-50:-38;Arm->SetRelativeRotation(FRotator(Pitch,Yaw,0));bOverview=Index==1;
    if(Index==5){Yaw=225;Pitch=-48;Arm->SetRelativeRotation(FRotator(Pitch,Yaw,0));}
    if(Index==7)
    {
        ObservedAgent=3;bFollowing=true;bShowInspector=true;
        if(auto* Agent=GetObservedAgent())SetActorLocation(Agent->GetActorLocation());
    }
}
void AMRCameraPawn::ToggleInspector(){bShowInspector=!bShowInspector;}
void AMRCameraPawn::PreviousAgent(){CycleAgent(-1);}
void AMRCameraPawn::NextAgent(){CycleAgent(1);}
void AMRCameraPawn::CycleAgent(int32 Delta)
{
    for(TActorIterator<AMRWorld> It(GetWorld());It;++It)if(It->Agents.Num()>0)
    {ObservedAgent=(ObservedAgent+Delta+It->Agents.Num())%It->Agents.Num();break;}
    bShowInspector=true;
}
void AMRCameraPawn::ToggleFollow(){bFollowing=!bFollowing;bShowInspector=true;if(bFollowing)Arm->TargetArmLength=1900;}
AMRAgent* AMRCameraPawn::GetObservedAgent() const
{
    for(TActorIterator<AMRWorld> It(GetWorld());It;++It)
        return It->Agents.IsValidIndex(ObservedAgent)?It->Agents[ObservedAgent].Get():nullptr;
    return nullptr;
}

void AMRCameraPawn::Tick(float Dt)
{
    Super::Tick(Dt);auto* PC=Cast<APlayerController>(GetController());if(!PC)return;
    Arm->bDoCollisionTest=bFollowing;
    if(bFollowing)if(AMRAgent* Agent=GetObservedAgent())SetActorLocation(FMath::VInterpTo(GetActorLocation(),Agent->GetActorLocation(),Dt,4));
    if(PC->IsInputKeyDown(EKeys::RightMouseButton))
    {
        float DX=0,DY=0;PC->GetInputMouseDelta(DX,DY);Yaw+=DX*.25f;Pitch=FMath::Clamp(Pitch-DY*.2f,-85.f,-12.f);
        Arm->SetRelativeRotation(FRotator(Pitch,Yaw,0));
    }
    FVector Movement=FVector::ZeroVector;
    FVector Forward=Camera->GetForwardVector();Forward.Z=0;Forward.Normalize();
    const FVector Right=Camera->GetRightVector();
    if(PC->IsInputKeyDown(EKeys::W))Movement+=Forward;
    if(PC->IsInputKeyDown(EKeys::S))Movement-=Forward;
    if(PC->IsInputKeyDown(EKeys::D))Movement+=Right;
    if(PC->IsInputKeyDown(EKeys::A))Movement-=Right;
    if(!Movement.IsNearlyZero())bFollowing=false;
    AddActorWorldOffset(Movement*Dt*Arm->TargetArmLength*.4f);
}
