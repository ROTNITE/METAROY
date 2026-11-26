#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MRAgent.generated.h"
class UBoxComponent;
class UStaticMeshComponent;
class AMRWorld;

UCLASS()
class METARESCUE_API AMRAgent : public AActor
{
    GENERATED_BODY()
public:
    AMRAgent();
    void Initialize(AMRWorld* InWorld, int32 InId, bool Aerial, const TArray<FVector>& InitialPath);
    virtual void Tick(float Dt) override;
    UFUNCTION(BlueprintCallable, Category="Rescue") void SetWaypoints(const TArray<FVector>& Waypoints);
    bool IsIdle() const { return State == "idle"; }
    void StartRescue(int32 TargetIndex);
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly) TObjectPtr<UBoxComponent> Body;
    UPROPERTY() TObjectPtr<AMRWorld> Mission;
    UPROPERTY() TArray<TObjectPtr<UStaticMeshComponent>> Parts;
    bool bAerial = false;
    int32 AgentId = 0;
    int32 TargetIndex = -1;
    FString State = "idle";
    float Energy = 220;
    float DistanceTravelled = 0;
    float MaxSpeed = 620;
    FVector Dock;
private:
    TArray<FVector> Path;
    int32 PathIndex = 0;
    float ServiceRemaining = 0;
    FVector Previous;
    UStaticMeshComponent* Part(const FString& Mesh, FVector Position, FVector Size,
        const FString& Material, FRotator Rotation = FRotator::ZeroRotator);
    void Arrived();
};
