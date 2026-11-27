#include "MRExplorationScene.h"
#include "MRWorld.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/PostProcessComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "RHI.h"
#include "UObject/UObjectIterator.h"

void FMRExplorationScene::HideAtmosphere()
{
    // The command camera uses a fixed light on its own lighting channel.
    for(TActorIterator<ASkyLight> It(World->GetWorld());It;++It)
    {
        auto* Sky=It->GetLightComponent();SkyCaptureStates.Add({Sky,Sky->IsRealTimeCaptureEnabled()});
        AtmosphereVisibility.Add({Sky,Sky->IsVisible()});Sky->SetVisibility(false);
        Sky->SetRealTimeCaptureEnabled(false);
    }
    for(TActorIterator<AExponentialHeightFog> It(World->GetWorld());It;++It)
    {auto* Fog=It->GetComponent();AtmosphereVisibility.Add({Fog,Fog->IsVisible()});Fog->SetVisibility(false);}
    for(TObjectIterator<USkyAtmosphereComponent> It;It;++It)if(It->GetWorld()==World->GetWorld()&&It->IsRegistered())
    {AtmosphereVisibility.Add({*It,It->IsVisible()});It->SetVisibility(false);}
}
void FMRExplorationScene::RestoreAtmosphere()
{
    for(const auto& Pair:AtmosphereVisibility)if(Pair.Key.IsValid())Pair.Key->SetVisibility(Pair.Value);
    for(const auto& Pair:SkyCaptureStates)if(Pair.Key.IsValid())Pair.Key->SetRealTimeCaptureEnabled(Pair.Value);
    AtmosphereVisibility.Empty();SkyCaptureStates.Empty();
}
namespace
{
bool InitialStreetProp(const UStaticMeshComponent* Mesh)
{
    if (Mesh->ComponentHasTag(TEXT("CityStructurePart")) || Mesh->ComponentHasTag(TEXT("CityUtilityShelter")) ||
        Mesh->ComponentHasTag(TEXT("CityInfrastructure")) || Mesh->ComponentHasTag(TEXT("CityStreetMarking")) ||
        Mesh->ComponentHasTag(TEXT("CityTrafficSignal")))
        return true;
    for (const auto& Tag : Mesh->ComponentTags)
    {
        const FString Name=Tag.ToString();
        if (Name.StartsWith(TEXT("CityVegetation:")) || Name.StartsWith(TEXT("CityProp:")))
            return true;
    }
    return false;
}

int32 GroundFinish(const UProceduralMeshComponent* Source,const UMaterialInterface* Material)
{
    const FString Name=GetNameSafe(Material);
    if (Name.Contains(TEXT("Asphalt")) || Name.Contains(TEXT("Service"))) return 1;
    if (Name.Contains(TEXT("Paving")) || Name.Contains(TEXT("Yard"))) return 2;
    if (Source->ComponentHasTag(TEXT("CityLotCover"))) return 3;
    return 0;
}

void AppendGround(FProcMeshSection& Tile,const FProcMeshSection& Part)
{
    const int32 Base=Tile.ProcVertexBuffer.Num();
    for(auto Vertex:Part.ProcVertexBuffer)
    {
        // An unchanged observed surface wins the depth test. Lower surfaces
        // such as an observed cavity use the separate delivered stencil map.
        Vertex.Position.Z-=2.;
        Tile.SectionLocalBox+=Vertex.Position;
        Tile.ProcVertexBuffer.Add(Vertex);
    }
    for(uint32 Index:Part.ProcIndexBuffer)Tile.ProcIndexBuffer.Add(Base+Index);
    Tile.bEnableCollision=false;
    Tile.bSectionVisible=true;
}
}

void FMRExplorationScene::CaptureBackdrop()
{
    // Called once per reset, before the event clock starts. Only construction
    // generated from the initial map is admitted. No actors, water, deposits,
    // light emitters, physically displaced parts, or runtime FX are read here.
    TMap<FIntVector,FProcMeshSection> Tiles;
    for(const auto& Source:Sources)
    {
        if(const auto* Mesh=Source.Mesh.Get();Mesh&&InitialStreetProp(Mesh))
        {
            auto Copy=ReadMesh(Source,0);
            if(Copy.Mesh.IsValid())InitialScenery.Add(MoveTemp(Copy));
        }
        const auto* Proc=Source.Procedural.Get();
        if(!Proc||(!Proc->ComponentHasTag(TEXT("UrbanPhysicalTerrain"))&&!Proc->ComponentHasTag(TEXT("UrbanRoadSurface"))
            &&!Proc->ComponentHasTag(TEXT("CityLotCover"))))continue;
        auto Copy=ReadMesh(Source,0);
        for(auto& Part:Copy.Procedural)
        {
            const FVector Center=Part.Geometry.SectionLocalBox.GetCenter();
            const int32 Finish=GroundFinish(Proc,Part.Material.Get());
            const FIntVector Tile(FMath::FloorToInt(Center.X/8000),FMath::FloorToInt(Center.Y/8000),Finish);
            AppendGround(Tiles.FindOrAdd(Tile),Part.Geometry);
        }
    }
    for(auto& Tile:Tiles)
    {
        auto& Surface=InitialGround.AddDefaulted_GetRef();
        Surface.Key=Tile.Key.Z;
        auto& Part=Surface.Procedural.AddDefaulted_GetRef();
        Part.Geometry=MoveTemp(Tile.Value);
    }
}

void FMRExplorationScene::SetRevealMaterial(UMaterialInstanceDynamic* Material) const
{
    const auto Origin=Grid.GetOriginCm();
    Material->SetVectorParameterValue(TEXT("GridOrigin"),FLinearColor(Origin.X,Origin.Y,0,0));
    Material->SetVectorParameterValue(TEXT("GridCells"),FLinearColor(Grid.GetWidth(),Grid.GetHeight(),0,0));
    Material->SetScalarParameterValue(TEXT("CellSizeCm"),Grid.GetCellCm());
    Material->SetTextureParameterValue(TEXT("DeliveredMask"),NightReveal.Get());
}

void FMRExplorationScene::BuildBackdrop()
{
    auto* Actor=Renderer.Get();
    if(!Actor)return;
    const int32 Width=Grid.GetWidth(),Height=Grid.GetHeight();
    if(Width<1||Height<1)return;
    TArray<FColor> Empty;
    Empty.Init(FColor::Black,Width*Height);
    NightReveal.Reset(UTexture2D::CreateTransient(Width,Height,PF_B8G8R8A8,NAME_None,
        MakeArrayView(reinterpret_cast<const uint8*>(Empty.GetData()),Empty.Num()*sizeof(FColor))));
    if(!NightReveal.IsValid())return;
    NightReveal->SRGB=false;
    NightReveal->NeverStream=true;
    NightReveal->Filter=TF_Bilinear;
    NightReveal->AddressX=NightReveal->AddressY=TA_Clamp;
    NightReveal->UpdateResource();

    auto* Dome=NewObject<UStaticMeshComponent>(Actor);
    Actor->AddInstanceComponent(Dome);
    Dome->SetupAttachment(Actor->GetRootComponent());
    Dome->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Sphere.Sphere")));
    Dome->SetMaterial(0,LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/ExplorationBackdropR2/M_NightSky.M_NightSky")));
    ConfigureRenderer(Dome,0,false);
    const auto Low=Grid.GetOriginCm();
    Dome->SetRelativeLocation(FVector(Low.X+Width*Grid.GetCellCm()*.5,Low.Y+Height*Grid.GetCellCm()*.5,0));
    Dome->SetRelativeScale3D(FVector(FMath::Max(Width,Height)*Grid.GetCellCm()/50*2));
    Dome->RegisterComponent();

    auto* Lamp=NewObject<UDirectionalLightComponent>(Actor);
    Actor->AddInstanceComponent(Lamp);
    Lamp->SetupAttachment(Actor->GetRootComponent());
    Lamp->SetMobility(EComponentMobility::Movable);
    Lamp->SetLightingChannels(false,true,false);
    Lamp->SetIntensity(850);
    Lamp->SetLightColor(FLinearColor(.66f,.79f,1.f),false);
    Lamp->SetRelativeRotation(FRotator(-55,-38,0));
    Lamp->SetCastShadows(false);
    Lamp->SetVolumetricScatteringIntensity(0);
    Lamp->RegisterComponent();
    auto* Fill=NewObject<UDirectionalLightComponent>(Actor);
    Actor->AddInstanceComponent(Fill);
    Fill->SetupAttachment(Actor->GetRootComponent());
    Fill->SetMobility(EComponentMobility::Movable);
    Fill->SetLightingChannels(false,true,false);
    Fill->SetIntensity(360);
    Fill->SetLightColor(FLinearColor(.56f,.67f,.93f),false);
    Fill->SetRelativeRotation(FRotator(-25,140,0));
    Fill->SetCastShadows(false);
    Fill->SetVolumetricScatteringIntensity(0);
    Fill->RegisterComponent();

    auto* Post=NewObject<UPostProcessComponent>(Actor);
    Actor->AddInstanceComponent(Post);
    Post->SetupAttachment(Actor->GetRootComponent());
    Post->bUnbound=true;Post->Priority=100000;Post->BlendWeight=1;
    if(auto* Grade=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/ExplorationBackdropR2/M_KnowledgeGrade.M_KnowledgeGrade")))
    {
        auto* Instance=UMaterialInstanceDynamic::Create(Grade,Post);
        SetRevealMaterial(Instance);
        Post->Settings.AddBlendable(Instance,1);
    }
    auto& Settings=Post->Settings;
    Settings.bOverride_AutoExposureMinBrightness=Settings.bOverride_AutoExposureMaxBrightness=true;
    Settings.AutoExposureMinBrightness=Settings.AutoExposureMaxBrightness=9.0f;
    Settings.bOverride_AutoExposureBias=true;Settings.AutoExposureBias=0;
    Settings.bOverride_LocalExposureHighlightContrastScale=Settings.bOverride_LocalExposureShadowContrastScale=true;
    Settings.LocalExposureHighlightContrastScale=Settings.LocalExposureShadowContrastScale=1;
    Settings.bOverride_ReflectionMethod=Settings.bOverride_DynamicGlobalIlluminationMethod=true;
    Settings.ReflectionMethod=EReflectionMethod::None;
    Settings.DynamicGlobalIlluminationMethod=EDynamicGlobalIlluminationMethod::None;
    Post->RegisterComponent();Matte=Post;

    auto* Ground=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/EffectsV3/ExplorationBackdropR2/M_NightGround.M_NightGround"));
    if(!Ground)
    {
        UE_LOG(LogTemp,Error,TEXT("MR_EXPLORATION night ground material is missing"));
        return;
    }
    const FLinearColor Tints[]={{.025f,.036f,.031f},{.031f,.046f,.061f},{.057f,.063f,.075f},{.022f,.041f,.035f}};
    TStrongObjectPtr<UMaterialInstanceDynamic> Finishes[UE_ARRAY_COUNT(Tints)];
    for(int32 Index=0;Index<UE_ARRAY_COUNT(Tints);++Index)
    {
        Finishes[Index].Reset(UMaterialInstanceDynamic::Create(Ground,Actor));
        SetRevealMaterial(Finishes[Index].Get());
        Finishes[Index]->SetVectorParameterValue(TEXT("NightTint"),Tints[Index]);
    }
    for(auto& Mesh:InitialGround)
        Mesh.Procedural[0].Material.Reset(Finishes[Mesh.Key].Get());
    RenderMeshes(InitialGround,GroundRenderers,0);

    // Stable IDs allow delivery to remove a facade or street part without
    // rebuilding the untouched city or waiting for an asynchronous HISM tree.
    TMap<FString,TArray<const FMRSceneMesh*>> Groups;
    for(const auto& Mesh:InitialScenery)
    {
        if(Objects.Contains(Mesh.Key))continue;
        FString Key=FString::FromInt(Mesh.Mesh->GetUniqueID());
        for(const auto& Material:Mesh.Materials)Key+=TEXT("/")+FString::FromInt(Material.IsValid()?Material->GetUniqueID():0);
        Groups.FindOrAdd(Key).Add(&Mesh);
    }
    for(const auto& Pair:Groups)
    {
        const auto& Parts=Pair.Value;
        auto* Batch=NewObject<UInstancedStaticMeshComponent>(Actor);
        Actor->AddInstanceComponent(Batch);
        Batch->SetMobility(EComponentMobility::Movable);
        Batch->SetStaticMesh(Parts[0]->Mesh.Get());
        for(int32 Index=0;Index<Parts[0]->Materials.Num();++Index)
            Batch->SetMaterial(Index,Parts[0]->Materials[Index].Get());
        ConfigureRenderer(Batch,0,false);
        TArray<FTransform> Poses;
        Poses.Reserve(Parts.Num());
        for(const auto* Part:Parts)Poses.Add(Part->Pose);
        const auto Ids=Batch->AddInstancesById(Poses,true,false);
        for(int32 Index=0;Index<Ids.Num();++Index)SceneryInstances.Add(Parts[Index]->Key,{Batch,Ids[Index]});
        Batch->RegisterComponent();
    }
    BackdropBuilt=true;
}

void FMRExplorationScene::ReplaceBackdropParts()
{
    TMap<UInstancedStaticMeshComponent*,TArray<FPrimitiveInstanceId>> Removals;
    for(uint64 Key:DeliveredReplacements)
    {
        FInitialInstance Instance;
        if(SceneryInstances.RemoveAndCopyValue(Key,Instance)&&Instance.Batch.IsValid())
            Removals.FindOrAdd(Instance.Batch.Get()).Add(Instance.Id);
    }
    for(const auto& Batch:Removals)Batch.Key->RemoveInstancesById(Batch.Value,false);
    DeliveredReplacements.Reset();
}

void FMRExplorationScene::UpdateReveal(double At)
{
    const int32 Epoch=FMath::FloorToInt(At/4.);
    if(!NightReveal.IsValid()||(!RevealDirty&&Epoch==LastRevealEpoch))return;
    LastRevealEpoch=Epoch;RevealDirty=false;
    const int32 Width=Grid.GetWidth(),Height=Grid.GetHeight();
    auto* Pixels=new FColor[Width*Height]();
    for(const auto& Pair:Surfaces)
    {
        const auto& Cell=Pair.Value;
        const double Recent=Cell.ObservedAt==0?1.:1.-FMath::SmoothStep(12.,40.,At-Cell.ObservedAt);
        Pixels[Pair.Key]=FColor(Cell.SolidSurface?255:0,FMath::RoundToInt(Recent*255),255);
    }
    auto* Region=new FUpdateTextureRegion2D(0,0,0,0,Width,Height);
    NightReveal->UpdateTextureRegions(0,1,Region,Width*sizeof(FColor),sizeof(FColor),
        reinterpret_cast<uint8*>(Pixels),[](uint8* Data,const FUpdateTextureRegion2D* Rect)
        {delete[] reinterpret_cast<FColor*>(Data);delete Rect;});
}
