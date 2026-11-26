using UnrealBuildTool;
public class MetaRescue : ModuleRules
{
    public MetaRescue(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "Engine", "InputCore", "Json", "JsonUtilities",
            "PhysicsCore", "ProceduralMeshComponent", "Niagara"
        });
        if(Target.bBuildEditor)PrivateDependencyModuleNames.Add("NiagaraEditor");
    }
}
