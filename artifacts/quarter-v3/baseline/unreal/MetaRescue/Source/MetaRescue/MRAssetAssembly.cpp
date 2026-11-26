#include "MRAssetAssembly.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Actor.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace
{
TSharedPtr<FJsonObject> Library;
bool bRead = false;
FVector Position(const TArray<TSharedPtr<FJsonValue>>& V)
{ return FVector(V[0]->AsNumber()*100,-V[1]->AsNumber()*100,V[2]->AsNumber()*100); }
}

USceneComponent* MRBuildAssembly(AActor* Owner,USceneComponent* Parent,const FString& Asset,
    const FVector& Location,const FRotator& Rotation,const FVector& Scale,
    bool Collision,bool Movable,TArray<FMRAssemblyPart>& Parts,const FString& Module)
{
    if(!bRead)
    {
        bRead=true;FString Text;
        if(FFileHelper::LoadFileToString(Text,*(FPaths::ProjectContentDir()/TEXT("Scenario/library-v2.json"))))
            FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Library);
    }
    if(!Library){UE_LOG(LogTemp,Error,TEXT("MR v2 library manifest is missing"));return nullptr;}
    TSharedPtr<FJsonObject> Record;
    for(const auto& Row:Library->GetArrayField(TEXT("assets")))
        if(Row->AsObject()->GetStringField(TEXT("id"))==Asset){Record=Row->AsObject();break;}
    if(!Record){UE_LOG(LogTemp,Error,TEXT("MR unknown library assembly %s"),*Asset);return nullptr;}
    auto* Root=NewObject<USceneComponent>(Owner);Owner->AddInstanceComponent(Root);Root->SetupAttachment(Parent);
    Root->SetMobility(Movable?EComponentMobility::Movable:EComponentMobility::Static);
    Root->SetRelativeLocation(Location);Root->SetRelativeRotation(Rotation);Root->SetRelativeScale3D(Scale);Root->RegisterComponent();
    Root->ComponentTags.Add(FName(*(TEXT("MRAsset:")+Asset)));
    TMap<FString,UStaticMeshComponent*> Lookup;
    TArray<TPair<UStaticMeshComponent*,FString>> Parents;
    for(const auto& Row:Record->GetArrayField(TEXT("parts")))
    {
        auto J=Row->AsObject();const FString Name=J->GetStringField(TEXT("object"));
        if(!Module.IsEmpty()&&Name!=Module)continue;
        auto* C=NewObject<UStaticMeshComponent>(Owner);Owner->AddInstanceComponent(C);C->SetupAttachment(Root);
        auto* Mesh=LoadObject<UStaticMesh>(nullptr,*J->GetStringField(TEXT("mesh")));
        if(!Mesh){UE_LOG(LogTemp,Error,TEXT("MR missing library mesh %s/%s"),*Asset,*Name);continue;}
        C->SetStaticMesh(Mesh);C->SetMobility(Movable?EComponentMobility::Movable:EComponentMobility::Static);
        const FString Role=J->GetStringField(TEXT("role"));
        const bool Solid=Collision&&Role!=TEXT("glass")&&Role!=TEXT("beacon");
        C->SetCollisionProfileName(Solid?TEXT("BlockAll"):TEXT("NoCollision"));
        C->SetCollisionEnabled(Solid?ECollisionEnabled::QueryAndPhysics:ECollisionEnabled::NoCollision);
        if(Name.StartsWith(TEXT("window_")))C->SetCollisionResponseToChannel(ECC_Visibility,ECR_Ignore);
        const auto& R=J->GetArrayField(TEXT("rotation_deg"));
        const FVector P=Module.IsEmpty()?Position(J->GetArrayField(TEXT("location_m"))):FVector::ZeroVector;
        C->SetRelativeLocation(P);C->SetRelativeRotation(FRotator(-R[1]->AsNumber(),-R[2]->AsNumber(),R[0]->AsNumber()));
        C->RegisterComponent();C->ComponentTags.Add(FName(*Role));C->ComponentTags.Add(FName(*Name));
        Lookup.Add(Name,C);
        if(Module.IsEmpty()&&J->HasTypedField<EJson::String>(TEXT("parent")))Parents.Add({C,J->GetStringField(TEXT("parent"))});
        FMRAssemblyPart Part;Part.Mesh=C;Part.Rest=C->GetRelativeTransform();Part.Name=Name;Part.Role=Role;
        double V;
        if(J->TryGetNumberField(TEXT("radius_m"),V))Part.Radius=V*100;
        if(J->TryGetNumberField(TEXT("spin"),V))Part.Spin=V;
        if(J->TryGetNumberField(TEXT("side"),V))Part.Side=-V;
        Parts.Add(Part);
    }
    for(const auto& Item:Parents)
        if(auto** P=Lookup.Find(Item.Value))Item.Key->AttachToComponent(*P,FAttachmentTransformRules::KeepRelativeTransform);
    return Root;
}
