"""catlass generated_harness · **静态构建门**（Mac 可跑，真机 build 前必过）。

只做**静态**校验，**不**声称能证明 msprof 命中（那是运行后 profile_hit_gate 的活，拆两层·补 codex #13）：
- runner cpp 含 `extern "C" __global__ __aicore__` 钉死符号声明 + 期望 kernel 符号名；
- harness CMakeLists：`set_source_files_properties(... LANGUAGE ASC)` + 单行 `catlass_example_add_executable(...)`；
- build 命令：-DCATLASS_ARCH 已注入且 ∈ 白名单 {2201,3510}；
- （给了 staged catlass 根时）examples/CMakeLists.txt 已幂等注入 `add_subdirectory(<harness>)`。

用法: python3 verify_catlass_build.py --arch 3510 [--catlass-dir <staged catlass 根>]
只读、stdlib。打印累积 error（非 fail-fast）+ 末行 STATUS: PASSED|FAILED，exit 0/1。
"""
import argparse, os, re, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # acc-common/ 上 sys.path，import catlass_adapter
import catlass_adapter as A  # noqa: E402


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

    # 2) runner cpp：extern C 钉死符号 + 期望符号名
    if not os.path.exists(runner_path):
        errs.append(f"缺 runner cpp：{prof['runner']}")
    else:
        src = open(runner_path, encoding="utf-8").read()
        if 'extern "C" __global__ __aicore__' not in src:
            errs.append(f"{prof['runner']}: 缺 extern \"C\" __global__ __aicore__ 钉死符号（msprof -k 需稳定符号）")
        if prof["kernel_symbol"] not in src:
            errs.append(f"{prof['runner']}: 未见期望 kernel 符号 {prof['kernel_symbol']}")

    # 3) harness CMakeLists：LANGUAGE ASC + 单行 catlass_example_add_executable
    if not os.path.exists(cmake_path):
        errs.append("缺 harness CMakeLists.txt 模板")
    else:
        lines = open(cmake_path, encoding="utf-8").read().splitlines()
        if not any("LANGUAGE ASC" in ln for ln in lines):
            errs.append("CMakeLists 缺 set_source_files_properties(... LANGUAGE ASC)")
        add_lines = [ln for ln in lines if "catlass_example_add_executable" in ln and not ln.strip().startswith("#")]
        if not add_lines:
            errs.append("CMakeLists 缺 catlass_example_add_executable(...)")
        elif len(add_lines) != 1:
            errs.append(f"catlass_example_add_executable 非单行（{len(add_lines)} 行）—— 防符号解析漂移须单行")

    # 4) staged catlass（可选）：examples/CMakeLists.txt 已注入 add_subdirectory
    if catlass_dir:
        root_cmake = os.path.join(catlass_dir, "examples", "CMakeLists.txt")
        if not os.path.exists(root_cmake):
            errs.append(f"找不到 {root_cmake}（--catlass-dir 是否为 catlass 根？）")
        else:
            txt = open(root_cmake, encoding="utf-8").read()
            if f"add_subdirectory({prof['harness']})" not in txt:
                errs.append(f"examples/CMakeLists.txt 未注入 add_subdirectory({prof['harness']})（先跑 stage_into_catlass.sh）")
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
