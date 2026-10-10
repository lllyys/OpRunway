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
 * 复跑（Q4 裁定）：同一子进程、同一 stream 内重复 runs 次，每轮先从原始副本恢复
 * 输入再调算子，第 2..N 轮与第 1 轮逐键 bit-wise 比对（memcmp 字节域，NaN 按位
 * 等同）。第 1 轮的输出一定落盘；出现失配时该轮输出另存 *.diff.bin 留证。
 *
 * 三键落盘（{out32, info, status}）：out32 与 info 是裸二进制无头文件，形状与 dtype
 * 写在 result 文本里由编排层组装 npz——npz 依赖不进 C++ 侧（PROBE §6.3）。
 *
 * 退出码（编排层按此归类，见 exec_case.py 的 EXIT_KIND）：
 *   0  正常           10 spec 错误      11 I/O 错误        12 口径/算子未实现
 *   13 ACL 前置失败   14 算子返回非成功  15 复跑 bit-wise 失配
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

constexpr int kOk = 0;
constexpr int kSpecError = 10;
constexpr int kIoError = 11;
constexpr int kUnsupported = 12;
constexpr int kAclError = 13;
constexpr int kOpError = 14;
constexpr int kRerunMismatch = 15;

// ---------------------------------------------------------------------------
// spec 解析
// ---------------------------------------------------------------------------

const char *kRequired[] = {"op", "abi", "call_shape", "dtype", "device_id",
                           "n", "batch", "runs", "in_a", "out_a", "out_info",
                           "result"};

class Spec {
  public:
    bool Load(const std::string &path, std::string *err) {
        std::ifstream fh(path);
        if (!fh) {
            *err = "spec 打不开: " + path;
            return false;
        }
        std::string line;
        while (std::getline(fh, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            const size_t head = line.find_first_not_of(" \t");
            if (head == std::string::npos || line[head] == '#') continue;
            const size_t eq = line.find('=');
            if (eq == std::string::npos) {
                *err = "spec 行缺 '=': " + line;
                return false;
            }
            kv_[Trim(line.substr(0, eq))] = Trim(line.substr(eq + 1));
        }
        for (const char *k : kRequired) {
            if (kv_.find(k) == kv_.end()) {
                *err = std::string("spec 缺必需键: ") + k;
                return false;
            }
        }
        return true;
    }

    std::string Str(const std::string &k, const std::string &dflt = "") const {
        auto it = kv_.find(k);
        return it == kv_.end() ? dflt : it->second;
    }

    long long Num(const std::string &k, long long dflt = 0) const {
        auto it = kv_.find(k);
        if (it == kv_.end() || it->second.empty()) return dflt;
        return std::strtoll(it->second.c_str(), nullptr, 10);
    }

  private:
    static std::string Trim(const std::string &s) {
        const size_t a = s.find_first_not_of(" \t");
        if (a == std::string::npos) return "";
        const size_t b = s.find_last_not_of(" \t");
        return s.substr(a, b - a + 1);
    }
    std::map<std::string, std::string> kv_;
};

// ---------------------------------------------------------------------------
// 字节域工具：FNV-1a 64 摘要（无外部依赖，供 result 文本记逐轮指纹）
// ---------------------------------------------------------------------------

uint64_t Fnv1a(const void *data, size_t bytes) {
    const unsigned char *p = static_cast<const unsigned char *>(data);
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < bytes; ++i) {
        h ^= p[i];
        h *= 1099511628211ULL;
    }
    return h;
}

bool ReadAll(const std::string &path, void *dst, size_t bytes) {
    std::ifstream fh(path, std::ios::binary);
    if (!fh) return false;
    fh.read(static_cast<char *>(dst), static_cast<std::streamsize>(bytes));
    if (static_cast<size_t>(fh.gcount()) != bytes) return false;
    return fh.peek() == EOF;  // 多出字节即规格不符，不静默截断
}

bool WriteAll(const std::string &path, const void *src, size_t bytes) {
    std::ofstream fh(path, std::ios::binary | std::ios::trunc);
    if (!fh) return false;
    fh.write(static_cast<const char *>(src), static_cast<std::streamsize>(bytes));
    return static_cast<bool>(fh);
}

// ---------------------------------------------------------------------------
// result 文本
// ---------------------------------------------------------------------------

class Result {
  public:
    void Set(const std::string &k, const std::string &v) { lines_.emplace_back(k, v); }
    void SetNum(const std::string &k, long long v) { Set(k, std::to_string(v)); }
    void SetHex(const std::string &k, uint64_t v) {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "0x%016llx", static_cast<unsigned long long>(v));
        Set(k, buf);
    }
    void SetMs(const std::string &k, double v) {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "%.3f", v);
        Set(k, buf);
    }
    bool Flush(const std::string &path) const {
        std::ofstream fh(path, std::ios::trunc);
        if (!fh) return false;
        for (const auto &kv : lines_) fh << kv.first << "=" << kv.second << "\n";
        return static_cast<bool>(fh);
    }

  private:
    std::vector<std::pair<std::string, std::string>> lines_;
};

// 失败也要留下 result 文本，否则编排层只剩退出码、没有现场。
int Bail(Result &r, const std::string &result_path, int code,
         const std::string &status, const std::string &detail) {
    r.Set("status", status);
    r.Set("detail", detail);
    r.SetNum("exit", code);
    r.Flush(result_path);
    std::fprintf(stderr, "[harness_exec] %s: %s\n", status.c_str(), detail.c_str());
    return code;
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
    aclrtFree(dev);
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

    const size_t elems = static_cast<size_t>(batch) * static_cast<size_t>(n) *
                         static_cast<size_t>(n);
    const size_t bytes = elems * itemsize;
    const size_t info_len = (spec.Str("info_kind") == "array")
                                ? static_cast<size_t>(batch)
                                : 1;

    // --- ACL 前置（调用序与 test/<op>/<op>_test.cpp 惯例逐项对位，PROBE §5.2）---
    int ret = aclInit(nullptr);
    if (ret != 0) {
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclInit ret=" + std::to_string(ret) +
                        "（500000 通常是容器非 --privileged 看不到 NPU）");
    }
    ret = aclrtSetDevice(device_id);
    if (ret != 0) {
        aclFinalize();
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclrtSetDevice ret=" + std::to_string(ret));
    }
    aclrtStream stream = nullptr;
    ret = aclrtCreateStream(&stream);
    if (ret != 0) {
        aclrtResetDevice(device_id);
        aclFinalize();
        return Bail(r, result_path, kAclError, "acl_error",
                    "aclrtCreateStream ret=" + std::to_string(ret));
    }
    aclsolverHandle_t handle = nullptr;
    // aclsolverCreate 的返回类型随基线变过（旧 aclError / 新 aclsolverStatus_t），
    // 两者的成功值都是 0，所以收进 int 比 0 ——不钉死类型名。
    int screate = aclsolverCreate(&handle);
    if (screate == 0) screate = aclsolverSetStream(handle, stream);
    if (screate != 0) {
        aclrtDestroyStream(stream);
        aclrtResetDevice(device_id);
        aclFinalize();
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
            std::memcpy(host_a, pristine.data(), bytes);   // Q4：每轮恢复输入
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
            r.SetHex(tag + "_info_fnv", Fnv1a(info.data(), info_len * sizeof(int32_t)));

            if (op_ret != 0) {
                code = kOpError;
                status = "op_error";
                detail = "算子返回 " + std::to_string(op_ret) + "（第 " +
                         std::to_string(run) + " 轮）";
                std::memcpy(first_out.data(), out, bytes);
                first_info.assign(info.begin(), info.end());
                break;
            }

            if (run == 1) {
                std::memcpy(first_out.data(), out, bytes);
                first_info.assign(info.begin(), info.end());
                continue;
            }
            // 逐键 bit-wise 比对（memcmp 字节域：NaN 按位等同、-0.0 与 0.0 不等同）
            const char *diff_key = nullptr;
            if (std::memcmp(first_out.data(), out, bytes) != 0) diff_key = "out32";
            else if (std::memcmp(first_info.data(), info.data(),
                                 info_len * sizeof(int32_t)) != 0) diff_key = "info";
            if (diff_key != nullptr) {
                // 失配轮的输出另存，两轮都留在证据里才可独立重判
                WriteAll(spec.Str("out_a") + ".diff.bin", out, bytes);
                WriteAll(spec.Str("out_info") + ".diff.bin", info.data(),
                         info_len * sizeof(int32_t));
                code = kRerunMismatch;
                status = "rerun_mismatch";
                detail = std::string("第 ") + std::to_string(run) + " 轮 " + diff_key +
                         " 与第 1 轮字节不一致";
                r.SetNum("rerun_first_diff_run", run);
                r.Set("rerun_first_diff_key", diff_key);
                break;
            }
        }
        r.Set("rerun_consistent", (code == kRerunMismatch) ? "0" : "1");
    } while (false);

    // --- 三键落盘：第 1 轮的 out32 与 info 一定写出（失败轮也写，留证优先）---
    if (code != kSpecError && code != kIoError) {
        if (!WriteAll(spec.Str("out_a"), first_out.data(), bytes) ||
            !WriteAll(spec.Str("out_info"), first_info.data(),
                      info_len * sizeof(int32_t))) {
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

    if (host_a != nullptr) aclrtFreeHost(host_a);
    if (host_ainv != nullptr) aclrtFreeHost(host_ainv);
    aclrtDestroyStream(stream);
    aclsolverDestroy(handle);
    aclrtResetDevice(device_id);
    aclFinalize();

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
