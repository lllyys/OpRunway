#include "solver_adapter.h"
#include "cann_ops_solver.h"
#include <complex>
#include <cstring>
#include <new>
// No input generation or verdict here. This module is shared by test and harness.
namespace {
struct State { aclsolverHandle_t handle; int64_t n, batch; size_t bytes;
    void *a=nullptr, *out=nullptr; int32_t info=-999; };
}
extern "C" const SolverAdapterDesc *solver_adapter_descriptor() {
    static const SolverAdapterDesc d{SOLVER_ADAPTER_VERSION,sizeof(SolverAdapterDesc),
        "cmatinv_batched","complex64","host","row_major","scalar",
        reinterpret_cast<const void*>(&aclsolverCmatinvBatched)};
    return &d;
}
extern "C" int solver_adapter_create(const SolverAdapterCase *c,void *handle,void *,void **state) {
    if (!state) return 1;
    *state=nullptr;
    if (!c || c->version!=SOLVER_ADAPTER_VERSION || c->struct_size!=sizeof(*c)
        || !handle || c->n<=0 || c->n>256 || c->batch<=0 || c->batch>3000 || c->lda!=c->n
        || c->a_bytes!=static_cast<size_t>(c->n*c->n*c->batch)*8
        || c->b_bytes || c->out_bytes!=c->a_bytes || c->info_count!=1) return 1;
    auto *s=new(std::nothrow) State{static_cast<aclsolverHandle_t>(handle),c->n,c->batch,c->a_bytes};
    if (!s) return 2;
    *state=s; // Publish before allocation so partial failures can be cleaned.
    if (aclrtMallocHost(&s->a,s->bytes) || aclrtMallocHost(&s->out,s->bytes)) return 2;
    return 0;
}
extern "C" int solver_adapter_reset(void *state,const void *a,size_t bytes,const void *,size_t bbytes) {
    auto *s=static_cast<State*>(state);
    if (!s || !a || bytes!=s->bytes || bbytes) return 1;
    std::memcpy(s->a,a,bytes);std::memset(s->out,0,bytes);s->info=-999;return 0;
}
extern "C" int solver_adapter_execute(void *state,int *status) {
    auto *s=static_cast<State*>(state);if (!s || !status) return 1;
    *status=aclsolverCmatinvBatched(s->handle,s->n,static_cast<std::complex<float>*>(s->a),s->n,
        static_cast<std::complex<float>*>(s->out),s->n,&s->info,s->batch);
    return 0;
}
extern "C" int solver_adapter_readback(void *state,void *out,size_t bytes,int32_t *info,size_t count) {
    auto *s=static_cast<State*>(state);
    if (!s || !out || !info || bytes!=s->bytes || count!=1) return 1;
    std::memcpy(out,s->out,bytes);info[0]=s->info;return 0;
}
extern "C" int solver_adapter_destroy(void *state) {
    auto *s=static_cast<State*>(state);if (!s) return 0;
    int ret=0;
    if (s->out) ret=aclrtFreeHost(s->out);
    if (s->a) { int rc=aclrtFreeHost(s->a);if (!ret) ret=rc; }
    delete s;return ret;
}
