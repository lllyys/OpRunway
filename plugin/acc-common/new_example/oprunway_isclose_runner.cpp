#include <cerrno>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <limits>
#include <algorithm>
#include <sstream>
#include <string>
#include <vector>

#include "acl/acl.h"
#ifndef __has_include
#define __has_include(x) 0
#endif
#if __has_include("aclnnop/aclnn_is_close.h")
#include "aclnnop/aclnn_is_close.h"
#else
#include "aclnnop/aclnn_isclose.h"
#endif

#define SUCCESS 0
#define FAILED 1

#define CHECK_RET(cond, return_expr) \
    do {                             \
        if (!(cond)) {               \
            return_expr;             \
        }                            \
    } while (0)

#define LOG_PRINT(message, ...)         \
    do {                                \
        printf(message, ##__VA_ARGS__); \
    } while (0)

namespace {

struct CaseSpec {
    std::string caseId;
    std::string dtype;
    double rtol = 0.0;
    double atol = 0.0;
    bool equalNan = false;
    std::vector<int64_t> shape;
};

struct TensorResource {
    aclTensor *tensor = nullptr;
    void *deviceAddr = nullptr;

    ~TensorResource()
    {
        if (tensor != nullptr) {
            aclDestroyTensor(tensor);
            tensor = nullptr;
        }
        if (deviceAddr != nullptr) {
            aclrtFree(deviceAddr);
            deviceAddr = nullptr;
        }
    }
};

struct WorkspaceResource {
    void *addr = nullptr;

    ~WorkspaceResource()
    {
        if (addr != nullptr) {
            aclrtFree(addr);
            addr = nullptr;
        }
    }
};

std::string JoinPath(const std::string &lhs, const std::string &rhs)
{
    if (lhs.empty() || lhs[lhs.size() - 1] == '/') {
        return lhs + rhs;
    }
    return lhs + "/" + rhs;
}

int64_t GetShapeSize(const std::vector<int64_t> &shape)
{
    int64_t shapeSize = 1;
    for (auto dim : shape) {
        shapeSize *= dim;
    }
    return shapeSize;
}

bool ComputeNumel(const std::vector<int64_t> &shape, size_t *numel, std::string *err)
{
    size_t value = 1;
    for (auto dim : shape) {
        if (dim < 0) {
            *err = "negative dimension";
            return false;
        }
        if (dim == 0) {
            value = 0;
            continue;
        }
        const size_t dimSize = static_cast<size_t>(dim);
        if (value != 0 && dimSize > std::numeric_limits<size_t>::max() / value) {
            *err = "shape numel overflow";
            return false;
        }
        value *= dimSize;
    }
    *numel = value;
    return true;
}

bool ComputeBytes(size_t numel, size_t elemSize, size_t *bytes, std::string *err)
{
    if (elemSize != 0 && numel > std::numeric_limits<size_t>::max() / elemSize) {
        *err = "byte size overflow";
        return false;
    }
    *bytes = numel * elemSize;
    return true;
}

std::vector<int64_t> CalcRowMajorStrides(const std::vector<int64_t> &shape)
{
    std::vector<int64_t> strides(shape.size(), 1);
    for (int64_t i = static_cast<int64_t>(shape.size()) - 2; i >= 0; --i) {
        strides[static_cast<size_t>(i)] = shape[static_cast<size_t>(i + 1)] * strides[static_cast<size_t>(i + 1)];
    }
    return strides;
}

bool ParseCaseLine(const std::string &line, int64_t lineNo, CaseSpec *spec, std::string *displayId, std::string *err)
{
    std::istringstream iss(line);
    int equalNan = 0;
    int64_t ndim = 0;
    if (!(iss >> spec->caseId)) {
        *displayId = "line_" + std::to_string(lineNo);
        *err = "missing case_id";
        return false;
    }
    *displayId = spec->caseId;
    if (!(iss >> spec->dtype >> spec->rtol >> spec->atol >> equalNan >> ndim)) {
        *err = "malformed manifest fields";
        return false;
    }
    if (spec->dtype != "float32" && spec->dtype != "float16") {
        *err = "unsupported dtype: " + spec->dtype;
        return false;
    }
    if (equalNan != 0 && equalNan != 1) {
        *err = "equal_nan must be 0 or 1";
        return false;
    }
    if (ndim < 0) {
        *err = "ndim must be non-negative";
        return false;
    }
    spec->equalNan = (equalNan == 1);
    spec->shape.clear();
    spec->shape.reserve(static_cast<size_t>(ndim));
    for (int64_t i = 0; i < ndim; ++i) {
        int64_t dim = 0;
        if (!(iss >> dim)) {
            *err = "not enough shape dimensions";
            return false;
        }
        spec->shape.push_back(dim);
    }
    std::string extra;
    if (iss >> extra) {
        *err = "extra fields in manifest line";
        return false;
    }
    return true;
}

bool ReadExactFile(const std::string &path, void *buffer, size_t bytes, std::string *err)
{
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file.is_open()) {
        *err = "open failed: " + path + ": " + std::strerror(errno);
        return false;
    }
    const std::streampos endPos = file.tellg();
    if (endPos < 0) {
        *err = "tellg failed: " + path;
        return false;
    }
    const size_t actualSize = static_cast<size_t>(endPos);
    if (actualSize != bytes) {
        *err = "size mismatch: " + path + " expected " + std::to_string(bytes) + " got " +
               std::to_string(actualSize);
        return false;
    }
    file.seekg(0, std::ios::beg);
    if (bytes > 0 && !file.read(static_cast<char *>(buffer), static_cast<std::streamsize>(bytes))) {
        *err = "read failed: " + path;
        return false;
    }
    return true;
}

bool WriteExactFile(const std::string &path, const void *buffer, size_t bytes, std::string *err)
{
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    if (!file.is_open()) {
        *err = "open output failed: " + path + ": " + std::strerror(errno);
        return false;
    }
    if (bytes > 0) {
        file.write(static_cast<const char *>(buffer), static_cast<std::streamsize>(bytes));
        if (!file.good()) {
            *err = "write failed: " + path;
            return false;
        }
    }
    return true;
}

int Init(int32_t deviceId, aclrtStream *stream)
{
    auto ret = aclInit(nullptr);
    CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclInit failed. ERROR: %d\n", ret); return ret);
    ret = aclrtSetDevice(deviceId);
    CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtSetDevice failed. ERROR: %d\n", ret); return ret);
    ret = aclrtCreateStream(stream);
    CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtCreateStream failed. ERROR: %d\n", ret); return ret);
    return SUCCESS;
}

template <typename T>
int CreateAclTensor(const std::vector<T> &hostData, const std::vector<int64_t> &shape, void **deviceAddr,
                    aclDataType dataType, aclTensor **tensor)
{
    auto size = GetShapeSize(shape) * sizeof(T);
    auto ret = aclrtMalloc(deviceAddr, static_cast<size_t>(size), ACL_MEM_MALLOC_HUGE_FIRST);
    CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtMalloc failed. ERROR: %d\n", ret); return ret);

    ret = aclrtMemcpy(*deviceAddr, static_cast<size_t>(size), hostData.data(), static_cast<size_t>(size),
                      ACL_MEMCPY_HOST_TO_DEVICE);
    CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtMemcpy failed. ERROR: %d\n", ret); return ret);

    std::vector<int64_t> strides = CalcRowMajorStrides(shape);
    *tensor = aclCreateTensor(shape.data(), shape.size(), dataType, strides.data(), 0, ACL_FORMAT_ND, shape.data(),
                              shape.size(), *deviceAddr);
    CHECK_RET(*tensor != nullptr, LOG_PRINT("aclCreateTensor failed.\n"); return FAILED);
    return SUCCESS;
}

void DestroyExecutorIfNeeded(aclOpExecutor *executor)
{
    if (executor != nullptr) {
        aclDestroyAclOpExecutor(executor);
    }
}

template <typename T>
bool ReadInputTensor(const std::string &path, size_t numel, std::vector<T> *data, std::string *err)
{
    size_t bytes = 0;
    if (!ComputeBytes(numel, sizeof(T), &bytes, err)) {
        return false;
    }
    data->assign(numel, T{});
    return ReadExactFile(path, data->data(), bytes, err);
}

void WriteMedianUs(const std::string &caseDir, std::vector<double> &samples)
{
    if (samples.empty()) {
        return;
    }
    std::sort(samples.begin(), samples.end());
    const double median = samples[samples.size() / 2];
    std::ofstream out(JoinPath(caseDir, "perf_us.txt"));
    out << median << "\n";
}

// 性能采集：aclrt event 计时 aclnnIsClose 启动（warmup + iters 取中位）。≈kernel-only，非 msprof；失败非致命。
bool TimeCase(aclTensor *x1, aclTensor *x2, aclTensor *y, const CaseSpec &spec, aclrtStream stream,
              const std::string &caseDir, std::string *err)
{
    const int warmup = 3;
    const int iters = 10;
    aclrtEvent start = nullptr;
    aclrtEvent stop = nullptr;
    if (aclrtCreateEvent(&start) != ACL_SUCCESS || aclrtCreateEvent(&stop) != ACL_SUCCESS) {
        *err = "aclrtCreateEvent failed";
        if (start != nullptr) { aclrtDestroyEvent(start); }
        if (stop != nullptr) { aclrtDestroyEvent(stop); }
        return false;
    }
    std::vector<double> samples;
    bool ok = true;
    for (int i = 0; i < warmup + iters && ok; ++i) {
        uint64_t workspaceSize = 0;
        aclOpExecutor *executor = nullptr;
        if (aclnnIsCloseGetWorkspaceSize(x1, x2, spec.rtol, spec.atol, spec.equalNan, y, &workspaceSize,
                                         &executor) != ACL_SUCCESS) {
            *err = "time GetWorkspaceSize failed";
            ok = false;
            break;
        }
        void *wsAddr = nullptr;
        if (workspaceSize > static_cast<uint64_t>(0) &&
            aclrtMalloc(&wsAddr, workspaceSize, ACL_MEM_MALLOC_HUGE_FIRST) != ACL_SUCCESS) {
            DestroyExecutorIfNeeded(executor);
            *err = "time aclrtMalloc failed";
            ok = false;
            break;
        }
        aclrtRecordEvent(start, stream);
        const int ret = aclnnIsClose(wsAddr, workspaceSize, executor, stream);
        aclrtRecordEvent(stop, stream);
        aclrtSynchronizeStream(stream);
        if (wsAddr != nullptr) { aclrtFree(wsAddr); }
        if (ret != ACL_SUCCESS) {
            *err = "time aclnnIsClose failed";
            ok = false;
            break;
        }
        float ms = 0.0f;
        aclrtEventElapsedTime(&ms, start, stop);
        if (i >= warmup) {
            samples.push_back(static_cast<double>(ms) * 1000.0);
        }
    }
    aclrtDestroyEvent(start);
    aclrtDestroyEvent(stop);
    if (!ok) {
        return false;
    }
    WriteMedianUs(caseDir, samples);
    return true;
}

template <typename T>
bool RunTypedCase(const std::string &caseDir, const CaseSpec &spec, size_t numel, aclDataType aclDtype,
                  aclrtStream stream, std::string *err)
{
    std::vector<T> x1Host;
    std::vector<T> x2Host;
    if (!ReadInputTensor(JoinPath(caseDir, "x1.bin"), numel, &x1Host, err)) {
        return false;
    }
    if (!ReadInputTensor(JoinPath(caseDir, "x2.bin"), numel, &x2Host, err)) {
        return false;
    }

    TensorResource x1;
    TensorResource x2;
    TensorResource y;
    std::vector<uint8_t> yInit(numel, 0);
    std::vector<uint8_t> yHost(numel, 0);

    int ret = CreateAclTensor(x1Host, spec.shape, &x1.deviceAddr, aclDtype, &x1.tensor);
    if (ret != SUCCESS) {
        *err = "CreateAclTensor x1 failed: " + std::to_string(ret);
        return false;
    }
    ret = CreateAclTensor(x2Host, spec.shape, &x2.deviceAddr, aclDtype, &x2.tensor);
    if (ret != SUCCESS) {
        *err = "CreateAclTensor x2 failed: " + std::to_string(ret);
        return false;
    }
    ret = CreateAclTensor(yInit, spec.shape, &y.deviceAddr, ACL_BOOL, &y.tensor);
    if (ret != SUCCESS) {
        *err = "CreateAclTensor y failed: " + std::to_string(ret);
        return false;
    }

    uint64_t workspaceSize = 0;
    aclOpExecutor *executor = nullptr;
    ret = aclnnIsCloseGetWorkspaceSize(x1.tensor, x2.tensor, spec.rtol, spec.atol, spec.equalNan, y.tensor,
                                       &workspaceSize, &executor);
    if (ret != ACL_SUCCESS) {
        *err = "aclnnIsCloseGetWorkspaceSize failed: " + std::to_string(ret);
        return false;
    }

    WorkspaceResource workspace;
    if (workspaceSize > static_cast<uint64_t>(0)) {
        ret = aclrtMalloc(&workspace.addr, workspaceSize, ACL_MEM_MALLOC_HUGE_FIRST);
        if (ret != ACL_SUCCESS) {
            DestroyExecutorIfNeeded(executor);
            *err = "aclrtMalloc workspace failed: " + std::to_string(ret);
            return false;
        }
    }

    ret = aclnnIsClose(workspace.addr, workspaceSize, executor, stream);
    if (ret != ACL_SUCCESS) {
        DestroyExecutorIfNeeded(executor);
        *err = "aclnnIsClose failed: " + std::to_string(ret);
        return false;
    }
    executor = nullptr;

    ret = aclrtSynchronizeStream(stream);
    if (ret != ACL_SUCCESS) {
        *err = "aclrtSynchronizeStream failed: " + std::to_string(ret);
        return false;
    }

    size_t outBytes = 0;
    if (!ComputeBytes(numel, sizeof(uint8_t), &outBytes, err)) {
        return false;
    }
    ret = aclrtMemcpy(yHost.data(), outBytes, y.deviceAddr, outBytes, ACL_MEMCPY_DEVICE_TO_HOST);
    if (ret != ACL_SUCCESS) {
        *err = "aclrtMemcpy D2H failed: " + std::to_string(ret);
        return false;
    }

    if (!WriteExactFile(JoinPath(caseDir, "out.bin"), yHost.data(), outBytes, err)) {
        return false;
    }
    std::string timeErr;
    if (!TimeCase(x1.tensor, x2.tensor, y.tensor, spec, stream, caseDir, &timeErr)) {
        LOG_PRINT("%s WARN perf timing failed: %s\n", spec.caseId.c_str(), timeErr.c_str());
    }
    return true;
}

bool RunCase(const std::string &baseDir, const CaseSpec &spec, aclrtStream stream, std::string *err)
{
    size_t numel = 0;
    if (!ComputeNumel(spec.shape, &numel, err)) {
        return false;
    }

    const std::string caseDir = JoinPath(baseDir, spec.caseId);
    if (numel == 0) {
        const uint8_t *empty = nullptr;
        return WriteExactFile(JoinPath(caseDir, "out.bin"), empty, 0, err);
    }

    if (spec.dtype == "float32") {
        return RunTypedCase<float>(caseDir, spec, numel, ACL_FLOAT, stream, err);
    }
    if (spec.dtype == "float16") {
        return RunTypedCase<uint16_t>(caseDir, spec, numel, ACL_FLOAT16, stream, err);
    }
    if (spec.dtype == "bfloat16") {  // bf16：uint16 位模式存储 + ACL_BF16（本轮扩，与 gen_cases codec 对齐）
        return RunTypedCase<uint16_t>(caseDir, spec, numel, ACL_BF16, stream, err);
    }
    *err = "unsupported dtype: " + spec.dtype;
    return false;
}

}  // namespace

int main()
{
    const char *baseEnv = std::getenv("OPRUNWAY_CASES");
    if (baseEnv == nullptr || baseEnv[0] == '\0') {
        LOG_PRINT("OPRUNWAY_CASES is not set\n");
        return FAILED;
    }
    const std::string baseDir(baseEnv);
    const std::string manifestPath = JoinPath(baseDir, "manifest.txt");
    std::ifstream manifest(manifestPath);
    if (!manifest.is_open()) {
        LOG_PRINT("open manifest failed: %s: %s\n", manifestPath.c_str(), std::strerror(errno));
        return FAILED;
    }

    aclrtStream stream = nullptr;
    const int32_t deviceId = 0;
    int ret = Init(deviceId, &stream);
    if (ret != SUCCESS) {
        return FAILED;
    }

    int64_t total = 0;
    int64_t failed = 0;
    std::string line;
    int64_t lineNo = 0;
    while (std::getline(manifest, line)) {
        ++lineNo;
        if (line.empty()) {
            continue;
        }

        ++total;
        CaseSpec spec;
        std::string displayId;
        std::string err;
        if (!ParseCaseLine(line, lineNo, &spec, &displayId, &err)) {
            ++failed;
            LOG_PRINT("%s FAIL %s\n", displayId.c_str(), err.c_str());
            continue;
        }

        if (RunCase(baseDir, spec, stream, &err)) {
            LOG_PRINT("%s ok\n", spec.caseId.c_str());
        } else {
            ++failed;
            LOG_PRINT("%s FAIL %s\n", spec.caseId.c_str(), err.c_str());
        }
    }

    LOG_PRINT("OPRUNWAY_DONE total=%" PRId64 " ok=%" PRId64 " fail=%" PRId64 "\n",
              total, total - failed, failed);

    aclrtDestroyStream(stream);
    aclrtResetDevice(deviceId);
    aclFinalize();

    return failed == 0 ? SUCCESS : FAILED;
}
