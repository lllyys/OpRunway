// OpRunway 真机 runner · Sign（一元、数值输出，同 dtype）。读 OPRUNWAY_CASES/manifest.txt 循环 case，
// 读 x1.bin → aclnnSign(self, out) → 写 out.bin（同 dtype）。manifest 行：case_id dtype ndim dims...
#include <algorithm>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "acl/acl.h"
#include "aclnnop/aclnn_sign.h"

#define SUCCESS 0
#define FAILED 1
#define LOG_PRINT(m, ...) do { std::printf(m, ##__VA_ARGS__); } while (0)

namespace {
std::string JoinPath(const std::string &a, const std::string &b) { return a + "/" + b; }
int64_t ShapeSize(const std::vector<int64_t> &s) { int64_t n = 1; for (auto d : s) n *= d; return n; }

bool ReadExact(const std::string &p, void *buf, size_t bytes, std::string *err) {
    std::ifstream f(p, std::ios::binary);
    if (!f) { *err = "open " + p; return false; }
    f.read(static_cast<char *>(buf), static_cast<std::streamsize>(bytes));
    if (static_cast<size_t>(f.gcount()) != bytes) { *err = p + " short read"; return false; }
    return true;
}
bool WriteExact(const std::string &p, const void *buf, size_t bytes, std::string *err) {
    std::ofstream f(p, std::ios::binary);
    if (!f) { *err = "open-w " + p; return false; }
    if (bytes) f.write(static_cast<const char *>(buf), static_cast<std::streamsize>(bytes));
    if (!f) { *err = p + " write"; return false; }
    return true;
}
int Init(int32_t dev, aclrtStream *stream) {
    if (aclInit(nullptr) != ACL_SUCCESS || aclrtSetDevice(dev) != ACL_SUCCESS ||
        aclrtCreateStream(stream) != ACL_SUCCESS) return FAILED;
    return SUCCESS;
}
template <typename T>
int MkTensor(const std::vector<T> &host, const std::vector<int64_t> &shape, void **dev,
             aclDataType dt, aclTensor **t) {
    size_t bytes = static_cast<size_t>(ShapeSize(shape)) * sizeof(T);
    if (aclrtMalloc(dev, bytes, ACL_MEM_MALLOC_HUGE_FIRST) != ACL_SUCCESS) return FAILED;
    if (bytes && aclrtMemcpy(*dev, bytes, host.data(), bytes, ACL_MEMCPY_HOST_TO_DEVICE) != ACL_SUCCESS)
        return FAILED;
    std::vector<int64_t> st(shape.size(), 1);
    for (int64_t i = static_cast<int64_t>(shape.size()) - 2; i >= 0; --i) st[i] = shape[i + 1] * st[i + 1];
    *t = aclCreateTensor(shape.data(), shape.size(), dt, st.data(), 0, ACL_FORMAT_ND,
                         shape.data(), shape.size(), *dev);
    return *t ? SUCCESS : FAILED;
}

struct Case { std::string id, dtype; std::vector<int64_t> shape; };

bool ParseLine(const std::string &line, Case *c, std::string *err) {
    std::istringstream is(line);
    int64_t ndim = 0;
    if (!(is >> c->id >> c->dtype >> ndim)) { *err = "bad manifest line"; return false; }
    if (ndim < 0 || ndim > 8) { *err = "bad ndim"; return false; }
    c->shape.clear();
    for (int64_t i = 0; i < ndim; ++i) {
        int64_t d;
        if (!(is >> d) || d < 0) { *err = "bad dim"; return false; }
        c->shape.push_back(d);
    }
    return true;
}

template <typename T>
bool RunTyped(const std::string &dir, const Case &c, size_t numel, aclDataType dt,
              aclrtStream stream, std::string *err) {
    std::vector<T> x(numel, T{}), yh(numel, T{});
    if (!ReadExact(JoinPath(dir, "x1.bin"), x.data(), numel * sizeof(T), err)) return false;
    void *xd = nullptr, *yd = nullptr;
    aclTensor *xt = nullptr, *yt = nullptr;
    if (MkTensor(x, c.shape, &xd, dt, &xt) != SUCCESS) { *err = "mk x"; return false; }
    if (MkTensor(yh, c.shape, &yd, dt, &yt) != SUCCESS) { *err = "mk y"; return false; }
    uint64_t ws = 0;
    aclOpExecutor *ex = nullptr;
    if (aclnnSignGetWorkspaceSize(xt, yt, &ws, &ex) != ACL_SUCCESS) { *err = "GetWorkspaceSize"; return false; }
    void *wa = nullptr;
    if (ws > 0 && aclrtMalloc(&wa, ws, ACL_MEM_MALLOC_HUGE_FIRST) != ACL_SUCCESS) { *err = "malloc ws"; return false; }
    int r = aclnnSign(wa, ws, ex, stream);
    if (r != ACL_SUCCESS) { *err = "aclnnSign " + std::to_string(r); return false; }
    if (aclrtSynchronizeStream(stream) != ACL_SUCCESS) { *err = "sync"; return false; }
    if (aclrtMemcpy(yh.data(), numel * sizeof(T), yd, numel * sizeof(T),
                    ACL_MEMCPY_DEVICE_TO_HOST) != ACL_SUCCESS) { *err = "D2H"; return false; }
    if (wa) aclrtFree(wa);
    aclDestroyTensor(xt); aclDestroyTensor(yt); aclrtFree(xd); aclrtFree(yd);
    return WriteExact(JoinPath(dir, "out.bin"), yh.data(), numel * sizeof(T), err);
}

bool RunCase(const std::string &base, const Case &c, aclrtStream stream, std::string *err) {
    int64_t n = 1;
    for (auto d : c.shape) { if (d && n > INT64_MAX / d) { *err = "numel overflow"; return false; } n *= d; }
    const size_t numel = static_cast<size_t>(n);
    const std::string dir = JoinPath(base, c.id);
    if (numel == 0) { const float e = 0; return WriteExact(JoinPath(dir, "out.bin"), &e, 0, err); }
    if (c.dtype == "float32") return RunTyped<float>(dir, c, numel, ACL_FLOAT, stream, err);
    if (c.dtype == "float16") return RunTyped<uint16_t>(dir, c, numel, ACL_FLOAT16, stream, err);
    if (c.dtype == "bfloat16") return RunTyped<uint16_t>(dir, c, numel, ACL_BF16, stream, err);  // bf16：uint16+ACL_BF16
    *err = "unsupported dtype " + c.dtype;
    return false;
}
}  // namespace

int main() {
    const char *base = std::getenv("OPRUNWAY_CASES");
    if (base == nullptr || base[0] == '\0') { LOG_PRINT("OPRUNWAY_CASES not set\n"); return FAILED; }
    std::ifstream man(JoinPath(base, "manifest.txt"));
    if (!man.is_open()) { LOG_PRINT("open manifest failed\n"); return FAILED; }
    aclrtStream stream = nullptr;
    if (Init(0, &stream) != SUCCESS) return FAILED;
    int64_t total = 0, failed = 0;
    std::string line;
    while (std::getline(man, line)) {
        if (line.empty()) continue;
        ++total;
        Case c;
        std::string err;
        if (!ParseLine(line, &c, &err)) { ++failed; LOG_PRINT("? FAIL %s\n", err.c_str()); continue; }
        if (RunCase(base, c, stream, &err)) LOG_PRINT("%s ok\n", c.id.c_str());
        else { ++failed; LOG_PRINT("%s FAIL %s\n", c.id.c_str(), err.c_str()); }
    }
    LOG_PRINT("OPRUNWAY_DONE total=%" PRId64 " ok=%" PRId64 " fail=%" PRId64 "\n",
              total, total - failed, failed);
    aclrtDestroyStream(stream);
    aclrtResetDevice(0);
    aclFinalize();
    return failed == 0 ? SUCCESS : FAILED;
}
