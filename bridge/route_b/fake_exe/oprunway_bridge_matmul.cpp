/**
 * OpRunway · AscendOpTest 桥 · 路线 B 假 exe（catlass basic matmul 载体，arch 3510/Ascend950）
 *
 * 作用：冒充 AscendOpTest 生成的被测 exe，接收框架传入的 `--case_name --timestamp`，
 * 按框架路径协议读 data_gen 造好的输入 bin、launch catlass basic matmul kernel、
 * 把结果写到框架 compare 会读的 output.bin。这样 data_gen / golden / compare 全复用。
 *
 * 路径协议（回源码核实，均相对 case_path；见 run_test.py:390 / compute.py / output_parse.py）：
 *   输入 : <base>/op_test/<op小写>_<case小写>_<ts>/input/<name>.bin
 *   输出 : <base>/op_test/<op小写>_<case小写>_<ts>/output/<name>.bin
 *   其中 <base> = 环境变量 OPRUNWAY_CASE_PATH（缺省 "."），须与 case json 的 case_path 一致。
 *
 * kernel 主体逐行照抄 examples/43_ascend950_basic_matmul/basic_matmul_tla.cpp，
 * 仅把两端 IO 从 FillRandomData / 进程内 compare 换成 读输入bin / 写输出bin。
 * 因 M==N，43 里 `m>n` 恒为假 → 只保留 else 分支（swizzle 方向 1），钉死单一 kernel 符号，便于 msprof -k。
 *
 * 编译：替换 43 example 的源文件后走 catlass build.sh 编（保持文件名/靶名不变）。
 *   形状默认 512×512×512（fp32），可 -DOPRUNWAY_M= / _N= / _K= 覆盖，但须与 case json 的 shape 一致。
 *   算子名默认 "catlassbasicmatmul"，须与 ir/case json 的 op / op_name 小写一致；-DOPRUNWAY_OP_LOWER=\"...\" 覆盖。
 */

#ifndef K_MAX_SHAPE_DIM
#define K_MAX_SHAPE_DIM 0
#endif

#include "catlass/gemm/kernel/basic_matmul_tla.hpp"

#include "catlass/arch/arch.hpp"
#include "catlass/catlass.hpp"
#include "catlass/gemm/block/block_mmad.hpp"
#include "catlass/gemm/block/block_swizzle.hpp"
#include "catlass/gemm/device/device_gemm.hpp"
#include "catlass/gemm/dispatch_policy.hpp"
#include "catlass/gemm/gemm_type.hpp"
#include "catlass/layout/layout.hpp"
#include "catlass/status.hpp"
#include "tla/layout.hpp"

#include "helper.hpp"   // ACL_CHECK

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <vector>

using namespace Catlass;
using namespace tla;

#ifndef OPRUNWAY_M
#define OPRUNWAY_M 512
#endif
#ifndef OPRUNWAY_N
#define OPRUNWAY_N 512
#endif
#ifndef OPRUNWAY_K
#define OPRUNWAY_K 512
#endif
#ifndef OPRUNWAY_OP_LOWER
#define OPRUNWAY_OP_LOWER "catlassbasicmatmul"
#endif

namespace {

std::string ToLower(std::string s)
{
    for (auto &c : s) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

// 读整块 bin 到 host 向量；文件字节数必须 == count*sizeof(T)，否则报错退出（避免静默错位）。
template <typename T>
bool ReadBin(const std::string &path, std::vector<T> &buf)
{
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f.is_open()) {
        std::cerr << "[ERROR] open input bin failed: " << path << std::endl;
        return false;
    }
    std::streamsize bytes = f.tellg();
    f.seekg(0, std::ios::beg);
    size_t need = buf.size() * sizeof(T);
    if (static_cast<size_t>(bytes) != need) {
        std::cerr << "[ERROR] size mismatch " << path << " : file=" << bytes
                  << " expect=" << need << std::endl;
        return false;
    }
    if (!f.read(reinterpret_cast<char *>(buf.data()), bytes)) {
        std::cerr << "[ERROR] read input bin failed: " << path << std::endl;
        return false;
    }
    return true;
}

bool MkdirP(const std::string &dir)
{
    if (dir.empty()) {
        return true;
    }
    std::string cur;
    size_t pos = 0;
    if (dir[0] == '/') {
        cur = "/";
        pos = 1;
    }
    while (pos <= dir.size()) {
        size_t next = dir.find('/', pos);
        std::string part = dir.substr(pos, next == std::string::npos ? std::string::npos : next - pos);
        if (!part.empty()) {
            if (!cur.empty() && cur.back() != '/') {
                cur += "/";
            }
            cur += part;
            if (mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) {
                std::cerr << "[ERROR] mkdir failed for: " << cur << std::endl;
                return false;
            }
        }
        if (next == std::string::npos) {
            break;
        }
        pos = next + 1;
    }
    return true;
}

// 写 host 向量到 bin；先 mkdir -p 父目录（对齐框架 WriteFile 行为）。
template <typename T>
bool WriteBin(const std::string &path, const std::vector<T> &buf)
{
    size_t slash = path.find_last_of('/');
    if (slash != std::string::npos) {
        if (!MkdirP(path.substr(0, slash))) {
            std::cerr << "[ERROR] mkdir failed for: " << path << std::endl;
            return false;
        }
    }
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    if (!f.is_open()) {
        std::cerr << "[ERROR] open output bin failed: " << path << std::endl;
        return false;
    }
    f.write(reinterpret_cast<const char *>(buf.data()), buf.size() * sizeof(T));
    if (!f) {
        std::cerr << "[ERROR] write output bin failed: " << path << std::endl;
        return false;
    }
    return true;
}

}  // namespace

static int Run(const std::string &caseName, const std::string &timestamp)
{
    const std::string base = []() {
        const char *e = std::getenv("OPRUNWAY_CASE_PATH");
        return std::string((e && e[0]) ? e : ".");
    }();
    const std::string dir =
        base + "/op_test/" + std::string(OPRUNWAY_OP_LOWER) + "_" + ToLower(caseName) + "_" + timestamp;
    const std::string inX1 = dir + "/input/x1.bin";
    const std::string inX2 = dir + "/input/x2.bin";
    const std::string outY = dir + "/output/y.bin";

    const uint32_t m = OPRUNWAY_M;
    const uint32_t n = OPRUNWAY_N;
    const uint32_t k = OPRUNWAY_K;

    using ElementA = float;
    using ElementB = float;
    using ElementC = float;
    using ElementBias = void;  // 无 bias

    using LayoutTagA = layout::RowMajor;
    using LayoutTagB = layout::RowMajor;
    using LayoutTagC = layout::RowMajor;
    LayoutTagA tagA = LayoutTagA::MakeLayout<ElementA>(m, k);
    LayoutTagB tagB = LayoutTagB::MakeLayout<ElementB>(k, n);
    LayoutTagC tagC = LayoutTagC::MakeLayout<ElementC>(m, n);

    size_t lenA = tagA.Capacity();
    size_t lenB = tagB.Capacity();
    size_t lenC = tagC.Capacity();
    size_t sizeA = lenA * sizeof(ElementA);
    size_t sizeB = lenB * sizeof(ElementB);
    size_t sizeC = lenC * sizeof(ElementC);
    size_t sizeWorkspace;

    // --- 输入：读 data_gen 造好的 bin（替代 43 的 FillRandomData）---
    std::vector<ElementA> hostA(lenA);
    std::vector<ElementB> hostB(lenB);
    if (!ReadBin(inX1, hostA) || !ReadBin(inX2, hostB)) {
        return 1;
    }

    aclrtStream stream{nullptr};
    ACL_CHECK(aclInit(nullptr));
    ACL_CHECK(aclrtSetDevice(0));
    ACL_CHECK(aclrtCreateStream(&stream));

    uint8_t *deviceA{nullptr};
    ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&deviceA), sizeA, ACL_MEM_MALLOC_HUGE_FIRST));
    ACL_CHECK(aclrtMemcpy(deviceA, sizeA, hostA.data(), sizeA, ACL_MEMCPY_HOST_TO_DEVICE));

    uint8_t *deviceB{nullptr};
    ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&deviceB), sizeB, ACL_MEM_MALLOC_HUGE_FIRST));
    ACL_CHECK(aclrtMemcpy(deviceB, sizeB, hostB.data(), sizeB, ACL_MEMCPY_HOST_TO_DEVICE));

    uint8_t *deviceC{nullptr};
    ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&deviceC), sizeC, ACL_MEM_MALLOC_HUGE_FIRST));

    uint8_t *deviceWorkspace{nullptr};

    auto aicCoreNum = platform_ascendc::PlatformAscendCManager::GetInstance()->GetCoreNumAic();

    // Ascend950（arch 3510）—— 与 43 一致
    using ArchTag = Arch::Ascend950;
    constexpr bool enableUnitFlag = true;
    constexpr bool useHF32 = false;
    constexpr bool enableL1Resident = false;
    constexpr uint32_t l0CStages = 1;
    constexpr uint32_t l1AStages = 2;
    constexpr uint32_t l1BStages = 2;
    constexpr uint32_t l0AStages = 2;
    constexpr uint32_t l0BStages = 2;
    using DispatchPolicy = Gemm::MmadPingpong<
        ArchTag, enableUnitFlag, useHF32, l0CStages, enableL1Resident,
        l1AStages, l1BStages, l0AStages, l0BStages>;
    using L1TileShape = Shape<Int<256>, Int<256>, Int<128>>;
    using L0TileShape = Shape<Int<256>, Int<256>, Int<32>>;

    auto layoutA = tla::MakeLayout<ElementA, LayoutTagA>(m, k);
    auto layoutB = tla::MakeLayout<ElementB, LayoutTagB>(k, n);
    auto layoutC = tla::MakeLayout<ElementC, LayoutTagC>(m, n);

    using TileCopy = Gemm::Tile::PackedTileCopyTla<
        ArchTag, ElementA, LayoutTagA, ElementB, LayoutTagB, ElementC, LayoutTagC, ElementBias>;
    using BlockMmad = Gemm::Block::BlockMmadTla<
        DispatchPolicy, L1TileShape, L0TileShape, ElementA, ElementB, ElementC, ElementBias, TileCopy>;
    using BlockEpilogue = void;

    GemmCoord problemShape{m, n, k};
    uint32_t taskNum = CeilDiv(m, tla::get<0>(L1TileShape{})) *
                       CeilDiv(n, tla::get<1>(L1TileShape{}));
    uint32_t aicCoreUsed = min(aicCoreNum, taskNum);

    // M==N → 单一 swizzle 方向（对齐 43 的 else 分支），钉死单一 kernel 符号
    using BlockScheduler = typename Gemm::Block::GemmIdentityBlockSwizzle<3, 1>;
    using MatmulKernel = Gemm::Kernel::BasicMatmulTla<BlockMmad, BlockEpilogue, BlockScheduler>;
    using MatmulAdapter = Gemm::Device::DeviceGemm<MatmulKernel>;

    MatmulKernel::Arguments arguments{
        problemShape, deviceA, layoutA, deviceB, layoutB, deviceC, layoutC, nullptr};

    MatmulAdapter matmulOp;
    matmulOp.CanImplement(arguments);
    sizeWorkspace = matmulOp.GetWorkspaceSize(arguments);
    if (sizeWorkspace > 0) {
        ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&deviceWorkspace), sizeWorkspace, ACL_MEM_MALLOC_HUGE_FIRST));
    }
    matmulOp.Initialize(arguments, deviceWorkspace);
    matmulOp(stream, aicCoreUsed);
    ACL_CHECK(aclrtSynchronizeStream(stream));

    // --- 输出：D2H 后写框架 compare 会读的 output bin（替代 43 的进程内 compare）---
    std::vector<ElementC> hostC(lenC);
    ACL_CHECK(aclrtMemcpy(hostC.data(), sizeC, deviceC, sizeC, ACL_MEMCPY_DEVICE_TO_HOST));

    bool okWrite = WriteBin(outY, hostC);

    ACL_CHECK(aclrtFree(deviceA));
    ACL_CHECK(aclrtFree(deviceB));
    ACL_CHECK(aclrtFree(deviceC));
    if (sizeWorkspace > 0) {
        ACL_CHECK(aclrtFree(deviceWorkspace));
    }
    ACL_CHECK(aclrtDestroyStream(stream));
    ACL_CHECK(aclrtResetDevice(0));
    ACL_CHECK(aclFinalize());

    if (!okWrite) {
        return 1;
    }
    std::cout << "[OPRUNWAY] wrote output: " << outY << std::endl;
    return 0;
}

int main(int argc, const char **argv)
{
    std::string caseName;
    std::string timestamp;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--case_name") == 0 && i + 1 < argc) {
            caseName = argv[++i];
        } else if (std::strcmp(argv[i], "--timestamp") == 0 && i + 1 < argc) {
            timestamp = argv[++i];
        } else if (std::strcmp(argv[i], "--output_shapes") == 0 && i + 1 < argc) {
            ++i;  // basic matmul 定 shape，忽略但不报错
        }
    }
    if (caseName.empty() || timestamp.empty()) {
        std::cout << "Usage: " << argv[0] << " --case_name <case> --timestamp <ts> [--output_shapes <dict>]"
                  << std::endl;
        return 1;
    }
    return Run(caseName, timestamp);
}
