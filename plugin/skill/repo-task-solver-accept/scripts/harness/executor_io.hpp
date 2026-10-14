// Shared internal spec/result/IO helpers; include inside executor namespace.
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

// 乘法溢出检查：size_t 回绕会先分出一个小缓冲，再按大尺寸读写，直接越界。
bool MulSize(size_t a, size_t b, size_t *out) {
    if (a != 0 && b > SIZE_MAX / a) return false;
    *out = a * b;
    return true;
}

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
// 统一收尾：每一处释放都查返回值，首个失败连同全部失败明细都记下来
// ---------------------------------------------------------------------------

class Teardown {
  public:
    // 记一次清理调用的返回值。0 忽略；非 0 累进明细，并保留**第一个**失败的返回码。
    void Note(const char *what, int ret) {
        if (ret == 0) return;
        const std::string one = std::string(what) + " ret=" + std::to_string(ret);
        detail_ = detail_.empty() ? one : detail_ + "; " + one;
        if (first_ret_ == 0) first_ret_ = ret;
    }
    int first_ret() const { return first_ret_; }
    const std::string &detail() const { return detail_; }

  private:
    int first_ret_ = 0;
    std::string detail_;
};

