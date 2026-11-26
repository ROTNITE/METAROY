#include "MRWorld.h"
#include "MRAgent.h"
#include "ProceduralMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/BoxComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInterface.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "HAL/FileManager.h"
#include "UnrealClient.h"

AMRWorld::AMRWorld()
{
    PrimaryActorTick.bCanEverTick=true;
    SetRootComponent(CreateDefaultSubobject<USceneComponent>(TEXT("DistrictRoot")));
    GetRootComponent()->SetMobility(EComponentMobility::Static);
}

bool AMRWorld::ReadScenario()
{
    FString Text;
    const FString File=FPaths::ProjectContentDir()/TEXT("Scenario/scenario.json");
    if(!FFileHelper::LoadFileToString(Text,*File)||!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Scenario)||!Scenario)
    { UE_LOG(LogTemp,Error,TEXT("MR: missing or invalid scenario: %s"),*File);return false; }
    GridSize=Scenario->GetIntegerField(TEXT("width"));Seed=Scenario->GetIntegerField(TEXT("seed"));
    const auto& Rows=Scenario->GetArrayField(TEXT("grid"));
    if(GridSize<1||GridSize>65||Rows.Num()!=GridSize)return false;
    Grid.Empty();
    for(const auto& Row:Rows)
    {
        TArray<int32> Cells;for(const auto& V:Row->AsArray())Cells.Add(int32(V->AsNumber()));
        if(Cells.Num()!=GridSize)return false;Grid.Add(Cells);
    }
    const auto& B=Scenario->GetArrayField(TEXT("base"));
    Base=FVector(B[0]->AsNumber()*1000,B[1]->AsNumber()*1000,0);
    return true;
}

UStaticMeshComponent* AMRWorld::Shape(const FString& Mesh,const FVector& P,const FVector& S,
    const FString& Mat,const FRotator& R,bool Collision)
{
    auto* C=NewObject<UStaticMeshComponent>(this);AddInstanceComponent(C);C->SetupAttachment(GetRootComponent());
    C->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,*(TEXT("/Engine/BasicShapes/")+Mesh+TEXT(".")+Mesh)));
    C->SetMaterial(0,LoadObject<UMaterialInterface>(nullptr,*(TEXT("/Game/Materials/")+Mat+TEXT(".")+Mat)));
    C->SetMobility(EComponentMobility::Static);
    C->SetCollisionEnabled(Collision?ECollisionEnabled::QueryAndPhysics:ECollisionEnabled::NoCollision);
    C->SetRelativeLocation(P);C->SetRelativeRotation(R);C->SetRelativeScale3D(S/100.f);
    C->RegisterComponent();Generated.Add(C);return C;
}

UProceduralMeshComponent* AMRWorld::Chunk(FRandomStream& Rng,const FVector& P,const FVector& S,bool Physics,bool Collapse)
{
    auto* C=NewObject<UProceduralMeshComponent>(this);AddInstanceComponent(C);C->SetupAttachment(GetRootComponent());
    C->SetMobility(Physics?EComponentMobility::Movable:EComponentMobility::Static);
    C->bUseComplexAsSimpleCollision=false;C->bUseAsyncCooking=false;
    C->SetRelativeLocation(P);C->SetRelativeRotation(FRotator(Rng.FRandRange(-24,24),Rng.FRandRange(-180,180),Rng.FRandRange(-22,22)));
    C->SetCollisionProfileName(TEXT("PhysicsActor"));C->RegisterComponent();
    TArray<FVector> Corners;
    for(int32 I=0;I<8;I++)Corners.Add(FVector((I&1?1:-1)*S.X*.5,(I&2?1:-1)*S.Y*.5,(I&4?1:-1)*S.Z*.5)
        +FVector(Rng.FRandRange(-.1,.1)*S.X,Rng.FRandRange(-.1,.1)*S.Y,Rng.FRandRange(-.15,.15)*S.Z));
    const int32 Faces[6][4]={{0,4,6,2},{1,3,7,5},{0,1,5,4},{2,6,7,3},{0,2,3,1},{4,5,7,6}};
    TArray<FVector> V,N;TArray<int32> Tri;TArray<FVector2D> UV;
    for(const auto& Face:Faces)
    {
        const int32 Offset=V.Num();const FVector Normal=FVector::CrossProduct(Corners[Face[1]]-Corners[Face[0]],Corners[Face[2]]-Corners[Face[0]]).GetSafeNormal();
        for(int32 J=0;J<4;J++){V.Add(Corners[Face[J]]);N.Add(Normal);UV.Add(FVector2D(J==1||J==2?1:0,J>=2?1:0));}
        // Unreal uses clockwise front faces; normals remain outward.
        Tri.Append({Offset,Offset+2,Offset+1,Offset,Offset+3,Offset+2});
    }
    C->CreateMeshSection_LinearColor(0,V,Tri,N,UV,TArray<FLinearColor>(),TArray<FProcMeshTangent>(),false);
    C->AddCollisionConvexMesh(Corners);
    C->SetMaterial(0,LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/Materials/MI_Concrete.MI_Concrete")));
    C->SetLinearDamping(.3f);C->SetAngularDamping(.9f);
    C->SetMassOverrideInKg(NAME_None,FMath::Clamp(float(S.X*S.Y*S.Z*.0008),20.f,1400.f));
    Generated.Add(C);
    if(Physics){PhysicsPieces.Add(C);if(Collapse)CollapsePieces.Add(C);}
    return C;
}

void AMRWorld::Building(FRandomStream& Rng,int32 X,int32 Y)
{
    const FVector Center(X*1000,Y*1000,0);const int32 Floors=Rng.RandRange(2,4);
    Shape("Cube",Center+FVector(0,0,30),FVector(850,850,60),"MI_Concrete");
    for(int32 SX:{-1,1})for(int32 SY:{-1,1})
        Shape("Cube",Center+FVector(SX*345,SY*345,Floors*155),FVector(42,42,Floors*310),"MI_Concrete");
    for(int32 F=1;F<=Floors;F++)
    {
        const float Z=F*310;
        Shape("Cube",Center+FVector(-110,0,Z),FVector(570,780,28),"MI_Concrete");
        if(Rng.FRand()>.35)Shape("Cube",Center+FVector(295,170,Z-35),FVector(220,380,23),"MI_Concrete",FRotator(0,0,Rng.FRandRange(-9,9)));
        for(int32 Side:{-1,1})
        {
            Shape("Cube",Center+FVector(0,Side*365,Z-245),FVector(740,22,105),"MI_Plaster");
            if(Rng.FRand()>.3)
            {
                Shape("Cube",Center+FVector(0,Side*367,Z-120),FVector(480,9,128),"MI_Glass",FRotator::ZeroRotator,false);
                for(int32 K:{-1,0,1})Shape("Cube",Center+FVector(K*242,Side*370,Z-125),FVector(12,18,145),"MI_Metal",FRotator::ZeroRotator,false);
            }
            if(Rng.FRand()>.4)Shape("Cube",Center+FVector(Side*365,-80,Z-155),FVector(28,510,260),"MI_Plaster");
        }
        // Exposed ends of floor reinforcement remain distinct from solid walls.
        for(int32 I=0;I<3;I++)Shape("Cube",Center+FVector(415,-190+I*105,Z-8),FVector(160,5,5),"MI_Rust",FRotator(0,Rng.FRandRange(-8,8),Rng.FRandRange(-6,6)),false);
    }
    for(int32 I=0;I<5;I++)Chunk(Rng,Center+FVector(Rng.FRandRange(-420,420),Rng.FRandRange(-420,420),60),FVector(Rng.FRandRange(120,260),Rng.FRandRange(100,200),Rng.FRandRange(50,100)),false);
}

void AMRWorld::Checkpoint()
{
    // Keep the shelter outside the route grid: props must not obstruct the exit.
    const FVector C=Base+FVector(-1500,1000,0);
    Shape("Cube",C+FVector(0,0,12),FVector(1750,1350,24),"MI_Concrete");
    Shape("Cube",C+FVector(0,270,180),FVector(1100,530,360),"MI_Teal");
    Shape("Cube",C+FVector(0,250,371),FVector(1150,600,30),"MI_Metal");
    for(int32 I=-4;I<=4;I++)Shape("Cube",C+FVector(I*110,1,180),FVector(8,5,342),"MI_Metal",FRotator::ZeroRotator,false);
    Shape("Cube",C+FVector(200,-3,180),FVector(310,15,250),"MI_Glass",FRotator::ZeroRotator,false);
    Shape("Cube",C+FVector(-250,-11,210),FVector(40,15,165),"MI_WhiteGlow",FRotator::ZeroRotator,false);
    Shape("Cube",C+FVector(-250,-12,210),FVector(165,15,40),"MI_WhiteGlow",FRotator::ZeroRotator,false);
    Shape("Cube",C+FVector(0,-330,310),FVector(1050,650,18),"MI_Teal",FRotator(-4,0,0));
    for(int32 I:{-1,1})Shape("Cylinder",C+FVector(I*480,-600,155),FVector(12,12,310),"MI_Metal");
    // A small service vehicle, with clear rescue colours and cab proportions.
    const FVector A=Base+FVector(-1000,-150,0);
    Shape("Cube",A+FVector(0,0,120),FVector(520,215,150),"MI_White");
    Shape("Cube",A+FVector(170,0,195),FVector(160,210,125),"MI_White");
    Shape("Cube",A+FVector(245,0,220),FVector(9,180,65),"MI_Glass",FRotator(0,0,0),false);
    for(int32 Y:{-1,1})
    {
        Shape("Cube",A+FVector(-50,Y*110,155),FVector(380,5,22),"MI_Red",FRotator::ZeroRotator,false);
        for(int32 X:{-175,170})Shape("Cylinder",A+FVector(X,Y*110,53),FVector(94,94,23),"MI_Rubber",FRotator(0,0,90));
    }
    Shape("Cube",A+FVector(150,0,265),FVector(40,145,20),"MI_CyanGlow",FRotator::ZeroRotator,false);
    // Cones, crates and a marked collection area make the checkpoint readable.
    for(int32 I=0;I<10;I++)
    {
        FVector P=Base+FVector(-850,-400+I*180,0);
        Shape("Cube",P+FVector(0,0,4),FVector(65,65,8),"MI_Dark");
        Shape("Cone",P+FVector(0,0,42),FVector(48,48,76),"MI_Orange");
    }
    for(int32 I=0;I<4;I++)Shape("Cube",C+FVector(-600+I*120,0,55),FVector(100,85,110),"MI_Equipment");
    Shape("Cube",Base+FVector(0,0,1),FVector(820,650,2),"MI_Zone",FRotator::ZeroRotator,false);
    for(int32 I=0;I<4;I++)Shape("Cube",Base+FVector(-300+I*200,0,3),FVector(110,4,2),"MI_White",FRotator::ZeroRotator,false);
}

void AMRWorld::BuildScene()
{
    for(USceneComponent* C:Generated){if(C){RemoveInstanceComponent(C);C->DestroyComponent();}}
    Generated.Empty();PhysicsPieces.Empty();CollapsePieces.Empty();
    if(!ReadScenario())return;
    FRandomStream Rng(Seed);const float Mid=(GridSize-1)*500.f;
    Shape("Cube",FVector(Mid,Mid,-60),FVector(GridSize*1000+3200,GridSize*1000+3200,100),"MI_Asphalt");
    for(int32 Y=0;Y<GridSize;Y++)for(int32 X=0;X<GridSize;X++)
    {
        const int32 V=Grid[Y][X];const FVector P(X*1000,Y*1000,-10);
        const bool Road=X<=2||Y<=2||X==GridSize/2||Y==GridSize/2||X%5==0||Y%5==0;
        if(V==3)
        {
            Shape("Plane",P+FVector(0,0,13),FVector(1000,1000,100),"MI_Water",FRotator::ZeroRotator,false);
            auto* Bank=NewObject<UBoxComponent>(this);AddInstanceComponent(Bank);Bank->SetupAttachment(GetRootComponent());
            Bank->SetBoxExtent(FVector(470,470,160));Bank->SetRelativeLocation(P+FVector(0,0,150));
            Bank->SetCollisionProfileName(TEXT("BlockAll"));Bank->RegisterComponent();Generated.Add(Bank);
            continue;
        }
        if(V==2)Shape("Cylinder",P+FVector(0,0,11),FVector(700,850,2),"MI_Mud",FRotator(0,Rng.FRandRange(0,180),0),false);
        if(V==1)
        {
            if(V==1&&Rng.FRand()>.4)Building(Rng,X,Y);
            else
            {
                for(int32 I=0;I<9;I++)Chunk(Rng,P+FVector(Rng.FRandRange(-330,330),Rng.FRandRange(-330,330),70+I*16),FVector(Rng.FRandRange(180,550),Rng.FRandRange(120,430),Rng.FRandRange(70,160)),false);
                for(int32 I=0;I<4;I++)Shape("Cube",P+FVector(Rng.FRandRange(-300,300),Rng.FRandRange(-300,300),130),FVector(350,5,5),"MI_Rust",FRotator(0,Rng.FRandRange(0,180),Rng.FRandRange(-30,30)),false);
            }
        }
        else if(Road&&X>2&&Y>2)
        {
            if(X%5==0)Shape("Cube",P+FVector(0,0,11),FVector(10,300,1),"MI_Paint",FRotator::ZeroRotator,false);
            else if(Y%5==0)Shape("Cube",P+FVector(0,0,11),FVector(300,10,1),"MI_Paint",FRotator::ZeroRotator,false);
        }
    }
    Checkpoint();
    // Deliberately unstable detached concrete. Chaos, not an animated transform.
    const FVector CollapseOrigin=Base+FVector(2300,1800,0);
    for(int32 I=0;I<18;I++)
        Chunk(Rng,CollapseOrigin+FVector(Rng.FRandRange(-500,500),Rng.FRandRange(-430,430),60+I*42),
              FVector(Rng.FRandRange(150,390),Rng.FRandRange(130,290),Rng.FRandRange(50,110)),true);
    for(int32 I=0;I<5;I++)
        Chunk(Rng,CollapseOrigin+FVector(-400+I*190,80,1050+I*85),FVector(400,520,65),true,true);
    for(int32 I=0;I<5;I++)
    {
        FVector P=Base+FVector(900+I*1300,-450,0);
        Shape("Cylinder",P+FVector(0,0,300),FVector(17,17,600),"MI_Metal");
        Shape("Cube",P+FVector(80,0,605),FVector(170,36,15),"MI_Metal");
        Shape("Cube",P+FVector(110,0,593),FVector(110,27,5),"MI_WhiteGlow",FRotator::ZeroRotator,false);
    }
    UE_LOG(LogTemp,Display,TEXT("MR scene built: %d components, %d Chaos pieces, grid %d"),Generated.Num(),PhysicsPieces.Num(),GridSize);
}

void AMRWorld::SpawnTargets()
{
    Targets.Empty();
    for(const auto& Item:Scenario->GetArrayField(TEXT("people")))
    {
        auto J=Item->AsObject();FMRTarget T;T.Id=J->GetIntegerField(TEXT("id"));
        T.Position=FVector(J->GetNumberField(TEXT("x"))*1000,J->GetNumberField(TEXT("y"))*1000,24);
        for(const auto& P:J->GetArrayField(TEXT("path")))
        {const auto& XY=P->AsArray();T.Path.Add(FVector(XY[0]->AsNumber()*1000,XY[1]->AsNumber()*1000,48));}
        AActor* Visual=GetWorld()->SpawnActor<AActor>(T.Position,FRotator::ZeroRotator);
        USceneComponent* Root=NewObject<USceneComponent>(Visual);Visual->SetRootComponent(Root);Visual->AddInstanceComponent(Root);Root->RegisterComponent();Visual->SetActorLocation(T.Position);
        auto BodyPart=[Visual,Root](FVector P,FVector S,const FString& Mesh)
        {
            auto* C=NewObject<UStaticMeshComponent>(Visual);Visual->AddInstanceComponent(C);C->SetupAttachment(Root);
            C->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,*(TEXT("/Engine/BasicShapes/")+Mesh+TEXT(".")+Mesh)));
            C->SetMaterial(0,LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/Materials/MI_Red.MI_Red")));
            C->SetRelativeLocation(P);C->SetRelativeScale3D(S/100);C->SetCollisionEnabled(ECollisionEnabled::NoCollision);C->RegisterComponent();
        };
        BodyPart(FVector(0,0,0),FVector(75,43,32),"Sphere");
        BodyPart(FVector(62,0,2),FVector(30,30,30),"Sphere");
        for(int32 Side:{-1,1}){BodyPart(FVector(-60,Side*14,-4),FVector(90,20,24),"Sphere");BodyPart(FVector(0,Side*36,-5),FVector(74,16,18),"Sphere");}
        T.Visual=Visual;Targets.Add(T);
    }
}

void AMRWorld::SpawnAgents()
{
    const int32 Drones=Scenario->GetIntegerField(TEXT("scouts")),Robots=Scenario->GetIntegerField(TEXT("rescuers"));
    const auto& Routes=Scenario->GetArrayField(TEXT("scout_waypoints"));
    for(int32 I=0;I<Drones+Robots;I++)
    {
        const bool Aerial=I<Drones;const int32 Id=Aerial?I:I-Drones;
        FVector P=Base+FVector((Id%2?1:-1)*250,(Id/2?1:-1)*230,Aerial?1900:60);
        auto* A=GetWorld()->SpawnActor<AMRAgent>(P,FRotator::ZeroRotator);
        TArray<FVector> Waypoints;
        if(Aerial&&Routes.IsValidIndex(Id))for(const auto& W:Routes[Id]->AsArray())
        {const auto& XY=W->AsArray();Waypoints.Add(FVector(XY[0]->AsNumber()*1000,XY[1]->AsNumber()*1000,1900+Id*150));}
        A->Initialize(this,Id,Aerial,Waypoints);Agents.Add(A);
    }
}

void AMRWorld::BeginPlay()
{
    Super::BeginPlay();
    if(!ReadScenario())return;
    if(Generated.IsEmpty())BuildScene();
    IFileManager::Get().MakeDirectory(*(FPaths::ProjectSavedDir()/TEXT("Telemetry")),true);
    FFileHelper::SaveStringToFile(TEXT(""),*(FPaths::ProjectSavedDir()/TEXT("Telemetry/events.jsonl")));
    for(UProceduralMeshComponent* P:PhysicsPieces)
    {
        InitialPiecePositions.Add(P->GetComponentLocation());P->SetNotifyRigidBodyCollision(true);
        P->OnComponentHit.AddDynamic(this,&AMRWorld::OnRubbleHit);
        P->SetSimulatePhysics(!CollapsePieces.Contains(P));
    }
    SpawnTargets();SpawnAgents();AppendEvent(TEXT("episode_started"));
    bSmoke=FParse::Param(FCommandLine::Get(),TEXT("MRSmoke"));
    FParse::Value(FCommandLine::Get(),TEXT("MRQuitAfter="),QuitAfter);
}

void AMRWorld::ReleaseCollapse()
{
    if(bCollapseReleased)return;bCollapseReleased=true;
    for(UProceduralMeshComponent* P:CollapsePieces){P->SetSimulatePhysics(true);P->WakeAllRigidBodies();P->AddImpulse(FVector(2000,3500,0));}
    AppendEvent(TEXT("demo_collapse_released"));
}

void AMRWorld::OnRubbleHit(UPrimitiveComponent*,AActor*,UPrimitiveComponent*,FVector Impulse,const FHitResult&)
{if(Impulse.Size()>1)ContactCount++;}

int32 AMRWorld::FoundCount()const{int32 N=0;for(const auto& T:Targets)N+=T.bDiscovered;return N;}
int32 AMRWorld::SavedCount()const{int32 N=0;for(const auto& T:Targets)N+=T.bRescued;return N;}

void AMRWorld::SenseAndDispatch(float Dt)
{
    SensorClock+=Dt;if(SensorClock<.3f)return;SensorClock=0;
    for(auto& T:Targets)
    {
        if(T.bDiscovered)continue;
        for(AMRAgent* A:Agents)
        {
            if(!A->bAerial||FVector::Dist2D(A->GetActorLocation(),T.Position)>3600)continue;
            FCollisionQueryParams Params;Params.AddIgnoredActor(A);
            FHitResult Hit;
            if(!GetWorld()->LineTraceSingleByChannel(Hit,A->GetActorLocation(),T.Position+FVector(0,0,60),ECC_Visibility,Params))
            {T.bDiscovered=true;T.DiscoveredAt=GetWorld()->GetTimeSeconds();AppendEvent(TEXT("person_detected"),T.Id);break;}
        }
    }
    // A deliberately small demo dispatcher. Runtime NSGA/controller integration is separate.
    for(AMRAgent* A:Agents)
    {
        if(A->bAerial||!A->IsIdle()||A->Energy<10)continue;
        int32 Choice=-1;double Best=TNumericLimits<double>::Max();
        for(int32 I=0;I<Targets.Num();I++)
        {
            auto& T=Targets[I];if(!T.bDiscovered||T.bAssigned||T.bRescued||T.Path.IsEmpty())continue;
            double Length=0;for(int32 J=1;J<T.Path.Num();J++)Length+=FVector::Dist2D(T.Path[J-1],T.Path[J]);
            if(Length/1000*2>A->Energy-22)continue;
            if(Length<Best){Best=Length;Choice=I;}
        }
        if(Choice>=0)A->StartRescue(Choice);
    }
}

void AMRWorld::AppendEvent(const FString& Type,int32 Id)
{
    TSharedRef<FJsonObject> J=MakeShared<FJsonObject>();J->SetStringField(TEXT("type"),Type);
    J->SetNumberField(TEXT("time_s"),GetWorld()->GetTimeSeconds());J->SetNumberField(TEXT("target_id"),Id);
    FString Text;FJsonSerializer::Serialize(J,TJsonWriterFactory<TCHAR,TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text));
    FFileHelper::SaveStringToFile(Text+TEXT("\n"),*(FPaths::ProjectSavedDir()/TEXT("Telemetry/events.jsonl")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM,&IFileManager::Get(),FILEWRITE_Append);
    UE_LOG(LogTemp,Display,TEXT("MR event: %s"),*Text);
}

void AMRWorld::WriteTelemetry()
{
    TSharedRef<FJsonObject> J=MakeShared<FJsonObject>();J->SetStringField(TEXT("kind"),TEXT("3d_physics_sandbox_debug"));
    J->SetNumberField(TEXT("time_s"),GetWorld()->GetTimeSeconds());J->SetNumberField(TEXT("seed"),Seed);
    J->SetNumberField(TEXT("discovered"),FoundCount());J->SetNumberField(TEXT("delivered"),SavedCount());
    J->SetNumberField(TEXT("contacts"),ContactCount);J->SetBoolField(TEXT("collapse_released"),bCollapseReleased);
    TArray<TSharedPtr<FJsonValue>> Bodies;
    for(int32 I=0;I<PhysicsPieces.Num();I++)
    {
        UProceduralMeshComponent* P=PhysicsPieces[I];auto Row=MakeShared<FJsonObject>();
        Row->SetNumberField(TEXT("initial_z_cm"),InitialPiecePositions[I].Z);
        Row->SetNumberField(TEXT("z_cm"),P->GetComponentLocation().Z);Row->SetBoolField(TEXT("simulating"),P->IsSimulatingPhysics());
        Row->SetNumberField(TEXT("speed_cm_s"),P->GetPhysicsLinearVelocity().Size());Bodies.Add(MakeShared<FJsonValueObject>(Row));
    }
    J->SetArrayField(TEXT("physics_bodies"),Bodies);TArray<TSharedPtr<FJsonValue>> Rows;
    for(AMRAgent* A:Agents)
    {
        auto Row=MakeShared<FJsonObject>();const FVector P=A->GetActorLocation();
        Row->SetNumberField(TEXT("id"),A->AgentId);Row->SetBoolField(TEXT("aerial"),A->bAerial);
        Row->SetStringField(TEXT("state"),A->State);Row->SetNumberField(TEXT("energy"),A->Energy);
        Row->SetNumberField(TEXT("x_cm"),P.X);Row->SetNumberField(TEXT("y_cm"),P.Y);Row->SetNumberField(TEXT("z_cm"),P.Z);
        Row->SetNumberField(TEXT("distance_cm"),A->DistanceTravelled);Row->SetBoolField(TEXT("simulating"),A->Body->IsSimulatingPhysics());Rows.Add(MakeShared<FJsonValueObject>(Row));
    }
    J->SetArrayField(TEXT("agents"),Rows);FString Text;FJsonSerializer::Serialize(J,TJsonWriterFactory<>::Create(&Text));
    FFileHelper::SaveStringToFile(Text,*(FPaths::ProjectSavedDir()/TEXT("Telemetry/latest.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
}

void AMRWorld::Tick(float Dt)
{
    Super::Tick(Dt);if(!Scenario)return;
    const float Time=GetWorld()->GetTimeSeconds();SenseAndDispatch(Dt);
    if(Time>18&&!bCollapseReleased)ReleaseCollapse();
    TelemetryClock+=Dt;if(TelemetryClock>=2){WriteTelemetry();TelemetryClock=0;}
    if(bSmoke&&Time>2&&!bStartScreenshotTaken)
    {IFileManager::Get().MakeDirectory(*(FPaths::ProjectSavedDir()/TEXT("Screenshots")),true);
     FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir()/TEXT("Screenshots/scene-start.png"),true,false);bStartScreenshotTaken=true;}
    if(bSmoke&&Time>26&&!bScreenshotTaken)
    {IFileManager::Get().MakeDirectory(*(FPaths::ProjectSavedDir()/TEXT("Screenshots")),true);
     FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir()/TEXT("Screenshots/physics-smoke.png"),true,false);bScreenshotTaken=true;}
    if(QuitAfter>0&&Time>QuitAfter){WriteTelemetry();FGenericPlatformMisc::RequestExit(false);}
}
