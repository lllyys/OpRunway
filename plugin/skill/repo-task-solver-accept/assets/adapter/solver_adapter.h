#ifndef SOLVER_ADAPTER_H
#define SOLVER_ADAPTER_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define SOLVER_ADAPTER_VERSION 1u
/* Transport: tightly packed row-major Host buffers. complex64 is interleaved
 * float32(real,imag). Actual DUT memory/layout is described separately. */
typedef struct {
    uint32_t version, struct_size;
    const char *op, *dtype, *dut_abi, *dut_layout, *info_kind;
    const void *target_symbol; /* Address of the actual public DUT function. */
} SolverAdapterDesc;
typedef struct {
    uint32_t version, struct_size;
    int64_t n, nrhs, batch, lda, ldb;
    char uplo;
    const char *info_probe, *input_kind; /* matrix or cholesky_factor */
    size_t a_bytes, b_bytes, out_bytes, info_count;
} SolverAdapterCase;
const SolverAdapterDesc *solver_adapter_descriptor(void);
/* Handle and stream are owned by harness. create must publish partially
 * allocated context through state so destroy can clean it after failure. */
int solver_adapter_create(const SolverAdapterCase *c, void *handle, void *stream, void **state);
/* Restore all modified inputs, outputs, info; never call the target operator. */
int solver_adapter_reset(void *state, const void *a, size_t a_bytes, const void *b, size_t b_bytes);
/* Return adapter status; separately return unmodified DUT API return code. */
int solver_adapter_execute(void *state, int *dut_status);
/* Harness synchronizes the supplied stream before readback. */
int solver_adapter_readback(void *state, void *out, size_t out_bytes, int32_t *info, size_t info_count);
int solver_adapter_destroy(void *state); /* null/partially initialized is valid */
#ifdef __cplusplus
}
#endif
#endif
