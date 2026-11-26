#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "MRCameraPawn.generated.h"
class USpringArmComponent;
class UCameraComponent;

UCLASS()
class METARESCUE_API AMRCameraPawn : public APawn
{
    GENERATED_BODY()
public:
    AMRCameraPawn();
    virtual void BeginPlay() override;
    virtual void Tick(float Dt) override;
    virtual void SetupPlayerInputComponent(UInputComponent* Input) override;
    UPROPERTY(VisibleAnywhere) TObjectPtr<USpringArmComponent> Arm;
    UPROPERTY(VisibleAnywhere) TObjectPtr<UCameraComponent> Camera;
private:
    float Yaw = 45;
    float Pitch = -42;
    bool bOverview = false;
    void Overview();
    void ZoomIn();
    void ZoomOut();
    void Collapse();
    void Pause();
    void Screenshot();
};
