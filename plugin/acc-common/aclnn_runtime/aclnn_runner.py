"""ctypes-ACL 单算子运行器（adapt 参考仓 cannbot-ops-input adapters/aclnn_runner.py）。

一个 registered aclnn 算子走 compile -> install -> run：``build.sh`` 把 ``custom_opp_*.run``
装进 ``$ASCEND_OPP_PATH/vendors/<vendor>/``（暴露 ``libcust_opapi.so`` + ``aclnn_<op>.h``）；
本模块做 **run** 步——经 ACL 单算子 C API（ctypes）调 ``aclnn<Op>GetWorkspaceSize`` /
``aclnn<Op>`` 两段式，**无 per-op runner 源、无 in-tree torch build**。

相对参考仓的改动（蓝图 §4.3/4.4 + D1 真机发现）：
  1. ``run(op_name, slots)`` 走**有序 slots**：输入张量 / 穿插的标量属性（int64/bool/float/aclScalar）/
     输出张量 / out_null 按 slots 真实顺序建实参 + 拼 ``argtypes``——**不再假设「张量全在前、attr 不存在」**
     （median ``(self, dim, keepDim, values, indices)`` 段错误的根因：dim/keepDim 夹在 self 与 values 之间）。
     支持多输出（逐个建 out tensor、逐个 D2H）与 out_null（传 NULL、不回读，如全局 median 无 indices）。
  2. ``parse_aclnn_signature`` 扩为解析**完整有序形参表**（tensor-in / tensor-out / scalar-attr，据 C 类型
     通用分类，绝不按算子名）；``AclnnSignature`` 承载有序混合表，供 run() **交叉校验** slots 与签名一致。
     ⚠ 校验现为**强制**（audit#1）：``run(..., signature=...)`` 必传，逐项对 ``(name, role, ctype)`` +
     算子名 + 参数总数；无 header 的调用方须自行构造 ``AclnnSignature``（仍受同一套校验，绝不按算子名特判）。
  3. **bf16 窄化**：numpy 无 bf16 → host 侧真位截断（round-half-to-even）；输出 bf16 D2H 后按
     2 字节解释再转 fp32。
  4. ``_find_custom_opapi_libs`` 从 ``$ASCEND_OPP_PATH/vendors/*/op_api/lib/libcust_opapi.so`` glob，
     **custom vendor lib 可选**（Bug#1：无 custom lib 返回 ``[]`` 不 raise，内置 aclnn 算子照跑）。
  5. argtypes 全声明（防 ctypes 默认 c_int 截断 64-bit 指针）；标量属性 ``float``/``double`` **分开**
     marshal（c_float / c_double，audit#5）。
  6. 资源全程 **try/finally** 回收（tensor / aclScalar / device 缓冲 / workspace），``_make_tensor``
     自身在 H2D 或建 tensor 失败时就地释放本地 dev；清理异常绝不覆盖原始异常（audit#3）。
  7. 分配前定死**规范 storage dtype**（非 bf16 物理 dtype 必须 == 声明逻辑 dtype、bf16 必须 uint16/f32），
     并用**带溢出检查的 numel×itemsize** 独立算字节数、与缓冲实际 nbytes 核对（audit#2，防欠分配越界）。

纯 helper（``parse_aclnn_op`` / ``contiguous_strides`` / ``_acl_dtype`` / bf16 位转换 /
``_find_custom_opapi_libs``）**无 CANN 依赖、可离线单测**；``AclnnRunner`` 的 ctypes 执行路径需 NPU。
"""

from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .acl_consts import (
    ACL_FORMAT_ND,
    MALLOC_HUGE_FIRST,
    MEMCPY_D2H,
    MEMCPY_H2D,
    REPEAT_INITIALIZE,
    acl_dtype,
)
from .base import AclnnRunnerError


# ── header 解析（op 名 + 输入/输出 aclTensor 顺序，改动②）─────────────────────────


@dataclass
class AclnnSignature:
    """``aclnn<Op>GetWorkspaceSize`` 签名里解析出的算子名 + **完整有序混合形参表**。

    ``params`` 按**签名出现顺序**排列（末两个恒定形参 ``uint64_t *workspaceSize`` /
    ``aclOpExecutor **executor`` 不计入——它们是两段式框架参、非算子实参，且由
    :func:`parse_aclnn_signature` **显式校验类型/位置/唯一性**后才剔除），每项据 C 类型限定符
    **通用分类**（据类型，绝不按算子名），三类：
      · ``const aclTensor *`` → ``{"role":"in", "ctype":"tensor", "const":True}``（输入张量）；
      · 非 const ``aclTensor *`` → ``{"role":"out","ctype":"tensor","const":False}``（输出张量，``*Out`` 形参）；
      · ``int64_t`` / ``float`` / ``double`` / ``bool`` / ``aclScalar *`` →
        ``{"role":"attr","ctype":"int64"|"float32"|"float64"|"bool"|"scalar"}``（标量属性，**穿插**在张量之间）。
        ⚠ ``float`` 与 ``double`` **分开**（audit#5）：C ABI 上二者位宽不同，合并会按错误位宽传值。
    穿插的标量属性正是 median ``(self, int64 dim, bool keepDim, valuesOut, indicesOut)`` 需要承载的
    ——旧版只抓 aclTensor、把 dim/keepDim 连同其位置一起丢了，导致 run() 无处安放、段错误。

    用途 = **强制交叉校验**：``run()`` **必须**拿到本签名（audit#1：signature=None 兜底已删），据此校
    调用 slots 的 ``op_name`` / 参数总数 / 逐项 ``(name, role, ctype)`` 与签名一致，任一不符 fail-closed。
    无 header 的调用方（如内置 aclnn 算子）须**显式构造** :class:`AclnnSignature` 传入——仍受同一套校验，
    **绝不按算子名特判**。
    """

    op_name: str
    params: list[dict]

    @property
    def tensor_params(self) -> list[dict]:
        """向后兼容视图：只返回 aclTensor 形参，键用旧 ``io``（=role）。"""
        return [{"name": p["name"], "io": p["role"], "const": p.get("const", p["role"] == "in")}
                for p in self.params if p["ctype"] == "tensor"]

    @property
    def attr_params(self) -> list[dict]:
        return [p for p in self.params if p["role"] == "attr"]

    @property
    def input_names(self) -> list[str]:
        return [p["name"] for p in self.params if p["role"] == "in"]

    @property
    def output_names(self) -> list[str]:
        return [p["name"] for p in self.params if p["role"] == "out"]

    @property
    def num_inputs(self) -> int:
        return sum(1 for p in self.params if p["role"] == "in")

    @property
    def num_outputs(self) -> int:
        return sum(1 for p in self.params if p["role"] == "out")

    @property
    def tensor_count(self) -> int:
        return sum(1 for p in self.params if p["ctype"] == "tensor")


def _iter_aclnn_headers(op_dir: Path) -> list[Path]:
    """列出 op 工程（或**已安装** vendor 内容根）下的对外 aclnn 头（``*_impl.h`` 除外）。

    两种布局都认（据**目录形态**，非算子名）：源码工程 ``<op_dir>/op_api/aclnn_*.h``、
    install 后的 vendor ``<vendor_content_root>/op_api/include/aclnn_*.h``；另兜 ``<op_dir>/aclnn_*.h``。
    """
    seen, out = set(), []
    for pat in ("op_api/aclnn_*.h", "op_api/include/aclnn_*.h", "include/aclnn_*.h", "aclnn_*.h"):
        for h in sorted(op_dir.glob(pat)):
            if h.name.endswith("_impl.h") or str(h) in seen:
                continue
            seen.add(str(h))
            out.append(h)
    return out


def parse_aclnn_op(op_dir: str | Path, symbol: str | None = None) -> AclnnSignature:
    """从 ``op_api/aclnn_*.h``（或已安装 vendor 的 ``op_api/include/``）解析算子签名。

    ``op_name`` 为 CamelCase 基名（workspace 符号即 ``aclnn<op_name>GetWorkspaceSize``）。
    输入/输出 aclTensor + 穿插标量属性各自个数、顺序、类型据形参 ``const`` 限定符 / C 类型分类
    （通用，绝不按算子名）。

    ``symbol`` 非空 → 只认 ``op_name == symbol`` 的那份头（**据数据里的符号选**，不是按算子名分支）；
    找不到 fail-closed。``symbol`` 为空且目录里有多份可解析的头 → **fail-closed**（旧版静默取第一份，
    多算子目录下会拿错签名）。
    """
    op_dir = Path(op_dir).resolve()
    headers = _iter_aclnn_headers(op_dir)
    if not headers:
        raise AclnnRunnerError(f"no aclnn header found under {op_dir}")
    parsed: list[tuple[Path, AclnnSignature]] = []
    errors: list[str] = []
    for h in headers:
        try:
            parsed.append((h, parse_aclnn_signature(h.read_text(encoding="utf-8", errors="ignore"))))
        except AclnnRunnerError as exc:            # 同目录别的头解析不了不该拖垮本次查找
            errors.append(f"{h.name}: {exc}")
    if symbol is not None:
        hits = [s for _, s in parsed if s.op_name == symbol]
        if len(hits) != 1:
            raise AclnnRunnerError(
                f"在 {op_dir} 下找 aclnn{symbol}GetWorkspaceSize 的头：命中 {len(hits)} 份"
                f"（候选 {[s.op_name for _, s in parsed]}；解析失败 {errors}）——fail-closed")
        return hits[0]
    if not parsed:
        raise AclnnRunnerError(f"{op_dir} 下的 aclnn 头都解析不出两段式签名：{errors}")
    if len(parsed) > 1:
        raise AclnnRunnerError(
            f"{op_dir} 下有多份 aclnn 头 {[p.name for p, _ in parsed]}——须显式给 symbol 选定，fail-closed")
    return parsed[0][1]


# 两段式签名**末两个**恒定框架参：``uint64_t *workspaceSize`` + ``aclOpExecutor **executor``。
# audit#7：显式按类型校验（位置 + 唯一性），不再靠「名字叫 workspaceSize 就丢弃」的宽松判据。
_WS_PARAM_RE = re.compile(r"^(?:const\s+)?uint64_t\s*\*\s*\w+$")
_EXEC_PARAM_RE = re.compile(r"^(?:const\s+)?aclOpExecutor\s*\*\s*\*\s*\w+$")


def _classify_param(raw: str) -> dict:
    """把一个**算子实参** token 分类成有序形参表项（框架参已由调用方剔除，此处出现即报错）。

    据 C 类型**通用**分类（绝不按算子名）：aclTensor→张量 in/out；int64_t/bool/float/double/aclScalar*
    →标量属性；aclTensorList→域外形态 fail-closed；其它未知类型 / 裸指针 → fail-closed（域内签名不应出现）。
    """
    tok = " ".join(raw.split())
    if not tok:
        raise AclnnRunnerError("aclnn 签名里出现空形参（多余逗号？）——fail-closed")
    name_m = re.search(r"(\w+)\s*$", tok)
    name = name_m.group(1) if name_m else ""
    if _WS_PARAM_RE.match(tok) or _EXEC_PARAM_RE.match(tok) or "aclOpExecutor" in tok:
        raise AclnnRunnerError(
            f"两段式框架参（workspaceSize / aclOpExecutor）只应出现在形参表末两位且各一次，得 {tok!r}——fail-closed")
    if "aclTensorList" in tok:
        raise AclnnRunnerError(
            f"aclTensorList 属域外接口形态（本 runner 只支持标准两段式的 aclTensor），fail-closed: {tok!r}")
    if "aclScalar" in tok:
        return {"name": name, "role": "attr", "ctype": "scalar"}
    if "aclTensor" in tok:
        is_const = bool(re.search(r"\bconst\b", tok))
        return {"name": name, "role": "in" if is_const else "out",
                "ctype": "tensor", "const": is_const}
    if "*" in tok or "[" in tok:                  # 裸指针 / 数组形参：域内两段式不应出现 → 别猜
        raise AclnnRunnerError(
            f"aclnn 形参 {tok!r} 是指针/数组形态（非 aclTensor/aclScalar）——域外接口能力，fail-closed")
    if re.search(r"\bint64_t\b", tok):
        return {"name": name, "role": "attr", "ctype": "int64"}
    if re.search(r"\bbool\b", tok):
        return {"name": name, "role": "attr", "ctype": "bool"}
    # audit#5：float / double 位宽不同，**分开**记（marshal 时各走 c_float / c_double）。
    if re.search(r"\bdouble\b", tok):
        return {"name": name, "role": "attr", "ctype": "float64"}
    if re.search(r"\bfloat\b", tok):
        return {"name": name, "role": "attr", "ctype": "float32"}
    raise AclnnRunnerError(
        f"无法分类的 aclnn 形参（域内签名应仅含 aclTensor / int64_t / bool / float / double / aclScalar）: {tok!r}")


def parse_aclnn_signature(text: str) -> AclnnSignature:
    """从头文件文本解析 ``aclnn<Op>GetWorkspaceSize`` 的**完整有序形参表**（抽出便于离线单测）。

    audit#7 加固：**找不到右括号立即 raise**（不再拿文件末尾当形参表，截断头会伪装成有效签名）；
    末两个形参**必须**依次是 ``uint64_t *<name>`` + ``aclOpExecutor **<name>`` 且**全表唯一**，
    校验通过后才从算子形参表剔除。
    """
    match = re.search(r"aclnn(\w+)GetWorkspaceSize\s*\(", text)
    if not match:
        raise AclnnRunnerError("cannot find aclnn<Op>GetWorkspaceSize signature")
    op_name = match.group(1)
    # 取 ``(`` 到匹配 ``)`` 之间的形参列表。aclnn 两段式签名无嵌套括号（形参类型形如
    # ``aclOpExecutor **executor`` 不含括号），故取首个 ``)`` 即整段形参；缺 ``)`` = 头被截断 → fail-closed。
    close = text.find(")", match.end())
    if close == -1:
        raise AclnnRunnerError(
            f"aclnn{op_name}GetWorkspaceSize 的形参表没有闭合右括号（头文件被截断？）——fail-closed")
    raw_params = [" ".join(t.split()) for t in text[match.end():close].split(",")]
    if len(raw_params) < 2:
        raise AclnnRunnerError(
            f"aclnn{op_name}GetWorkspaceSize 形参不足两个——两段式签名末两位必须是 "
            f"uint64_t *workspaceSize + aclOpExecutor **executor，得 {raw_params!r}")
    if not _WS_PARAM_RE.match(raw_params[-2]) or not _EXEC_PARAM_RE.match(raw_params[-1]):
        raise AclnnRunnerError(
            f"aclnn{op_name}GetWorkspaceSize 末两个形参须依次为 `uint64_t *workspaceSize` + "
            f"`aclOpExecutor **executor`，得 {raw_params[-2]!r} / {raw_params[-1]!r}——fail-closed")
    # 逐个分类剩下的算子实参；框架参在别处再次出现（不唯一）由 _classify_param 拦下。
    params = [_classify_param(raw) for raw in raw_params[:-2]]
    return AclnnSignature(op_name=op_name, params=params)


# ── 纯 helper ────────────────────────────────────────────────────────────────


def contiguous_strides(shape: list[int]) -> list[int]:
    strides = [1] * len(shape)
    for i in range(len(shape) - 2, -1, -1):
        strides[i] = strides[i + 1] * shape[i + 1]
    return strides


def _acl_dtype(name: str) -> int:
    """委托 acl_consts（单一真源）；保留本地薄封装便于 runner 内调用。"""
    return acl_dtype(name)


# ── bf16 位窄化 / 展宽（改动③；adapt 自 gen_cases._f32_to_bf16_uint16 / _bf16_uint16_to_f32）──


def f32_to_bf16_bytes(v) -> np.ndarray:
    """fp32 -> bf16 的 uint16 位模式（round-half-to-even）。

    numpy 无 bfloat16 → 主机侧**真位截断**得 2 字节 bf16 设备字节（不能把 fp32 4 字节
    memcpy 当 bf16）。±0 保符号；inf 保 inf；进位可正确溢为 inf；NaN 保 quiet + 保符号。
    provenance：位对齐 gen_cases._f32_to_bf16_uint16（同一 round-half-even 口径，落盘/喂 kernel 一致）。
    """
    x = np.asarray(v, dtype=np.float32)
    u32 = x.view(np.uint32)
    is_nan = np.isnan(x)
    lsb = (u32 >> np.uint32(16)) & np.uint32(1)          # 目标 LSB，用于 round-half-to-even
    bias = np.uint32(0x7FFF) + lsb
    rounded = (u32 + bias) >> np.uint32(16)              # 进位可传入指数域 → 正确溢为 inf
    bf = rounded.astype(np.uint16)
    sign16 = ((u32 >> np.uint32(16)) & np.uint32(0x8000)).astype(np.uint16)
    bf = np.where(is_nan, np.uint16(0x7FC0) | sign16, bf)  # NaN -> quiet NaN（防截断后误成 inf）
    return np.ascontiguousarray(bf, dtype=np.uint16)


def bf16_bytes_to_f32(u) -> np.ndarray:
    """bf16 的 uint16 位模式 -> fp32（低 16 位零扩展；对网格上的值无损）。"""
    uu = np.asarray(u, dtype=np.uint16).astype(np.uint32) << np.uint32(16)
    return np.ascontiguousarray(uu.view(np.float32), dtype=np.float32)


def _storage_np(dtype: str) -> np.dtype:
    """设备字节的落盘/缓冲 numpy dtype：bf16 -> uint16（2 字节位模式）；余 = 逻辑 dtype。

    逻辑 dtype 不是合法 numpy dtype → fail-closed（不静默兜底成别的宽度）。
    """
    if dtype == "bfloat16":
        return np.dtype(np.uint16)
    try:
        return np.dtype(dtype)
    except TypeError as exc:
        raise AclnnRunnerError(f"未知逻辑 dtype {dtype!r}（无法定 storage 宽度）——fail-closed") from exc


# device 缓冲字节数的硬上限：C ``size_t`` / ACL 接口按 64bit 无符号传，超了即溢出 → 宁可 fail-closed。
_MAX_NBYTES = (1 << 63) - 1


def _checked_nbytes(shape, itemsize: int) -> int:
    """**独立**算 numel × itemsize 并做溢出检查（audit#2：不拿数组自报的 nbytes 当唯一依据）。

    维度非负整数校验 + 逐步累乘越界即 raise（防 numpy 的 int 溢出/大 shape 静默回绕）。
    0 维（shape=[]）→ numel=1（标量张量占 1 个元素）。
    """
    numel = 1
    for d in shape:
        d = int(d)
        if d < 0:
            raise AclnnRunnerError(f"非法 shape（维度为负）: {list(shape)!r}")
        numel *= d
        if numel > _MAX_NBYTES:
            raise AclnnRunnerError(f"shape {list(shape)!r} 的元素数溢出 64bit——fail-closed")
    itemsize = int(itemsize)
    if itemsize <= 0 or numel > _MAX_NBYTES // itemsize:
        raise AclnnRunnerError(
            f"shape {list(shape)!r} × itemsize {itemsize} 的字节数溢出 64bit——fail-closed")
    return numel * itemsize


def _keep_shape(storage: np.ndarray, shape) -> np.ndarray:
    """还原原始 shape：``np.ascontiguousarray`` 会把 **0 维**数组提成 ``(1,)``（audit#6 的另一半根因）。"""
    shape = tuple(shape)
    return storage if storage.shape == shape else storage.reshape(shape)


def _norm_param_name(name: str, role: str) -> str:
    """形参名归一，供 slots ↔ 签名逐项对账（audit#4）。

    只归一 **aclnn 接口层的稳定书写约定**（据 role，不据算子身份）：大小写 / 下划线 / 输出形参的
    ``Out`` 后缀（header 写 ``valuesOut``、spec 写 ``values``）。归一后仍不等即 fail-closed。
    """
    s = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if role == "out" and len(s) > 3 and s.endswith("out"):
        s = s[:-3]
    return s


# 标量属性 ctype → ctypes 类型（audit#5：float32/float64 分开，按 C ABI 的真实位宽传值）。
_ATTR_CTYPES = {
    "int64": ctypes.c_int64,
    "bool": ctypes.c_bool,
    "float32": ctypes.c_float,
    "float64": ctypes.c_double,
}


def _find_custom_opapi_libs() -> list[str]:
    """从 ``$ASCEND_OPP_PATH/vendors/*/op_api/lib/libcust_opapi.so`` glob（build install 产物）。

    **custom vendor lib 可选**（D1 真机发现 Bug#1）：未 set ``ASCEND_OPP_PATH`` 或无 custom lib →
    返回 ``[]``、**绝不 raise**。CANN **内置** aclnn 算子（如 ``aclnnAdd`` / ``aclnnMedianDim``，
    在 ``libopapi.so`` 全局符号里）不需任何 custom vendor 即可跑；只有当算子确实来自 PR build 的
    custom vendor 时才需要这些 lib。是否缺失由**算子来源**决定，不该在此处一刀切 fail、把内置算子全挡掉。
    """
    opp = os.environ.get("ASCEND_OPP_PATH")
    if not opp:
        return []
    return sorted(str(p) for p in Path(opp).glob("vendors/*/op_api/lib/libcust_opapi.so"))


# ── ctypes 执行体 ────────────────────────────────────────────────────────────


class AclnnRunner:
    """进程内 ACL 上下文，跑 install 好的 aclnn 算子（内置或 custom），支持多输出 + 任意 dtype。"""

    def __init__(self, device: int = 0):
        self.device = device
        self._acl = None
        self._stream = None

    def _ck(self, name: str, ret: int, ok: tuple[int, ...] = (0,)) -> None:
        if ret not in ok:
            raise AclnnRunnerError(f"{name} failed with ACL status {ret}")

    def _ensure_init(self) -> None:
        if self._acl is not None:
            return
        mode = os.RTLD_GLOBAL | os.RTLD_NOW
        cann = os.environ.get("ASCEND_TOOLKIT_HOME")
        if not cann:
            raise AclnnRunnerError("ASCEND_TOOLKIT_HOME is not set; source CANN set_env.sh first")
        for lib in ("libascendcl.so", "libnnopbase.so", "libopapi.so"):
            ctypes.CDLL(os.path.join(cann, "lib64", lib), mode=mode)
        # custom vendor lib 可选（Bug#1）：找到就 RTLD_GLOBAL 加载（PR build 出的 custom 算子）；
        # 空列表 = 无 custom vendor → 跳过，仅用上面三个 .so 即可跑内置 aclnn 算子。
        for lib in _find_custom_opapi_libs():
            ctypes.CDLL(lib, mode=mode)
        acl = ctypes.CDLL(None)
        vp = ctypes.c_void_p
        # 改动⑤：每个指针型形参 MUST 声明 argtypes，否则 ctypes 默认 c_int 截断 64-bit 指针。
        acl.aclCreateTensor.restype = vp
        acl.aclCreateTensor.argtypes = [vp, ctypes.c_uint64, ctypes.c_int, vp, ctypes.c_int64,
                                        ctypes.c_int, vp, ctypes.c_uint64, vp]
        acl.aclDestroyTensor.restype = ctypes.c_int
        acl.aclDestroyTensor.argtypes = [vp]
        # aclScalar 支持穿插的标量属性走 aclScalar* 形参的通用机制（median 用不到，但据签名 ctype 通用备着）。
        acl.aclCreateScalar.restype = vp
        acl.aclCreateScalar.argtypes = [vp, ctypes.c_int]
        acl.aclDestroyScalar.restype = ctypes.c_int
        acl.aclDestroyScalar.argtypes = [vp]
        acl.aclInit.restype = ctypes.c_int
        acl.aclInit.argtypes = [vp]
        acl.aclrtSetDevice.restype = ctypes.c_int
        acl.aclrtSetDevice.argtypes = [ctypes.c_int]
        acl.aclrtCreateStream.restype = ctypes.c_int
        acl.aclrtCreateStream.argtypes = [ctypes.POINTER(vp)]
        acl.aclrtSynchronizeStream.restype = ctypes.c_int
        acl.aclrtSynchronizeStream.argtypes = [vp]
        acl.aclrtMalloc.restype = ctypes.c_int
        acl.aclrtMalloc.argtypes = [ctypes.POINTER(vp), ctypes.c_size_t, ctypes.c_int]
        acl.aclrtMemcpy.restype = ctypes.c_int
        acl.aclrtMemcpy.argtypes = [vp, ctypes.c_size_t, vp, ctypes.c_size_t, ctypes.c_int]
        acl.aclrtFree.restype = ctypes.c_int
        acl.aclrtFree.argtypes = [vp]
        self._ck("aclInit", acl.aclInit(None), ok=(0, REPEAT_INITIALIZE))
        self._ck("aclrtSetDevice", acl.aclrtSetDevice(self.device))
        stream = vp()
        self._ck("aclrtCreateStream", acl.aclrtCreateStream(ctypes.byref(stream)))
        self._acl = acl
        self._stream = stream

    def _malloc(self, nbytes: int) -> ctypes.c_void_p:
        ptr = ctypes.c_void_p()
        self._ck("aclrtMalloc", self._acl.aclrtMalloc(
            ctypes.byref(ptr), ctypes.c_size_t(max(nbytes, 1)), MALLOC_HUGE_FIRST))
        return ptr

    def _make_tensor(self, shape: list[int], acl_dtype_name: str, *,
                     host: np.ndarray | None, nbytes: int):
        """建一个 device aclTensor（format=ND）。``host`` 非空则 H2D 拷入其字节。

        ``host`` 必须已是**设备字节 dtype**（bf16 传 uint16 位模式，非 fp32）；调用方负责窄化。
        """
        acl, vp = self._acl, ctypes.c_void_p
        dims = (ctypes.c_int64 * len(shape))(*shape) if shape else (ctypes.c_int64 * 1)(1)
        ndim = len(shape)
        strd_vals = contiguous_strides(shape) if shape else [1]
        strd = (ctypes.c_int64 * len(strd_vals))(*strd_vals)
        dtype_enum = _acl_dtype(acl_dtype_name)      # 未知 dtype 在分配前就 fail-closed
        dev = self._malloc(nbytes)
        # audit#3：dev 已分配、还没交回调用方——此段任何失败都必须**就地**释放，否则外层无从登记。
        try:
            if host is not None and nbytes > 0:
                self._ck("aclrtMemcpy(H2D)", acl.aclrtMemcpy(
                    dev, ctypes.c_size_t(nbytes), host.ctypes.data_as(vp),
                    ctypes.c_size_t(nbytes), MEMCPY_H2D))
            tensor = acl.aclCreateTensor(dims, ndim, dtype_enum, strd, 0,
                                         ACL_FORMAT_ND, dims, ndim, dev)
            if not tensor:
                raise AclnnRunnerError("aclCreateTensor returned NULL")
        except BaseException:
            self._free_quiet(dev)
            raise
        return tensor, dev

    def _free_quiet(self, dev) -> None:
        """best-effort 释放一块 device 缓冲：清理期的异常**绝不覆盖**原始异常（audit#3）。"""
        try:
            self._acl.aclrtFree(dev)
        except Exception:
            pass

    def _release_all(self, tensors: list, scalars: list, devs: list) -> None:
        """统一回收（成功/失败同一条路）：tensor → scalar → device 缓冲（含 workspace）。逐个吞异常。"""
        acl = self._acl
        for t in tensors:
            try:
                acl.aclDestroyTensor(t)
            except Exception:
                pass
        for sc in scalars:
            try:
                acl.aclDestroyScalar(sc)
            except Exception:
                pass
        for dev in devs:
            self._free_quiet(dev)

    def _prep_input(self, arr: np.ndarray, logical_dtype: str):
        """把逻辑输入数组转成**设备字节数组 + ACL dtype 名**（op-中立，据 logical_dtype 分派）。

        **规范 storage dtype 在任何分配之前定死**（audit#2）：
          · bf16：numpy 无该 dtype → 已是 uint16（gen_cases 落盘的 bf16 位模式）直接用；是 fp32 则真位
            截断（round-half-even）成 uint16；**其它物理 dtype 一律拒**（别拿 int8 数组冒充 bf16 位模式）。
          · 非 bf16：物理 dtype **必须**等于声明的逻辑 dtype，不等即 fail-closed。
            （旧版只 contiguous 不校验 → 2 元素 uint8 声明成 float32 时只分配 2 字节、而 tensor 按 8 字节
            读写 → kernel 越界。宁可拒，不静默转换/欠分配。）
        """
        arr = np.asarray(arr)
        if logical_dtype == "bfloat16":
            if arr.dtype == np.uint16:
                storage = np.ascontiguousarray(arr, dtype=np.uint16)   # 已是 bf16 位模式
            elif arr.dtype == np.float32:
                storage = f32_to_bf16_bytes(arr)                        # fp32 -> bf16 位窄化
            else:
                raise AclnnRunnerError(
                    f"bfloat16 输入的物理 dtype 只能是 uint16(位模式) 或 float32(待窄化)，得 "
                    f"{arr.dtype.name!r}——fail-closed")
            return _keep_shape(storage, arr.shape), "bfloat16"
        want = _storage_np(logical_dtype)
        if arr.dtype != want:
            raise AclnnRunnerError(
                f"输入物理 dtype {arr.dtype.name!r} ≠ 声明逻辑 dtype {logical_dtype!r}"
                f"（按声明 dtype 建 tensor、按物理字节分配 → 会欠/超分配）——fail-closed")
        storage = np.ascontiguousarray(arr)
        return _keep_shape(storage, arr.shape), logical_dtype

    @staticmethod
    def _validate_slots_against_signature(slots: list[dict], signature: "AclnnSignature",
                                          op_name: str) -> None:
        """**强制**交叉校验：算子名 + 参数总数 + 逐项 ``(name, role, ctype)`` 须与签名一致（audit#1/#4）。

        out_null slot 对应签名里**存在**的 out 张量形参（只是本 case 传 NULL、不回读），故映射到 role="out"。
        任一不一致 → fail-closed（防「slots 拼错顺序 / 同类张量对调 / 属性类型漂移」悄悄段错误或静默出错值）。
        名字比对走 :func:`_norm_param_name` 归一（aclnn 的 ``*Out`` 输出后缀 / 大小写 / 下划线是**接口约定**，
        不是算子身份——按约定归一，绝非按算子名特判）。
        """
        if not isinstance(signature, AclnnSignature):
            raise AclnnRunnerError(
                f"aclnn{op_name}: signature 必须是 AclnnSignature（audit#1：不接受 None / 兜底调用）")
        if signature.op_name != op_name:
            raise AclnnRunnerError(
                f"签名算子名 {signature.op_name!r} ≠ 调用符号 {op_name!r}——签名与调用不同源，fail-closed")
        if len(slots) != len(signature.params):
            raise AclnnRunnerError(
                f"aclnn{op_name}: 调用 slots 共 {len(slots)} 个 ≠ 签名形参 {len(signature.params)} 个"
                f"（arity 不符）——fail-closed，绝不带着错 arity 进 native 调用")
        for i, (s, p) in enumerate(zip(slots, signature.params)):
            kind = s.get("kind")
            role = "out" if kind == "out_null" else kind
            if role != p["role"]:
                raise AclnnRunnerError(
                    f"aclnn{op_name}: 第 {i} 个形参 role {role!r}（slot kind={kind!r}）≠ 签名 {p['role']!r}"
                    f"（签名参数名 {p['name']!r}）")
            name = s.get("name")
            if not name:
                raise AclnnRunnerError(
                    f"aclnn{op_name}: 第 {i} 个 slot 缺 name——slots 必须带 name 才能与签名逐项对账（audit#4）")
            if _norm_param_name(name, role) != _norm_param_name(p["name"], p["role"]):
                raise AclnnRunnerError(
                    f"aclnn{op_name}: 第 {i} 个形参名 {name!r} ≠ 签名 {p['name']!r}"
                    f"（同类张量对调会静默出错值）——fail-closed")
            ctype = "tensor" if role in ("in", "out") else s.get("ctype")
            if ctype != p.get("ctype"):
                raise AclnnRunnerError(
                    f"aclnn{op_name}: 形参 {p['name']!r} 的 ctype {ctype!r} ≠ 签名 {p.get('ctype')!r}")

    def run(self, op_name: str, slots: list[dict], *,
            signature: "AclnnSignature") -> list[np.ndarray]:
        """执行 ``aclnn<op_name>``，按**有序 slots** 拼实参，返回各 out-slot 输出数组（顺序 = out-slot 顺序）。

        ``slots`` 是 driver 从 caseset **每个 case 已解析好的** ``aclnn_call`` 直取的有序混合形参表，
        每项必带 ``name``（与签名逐项对账），``kind``：
          · ``{"kind":"in","name":...,"array":np.ndarray,"dtype":<逻辑 dtype>}`` —— 输入张量（bf16 窄化 + H2D）；
          · ``{"kind":"attr","name":...,"ctype":"int64"|"bool"|"float32"|"float64"|"scalar","value":...}``
            —— **穿插**的标量属性，据 ctype marshal（int64→c_int64 / bool→c_bool / float32→**c_float** /
            float64→c_double / scalar→aclCreateScalar+c_void_p）；
          · ``{"kind":"out","name":...,"shape":[...],"dtype":<逻辑 dtype>}`` —— 输出张量（alloc device+host、记待 D2H）；
          · ``{"kind":"out_null","name":...}`` —— 该输出本 case 不产（如全局 median 只有 values、无 indices）→ 传
            ctypes NULL、不 D2H、不产出。

        ``signature`` **必传**（audit#1：``None`` 兜底已删）——先做全量交叉校验（算子名 / arity / 逐项
        name+role+ctype），**校验不过绝不进 native 调用**。无 header 的调用方须显式构造 :class:`AclnnSignature`，
        仍受同一套校验，**绝不按算子名特判**。

        **argtypes 与实参严格按 slots 真实顺序拼**（不再假设「张量全在前、attr 不存在」——正是 median
        ``(self, dim, keepDim, values, indices)`` 段错误的根因）：``gws.argtypes = [每 slot 对应 ctype...]
        + [vp, vp]``（末尾 &workspaceSize / &executor）。**全程 try/finally**：tensor / scalar / device 缓冲 /
        workspace 一经登记，无论 H2D、GetWorkspaceSize、执行、同步还是 D2H 哪一步炸，都在 finally 里回收
        （audit#3；清理异常不覆盖原始异常）。
        """
        self._validate_slots_against_signature(slots, signature, op_name)
        self._ensure_init()
        acl, vp = self._acl, ctypes.c_void_p

        # 符号解析**前移到任何分配之前**（audit#3：符号找不到时不该已经占着 device 内存）。
        gws = getattr(acl, f"aclnn{op_name}GetWorkspaceSize", None)
        run_fn = getattr(acl, f"aclnn{op_name}", None)
        if gws is None or run_fn is None:
            raise AclnnRunnerError(
                f"aclnn{op_name}[GetWorkspaceSize] not found in loaded ACL libs")

        tensors_to_destroy: list = []   # 待 aclDestroyTensor（输入 + 非空输出张量）
        scalars_to_destroy: list = []   # 待 aclDestroyScalar
        devs: list = []                 # 待 aclrtFree（device 缓冲，含 workspace）
        keepalive: list = []            # 让标量 host 缓冲活到调用后（防 GC 提前回收）
        ordered_args: list = []         # 按 slots 顺序的实参
        argtypes: list = []             # 与 ordered_args 并列的 ctype
        out_specs: list = []            # (shape, logical_dtype, host_buffer, dev_ptr)，仅非空 out-slot

        try:
            for slot in slots:
                kind = slot["kind"]
                if kind == "in":
                    arr = np.asarray(slot["array"])
                    logical = slot.get("dtype") or arr.dtype.name
                    storage, acl_name = self._prep_input(arr, logical)
                    shape = list(storage.shape)          # 0 维保 []（audit#6：别把标量改成 [1]）
                    nbytes = _checked_nbytes(shape, storage.dtype.itemsize)
                    if nbytes != int(storage.nbytes):    # 独立算的字节数须与实际缓冲对得上
                        raise AclnnRunnerError(
                            f"输入 {slot.get('name')!r}: 据 shape×itemsize 算得 {nbytes} 字节 ≠ 缓冲实际 "
                            f"{int(storage.nbytes)} 字节——fail-closed")
                    t, dev = self._make_tensor(shape, acl_name, host=storage, nbytes=nbytes)
                    tensors_to_destroy.append(t)
                    devs.append(dev)
                    ordered_args.append(t)
                    argtypes.append(vp)
                elif kind == "attr":
                    ctype = slot.get("ctype")
                    if "value" not in slot:
                        raise AclnnRunnerError(
                            f"属性 {slot.get('name')!r} 无 value——调用须在 gen_cases 侧解析好，"
                            f"runner 不塞默认值（fail-closed）")
                    value = slot["value"]
                    if value is None:
                        raise AclnnRunnerError(
                            f"属性 {slot.get('name')!r} 的 value 为 null——须由 spec 的 call_variants 解析成"
                            f"确定值，runner 绝不静默兜底")
                    if ctype in _ATTR_CTYPES:
                        cty = _ATTR_CTYPES[ctype]
                        if ctype == "int64":
                            cval = cty(int(value))
                        elif ctype == "bool":
                            cval = cty(bool(value))
                        else:
                            cval = cty(float(value))
                        ordered_args.append(cval)
                        argtypes.append(cty)
                    elif ctype == "scalar":
                        np_dt = _storage_np(str(slot.get("dtype", "float32")))
                        buf = np.asarray(value, dtype=np_dt)
                        sc = acl.aclCreateScalar(buf.ctypes.data_as(vp), _acl_dtype(np_dt.name))
                        if not sc:
                            raise AclnnRunnerError("aclCreateScalar returned NULL")
                        scalars_to_destroy.append(sc)
                        keepalive.append(buf)
                        ordered_args.append(sc)
                        argtypes.append(vp)
                    else:
                        raise AclnnRunnerError(
                            f"unsupported attr ctype: {ctype!r}（可用 {sorted(_ATTR_CTYPES)} + 'scalar'；"
                            f"⚠ 'float' 已废——C float/double 位宽不同，须写 float32/float64）")
                elif kind == "out":
                    shp = [int(d) for d in slot["shape"]]
                    dt = slot["dtype"]
                    storage_np = _storage_np(dt)
                    nbytes = _checked_nbytes(shp, storage_np.itemsize)
                    n = nbytes // storage_np.itemsize
                    host_buf = np.empty(n, dtype=storage_np)
                    if int(host_buf.nbytes) != nbytes:
                        raise AclnnRunnerError(
                            f"输出 {slot.get('name')!r}: host 缓冲 {int(host_buf.nbytes)} 字节 ≠ 据 "
                            f"shape×itemsize 算得 {nbytes} 字节——fail-closed")
                    t, dev = self._make_tensor(shp, dt, host=None, nbytes=nbytes)
                    tensors_to_destroy.append(t)
                    devs.append(dev)
                    ordered_args.append(t)
                    argtypes.append(vp)
                    out_specs.append((shp, dt, host_buf, dev))
                elif kind == "out_null":
                    ordered_args.append(vp(None))           # ctypes NULL：该输出不产、不 D2H
                    argtypes.append(vp)
                else:
                    raise AclnnRunnerError(f"unknown slot kind: {kind!r}")

            # argtypes 按 slots 真实顺序拼（张量→vp、标量→其 C 类型），末尾 &workspaceSize + &executor。
            gws.restype = ctypes.c_int
            gws.argtypes = argtypes + [vp, vp]
            run_fn.restype = ctypes.c_int
            run_fn.argtypes = [vp, ctypes.c_uint64, vp, vp]

            ws = ctypes.c_uint64(0)
            exe = vp()
            self._ck(f"aclnn{op_name}GetWorkspaceSize",
                     gws(*ordered_args, ctypes.byref(ws), ctypes.byref(exe)))
            if ws.value > 0:
                ws_ptr = self._malloc(ws.value)
                devs.append(ws_ptr)                  # 立刻登记 → 后续任何失败都能在 finally 释放
            else:
                ws_ptr = vp()
            self._ck(f"aclnn{op_name}", run_fn(ws_ptr, ws.value, exe, self._stream))
            self._ck("aclrtSynchronizeStream", acl.aclrtSynchronizeStream(self._stream))

            # 逐 out-slot D2H + bf16 展宽（out_null 不在 out_specs 里 → 天然跳过、不产出）。
            results: list[np.ndarray] = []
            for shp, dt, host_buf, dev in out_specs:
                if host_buf.nbytes > 0:
                    self._ck("aclrtMemcpy(D2H)", acl.aclrtMemcpy(
                        host_buf.ctypes.data_as(vp), ctypes.c_size_t(host_buf.nbytes), dev,
                        ctypes.c_size_t(host_buf.nbytes), MEMCPY_D2H))
                if dt == "bfloat16":
                    arr = bf16_bytes_to_f32(host_buf)        # 2 字节 bf16 -> fp32
                else:
                    arr = host_buf
                results.append(arr.reshape(shp) if shp else arr.reshape(()))
            return results
        finally:
            self._release_all(tensors_to_destroy, scalars_to_destroy, devs)
