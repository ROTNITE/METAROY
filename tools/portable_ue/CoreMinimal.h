#pragma once
// Test-only adapter for the small value/container API used by MRHydrology.h
// and MRStreetSignals.h. NEVER add this directory to an Unreal build.
// This does not emulate UObject, Chaos, materials, networking or the engine.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <vector>

using int32=std::int32_t;
using int64=std::int64_t;
using int8=std::int8_t;
constexpr int32 INDEX_NONE=-1;
constexpr double MAX_dbl=std::numeric_limits<double>::max();

template<class T> struct TArray
{
    std::vector<T> V;
    TArray()=default;
    TArray(std::initializer_list<T> list):V(list){}
    void Init(const T& value,int32 size){V.assign(static_cast<std::size_t>(size),value);}
    int32 Num() const{return static_cast<int32>(V.size());}
    bool IsValidIndex(int32 i) const{return i>=0&&i<Num();}
    T& operator[](int32 i){return V.at(static_cast<std::size_t>(i));}
    const T& operator[](int32 i) const{return V.at(static_cast<std::size_t>(i));}
    auto begin(){return V.begin();}
    auto end(){return V.end();}
    auto begin() const{return V.begin();}
    auto end() const{return V.end();}
};
template<class T=void> struct TBitArray
{
    std::vector<bool> V;
    TBitArray()=default;
    TBitArray(bool value,int32 n):V(static_cast<std::size_t>(n),value){}
    int32 Num() const{return static_cast<int32>(V.size());}
    auto operator[](int32 i){return V.at(static_cast<std::size_t>(i));}
    bool operator[](int32 i) const{return V.at(static_cast<std::size_t>(i));}
};
template<class K,class V> struct TMap
{
    struct Pair {K Key;V Value;};
    std::vector<Pair> Items;
    void Empty(){Items.clear();}
    void Add(K key,V value)
    {for(auto& p:Items)if(p.Key==key){p.Value=value;return;}Items.push_back({key,value});}
    auto begin() const{return Items.begin();}
    auto end() const{return Items.end();}
};
struct FVector
{
    double X=0,Y=0,Z=0;
    FVector()=default;
    FVector(double x,double y,double z):X(x),Y(y),Z(z){}
    double Size() const{return std::sqrt(X*X+Y*Y+Z*Z);}
    static const FVector ZeroVector;
};
inline const FVector FVector::ZeroVector{};
struct FMath
{
    template<class T> static T Max(T a,T b){return std::max(a,b);}
    template<class T> static T Min(T a,T b){return std::min(a,b);}
    template<class T> static T Max3(T a,T b,T c){return Max(a,Max(b,c));}
    template<class T> static T Min3(T a,T b,T c){return Min(a,Min(b,c));}
    static bool IsFinite(double v){return std::isfinite(v);}
    static double Sqrt(double v){return std::sqrt(v);}
    static double Pow(double a,double b){return std::pow(a,b);}
    static double Abs(double a){return std::abs(a);}
    static double Fmod(double a,double b){return std::fmod(a,b);}
};
template<class T> struct TWeakObjectPtr {};
struct FName {};
