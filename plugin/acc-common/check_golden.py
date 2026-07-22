#!/usr/bin/env python3
"""`<ops_root>/<op>/golden.py` 的**来源契约**自检（确定性，无 NL 判断）。

`acc-runner-dev:gen_golden` 产完 golden.py 后跑这个。它把 `precision_policy` 的三层
（词表校验 → 授权核实 → 档位派生）串起来跑一遍，输出一份 JSON 账本。

**为什么是脚本不是 `python -c`**：这段自检要被 agent 反复照抄执行，抄错一个字就成了假绿。
三层分立的语义（`validate_golden_contract` 只看词表 / `verify_authorization` 只读快照 /
`derive_golden_tier` 只按词表判档，谁也不核自己）也必须原样保住——揉成一坨就退化成自证。

用法:
    python3 check_golden.py <Op>              # 契约层
    python3 check_golden.py <Op> --load       # 额外真跑 gen_cases.load_golden

⚠ **两种模式都会 import 执行整个 `golden.py`**（不执行就拿不到 `GOLDEN_CONTRACT`），
   它的**所有顶层依赖都会被 import**。照手册 §3 骨架把 torch 延迟到 `_require_torch()` 里，
   则不带 `--load` 时不会拉 torch；`--load` 另外 import `gen_cases` 并再走一遍引擎加载。

退出码（**编排按这三态路由，别改语义**）:
    0  可往下走：contract_ok、无 blocked_reason、无需人核
    2  需人核（`needs_human_review`）——**非失败**
    1  其余一律（词表不合规 / blocked / 缺件 / 账本自相矛盾 / 参数错误 / --load 失败）

⚠ **2 是「需人核」而不是「tier 3」**（2026-07-23 审计更正）：`derive_golden_tier` 里
   `multistep + oracle_method` 判的是 **(tier 1, needs_human_review=True)**——按 tier 路由会把
   这种「档位高但仍要人核」的 golden 静默放行。路由键是 `needs_human_review`，不是档位数字。

⚠ 诚实边界（两条，别当成没有）：
   ① 全绿 ≠ golden 数值对。本脚本只证「来源可不可信、够不够格往下跑」，
      数值只有 CP-D 真机跑测才验得到（同 `precision_policy` 开头那条边界）。
   ② **golden.py 与本脚本同进程执行**（ADR 0011 决策 6：它与 runner.cpp 同信任级）。
      已挡住的：`SystemExit` / 任意异常（一律账本化成 exit 1）、三层策略函数在任何 golden 执行前
      就被固化引用（见 `_V/_A/_D`，防属性改绑）。**挡不住的**：`os._exit(0)`、C 层退出、
      解释器状态篡改——真要挡须换子进程隔离。当前不做，理由是 runner.cpp 本身就要被编译并在
      NPU 上跑，只给 golden 加沙箱是不对称的安全戏；如实记在这，别当作已隔离。
"""
import argparse
import importlib.util
import json
import os
import sys

import precision_policy
import repo_adapter

# ⚠ **在任何 golden.py 执行之前**把三层策略函数固化成本模块的引用。
# golden.py 与本脚本同进程，`import precision_policy` 拿到的是同一个模块对象——它完全可以
# `precision_policy.derive_golden_tier = lambda *a: (1, False, None)` 把自己判成绿的。
# 先固化引用就挡住了这一类「被检查者改写检查器」的改绑（挡不住 `os._exit`，见 docstring 边界②）。
_V = precision_policy.validate_golden_contract
_A = precision_policy.verify_authorization
_D = precision_policy.derive_golden_tier

_REQUIRED = ("golden_fn", "GOLDEN_SOURCE", "GOLDEN_PROVENANCE")


def _load_module(op):
    """import 执行 <ops_root>/<op>/golden.py（路径把关复用 op_dir 的软链/逃逸拒绝）。

    ⚠ 守卫（目录段拒软链 / 最终文件 `islink`+`isfile`）在 `exec_module` **之前**跑，但检查与打开
    **不是原子的**——两者之间被 rename 成软链的 TOCTOU 窗口存在。同 docstring 边界②：
    golden.py 属同信任级代码，不为它单建 `O_NOFOLLOW` 通路。"""
    path = os.path.join(repo_adapter.op_dir(op), "golden.py")
    if not os.path.isfile(path):
        raise ValueError(
            f"golden.py 不存在: {path!r}\n"
            f"  → 先由 acc-runner-dev:gen_golden 据任务书产出"
            f"（手册 skills/acc-runner/references/golden-authoring.md）")
    if os.path.islink(path):
        raise ValueError(f"golden.py 是符号链接，拒绝（防换靶）: {path!r}")
    spec = importlib.util.spec_from_file_location(f"_golden_{op.lower()}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except BaseException as ex:                  # noqa: BLE001 —— **必须比 Exception 宽**
        # `SystemExit` 不是 `Exception` 的子类：golden.py 里一句 `sys.exit(0)` 会穿过 `except Exception`、
        # 直达解释器，让**一个连 GOLDEN_CONTRACT 都没有的 golden 以退出码 0 假绿收场**（实测复现）。
        # 这是本脚本最不能出的错，故这里捕 BaseException 并统一账本化。
        if isinstance(ex, KeyboardInterrupt):    # 用户主动中断不该被伪装成 golden 的问题
            raise
        raise ValueError(f"golden.py 执行失败({type(ex).__name__}): {path}: {ex}") from ex
    return mod, path


def _check_exports(mod):
    """镜像 `gen_cases.load_golden` 的**真实**约束，不是只看 `hasattr`。

    只查存在性会放过 `golden_fn = None` / `GOLDEN_SOURCE = ""` / `out_shape = "abc"` 这类件：
    本脚本给 exit 0，`load_golden` 到了 CP-D 才 fail-closed——正是「自检说没事、真跑才炸」。
    返回 (exports:dict, error:str|None)。"""
    ex = {k: hasattr(mod, k) for k in _REQUIRED}
    ex["out_shape"] = callable(getattr(mod, "out_shape", None))
    missing = [k for k in _REQUIRED if not ex[k]]
    if missing:
        return ex, f"golden.py 缺必需导出 {missing}（gen_cases.load_golden 会 fail-closed）"
    if not callable(mod.golden_fn):
        return ex, f"golden_fn 不可调用（得 {type(mod.golden_fn).__name__}）"
    for k in ("GOLDEN_SOURCE", "GOLDEN_PROVENANCE"):
        v = getattr(mod, k)
        if not (isinstance(v, str) and v.strip()):
            return ex, f"{k} 须非空字符串，得 {type(v).__name__}={v!r}"
    osh = getattr(mod, "out_shape", None)
    if osh is not None and not callable(osh):
        return ex, f"out_shape 导出了但不可调用（得 {type(osh).__name__}）"
    return ex, None


def check(op, do_load=False):
    """返回账本 dict。**不抛**——异常一律转成账本里的 error 字段，好让调用方拿到完整上下文。

    ⚠ 「不抛」是硬承诺：每一层都各自兜住，且兜的是 `Exception` 全域（不只 `ValueError`）。
    只兜 `ValueError` 时，一个不可读的快照（`PermissionError`）就会把栈丢给调用方、账本全丢。"""
    out = {"op": op, "contract_ok": False, "tier": None, "needs_human_review": None,
           "blocked_reason": None, "authorized": None, "authorization_reason": None,
           "exports": {}, "golden_path": None, "taskdoc_snapshot": None, "error": None}
    try:
        mod, path = _load_module(op)
    except Exception as ex:                      # noqa: BLE001 — 账本化，见 docstring
        out["error"] = f"[加载] {ex}"
        return out
    out["golden_path"] = path

    # ── 必需导出（与 gen_cases.load_golden 同一套约束，早报早修）────────────────
    try:
        out["exports"], err = _check_exports(mod)
    except Exception as ex:                      # noqa: BLE001 — 模块 __getattr__ 也可能抛
        out["error"] = f"[导出] {type(ex).__name__}: {ex}"
        return out
    if err:
        out["error"] = f"[导出] {err}"
        return out

    try:
        contract = getattr(mod, "GOLDEN_CONTRACT", None)
    except Exception as ex:                      # noqa: BLE001
        out["error"] = f"[契约块] 取 GOLDEN_CONTRACT 失败 {type(ex).__name__}: {ex}"
        return out
    if contract is None:
        out["error"] = ("[契约块] golden.py 无 GOLDEN_CONTRACT——加载不阻塞，但**派生不出档位**，"
                        "正式验收一律要写（手册 §3）")
        return out

    # ── 第一层：受控词表 + 结构（不核真伪、不判档）────────────────────────────
    try:
        _V(contract)
        out["contract_ok"] = True
    except ValueError as ex:
        out["error"] = f"[词表] 不合规: {ex}"
        return out
    except Exception as ex:                      # noqa: BLE001
        out["error"] = f"[词表] 校验异常 {type(ex).__name__}: {ex}"
        return out

    # ── 第二层：授权真伪（读快照逐字核，独立于判档）──────────────────────────
    try:
        snap = repo_adapter.taskdoc_snapshot_path(op)
        out["taskdoc_snapshot"] = snap
        # 快照最终文件那一层单独拒软链：`op_dir` 只逐段查**目录**，挡不住
        # `task_doc.snapshot.md` 本身是指向 ops_root 之外的软链（引文锚就核到别处去了）。
        if os.path.islink(snap):
            out["error"] = f"[授权] 任务书快照是符号链接，拒绝（防换锚）: {snap!r}"
            return out
        ok, why = _A(contract, snap)
    except Exception as ex:                      # noqa: BLE001
        out["error"] = f"[授权] 核实异常 {type(ex).__name__}: {ex}"
        return out
    out["authorized"], out["authorization_reason"] = bool(ok), why

    # ── 第三层：判档（只吃词表 + 上一层的布尔结论）──────────────────────────
    try:
        tier, need_human, blocked = _D(contract, ok)
    except Exception as ex:                      # noqa: BLE001
        out["error"] = f"[判档] 派生异常 {type(ex).__name__}: {ex}"
        return out
    out["tier"], out["needs_human_review"], out["blocked_reason"] = tier, bool(need_human), blocked

    if do_load:
        # 引擎真加载一遍（会 import torch 等重依赖）。本机缺 torch 时这里红属正常，如实记。
        try:
            import gen_cases
            loaded = gen_cases.load_golden(op)
            # 按名取用：`load_golden` 返回具名元组 `Golden(fn, source, provenance, out_shape, contract)`。
            # 按名访问既清楚、又不与元组 arity 耦合（下标写法一旦字段重排就指错，且不会报错）。
            out["engine_load"] = {"ok": True, "fields": list(loaded._fields),
                                  "has_out_shape": loaded.out_shape is not None}
        except BaseException as ex:              # noqa: BLE001 — 同 _load_module：SystemExit 也要兜
            if isinstance(ex, KeyboardInterrupt):
                raise
            out["engine_load"] = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    return out


def _exit_code(led):
    """账本 → 退出码。**fail-closed 的状态机**：只有明确合法的组合才给 0，其余一律 1。

    ⚠ 三条曾经踩过的坑，都钉在这里了：
      ① 路由键是 `needs_human_review`、**不是 tier**（`multistep + oracle_method` = tier 1 但要人核）；
      ② `blocked_reason` 非空一律 1，**不管 tier 说什么**——账本自相矛盾时按坏的算；
      ③ 认不出的状态（tier 为 None / 4 / 越界）落 1，不落 0。
    """
    if led.get("error") or led.get("contract_ok") is not True:
        return 1
    eng = led.get("engine_load")
    if eng and not eng.get("ok"):
        return 1
    if led.get("blocked_reason"):                 # 矛盾账本按坏的算（tier 1 + blocked 也是 1）
        return 1
    if led.get("authorized") is False:            # 授权核不实却没落 blocked → 账本不自洽
        return 1
    if led.get("tier") not in (1, 2, 3):          # None / 4 / 未知 → fail-closed
        return 1
    if led.get("needs_human_review"):
        return 2                                  # 要人核，不是失败
    return 0


class _Parser(argparse.ArgumentParser):
    """参数错误退 **1** 而不是 argparse 默认的 2。

    默认的 2 与本脚本「2 = 需人核、可继续」撞车：少打一个 `<Op>` 会被编排读成
    「golden 没问题、只是要人核」——一个从没被检查过的算子就这么放行了。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: 参数错误: {message}\n")
        raise SystemExit(1)


def main(argv=None):
    ap = _Parser(description="golden.py 来源契约自检")
    ap.add_argument("op", help="算子名（目录名，如 IsClose）")
    ap.add_argument("--load", action="store_true",
                    help="额外真跑 gen_cases.load_golden（会 import torch）")
    a = ap.parse_args(argv)
    ledger = check(a.op, do_load=a.load)
    print(json.dumps(ledger, ensure_ascii=False, indent=2))
    return _exit_code(ledger)


if __name__ == "__main__":
    sys.exit(main())
