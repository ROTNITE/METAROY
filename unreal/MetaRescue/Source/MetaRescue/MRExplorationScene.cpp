#include "MRExplorationScene.h"
#include "MRSceneClip.h"
#include "MRWorld.h"
#include "MRAgent.h"
#include "MRCoreValues.h"
#include "Components/StaticMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/BoxComponent.h"
#include "Components/PostProcessComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "GameFramework/PlayerController.h"
#include "Camera/PlayerCameraManager.h"
#include "Misc/Crc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

using namespace MRCoreValues;
namespace
{
bool TiledSurface(const UProceduralMeshComponent* P)
{
    return P&&(P->ComponentHasTag(TEXT("UrbanPhysicalTerrain"))||P->ComponentHasTag(TEXT("UrbanRoadSurface"))||
        P->ComponentHasTag(TEXT("UrbanFlood"))||P->ComponentHasTag(TEXT("CityLotCover")));
}
bool Visible(const USceneComponent* C){return C&&C->IsRegistered()&&C->IsVisible()&&!C->bHiddenInGame;}
FBox XYCell(const FMRExplorationGrid& Grid,FIntPoint Cell)
{
    const auto P=Grid.Center(Cell);const double R=Grid.GetCellCm()*.5;
    return FBox(P-FVector(R,R,100000),P+FVector(R,R,100000));
}
void DeleteRenderers(TArray<TWeakObjectPtr<USceneComponent>>& Components)
{for(auto& C:Components)if(C.IsValid())C->DestroyComponent();Components.Empty();}
void Append(FProcMeshSection& To,const FProcMeshSection& From,FVector Offset=FVector::ZeroVector)
{
    const uint32 Base=To.ProcVertexBuffer.Num();
    for(auto V:From.ProcVertexBuffer){V.Position-=Offset;To.ProcVertexBuffer.Add(V);To.SectionLocalBox+=V.Position;}
    for(uint32 I:From.ProcIndexBuffer)To.ProcIndexBuffer.Add(Base+I);
    To.bEnableCollision=false;To.bSectionVisible=true;
}
FProcMeshVertex WorldVertex(FProcMeshVertex V,const FTransform& Pose)
{
    V.Position=Pose.TransformPosition(V.Position);
    const auto S=Pose.GetScale3D();
    V.Normal=Pose.TransformVectorNoScale(V.Normal/FVector(FMath::Max(FMath::Abs(S.X),.0001)*FMath::Sign(S.X),
        FMath::Max(FMath::Abs(S.Y),.0001)*FMath::Sign(S.Y),FMath::Max(FMath::Abs(S.Z),.0001)*FMath::Sign(S.Z))).GetSafeNormal();
    V.Tangent.TangentX=Pose.TransformVector(V.Tangent.TangentX).GetSafeNormal();return V;
}
}

FMRExplorationScene::~FMRExplorationScene(){Restore();if(Renderer.IsValid())Renderer->Destroy();}

void FMRExplorationScene::Restore()
{
    if(!Active)return;
    RestoreAtmosphere();
    if(Matte.IsValid())Matte->bEnabled=false;
    World->SetActorHiddenInGame(false);
    for(auto& Agent:World->Agents)if(Agent)Agent->SetActorHiddenInGame(false);
    for(const auto& Target:World->Targets)if(Target.Visual)
        Target.Visual->SetActorHiddenInGame(Target.bRescued||!Target.bAvailabilityReported&&Target.AvailableAt>0);
    Active=false;
}

void FMRExplorationScene::Reset(const FMRExplorationMemory& Knowledge)
{
    Restore();if(Renderer.IsValid())Renderer->Destroy();Renderer.Reset();
    Matte.Reset();NightReveal.Reset();InitialScenery.Empty();InitialGround.Empty();
    SceneryInstances.Empty();DeliveredReplacements.Empty();GroundRenderers.Empty();
    BackdropBuilt=false;RevealDirty=true;LastRevealEpoch=-1;
    Grid=Knowledge.Grid;Sources.Empty();SourceLookup.Empty();SourceBins.Empty();Objects.Empty();Surfaces.Empty();
    Actors.Empty();Effects.Empty();ActorRenderers.Empty();ObjectInstances.Empty();ObjectBatches.Empty();ObjectProcedurals.Empty();
    EffectRenderers.Empty();TerrainRenderers.Empty();Materials.Empty();
    DirtyTiles.Empty();DirtyActors.Empty();DirtyObjects.Empty();EffectsDirty=false;Revision=0;LastSignature=-1;CatalogAt=-1;
    RefreshSources(0);
    CaptureBackdrop();
    TArray<FMRSeenSurface> Owned;
    for(int32 Y=0;Y<Grid.GetHeight();++Y)for(int32 X=0;X<Grid.GetWidth();++X)if(Grid.IsOpen(Grid.Center(FIntPoint(X,Y))))
    {
        FMRSeenSurface Sample;Sample.Cell=FIntPoint(X,Y);Sample.PointCm=Grid.Center(Sample.Cell);
        Sample.Quality=1;Sample.Source=TEXT("owned_base");Sample.ObservedAt=Sample.ReceivedAt=0;Owned.Add(Sample);
    }
    auto Initial=Capture(Knowledge,Owned,World->Base,TEXT("owned_base"),0,[](FVector,FVector){return 1.;},true);
    // Initial construction information belongs to the deployed base. It does
    // not turn the rest of the announced map into observed scenery.
    for(const auto& Cell:Initial->Cells){Surfaces.Add(Cell.Cell.Y*Grid.GetWidth()+Cell.Cell.X,Cell);DirtyTiles.Add(FIntPoint(Cell.Cell.X/8,Cell.Cell.Y/8));}
    for(const auto& Mesh:Initial->Objects)if(Mesh.Mesh.IsValid()||!Mesh.Procedural.IsEmpty())
    {
        Objects.Add(Mesh.Key,{Mesh,0});
        DirtyObjects.Add(Mesh.Key);
    }
}

UMaterialInterface* FMRExplorationScene::RememberMaterial(UMaterialInterface* Source,double At)
{
    if(!Source)return nullptr;
    auto* Dynamic=Cast<UMaterialInstanceDynamic>(Source);
    if(!Dynamic)return Source; // Imported materials are immutable; geometry is copied independently.
    TArray<FMaterialParameterInfo> Info;TArray<FGuid> Ids;FString Key=FString::FromInt(Source->GetUniqueID());
    Source->GetAllScalarParameterInfo(Info,Ids);
    const bool Water=Source->GetMaterial()->GetPathName().Contains(TEXT("M_UrbanFlood"));
    for(const auto& I:Info){if(Water&&I.Name==TEXT("ModelTime"))continue;float V=0;Source->GetScalarParameterValue(I,V);Key+=TEXT("|")+I.Name.ToString()+FString::Printf(TEXT("=%.9g"),V);}
    Info.Empty();Ids.Empty();Source->GetAllVectorParameterInfo(Info,Ids);
    for(const auto& I:Info){FLinearColor V;Source->GetVectorParameterValue(I,V);Key+=TEXT("|")+I.Name.ToString()+V.ToString();}
    Info.Empty();Ids.Empty();Source->GetAllTextureParameterInfo(Info,Ids);
    for(const auto& I:Info){UTexture* V=nullptr;Source->GetTextureParameterValue(I,V);Key+=TEXT("|")+I.Name.ToString()+GetPathNameSafe(V);}
    if(const auto* Old=Materials.Find(Key);Old&&Old->IsValid())return Old->Get();
    auto* Parent=Water?LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/EffectsV3/ExplorationWaterR2/M_RememberedFlood.M_RememberedFlood"),nullptr,LOAD_NoWarn|LOAD_Quiet)
        :Dynamic->Parent.Get();
    if(Water&&!Parent)Parent=LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/EffectsV3/ExplorationEffectsR1/M_RememberedFlood.M_RememberedFlood"));
    if(!Parent)Parent=Dynamic->Parent.Get();
    auto* Frozen=UMaterialInstanceDynamic::Create(Parent,GetTransientPackage());
    Frozen->CopyMaterialUniformParameters(Source);
    Frozen->SetScalarParameterValue(TEXT("SnapshotTime"),At);Frozen->SetScalarParameterValue(TEXT("SnapshotMode"),1);
    if(Materials.Num()>=4096)
    {
        for(auto It=Materials.CreateIterator();It;++It)if(!It.Value().IsValid())It.RemoveCurrent();
        if(Materials.Num()>=4096)Materials.Reset();
    }
    Materials.Add(Key,Frozen);return Frozen;
}

void FMRExplorationScene::RefreshSources(double At)
{
    if(CatalogAt==At)return;CatalogAt=At;
    SurfaceIndex.Reset();
    TArray<UStaticMeshComponent*> Meshes;TArray<UProceduralMeshComponent*> Procedural;
    World->GetComponents(Meshes);World->GetComponents(Procedural);
    uint32 Signature=HashCombineFast(GetTypeHash(Meshes.Num()),GetTypeHash(Procedural.Num()));
    for(const auto* Mesh:Meshes)if(const auto* Batch=Cast<UInstancedStaticMeshComponent>(Mesh))
        Signature=HashCombineFast(Signature,HashCombineFast(Mesh->GetUniqueID(),GetTypeHash(Batch->GetInstanceCount())));
    bool Reindex=int32(Signature)!=LastSignature;
    if(Reindex)
    {
        LastSignature=int32(Signature);for(auto& Source:Sources)Source.Alive=false;
        auto Add=[&](UStaticMeshComponent* Mesh,UProceduralMeshComponent* Proc,int32 Instance,FTransform Pose)
        {
            if((Mesh&&(!Mesh->GetStaticMesh()||Mesh->ComponentHasTag(TEXT("CityVisualHorizon"))||Mesh->ComponentHasTag(TEXT("PhysicalSmoke"))))||
                (Proc&&!Proc->IsRegistered()))return;
            const auto* C=Mesh?static_cast<UPrimitiveComponent*>(Mesh):static_cast<UPrimitiveComponent*>(Proc);
            const FString Identity=FString::FromInt(C->GetUniqueID())+(Instance==INDEX_NONE?TEXT("/single"):TEXT("/")+Pose.ToString());
            int32 Index;
            if(const auto* Old=SourceLookup.Find(Identity))Index=*Old;
            else {Index=Sources.AddDefaulted();SourceLookup.Add(Identity,Index);Sources[Index].Key=uint64(Index)+1;}
            auto& Source=Sources[Index];Source.Mesh=Mesh;Source.Procedural=Proc;Source.Instance=Instance;Source.Pose=Pose;Source.Alive=true;
            Source.Bounds=Mesh?Mesh->GetStaticMesh()->GetBoundingBox().TransformBy(Pose):C->Bounds.GetBox();
        };
        for(auto* Mesh:Meshes)
        {
            if(auto* Batch=Cast<UInstancedStaticMeshComponent>(Mesh))
                for(int32 I=0;I<Batch->GetInstanceCount();++I){FTransform Pose;if(Batch->GetInstanceTransform(I,Pose,true))Add(Mesh,nullptr,I,Pose);}
            else Add(Mesh,nullptr,INDEX_NONE,Mesh->GetComponentTransform());
        }
        for(auto* Proc:Procedural)Add(nullptr,Proc,INDEX_NONE,Proc->GetComponentTransform());
    }
    else for(auto& Source:Sources)
    {
        auto* C=Source.Mesh.IsValid()?static_cast<UPrimitiveComponent*>(Source.Mesh.Get()):static_cast<UPrimitiveComponent*>(Source.Procedural.Get());
        if(Source.Instance==INDEX_NONE&&C&&C->Mobility==EComponentMobility::Movable)
        {
            const auto Pose=C->GetComponentTransform();const auto Bounds=C->Bounds.GetBox();
            if(!Pose.Equals(Source.Pose)||!Bounds.Equals(Source.Bounds)){Source.Pose=Pose;Source.Bounds=Bounds;Reindex=true;}
        }
    }
    if(!Reindex)return;
    SourceBins.Empty();
    for(int32 I=0;I<Sources.Num();++I)
    {
        const auto& Bounds=Sources[I].Bounds;if(!Bounds.IsValid)continue;
        const auto A=Grid.CellAt(Bounds.Min),B=Grid.CellAt(Bounds.Max-FVector(.01,.01,0));
        for(int32 Y=FMath::Max(0,A.Y);Y<=FMath::Min(Grid.GetHeight()-1,B.Y);++Y)
            for(int32 X=FMath::Max(0,A.X);X<=FMath::Min(Grid.GetWidth()-1,B.X);++X)SourceBins.FindOrAdd(FIntPoint(X,Y)).Add(I);
    }
}

const TArray<FMRExplorationScene::FIndexedSurface>& FMRExplorationScene::IndexSurface(
    const FSource& Source,UProceduralMeshComponent* Mesh)
{
    if(const auto* Existing=SurfaceIndex.Find(Source.Key))return *Existing;
    auto& Sections=SurfaceIndex.Add(Source.Key);
    Sections.SetNum(Mesh->GetNumSections());
    for(int32 Index=0;Index<Sections.Num();++Index)
    {
        const auto* Geometry=Mesh->GetProcMeshSection(Index);
        if(!Geometry||!Geometry->bSectionVisible)continue;
        auto& Result=Sections[Index];
        Result.Vertices.Reserve(Geometry->ProcVertexBuffer.Num());
        for(const auto& Vertex:Geometry->ProcVertexBuffer)Result.Vertices.Add(WorldVertex(Vertex,Source.Pose));
        Result.TriangleBounds.Reserve(Geometry->ProcIndexBuffer.Num()/3);
        for(int32 Offset=0;Offset<Geometry->ProcIndexBuffer.Num();Offset+=3)
        {
            FBox Bounds(ForceInit);
            for(int32 K=0;K<3;++K)Bounds+=Result.Vertices[Geometry->ProcIndexBuffer[Offset+K]].Position;
            Result.TriangleBounds.Add(Bounds);
            const auto Low=Grid.CellAt(Bounds.Min-FVector(.001)),High=Grid.CellAt(Bounds.Max+FVector(.001));
            for(int32 Y=FMath::Max(0,Low.Y);Y<=FMath::Min(Grid.GetHeight()-1,High.Y);++Y)
                for(int32 X=FMath::Max(0,Low.X);X<=FMath::Min(Grid.GetWidth()-1,High.X);++X)
                    Result.TrianglesByCell.FindOrAdd(FIntPoint(X,Y)).Add(Offset);
        }
    }
    return Sections;
}

FMRSceneMesh FMRExplorationScene::ReadMesh(const FSource& Source,double At)
{
    FMRSceneMesh Result;Result.Key=Source.Key;Result.Bounds=Source.Bounds;Result.Pose=Source.Pose;
    auto* Mesh=Source.Mesh.Get();auto* Proc=Source.Procedural.Get();
    if(!Source.Alive||!Visible(Mesh?static_cast<USceneComponent*>(Mesh):static_cast<USceneComponent*>(Proc)))return Result;
    if(Mesh)
    {
        Result.Mesh=TStrongObjectPtr<UStaticMesh>(Mesh->GetStaticMesh());Result.Shadow=Mesh->CastShadow;
        for(int32 I=0;I<Mesh->GetNumMaterials();++I)Result.Materials.Emplace(RememberMaterial(Mesh->GetMaterial(I),At));
    }
    if(Proc)for(int32 S=0;S<Proc->GetNumSections();++S)
    {
        const auto* Section=Proc->GetProcMeshSection(S);if(!Section||!Section->bSectionVisible||Section->ProcIndexBuffer.IsEmpty())continue;
        FMRSceneSection Copy;Copy.Material=TStrongObjectPtr<UMaterialInterface>(RememberMaterial(Proc->GetMaterial(S),At));
        Copy.Geometry=*Section;Copy.Geometry.SectionLocalBox=FBox(ForceInit);Copy.Geometry.bEnableCollision=false;
        for(auto& V:Copy.Geometry.ProcVertexBuffer){V=WorldVertex(V,Source.Pose);Copy.Geometry.SectionLocalBox+=V.Position;}
        if(Source.Pose.GetDeterminant()<0)for(int32 I=0;I<Copy.Geometry.ProcIndexBuffer.Num();I+=3)Swap(Copy.Geometry.ProcIndexBuffer[I+1],Copy.Geometry.ProcIndexBuffer[I+2]);
        Result.Procedural.Add(MoveTemp(Copy));
    }
    return Result;
}

TSharedPtr<FMRSceneReport> FMRExplorationScene::Capture(const FMRExplorationMemory& Knowledge,
    const TArray<FMRSeenSurface>& Cells,FVector Eye,const FString& SourceId,double At,
    TFunctionRef<double(FVector,FVector)> Quality,bool Owned,double RangeCm,bool OpticalAccept)
{
    RefreshSources(At);auto Report=MakeShared<FMRSceneReport>();Report->ObservedAt=At;Report->Source=SourceId;
    TSet<FIntPoint> Observed;TSet<int32> Candidates;
    FCollisionQueryParams Sight(SCENE_QUERY_STAT(MRRememberedGeometry),false);
    for(const auto& A:World->Agents)Sight.AddIgnoredActor(A);
    for(const auto& T:World->Targets)if(T.Visual)Sight.AddIgnoredActor(T.Visual);
    for(const auto& Sample:Cells)
    {
        Observed.Add(Sample.Cell);FMRSceneCell Cell;Cell.Cell=Sample.Cell;Cell.ObservedAt=At;
        const auto* Nearby=SourceBins.Find(Sample.Cell);
        uint32 VisibleSupport=Sample.SurfaceComponent;
        if(!Owned&&!Sample.Water&&Sample.Normal.Z>.4&&Nearby)
            for(int32 Index:*Nearby)
            {
                auto* Primary=Sources[Index].Mesh.Get();if(!Primary||Primary->GetUniqueID()!=Sample.SurfaceComponent)continue;
                // Imported road pieces sit immediately over the tessellated
                // road. Capture that same visible support level when the whole
                // module spans cells that have not all been opened yet. A five
                // centimetre limit cannot reveal soil beneath a roof or a pier.
                auto Query=Sight;Query.AddIgnoredComponent(Primary);FHitResult Support;
                if(World->GetWorld()->LineTraceSingleByChannel(Support,Sample.PointCm+FVector(0,0,.5),
                    Sample.PointCm-FVector(0,0,5),ECC_Visibility,Query)&&Support.ImpactNormal.Z>.4&&
                    FMath::Abs(Support.ImpactPoint.Z-Sample.PointCm.Z)<=5)
                    VisibleSupport=Support.GetComponent()?Support.GetComponent()->GetUniqueID():VisibleSupport;
                break;
            }
        if(Nearby)for(int32 Index:*Nearby)
        {
            const auto& Item=Sources[Index];auto* Proc=Item.Procedural.Get();
            if(!TiledSurface(Proc)){Candidates.Add(Index);continue;}
            if(!Visible(Proc))continue;
            const bool Water=Proc->ComponentHasTag(TEXT("UrbanFlood"));
            if(!Owned&&(Sample.Water!=Water||!Sample.Water&&VisibleSupport!=Proc->GetUniqueID()&&
                (!Proc->ComponentHasTag(TEXT("CityLotCover"))||Sample.Normal.Z<.4)))continue;
            const auto Box=XYCell(Grid,Sample.Cell);
            const auto& Indexed=IndexSurface(Item,Proc);
            for(int32 S=0;S<Proc->GetNumSections();++S)
            {
                const auto* Mesh=Proc->GetProcMeshSection(S);if(!Mesh||!Mesh->bSectionVisible)continue;
                const auto& Geometry=Indexed[S];
                const auto* Triangles=Geometry.TrianglesByCell.Find(Sample.Cell);
                if(!Triangles)continue;
                FMRSceneSection Section;Section.Material=TStrongObjectPtr<UMaterialInterface>(RememberMaterial(Proc->GetMaterial(S),At));
                for(const int32 I:*Triangles)
                {
                    const auto& Bounds=Geometry.TriangleBounds[I/3];
                    if(!Bounds.IntersectXY(Box))continue;
                    if(!Owned&&Proc->ComponentHasTag(TEXT("CityLotCover"))&&
                        (Bounds.Min.Z>Sample.PointCm.Z+20||Bounds.Max.Z<Sample.PointCm.Z-20))continue;
                    FProcMeshVertex V[3];for(int32 K=0;K<3;++K)
                    {V[K]=Geometry.Vertices[Mesh->ProcIndexBuffer[I+K]];if(Water)V[K].UV3.X=At;}
                    if(Item.Pose.GetDeterminant()<0)Swap(V[1],V[2]);
                    MRSceneClip::Triangle(V[0],V[1],V[2],Box,Section.Geometry);
                }
                if(!Section.Geometry.ProcIndexBuffer.IsEmpty())
                {Cell.Surface.Add(MoveTemp(Section));Cell.SurfaceVisible=true;Cell.SolidSurface|=!Water;}
            }
        }
        Report->Cells.Add(MoveTemp(Cell));
    }
    for(int32 Index:Candidates)
    {
        const auto& Item=Sources[Index];const auto A=Grid.CellAt(Item.Bounds.Min),B=Grid.CellAt(Item.Bounds.Max-FVector(.01,.01,0));
        bool Whole=true;
        for(int32 Y=A.Y;Y<=B.Y&&Whole;++Y)for(int32 X=A.X;X<=B.X;++X)
            if(!Grid.IsValid(FIntPoint(X,Y))||!Knowledge.Grid.IsOpen(Grid.Center(FIntPoint(X,Y)))&&!Observed.Contains(FIntPoint(X,Y)))
                {Whole=false;break;}
        if(!Whole)continue;
        // A cornice can cover the nearest box corner while most of the actual
        // facade remains visible. Sample the real part's face centres too.
        TArray<FVector,TInlineAllocator<8>> Points;const auto Center=Item.Bounds.GetCenter(),Half=Item.Bounds.GetExtent();
        Points.Add(Item.Bounds.GetClosestPointTo(Eye));Points.Add(Center);
        for(int32 Axis=0;Axis<3;++Axis)for(double Sign:{-1.,1.})
        {FVector P=Center;P[Axis]+=Sign*Half[Axis]*.98;Points.Add(P);}
        bool Seen=Owned;
        for(const auto& End:Points)
        {
            if(Seen)break;if(RangeCm>0&&FVector::DistSquared2D(Eye,End)>RangeCm*RangeCm)continue;
            FHitResult Hit;const bool Blocked=World->GetWorld()->LineTraceSingleByChannel(Hit,Eye,End,ECC_Visibility,Sight);
            const bool Same=Item.Alive&&Blocked&&(Hit.GetComponent()==Item.Mesh.Get()||Hit.GetComponent()==Item.Procedural.Get())&&
                (Item.Instance==INDEX_NONE||Hit.Item==Item.Instance);
            if((!Blocked||Same||Hit.Distance+3>=FVector::Dist(Eye,End))&&Quality(Eye,Blocked?Hit.ImpactPoint:End)>=.15)Seen=true;
        }
        if(!Seen)continue;
        Report->Objects.Add(ReadMesh(Item,At));
    }
    if(!Owned&&OpticalAccept&&RangeCm>0)CaptureEffects(*Report,Eye,RangeCm,Quality);
    return Report;
}

void FMRExplorationScene::Apply(const FMRSceneReport& Report,const FMRExplorationMemory& Knowledge)
{
    bool Changed=false;
    for(const auto& Cell:Report.Cells)
    {
        const auto* Accepted=Knowledge.Last(Cell.Cell);
        if(!Accepted||Accepted->ObservedAt!=Report.ObservedAt||Accepted->Source!=Report.Source)continue;
        if(!Cell.SurfaceVisible)continue;
        const int32 Key=Cell.Cell.Y*Grid.GetWidth()+Cell.Cell.X;
        if(const auto* Old=Surfaces.Find(Key);Old&&Old->ObservedAt>=Cell.ObservedAt)continue;
        Surfaces.Add(Key,Cell);DirtyTiles.Add(FIntPoint(Cell.Cell.X/8,Cell.Cell.Y/8));RevealDirty=Changed=true;
    }
    for(const auto& Mesh:Report.Objects)
    {
        const auto A=Grid.CellAt(Mesh.Bounds.Min),B=Grid.CellAt(Mesh.Bounds.Max-FVector(.01,.01,0));
        bool Fresh=true,AcceptedCell=false;
        for(int32 Y=A.Y;Y<=B.Y&&Fresh;++Y)for(int32 X=A.X;X<=B.X;++X)
            if(const auto* Sample=Knowledge.Last(FIntPoint(X,Y)))
            {
                if(Sample->ObservedAt>Report.ObservedAt){Fresh=false;break;}
                AcceptedCell|=Sample->ObservedAt==Report.ObservedAt&&Sample->Source==Report.Source;
            }
        if(!Fresh||!AcceptedCell)continue;
        if(const auto* Previous=Objects.Find(Mesh.Key);Previous&&Previous->At>=Report.ObservedAt)continue;
        // Objects are captured only inside the packet footprint or previously
        // opened cells; delivery is still mandatory for every changed part.
        if(BackdropBuilt&&SceneryInstances.Contains(Mesh.Key))DeliveredReplacements.Add(Mesh.Key);
        auto& Stored=Objects.FindOrAdd(Mesh.Key);
        const bool Same=Stored.Value.Mesh.Get()==Mesh.Mesh.Get()&&Stored.Value.Pose.Equals(Mesh.Pose)&&
            Stored.Value.Materials.Num()==Mesh.Materials.Num()&&Stored.Value.Procedural.IsEmpty()&&Mesh.Procedural.IsEmpty();
        bool SameMaterials=Same;
        if(Same)for(int32 I=0;I<Mesh.Materials.Num();++I)if(Stored.Value.Materials[I].Get()!=Mesh.Materials[I].Get()){SameMaterials=false;break;}
        Stored={Mesh,Report.ObservedAt};if(!SameMaterials){DirtyObjects.Add(Mesh.Key);Changed=true;}
    }
    for(const auto& Effect:Report.Effects)
    {
        const auto* Previous=Effects.Find(Effect.Id);if(Previous&&Previous->ObservedAt>=Effect.ObservedAt)continue;
        const bool Present=Effect.Visual.Mesh.IsValid()||!Effect.Visual.Procedural.IsEmpty();
        if(!Present){if(Previous){Effects.Remove(Effect.Id);EffectsDirty=Changed=true;}continue;}
        Effects.Add(Effect.Id,Effect);EffectsDirty=Changed=true;
    }
    if(Changed)++Revision;
}

FMRSceneActorState FMRExplorationScene::ReadActor(AActor* Actor,const FString& Id,double At,bool Erase)
{
    FMRSceneActorState Result;Result.Id=Id;Result.ObservedAt=At;if(!Actor||Erase)return Result;
    TArray<UStaticMeshComponent*> Meshes;TArray<UProceduralMeshComponent*> Proc;Actor->GetComponents(Meshes);Actor->GetComponents(Proc);
    for(auto* Mesh:Meshes)if(Visible(Mesh)&&Mesh->GetStaticMesh())
    {FSource Source;Source.Mesh=Mesh;Source.Pose=Mesh->GetComponentTransform();Source.Bounds=Mesh->Bounds.GetBox();Result.Meshes.Add(ReadMesh(Source,At));}
    for(auto* Mesh:Proc)if(Visible(Mesh))
    {FSource Source;Source.Procedural=Mesh;Source.Pose=Mesh->GetComponentTransform();Source.Bounds=Mesh->Bounds.GetBox();Result.Meshes.Add(ReadMesh(Source,At));}
    return Result;
}

TSharedPtr<FMRSceneActorReport> FMRExplorationScene::CaptureActor(AMRAgent* Agent,double At)
{
    auto Report=MakeShared<FMRSceneActorReport>();
    Report->Actors.Add(ReadActor(Agent,Agent->AgentKey(),At));
    // Cargo keeps its original identity. Receiving a new carrier pose moves
    // that one remembered casualty; it cannot leave a duplicate on the water.
    for(const auto& Target:World->Targets)if(Target.SupportCarrier.Get()==Agent)
        Report->Actors.Add(ReadActor(Target.Visual,FString::Printf(TEXT("person-%d"),Target.Id),At));
    return Report;
}
TSharedPtr<FMRSceneActorReport> FMRExplorationScene::CaptureTarget(int32 Index,double At)
{
    auto Report=MakeShared<FMRSceneActorReport>();if(!World->Targets.IsValidIndex(Index))return Report;
    const auto& Target=World->Targets[Index];
    Report->Actors.Add(ReadActor(Target.Visual,FString::Printf(TEXT("person-%d"),Target.Id),At,Target.bRescued));return Report;
}
void FMRExplorationScene::ApplyActors(const TSharedPtr<FMRSceneActorReport>& Report)
{
    if(!Report)return;
    bool Changed=false;
    for(const auto& Item:Report->Actors)
    {
        const auto* Previous=Actors.Find(Item.Id);if(Previous&&Previous->ObservedAt>=Item.ObservedAt)continue;
        Actors.Add(Item.Id,Item);DirtyActors.Add(Item.Id);Changed=true;
    }
    if(Changed)++Revision;
}

void FMRExplorationScene::ConfigureRenderer(UPrimitiveComponent* Component,int32 Stencil,bool Shadow) const
{
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Component->SetCanEverAffectNavigation(false);
    Component->SetCastShadow(Stencil!=0&&Shadow);
    Component->SetCastHiddenShadow(false);
    Component->SetAffectDistanceFieldLighting(false);
    Component->SetVisibleInRayTracing(false);
    Component->bVisibleInReflectionCaptures=false;
    Component->bVisibleInRealTimeSkyCaptures=false;
    Component->SetLightingChannels(false,true,false);
    Component->SetRenderCustomDepth(Stencil!=0);
    if(Stencil!=0)Component->SetCustomDepthStencilValue(Stencil);
}

void FMRExplorationScene::RenderMeshes(const TArray<FMRSceneMesh>& Meshes,
    TArray<TWeakObjectPtr<USceneComponent>>& Output,int32 Stencil)
{
    DeleteRenderers(Output);if(!Renderer.IsValid())return;auto* Actor=Renderer.Get();
    TMap<FString,UHierarchicalInstancedStaticMeshComponent*> Groups;
    for(const auto& Source:Meshes)
    {
        if(auto* Mesh=Source.Mesh.Get())
        {
            FString Key=FString::FromInt(Mesh->GetUniqueID())+(Source.Shadow?TEXT("/1"):TEXT("/0"));
            for(const auto& M:Source.Materials)Key+=TEXT("/")+FString::FromInt(M.IsValid()?M->GetUniqueID():0);
            auto* Group=Groups.FindRef(Key);
            if(!Group)
            {
                Group=NewObject<UHierarchicalInstancedStaticMeshComponent>(Actor);Actor->AddInstanceComponent(Group);
                Group->SetMobility(EComponentMobility::Movable);Group->SetStaticMesh(Mesh);
                ConfigureRenderer(Group,Stencil,Source.Shadow);
                Group->bAutoRebuildTreeOnInstanceChanges=false;
                for(int32 I=0;I<Source.Materials.Num();++I)Group->SetMaterial(I,Source.Materials[I].Get());
                Group->RegisterComponent();Groups.Add(Key,Group);Output.Add(Group);
            }
            Group->AddInstance(Source.Pose,true);
        }
        if(!Source.Procedural.IsEmpty())
        {
            auto* Proc=NewObject<UProceduralMeshComponent>(Actor);Actor->AddInstanceComponent(Proc);
            ConfigureRenderer(Proc,Stencil,Source.Shadow);Proc->RegisterComponent();
            for(int32 S=0;S<Source.Procedural.Num();++S)
            {Proc->SetProcMeshSection(S,Source.Procedural[S].Geometry);Proc->SetMaterial(S,Source.Procedural[S].Material.Get());}
            Output.Add(Proc);
        }
    }
    for(auto& Pair:Groups){Pair.Value->bAutoRebuildTreeOnInstanceChanges=true;Pair.Value->BuildTreeIfOutdated(false,true);}
}

void FMRExplorationScene::UpdateObjectRenderers()
{
    auto* Actor=Renderer.Get();
    TMap<UInstancedStaticMeshComponent*,TArray<FPrimitiveInstanceId>> Removed;
    TMap<UInstancedStaticMeshComponent*,TArray<const FMRSceneMesh*>> Added;
    for(uint64 Id:DirtyObjects)
    {
        FInitialInstance Old;
        if(ObjectInstances.RemoveAndCopyValue(Id,Old)&&Old.Batch.IsValid())
            Removed.FindOrAdd(Old.Batch.Get()).Add(Old.Id);
        auto* Previous=ObjectProcedurals.FindRef(Id).Get();
        if(Previous){Previous->DestroyComponent();ObjectProcedurals.Remove(Id);}
        const auto& Source=Objects.FindChecked(Id).Value;
        if(auto* Mesh=Source.Mesh.Get())
        {
            const auto Cell=Grid.CellAt(Source.Bounds.GetCenter());
            FString Key=FString::Printf(TEXT("%d/%d/%d/%d"),Mesh->GetUniqueID(),Cell.X/16,Cell.Y/16,Source.Shadow?1:0);
            for(const auto& Material:Source.Materials)Key+=TEXT("/")+FString::FromInt(Material.IsValid()?Material->GetUniqueID():0);
            auto* Batch=ObjectBatches.FindRef(Key).Get();
            if(!Batch)
            {
                Batch=NewObject<UInstancedStaticMeshComponent>(Actor);Actor->AddInstanceComponent(Batch);
                Batch->SetMobility(EComponentMobility::Movable);Batch->SetStaticMesh(Mesh);
                ConfigureRenderer(Batch,2,Source.Shadow);
                for(int32 Slot=0;Slot<Source.Materials.Num();++Slot)Batch->SetMaterial(Slot,Source.Materials[Slot].Get());
                ObjectBatches.Add(Key,Batch);
            }
            Added.FindOrAdd(Batch).Add(&Source);
        }
        if(!Source.Procedural.IsEmpty())
        {
            auto* Proc=NewObject<UProceduralMeshComponent>(Actor);Actor->AddInstanceComponent(Proc);
            ConfigureRenderer(Proc,2,Source.Shadow);
            for(int32 Slot=0;Slot<Source.Procedural.Num();++Slot)
            {Proc->SetProcMeshSection(Slot,Source.Procedural[Slot].Geometry);Proc->SetMaterial(Slot,Source.Procedural[Slot].Material.Get());}
            Proc->RegisterComponent();ObjectProcedurals.Add(Id,Proc);
        }
    }
    for(const auto& Batch:Removed)Batch.Key->RemoveInstancesById(Batch.Value,false);
    for(const auto& Batch:Added)
    {
        TArray<FTransform> Poses;Poses.Reserve(Batch.Value.Num());
        for(const auto* Source:Batch.Value)Poses.Add(Source->Pose);
        const auto Ids=Batch.Key->AddInstancesById(Poses,true,false);
        for(int32 Index=0;Index<Ids.Num();++Index)ObjectInstances.Add(Batch.Value[Index]->Key,{Batch.Key,Ids[Index]});
        if(!Batch.Key->IsRegistered())Batch.Key->RegisterComponent();
    }
    for(auto It=ObjectBatches.CreateIterator();It;++It)
    {
        auto* Batch=It.Value().Get();
        if(Batch&&Batch->GetInstanceCount()>0)continue;
        if(Batch)Batch->DestroyComponent();
        It.RemoveCurrent();
    }
    DirtyObjects.Reset();
}

void FMRExplorationScene::UpdateActorRenderers(const FMRSceneActorState& State)
{
    auto& Output=ActorRenderers.FindOrAdd(State.Id);
    if(State.Meshes.ContainsByPredicate([](const auto& Part){return !Part.Mesh.IsValid()||!Part.Procedural.IsEmpty();}))
    {RenderMeshes(State.Meshes,Output,2);return;}
    if(Output.Num()!=State.Meshes.Num()||Output.ContainsByPredicate([](const auto& Part)
        {return !Cast<UStaticMeshComponent>(Part.Get())||Cast<UInstancedStaticMeshComponent>(Part.Get());}))
        DeleteRenderers(Output);
    for(int32 Index=0;Index<State.Meshes.Num();++Index)
    {
        const auto& Part=State.Meshes[Index];
        UStaticMeshComponent* Instance=nullptr;
        if(Output.IsValidIndex(Index))Instance=CastChecked<UStaticMeshComponent>(Output[Index].Get());
        else
        {
            Instance=NewObject<UStaticMeshComponent>(Renderer.Get());
            Renderer->AddInstanceComponent(Instance);Instance->SetupAttachment(Renderer->GetRootComponent());
            Instance->SetMobility(EComponentMobility::Movable);
            ConfigureRenderer(Instance,2,Part.Shadow);Output.Add(Instance);
        }
        if(Instance->GetStaticMesh()!=Part.Mesh.Get())Instance->SetStaticMesh(Part.Mesh.Get());
        if(Instance->CastShadow!=Part.Shadow)Instance->SetCastShadow(Part.Shadow);
        for(int32 Slot=0;Slot<Part.Materials.Num();++Slot)
            if(Instance->GetMaterial(Slot)!=Part.Materials[Slot].Get())Instance->SetMaterial(Slot,Part.Materials[Slot].Get());
        Instance->SetWorldTransform(Part.Pose,false,nullptr,ETeleportType::TeleportPhysics);
        if(!Instance->IsRegistered())Instance->RegisterComponent();
    }
}
void FMRExplorationScene::RebuildTile(FIntPoint Tile)
{
    if(!Renderer.IsValid())return;
    auto* Proc=TerrainRenderers.FindRef(Tile).Get();
    if(!Proc)
    {
        Proc=NewObject<UProceduralMeshComponent>(Renderer.Get());Renderer->AddInstanceComponent(Proc);
        ConfigureRenderer(Proc,1,false);Proc->RegisterComponent();TerrainRenderers.Add(Tile,Proc);
    }
    Proc->ClearAllMeshSections();TMap<UMaterialInterface*,FProcMeshSection> Sections;
    const FVector Origin=Grid.Center(FIntPoint(Tile.X*8,Tile.Y*8));Proc->SetWorldLocation(Origin);
    for(int32 Y=Tile.Y*8;Y<FMath::Min(Grid.GetHeight(),Tile.Y*8+8);++Y)
        for(int32 X=Tile.X*8;X<FMath::Min(Grid.GetWidth(),Tile.X*8+8);++X)
            if(const auto* Cell=Surfaces.Find(Y*Grid.GetWidth()+X))
                for(const auto& Part:Cell->Surface)Append(Sections.FindOrAdd(Part.Material.Get()),Part.Geometry,Origin);
    int32 Index=0;for(const auto& Section:Sections)
    {Proc->SetProcMeshSection(Index,Section.Value);Proc->SetMaterial(Index,Section.Key);++Index;}
}

void FMRExplorationScene::View(bool KnowledgeView,double At)
{
    if(KnowledgeView!=Active)if(auto* PC=World->GetWorld()->GetFirstPlayerController())
        if(PC->PlayerCameraManager)PC->PlayerCameraManager->SetGameCameraCutThisFrame();
    if(!KnowledgeView){Restore();if(Renderer.IsValid())Renderer->SetActorHiddenInGame(true);return;}
    if(!Renderer.IsValid())
    {
        auto* Actor=World->GetWorld()->SpawnActor<AActor>();Actor->SetActorTickEnabled(false);Actor->SetActorEnableCollision(false);Renderer=Actor;
        auto* Root=NewObject<USceneComponent>(Actor);Actor->SetRootComponent(Root);Actor->AddInstanceComponent(Root);Root->RegisterComponent();
        BuildBackdrop();
    }
    if(!Active)HideAtmosphere();
    Active=true;Renderer->SetActorHiddenInGame(false);World->SetActorHiddenInGame(true);
    if(Matte.IsValid())Matte->bEnabled=true;
    for(auto& Agent:World->Agents)if(Agent)Agent->SetActorHiddenInGame(true);
    for(const auto& Target:World->Targets)if(Target.Visual)Target.Visual->SetActorHiddenInGame(true);
    if(!DirtyObjects.IsEmpty())UpdateObjectRenderers();
    UpdateReveal(At);
    if(!DeliveredReplacements.IsEmpty())ReplaceBackdropParts();
    if(EffectsDirty)
    {TArray<FMRSceneMesh> Meshes;for(const auto& Pair:Effects)Meshes.Add(Pair.Value.Visual);RenderMeshes(Meshes,EffectRenderers,2);EffectsDirty=false;}
    for(const auto& Tile:DirtyTiles)RebuildTile(Tile);DirtyTiles.Empty();
    for(const auto& Id:DirtyActors)if(const auto* State=Actors.Find(Id))UpdateActorRenderers(*State);
    DirtyActors.Empty();
}

TSharedPtr<FJsonObject> FMRExplorationScene::Snapshot() const
{
    int32 Parts=0,Triangles=0;uint32 Geometry=0;
    for(const auto& Pair:Objects)if(Pair.Value.Value.Mesh.IsValid()||!Pair.Value.Value.Procedural.IsEmpty())++Parts;
    for(const auto& Pair:Surfaces)for(const auto& Section:Pair.Value.Surface)
    {
        Triangles+=Section.Geometry.ProcIndexBuffer.Num()/3;
        for(const auto& V:Section.Geometry.ProcVertexBuffer)Geometry=HashCombineFast(Geometry,GetTypeHash(V.Position));
    }
    int32 Smoke=0,Fire=0;uint32 OpticalHash=0;TArray<FString> EffectIds;Effects.GetKeys(EffectIds);EffectIds.Sort();
    static const bool Detailed=FParse::Param(FCommandLine::Get(),TEXT("MROpticalEvidence"));
    TArray<Value> OpticalSamples;
    auto HashMaterial=[&](UMaterialInterface* Material)
    {
        TArray<Value> Parameters;
        if(!Material)return Parameters;
        TArray<FMaterialParameterInfo> Info;TArray<FGuid> Ids;
        Material->GetAllScalarParameterInfo(Info,Ids);
        for(const auto& I:Info)
        {
            float V=0;Material->GetScalarParameterValue(I,V);
            OpticalHash=HashCombineFast(OpticalHash,GetTypeHash(V));
            if(Detailed)Parameters.Add(Arr({Str(I.Name.ToString()),Num(V)}));
        }
        Info.Empty();Ids.Empty();Material->GetAllVectorParameterInfo(Info,Ids);
        for(const auto& I:Info)
        {
            FLinearColor V=FLinearColor::Black;Material->GetVectorParameterValue(I,V);
            OpticalHash=HashCombineFast(OpticalHash,GetTypeHash(V));
            if(Detailed)Parameters.Add(Arr({Str(I.Name.ToString()),Num(V.R),Num(V.G),Num(V.B),Num(V.A)}));
        }
        return Parameters;
    };
    for(const auto& Id:EffectIds)
    {
        const auto& Sample=Effects.FindChecked(Id);Smoke+=Sample.Smoke;Fire+=!Sample.Smoke;
        OpticalHash=HashCombineFast(OpticalHash,FCrc::StrCrc32(*Id));OpticalHash=HashCombineFast(OpticalHash,GetTypeHash(Sample.Focus));
        TArray<Value> MaterialRows,GeometryRows;
        for(const auto& M:Sample.Visual.Materials)
        {
            auto Parameters=HashMaterial(M.Get());
            if(Detailed)MaterialRows.Add(Arr(Parameters));
        }
        for(const auto& Section:Sample.Visual.Procedural)
        {
            auto Parameters=HashMaterial(Section.Material.Get());
            TArray<Value> Vertices,Indices;
            for(const auto& V:Section.Geometry.ProcVertexBuffer)
            {
                OpticalHash=HashCombineFast(OpticalHash,GetTypeHash(V.Position));
                if(Detailed)Vertices.Add(Arr({Point(V.Position),Point(V.Normal),Num(V.UV0.X),Num(V.UV0.Y)}));
            }
            if(Detailed)
            {
                for(uint32 Index:Section.Geometry.ProcIndexBuffer)Indices.Add(Num(Index));
                GeometryRows.Add(Json(Obj({{TEXT("parameters"),Arr(Parameters)},
                    {TEXT("vertices"),Arr(Vertices)},{TEXT("indices"),Arr(Indices)}})));
            }
        }
        if(Detailed)
        {
            const auto& Pose=Sample.Visual.Pose;
            const auto Q=Pose.GetRotation();
            OpticalSamples.Add(Json(Obj({{TEXT("id"),Str(Id)},{TEXT("kind"),Str(Sample.Smoke?TEXT("smoke"):TEXT("flame"))},
                {TEXT("observed_at"),Num(Sample.ObservedAt)},{TEXT("focus_cm"),Point(Sample.Focus)},
                {TEXT("translation_cm"),Point(Pose.GetTranslation())},{TEXT("scale"),Point(Pose.GetScale3D())},
                {TEXT("rotation"),Arr({Num(Q.X),Num(Q.Y),Num(Q.Z),Num(Q.W)})},
                {TEXT("materials"),Arr(MaterialRows)},{TEXT("sections"),Arr(GeometryRows)}})));
        }
    }
    auto Result=Obj({{TEXT("format"),Str(TEXT("metarescue.remembered-scene.v1"))},{TEXT("revision"),Num(Revision)},
        {TEXT("source_parts"),Num(Parts)},{TEXT("surface_cells"),Num(Surfaces.Num())},{TEXT("surface_triangles"),Num(Triangles)},
        {TEXT("surface_geometry_hash"),Str(FString::Printf(TEXT("%08x"),Geometry))},{TEXT("actor_snapshots"),Num(Actors.Num())},
        {TEXT("basis"),Str(TEXT("captured_at_observation_delivered_by_radio"))},
        {TEXT("backdrop_basis"),Str(TEXT("initial_mapped_city_and_delivered_replacements"))},
        {TEXT("backdrop_parts"),Num(InitialScenery.Num())},{TEXT("backdrop_ground_tiles"),Num(InitialGround.Num())},
        {TEXT("full_bounds_required_for_objects"),Bool(true)},{TEXT("optical_effect_samples"),Num(Effects.Num())},
        {TEXT("smoke_samples"),Num(Smoke)},{TEXT("flame_samples"),Num(Fire)},
        {TEXT("optical_effect_hash"),Str(FString::Printf(TEXT("%08x"),OpticalHash))},
        {TEXT("effects_connected"),Bool(true)}});
    if(Detailed)Result->SetArrayField(TEXT("optical_samples"),OpticalSamples);
    return Result;
}
