#pragma once
#include "CoreMinimal.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "UObject/StrongObjectPtr.h"
#include "MRExplorationMemory.h"
#include "ProceduralMeshComponent.h"

class AMRWorld;
class AMRAgent;
class UStaticMesh;
class UStaticMeshComponent;
class UMaterialInterface;
class UMaterialInstanceDynamic;
class UTexture2D;
class APlayerController;
class UPostProcessComponent;
class USkyLightComponent;
class FJsonObject;

struct FMRSceneSection
{
    TStrongObjectPtr<UMaterialInterface> Material;
    FProcMeshSection Geometry;
};
struct FMRSceneMesh
{
    uint64 Key=0;
    FTransform Pose;
    FBox Bounds=FBox(ForceInit);
    TStrongObjectPtr<UStaticMesh> Mesh;
    TArray<TStrongObjectPtr<UMaterialInterface>> Materials;
    TArray<FMRSceneSection> Procedural;
    bool Shadow=true;
};
struct FMRSceneActorState
{
    FString Id;
    double ObservedAt=-1;
    TArray<FMRSceneMesh> Meshes;
};
struct FMRSceneActorReport {TArray<FMRSceneActorState> Actors;};
struct FMRSceneCell
{
    FIntPoint Cell;
    double ObservedAt=-1;
    bool SurfaceVisible=false;
    bool SolidSurface=false;
    TArray<FMRSceneSection> Surface;
};
struct FMRSceneEffect
{
    FString Id,Source;
    FIntVector Cell;
    FVector Focus=FVector::ZeroVector;
    double ObservedAt=-1;
    bool Smoke=false;
    FMRSceneMesh Visual;
};
struct FMRSceneReport
{
    double ObservedAt=-1;
    FString Source;
    TArray<FMRSceneCell> Cells;
    TArray<FMRSceneMesh> Objects; // An empty mesh is an optically witnessed removal.
    TArray<FMRSceneEffect> Effects;
};

// This object never changes physics or sensor queries. True renderers are
// hidden in command view; independent meshes contain only delivered samples.
class FMRExplorationScene
{
public:
    explicit FMRExplorationScene(AMRWorld* InWorld):World(InWorld){}
    ~FMRExplorationScene();
    void Reset(const FMRExplorationMemory& Knowledge);
    TSharedPtr<FMRSceneReport> Capture(const FMRExplorationMemory& Knowledge,
        const TArray<FMRSeenSurface>& Cells,FVector Eye,const FString& Source,double At,
        TFunctionRef<double(FVector,FVector)> Quality,bool Owned=false,double RangeCm=0,bool OpticalAccept=true);
    void Apply(const FMRSceneReport& Report,const FMRExplorationMemory& Knowledge);
    TSharedPtr<FMRSceneActorReport> CaptureActor(AMRAgent* Agent,double At);
    TSharedPtr<FMRSceneActorReport> CaptureTarget(int32 Index,double At);
    void ApplyActors(const TSharedPtr<FMRSceneActorReport>& Report);
    void View(bool KnowledgeView,double At);
    TSharedPtr<FJsonObject> Snapshot() const;
    int32 GetRevision() const {return Revision;}
private:
    struct FSource
    {
        TWeakObjectPtr<UStaticMeshComponent> Mesh;
        TWeakObjectPtr<UProceduralMeshComponent> Procedural;
        uint64 Key=0;FTransform Pose;FBox Bounds=FBox(ForceInit);
        int32 Instance=INDEX_NONE;bool Alive=true;
    };
    struct FRememberedObject {FMRSceneMesh Value;double At=-1;};
    struct FIndexedSurface
    {
        TArray<FProcMeshVertex> Vertices;
        TArray<FBox> TriangleBounds;
        TMap<FIntPoint,TArray<int32>> TrianglesByCell;
    };
    AMRWorld* World;
    FMRExplorationGrid Grid;
    TArray<FSource> Sources;
    TMap<FString,int32> SourceLookup;
    TMap<FIntPoint,TArray<int32>> SourceBins;
    TMap<uint64,TArray<FIndexedSurface>> SurfaceIndex;
    TMap<uint64,FRememberedObject> Objects;
    TMap<int32,FMRSceneCell> Surfaces;
    TMap<FString,FMRSceneActorState> Actors;
    TMap<FString,FMRSceneEffect> Effects;
    TMap<FString,TArray<TWeakObjectPtr<USceneComponent>>> ActorRenderers;
    TMap<FString,TWeakObjectPtr<UMaterialInterface>> Materials;
    TArray<FMRSceneMesh> InitialScenery;
    TArray<FMRSceneMesh> InitialGround;
    struct FInitialInstance
    {
        TWeakObjectPtr<UInstancedStaticMeshComponent> Batch;
        FPrimitiveInstanceId Id;
    };
    TMap<uint64,FInitialInstance> SceneryInstances;
    TArray<uint64> DeliveredReplacements;
    TArray<TWeakObjectPtr<USceneComponent>> GroundRenderers;
    TStrongObjectPtr<UTexture2D> NightReveal;
    int32 LastRevealEpoch=-1;
    bool RevealDirty=true;
    TArray<TPair<TWeakObjectPtr<USceneComponent>,bool>> AtmosphereVisibility;
    TArray<TPair<TWeakObjectPtr<USkyLightComponent>,bool>> SkyCaptureStates;
    TWeakObjectPtr<AActor> Renderer;
    TWeakObjectPtr<UPostProcessComponent> Matte;
    TMap<uint64,FInitialInstance> ObjectInstances;
    TMap<FString,TWeakObjectPtr<UInstancedStaticMeshComponent>> ObjectBatches;
    TMap<uint64,TWeakObjectPtr<UProceduralMeshComponent>> ObjectProcedurals;
    TArray<TWeakObjectPtr<USceneComponent>> EffectRenderers;
    TMap<FIntPoint,TWeakObjectPtr<UProceduralMeshComponent>> TerrainRenderers;
    TSet<FIntPoint> DirtyTiles;
    TSet<FString> DirtyActors;
    TSet<uint64> DirtyObjects;
    bool EffectsDirty=false,BackdropBuilt=false,Active=false;
    int32 Revision=0,LastSignature=-1;
    double CatalogAt=-1;
    UMaterialInterface* RememberMaterial(UMaterialInterface* Material,double At);
    FMRSceneMesh ReadMesh(const FSource& Source,double At);
    FMRSceneActorState ReadActor(AActor* Actor,const FString& Id,double At,bool Erase=false);
    void RefreshSources(double At);
    const TArray<FIndexedSurface>& IndexSurface(const FSource& Source,UProceduralMeshComponent* Mesh);
    void UpdateObjectRenderers();
    void UpdateActorRenderers(const FMRSceneActorState& State);
    void CaptureBackdrop();
    void BuildBackdrop();
    void ReplaceBackdropParts();
    void UpdateReveal(double At);
    void SetRevealMaterial(UMaterialInstanceDynamic* Material) const;
    void HideAtmosphere();
    void RestoreAtmosphere();
    void CaptureEffects(FMRSceneReport& Report,FVector Eye,double RangeCm,TFunctionRef<double(FVector,FVector)> Quality);
    void RebuildTile(FIntPoint Tile);
    void RenderMeshes(const TArray<FMRSceneMesh>& Meshes,TArray<TWeakObjectPtr<USceneComponent>>& Output,int32 Stencil=1);
    void ConfigureRenderer(UPrimitiveComponent* Component,int32 Stencil,bool Shadow) const;
    void Restore();
};
