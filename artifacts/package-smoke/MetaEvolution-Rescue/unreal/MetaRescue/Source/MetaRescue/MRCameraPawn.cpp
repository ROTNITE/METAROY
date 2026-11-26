#include "MRCameraPawn.h"
#include "MRWorld.h"
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
    Arm->TargetArmLength=6500;Arm->bDoCollisionTest=false;Arm->SetRelativeRotation(FRotator(Pitch,Yaw,0));
    Camera=CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));Camera->SetupAttachment(Arm);
    Camera->FieldOfView=48;
}

void AMRCameraPawn::BeginPlay()
{
    Super::BeginPlay();SetActorLocation(FVector(1500,2000,0));
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
}

void AMRCameraPawn::ZoomIn(){Arm->TargetArmLength=FMath::Max(900.f,Arm->TargetArmLength*.85f);}
void AMRCameraPawn::ZoomOut(){Arm->TargetArmLength=FMath::Min(50000.f,Arm->TargetArmLength/ .85f);}
void AMRCameraPawn::Overview()
{
    bOverview=!bOverview;
    for(TActorIterator<AMRWorld> It(GetWorld());It;++It)
    {
        SetActorLocation(bOverview?FVector((It->GridSize-1)*500,(It->GridSize-1)*500,0):FVector(1500,2000,0));
        Arm->TargetArmLength=bOverview?It->GridSize*1300:6500;break;
    }
}
void AMRCameraPawn::Collapse(){for(TActorIterator<AMRWorld> It(GetWorld());It;++It)It->ReleaseCollapse();}
void AMRCameraPawn::Pause(){UGameplayStatics::SetGamePaused(GetWorld(),!UGameplayStatics::IsGamePaused(GetWorld()));}
void AMRCameraPawn::Screenshot(){FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir()/TEXT("Screenshots/manual.png"),true,true);}

void AMRCameraPawn::Tick(float Dt)
{
    Super::Tick(Dt);auto* PC=Cast<APlayerController>(GetController());if(!PC)return;
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
    AddActorWorldOffset(Movement*Dt*Arm->TargetArmLength*.4f);
}
