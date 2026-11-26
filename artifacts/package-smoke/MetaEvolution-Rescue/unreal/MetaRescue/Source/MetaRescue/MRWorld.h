#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MRWorld.generated.h"

class AMRAgent;
class UProceduralMeshComponent;
class UStaticMeshComponent;
class UMaterialInterface;
class UPrimitiveComponent;
class FJsonObject;

struct FMRTarget
{
    int32 Id = 0;
    FVector Position = FVector::ZeroVector;
    bool bDiscovered = false;
    bool bAssigned = false;
    bool bRescued = false;
    double DiscoveredAt = -1;
    TObjectPtr<AActor> Visual = nullptr;
    TArray<FVector> Path;
};

UCLASS()
class METARESCUE_API AMRWorld : public AActor
{
    GENERATED_BODY()
public:
    AMRWorld();
    UFUNCTION(CallInEditor, BlueprintCallable, Category="Rescue") void BuildScene();
    UFUNCTION(BlueprintCallable, Category="Rescue") void ReleaseCollapse();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    int32 FoundCount() const;
    int32 SavedCount() const;
    int32 ContactCount = 0;
    int32 GridSize = 25;
    int32 Seed = 2026;
    bool bCollapseReleased = false;
    FVector Base = FVector(1000,1000,0);
    TArray<FMRTarget> Targets;
    UPROPERTY() TArray<TObjectPtr<AMRAgent>> Agents;
    UPROPERTY() TArray<TObjectPtr<USceneComponent>> Generated;
    UPROPERTY() TArray<TObjectPtr<UProceduralMeshComponent>> PhysicsPieces;
    UPROPERTY() TArray<TObjectPtr<UProceduralMeshComponent>> CollapsePieces;
    TArray<FVector> InitialPiecePositions;
    TSharedPtr<FJsonObject> Scenario;
    void AppendEvent(const FString& Type, int32 Id = -1);
    void WriteTelemetry();
private:
    float SensorClock = 0;
    float TelemetryClock = 0;
    float QuitAfter = 0;
    bool bSmoke = false;
    bool bScreenshotTaken = false;
    bool bStartScreenshotTaken = false;
    TArray<TArray<int32>> Grid;
    UStaticMeshComponent* Shape(const FString& Mesh, const FVector& Position,
        const FVector& Size, const FString& Material, const FRotator& Rotation = FRotator::ZeroRotator, bool Collision = true);
    UProceduralMeshComponent* Chunk(FRandomStream& Rng, const FVector& Position,
        const FVector& Size, bool Physics, bool Collapse = false);
    void Building(FRandomStream& Rng, int32 X, int32 Y);
    void Checkpoint();
    void SpawnAgents();
    void SpawnTargets();
    void SenseAndDispatch(float Dt);
    bool ReadScenario();
    UFUNCTION() void OnRubbleHit(UPrimitiveComponent* HitComponent, AActor* OtherActor,
        UPrimitiveComponent* OtherComponent, FVector NormalImpulse, const FHitResult& Hit);
};
