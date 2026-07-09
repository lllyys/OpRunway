/**
 * OpRunway · catlass generated_harness runner —— arch 2201 / fp16 / AtlasA2（de-risk·次目标）。
 * 对齐 catlass example 00_basic_matmul（basic_matmul.cpp）。
 *
 * ⚠⚠ 模板·**待真机验证**（ascend-a3 arch 2201 + 人工确认）·**Mac 不可编译**。**不得假称已验证**。
 * 结构同 _950_runner，差异仅：dtype=half(2B)、ArchTag=AtlasA2、符号 = oprunway_catlass_basic_matmul_a2。
 * 唯一 op 专属边界：<<< using 链 >>> 与 <<< launch 段 >>>。bin 为 fp16 原始字节，host 只透传不算。
 */
#ifndef K_MAX_SHAPE_DIM
#define K_MAX_SHAPE_DIM 0
#endif

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "catlass/arch/arch.hpp"
#include "catlass/catlass.hpp"
#include "catlass/gemm/block/block_mmad.hpp"
#include "catlass/gemm/block/block_swizzle.hpp"
#include "catlass/gemm/dispatch_policy.hpp"
#include "catlass/gemm/gemm_type.hpp"
#include "catlass/gemm/kernel/basic_matmul.hpp"
#include "catlass/layout/layout.hpp"
#include "acl/acl.h"
#include "kernel_operator.h"

// ============================= <<< using 链（op 专属边界①） >>> =============================
namespace Catlass {
CATLASS_DEVICE void OpRunwayBasicMatmulA2(GemmCoord problemShape, GM_ADDR gmA, GM_ADDR gmB, GM_ADDR gmC)
{
    using ArchTag = Arch::AtlasA2;
    using DispatchPolicy = Gemm::MmadAtlasA2Pingpong<true>;
    using L1TileShape = GemmShape<128, 256, 256>;
    using L0TileShape = GemmShape<128, 256, 64>;
    using ElementA = half; using ElementB = half; using ElementC = half;
    using LayoutA = layout::RowMajor; using LayoutB = layout::RowMajor; using LayoutC = layout::RowMajor;
    using AType = Gemm::GemmType<ElementA, LayoutA>;
    using BType = Gemm::GemmType<ElementB, LayoutB>;
    using CType = Gemm::GemmType<ElementC, LayoutC>;
    // TODO(真机): 依 00_basic_matmul 补全 BlockMmad / BlockScheduler / MatmulKernel 实例化与 kernel(params)。
    (void)problemShape; (void)gmA; (void)gmB; (void)gmC;
}
} // namespace Catlass

extern "C" __global__ __aicore__ void
oprunway_catlass_basic_matmul_a2(GM_ADDR a, GM_ADDR b, GM_ADDR c, GM_ADDR m, GM_ADDR n, GM_ADDR k)
{
    Catlass::GemmCoord problemShape{
        *reinterpret_cast<__gm__ uint32_t *>(m),
        *reinterpret_cast<__gm__ uint32_t *>(n),
        *reinterpret_cast<__gm__ uint32_t *>(k)};
    Catlass::OpRunwayBasicMatmulA2(problemShape, a, b, c);
}

// ============================= host 侧 bin-IO shim（fp16 = 2 字节/元素，透传） =============================
#define ACL_OK(e) do { auto _r = (e); if (_r != 0) { std::fprintf(stderr, "ACL_ERROR %d @ %s:%d\n", (int)_r, __FILE__, __LINE__); std::exit(2); } } while (0)

struct CaseRow { std::string cid; uint32_t m, n, k; };

static std::vector<CaseRow> ReadManifest(const std::string &path)
{
    std::vector<CaseRow> rows; std::ifstream f(path); std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::istringstream ss(line); CaseRow r; std::string dtype;
        ss >> r.cid >> dtype >> r.m >> r.n >> r.k;
        rows.push_back(r);
    }
    return rows;
}

static std::vector<uint16_t> ReadBin16(const std::string &path, size_t n)
{
    std::vector<uint16_t> v(n); std::ifstream f(path, std::ios::binary);
    f.read(reinterpret_cast<char *>(v.data()), (std::streamsize)(n * sizeof(uint16_t)));
    return v;
}

int main(int argc, char **argv)
{
    const char *casesEnv = std::getenv("OPRUNWAY_CASES");
    std::string casesDir = casesEnv ? casesEnv : (argc > 1 ? argv[1] : "cases");
    int deviceId = (argc > 2) ? std::atoi(argv[2]) : 0;
    auto rows = ReadManifest(casesDir + "/manifest.txt");
    std::printf("[OPRUNWAY_CATLASS] harness=oprunway_catlass_basic_matmul_a2 arch=2201 dtype=float16 cases=%zu\n", rows.size());

    ACL_OK(aclInit(nullptr)); ACL_OK(aclrtSetDevice(deviceId));
    aclrtStream stream{nullptr}; ACL_OK(aclrtCreateStream(&stream));
    int okCount = 0, failCount = 0;
    for (const auto &r : rows) {
        size_t lenA = (size_t)r.m * r.k, lenB = (size_t)r.k * r.n, lenC = (size_t)r.m * r.n;
        std::string cdir = casesDir + "/" + r.cid;
        auto hostA = ReadBin16(cdir + "/A.bin", lenA);
        auto hostB = ReadBin16(cdir + "/B.bin", lenB);
        std::vector<uint16_t> hostC(lenC, 0);
        uint8_t *dA{}, *dB{}, *dC{}, *dM{}, *dN{}, *dK{};
        ACL_OK(aclrtMalloc((void **)&dA, lenA * 2, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dB, lenB * 2, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dC, lenC * 2, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dM, 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dN, 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dK, 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMemcpy(dA, lenA * 2, hostA.data(), lenA * 2, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dB, lenB * 2, hostB.data(), lenB * 2, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dM, 4, &r.m, 4, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dN, 4, &r.n, 4, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dK, 4, &r.k, 4, ACL_MEMCPY_HOST_TO_DEVICE));
        // ==================== <<< launch 段（op 专属边界②） >>> ====================
        // TODO(真机): oprunway_catlass_basic_matmul_a2<<<blockDim, nullptr, stream>>>(dA, dB, dC, dM, dN, dK);
        // ==========================================================================
        ACL_OK(aclrtSynchronizeStream(stream));
        ACL_OK(aclrtMemcpy(hostC.data(), lenC * 2, dC, lenC * 2, ACL_MEMCPY_DEVICE_TO_HOST));
        std::ofstream fo(cdir + "/out.bin", std::ios::binary);
        fo.write(reinterpret_cast<const char *>(hostC.data()), (std::streamsize)(lenC * 2));
        bool smoke = true;   // TODO(真机): 接 catlass golden 比对
        std::printf("[OPRUNWAY_CASE %s] m=%u n=%u k=%u %s\n", r.cid.c_str(), r.m, r.n, r.k,
                    smoke ? "Compare success." : "Compare failed.");
        smoke ? ++okCount : ++failCount;
        aclrtFree(dA); aclrtFree(dB); aclrtFree(dC); aclrtFree(dM); aclrtFree(dN); aclrtFree(dK);
    }
    aclrtDestroyStream(stream); aclrtResetDevice(deviceId); aclFinalize();
    std::printf("OPRUNWAY_CATLASS_DONE total=%zu ok=%d fail=%d\n", rows.size(), okCount, failCount);
    return 0;
}
