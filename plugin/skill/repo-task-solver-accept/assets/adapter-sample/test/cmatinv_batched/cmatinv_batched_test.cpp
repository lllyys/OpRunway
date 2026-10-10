#include "solver_adapter.h"
#include "cann_ops_solver.h"
#include <complex>
#include <cstdio>
#include <vector>
// Developer smoke example: shares the exact adapter used by the skill.
int main() {
    constexpr int n=8,batch=4; size_t bytes=n*n*batch*sizeof(std::complex<float>);
    std::vector<std::complex<float>> a(n*n*batch),out(a.size());
    for(int b=0;b<batch;++b) for(int i=0;i<n;++i) a[b*n*n+i*n+i]={2,0};
    int rc=aclInit(nullptr);if(rc)return 1;
    if(aclrtSetDevice(0)){aclFinalize();return 1;}
    aclrtStream stream=nullptr;aclsolverHandle_t handle=nullptr;void *state=nullptr;
    int status=-1;int32_t info=-999;
    SolverAdapterCase c{SOLVER_ADAPTER_VERSION,sizeof(SolverAdapterCase),n,0,batch,n,n,'L',"","matrix",bytes,0,bytes,1};
    if(aclrtCreateStream(&stream) || aclsolverCreate(&handle) || aclsolverSetStream(handle,stream)) rc=1;
    if(!rc) {
        SolverAdapterCase invalid=c;invalid.version=0;void *bad=nullptr;
        if(solver_adapter_create(&invalid,handle,stream,&bad)==0 || bad) rc=1;
    }
    if(!rc)rc=solver_adapter_create(&c,handle,stream,&state);
    if(!rc && solver_adapter_reset(state,a.data(),bytes-1,nullptr,0)==0)rc=1;
    if(!rc && solver_adapter_readback(state,out.data(),bytes,&info,0)==0)rc=1;
    if(!rc)rc=solver_adapter_reset(state,a.data(),bytes,nullptr,0);
    if(!rc)rc=aclrtSynchronizeStream(stream);
    if(!rc)rc=solver_adapter_execute(state,&status);
    if(!rc)rc=aclrtSynchronizeStream(stream);
    if(!rc)rc=solver_adapter_readback(state,out.data(),bytes,&info,1);
    bool correct=rc==0 && status==0 && info==0;
    for(size_t i=0;i<out.size();++i) {
        std::complex<float> expected=(i%(n*n))%(n+1)==0?std::complex<float>(0.5f,0):std::complex<float>(0,0);
        if(!(std::abs(out[i]-expected)<=1e-5f))correct=false;
    }
    if(stream && aclrtSynchronizeStream(stream))correct=false;
    if(solver_adapter_destroy(state))correct=false;
    if(handle && aclsolverDestroy(handle))correct=false;
    if(stream && aclrtDestroyStream(stream))correct=false;
    if(aclrtResetDevice(0))correct=false;
    if(aclFinalize())correct=false;
    std::printf("developer smoke: %s (not acceptance evidence)\n",correct?"PASS":"FAIL");
    return correct?0:1;
}
