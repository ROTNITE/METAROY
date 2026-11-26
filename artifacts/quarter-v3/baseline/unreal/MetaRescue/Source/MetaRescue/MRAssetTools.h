#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MRAssetTools.generated.h"
class UNiagaraSystem;
class UMaterialInterface;

UCLASS()
class METARESCUE_API UMRAssetTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable,Category="Rescue|Asset Preparation")
    static UNiagaraSystem* CreateEffect(const FString& EmitterPath,const FString& PackagePath,UMaterialInterface* Material);
};
