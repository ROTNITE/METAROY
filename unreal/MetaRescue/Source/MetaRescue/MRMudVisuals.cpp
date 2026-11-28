#include "MRWorld.h"
#include "MRMudContour.h"
#include "MRAgent.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "ProceduralMeshComponent.h"

namespace
{
struct FMudBatch
{
    TArray<FVector> Vertices,Normals;
    TArray<FVector2D> UV;
    TArray<FLinearColor> Colors;
    TArray<int32> Indices;
    void Append(const MRMudContour::Polygon& P,const FVector& Origin,bool Fringe)
    {
        if(P.Count<3)return;
        const int32 First=Vertices.Num();
        for(int I=0;I<P.Count;++I)
        {
            const auto& V=P.Points[I];
            const FVector Normal=FVector(V.NX,V.NY,V.NZ).GetSafeNormal();
            // The dark wet edge sits below the opaque mud; both remain
            // render-only and follow the sampled support.
            Vertices.Add(FVector(V.X,V.Y,V.Z+(Fringe?.8:1.6))-Origin);
            Normals.Add(Normal);UV.Emplace(V.X/350.,V.Y/350.);
            Colors.Emplace(0,0,0,Fringe?float(MRMudContour::Smooth(MRMudContour::Outer,MRMudContour::Core,V.Mask)):1.f);
        }
        for(int I=1;I+1<P.Count;++I)Indices.Append({First,First+I,First+I+1});
    }
};
}

void AMRWorld::InvalidateMudGround(const FBox& Bounds)
{
    if(!bDisasterCity||!Environment.bRevisedDisasters)return;
    const FIntPoint Lo=FMRDistrictEnvironment::Cell(Bounds.Min),Hi=FMRDistrictEnvironment::Cell(Bounds.Max);
    for(int32 X=Lo.X;X<=Hi.X;++X)for(int32 Y=Lo.Y;Y<=Hi.Y;++Y)MudVisualMasks.Remove(FIntPoint(X,Y));
    MudVisualRevision=-1;
}

void AMRWorld::RefreshMudSurfaces()
{
    if(MudVisualRevision==Environment.Revision)return;
    MudVisualRevision=Environment.Revision;
    TMap<FIntPoint,FName> Owners;
    for(const auto& Event:Environment.Applied)
        if(Event.Kind==EMRDisturbance::Mud&&Environment.IsActive(Event))
            for(const auto Cell:Event.Cells)
                if(!Owners.Contains(Cell))Owners.Add(Cell,FName(*Event.Id));
    const auto Remove=[&](UProceduralMeshComponent* Mesh)
    {
        if(!Mesh)return;
        HazardVisuals.RemoveSingleSwap(Mesh,EAllowShrinking::No);
        CoreEventComponents.RemoveSingleSwap(Mesh,EAllowShrinking::No);
        Mesh->DestroyComponent();
    };
    for(auto It=MudVisualMeshes.CreateIterator();It;++It)
        if(!Owners.Contains(It.Key())){Remove(It.Value().Get());It.RemoveCurrent();}
    for(auto It=MudVisualMasks.CreateIterator();It;++It)if(!Owners.Contains(It.Key()))It.RemoveCurrent();
    if(Owners.IsEmpty())return;
    static_assert(MRDistrictSettings::CellCm==MRMudContour::Cell,"Mud mask must use authoritative cell size");
    constexpr int32 Steps=10,Side=Steps+1;
    constexpr double Step=MRMudContour::Cell/Steps;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(MRMudSupport),false);
    for(AMRAgent* Agent:Agents)Query.AddIgnoredActor(Agent);
    UMaterialInterface* MudBase=nullptr;UMaterialInterface* FringeBase=nullptr;
    bool MaterialsLoaded=false;
    int32 Rebuilt=0;
    for(const auto& Entry:Owners)
    {
        const FIntPoint Cell=Entry.Key;
        uint16 Mask=0;
        for(int32 Y=-1;Y<=1;++Y)for(int32 X=-1;X<=1;++X)
            if(Owners.Contains(Cell+FIntPoint(X,Y)))Mask|=MRMudContour::Bit(X,Y);
        auto* Existing=MudVisualMeshes.FindRef(Cell).Get();
        if(const auto* Previous=MudVisualMasks.Find(Cell);Previous&&*Previous==Mask)
        {
            if(Existing)Existing->ComponentTags[0]=Entry.Value;
            continue;
        }
        if(Existing){Remove(Existing);MudVisualMeshes.Remove(Cell);}
        MudVisualMasks.Add(Cell,Mask);
        const FVector Origin=FMRDistrictEnvironment::Center(Cell);
        const double X0=Cell.X*MRMudContour::Cell,Y0=Cell.Y*MRMudContour::Cell;
        std::array<MRMudContour::Sample,Side*Side> Samples{};
        std::array<bool,Side*Side> Valid{};
        // Only the few footprints near this cell can exclude ground.
        // Sample corners once, not four separate flat quads per sample.
        TArray<FBox> Buildings;
        const FBox Region(FVector(X0,Y0,-100000),FVector(X0+MRMudContour::Cell,Y0+MRMudContour::Cell,100000));
        for(const auto& Box:StructureFootprints)
            if(Box.ExpandBy(36).IntersectXY(Region))Buildings.Add(Box.ExpandBy(36));
        for(int32 Y=0;Y<=Steps;++Y)for(int32 X=0;X<=Steps;++X)
        {
            const int32 I=Y*Side+X;
            FVector P(X0+X*Step,Y0+Y*Step,0);
            const double Field=MRMudContour::MaskAt(Mask,X*Step,Y*Step,P.X,P.Y);
            if(Buildings.ContainsByPredicate([&](const FBox& Box){return Box.IsInsideXY(P);}))continue;
            P.Z=InitialGroundHeightCm(P);
            FHitResult Hit;
            if(!GetWorld()->LineTraceSingleByChannel(Hit,P+FVector(0,0,60),P-FVector(0,0,400),ECC_Visibility,Query)||
                Hit.ImpactNormal.Z<.8||Hit.ImpactPoint.Z>P.Z+55||Hit.ImpactPoint.Z<P.Z-390)continue;
            Samples[I]={P.X,P.Y,Hit.ImpactPoint.Z,Hit.ImpactNormal.X,Hit.ImpactNormal.Y,Hit.ImpactNormal.Z,Field};
            Valid[I]=true;
        }
        FMudBatch Core,Fringe;
        auto Tri=[&](int32 A,int32 B,int32 C)
        {
            if(!Valid[A]||!Valid[B]||!Valid[C])return;
            const auto& V=Samples[A];const auto& W=Samples[B];const auto& Q=Samples[C];
            if(FMath::Max3(V.Z,W.Z,Q.Z)-FMath::Min3(V.Z,W.Z,Q.Z)>45)return;
            if(FMath::Max3(V.Mask,W.Mask,Q.Mask)<MRMudContour::Outer)return;
            const auto Polygon=MRMudContour::Triangle(V,W,Q);
            Core.Append(MRMudContour::Clip(Polygon,MRMudContour::Core,true),Origin,false);
            // Only the transition band needs translucent pixels.
            Fringe.Append(MRMudContour::Clip(MRMudContour::Clip(Polygon,MRMudContour::Outer,true),MRMudContour::Core,false),Origin,true);
        };
        for(int32 Y=0;Y<Steps;++Y)for(int32 X=0;X<Steps;++X)
        {
            const int32 A=Y*Side+X;
            Tri(A,A+Side,A+1);Tri(A+1,A+Side,A+Side+1);
        }
        if(Core.Indices.IsEmpty()&&Fringe.Indices.IsEmpty())continue;
        if(!MaterialsLoaded)
        {
            MudBase=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/Revision5/M_EventMud.M_EventMud"));
            // Already shipped with revised urban water: vertex-alpha wet
            // tint, no new binary asset required for this visual fix.
            FringeBase=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/WaterR6/M_WetFringe.M_WetFringe"),nullptr,LOAD_NoWarn|LOAD_Quiet);
            MaterialsLoaded=true;
            if(!FringeBase)UE_LOG(LogTemp,Warning,TEXT("MR_MUD_VISUAL wet fringe unavailable; rounded PBR core remains"));
        }
        if(!MudBase)continue;
        auto* Mesh=NewObject<UProceduralMeshComponent>(this);
        AddInstanceComponent(Mesh);Mesh->SetupAttachment(GetRootComponent());
        Mesh->SetMobility(EComponentMobility::Movable);Mesh->SetRelativeLocation(Origin);
        Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);Mesh->SetGenerateOverlapEvents(false);
        Mesh->SetCanEverAffectNavigation(false);Mesh->SetCastShadow(false);Mesh->SetAffectDistanceFieldLighting(false);
        Mesh->SetCullDistance(FMath::Clamp(GridSize*3000.f,65000.f,105000.f));
        Mesh->ComponentTags={Entry.Value,TEXT("mud"),TEXT("OrganicMudSurface"),TEXT("NoMissionCollision")};
        auto* Mud=UMaterialInstanceDynamic::Create(MudBase,Mesh);
        Mud->SetVectorParameterValue(TEXT("MudTint"),FLinearColor(.42f,.32f,.22f));
        Mud->SetScalarParameterValue(TEXT("Wetness"),.6f);Mesh->SetMaterial(0,Mud);
        if(FringeBase)
        {
            auto* Wet=UMaterialInstanceDynamic::Create(FringeBase,Mesh);
            Wet->SetVectorParameterValue(TEXT("WetTint"),FLinearColor(.035f,.022f,.012f,1));
            Mesh->SetMaterial(1,Wet);
        }
        Mesh->RegisterComponent();
        if(!Core.Indices.IsEmpty())Mesh->CreateMeshSection_LinearColor(0,Core.Vertices,Core.Indices,Core.Normals,Core.UV,Core.Colors,{},false,false);
        if(FringeBase&&!Fringe.Indices.IsEmpty())Mesh->CreateMeshSection_LinearColor(1,Fringe.Vertices,Fringe.Indices,Fringe.Normals,Fringe.UV,Fringe.Colors,{},false,false);
        HazardVisuals.Add(Mesh);CoreEventComponents.AddUnique(Mesh);
        MudVisualMeshes.Add(Cell,Mesh);++Rebuilt;
    }
    if(Rebuilt)UE_LOG(LogTemp,Display,TEXT("MR_MUD_VISUAL active_cells=%d rebuilt=%d"),Owners.Num(),Rebuilt);
}
