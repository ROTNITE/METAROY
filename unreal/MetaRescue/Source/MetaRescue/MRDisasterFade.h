#pragma once
#include "CoreMinimal.h"

// Model-time radiance envelope, shared by live effects and delivered optical
// snapshots. A hidden Niagara renderer may expose its default material rather
// than the last material override, so it is not an authoritative fade clock.
inline float MRDisasterFade(float Age,float IgnitionDelay,float Duration,float FadeInSeconds=1.5f)
{
    const float FadeOut=FMath::Min(4.f,Duration*.25f);
    return FMath::SmoothStep(0.f,FadeInSeconds,Age-IgnitionDelay)*
        (Duration>0?1-FMath::SmoothStep(Duration-FadeOut,Duration,Age):1.f);
}
