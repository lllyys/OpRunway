/* 独立执行器的固定运行时。**按算子变的只有 op_call.inc**，由 gen_runner.py 生成。
 *
 * 它读一份行式描述符（不是 JSON——为一个描述符引 JSON 库不值），按描述建 aclTensor
 * 与各类 acl 对象，走 aclnn 两段式，把输出 D2H 回文件。不认识 ATK，也不链 torch。
 *
 * 用法： runner <描述符目录> <device> <case id...>
 * 每条用例打一行 `CASE <id> <OK|FAIL> <阶段> <ret>`，调用方按这一行判定。
 * 进程被 aicore 异常打废之后后面全会失败，所以调用方负责崩了重启——这正是
 * 本执行器相对 ATK 的唯一实质差别。
 */
#include <acl/acl.h>
#include <dlfcn.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "op_call.inc"

namespace {

struct TensorSpec {
    std::string dtype;
    std::vector<int64_t> view;
    std::vector<int64_t> strides;
    std::vector<int64_t> storage;
    std::string file;       // 输入才有
    std::string outFile;    // 输出才有
    int64_t nbytes = 0;
};

struct Arg {
    std::string kind;
    aclTensor* tensor = nullptr;
    aclScalar* scalar = nullptr;
    aclIntArray* intArray = nullptr;
    aclBoolArray* boolArray = nullptr;
    aclFloatArray* floatArray = nullptr;
    aclTensorList* tensorList = nullptr;
    int64_t i64 = 0;
    double f64 = 0;
    bool b = false;
    std::string str;
};

struct CaseData {
    std::vector<Arg> args;
    std::vector<void*> devPtrs;
    // aclCreateTensor 收的是 shape / stride 数组的**指针**。规格必须活到用例跑完，
    // 用 deque 是因为它扩容不搬已有元素，vector 扩容后旧引用就悬空了。
    std::deque<TensorSpec> specs;
    // **aclCreateIntArray / FloatArray / BoolArray / Scalar 同样只存指针，不拷贝。**
    // 早先这四处用的是块内局部变量，出了 else-if 就析构：算子读到的是已释放内存。
    // 症状不是崩，是 output_size 变成垃圾值、kernel 什么都不写、输出全零，
    // 外加退出时 `corrupted size vs. prev_size in fastbins`。**同一个 UB 有两种表现**：
    // 有的算子全批栽在这里，有的侥幸跑对——只是释放的小块还没被覆盖。
    std::deque<std::vector<int64_t>> intBufs;
    std::deque<std::vector<float>> floatBufs;
    std::deque<std::vector<uint8_t>> boolBufs;
    std::deque<std::vector<uint8_t>> scalarBufs;
    std::vector<TensorSpec*> outSpecs;
    std::vector<void*> outPtrs;
};

std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> out;
    std::string item;
    std::istringstream stream(s);
    while (std::getline(stream, item, sep)) {
        if (!item.empty()) out.push_back(item);
    }
    return out;
}

std::vector<int64_t> parseInts(const std::string& s) {
    std::vector<int64_t> out;
    if (s == "-") return out;
    for (const auto& piece : split(s, ',')) out.push_back(std::stoll(piece));
    return out;
}

std::vector<double> parseDoubles(const std::string& s) {
    std::vector<double> out;
    if (s == "-") return out;
    for (const auto& piece : split(s, ',')) out.push_back(std::stod(piece));
    return out;
}

aclDataType toAclType(const std::string& name) {
    if (name == "float32") return ACL_FLOAT;
    if (name == "float16") return ACL_FLOAT16;
    if (name == "bfloat16") return ACL_BF16;
    if (name == "float64") return ACL_DOUBLE;
    if (name == "int64") return ACL_INT64;
    if (name == "int32") return ACL_INT32;
    if (name == "int16") return ACL_INT16;
    if (name == "int8") return ACL_INT8;
    if (name == "uint8") return ACL_UINT8;
    if (name == "uint16") return ACL_UINT16;
    if (name == "uint32") return ACL_UINT32;
    if (name == "uint64") return ACL_UINT64;
    if (name == "bool") return ACL_BOOL;
    if (name == "complex64") return ACL_COMPLEX64;
    if (name == "complex128") return ACL_COMPLEX128;
    // 认不出的 dtype 必须停下：猜一个最接近的会让结论静默变成另一件事。
    fprintf(stderr, "unsupported dtype in descriptor: %s\n", name.c_str());
    exit(4);
}

// 描述符里的 nbytes 是 Python 侧按 stride 能触到的最远字节算好的，
// 不要在这里按 numel*itemsize 重算——非连续视图两者不等。
bool loadToDevice(const std::string& path, int64_t nbytes, void** devPtr) {
    *devPtr = nullptr;
    if (nbytes == 0) return true;
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        fprintf(stderr, "cannot open %s\n", path.c_str());
        return false;
    }
    std::vector<char> host(static_cast<size_t>(nbytes));
    in.read(host.data(), nbytes);
    if (in.gcount() != nbytes) {
        fprintf(stderr, "%s: short read %lld/%lld\n", path.c_str(),
                static_cast<long long>(in.gcount()), static_cast<long long>(nbytes));
        return false;
    }
    if (aclrtMalloc(devPtr, static_cast<size_t>(nbytes), ACL_MEM_MALLOC_HUGE_FIRST) != ACL_SUCCESS) {
        return false;
    }
    return aclrtMemcpy(*devPtr, nbytes, host.data(), nbytes, ACL_MEMCPY_HOST_TO_DEVICE) == ACL_SUCCESS;
}

bool dumpFromDevice(const std::string& path, int64_t nbytes, void* devPtr) {
    std::vector<char> host(static_cast<size_t>(nbytes));
    if (nbytes > 0 &&
        aclrtMemcpy(host.data(), nbytes, devPtr, nbytes, ACL_MEMCPY_DEVICE_TO_HOST) != ACL_SUCCESS) {
        return false;
    }
    std::ofstream out(path, std::ios::binary);
    if (!out) return false;
    out.write(host.data(), nbytes);
    return out.good();
}

// ATK 建 tensor 时 offset 恒传 0、storage shape 默认取 view shape，地址是已经
// 偏移过的 data_ptr（atk/tasks/backends/lib_interface/acl_wrapper.py 的
// create_acl_tensor）。这里照抄，改任何一处都会让两侧测的不是同一个布局。
aclTensor* makeTensor(const TensorSpec& spec, void* devPtr) {
    return aclCreateTensor(spec.view.data(), spec.view.size(), toAclType(spec.dtype),
                           spec.strides.data(), 0, ACL_FORMAT_ND,
                           spec.storage.data(), spec.storage.size(), devPtr);
}

TensorSpec readTensorSpec(const std::vector<std::string>& f, size_t base) {
    TensorSpec spec;
    spec.dtype = f[base];
    spec.view = parseInts(f[base + 1]);
    spec.strides = parseInts(f[base + 2]);
    spec.storage = parseInts(f[base + 3]);
    spec.nbytes = std::stoll(f[base + 4]);
    return spec;
}

const Arg& need(const std::vector<Arg>& args, size_t index, const char* kind) {
    if (index >= args.size()) {
        fprintf(stderr, "descriptor has %zu args, op_call.inc wants index %zu — "
                        "签名与描述符对不上，重跑 gen_runner.py\n", args.size(), index);
        exit(4);
    }
    if (args[index].kind != kind) {
        fprintf(stderr, "arg %zu is '%s' but op_call.inc wants '%s' — "
                        "签名与用例参数顺序对不上\n",
                index, args[index].kind.c_str(), kind);
        exit(4);
    }
    return args[index];
}

void release(CaseData& data) {
    for (auto& arg : data.args) {
        if (arg.tensor) aclDestroyTensor(arg.tensor);
        if (arg.scalar) aclDestroyScalar(arg.scalar);
        if (arg.intArray) aclDestroyIntArray(arg.intArray);
        if (arg.boolArray) aclDestroyBoolArray(arg.boolArray);
        if (arg.floatArray) aclDestroyFloatArray(arg.floatArray);
        if (arg.tensorList) aclDestroyTensorList(arg.tensorList);
    }
    for (auto* ptr : data.devPtrs) {
        if (ptr) aclrtFree(ptr);
    }
    for (auto* ptr : data.outPtrs) {
        if (ptr) aclrtFree(ptr);
    }
}

bool parseCase(const std::string& path, const std::string& dir, CaseData& data) {
    std::ifstream in(path);
    if (!in) {
        fprintf(stderr, "cannot open %s\n", path.c_str());
        return false;
    }
    std::string line;
    while (std::getline(in, line)) {
        auto f = split(line, ' ');
        if (f.empty() || f[0] == "CASE" || f[0] == "END") continue;

        bool isOut = (f[0] == "OUT");
        const std::string& kind = f[1];

        if (kind == "tensor") {
            data.specs.push_back(readTensorSpec(f, 2));
            TensorSpec& spec = data.specs.back();
            if (isOut) {
                spec.outFile = dir + "/" + f[7];
                void* ptr = nullptr;
                if (spec.nbytes > 0 &&
                    aclrtMalloc(&ptr, static_cast<size_t>(spec.nbytes),
                                ACL_MEM_MALLOC_HUGE_FIRST) != ACL_SUCCESS) {
                    return false;
                }
                // out 不清零就是拿未初始化显存当基准，算子只写了一部分时
                // 比对结果依赖上一条用例的残留，不可复现。
                if (ptr) aclrtMemset(ptr, spec.nbytes, 0, spec.nbytes);
                data.outPtrs.push_back(ptr);
                data.outSpecs.push_back(&spec);
                Arg arg;
                arg.kind = "tensor";
                arg.tensor = makeTensor(spec, ptr);
                data.args.push_back(arg);
                continue;
            }
            spec.file = dir + "/" + f[7];
            void* ptr = nullptr;
            if (!loadToDevice(spec.file, spec.nbytes, &ptr)) return false;
            data.devPtrs.push_back(ptr);
            Arg arg;
            arg.kind = "tensor";
            arg.tensor = makeTensor(spec, ptr);
            data.args.push_back(arg);
        } else if (kind == "scalar") {
            Arg arg;
            arg.kind = "scalar";
            aclDataType dt = toAclType(f[2]);
            /* **缓冲区宽度必须与 dtype 一致。** aclCreateScalar 按 dtype 决定
             * 从这个指针读几个字节；早先除 int64/int32/bool 外一律写 8 字节
             * double，再声明成 int16/uint8/fp16，读到的就是 double 位模式的低位
             * ——值全错且不报错。int8/int16/uint8 这些正是社区任务最常新增的
             * dtype，撞上的概率不低。宽度对齐的写法照 ATK 的
             * `create_acl_scalar`（acl_wrapper.py）。 */
            const std::string& tn = f[2];
            auto put = [&data](const void* src, size_t n) {
                data.scalarBufs.emplace_back(n);
                std::memcpy(data.scalarBufs.back().data(), src, n);
                return data.scalarBufs.back().data();
            };
            void* buf = nullptr;
            if (tn == "fp16" || tn == "float16") {
                /* fp16 没有内建类型，按 IEEE754 半精度拼出位模式再当 uint16 存。 */
                float fv = static_cast<float>(std::stod(f[3]));
                uint32_t bits;
                std::memcpy(&bits, &fv, sizeof(bits));
                uint16_t sign = static_cast<uint16_t>((bits >> 16) & 0x8000u);
                int32_t exp = static_cast<int32_t>((bits >> 23) & 0xFF) - 127 + 15;
                uint32_t man = bits & 0x7FFFFFu;
                uint16_t h;
                if (exp <= 0)        h = sign;                       /* 下溢按 0 */
                else if (exp >= 31)  h = static_cast<uint16_t>(sign | 0x7C00u);
                else h = static_cast<uint16_t>(sign | (exp << 10) | (man >> 13));
                buf = put(&h, sizeof(h));
            } else if (tn == "bf16" || tn == "bfloat16") {
                float fv = static_cast<float>(std::stod(f[3]));
                uint32_t bits;
                std::memcpy(&bits, &fv, sizeof(bits));
                uint16_t h = static_cast<uint16_t>(bits >> 16);       /* 截高 16 位 */
                buf = put(&h, sizeof(h));
            } else if (tn == "fp32" || tn == "float32" || tn == "float") {
                float fv = static_cast<float>(std::stod(f[3]));
                buf = put(&fv, sizeof(fv));
            } else if (tn == "fp64" || tn == "float64" || tn == "double") {
                double v = std::stod(f[3]);
                buf = put(&v, sizeof(v));
            } else if (tn == "bool") {
                uint8_t v = (std::stoll(f[3]) != 0) ? 1 : 0;
                buf = put(&v, sizeof(v));
            } else if (tn == "int8" || tn == "uint8") {
                int64_t v = std::stoll(f[3]);
                uint8_t b = static_cast<uint8_t>(v);
                buf = put(&b, sizeof(b));
            } else if (tn == "int16" || tn == "uint16") {
                int64_t v = std::stoll(f[3]);
                uint16_t b = static_cast<uint16_t>(v);
                buf = put(&b, sizeof(b));
            } else if (tn == "int32" || tn == "uint32") {
                int64_t v = std::stoll(f[3]);
                uint32_t b = static_cast<uint32_t>(v);
                buf = put(&b, sizeof(b));
            } else if (tn == "int64" || tn == "uint64") {
                int64_t v = std::stoll(f[3]);
                buf = put(&v, sizeof(v));
            } else {
                fprintf(stderr, "%s: 标量 dtype %s 没实现，"
                                "补进 runner.cpp 的 scalar 分支\n",
                        path.c_str(), tn.c_str());
                return false;
            }
            arg.scalar = aclCreateScalar(buf, dt);
            data.args.push_back(arg);
        } else if (kind == "intarray") {
            Arg arg;
            arg.kind = "intarray";
            data.intBufs.push_back(parseInts(f[2]));
            const std::vector<int64_t>& values = data.intBufs.back();
            arg.intArray = aclCreateIntArray(values.data(), values.size());
            data.args.push_back(arg);
        } else if (kind == "floatarray") {
            Arg arg;
            arg.kind = "floatarray";
            auto raw = parseDoubles(f[2]);
            data.floatBufs.emplace_back(raw.begin(), raw.end());
            const std::vector<float>& asFloat = data.floatBufs.back();
            arg.floatArray = aclCreateFloatArray(asFloat.data(), asFloat.size());
            data.args.push_back(arg);
        } else if (kind == "boolarray") {
            Arg arg;
            arg.kind = "boolarray";
            auto values = parseInts(f[2]);
            // std::vector<bool> 是位压缩的，取不到连续 bool 数组，必须转一手
            data.boolBufs.emplace_back(values.size());
            std::vector<uint8_t>& raw = data.boolBufs.back();
            for (size_t i = 0; i < values.size(); ++i) raw[i] = values[i] ? 1 : 0;
            arg.boolArray = aclCreateBoolArray(reinterpret_cast<const bool*>(raw.data()), raw.size());
            data.args.push_back(arg);
        } else if (kind == "int") {
            Arg arg;
            arg.kind = "int";
            arg.i64 = std::stoll(f[2]);
            data.args.push_back(arg);
        } else if (kind == "double") {
            Arg arg;
            arg.kind = "double";
            arg.f64 = std::stod(f[2]);
            data.args.push_back(arg);
        } else if (kind == "bool") {
            Arg arg;
            arg.kind = "bool";
            arg.b = (f[2] == "1");
            data.args.push_back(arg);
        } else if (kind == "str") {
            Arg arg;
            arg.kind = "str";
            arg.str = f[2];
            data.args.push_back(arg);
        } else if (kind == "null") {
            Arg arg;
            arg.kind = "null";
            data.args.push_back(arg);
        } else {
            fprintf(stderr, "unknown arg kind '%s'\n", kind.c_str());
            return false;
        }
    }
    return true;
}

}  // namespace

#define A_TENSOR(i) need(data.args, i, "tensor").tensor
#define A_SCALAR(i) need(data.args, i, "scalar").scalar
#define A_INTARRAY(i) need(data.args, i, "intarray").intArray
#define A_BOOLARRAY(i) need(data.args, i, "boolarray").boolArray
#define A_FLOATARRAY(i) need(data.args, i, "floatarray").floatArray
#define A_TENSORLIST(i) need(data.args, i, "tensorlist").tensorList
#define A_INT(i) need(data.args, i, "int").i64
#define A_DOUBLE(i) need(data.args, i, "double").f64
#define A_BOOL(i) need(data.args, i, "bool").b
#define A_STR(i) const_cast<char*>(need(data.args, i, "str").str.c_str())

int main(int argc, char** argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: runner <descriptor dir> <device> <case id...>\n");
        return 2;
    }
    const std::string dir = argv[1];
    const int32_t device = atoi(argv[2]);

    if (aclInit(nullptr) != ACL_SUCCESS) {
        fprintf(stderr, "aclInit failed\n");
        return 2;
    }
    if (aclrtSetDevice(device) != ACL_SUCCESS) {
        fprintf(stderr, "aclrtSetDevice(%d) failed\n", device);
        return 2;
    }
    aclrtStream stream = nullptr;
    if (aclrtCreateStream(&stream) != ACL_SUCCESS) {
        fprintf(stderr, "aclrtCreateStream failed\n");
        return 2;
    }

    /* 先把算子包自带的 tiling 库装进来，再跑任何算子。
     *
     * **kernel 与 tiling 必须来自同一个包。** 不装它，kernel 用的是待验收包里那份、
     * tiling 用的是 CANN 内置那份，两者不配套：aclnn 不报错、sync 也成功，输出却是
     * 错的（实测见过全批只写几十个元素，其余全零）。装上之后同一条用例
     * 与 golden 逐位相同。ATK 那边是靠 torch_npu 的初始化顺带 dlopen 了它，
     * 所以两边结论对不上——这一层 `BOUND` 断言看不到，它只管接口符号的落点。
     *
     * 路径由调用方从算子包里找出来，通过 RUNNER_OPTILING_SO 传进来；包里没有这个
     * 文件时调用方不设它，这里就不做。装不上直接退 3，不带着半套实现往下跑。 */
    {
        const char* optiling = getenv("RUNNER_OPTILING_SO");
        if (optiling && *optiling) {
            void* handle = dlopen(optiling, RTLD_NOW | RTLD_GLOBAL);
            if (handle == nullptr) {
                fprintf(stderr, "dlopen %s failed: %s\n", optiling, dlerror());
                fprintf(stderr, "算子包的 tiling 库装不上，再往下跑就是 kernel 与 "
                                "tiling 不配套，结果错且不报错。\n");
                return 3;
            }
            printf("OPTILING %s\n", optiling);
            fflush(stdout);
        }
    }

    /* 打出 GetWorkspaceSize 这个符号**实际**解析到了哪个 .so。
     *
     * 待验收实现（libcust_opapi.so）与 CANN 内置实现（libopapi.so）导出同名符号，
     * 链接顺序不对就会静默连到内置那份——测的是 CANN 自带算子，不是待验收的。
     * 实测踩过：内置实现不收待验收算子新增的那个 dtype，于是全批报
     * `EZ1001 ... should be in dtype support list`，看上去像待验收实现不支持这个
     * dtype，实际上根本没调到它。调用方按这一行断言落点。 */
    {
        // 必须 dlsym 拿真地址再 dladdr。**直接对函数名取址拿到的是本可执行文件里的
        // PLT 桩**，dladdr 会报成 runner 自己，看不出真实落点——实测踩过。
        Dl_info info;
        void* real = dlsym(RTLD_DEFAULT, OP_WS_SYMBOL_NAME);
        if (real && dladdr(real, &info) && info.dli_fname) {
            printf("BOUND %s\n", info.dli_fname);
        } else {
            printf("BOUND unknown\n");
        }
        fflush(stdout);
    }

    for (int i = 3; i < argc; ++i) {
        const std::string id = argv[i];
        CaseData data;
        const std::string spec = dir + "/case" + id + ".txt";
        if (!parseCase(spec, dir, data)) {
            printf("CASE %s FAIL parse 0\n", id.c_str());
            fflush(stdout);
            release(data);
            continue;
        }

        // 排障用：把刚搬上 device 的输入原样拷回来，确认 H2D/D2H 通路没问题。
        if (getenv("RUNNER_ECHO")) {
            for (size_t k = 0, n = 0; k < data.args.size(); ++k) {
                if (data.args[k].kind != "tensor" || n >= data.devPtrs.size()) continue;
                dumpFromDevice(dir + "/echo_" + id + "_" + std::to_string(k) + ".bin",
                               data.specs[n].nbytes, data.devPtrs[n]);
                ++n;
            }
        }

        uint64_t workspaceSize = 0;
        aclOpExecutor* executor = nullptr;
        aclnnStatus ret = OP_GET_WORKSPACE_SIZE(&workspaceSize, &executor);
        if (ret != ACL_SUCCESS) {
            // **判定行先落地，再去问原因。顺序不能反**：算子把 host 堆写坏时
            // aclGetRecentErrMsg 自己会 abort，这条用例的判定行就跟着丢了，
            // 调用方只看到「整批一条都没判出来」，去查二进制起没起来——查错方向。
            printf("CASE %s FAIL workspace %d\n", id.c_str(), static_cast<int>(ret));
            fflush(stdout);
            // 接口层拒绝时错误码只说「参数错」，原因在 aclGetRecentErrMsg 里，
            // 不打出来就只能靠猜哪个参数不对。
            const char* why = aclGetRecentErrMsg();
            fprintf(stderr, "[case %s] %s\n", id.c_str(), why ? why : "(no message)");
            release(data);
            continue;
        }

        void* workspace = nullptr;
        if (workspaceSize > 0 &&
            aclrtMalloc(&workspace, workspaceSize, ACL_MEM_MALLOC_HUGE_FIRST) != ACL_SUCCESS) {
            printf("CASE %s FAIL wsmalloc 0\n", id.c_str());
            fflush(stdout);
            release(data);
            continue;
        }

        ret = OP_EXECUTE(workspace, workspaceSize, executor, stream);
        if (ret != ACL_SUCCESS) {
            // 与 workspace 那一档一样必须打原文。561000 只说「发射失败」，
            // 是这个 dtype 没有 kernel、还是 tiling 拒了、还是调用方构造错了，
            // 全在 aclGetRecentErrMsg 里——不打出来就只能靠猜。
            // **判定行先落地，再去问原因。顺序不能反**：算子把 host 堆写坏时
            // aclGetRecentErrMsg 自己会 abort，这条用例的判定行就跟着丢了，
            // 调用方只看到「整批一条都没判出来」，去查二进制起没起来——查错方向。
            printf("CASE %s FAIL launch %d\n", id.c_str(), static_cast<int>(ret));
            fflush(stdout);
            const char* why = aclGetRecentErrMsg();
            fprintf(stderr, "[case %s] %s\n", id.c_str(), why ? why : "(no message)");
            if (workspace) aclrtFree(workspace);
            release(data);
            continue;
        }

        // 这一步失败几乎总是 aicore 异常（507015）。它把本进程的 device context
        // 打废，之后每条都会挂——调用方看到这一行就该重启进程。
        //
        // **同步整个 device，不只同步这条流。** aclnn 算子内部可能在别的流上发射
        // 子任务，只等自己这条流会在算子还没写完时就把输出拷回来，读到的是清零后的
        // 初值——实测就是全零。ATK 那边等价的动作是 `torch.npu.synchronize()`。
        aclError sync = aclrtSynchronizeStream(stream);
        if (sync == ACL_SUCCESS) sync = aclrtSynchronizeDevice();
        if (sync != ACL_SUCCESS) {
            printf("CASE %s FAIL sync %d\n", id.c_str(), static_cast<int>(sync));
            fflush(stdout);
            if (workspace) aclrtFree(workspace);
            release(data);
            return 3;
        }

        bool dumped = true;
        for (size_t k = 0; k < data.outSpecs.size(); ++k) {
            if (!dumpFromDevice(data.outSpecs[k]->outFile, data.outSpecs[k]->nbytes,
                                data.outPtrs[k])) {
                dumped = false;
                break;
            }
        }
        printf("CASE %s %s dump 0\n", id.c_str(), dumped ? "OK" : "FAIL");
        if (getenv("RUNNER_VERBOSE")) {
            const char* msg = aclGetRecentErrMsg();
            fprintf(stderr, "[case %s] workspace=%llu outs=%zu %s\n", id.c_str(),
                    static_cast<unsigned long long>(workspaceSize),
                    data.outSpecs.size(), (msg && *msg) ? msg : "");
        }
        fflush(stdout);

        if (workspace) aclrtFree(workspace);
        release(data);
    }

    aclrtDestroyStream(stream);
    aclrtResetDevice(device);
    aclFinalize();
    return 0;
}
