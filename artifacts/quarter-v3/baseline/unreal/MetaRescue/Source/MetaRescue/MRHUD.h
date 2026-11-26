#pragma once
#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "MRHUD.generated.h"
UCLASS()
class METARESCUE_API AMRHUD : public AHUD
{
    GENERATED_BODY()
public: virtual void DrawHUD() override;
};
