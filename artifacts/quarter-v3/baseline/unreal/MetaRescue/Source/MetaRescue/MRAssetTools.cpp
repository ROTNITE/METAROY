#include "MRAssetTools.h"
#include "NiagaraSystem.h"
#include "NiagaraEmitter.h"
#include "NiagaraSpriteRendererProperties.h"
#include "UObject/Package.h"
#include "Misc/PackageName.h"
#if WITH_EDITOR
#include "NiagaraSystemFactoryNew.h"
#include "NiagaraEditorUtilities.h"
#endif

UNiagaraSystem* UMRAssetTools::CreateEffect(const FString& EmitterPath,const FString& PackagePath,UMaterialInterface* Material)
{
#if WITH_EDITOR
    // A package may exist on disk without any loaded objects. Load it before
    // testing FindObject; creating a second object at that path corrupts saves.
    const FString ObjectPath=PackagePath+TEXT(".")+FPackageName::GetLongPackageAssetName(PackagePath);
    auto* System=LoadObject<UNiagaraSystem>(nullptr,*ObjectPath);
    auto* Source=LoadObject<UNiagaraEmitter>(nullptr,*EmitterPath);
    if(!Source||!Material)return nullptr;
    auto* Package=CreatePackage(*PackagePath);
    if(!System)
    {
        System=NewObject<UNiagaraSystem>(Package,*FPackageName::GetLongPackageAssetName(PackagePath),RF_Public|RF_Standalone);
        UNiagaraSystemFactoryNew::InitializeSystem(System,true);
    }
    // A raw emitter handle does not wire its spawn/update nodes into the system
    // graph. Rebuild also repairs older generated assets with empty execution.
    TSet<FGuid> OldHandles;
    for(const auto& Handle:System->GetEmitterHandles())OldHandles.Add(Handle.GetId());
    if(!OldHandles.IsEmpty())System->RemoveEmitterHandlesById(OldHandles);
    FNiagaraEditorUtilities::AddEmitterToSystem(*System,*Source,Source->GetExposedVersion().VersionGuid);
    for(auto& Handle:System->GetEmitterHandles())
        for(auto* Renderer:Handle.GetEmitterData()->GetRenderers())
            if(auto* Sprite=Cast<UNiagaraSpriteRendererProperties>(Renderer))Sprite->Material=Material;
    System->RequestCompile(true);System->WaitForCompilationComplete(true,false);
    System->MarkPackageDirty();return System;
#else
    return nullptr;
#endif
}
