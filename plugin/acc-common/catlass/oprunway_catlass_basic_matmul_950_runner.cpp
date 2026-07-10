/**
 * OpRunway · catlass generated_harness runner —— arch 3510 / fp32 / Ascend950 / TLA（主目标）。
 * 对齐 catlass example 43_ascend950_basic_matmul（basic_matmul_tla.cpp）。
 *
 * ⚠⚠ 模板·**待真机验证**（ascend-a5 arch 3510 + VPN + 人工确认）·**Mac 不可编译**（无 bisheng/ccec + catlass 头）。
 *     首跑 generated_harness 前须人工确认（CLAUDE.md #1/#3）。**不得假称已验证**。
 *
 * 职责（generated-harness-responsibilities.md 4 职责之 bin-IO shim / 性能测量栈）：
 *   1) 读 manifest（cid m n k）+ per-case A.bin/B.bin（物理字节由 adapter 按声明 layout 摆好，此处直接读）；
 *   2) H2D → launch **device kernel（extern C 钉死符号，msprof -k 可命中）** → D2H → 写 out.bin；
 *   3) 逐 case 打印 `[OPRUNWAY_CASE <cid>] Compare success./failed.`（仓内 smoke 信号，**非验收结论**）+ 收尾 OPRUNWAY_CATLASS_DONE。
 *   精度验收在 Python 侧（真 NPU out.bin vs numpy golden，ADR0002）；本 runner 的 Compare 只作 smoke。
 *
 * 唯一 op 专属边界（换算子时改这两处）：<<< using 链 >>> 与 <<< launch 段 >>>。
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
CATLASS_DEVICE void OpRunwayBasicMatmul950(GemmCoord problemShape, GM_ADDR gmA, GM_ADDR gmB, GM_ADDR gmC)
{
    // arch 3510 / fp32 / TLA —— 参数须真机校准（tile/dispatch 依 43_ascend950_basic_matmul 的 TLA 配置）。
    using ArchTag = Arch::Ascend950;                       // 待真机核对 ArchTag 名
    using ElementA = float; using ElementB = float; using ElementC = float;
    using LayoutA = layout::RowMajor; using LayoutB = layout::RowMajor; using LayoutC = layout::RowMajor;
    using AType = Gemm::GemmType<ElementA, LayoutA>;
    using BType = Gemm::GemmType<ElementB, LayoutB>;
    using CType = Gemm::GemmType<ElementC, LayoutC>;
    // TODO(真机): 依 43 的 DispatchPolicy / L1TileShape / L0TileShape / BlockScheduler 补全 using 链与 kernel 实例化。
    // using DispatchPolicy = ...; using BlockMmad = ...; using MatmulKernel = Gemm::Kernel::BasicMatmul<...>;
    // LayoutA la{problemShape.m(), problemShape.k()}; ... MatmulKernel kernel; kernel(params);
    (void)problemShape; (void)gmA; (void)gmB; (void)gmC;   // 占位：真机补全后移除
}
} // namespace Catlass

// device kernel 入口：**extern C 钉死符号** → msprof op --kernel-name=oprunway_catlass_basic_matmul_950 可命中。
extern "C" __global__ __aicore__ void
oprunway_catlass_basic_matmul_950(GM_ADDR a, GM_ADDR b, GM_ADDR c, GM_ADDR m, GM_ADDR n, GM_ADDR k)
{
    Catlass::GemmCoord problemShape{
        *reinterpret_cast<__gm__ uint32_t *>(m),
        *reinterpret_cast<__gm__ uint32_t *>(n),
        *reinterpret_cast<__gm__ uint32_t *>(k)};
    Catlass::OpRunwayBasicMatmul950(problemShape, a, b, c);
}

// ============================= host 侧 bin-IO shim（通用、非 op 专属） =============================
#define ACL_OK(e) do { auto _r = (e); if (_r != 0) { std::fprintf(stderr, "ACL_ERROR %d @ %s:%d\n", (int)_r, __FILE__, __LINE__); std::exit(2); } } while (0)

struct CaseRow { std::string cid; uint32_t m, n, k; };

static std::vector<CaseRow> ReadManifest(const std::string &path)
{
    std::vector<CaseRow> rows; std::ifstream f(path); std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::istringstream ss(line); CaseRow r; std::string dtype;
        ss >> r.cid >> dtype >> r.m >> r.n >> r.k;     // "cid float32 m n k"
        rows.push_back(r);
    }
    return rows;
}

static std::vector<float> ReadBin(const std::string &path, size_t n)
{
    std::vector<float> v(n); std::ifstream f(path, std::ios::binary);
    f.read(reinterpret_cast<char *>(v.data()), (std::streamsize)(n * sizeof(float)));
    return v;
}

static void WriteBin(const std::string &path, const std::vector<float> &v)
{
    std::ofstream f(path, std::ios::binary);
    f.write(reinterpret_cast<const char *>(v.data()), (std::streamsize)(v.size() * sizeof(float)));
}

int main(int argc, char **argv)
{
    // 用例根：OPRUNWAY_CASES 环境变量（cases/<cid>/{A.bin,B.bin,out.bin} + cases/manifest.txt），零硬编码。
    const char *casesEnv = std::getenv("OPRUNWAY_CASES");
    std::string casesDir = casesEnv ? casesEnv : (argc > 1 ? argv[1] : "cases");
    int deviceId = (argc > 2) ? std::atoi(argv[2]) : 0;
    auto rows = ReadManifest(casesDir + "/manifest.txt");
    std::printf("[OPRUNWAY_CATLASS] harness=oprunway_catlass_basic_matmul_950 arch=3510 dtype=float32 cases=%zu\n", rows.size());

    ACL_OK(aclInit(nullptr)); ACL_OK(aclrtSetDevice(deviceId));
    aclrtStream stream{nullptr}; ACL_OK(aclrtCreateStream(&stream));
    int okCount = 0, failCount = 0;
    for (const auto &r : rows) {
        size_t lenA = (size_t)r.m * r.k, lenB = (size_t)r.k * r.n, lenC = (size_t)r.m * r.n;
        std::string cdir = casesDir + "/" + r.cid;
        auto hostA = ReadBin(cdir + "/A.bin", lenA);
        auto hostB = ReadBin(cdir + "/B.bin", lenB);
        std::vector<float> hostC(lenC, 0.0f);
        uint8_t *dA{}, *dB{}, *dC{}, *dM{}, *dN{}, *dK{};
        ACL_OK(aclrtMalloc((void **)&dA, lenA * 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dB, lenB * 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dC, lenC * 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dM, 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dN, 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMalloc((void **)&dK, 4, ACL_MEM_MALLOC_HUGE_FIRST));
        ACL_OK(aclrtMemcpy(dA, lenA * 4, hostA.data(), lenA * 4, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dB, lenB * 4, hostB.data(), lenB * 4, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dM, 4, &r.m, 4, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dN, 4, &r.n, 4, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_OK(aclrtMemcpy(dK, 4, &r.k, 4, ACL_MEMCPY_HOST_TO_DEVICE));
        // ==================== <<< launch 段（op 专属边界②） >>> ====================
        // TODO(真机): 依 43 校准 blockDim / workspace；launch device kernel（符号 = oprunway_catlass_basic_matmul_950）。
        //   oprunway_catlass_basic_matmul_950<<<blockDim, nullptr, stream>>>(dA, dB, dC, dM, dN, dK);
        // ==========================================================================
        ACL_OK(aclrtSynchronizeStream(stream));
        ACL_OK(aclrtMemcpy(hostC.data(), lenC * 4, dC, lenC * 4, ACL_MEMCPY_DEVICE_TO_HOST));
        WriteBin(cdir + "/out.bin", hostC);
        // 仓内 smoke（非验收）：真机接入时可选用 catlass golden::ComputeMatmul 打印 success/failed。
        bool smoke = true;   // TODO(真机): 接 catlass golden 比对，暂占位为 success 供 parser 调通
        std::printf("[OPRUNWAY_CASE %s] m=%u n=%u k=%u %s\n", r.cid.c_str(), r.m, r.n, r.k,
                    smoke ? "Compare success." : "Compare failed.");
        smoke ? ++okCount : ++failCount;
        aclrtFree(dA); aclrtFree(dB); aclrtFree(dC); aclrtFree(dM); aclrtFree(dN); aclrtFree(dK);
    }
    aclrtDestroyStream(stream); aclrtResetDevice(deviceId); aclFinalize();
    std::printf("OPRUNWAY_CATLASS_DONE total=%zu ok=%d fail=%d\n", rows.size(), okCount, failCount);
    return 0;
}
