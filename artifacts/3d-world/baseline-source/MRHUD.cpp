#include "MRHUD.h"
#include "MRWorld.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"

void AMRHUD::DrawHUD()
{
    Super::DrawHUD();if(!Canvas)return;
    AMRWorld* Mission=nullptr;for(TActorIterator<AMRWorld> It(GetWorld());It;++It){Mission=*It;break;}
    const FLinearColor White(.88f,.94f,.92f,1),Muted(.5f,.65f,.61f,1),Lime(.77f,.94f,.36f,1);
    DrawRect(FLinearColor(.018f,.04f,.035f,.88f),24,24,325,205);
    DrawRect(Lime,24,24,3,205);
    DrawText(TEXT("META / RESCUE"),White,43,39,GEngine->GetLargeFont(),.78f);
    DrawText(TEXT("PHYSICS ENVIRONMENT  /  UE 5.8"),Muted,44,72,GEngine->GetSmallFont(),.9f);
    if(Mission)
    {
        DrawText(FString::Printf(TEXT("%02d FOUND     %02d DELIVERED"),Mission->FoundCount(),Mission->SavedCount()),Lime,44,103,GEngine->GetMediumFont(),1.05f);
        DrawText(FString::Printf(TEXT("TIME %.1fs     CONTACTS %d"),GetWorld()->GetTimeSeconds(),Mission->ContactCount),White,44,138,GEngine->GetSmallFont(),1.f);
        DrawText(Mission->bCollapseReleased?TEXT("COLLAPSE RELEASED / CHAOS ACTIVE"):TEXT("DETACHED SLABS / RELEASE AT 18s"),Muted,44,166,GEngine->GetSmallFont(),.9f);
        DrawText(TEXT("DEMO POLICY / CONTROLLER NOT CONNECTED"),Muted,44,196,GEngine->GetSmallFont(),.75f);
    }
    DrawRect(FLinearColor(.018f,.04f,.035f,.84f),24,Canvas->ClipY-57,Canvas->ClipX-48,33);
    DrawText(TEXT("RMB orbit  |  Wheel zoom  |  WASD pan  |  TAB overview  |  F collapse  |  SPACE pause  |  F9 capture"),White,40,Canvas->ClipY-48,GEngine->GetSmallFont(),.86f);
    if(UGameplayStatics::IsGamePaused(GetWorld()))DrawText(TEXT("PAUSED"),Lime,Canvas->ClipX*.5f-60,35,GEngine->GetLargeFont(),1.f);
}
