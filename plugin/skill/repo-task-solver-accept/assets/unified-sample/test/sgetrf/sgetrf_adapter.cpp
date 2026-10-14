#include "solver_adapter.h"
#include "cann_ops_solver.h"
#include <algorithm>
#include <cstring>
#include <new>
#include <vector>
// Public interface: compact row-major Host A, Host ipiv/info.
namespace {
struct State {
    aclsolverHandle_t handle;
    int64_t m, n, lda;
    std::vector<float> a;
    std::vector<int32_t> piv;
    int32_t info;
};
}
extern "C" const SolverAdapterDesc *solver_adapter_descriptor() {
    static const SolverAdapterDesc d{SOLVER_ADAPTER_VERSION_V2, sizeof(SolverAdapterDesc),
        "sgetrf", "float32", "host", "row_major", "scalar",
        reinterpret_cast<const void *>(&aclsolverSgetrf)};
    return &d;
}
extern "C" int solver_adapter_create_v2(const SolverAdapterCaseV2 *c, void *handle,
                                         void *, void **state) {
    if (!state) return 1;
    *state = nullptr;
    if (!c || !handle || c->base.version != SOLVER_ADAPTER_VERSION_V2
        || c->base.struct_size != sizeof(*c) || c->base.batch != 1
        || c->m <= 0 || c->m > 8192 || c->base.n <= 0 || c->base.n > INT32_MAX
        || c->m > INT32_MAX / c->base.n || c->base.lda != c->base.n
        || c->base.b_bytes || c->base.info_count != 1
        || c->in_ipiv_count || c->in_tau_count || c->out_tau_count
        || c->out_ipiv_count != static_cast<size_t>(std::min(c->m, c->base.n))) return 1;
    const size_t cells = static_cast<size_t>(c->m) * c->base.n;
    if (c->base.a_bytes != cells * sizeof(float) || c->base.out_bytes != c->base.a_bytes) return 1;
    auto *s = new(std::nothrow) State{static_cast<aclsolverHandle_t>(handle), c->m,
                                     c->base.n, c->base.lda, {}, {}, -999};
    if (!s) return 2;
    *state = s;
    try { s->a.resize(cells); s->piv.resize(c->out_ipiv_count); }
    catch (const std::bad_alloc &) { return 2; }
    return 0;
}
extern "C" int solver_adapter_reset_v2(void *state, const void *a, size_t bytes,
    const void *, size_t bbytes, const int32_t *, size_t piv_count, const void *, size_t tau_bytes) {
    auto *s = static_cast<State *>(state);
    if (!s || !a || bytes != s->a.size()*sizeof(float) || bbytes || piv_count || tau_bytes) return 1;
    std::memcpy(s->a.data(), a, bytes);
    std::fill(s->piv.begin(), s->piv.end(), -999);
    s->info = -999;
    return 0;
}
extern "C" int solver_adapter_execute(void *state, int *status) {
    auto *s = static_cast<State *>(state);
    if (!s || !status) return 1;
    *status = aclsolverSgetrf(s->handle, s->m, s->n, s->a.data(), s->lda,
                            s->piv.data(), &s->info);
    return 0;
}
extern "C" int solver_adapter_readback_v2(void *state, void *out, size_t bytes,
    int32_t *info, size_t info_count, int32_t *piv, size_t piv_count, void *, size_t tau_bytes) {
    auto *s = static_cast<State *>(state);
    if (!s || !out || !info || !piv || bytes != s->a.size()*sizeof(float)
        || info_count != 1 || piv_count != s->piv.size() || tau_bytes) return 1;
    std::memcpy(out, s->a.data(), bytes);
    std::memcpy(piv, s->piv.data(), piv_count*sizeof(int32_t));
    *info = s->info;
    return 0;
}
extern "C" int solver_adapter_destroy(void *state) {
    delete static_cast<State *>(state);
    return 0;
}
