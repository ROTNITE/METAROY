#pragma once
#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "MRGameMode.generated.h"
UCLASS()
class METARESCUE_API AMRGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    AMRGameMode();
    virtual void BeginPlay() override;
};
