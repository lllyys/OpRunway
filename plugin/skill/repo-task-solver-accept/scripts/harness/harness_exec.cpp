/*
 * harness_exec.cpp —— 验收共享 harness 的 S2 执行子进程（一个 case 一个进程）。
 *
 * 为什么是 C++ 而不是 ctypes：公开头 include/cann_ops_solver.h 把算子声明放在
 * `extern "C"` 块外，且参数带 `std::complex<float>`——C linkage 在类型层面就不可能，
 * ctypes 按 C 原名取不到符号（PROBE §3.1，基线 05e6b09 复核仍然成立）。所以执行段
 * 对公开头编译，不依赖符号名修饰规则。
 *
 * 编译（普通 g++，不需要 AscendC 编译器——test 下各算子的 CMakeLists 用的就是 CXX）：
 *   g++ -std=c++17 -O2 -o harness_exec harness_exec.cpp \
 *       -I<repo>/include -I$ASCEND_HOME_PATH/$(uname -m)-linux/include \
 *       <repo>/build/libops_solver.so $ASCEND_HOME_PATH/lib64/libascendcl.so -ldl
 *   另加 -DHARNESS_OP_<OP>=1：交付头里真的声明了入口的算子才编进来（见 op_abi.py）。
 *
 * 调用：harness_exec <spec 文件>
 *
 * spec 是 `key=value` 行文本（`#` 起行为注释），必需键见 kRequired。进程只读它，
 * 不读环境变量里的规格，所以一份 spec 加一组输入 bin 就能脱离编排层独立复跑。
 *
 * spec 核验：调用前把 spec 的 dtype、call_shape、批量性与**编译期算子描述**
 * （kOpDescs，每个编进来的算子一行）逐项比对，不一致就退 spec 错误并说清期望与
 * 实际。缓冲尺寸的乘法逐步查 size_t 溢出——spec 填错 dtype 会按半尺寸分配再按全
 * 尺寸读写，那是越界，不是判定问题。
 *
 * 复跑：同一子进程、同一 stream 内重复 runs 次，每轮先从原始副本恢复输入再调算子，
 * 第 2..N 轮与第 1 轮逐键 bit-wise 比对（memcmp 字节域，NaN 按位等同）。纪律三条：
 *   - 第 1 轮跑完立刻落盘，后面哪一轮崩了首轮现场都还在；
 *   - 第 2 轮及以后算子返回非成功时，该轮输出另存 *.fail.bin，不覆盖首轮；
 *   - result 里分开记 rerun_runs_requested 与 rerun_runs_completed，规定轮数没跑完
 *     时 rerun_consistent 记 `unknown` 而不是 `1`——没做完的比较不许报一致。
 *   失配时该轮输出另存 *.diff.bin 留证，rerun_consistent 记 `0`。
 *
 * 三键落盘（{out32, info, status}）：out32 与 info 是裸二进制无头文件，形状与 dtype
 * 写在 result 文本里由编排层组装 npz——npz 依赖不进 C++ 侧。
 *
 * 收尾：全部 ACL 释放走同一个 Teardown，逐项查返回值。主流程成功而清理失败时报
 * ACL 错误（清理失败意味着设备侧状态不明，不能当成功交付）；主流程已失败时保留
 * 首个主错误，清理失败只进 result 的 teardown_detail。
 *
 * 退出码（编排层按此归类，见 exec_case.py 的 EXIT_KIND）：
 *   0  正常           10 spec 错误      11 I/O 错误        12 口径/算子未实现
 *   13 ACL 前置或收尾失败              14 算子返回非成功  15 复跑 bit-wise 失配
 */

#include <acl/acl.h>
#include <dlfcn.h>

#include <algorithm>
#include <chrono>
#include <complex>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "cann_ops_solver.h"

namespace {

#include "executor_io.hpp"

// ---------------------------------------------------------------------------
// 编译期算子描述：spec 核验的对照面，每个编进来的算子一行
// ---------------------------------------------------------------------------

// 与 op_abi.py 的 ABI 表同源（tests/test_harness_exec.py 逐行核对两边不漂移）。
// spec 说的 dtype/call_shape/批量性必须与这里一致，否则缓冲尺寸就是错的。
struct OpDesc {
    const char *op;
    const char *call_shape;
    const char *dtype;
    const char *info_kind;
    bool batched;
};

constexpr OpDesc kOpDescs[] = {
#ifdef HARNESS_OP_CMATINV_BATCHED
    {"cmatinv_batched", "outofplace_a_ainv", "complex64", "scalar", true},
#endif
#ifdef HARNESS_OP_CGETRI_BATCHED
    {"cgetri_batched", "outofplace_a_ainv", "complex64", "scalar", true},
#endif
#ifdef HARNESS_OP_CGETRI
    {"cgetri", "inplace_a", "complex64", "scalar", false},
#endif
#ifdef HARNESS_OP_SGETRI
    {"sgetri", "inplace_a", "float32", "scalar", false},
#endif
    {nullptr, nullptr, nullptr, nullptr, false},
};

const OpDesc *FindOpDesc(const std::string &op) {
    for (const OpDesc *d = kOpDescs; d->op != nullptr; ++d) {
        if (op == d->op) return d;
    }
    return nullptr;
}

// ---------------------------------------------------------------------------
// Device 口径（任务书 §2.1–2.4）：搬运管道已实做并可自检，算子调用点是唯一 TODO
// ---------------------------------------------------------------------------

// 调用方备 Device 内存 + H2D/D2H 的最小往返。Cholesky 交付后，`abi=device` 的算子
// 调用插在 H2D 与 D2H 之间：设备指针来自本函数同一套 aclrtMalloc，devInfo 另开一块
// int32、调用后单独 D2H；Workspace 与 Lwork 由交付头的 `_bufferSize` 接口定尺寸
// （现工程无此接口，固定内部 16 MB，PROBE §3.2）。
// TODO(cholesky-delivery): 签名、uplo 枚举值、列主序与 lda>n 的 padding 行为以**交付头**
// 为准核对后填入；在此之前 `abi=device` 只放行 op=_device_selftest。
int DeviceRoundTrip(const void *host_src, void *host_dst, size_t bytes,
                    aclrtStream stream, std::string *err) {
    void *dev = nullptr;
    int ret = aclrtMalloc(&dev, bytes, ACL_MEM_MALLOC_HUGE_FIRST);
    if (ret != 0) {
        *err = "aclrtMalloc ret=" + std::to_string(ret);
        return ret;
    }
    ret = aclrtMemcpy(dev, bytes, host_src, bytes, ACL_MEMCPY_HOST_TO_DEVICE);
    if (ret == 0) ret = aclrtSynchronizeStream(stream);
    if (ret == 0) ret = aclrtMemcpy(host_dst, bytes, dev, bytes, ACL_MEMCPY_DEVICE_TO_HOST);
    if (ret != 0) *err = "H2D/D2H ret=" + std::to_string(ret);
    const int fret = aclrtFree(dev);     // 释放失败说明设备侧状态不明，不能当没事
    if (fret != 0) {
        const std::string one = "aclrtFree ret=" + std::to_string(fret);
        *err = err->empty() ? one : *err + "; " + one;
        if (ret == 0) ret = fret;
    }
    return ret;
}

// ---------------------------------------------------------------------------
// 算子调用：按 call_shape 分支，一个形状服务多个算子
// ---------------------------------------------------------------------------

struct Call {
    aclsolverHandle_t handle;
    std::string op;
    std::string call_shape;
    std::string dtype;
    int64_t n;
    int64_t lda;
    int64_t batch;
    void *a;       // 工作输入缓冲（原地形状即 out32 所在）
    void *ainv;    // 异地形状的输出缓冲
    int32_t *info;
};

// 返回算子自己的返回码；形状/算子未编进来时把 *unsupported 置真。
int Invoke(const Call &c, bool *unsupported) {
    *unsupported = false;

    if (c.call_shape == "outofplace_a_ainv") {
#ifdef HARNESS_OP_CMATINV_BATCHED
        if (c.op == "cmatinv_batched")
            return aclsolverCmatinvBatched(
                c.handle, c.n, static_cast<std::complex<float> *>(c.a), c.lda,
                static_cast<std::complex<float> *>(c.ainv), c.lda, c.info, c.batch);
#endif
#ifdef HARNESS_OP_CGETRI_BATCHED
        if (c.op == "cgetri_batched")
            return aclsolverCgetriBatched(
                c.handle, c.n, static_cast<std::complex<float> *>(c.a), c.lda,
                static_cast<std::complex<float> *>(c.ainv), c.lda, c.info, c.batch);
#endif
    } else if (c.call_shape == "inplace_a") {
#ifdef HARNESS_OP_CGETRI
        if (c.op == "cgetri")
            return aclsolverCgetri(c.handle, c.n,
                                   static_cast<std::complex<float> *>(c.a),
                                   c.lda, c.info);
#endif
#ifdef HARNESS_OP_SGETRI
        if (c.op == "sgetri")
            return aclsolverSgetri(c.handle, c.n, static_cast<float *>(c.a),
                                   c.lda, c.info);
#endif
    }

    *unsupported = true;
    return -1;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc != 2) {
        std::fprintf(stderr, "用法: harness_exec <spec 文件>\n");
        return kSpecError;
    }

    Spec spec;
    std::string err;
    if (!spec.Load(argv[1], &err)) {
        std::fprintf(stderr, "[harness_exec] spec_error: %s\n", err.c_str());
        return kSpecError;
    }

    const std::string result_path = spec.Str("result");
    const std::string op = spec.Str("op");
    const std::string abi = spec.Str("abi");
    const std::string shape = spec.Str("call_shape");
    const std::string dtype = spec.Str("dtype");
    const int64_t n = spec.Num("n");
    const int64_t batch = spec.Num("batch", 1);
    const int64_t lda = spec.Num("lda", n);
    const int64_t nrhs = spec.Num("nrhs", 0);
    const int runs = static_cast<int>(spec.Num("runs", 1));
    const int device_id = static_cast<int>(spec.Num("device_id", 0));

    Result r;
    r.Set("op", op);
    r.Set("abi", abi);
    r.Set("call_shape", shape);
    r.Set("dtype", dtype);
    r.SetNum("n", n);
    r.SetNum("batch", batch);
    r.SetNum("nrhs", nrhs);
    r.SetNum("runs", runs);

    if (n <= 0 || batch <= 0 || runs <= 0) {
        return Bail(r, result_path, kSpecError, "spec_error",
                    "n/batch/runs 必须为正");
    }
    if (spec.Str("layout", "row_major") != "row_major" || lda != n) {
        return Bail(r, result_path, kSpecError, "spec_error",
                    "only compact row_major with lda=n is implemented");
    }
    r.SetNum("rerun_runs_completed", 0);
    r.Set("rerun_consistent", "unknown");
    if (!r.Flush(result_path)) return kIoError;
    const size_t itemsize = (dtype == "complex64") ? 8 : (dtype == "float32") ? 4 : 0;
    if (itemsize == 0) {
        return Bail(r, result_path, kSpecError, "spec_error", "dtype 只支持 float32/complex64");
    }
    if (abi != "host" && abi != "device") {
        return Bail(r, result_path, kSpecError, "spec_error", "abi 只支持 host/device");
    }
    if (abi == "device" && op != "_device_selftest") {
        return Bail(r, result_path, kUnsupported, "unsupported",
                    "Device 口径的算子调用点待 Cholesky 交付后按交付头实做"
                    "（见 DeviceRoundTrip 的 TODO）；搬运管道可用 op=_device_selftest 自检");
    }

    // --- spec 核验：spec 说的与编译期算子描述必须一致，否则缓冲尺寸就是错的 ---
    if (op != "_device_selftest") {
        const OpDesc *desc = FindOpDesc(op);
        if (desc == nullptr) {
            return Bail(r, result_path, kUnsupported, "unsupported",
                        "算子 " + op + " 没有编进本执行件（缺 -DHARNESS_OP_*，"
                        "或该算子的调用分支未实现）");
        }
        if (spec.Str("info_kind") != desc->info_kind) {
            return Bail(r, result_path, kSpecError, "spec_error",
                        "info_kind does not match compiled operator descriptor");
        }
        if (dtype != desc->dtype) {
            return Bail(r, result_path, kSpecError, "spec_error",
                        "dtype 与算子不符：" + op + " 期望 " + desc->dtype +
                            "，spec 给的是 " + dtype);
        }
        if (shape != desc->call_shape) {
            return Bail(r, result_path, kSpecError, "spec_error",
                        "call_shape 与算子不符：" + op + " 期望 " +
                            desc->call_shape + "，spec 给的是 " + shape);
        }
        if (!desc->batched && batch != 1) {
            return Bail(r, result_path, kSpecError, "spec_error",
                        "批量性与算子不符：" + op +
                            " 是非批量入口，期望 batch=1，spec 给的是 batch=" +
                            std::to_string(batch));
        }
    }

    // 缓冲尺寸逐步查溢出：填错 dtype 或超大 n 会回绕成小缓冲，再按大尺寸读写即越界
    size_t elems = 0;
    size_t bytes = 0;
    size_t info_bytes = 0;
    const size_t info_len = (spec.Str("info_kind") == "array")
                                ? static_cast<size_t>(batch)
                                : 1;
    if (!MulSize(static_cast<size_t>(batch), static_cast<size_t>(n), &elems) ||
        !MulSize(elems, static_cast<size_t>(n), &elems) ||
        !MulSize(elems, itemsize, &bytes) ||
        !MulSize(info_len, sizeof(int32_t), &info_bytes)) {
        return Bail(r, result_path, kSpecError, "spec_error",
                    "缓冲字节数溢出 size_t：batch=" + std::to_string(batch) +
                        " n=" + std::to_string(n) + " itemsize=" +
                        std::to_string(itemsize));
    }

    // --- ACL 前置（调用序与 test/<op>/<op>_test.cpp 惯例逐项对位，PROBE §5.2）---
    int ret = aclInit(nullptr);
    if (ret != 0) {
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclInit ret=" + std::to_string(ret) +
                        "（500000 通常是容器非 --privileged 看不到 NPU）");
    }
    ret = aclrtSetDevice(device_id);
    if (ret != 0) {
        Teardown td;
        td.Note("aclFinalize", aclFinalize());
        r.Set("teardown_detail", td.detail());
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclrtSetDevice ret=" + std::to_string(ret));
    }
    aclrtStream stream = nullptr;
    ret = aclrtCreateStream(&stream);
    if (ret != 0) {
        Teardown td;
        td.Note("aclrtResetDevice", aclrtResetDevice(device_id));
        td.Note("aclFinalize", aclFinalize());
        r.Set("teardown_detail", td.detail());
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclrtCreateStream ret=" + std::to_string(ret));
    }
    aclsolverHandle_t handle = nullptr;
    // aclsolverCreate 的返回类型随基线变过（旧 aclError / 新 aclsolverStatus_t），
    // 两者的成功值都是 0，所以收进 int 比 0 ——不钉死类型名。
    int screate = aclsolverCreate(&handle);
    if (screate == 0) screate = aclsolverSetStream(handle, stream);
    if (screate != 0) {
        // SetStream 失败时 handle 已经建出来了，必须销毁——只销毁 stream 会漏掉它
        Teardown td;
        if (handle != nullptr) td.Note("aclsolverDestroy", aclsolverDestroy(handle));
        td.Note("aclrtDestroyStream", aclrtDestroyStream(stream));
        td.Note("aclrtResetDevice", aclrtResetDevice(device_id));
        td.Note("aclFinalize", aclFinalize());
        if (!td.detail().empty()) r.Set("teardown_detail", td.detail());
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclsolverCreate/SetStream ret=" + std::to_string(screate));
    }

    int code = kOk;
    std::string status = "ok";
    std::string detail;

    // --- 缓冲：pristine 保原始输入，work 每轮恢复，first 存第 1 轮输出供逐轮比对 ---
    std::vector<char> pristine(bytes), first_out(bytes);
    std::vector<int32_t> info(info_len, 0), first_info(info_len, 0);
    void *host_a = nullptr;
    void *host_ainv = nullptr;
    const bool outofplace = (shape == "outofplace_a_ainv");
    int runs_completed = 0;        // 真正跑完（算子返回 0，且第 2 轮起比对做完）的轮数
    bool mismatch = false;         // 出现过 bit-wise 失配
    bool first_written = false;    // 首轮三键是否已落盘

    do {
        if (!ReadAll(spec.Str("in_a"), pristine.data(), bytes)) {
            code = kIoError;
            status = "io_error";
            detail = "输入读不全或多出字节: " + spec.Str("in_a") +
                     "（期望 " + std::to_string(bytes) + " 字节）";
            break;
        }
        // 工程惯例：测试程序一次 Device 内存都不分配，只用 aclrtMallocHost（PROBE §5.2）
        if (aclrtMallocHost(&host_a, bytes) != 0 ||
            (outofplace && aclrtMallocHost(&host_ainv, bytes) != 0)) {
            code = kAclError;
            status = "acl_error";
            detail = "aclrtMallocHost 失败";
            break;
        }

        if (op == "_device_selftest") {
            // Device 口径的搬运管道自检：H2D → 同步 → D2H 往返后与原始输入逐字节比对。
            std::memcpy(host_a, pristine.data(), bytes);
            std::vector<char> back(bytes, 0);
            const int dret = DeviceRoundTrip(pristine.data(), back.data(), bytes,
                                             stream, &detail);
            const bool same = (dret == 0) &&
                              std::memcmp(back.data(), pristine.data(), bytes) == 0;
            r.SetNum("device_roundtrip_ret", dret);
            r.Set("device_roundtrip_bitwise", same ? "1" : "0");
            std::memcpy(first_out.data(), back.data(), bytes);
            if (!same) {
                code = kOpError;
                status = "op_error";
                if (detail.empty()) detail = "Device 往返后字节不一致";
            }
            break;
        }

        for (int run = 1; run <= runs; ++run) {
            std::memcpy(host_a, pristine.data(), bytes);   // 每轮从原始副本恢复输入
            if (outofplace) std::memset(host_ainv, 0, bytes);
            std::fill(info.begin(), info.end(), 0);

            Call call{handle, op, shape, dtype, n, lda, batch, host_a,
                      outofplace ? host_ainv : nullptr, info.data()};
            bool unsupported = false;
            const auto t0 = std::chrono::steady_clock::now();
            const int op_ret = Invoke(call, &unsupported);
            const double ms = std::chrono::duration<double, std::milli>(
                                  std::chrono::steady_clock::now() - t0)
                                  .count();
            if (unsupported) {
                code = kUnsupported;
                status = "unsupported";
                detail = "算子 " + op + " 的形状 " + shape +
                         " 未编进本执行件（检查 -DHARNESS_OP_* 与交付头声明）";
                break;
            }

            const void *out = outofplace ? host_ainv : host_a;
            const std::string tag = "run" + std::to_string(run);
            r.SetMs(tag + "_ms", ms);
            r.SetNum(tag + "_ret", op_ret);
            r.SetHex(tag + "_out32_fnv", Fnv1a(out, bytes));
            r.SetHex(tag + "_info_fnv", Fnv1a(info.data(), info_bytes));

            if (run == 1) {
                // 首轮现场立刻落盘：后面哪一轮崩了、超时了，这份输出都已经在盘上
                std::memcpy(first_out.data(), out, bytes);
                first_info.assign(info.begin(), info.end());
                if (!WriteAll(spec.Str("out_a"), first_out.data(), bytes) ||
                    !WriteAll(spec.Str("out_info"), first_info.data(), info_bytes)) {
                    code = kIoError;
                    status = "io_error";
                    detail = "第 1 轮输出写盘失败";
                    break;
                }
                first_written = true;
            } else if (op_ret != 0) {
                // 失败轮单独保存，不覆盖首轮现场
                WriteAll(spec.Str("out_a") + ".fail.bin", out, bytes);
                WriteAll(spec.Str("out_info") + ".fail.bin", info.data(), info_bytes);
                r.SetNum("fail_run", run);
            }

            if (op_ret != 0) {
                code = kOpError;
                status = "op_error";
                detail = "算子返回 " + std::to_string(op_ret) + "（第 " +
                         std::to_string(run) + " 轮）";
                break;                     // 该轮不算跑完：不计入 runs_completed
            }
            ++runs_completed;
            r.SetNum("rerun_runs_completed", runs_completed);
            if (!r.Flush(result_path)) {
                code = kIoError; status = "io_error"; detail = "result checkpoint failed"; break;
            }
            if (run == 1) continue;

            // 逐键 bit-wise 比对（memcmp 字节域：NaN 按位等同、-0.0 与 0.0 不等同）
            const char *diff_key = nullptr;
            if (std::memcmp(first_out.data(), out, bytes) != 0) diff_key = "out32";
            else if (std::memcmp(first_info.data(), info.data(), info_bytes) != 0)
                diff_key = "info";
            if (diff_key != nullptr) {
                // 失配轮的输出另存，两轮都留在证据里才可独立重判
                WriteAll(spec.Str("out_a") + ".diff.bin", out, bytes);
                WriteAll(spec.Str("out_info") + ".diff.bin", info.data(), info_bytes);
                mismatch = true;
                code = kRerunMismatch;
                status = "rerun_mismatch";
                detail = std::string("第 ") + std::to_string(run) + " 轮 " + diff_key +
                         " 与第 1 轮字节不一致";
                r.SetNum("rerun_first_diff_run", run);
                r.Set("rerun_first_diff_key", diff_key);
                break;
            }
        }
        // 确定性结论三态：失配记 0，规定轮数全跑完且无失配记 1，其余记 unknown。
        // 没做完的比较不许报一致——编排层按 unknown 当「无结论」处理。
        r.SetNum("rerun_runs_requested", runs);
        r.SetNum("rerun_runs_completed", runs_completed);
        r.Set("rerun_consistent",
              mismatch ? "0" : (runs_completed >= runs ? "1" : "unknown"));
    } while (false);

    // --- 三键落盘：首轮已在循环里写过；没写过的路径（自检、循环没进去）在这里补 ---
    if (!first_written && code != kSpecError && code != kIoError) {
        if (!WriteAll(spec.Str("out_a"), first_out.data(), bytes) ||
            !WriteAll(spec.Str("out_info"), first_info.data(), info_bytes)) {
            if (code == kOk) {
                code = kIoError;
                status = "io_error";
                detail = "输出写盘失败";
            }
        }
    }

    // --- 实际加载库：对算子函数地址取 dladdr，拿到真正被调用的那个 .so 的路径 ---
    {
        Dl_info di;
        const void *probe = nullptr;
#ifdef HARNESS_OP_CMATINV_BATCHED
        probe = reinterpret_cast<const void *>(&aclsolverCmatinvBatched);
#elif defined(HARNESS_OP_SGETRI)
        probe = reinterpret_cast<const void *>(&aclsolverSgetri);
#else
        probe = reinterpret_cast<const void *>(&aclsolverCreate);
#endif
        if (probe != nullptr && dladdr(const_cast<void *>(probe), &di) != 0 &&
            di.dli_fname != nullptr) {
            r.Set("lib_path", di.dli_fname);
        }
    }

    r.SetNum("out32_bytes", static_cast<long long>(bytes));
    r.Set("out32_dtype", dtype);
    r.SetNum("info_len", static_cast<long long>(info_len));
    r.Set("info_dtype", "int32");

    // --- 统一收尾：逆序释放，逐项查返回值。handle 先于它所用的 stream 销毁 ---
    Teardown td;
    if (host_a != nullptr) td.Note("aclrtFreeHost(host_a)", aclrtFreeHost(host_a));
    if (host_ainv != nullptr)
        td.Note("aclrtFreeHost(host_ainv)", aclrtFreeHost(host_ainv));
    if (handle != nullptr) td.Note("aclsolverDestroy", aclsolverDestroy(handle));
    if (stream != nullptr) td.Note("aclrtDestroyStream", aclrtDestroyStream(stream));
    td.Note("aclrtResetDevice", aclrtResetDevice(device_id));
    td.Note("aclFinalize", aclFinalize());
    r.SetNum("teardown_ret", td.first_ret());
    r.Set("teardown_detail", td.detail());
    if (td.first_ret() != 0 && code == kOk) {
        // 主流程成功但清理失败：设备侧状态不明，不能当成功交付
        code = kAclError;
        status = "acl_error";
        detail = "收尾清理失败: " + td.detail();
    }

    r.Set("status", status);
    r.Set("detail", detail);
    r.SetNum("exit", code);
    if (!r.Flush(result_path)) {
        std::fprintf(stderr, "[harness_exec] result 写盘失败: %s\n", result_path.c_str());
        return kIoError;
    }
    if (code != kOk) {
        std::fprintf(stderr, "[harness_exec] %s: %s\n", status.c_str(), detail.c_str());
    }
    return code;
}
