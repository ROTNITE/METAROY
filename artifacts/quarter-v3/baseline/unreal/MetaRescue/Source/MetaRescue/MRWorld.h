#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MRAssetAssembly.h"
#include "MRWorld.generated.h"

class AMRAgent;
class UProceduralMeshComponent;
class UStaticMeshComponent;
class UMaterialInterface;
class UPrimitiveComponent;
class FJsonObject;
class UNiagaraComponent;

struct FMRTarget
{
    int32 Id = 0;
    FVector Position = FVector::ZeroVector;
    bool bDiscovered = false;
    bool bAssigned = false;
    bool bRescued = false;
    double DiscoveredAt = -1;
    double ObservedAt = -1;
    double AvailableAt = 0;
    int32 Observer = -1;
    FVector ReportOrigin = FVector::ZeroVector;
    bool bAvailabilityReported = false;
    TObjectPtr<AActor> Visual = nullptr;
    TArray<FVector> Path;
};

UCLASS()
class METARESCUE_API AMRWorld : public AActor
{
    GENERATED_BODY()
public:
    AMRWorld();
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Rescue") bool bRescueQuarter = false;
    UFUNCTION(CallInEditor, BlueprintCallable, Category="Rescue") void BuildScene();
    UFUNCTION(BlueprintCallable, Category="Rescue") void ReleaseCollapse();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    int32 FoundCount() const;
    int32 SavedCount() const;
    int32 ContactCount = 0;
    double ProfileSeconds = 0;
    int32 ProfileFrames = 0;
    float WorstFrameSeconds = 0;
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
    float GroundSpeedFactor(const FVector& Position) const;
    bool IsUnsafeGround(const FVector& Position) const;
    bool HasRadio(const FVector& Position) const;
    float WaterDepth(const FVector& Position) const;
    float SensorRange(const FVector& Position) const;
    int32 PendingReports() const;
    bool bRelayOnline = true;
    bool bFloodActive = false;
private:
    void BuildDistrict();
    USceneComponent* DistrictAssembly(const FString& Name, const FVector& Position,
        const FRotator& Rotation = FRotator::ZeroRotator, const FVector& Scale = FVector::OneVector,
        bool Collision = true, bool Movable = false, const FString& Module = TEXT(""));
    void Apartment(const FVector& Position, int32 Floors, int32 Variant, bool Damaged);
    void TickDistrict(float Dt);
    TArray<FMRAssemblyPart> DistrictMechanisms;
    TObjectPtr<USceneComponent> ServiceVan = nullptr;
    FVector VanPark;
    bool bDustActive = false;
    UPROPERTY() TObjectPtr<UNiagaraComponent> DustEffect;
    UPROPERTY() TObjectPtr<UNiagaraComponent> FireEffect;
    UPROPERTY() TObjectPtr<UNiagaraComponent> LeakEffect;
    UPROPERTY() TArray<TObjectPtr<AMRAgent>> MechanicsProbes;
    bool bMechanicsProbe = false;
    bool bProbeSpawned = false;
    UStaticMeshComponent* DistrictMesh(const FString& Name, const FVector& Position,
        const FRotator& Rotation = FRotator::ZeroRotator, const FVector& Scale = FVector::OneVector, bool Collision = true);
    void DistrictLabel(const FString& Text, const FVector& Position, float Size = 45);
    float SensorClock = 0;
    float TelemetryClock = 0;
    float QuitAfter = 0;
    bool bSmoke = false;
    bool bScreenshotTaken = false;
    bool bStartScreenshotTaken = false;
    bool bPhotoTour = false;
    int32 PhotoStage = 0;
    bool bPhotoPending = false;
    float PhotoRequestedAt = 0;
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
