// Standalone compute verification of the authored HLSL. No engine stubs.
#include "../unreal/MetaRescue/Source/MetaRescue/MRSmokeOptics.h"
#include <vulkan/vulkan.h>
#include <array>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

using MRSmokeOptics::Vec;
using MRSmokeOptics::Field;
using V4=std::array<float,4>;
constexpr uint32_t N=2048,Stride=9;
static_assert(sizeof(V4)==16);
void check(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan error "+std::to_string(r));}
template<class T> T init(VkStructureType type){T t{};t.sType=type;return t;}
Vec vec(V4 a){return {a[0],a[1],a[2]};}
V4 v4(Vec a,float w=0){return {float(a.X),float(a.Y),float(a.Z),w};}
Vec sub(Vec a,Vec b){return {a.X-b.X,a.Y-b.Y,a.Z-b.Z};}
Vec add(Vec a,Vec b){return {a.X+b.X,a.Y+b.Y,a.Z+b.Z};}
bool inside(Vec p,Vec l,Vec h){return p.X>=l.X&&p.Y>=l.Y&&p.Z>=l.Z&&p.X<=h.X&&p.Y<=h.Y&&p.Z<=h.Z;}
Vec maxv(Vec a,Vec b){return {std::max(a.X,b.X),std::max(a.Y,b.Y),std::max(a.Z,b.Z)};}
Vec minv(Vec a,Vec b){return {std::min(a.X,b.X),std::min(a.Y,b.Y),std::min(a.Z,b.Z)};}
bool clip(Vec& a,Vec& b,Vec lo,Vec hi)
{
    const double p[]={a.X,a.Y,a.Z},q[]={b.X,b.Y,b.Z},l[]={lo.X,lo.Y,lo.Z},h[]={hi.X,hi.Y,hi.Z};
    double from=0,to=1;
    for(int k=0;k<3;++k)
    {
        if(h[k]<=l[k])return false;
        const double d=q[k]-p[k];
        if(std::abs(d)<1.e-10){if(p[k]<l[k]||p[k]>h[k])return false;continue;}
        const double x=(l[k]-p[k])/d,y=(h[k]-p[k])/d;
        from=std::max(from,std::min(x,y));to=std::min(to,std::max(x,y));
        if(from>=to)return false;
    }
    const Vec begin=MRSmokeOptics::Mix(a,b,from),end=MRSmokeOptics::Mix(a,b,to);
    a=begin;b=end;return true;
}
int main(int argc,char** argv)
{
    if(argc!=2)throw std::runtime_error("SPIR-V file required");
    std::ifstream in(argv[1],std::ios::binary|std::ios::ate);
    const auto bytes=in.tellg();
    if(bytes<=0||bytes%4)throw std::runtime_error("Invalid SPIR-V");
    std::vector<uint32_t> code(size_t(bytes)/4);
    in.seekg(0);in.read(reinterpret_cast<char*>(code.data()),bytes);
    if(!in)throw std::runtime_error("Failed to read SPIR-V");
    std::vector<V4> samples(N*Stride);
    std::vector<std::array<double,2>> expected(N);
    std::mt19937 rng(2026);
    std::uniform_real_distribution<double> wind(-400,400),far(-8000,8000),z(-500,5000);
    uint32_t insideCount=0,clippedCount=0;
    for(uint32_t i=0;i<N;++i)
    {
        const double origin=i%2?150000:0;
        const Vec source{origin,origin,340};
        const Field f{i%3==0?900.:i%3==1?3000.:4200.,i%3==0?1300.:650.,
            i%5==0?0.:.28,wind(rng),wind(rng),i%3==0?100.:280.,i%3==0?.0018:.006,i%17==0?0.:.9};
        const double height=f.Height*.5;
        const Vec center{f.WindX*height/f.Rise,f.WindY*height/f.Rise,height};
        const double radius=f.Radius+f.Growth*height;
        Vec a=add(center,{-radius-300,0,0}),b=add(center,{radius+300,0,0});
        if(i%7==0)a=center;
        if(i%4==0){a={far(rng),far(rng),z(rng)};b={far(rng),far(rng),z(rng)};}
        if(i%11==0){a.Y+=radius*.99;b.Y+=radius*.99;}
        const double fraction=i%3==0?.35:i%3==1?.75:1.;
        if(fraction<1)++clippedCount;
        const Vec top{f.WindX*f.Height/f.Rise,f.WindY*f.Height/f.Rise,f.Height};
        const double r=f.Radius+f.Growth*f.Height;
        const Vec lo{std::min(0.,top.X)-r,std::min(0.,top.Y)-r,0};
        const Vec hi{std::max(0.,top.X)+r,std::max(0.,top.Y)+r,f.Height};
        Vec cell=center;if(i%9==0)cell=add(center,{4*r,4*r,0});
        if(i%7==0)cell=a;
        const Vec cl=add(cell,{-250,-250,-250}),ch=add(cell,{250,250,250});
        const float side=inside(a,lo,hi)?-1.f:1.f,rememberedSide=inside(a,maxv(lo,cl),minv(hi,ch))?-1.f:1.f;
        if(side<0)++insideCount;
        const uint32_t p=i*Stride;
        samples[p]=v4(source,side);samples[p+1]=v4({f.WindX,f.WindY,0},rememberedSide);
        samples[p+2]=v4({f.Height,f.Radius,f.Growth});
        samples[p+3]={float(f.Rise),float(f.Extinction),float(f.Strength),float(fraction)};
        samples[p+4]=v4(add(source,b));samples[p+5]=v4(add(source,a));
        samples[p+6]=v4(add(source,cl));samples[p+7]=v4(add(source,ch));samples[p+8]={-1,-1,-1,-1};
        // Quantize the reference to the exact float input sent to the shader.
        const auto shape=samples[p+2],wd=samples[p+1],args=samples[p+3];
        const Field ff{shape[0],shape[1],shape[2],wd[0],wd[1],args[0],args[1],args[2]};
        a=sub(vec(samples[p+5]),vec(samples[p]));b=sub(vec(samples[p+4]),vec(samples[p]));
        b=MRSmokeOptics::Mix(a,b,args[3]);
        expected[i][0]=1-std::exp(-MRSmokeOptics::Integrate(ff,a,b));
        expected[i][1]=clip(a,b,sub(vec(samples[p+6]),vec(samples[p])),sub(vec(samples[p+7]),vec(samples[p])))?
            1-std::exp(-MRSmokeOptics::Integrate(ff,a,b)):0;
    }
    auto app=init<VkApplicationInfo>(VK_STRUCTURE_TYPE_APPLICATION_INFO);app.pApplicationName="MetaRescue smoke probe";app.apiVersion=VK_API_VERSION_1_1;
    auto ci=init<VkInstanceCreateInfo>(VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO);ci.pApplicationInfo=&app;
    VkInstance instance{};check(vkCreateInstance(&ci,nullptr,&instance));
    uint32_t count=0;check(vkEnumeratePhysicalDevices(instance,&count,nullptr));
    std::vector<VkPhysicalDevice> devices(count);check(vkEnumeratePhysicalDevices(instance,&count,devices.data()));
    VkPhysicalDevice physical=VK_NULL_HANDLE;VkPhysicalDeviceProperties props{};
    for(auto d:devices){vkGetPhysicalDeviceProperties(d,&props);if(props.deviceType==VK_PHYSICAL_DEVICE_TYPE_CPU){physical=d;break;}}
    if(!physical)throw std::runtime_error("CPU Vulkan device not available");
    vkGetPhysicalDeviceQueueFamilyProperties(physical,&count,nullptr);
    std::vector<VkQueueFamilyProperties> families(count);vkGetPhysicalDeviceQueueFamilyProperties(physical,&count,families.data());
    uint32_t family=0;while(family<count&&!(families[family].queueFlags&VK_QUEUE_COMPUTE_BIT))++family;
    if(family==count)throw std::runtime_error("No compute queue");
    const float priority=1;
    auto queueInfo=init<VkDeviceQueueCreateInfo>(VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO);
    queueInfo.queueFamilyIndex=family;queueInfo.queueCount=1;queueInfo.pQueuePriorities=&priority;
    auto deviceInfo=init<VkDeviceCreateInfo>(VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO);
    deviceInfo.queueCreateInfoCount=1;deviceInfo.pQueueCreateInfos=&queueInfo;
    VkDevice device{};check(vkCreateDevice(physical,&deviceInfo,nullptr,&device));
    VkQueue queue{};vkGetDeviceQueue(device,family,0,&queue);
    auto bufferInfo=init<VkBufferCreateInfo>(VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO);
    bufferInfo.size=samples.size()*sizeof(V4);bufferInfo.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;bufferInfo.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
    VkBuffer buffer{};check(vkCreateBuffer(device,&bufferInfo,nullptr,&buffer));
    VkMemoryRequirements req{};vkGetBufferMemoryRequirements(device,buffer,&req);
    VkPhysicalDeviceMemoryProperties memProps{};vkGetPhysicalDeviceMemoryProperties(physical,&memProps);
    uint32_t memoryType=0;const VkMemoryPropertyFlags flags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    while(memoryType<memProps.memoryTypeCount&&(!(req.memoryTypeBits&(1u<<memoryType))||(memProps.memoryTypes[memoryType].propertyFlags&flags)!=flags))++memoryType;
    if(memoryType==memProps.memoryTypeCount)throw std::runtime_error("No coherent host buffer");
    auto alloc=init<VkMemoryAllocateInfo>(VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO);
    alloc.allocationSize=req.size;alloc.memoryTypeIndex=memoryType;
    VkDeviceMemory memory{};check(vkAllocateMemory(device,&alloc,nullptr,&memory));check(vkBindBufferMemory(device,buffer,memory,0));
    void* mapped=nullptr;check(vkMapMemory(device,memory,0,bufferInfo.size,0,&mapped));std::memcpy(mapped,samples.data(),bufferInfo.size);
    VkDescriptorSetLayoutBinding binding{};binding.binding=0;binding.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;binding.descriptorCount=1;binding.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT;
    auto layoutInfo=init<VkDescriptorSetLayoutCreateInfo>(VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO);layoutInfo.bindingCount=1;layoutInfo.pBindings=&binding;
    VkDescriptorSetLayout layout{};check(vkCreateDescriptorSetLayout(device,&layoutInfo,nullptr,&layout));
    VkDescriptorPoolSize poolSize{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1};
    auto poolInfo=init<VkDescriptorPoolCreateInfo>(VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO);poolInfo.maxSets=1;poolInfo.poolSizeCount=1;poolInfo.pPoolSizes=&poolSize;
    VkDescriptorPool pool{};check(vkCreateDescriptorPool(device,&poolInfo,nullptr,&pool));
    auto setInfo=init<VkDescriptorSetAllocateInfo>(VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO);setInfo.descriptorPool=pool;setInfo.descriptorSetCount=1;setInfo.pSetLayouts=&layout;
    VkDescriptorSet set{};check(vkAllocateDescriptorSets(device,&setInfo,&set));
    VkDescriptorBufferInfo descriptor{buffer,0,bufferInfo.size};
    auto write=init<VkWriteDescriptorSet>(VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET);write.dstSet=set;write.descriptorCount=1;write.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;write.pBufferInfo=&descriptor;
    vkUpdateDescriptorSets(device,1,&write,0,nullptr);
    auto pipelineLayoutInfo=init<VkPipelineLayoutCreateInfo>(VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO);pipelineLayoutInfo.setLayoutCount=1;pipelineLayoutInfo.pSetLayouts=&layout;
    VkPipelineLayout pipelineLayout{};check(vkCreatePipelineLayout(device,&pipelineLayoutInfo,nullptr,&pipelineLayout));
    auto moduleInfo=init<VkShaderModuleCreateInfo>(VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO);moduleInfo.codeSize=code.size()*4;moduleInfo.pCode=code.data();
    VkShaderModule module{};check(vkCreateShaderModule(device,&moduleInfo,nullptr,&module));
    auto pipelineInfo=init<VkComputePipelineCreateInfo>(VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO);
    pipelineInfo.stage=init<VkPipelineShaderStageCreateInfo>(VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO);
    pipelineInfo.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT;pipelineInfo.stage.module=module;pipelineInfo.stage.pName="main";pipelineInfo.layout=pipelineLayout;
    VkPipeline pipeline{};check(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&pipelineInfo,nullptr,&pipeline));
    auto cmdPoolInfo=init<VkCommandPoolCreateInfo>(VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO);cmdPoolInfo.queueFamilyIndex=family;
    VkCommandPool cmdPool{};check(vkCreateCommandPool(device,&cmdPoolInfo,nullptr,&cmdPool));
    auto cmdAlloc=init<VkCommandBufferAllocateInfo>(VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO);cmdAlloc.commandPool=cmdPool;cmdAlloc.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;cmdAlloc.commandBufferCount=1;
    VkCommandBuffer cmd{};check(vkAllocateCommandBuffers(device,&cmdAlloc,&cmd));
    auto begin=init<VkCommandBufferBeginInfo>(VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO);check(vkBeginCommandBuffer(cmd,&begin));
    vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,pipelineLayout,0,1,&set,0,nullptr);
    vkCmdDispatch(cmd,N/64,1,1);
    auto barrier=init<VkMemoryBarrier>(VK_STRUCTURE_TYPE_MEMORY_BARRIER);barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_HOST_READ_BIT;
    vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,nullptr,0,nullptr);
    check(vkEndCommandBuffer(cmd));
    auto submit=init<VkSubmitInfo>(VK_STRUCTURE_TYPE_SUBMIT_INFO);submit.commandBufferCount=1;submit.pCommandBuffers=&cmd;
    const auto start=std::chrono::steady_clock::now();check(vkQueueSubmit(queue,1,&submit,VK_NULL_HANDLE));check(vkQueueWaitIdle(queue));
    const double millis=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    std::memcpy(samples.data(),mapped,bufferInfo.size);
    double liveError=0,rememberedError=0;uint32_t liveNonzero=0,rememberedNonzero=0,bad=0;
    for(uint32_t i=0;i<N;++i)
    {
        const auto result=samples[i*Stride+8];
        const double a=std::abs(result[0]-expected[i][0]),b=std::abs(result[2]-expected[i][1]);
        liveError=std::max(liveError,a);rememberedError=std::max(rememberedError,b);
        if(expected[i][0]>.001)++liveNonzero;
        if(expected[i][1]>.001)++rememberedNonzero;
        if(!std::isfinite(result[0])||!std::isfinite(result[2])||a>.001||b>.001||result[1]!=0||result[3]!=0)
        {if(bad<10)std::cerr<<"Mismatch "<<i<<" live="<<result[0]<<" expected="<<expected[i][0]<<" frozen="<<result[2]<<" expected="<<expected[i][1]<<" wrong sides="<<result[1]<<","<<result[3]<<"\n";++bad;}
    }
    vkUnmapMemory(device,memory);vkDestroyCommandPool(device,cmdPool,nullptr);vkDestroyPipeline(device,pipeline,nullptr);
    vkDestroyShaderModule(device,module,nullptr);vkDestroyPipelineLayout(device,pipelineLayout,nullptr);vkDestroyDescriptorPool(device,pool,nullptr);
    vkDestroyDescriptorSetLayout(device,layout,nullptr);vkDestroyBuffer(device,buffer,nullptr);vkFreeMemory(device,memory,nullptr);
    vkDestroyDevice(device,nullptr);vkDestroyInstance(instance,nullptr);
    std::cout<<"Vulkan device "<<props.deviceName<<" cases="<<N<<" live_nonzero="<<liveNonzero<<" remembered_nonzero="<<rememberedNonzero
             <<" inside="<<insideCount<<" depth_clipped="<<clippedCount<<" live_error="<<liveError<<" remembered_error="<<rememberedError
             <<" dispatch_ms="<<millis<<" mismatches="<<bad<<"\n";
    if(bad||liveNonzero<100||rememberedNonzero<100||!insideCount||!clippedCount)throw std::runtime_error("Optical acceptance failed");
}
