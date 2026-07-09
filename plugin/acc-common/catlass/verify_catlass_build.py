"""catlass generated_harness · **静态构建门**（Mac 可跑，真机 build 前必过）。

只做**静态**校验，**不**声称能证明 msprof 命中（那是运行后 profile_hit_gate 的活，拆两层·补 codex #13）：
- runner cpp 含 `extern "C" __global__ __aicore__` 钉死符号声明 + 期望 kernel 符号名（**去注释/去伪装后**锚定确认）；
- harness CMakeLists：`set_source_files_properties(... LANGUAGE ASC)` + 单行 `catlass_example_add_executable(...)`；
- build 命令：-DCATLASS_ARCH 已注入且 ∈ 白名单 {2201,3510}；
- （给了 staged catlass 根时）先确认它**是真 catlass 根**，再**去注释后**锚定确认非注释、非 `if(FALSE)` 死分支的
  `add_subdirectory(<harness>)`，并校验 `examples/<harness>/` 已落盘（codex #15/#16）。

抗蒙混（codex #15/#16/#17）：
- 一切文本命中走**去注释 + 锚定正则**，纯 substring 会被「注释掉的行 / 字符串里埋的假信号」骗过；
- 外部 `--catlass-dir` 的文件先**边界读**（拒 symlink/非普通文件/超大），FIFO/目录/巨型文件不再崩栈。

用法: python3 verify_catlass_build.py --arch 3510 [--catlass-dir <staged catlass 根>]
只读、stdlib。打印累积 error（非 fail-fast）+ 末行 STATUS: PASSED|FAILED，exit 0/1。
"""
import argparse, os, re, stat, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # acc-common/ 上 sys.path，import catlass_adapter
import catlass_adapter as A  # noqa: E402

_MAX_BYTES = 4 * 1024 * 1024   # 文件读取上限（codex #17：拒巨型文件，防内存/FIFO 挂死）


# ============================================================ 安全读 + 去注释（codex #16/#17）
def _read_text(path):
    """边界读文本：拒 symlink / 非普通文件（目录/FIFO/设备）/ 超大文件；OSError/解码错 → (None, err)。

    专防 `--catlass-dir` 下的外部文件（攻击面）：`CMakeLists.txt` 为目录时干净 FAILED 而非 IsADirectoryError 崩栈。
    """
    try:
        if os.path.islink(path):
            return None, f"{path} 是 symlink，拒绝读取（防指向仓外/敏感文件）"
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return None, f"{path} 非普通文件（目录 / FIFO / 设备？），拒绝读取"
        if st.st_size > _MAX_BYTES:
            return None, f"{path} 过大（{st.st_size}B > {_MAX_BYTES}B 上限），拒绝读取"
        with open(path, "rb") as f:
            raw = f.read(_MAX_BYTES + 1)
        return raw.decode("utf-8"), None
    except (OSError, UnicodeDecodeError) as e:
        return None, f"读取 {path} 失败：{e}"


def _strip_c_comments(s):
    """去 C/C++ 注释（`//`、`/* */`），**保留字符串字面量**（`extern "C"` 的 "C" 是字符串，去了会误杀真声明）。

    仅去注释即可堵「注释里埋假信号」；字符串里自埋正向信号是自欺、非威胁模型（runner 是我们自己的模板）。
    """
    out = []
    i, n = 0, len(s)
    st_ = "code"
    while i < n:
        c = s[i]
        nx = s[i + 1] if i + 1 < n else ""
        if st_ == "code":
            if c == "/" and nx == "/":
                st_ = "line"; i += 2; continue
            if c == "/" and nx == "*":
                st_ = "block"; out.append(" "); i += 2; continue
            if c == '"':
                st_ = "str"; out.append(c); i += 1; continue
            if c == "'":
                st_ = "chr"; out.append(c); i += 1; continue
            out.append(c); i += 1; continue
        if st_ == "line":
            if c == "\n":
                st_ = "code"; out.append("\n")
            i += 1; continue
        if st_ == "block":
            if c == "*" and nx == "/":
                st_ = "code"; out.append(" "); i += 2; continue
            out.append("\n" if c == "\n" else " "); i += 1; continue
        if st_ in ("str", "chr"):
            out.append(c)
            if c == "\\" and nx:
                out.append(nx); i += 2; continue
            if (st_ == "str" and c == '"') or (st_ == "chr" and c == "'"):
                st_ = "code"
            i += 1; continue
    return "".join(out)


def _strip_cmake_comments(s):
    """去 CMake 注释（`#` 行注释、`#[=*[ ... ]=*]` 括号注释），保留双引号字符串里的 `#`。"""
    out = []
    i, n = 0, len(s)
    in_str = False
    bracket = re.compile(r"#\[(=*)\[")
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if c == "#":
            m = bracket.match(s, i)
            if m:
                close = "]" + m.group(1) + "]"
                end = s.find(close, m.end())
                if end == -1:
                    break                       # 未闭合括号注释 → 丢弃其后
                out.append("\n" * s[i:end].count("\n"))
                i = end + len(close); continue
            while i < n and s[i] != "\n":       # 行注释 → 跳到行尾
                i += 1
            continue
        out.append(c); i += 1
    return "".join(out)


# ============================================================ catlass 根 + if(FALSE) 死分支检测（codex #15）
def _is_catlass_root(d):
    """真 catlass 根特征：scripts/build.sh + examples/（堵「伪 catlass 根蒙混过门」）。"""
    return (os.path.isfile(os.path.join(d, "scripts", "build.sh"))
            and os.path.isdir(os.path.join(d, "examples")))


_CMAKE_FALSE = {"", "0", "OFF", "NO", "FALSE", "N", "IGNORE", "NOTFOUND"}
_CMAKE_TRUE = {"1", "ON", "YES", "TRUE", "Y"}


def _cmake_cond_class(cond):
    """CMake if() 常量条件分类：false/true/unknown（多 token 或变量 → unknown，保守当活分支）。"""
    c = cond.strip()
    if not c or re.search(r"\s", c):
        return "false" if c == "" else "unknown"
    up = c.upper()
    if up in _CMAKE_FALSE or up.endswith("-NOTFOUND"):
        return "false"
    if up in _CMAKE_TRUE:
        return "true"
    return "unknown"


def _cmake_add_subdir_dead(text, harness):
    """判断（去注释后的）add_subdirectory(<harness>) 是否落在**常量假分支**（if(FALSE)/if(0)/…）里 → 等效未注册。

    简易 if/elseif/else/endif 栈：只对**常量**条件判死活（变量条件保守当活分支，不误杀）；覆盖 codex #15 举证的
    `if(FALSE) … add_subdirectory … endif()` 死包裹。
    """
    tok = re.compile(
        r"^[ \t]*(if|elseif|else|endif)[ \t]*\(([^)]*)\)"
        r"|^[ \t]*(add_subdirectory)[ \t]*\(\s*" + re.escape(harness) + r"\s*\)",
        re.I | re.M)
    stack = []
    for m in tok.finditer(text):
        if m.group(3):                          # add_subdirectory(<harness>)
            if any(f["dead"] for f in stack):
                return True
            continue
        kw = m.group(1).lower()
        cond = m.group(2) or ""
        if kw == "if":
            cl = _cmake_cond_class(cond)
            stack.append({"matched": cl == "true", "dead": cl == "false"})
        elif kw == "elseif":
            if not stack:
                continue
            top = stack[-1]
            if top["matched"]:
                top["dead"] = True
            else:
                cl = _cmake_cond_class(cond)
                top["dead"] = (cl == "false")
                if cl == "true":
                    top["matched"] = True
        elif kw == "else":
            if stack:
                stack[-1]["dead"] = stack[-1]["matched"]
        elif kw == "endif":
            if stack:
                stack.pop()
    return False


# ============================================================ 主体
def verify(arch, catlass_dir=None):
    """静态构建门主体。返回 error 列表（空=PASSED）。"""
    errs = []
    if arch not in A._ARCH_WHITELIST:
        errs.append(f"arch={arch!r} 不在白名单 {A._ARCH_WHITELIST}")
        return errs                       # arch 非法则后续 profile 无从取
    prof = A.catlass_profile(arch)
    runner_path = os.path.join(_HERE, prof["runner"])
    cmake_path = os.path.join(_HERE, "CMakeLists.txt")

    # 1) build 命令：-DCATLASS_ARCH 注入 + ∈ 白名单
    try:
        _argv, disp = A.build_command(arch, prof["harness"])
        if f"-DCATLASS_ARCH={arch}" not in disp:
            errs.append(f"build 命令未注入 -DCATLASS_ARCH={arch}：{disp}")
    except ValueError as e:
        errs.append(f"build_command 拼装失败：{e}")

    # 2) runner cpp：去注释后锚定 extern C 钉死符号 + 期望符号名作为函数声明（codex #16）
    src, rerr = _read_text(runner_path)
    if rerr:
        errs.append(f"runner cpp 不可读：{rerr}")
    elif src is None:
        errs.append(f"缺 runner cpp：{prof['runner']}")
    else:
        src_nc = _strip_c_comments(src)
        if not re.search(r'extern\s+"C"\s+__global__\s+__aicore__', src_nc):
            errs.append(f'{prof["runner"]}: 缺（非注释态）extern "C" __global__ __aicore__ 钉死符号（msprof -k 需稳定符号）')
        if not re.search(r"\b" + re.escape(prof["kernel_symbol"]) + r"\s*\(", src_nc):
            errs.append(f'{prof["runner"]}: 未见（非注释态）期望 kernel 符号 {prof["kernel_symbol"]} 的函数声明')

    # 3) harness CMakeLists：去注释后 LANGUAGE ASC + 单行 catlass_example_add_executable（codex #16）
    txt, cerr = _read_text(cmake_path)
    if cerr:
        errs.append(f"harness CMakeLists 不可读：{cerr}")
    elif txt is None:
        errs.append("缺 harness CMakeLists.txt 模板")
    else:
        nc = _strip_cmake_comments(txt)
        if "LANGUAGE ASC" not in nc:
            errs.append("CMakeLists 缺（非注释态）set_source_files_properties(... LANGUAGE ASC)")
        add_lines = re.findall(r"^[ \t]*catlass_example_add_executable[ \t]*\(", nc, re.M)
        if not add_lines:
            errs.append("CMakeLists 缺（非注释态）catlass_example_add_executable(...)")
        elif len(add_lines) != 1:
            errs.append(f"catlass_example_add_executable 非单行（{len(add_lines)} 行）—— 防符号解析漂移须单行")

    # 4) staged catlass（可选）：真根校验 + 去注释非死分支 add_subdirectory + examples/<harness>/ 落盘（codex #15）
    if catlass_dir:
        harness = prof["harness"]
        if not _is_catlass_root(catlass_dir):
            errs.append(f"{catlass_dir} 非 catlass 根（缺 scripts/build.sh 或 examples/），拒绝当作 staged 根")
        else:
            root_cmake = os.path.join(catlass_dir, "examples", "CMakeLists.txt")
            rtxt, rterr = _read_text(root_cmake)
            if rterr:
                errs.append(f"examples/CMakeLists.txt 不可读：{rterr}")
            elif rtxt is None:
                errs.append(f"找不到 {root_cmake}（--catlass-dir 是否为 catlass 根？）")
            else:
                rnc = _strip_cmake_comments(rtxt)
                add_re = re.compile(r"^[ \t]*add_subdirectory[ \t]*\(\s*" + re.escape(harness) + r"\s*\)", re.M)
                if not add_re.search(rnc):
                    errs.append(f"examples/CMakeLists.txt 未见（非注释态）add_subdirectory({harness})（先跑 stage_into_catlass.sh）")
                elif _cmake_add_subdir_dead(rnc, harness):
                    errs.append(f"add_subdirectory({harness}) 被常量假分支（if(FALSE)/if(0)/…）包裹，等效未注册")
            hdir = os.path.join(catlass_dir, "examples", harness)
            if not os.path.isdir(hdir):
                errs.append(f"缺 examples/{harness}/ 目录（staging 未落盘？）")
            elif not os.path.isfile(os.path.join(hdir, "CMakeLists.txt")):
                errs.append(f"examples/{harness}/ 缺 CMakeLists.txt（staging 不完整）")
    return errs


def main(argv):
    ap = argparse.ArgumentParser(description="catlass generated_harness 静态构建门（Mac 可跑）")
    ap.add_argument("--arch", required=True)
    ap.add_argument("--catlass-dir", default=None, help="staged catlass 工作副本根（可选）")
    a = ap.parse_args(argv)
    print(f"=== catlass 静态构建门 arch={a.arch} ===")
    errs = verify(a.arch, a.catlass_dir)
    for e in errs:
        print(f"  ✗ {e}")
    passed = not errs
    print(f"STATUS: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
