#!/usr/bin/env python3
"""把待验收算子目录合进母仓、构建、装包，并验证符号确实可见。

社区算子目录是母仓的一个子目录，独立构建不出来，必须回母仓跑 build.sh。
退出码 0 装好且符号可见，2 构建或安装失败，3 符号不可见，4 入参路径不对。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
import stage_clock  # noqa: E402 - 同目录量具，给 stage JSON 记阶段墙钟

# 两个都是**兜底上限，不是预期耗时**：超时只说明卡死了，不说明构建慢。
# 单算子增量构建实测两分钟量级（`install.json` 的 `build.elapsed_seconds`），
# 但母仓首次全量构建要编上千个算子，量级差两个数量级——上限按后者留，
# 不然换一台没有构建缓存的机器就会被误杀。
BUILD_TIMEOUT = 5400          # 90 分钟，覆盖冷缓存下的全量母仓构建
INSTALL_TIMEOUT = 600         # 10 分钟，`.run` 包只是解包拷贝，实测秒级


def _is_op_dir(path):
    """这个目录是不是一个算子目录。两种形态都认，**按结构认，不按名字认**。

    | 形态 | 长什么样 | 出处 |
    | --- | --- | --- |
    | 母仓子模块 | 直接含 `op_kernel/` 或 `op_host/` | ops-math / ops-nn |
    | 自足工程 | 含 `arch<NN>/`，且本级或 arch 级有 `*_host.cpp`／`*_kernel.cpp` | ops-sparse |

    第二种**host 与 kernel 放哪一级不统一**：spmv 两个都在 `arch22/` 下，
    spgemm 的 `spgemm_host.cpp` 在算子目录顶层、`arch22/` 只放 kernel
    （两者都实测过，2026-09-08）。所以判据是「有 arch 目录」加上
    「本级或 arch 级找得到 host 或 kernel 源码」，**不钉死在哪一级**。
    """
    if (path / "op_kernel").is_dir() or (path / "op_host").is_dir():
        return True
    archs = [d for d in path.iterdir() if d.is_dir() and d.name.startswith("arch")]
    if not archs:
        return False
    return any(any(where.rglob(pattern))
               for where in [path] + archs
               for pattern in ("*_host.cpp", "*_kernel.cpp"))


# 工程形态：**决定怎么编、怎么装、怎么核符号**，与「ATK 怎么调算子」正交。
# 后者是 facts.json 的 backend（见 run_atk.py 的 PROFILES），由生成侧定。
# 两者独立：自足工程也可以暴露 aclnn 两段式接口，母仓算子也可以只注册 torch 算子。
#
# 判据取仓根的结构，不取仓名：
#   - `build.sh` 且 `cmake/func.cmake`  -> opp-vendor（ops-math / ops-nn）
#   - `build.sh` 且没有 `cmake/func.cmake` -> standalone-so（ops-sparse）
KIND_OPP_VENDOR = "opp-vendor"
KIND_STANDALONE = "standalone-so"


def _find_op_dir(project, op):
    """待验收工程可能是算子目录本身，也可能是带着母仓路径前缀的解压包。

    **按目录结构找，不按名字找。** aclnn 接口名与算子目录名常常不同：
    aclnnIndexFillTensor 的目录是 index_fill，不是 index_fill_tensor。
    名字只在有多个候选时用来消歧。
    """
    project = Path(project).resolve()
    if not project.is_dir():
        return None, f"{project} 不是目录"
    if _is_op_dir(project):
        return project, None

    matches = [p for p in project.rglob("*")
               if p.is_dir() and "build" not in p.parts and _is_op_dir(p)]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, (f"{project} 下找不到算子目录。两种形态都认："
                      f"含 op_kernel/ 或 op_host/ 的（母仓子模块形态），"
                      f"或含 arch*/ 且底下有 *_host.cpp 的（自足工程形态）。"
                      f"--project 要指到算子目录或它的解压根。")

    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", op).lower()
    named = [p for p in matches if p.name == snake]
    if len(named) == 1:
        return named[0], None
    prefixed = [p for p in matches if snake.startswith(p.name)]
    if len(prefixed) == 1:
        return prefixed[0], None
    listed = "、".join(str(p) for p in matches)
    return None, f"{project} 下有多个候选（{listed}），--project 指具体一个。"


def _repo_root(op_dir):
    """算子是不是已经躺在一个能构建的仓里：沿**它自己的祖先**往上找。
    返回 `(仓根, 工程形态)`，找不到返回 `(None, None)`。

    `build.sh` 是仓的标记，`cmake/func.cmake` 区分两种形态：有它是 opp-vendor
    （算子是母仓的 CMake 子模块，装出来进 opp vendor 层），没有它是 standalone-so
    （自足工程，装出来是普通共享库）。**从前只认前一种**，自足工程被判成
    「不是仓」而退 4，让人手工构建。

    这和「在机器上搜一个母仓」不是一回事：这里问的是「交付物自己是什么形态」，
    答案只可能在这个算子的祖先链上。PR 工作流下开发者交的本来就是 fork 的分支，
    算子已经在它该在的位置，原地编即可——不用同步、不用推落点，也不存在
    「哪部分代码来自交付物、哪部分来自另一个母仓」的问题。
    """
    current = Path(op_dir).resolve()
    while current != current.parent:
        if (current / "build.sh").is_file():
            kind = (KIND_OPP_VENDOR if (current / "cmake" / "func.cmake").is_file()
                    else KIND_STANDALONE)
            return current, kind
        current = current.parent
    return None, None


def _locate_in_parent(parent, snake):
    """算子在母仓里已经在的位置。**不猜落点，看它在哪。**

    早先按 experimental/index/math/conversion 四个锚推路径，那等于假设「社区任务
    都是往 experimental/ 加新算子」。改已有算子的任务不成立：stateless_bernoulli
    在 `random/` 下，按锚推会落到非 experimental 位置再配 --experimental 构建，
    报 `Specified ops not found`，而报错文本指向算子名，不指向落点。
    """
    def _is_artifact(path):
        # 构建产物目录不止叫 build/build_out：真机上见过
        # build.stale_from_libotao2_20260813/，按前缀排除才拦得住。
        return any(part.startswith("build") or part == "CMakeFiles" for part in path.parts)

    return sorted(p for p in Path(parent).rglob(snake)
                  if p.is_dir() and not _is_artifact(p.relative_to(parent)))


def _who_defines(parent, symbol):
    """母仓里哪些算子目录定义了这个符号。

    aclnn 入口不一定在被测算子自己的目录里：改 stateless_bernoulli 的 kernel，
    而 aclnnBernoulli 的 host 实现在 dsa_gen_bit_mask 下。只编前者，装出来的包里
    没有这个符号，而符号核查的报错会指向 vendor 目录和包名——方向是反的。
    """
    result = subprocess.run(
        ["grep", "-rl", "--include=*.cpp", "--include=*.h", symbol, str(parent)],
        capture_output=True, text=True, check=False)
    parent = Path(parent)
    found = set()
    for line in (result.stdout or "").splitlines():
        path = Path(line)
        if {"build", "build_out"} & set(path.parts):
            continue
        for ancestor in path.parents:
            if (ancestor / "op_host").is_dir() or (ancestor / "op_kernel").is_dir():
                try:
                    found.add(str(ancestor.relative_to(parent)))
                except ValueError:
                    pass
                break
    return sorted(found)


def _git(op_dir, *args):
    result = subprocess.run(["git", "-C", str(op_dir), *args],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _source_identity(op_dir):
    """待验收源码到底是哪一份：仓根、分支、最近改动这个目录的提交、有无未提交改动。

    **符号可见证明不了装的是待验收实现。** 不同算子目录可以实现同一套 aclnn
    接口——母仓里实测见过两个不同目录导出同一个 `aclnn<Op>GetWorkspaceSize`，
    装错目录时下面第 4 步的符号核查照样通过、退出码照样 0，一路跑到精度报告
    才表现成「整类 dtype 不达标」。
    把身份记进 install.json 并打印，是让「装的是哪一份源码」在结论里可见的
    最小手段；对不对得上任务书由人判，脚本不猜。
    """
    if _git(op_dir, "rev-parse", "--is-inside-work-tree") != "true":
        return {"vcs": "none"}
    head = _git(op_dir, "log", "-1", "--format=%h\x1f%ad\x1f%s", "--date=short", "--", ".")
    parts = head.split("\x1f") if head else []
    dirty = [line for line in
             _git(op_dir, "status", "--porcelain", "--", ".").splitlines() if line.strip()]
    return {
        "vcs": "git",
        "repo": _git(op_dir, "rev-parse", "--show-toplevel"),
        "branch": _git(op_dir, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": parts[0] if parts else "",
        "date": parts[1] if len(parts) > 1 else "",
        "subject": parts[2] if len(parts) > 2 else "",
        "dirty_files": len(dirty),
    }


def _print_source(source, op_dir):
    print(f"源码      {op_dir}")
    if source.get("vcs") != "git":
        print("          不在 git 仓里，核对不了版本——自己确认这是待验收的那份")
        return
    print(f"          {source['commit']} {source['date']} {source['subject']}"
          f"（分支 {source['branch']}）")
    dirty = source["dirty_files"]
    print("          工作区干净" if not dirty else f"          工作区有 {dirty} 处未提交改动")
    print("          **这一行与任务书对不上就停下来**：符号核查判不出装错算子目录。")


def _force_rmtree(path):
    """装出来的 opp 目录里有只读的 scripts/，直接 rmtree 会 PermissionError。"""
    def _chmod_retry(func, target, _exc):
        os.chmod(Path(target).parent, 0o755)
        os.chmod(target, 0o755)
        func(target)

    shutil.rmtree(path, onexc=_chmod_retry)


def _run(command, cwd, timeout, log_path):
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as handle:
        proc = subprocess.run(command, cwd=cwd, stdout=handle,
                              stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return proc.returncode, time.time() - started


def _tail(path, count=25):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-count:]


# 编译器诊断行：`路径:行:列: <级别>…`。级别在中文 locale 下是「錯誤」「嚴重錯誤」
# 「警告」「附註」，英文下是 error/warning/note——**两种都要认**，构建机的 locale
# 不归本脚本管。只挑错误，警告与展开注释一概不算。
_DIAG = re.compile(r"^\s*(/[^\s:]+):\d+:\d+:\s*(.*)$")
_NOT_ERROR = ("警告", "附註", "附注", "warning", "note")


def _blame_lines(log_path, target):
    """构建失败时，先说清楚错在待验收算子里还是在母仓的公共代码里。

    **这两种失败的去向完全不同**：错在算子目录里是待验收实现的问题，按验收流程
    如实记进报告；错在母仓公共代码里与本算子无关，是母仓与本机 CANN 版本不匹配，
    改用例包、改算子都没用。不区分的话，读报告的人看到一屏编译错误只能自己去
    数路径，而母仓的错误信息里往往一次刷几十行头文件展开。
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    files, first = [], None
    for line in text.splitlines():
        m = _DIAG.match(line)
        if not m:
            continue
        level = m.group(2)
        if any(word in level for word in _NOT_ERROR):
            continue
        if "error" not in level and "錯誤" not in level and "错误" not in level:
            continue
        if m.group(1) not in files:
            files.append(m.group(1))
        if first is None:
            first = line.strip()
    if not files:
        return []
    scope = str(Path(target).resolve()) if target else None
    inside = [f for f in files if scope and f.startswith(scope + "/")]
    outside = [f for f in files if f not in inside]
    # 第一条错误最重要：后面的往往是它的连锁反应，而日志尾部多半是**别的**
    # 目标在并行编译里刷出来的东西，与真正的失败无关。实测踩过——尾部 12 行
    # 全是母仓 ONNX 插件的告警，真正的错在两千行之前的算子目录里。
    lines = [f"首个错误  {first}"] if first else []
    if inside:
        lines.append(f"报错落在待验收算子目录里（{len(inside)} 个文件）：")
        lines += [f"  {f}" for f in inside[:5]]
    if outside and not inside:
        lines.append(f"**报错全部落在算子目录外**（{len(outside)} 个文件），"
                     "与待验收实现无关：")
        lines += [f"  {f}" for f in outside[:5]]
        lines.append("这是母仓自身与本机 CANN 版本不匹配。改用例包或改算子都没用，"
                     "先让母仓在本机能空跑通一次构建，再回 A2。")
    elif outside:
        lines.append(f"另有 {len(outside)} 个文件的报错在算子目录外。")
    return lines


def _torch_extension(project, module):
    """把待验收工程里那份 torch 扩展编出来，返回它落在哪个目录。

    **扩展 so 是 .gitignore 掉的构建产物**：克隆下来的工程里没有它，A2 不编的话
    npu 剖面的探针就 import 不到注册模块，而那时的报错是「待验收实现没装进来」，
    指向装包，看不出少的是这一步。实测一轮在这里来回四次。

    编哪一份按 `setup.py` 的内容认，不按目录名认——不同工程放的位置不同。
    没有对应的 `setup.py` 就返回 None，那是「这个工程不需要编扩展」，不是错。
    """
    if not module:
        return None, ""
    root = Path(project).resolve()
    setups = [s for s in sorted(root.rglob("setup.py"))
              if "build" not in s.parts and ".git" not in s.parts]
    target = None
    for setup in setups:
        try:
            if module in setup.read_text(encoding="utf-8", errors="replace"):
                target = setup.parent
                break
        except OSError:
            continue
    if target is None:
        return None, ""
    print(f"torch 扩展  {target}/setup.py 里声明了 {module}，就地编")
    out = subprocess.run([sys.executable, "setup.py", "build_ext", "--inplace"],
                         cwd=target, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()[-15:]
        print(f"\n扩展没编出来（退 {out.returncode}）：", file=sys.stderr)
        print("  " + "\n  ".join(tail), file=sys.stderr)
        return None, "failed"
    # `--inplace` 的落点随 setup.py 里的包布局走，猜不得——编完现找。
    hits = [so for so in root.rglob(f"{module}*.so") if "temp." not in str(so)]
    if not hits:
        print(f"\n编完了但 {root} 下找不到 {module}*.so。", file=sys.stderr)
        return None, "failed"
    newest = max(hits, key=lambda so: so.stat().st_mtime)
    print(f"            {newest}")
    return newest.parent, ""


def _finish_standalone(args, record, op_dir, opp_root, snake, logs):
    """自足工程的第 4、5 步：核符号、写 env.sh、落 install.json。

    与 opp-vendor 那条的三处不同：
      - 库在 `<install-path>/cann/lib64/*.so`，**比给的路径多一层 `cann`**
      - 没有 opp vendor 层，生效靠 `LD_LIBRARY_PATH`
      - 符号名不是 `aclnn<Op>GetWorkspaceSize`：全仓 `GetWorkspaceSize` 命中 0，
        接口是一段式的 `aclsparse<Op>` 系列。所以核什么由 `--symbol` 给，
        缺省取 `--op` 原文当子串。
    """
    lib_dir = opp_root / "cann" / "lib64"
    libs = sorted(lib_dir.glob("*.so"))
    if not libs:
        print(f"\n{lib_dir} 下没有 .so。装包没落文件——`--install` 漏了的话"
              f"包解压完就退，退出码还是 0。", file=sys.stderr)
        return 3
    symbol = args.symbol or args.op
    hits = []
    for lib in libs:
        nm = subprocess.run(["nm", "-D", str(lib)], capture_output=True,
                            text=True, check=False)
        if symbol in nm.stdout:
            hits.append(lib)
    if not hits:
        print(f"\n{lib_dir} 下 {len(libs)} 个 .so 里都没有 {symbol}。", file=sys.stderr)
        print(f"实际导出的（每库前 5 个）：", file=sys.stderr)
        for lib in libs[:3]:
            nm = subprocess.run(["nm", "-D", "--defined-only", str(lib)],
                                capture_output=True, text=True, check=False)
            names = [l.split()[-1] for l in nm.stdout.splitlines() if " T " in l][:5]
            print(f"  {lib.name}: {'、'.join(names) or '（没有导出符号）'}", file=sys.stderr)
        print(f"\n核什么由 --symbol 给，缺省是 --op 的原文。算子接口名与 --op "
              f"不同名时显式给 --symbol。", file=sys.stderr)
        return 3
    record["symbol"] = symbol
    record["install_root"] = str(opp_root)
    record["lib_dir"] = str(lib_dir)
    record["under_test_lib"] = str(hits[0])
    record["register_module"] = args.register_module

    ext_dir, ext_err = _torch_extension(args.project, args.register_module)
    if ext_err:
        return 2
    record["torch_extension_dir"] = str(ext_dir) if ext_dir else ""

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe_env import write_env_sh  # noqa: E402 - 同目录量具，装完才有值可写

    write_env_sh("evidence/env.sh", os.environ.get("ASCEND_TOOLKIT_HOME", ""),
                 sys.executable, str(opp_root), standalone_lib=str(lib_dir),
                 pythonpath=str(ext_dir) if ext_dir else "")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(record), handle, ensure_ascii=False, indent=2)

    print(f"符号      {symbol} 在 {hits[0].name}（**符号可见 ≠ 装的是待验收实现**，"
          f"核上面那行源码）")
    print(f"库目录    {lib_dir}")
    print(f"env.sh    已重写，含 LD_LIBRARY_PATH")
    if not args.register_module:
        print("\n注册模块  没给 --register-module。npu 剖面跑测前要 import 它再核"
              "/proc/self/maps，缺了 run_atk.py 会退 3。", file=sys.stderr)
    print(f"写入      {args.out}")
    print("\n下一条命令起都要先 source evidence/env.sh。")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--op", required=True, help="算子名，如 Roll")
    parser.add_argument("--project", required=True, help="待验收算子工程目录")
    parser.add_argument("--parent-repo", default="",
                        help="母仓根，含 build.sh。只在交付物是裸算子目录时需要；交付物本身是仓时不用给")
    parser.add_argument("--soc", required=True, help="build.sh 的 --soc 取值")
    parser.add_argument("--target", default="",
                        help="算子在母仓里的落点，母仓相对路径。母仓里已有同名目录时不用给；新增算子或有多个同名目录时必须给，路径见任务书「PR 申请合入」")
    parser.add_argument("--extra-ops", default="",
                        help="逗号分隔，与被测算子一起编的其它算子目录名。aclnn 入口在别的算子里时要带上，符号核不到时脚本会报出该带谁")
    parser.add_argument("--opp-root", default="opp", help="装到哪，默认 opp/")
    parser.add_argument("--vendor-name", default="", help="默认 <op 小写>_atk")
    parser.add_argument("--jobs", default="16", help="build.sh -j，默认 16")
    parser.add_argument("--skip-build", action="store_true", help="复用已有 build_out")
    parser.add_argument("--no-clean", action="store_true",
                        help="不清 build/，只在连续构建同一个算子时用")
    parser.add_argument("--symbol", default="",
                        help="自足工程核哪个导出符号，缺省取 --op 原文当子串。"
                             "母仓形态忽略这个参数，固定核 aclnn<Op>GetWorkspaceSize")
    parser.add_argument("--register-module", default="",
                        help="import 一下就完成 torch 注册的那个 python 模块名。"
                             "npu 剖面跑测前 import 它再核 /proc/self/maps，"
                             "确认进程里装的是本轮这份实现")
    parser.add_argument("-o", "--out", default="install.json")
    args = parser.parse_args()

    op_dir, problem = _find_op_dir(args.project, args.op)
    if problem:
        print(problem, file=sys.stderr)
        return 4

    snake = op_dir.name
    vendor_name = args.vendor_name or f"{snake}_atk"
    logs = Path("evidence")
    logs.mkdir(parents=True, exist_ok=True)
    record = {
        "op": args.op,
        "op_dir": str(op_dir),
        "soc": args.soc,
        "vendor_name": vendor_name,
    }
    # 在构建之前打，不在之后打：装错目录时这一行是唯一能省下 80 秒构建
    # 加两分钟跑测的东西。
    record["source"] = _source_identity(op_dir)
    _print_source(record["source"], op_dir)

    # 1. 定构建根。首选交付物自己就是能构建的仓——那是 PR 工作流的自然产物，
    #    算子已经在它该在的位置，不用同步、不用推落点、不用分辨哪部分代码来自哪。
    # 显式给了 --parent-repo 就按裸目录模式走，显式压过探测
    repo, kind = (None, None) if args.parent_repo else _repo_root(op_dir)
    if repo is not None:
        build_root, target, need_sync = repo, op_dir, False
        record["mode"] = "repo"
        record["repo"] = str(repo)
        record["project_kind"] = kind
        print(f"交付形态  仓（{repo}），算子在 {op_dir.relative_to(repo)}，原地构建")
        print(f"工程形态  {kind}"
              + ("（母仓子模块，装进 opp vendor 层）" if kind == KIND_OPP_VENDOR
                 else "（自足工程，装出来是普通共享库）"))
    else:
        if not args.parent_repo:
            print(f"{op_dir} 编不出来：没有自带 build.sh，"
                  f"CMakeLists 只调母仓的宏（add_all_modules_sources）。", file=sys.stderr)
            print("两条路，选一条：", file=sys.stderr)
            print("  1. 给整个仓（你 fork 的分支导出，根目录含 build.sh），"
                  "--project 指到仓根 —— 推荐", file=sys.stderr)
            print("  2. 裸算子目录 + --parent-repo <母仓根>，母仓按任务书"
                  "「开源仓地址」自行 clone", file=sys.stderr)
            return 4
        parent = Path(args.parent_repo).resolve()
        if not (parent / "build.sh").is_file():
            print(f"{parent} 下没有 build.sh，不是母仓根。见 build-deploy.md。",
                  file=sys.stderr)
            return 4
        build_root = parent
        kind = (KIND_OPP_VENDOR if (parent / "cmake" / "func.cmake").is_file()
                else KIND_STANDALONE)
        record["mode"] = "parent-repo"
        record["parent_repo"] = str(parent)
        record["project_kind"] = kind
        if args.target:
            target = (parent / args.target).resolve()
        else:
            hits = _locate_in_parent(parent, snake)
            if len(hits) == 1:
                target = hits[0]
            elif not hits:
                print(f"{parent} 里没有名为 {snake} 的目录，这是新增算子。", file=sys.stderr)
                print("用 --target <母仓相对路径> 指定落点，路径见任务书"
                      "「PR 申请合入」那节的目标目录。", file=sys.stderr)
                return 4
            else:
                print(f"{parent} 里有多个 {snake} 目录，定不了落点：", file=sys.stderr)
                for hit in hits:
                    print(f"  - {hit.relative_to(parent)}", file=sys.stderr)
                print("用 --target <母仓相对路径> 指定一个。", file=sys.stderr)
                return 4
        # 落点与工程目录相同时**不能同步**：先 rmtree 再 copytree 会把源码删掉。
        need_sync = target.resolve() != op_dir.resolve()
        print(f"交付形态  裸算子目录 + 母仓 {parent}")
        print(f"落点      {target.relative_to(parent)}"
              f"（{'母仓里已有' if not args.target else '--target 指定'}）")

    record["target"] = str(target)
    if need_sync:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(op_dir, target)
        print(f"同步      {op_dir}\n       -> {target}")
    else:
        print("同步      跳过（算子已在构建根里）")

    # 2. 构建
    build_log = logs / "build.log"
    if args.skip_build:
        print("构建      跳过（--skip-build）")
        record["build"] = {"skipped": True}
    else:
        if not args.no_clean:
            # 母仓 build/ 是共享且有状态的：换一个 --ops 再构建时，
            # libcust_opapi.so 会停在上一个算子那次链接，obj 编了也不重链，
            # 装出来的包里只有上一个算子的符号。清掉才不会串。
            for stale in ("build", "build_out"):
                path = build_root / stale
                if path.is_dir():
                    shutil.rmtree(path)
            print("清理      已删 build/ 与 build_out/（换算子必须清，否则符号会串）")
        ops = [snake] + [x for x in args.extra_ops.split(",") if x.strip()]
        if kind == KIND_STANDALONE:
            # 自足工程的 build.sh 只认 --ops / --run / --soc / --pkg 四个，
            # 其余落 `*)` 分支打 Unknown option 并退 1（实测 ops-sparse）。
            # --experimental / --vendor_name / -j 都不能带。
            experimental = False
            command = ["bash", "build.sh", "--pkg", f"--soc={args.soc}",
                       f"--ops={','.join(ops)}"]
        else:
            # --experimental 是「编不编母仓根下 experimental/ 这个目录」的开关
            # （build.sh:144 -> CMakeLists.txt:124 add_subdirectory(experimental)），
            # 所以它由落点决定，不能写死：算子不在 experimental/ 下时加了就编不到。
            experimental = "experimental" in target.relative_to(build_root).parts
            command = ["bash", "build.sh", "--pkg"]
            if experimental:
                command.append("--experimental")
            command += [f"--soc={args.soc}", f"--ops={','.join(ops)}",
                        f"--vendor_name={vendor_name}", f"-j{args.jobs}"]
        record["experimental"] = experimental
        record["ops"] = ops
        print(f"构建      {' '.join(command)}")
        print(f"          日志 {build_log}，最长 {BUILD_TIMEOUT}s")
        try:
            code, elapsed = _run(command, build_root, BUILD_TIMEOUT, build_log)
        except subprocess.TimeoutExpired:
            print(f"\n构建超过 {BUILD_TIMEOUT}s 没结束。", file=sys.stderr)
            return 2
        record["build"] = {"command": " ".join(command), "returncode": code,
                           "elapsed_seconds": round(elapsed, 1), "log": str(build_log)}
        if code != 0:
            print(f"\n构建失败，退出码 {code}。", file=sys.stderr)
            for line in _blame_lines(build_log, target):
                print(line, file=sys.stderr)
            print("日志尾部：", file=sys.stderr)
            for line in _tail(build_log):
                print(f"  {line}", file=sys.stderr)
            return 2
        print(f"          成功，耗时 {elapsed:.0f}s")

    # 3. 装包
    #
    # 包名两种形态不同：opp-vendor 是 `cann-ops-<仓后缀>*.run`，
    # standalone-so 是 `cann-<SOC 段>-ops-<仓名>-<版本>_linux-<arch>.run`
    # （`cmake/package.cmake:74-85`，实测 ops-sparse）。宽 glob 兜住两种。
    if kind == KIND_STANDALONE:
        packages = sorted((build_root / "build_out").glob("cann-*.run"))
    else:
        packages = sorted((build_root / "build_out").glob(f"cann-ops-*{vendor_name}*.run"))
        if not packages:
            packages = sorted((build_root / "build_out").glob("cann-ops-*.run"))
    if not packages:
        print(f"{build_root}/build_out 下没有 .run 包。构建没产出，看 {build_log}。",
              file=sys.stderr)
        return 2
    package = max(packages, key=lambda p: p.stat().st_mtime)

    opp_root = Path(args.opp_root).resolve()
    if opp_root.exists():
        _force_rmtree(opp_root)
    opp_root.mkdir(parents=True)

    install_log = logs / "install.log"
    command = [str(package), "--quiet", f"--install-path={opp_root}"]
    if kind == KIND_STANDALONE:
        # **`--install` 必须给**：只给 `--install-path=` 时 makeself 包解压完就退，
        # 一个文件都不装，退出码还是 0（实测 ops-sparse）。
        command.insert(1, "--install")
    print(f"装包      {package.name} -> {opp_root}")
    try:
        code, _ = _run(command, build_root / "build_out", INSTALL_TIMEOUT, install_log)
    except subprocess.TimeoutExpired:
        print(f"\n装包超过 {INSTALL_TIMEOUT}s 没结束。", file=sys.stderr)
        return 2
    record["install"] = {"package": str(package), "returncode": code,
                         "opp_root": str(opp_root), "log": str(install_log)}
    if code != 0:
        print(f"\n装包失败，退出码 {code}。日志尾部：", file=sys.stderr)
        for line in _tail(install_log):
            print(f"  {line}", file=sys.stderr)
        return 2

    # 4. 验证符号可见
    if kind == KIND_STANDALONE:
        return _finish_standalone(args, record, op_dir, opp_root, snake, logs)

    libs = sorted(opp_root.glob("vendors/*/op_api/lib/libcust_opapi.so"))
    if not libs:
        print(f"\n{opp_root} 下没有 vendors/*/op_api/lib/libcust_opapi.so。"
              f"构建时 --ops={snake} 可能没匹配上算子目录名。", file=sys.stderr)
        return 3
    lib = libs[0]
    symbol = f"aclnn{args.op}GetWorkspaceSize"
    nm = subprocess.run(["nm", "-D", str(lib)], capture_output=True, text=True, check=False)
    if symbol not in nm.stdout:
        exported = sorted({m for m in re.findall(r"aclnn\w+GetWorkspaceSize", nm.stdout)})
        print(f"\n{lib} 里没有 {symbol}。", file=sys.stderr)
        print(f"包里实际导出的是：{'、'.join(exported) or '（一个都没有）'}", file=sys.stderr)
        owners = [o for o in _who_defines(build_root, symbol) if Path(o).name != snake]
        if owners:
            print(f"\n{symbol} 定义在母仓的 {'、'.join(owners)}，不在 {snake} 里。",
                  file=sys.stderr)
            extra = ",".join(sorted({Path(o).name for o in owners}))
            print(f"这个算子的 aclnn 入口不在自己目录，重跑时加 --extra-ops {extra}。",
                  file=sys.stderr)
        elif not owners:
            print(f"\n母仓里也没有定义 {symbol} 的地方，先确认 --op 的大小写"
                  f"与 aclnn 接口名一致，见 build-deploy.md「装完了但符号找不到」。",
                  file=sys.stderr)
        return 3

    vendor_dir = lib.parents[2]
    record["symbol"] = symbol
    record["custom_opp_lib"] = str(lib)
    record["vendor_dir"] = str(vendor_dir)

    # 5. 重写 env.sh 把三个环境变量补上
    # 扩展这一步与工程形态无关，只看给没给 --register-module：母仓里的算子
    # 一样可以只注册 torch 算子。两条轴各判各的。
    ext_dir, ext_err = _torch_extension(args.project, args.register_module)
    if ext_err:
        return 2
    record["torch_extension_dir"] = str(ext_dir) if ext_dir else ""

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe_env import write_env_sh  # noqa: E402 - 同目录量具，装完才有值可写

    write_env_sh("evidence/env.sh", os.environ.get("ASCEND_TOOLKIT_HOME", ""),
                 sys.executable, str(opp_root), pythonpath=str(ext_dir) if ext_dir else "")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(record), handle, ensure_ascii=False, indent=2)

    print(f"符号      {symbol} 可见（**符号可见 ≠ 装的是待验收实现**，核上面那行源码）")
    print(f"vendor    {vendor_dir}")
    print(f"env.sh    已重写，含 ASCEND_CUSTOM_OPP_PATH 与 ATK_CUSTOM_OPP_PATH")
    print(f"写入      {args.out}")
    print("\n下一条命令起都要先 source evidence/env.sh。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
