"""aclnn_driver — 容器内执行的驱动脚本（借参考仓 case_call.materialize_call 设计）。

职责边界（**只产原始输出、绝不判定**）：读 caseset + 各 case 的输入张量文件 → 取该 case **已解析好的**
``aclnn_call`` → 调 ``AclnnRunner.run(symbol, slots, signature=...)`` → 把每个输出落 ``out_k.bin``。
**判定/精度比对/pass-fail 唯一归 OpRunway 确定性脚本链**（validator / precision_policy，ADR 0007）
——本脚本一律不算 metrics、不下结论。

泛化（律令#0）：一切据 caseset 字段驱动，**绝无按算子名的分支**。
  · **调用变体由 spec 声明、gen_cases 逐 case 解析**（spec ``call_variants`` → case ``aclnn_call``）：
    driver **直接执行**该 case 的 ``aclnn_call``，**自己不推变体、不塞属性默认值、不猜哪个输出该产**
    （旧的 op 级 ``aclnn_call_template`` + `dim=None→0` 兜底已删——把全局 case 静默改成 dim=0 是**换了个算子**）。
    缺 ``aclnn_call`` → fail-closed（请用带 ``call_variants`` 的 spec 重跑 gen_cases）。
  · ``aclnn_call``：``{"symbol":"<S>", "slots":[{"role":"in","name":"self","input_idx":0},
    {"role":"attr","name":"dim","ctype":"int64","value":1}, {"role":"out","name":"values","output_idx":0},
    {"role":"out_null","name":"indices"}]}``——**每个 slot 必带 name**，用于与 aclnn 头签名逐项对账。
  · **签名强制**：driver 解析**已安装的** aclnn 头（op 工程 / vendor 的 ``op_api/aclnn_*.h`` 或
    ``op_api/include/aclnn_*.h``）建 :class:`AclnnSignature` 并传给 runner；取不到 → fail-closed
    （runner 已删 ``signature=None`` 兜底，错 arity/错序绝不进 native 调用）。头目录来源：``--op-dir``
    → env ``OPRUNWAY_ACLNN_OP_DIR`` → env ``ASCEND_CUSTOM_OPP_PATH``（冒号分隔的 vendor 内容根）。

输出契约（读 caseset ``expected``）：
  · 多输出（新契约 WI-A3）：``expected["outputs"]`` = 有序数组，每项含 ``out_shape`` +
    (``out_dtype`` 或 ``compare_dtype``) + 可选 ``role``；
  · 单输出（向后兼容旧 caseset）：无 ``outputs`` → 取 ``expected["out_shape"]`` +
    ``expected["compare_dtype"]`` 组成长度 1 的输出计划。
两条统一走「长度 N 输出计划」，driver 内不为单/多输出分叉语义；``aclnn_call`` 的 out-slot 用
``output_idx`` **显式**指向计划项（不再靠「第几个 out-slot」的位置隐含）。

输入文件读取：优先 caseset ``inputs[].path``。``.npy`` 直接 ``np.load``；``.bin``（repo_adapter
落的扁平 storage 字节）按 storage dtype + 声明 shape 读回。bf16 的 storage=uint16 位模式，
经 in-slot 的 ``dtype``（逻辑 dtype）传给 runner（runner 据此不做二次窄化、直接用位模式）。

**符号 provenance 进证据链**（D2 真机 Bug#A）：runner 逐符号记「实际调的实现来自哪个 ``.so``」，
本脚本把它原样写进 ``out_manifest.json`` 的 ``runtime`` 字段——用来证明**验的是被测 PR build 的
custom vendor 产物、不是 CANN 内置同名算子**（内置 ``libopapi.so`` 就导出 ``aclnnMedian`` 之类的
同名符号，旧版从全局取会静默打到内置 → **假 PASS**）。CLI **默认走严格档**
（``require_custom_vendor=True``，符号未被证明**由本次 DUT** 定义即 fail-closed）；确要跑
CANN 内置算子（无 PR 的基线场景）须显式 ``--allow-builtin-symbols``。
**严格档必须声明本次 DUT**（runner 改动⑪）：``--dut-lib <libcust_opapi.so 绝对路径>`` 或
``--dut-vendor-root <本次 install 的 vendor 内容根>``（DUT so = ``<root>/op_api/lib/libcust_opapi.so``）；
两个都给必须指同一文件（由 runner 校）。**两个都不给 → CLI 直接 fail-closed 并提示该加什么**——
不然「符号来自某个 custom vendor so」只证明得了它来自**环境里某个** custom so（``ASCEND_CUSTOM_OPP_PATH``
/ ``LD_LIBRARY_PATH`` 里继承来的**上次**安装产物同样满足），本次漏导符号时旧产物会代跑并报假 PASS。
⚠ 判读 ``runtime.symbols[*]`` 时**最载重的是 ``is_dut``**（定义方就是本次声明的 DUT so）；
``defining_lib_verified`` 只说明定义方属于「某个已加载 custom so」，**不等于本次被测物**。
``resolved_via``（dlsym 走的 handle）**不是**来源证据——dlsym 会沿 handle 的 DT_NEEDED 依赖树打到
CANN 内置 so；``global_conflict`` 也**不能单独**当 DUT 证据（两边可能都是内置实现）。
详见 ``aclnn_runner`` 模块文档改动⑩ / ⑪。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

from .base import AclnnRunnerError


def _safe_join(work_dir: str | Path, rel: str) -> Path:
    """把 caseset 里的相对路径钉在 work_dir 内，拒绝绝对路径 / .. 穿越（承 repo_adapter._safe）。"""
    base = Path(work_dir).resolve()
    if os.path.isabs(rel):
        raise AclnnRunnerError(f"path must be relative, got absolute: {rel!r}")
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise AclnnRunnerError(f"path escapes work_dir: {rel!r}")
    return target


def _storage_read_dtype(logical: str, declared_storage: str | None) -> np.dtype:
    """读盘 dtype：bf16 -> uint16（2 字节位模式）；余 = 逻辑 dtype。declared_storage 仅作交叉核。"""
    expected = "uint16" if logical == "bfloat16" else logical
    if declared_storage is not None and declared_storage != expected:
        raise AclnnRunnerError(
            f"自声明 storage_dtype={declared_storage!r} ≠ 据逻辑 dtype={logical!r} 反推 {expected!r}")
    try:
        return np.dtype(expected)
    except TypeError as exc:
        raise AclnnRunnerError(f"未知逻辑 dtype {logical!r}（读盘宽度定不出）——fail-closed") from exc


def _load_input(work_dir: str | Path, rec: dict) -> tuple[np.ndarray, str]:
    """读一个输入张量，返回 (array, 逻辑 dtype)。.npy 直载；.bin 按 storage dtype+shape 读回。"""
    path = rec["path"]
    logical = rec["dtype"]
    shape = list(rec.get("shape") or [])
    target = _safe_join(work_dir, path)
    if path.endswith(".npy"):
        arr = np.load(target)
    else:
        read_dt = _storage_read_dtype(logical, rec.get("storage_dtype"))
        arr = np.fromfile(target, dtype=read_dt)
        if shape:
            arr = arr.reshape(shape)
    return arr, logical


def _output_plan(case: dict) -> list[dict]:
    """从 case.expected 抽出统一的「输出计划」列表（多输出契约 / 单输出向后兼容，二者归一）。

    每项：``{"shape": [...], "dtype": "<logical>", "role": "value"|"index"|..., "index": k}``。
    """
    expected = case.get("expected") or {}
    outputs = expected.get("outputs")
    plan: list[dict] = []
    if isinstance(outputs, list) and outputs:
        for k, o in enumerate(outputs):
            shape = o.get("out_shape")
            if shape is None:
                raise AclnnRunnerError(f"{case.get('id')}: expected.outputs[{k}] 缺 out_shape")
            dtype = o.get("out_dtype") or o.get("compare_dtype")
            if dtype is None:
                raise AclnnRunnerError(f"{case.get('id')}: expected.outputs[{k}] 缺 out_dtype/compare_dtype")
            plan.append({"shape": list(shape), "dtype": str(dtype),
                         "role": o.get("role", "value"), "index": k})
        return plan
    # 向后兼容：单输出旧 caseset（无 outputs 数组）。
    shape = expected.get("out_shape")
    dtype = expected.get("compare_dtype")
    if shape is None or dtype is None:
        raise AclnnRunnerError(
            f"{case.get('id')}: 既无 expected.outputs[]，也无 out_shape/compare_dtype，无法定输出计划")
    plan.append({"shape": list(shape), "dtype": str(dtype), "role": "value", "index": 0})
    return plan


def _case_call(case: dict) -> dict:
    """取该 case **已解析好的** ``aclnn_call``；缺 / 畸形 → fail-closed（driver 不推变体）。"""
    call = case.get("aclnn_call")
    cid = case.get("id")
    if not isinstance(call, dict):
        raise AclnnRunnerError(
            f"{cid}: caseset 缺逐 case 的 aclnn_call——调用变体须由 spec 的 call_variants 声明、"
            f"gen_cases 逐 case 解析后写入（driver 绝不自己推变体、不塞默认值）。请用新 spec 重跑 gen_cases。")
    symbol = call.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise AclnnRunnerError(f"{cid}: aclnn_call 缺 symbol（aclnn<symbol>GetWorkspaceSize）")
    if not isinstance(call.get("slots"), list) or not call["slots"]:
        raise AclnnRunnerError(f"{cid}: aclnn_call.slots 须为非空数组")
    return call


def _idx(raw, n: int, cid: str, what: str) -> int:
    """把 slot 里的 ``input_idx`` / ``output_idx`` 校成合法下标（非整数 / 越界 → fail-closed）。"""
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise AclnnRunnerError(f"{cid}: {what} 须为整数下标，得 {raw!r}")
    if not 0 <= raw < n:
        raise AclnnRunnerError(f"{cid}: {what}={raw} 越界（本 case 共 {n} 项）")
    return raw


def _build_slots(call: dict, case: dict, work_dir: str | Path) -> list[dict]:
    """把该 case 的 ``aclnn_call.slots``（**已解析**）翻成 run() 吃的有序 slot 列表。

    - **in**：``input_idx`` **显式**指向 ``case.inputs[i]`` → ``{"kind":"in","name":...,"array":...,"dtype":...}``；
    - **attr**：``ctype`` + **已解析的** ``value``（缺 value / value=null → fail-closed，绝不塞默认）；
    - **out**：``output_idx`` **显式**指向本 case 输出计划项 → ``{"kind":"out",...,"index":k}``；
    - **out_null**：本 case 不产该输出（如全局 median 无 indices）→ 传 NULL、不回读。

    另做**账目自检**：输入 / 输出计划项各被**恰好一个** slot 消费（重复引用或漏引用 → fail-closed，
    防「解析出的调用与 case 数据对不上」悄悄产错文件）。slots 顺序 = aclnn 签名顺序（由 runner 与头签名逐项对账）。
    """
    cid = case.get("id")
    inputs = case.get("inputs") or []
    out_plan = _output_plan(case)
    slots: list[dict] = []
    used_in: list[int] = []
    used_out: list[int] = []
    for i, ts in enumerate(call["slots"]):
        if not isinstance(ts, dict):
            raise AclnnRunnerError(f"{cid}: aclnn_call.slots[{i}] 非对象: {ts!r}")
        role = ts.get("role")
        name = ts.get("name")
        if not name:
            raise AclnnRunnerError(
                f"{cid}: aclnn_call.slots[{i}] 缺 name——slots 必须带 name（与 header 签名逐项对账）")
        if role == "in":
            k = _idx(ts.get("input_idx"), len(inputs), cid, f"slots[{i}].input_idx")
            used_in.append(k)
            arr, logical = _load_input(work_dir, inputs[k])
            slots.append({"kind": "in", "name": name, "array": arr, "dtype": logical})
        elif role == "attr":
            ctype = ts.get("ctype")
            if not ctype:
                raise AclnnRunnerError(f"{cid}: 属性 slot {name!r} 缺 ctype")
            if "value" not in ts or ts["value"] is None:
                raise AclnnRunnerError(
                    f"{cid}: 属性 slot {name!r} 的 value 缺失/为 null——须由 spec 的 call_variants 解析成确定值"
                    f"（driver 绝不按 ctype 塞默认：把 dim=None 静默改成 0 等于换了个算子语义）")
            slot = {"kind": "attr", "name": name, "ctype": ctype, "value": ts["value"]}
            if ts.get("dtype") is not None:
                slot["dtype"] = ts["dtype"]              # aclScalar 的 host 缓冲 dtype
            slots.append(slot)
        elif role == "out":
            k = _idx(ts.get("output_idx"), len(out_plan), cid, f"slots[{i}].output_idx")
            used_out.append(k)
            p = out_plan[k]
            slots.append({"kind": "out", "name": name, "shape": p["shape"], "dtype": p["dtype"],
                          "role": p["role"], "index": p["index"]})
        elif role == "out_null":
            slots.append({"kind": "out_null", "name": name})
        else:
            raise AclnnRunnerError(f"{cid}: 未知 aclnn_call slot role: {role!r}")
    if sorted(used_in) != list(range(len(inputs))):
        raise AclnnRunnerError(
            f"{cid}: aclnn_call 的 in-slot 引用 {sorted(used_in)} ≠ 本 case 输入下标 "
            f"{list(range(len(inputs)))}（重复/漏引用）——fail-closed")
    if sorted(used_out) != list(range(len(out_plan))):
        raise AclnnRunnerError(
            f"{cid}: aclnn_call 的 out-slot 引用 {sorted(used_out)} ≠ 本 case 输出计划下标 "
            f"{list(range(len(out_plan)))}（重复/漏引用；不产的输出应写成 role=out_null）——fail-closed")
    return slots


# ── aclnn 头签名解析（强制：runner 不接受无签名调用）──────────────────────────────────

def _header_dirs(op_dir: str | Path | None) -> list[Path]:
    """aclnn 头的搜索目录（**按环境字段取，无私有默认、不按算子名猜**）。

    优先级：显式 ``op_dir`` → env ``OPRUNWAY_ACLNN_OP_DIR`` → env ``ASCEND_CUSTOM_OPP_PATH``
    （冒号分隔的**已安装** vendor 内容根，头在 ``<root>/op_api/include/aclnn_*.h``）。都没有 → 空列表 → 上层 fail-closed。
    """
    if op_dir:
        return [Path(op_dir)]
    env = (os.environ.get("OPRUNWAY_ACLNN_OP_DIR") or "").strip()
    if env:
        return [Path(env)]
    dirs: list[Path] = []
    for raw in (os.environ.get("ASCEND_CUSTOM_OPP_PATH") or "").split(":"):
        raw = raw.strip()
        if raw:
            dirs.append(Path(raw))
    return dirs


class _SignatureResolver:
    """按 symbol 解析 aclnn 头签名（带缓存）；取不到即 fail-closed。

    ``signatures`` 可由调用方直接注入（``{symbol: AclnnSignature}``，供离线单测与无 header 的调用方——
    仍受 runner 同一套逐项校验，**绝不按算子名特判**）。
    """

    def __init__(self, signatures: dict | None = None, op_dir: str | Path | None = None):
        self._cache = dict(signatures or {})
        self._dirs = _header_dirs(op_dir)

    def get(self, symbol: str):
        if symbol in self._cache:
            return self._cache[symbol]
        from .aclnn_runner import parse_aclnn_op         # 纯解析、无 CANN 依赖
        errors = []
        for d in self._dirs:
            try:
                sig = parse_aclnn_op(d, symbol=symbol)
            except AclnnRunnerError as exc:
                errors.append(f"{d}: {exc}")
                continue
            self._cache[symbol] = sig
            return sig
        # ⚠ 目标容器是 Python 3.11：f-string 的**表达式不得跨越隐式拼接的字面量**（PEP 701 是 3.12+）。
        # 这里先把提示文本算成普通变量，再单点插值——别把列表推导/or 表达式拆到两行 f-string 里。
        _dirs_hint = ([str(d) for d in self._dirs]
                      or "（空：请给 --op-dir，或设 OPRUNWAY_ACLNN_OP_DIR / ASCEND_CUSTOM_OPP_PATH）")
        raise AclnnRunnerError(
            f"取不到 aclnn{symbol}GetWorkspaceSize 的头签名——fail-closed（runner 必须拿可信签名才调 native）。"
            f"搜索目录 {_dirs_hint}；逐个失败原因: {errors}")


def _slot_digest(s: dict) -> str:
    """单个 slot 的**单行摘要**（role/name/shape/dtype，不含张量数据）——真机报错定位用。"""
    kind, name = s.get("kind"), s.get("name")
    if kind == "in":
        shape = list(getattr(s.get("array"), "shape", ()) or ())
        return f"in:{name}[{','.join(str(d) for d in shape)}]:{s.get('dtype')}"
    if kind == "attr":
        v = repr(s.get("value"))
        if len(v) > 64:
            v = v[:61] + "..."
        return f"attr:{name}:{s.get('ctype')}={v}"
    if kind == "out":
        shape = list(s.get("shape") or ())
        return (f"out#{s.get('index')}:{name}[{','.join(str(d) for d in shape)}]"
                f":{s.get('dtype')}({s.get('role')})")
    return f"{kind}:{name}"


def _slots_digest(slots) -> str:
    """slots 的紧凑摘要串（`in:self[2,3]:float32 | attr:dim:int64=1 | out#0:values[2]:float32(value)`）。

    真机 bug#11：runner 抛 `ACL 561103` 之类时，异常里若没有 case_id + 调用形状，60 条用例里根本
    定位不到是哪条、什么形状/dtype 触发的 —— 只能人肉逐条重放。摘要**只带元数据、不带数据**。
    """
    try:
        return " | ".join(_slot_digest(s) for s in slots)
    except Exception as exc:                     # 摘要本身绝不能盖掉原始故障
        return f"<slots 摘要生成失败: {exc}>"


def _signature_record(symbol: str, sig) -> dict:
    """本次实际采信的头签名摘要（**纯元数据、不判定**）——进 manifest 作证据。

    两项在报告里最载重、且在别处都看不见（runner 改动⑮）：
      · ``stage2_dispatch_form`` / ``stage2_call_arity``：执行段是标准 4 参，还是
        「框架三参 + stage1 实参原样重复 + stream」（extended，实测 ``aclnnGaussianBlur`` 10 参）；
      · 逐形参 ``direction_source``：``in``/``out`` 这个结论取自 stage2 的 const 限定符，
        还是 stage1 的 ``const`` 启发式。stage1 与 stage2 对 ``dst`` 写法不一致的 DUT 上，
        「dst 是输出、所以做了 D2H」全靠 stage2；不记来源就成了产物里一个凭空的 role（5.8）。
    ⚠ 这只是**工具从 header 读出来的方向**，不构成「dst 确实是被写的那块 buffer」的证明。
    """
    from .aclnn_runner import STAGE2_EXTENDED, STAGE2_STANDARD   # 纯常量，无 CANN 依赖
    form = getattr(sig, "stage2_form", None)
    params = list(getattr(sig, "params", None) or ())
    from_stage2 = form == STAGE2_EXTENDED
    # 不可派发的形态（header 无声明 / 调用方未声明）在收据里记 None：
    # 写成 STAGE2_STANDARD 等于替 runner 编一个它根本不会走的分支（run() 现在直接 fail-closed）。
    dispatch = form if form in (STAGE2_STANDARD, STAGE2_EXTENDED) else None
    return {
        "symbol": symbol,
        "stage2_form": form,
        "stage2_dispatch_form": dispatch,
        "stage2_call_arity": (3 + len(params) + 1 if from_stage2
                              else (4 if dispatch == STAGE2_STANDARD else None)),
        "params": [{
            "name": p.get("name"), "role": p.get("role"), "ctype": p.get("ctype"),
            "direction_source": (
                ("stage2_param_qualifier" if from_stage2 else "stage1_const_heuristic")
                if p.get("ctype") == "tensor" else "not_applicable"),
        } for p in params],
    }


def run_driver(caseset: dict, work_dir: str | Path, out_dir: str | Path, runner, *,
               signatures: dict | None = None, op_dir: str | Path | None = None) -> dict:
    """逐 case 跑 runner，落 out_k.bin，返回产物 manifest（**纯元数据、无判定**）。

    每个 case 用**它自己**已解析好的 ``aclnn_call``（:func:`_case_call` / :func:`_build_slots`），
    符号 = ``aclnn_call.symbol``，签名由 :class:`_SignatureResolver` 据已安装的头解析（或调用方注入）。
    ``runner`` 须实现 ``run(op_name, slots, *, signature) -> list[np.ndarray]``（真机为
    :class:`~.aclnn_runner.AclnnRunner`，测试可注入 mock）。runner 若还实现
    ``runtime_provenance() -> dict``（真机 runner 都实现），其返回值**原样整份**写进 manifest 的
    ``runtime`` 字段（``dut_lib`` / ``symbols[*].is_dut`` / ``ignored_custom_opapi_libs`` /
    ``device_owned`` / ``teardown`` … 一个都不裁），这是「验的是被测物」的证据链；未实现的
    mock/旧 runner → ``runtime=None``，不影响跑测。
    manifest 另记 ``signatures``（:func:`_signature_record`，逐 symbol 一条）：本次采信的
    stage2 派发形态 + native 实参个数 + 逐形参的 ``direction_source``——「为什么这个张量被当成
    输出、于是做了 D2H」在产物里得看得见（5.8），否则只剩一个凭空的 role。
    ⚠ 此刻 runner **还开着**，``teardown`` 恒 ``{"closed": false}``——close 之后的完整状态由
    :func:`refresh_manifest_runtime` 回写（CLI 路径已接）。
    """
    op = caseset["op"]
    out_root = Path(out_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    resolver = _SignatureResolver(signatures=signatures, op_dir=op_dir)
    produced = []
    symbols: list[str] = []
    sig_records: dict[str, dict] = {}
    for case in caseset["cases"]:
        cid = case["id"]
        call = _case_call(case)
        symbol = call["symbol"]
        if symbol not in symbols:
            symbols.append(symbol)
        slots = _build_slots(call, case, work_dir)
        sig = resolver.get(symbol)
        if symbol not in sig_records:
            sig_records[symbol] = _signature_record(symbol, sig)
        try:
            outs = runner.run(symbol, slots, signature=sig)
        except Exception as exc:                 # bug#11：原始异常不带 case 上下文 → 真机无从定位
            raise AclnnRunnerError(
                f"{cid}: 调用 aclnn{symbol} 失败：{type(exc).__name__}: {exc}"
                f"；slots=[{_slots_digest(slots)}]") from exc
        out_slots = [s for s in slots if s["kind"] == "out"]   # out_null 不产出 → 不计
        if len(outs) != len(out_slots):
            raise AclnnRunnerError(
                f"{cid}: runner 返回 {len(outs)} 输出 ≠ 非空 out-slot {len(out_slots)}（arity 不符）")
        case_out_dir = out_root / cid
        case_out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for s, arr in zip(out_slots, outs):
            arr = np.ascontiguousarray(arr)
            fname = f"out_{s['index']}.bin"
            (case_out_dir / fname).write_bytes(arr.tobytes())
            files.append({"index": s["index"], "role": s["role"],
                          "path": f"{cid}/{fname}", "shape": list(arr.shape),
                          "dtype": str(arr.dtype.name), "nbytes": int(arr.nbytes)})
        produced.append({"case_id": cid, "symbol": symbol, "outputs": files})
    # 符号 provenance（Bug#A 的证据）：runner 实现了就原样收进 manifest，没实现则记 None。
    prov_fn = getattr(runner, "runtime_provenance", None)
    runtime = prov_fn() if callable(prov_fn) else None
    manifest = {"op": op, "symbol": symbols[0] if len(symbols) == 1 else None,
                "symbols": symbols, "out_dir": str(out_root), "produced": produced,
                "signatures": [sig_records[s] for s in symbols if s in sig_records],
                "runtime": runtime}
    _write_manifest(manifest)
    return manifest


def _write_manifest(manifest: dict) -> None:
    """把 manifest 落到它自己记的 ``out_dir/out_manifest.json``（初次写与 provenance 回写共用）。"""
    Path(manifest["out_dir"], "out_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_manifest_runtime(manifest: dict, runner) -> dict:
    """runner **close 之后**重取 ``runtime_provenance()`` 覆写 ``manifest["runtime"]`` 并回写盘上文件。

    :func:`run_driver` 落 manifest 时 runner 还开着：那一刻 ``teardown`` 恒
    ``{"closed": false, "errors": []}``、``custom_opapi_libs`` 也还没转成 close 前的指纹快照——
    而「资源回收干没干净」「DUT 与被采信的 so 指纹是什么」都属证据链，得落全（不然报告里
    看到的 teardown 永远是「没关」，等于这一栏白记）。
    runner 未实现 ``runtime_provenance`` 的（mock / 旧 runner）→ 原样返回、不动盘上文件。
    """
    prov_fn = getattr(runner, "runtime_provenance", None)
    if not callable(prov_fn):
        return manifest
    manifest["runtime"] = prov_fn()
    _write_manifest(manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="aclnn_driver：容器内跑 caseset → 落 out_k.bin（只产原始输出，不判定）")
    parser.add_argument("caseset", help="caseset.json 路径")
    parser.add_argument("out_dir", help="输出目录（落 out_k.bin + out_manifest.json）")
    parser.add_argument("--work-dir", default=None,
                        help="输入张量文件的根目录（缺省 = caseset 的 work_dir 或其所在目录）")
    parser.add_argument("--op-dir", default=None,
                        help="aclnn 头所在的 op 工程 / 已安装 vendor 内容根（缺省读 env "
                             "OPRUNWAY_ACLNN_OP_DIR，再退 ASCEND_CUSTOM_OPP_PATH）；取不到签名即 fail-closed")
    parser.add_argument("--device", type=int, default=0, help="NPU device id（缺省 0/davinci0）")
    parser.add_argument("--allow-builtin-symbols", action="store_true",
                        help="允许目标 aclnn 符号来自 CANN 内置实现（默认**不允许**：验收路径走严格档，"
                             "符号必须由 custom vendor 的 libcust_opapi.so **定义**——dladdr 反查定义方，"
                             "不认 dlsym 沿依赖树打到的内置 so，否则等于验了内置算子=假 PASS）。"
                             "仅当本次确实要跑 CANN 内置算子（无 PR 的基线场景）才开。"
                             "性能采集侧的同款开关是 perf plan 的 allow_builtin_symbols")
    parser.add_argument("--dut-lib", default=None,
                        help="本次被测物（DUT）的 libcust_opapi.so **绝对路径**——严格档（默认）必给"
                             "（或改用 --dut-vendor-root）。严格档下只加载这一个 so，环境里探到的其它 "
                             "custom so 只作诊断记进 provenance 的 ignored_custom_opapi_libs")
    parser.add_argument("--dut-vendor-root", default=None,
                        help="本次 install 出的 vendor **内容根**（与 $ASCEND_CUSTOM_OPP_PATH 各段同口径），"
                             "DUT so = <root>/op_api/lib/libcust_opapi.so。与 --dut-lib 二选一；"
                             "两个都给必须指同一文件（由 runner 校，不一致即 fail-closed）")
    parser.add_argument("--hash-symbol-libs", action="store_true",
                        help="provenance 里附各 .so 的 sha256（大 so 要整读一遍；默认只记路径+size+mtime）")
    args = parser.parse_args(argv)

    # 严格档（默认）必须知道「本次该绑哪个 so」——这里先卡一道，给的是**CLI 语汇**的提示：
    # 不然调用方只会看到 runner 构造异常里的 dut_lib= 字样，不知道命令行该加什么参数。
    strict = not args.allow_builtin_symbols
    if strict and not (args.dut_lib or args.dut_vendor_root):
        raise AclnnRunnerError(
            "严格档（默认）必须声明本次 DUT，请加 --dut-lib <.../op_api/lib/libcust_opapi.so>，"
            "或 --dut-vendor-root <本次 install 出的 vendor 内容根>"
            "（DUT so = <root>/op_api/lib/libcust_opapi.so）。"
            "没有它，「符号来自某个 custom vendor so」证明不了它来自**本次 PR build 出来的**那个"
            "——ASCEND_CUSTOM_OPP_PATH / ASCEND_OPP_PATH / LD_LIBRARY_PATH 里继承来的**上次**"
            "安装产物同样满足，本次漏导符号时旧产物会代跑并报假 PASS。"
            "确要跑 CANN 内置算子（无 PR 的基线场景）才加 --allow-builtin-symbols。")

    caseset = json.loads(Path(args.caseset).read_text(encoding="utf-8"))
    work_dir = args.work_dir or caseset.get("work_dir") or str(Path(args.caseset).resolve().parent)

    # 真机才 import ctypes 执行体（离线单测注入 mock，不经此路）。
    from .aclnn_runner import AclnnRunner

    # 验收路径默认严格档：符号必须由**本次 DUT** 定义（--allow-builtin-symbols 显式放行内置算子）。
    # DUT 的两种写法原样透传给 runner——「二选一 / 都给须一致」的判据只此一处（runner 的
    # _resolve_dut_lib），CLI 不复制一份自己的解释。
    runner = AclnnRunner(device=args.device, require_custom_vendor=strict,
                         dut_lib=args.dut_lib, dut_vendor_root=args.dut_vendor_root,
                         hash_symbol_libs=args.hash_symbol_libs)
    manifest = None
    try:
        with runner:
            manifest = run_driver(caseset, work_dir, args.out_dir, runner, op_dir=args.op_dir)
    finally:
        # close 之后再把 provenance 落全（teardown / 指纹快照此刻才完整）。close 自己报错时
        # 也照落——那份「没回收干净」的记录正是要留的证据，异常仍照抛不吞。
        if manifest is not None:
            manifest = refresh_manifest_runtime(manifest, runner)
    runtime = manifest.get("runtime") or {}
    print(json.dumps({"op": manifest["op"], "cases": len(manifest["produced"]),
                      "out_dir": manifest["out_dir"],
                      "strict_custom_vendor": runtime.get("strict_custom_vendor"),
                      "dut_lib": (runtime.get("dut_lib") or {}).get("path"),
                      # 载重字段：符号的定义方**是不是本次 DUT**（不是「属于某个 custom so」）。
                      "symbol_is_dut": {s.get("symbol"): s.get("is_dut")
                                        for s in runtime.get("symbols", [])},
                      "symbol_sources": sorted({str(s.get("source"))
                                                for s in runtime.get("symbols", [])})},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
