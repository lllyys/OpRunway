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
  4. ``_find_custom_opapi_libs`` **三源都吃**（D2 真机 Bug#B：install 生成的权威
     ``vendors/<v>_nn/bin/set_env.bash`` 只导 ``ASCEND_CUSTOM_OPP_PATH`` + ``LD_LIBRARY_PATH``、
     **不设** ``ASCEND_OPP_PATH`` → 旧版按官方 env 跑一个 custom lib 都找不到）：
     ``$ASCEND_CUSTOM_OPP_PATH`` 各 vendor 内容根（与 ``aclnn_driver._header_dirs`` **同口径**）→
     ``$ASCEND_OPP_PATH/vendors/*/`` glob → ``$LD_LIBRARY_PATH`` 各目录兜底。
     **custom vendor lib 仍可选**（Bug#1：找不到返回 ``[]`` 不 raise，内置 aclnn 算子照跑；
     是否「必须来自 custom vendor」由改动⑧的严格档表态）。
  5. argtypes 全声明（防 ctypes 默认 c_int 截断 64-bit 指针）；标量属性 ``float``/``double`` **分开**
     marshal（c_float / c_double，audit#5）。
  6. 资源全程 **try/finally** 回收（tensor / aclScalar / device 缓冲 / workspace），``_make_tensor``
     自身在 H2D 或建 tensor 失败时就地释放本地 dev；清理异常绝不覆盖原始异常（audit#3）。
  7. 分配前定死**规范 storage dtype**（非 bf16 物理 dtype 必须 == 声明逻辑 dtype、bf16 必须 uint16/f32），
     并用**带溢出检查的 numel×itemsize** 独立算字节数、与缓冲实际 nbytes 核对（audit#2，防欠分配越界）。
  8. **符号解析优先打 custom vendor handle**（D2 真机 Bug#A，**会造成假 PASS 的最坏失效**）：
     ELF 全局符号「先加载者赢」，而 CANN 自带的 ``libopapi.so`` **本身就导出** ``aclnnMedian`` /
     ``aclnnMedianGetWorkspaceSize`` 之类的同名符号（a3 实测 global 地址 == libopapi.so 的、≠
     libcust_opapi.so 的）。旧版从 ``CDLL(None)`` 全局命名空间 ``getattr`` 取 → **验的是 CANN 内置实现、
     不是被测 PR 产物**；本次因内置 arity 不同当场报 ACL 161001 才暴露，**若 PR 改的是同名同签名的既有算子，
     会静默验内置版本、报假 PASS**。现改为：先逐个从 ``libcust_opapi.so`` 的 **CDLL handle 直接 dlsym**，
     找不到再退全局（``LD_PRELOAD`` 路线**不用**——a3 实测每次进程退出必 double free/SIGABRT，且已定位
     裸 ``aclInit+aclrtSetDevice`` 在有 LD_PRELOAD 时同样 abort、不是本工具的锅）。
     同时**逐符号记 provenance**（来自哪个 ``.so`` 绝对路径 + size/mtime + 可选 sha256 + 地址 +
     全局同名符号地址/所属 so + ``global_conflict`` 标记），经 :meth:`AclnnRunner.runtime_provenance`
     暴露给上层（driver 写进 ``out_manifest.json`` → 进证据链，用来**证明验的确实是 PR 产物**）。
     ``require_custom_vendor=True`` = **fail-closed 严格档**：目标符号只在全局/内置找得到即 raise，
     绝不静默拿内置冒充 DUT（验收路径默认走严格档，见 ``aclnn_driver.main``）。
 10. **dlsym 会沿依赖树继续找 → 严格档曾漏判**（D3 真机洞，比改动⑧原 bug 更隐蔽）：
     ``getattr(CDLL(libcust_opapi.so), sym)`` 底下是 POSIX ``dlsym(handle, …)``，它**不只查该 so 自身**，
     还沿其 ``DT_NEEDED`` 依赖树往下找。a3 实测 ``objdump -p libcust_opapi.so`` 的 NEEDED =
     ``libnnopbase.so`` + ``libopapi_math.so``（**都是 CANN 内置**），而 ``nm -D libopapi_math.so``
     定义了 ``aclnnAbs`` / ``aclnnIsClose`` / ``aclnnSign`` / ``aclnnSort`` … **整个 elementwise/math 家族**
     → 这些算子在严格档下**不 fail-closed**、假 PASS 通道仍开，且 provenance 把 handle 路径当来源
     **记错了出处**（比没证据更危险）。median 躲过纯属运气（``aclnnMedian`` 恰不在依赖闭包里）。
     现改为：dlsym 拿到函数指针后必用 ``dladdr`` 反查**定义方 so**（``defining_lib``），与**已加载的
     custom vendor lib 集合**按 ``realpath`` 比对；不属于该集合（含反查不出）就当「不在本 vendor」——
     严格档 raise、宽松档如实记 ``source="dependency_of_custom_vendor"``。provenance 的载重字段是
     ``defining_lib``（``lib`` 与它同源），dlsym 走的 handle 路径另记 ``resolved_via``。
     ⚠ ``global_conflict`` **不能单独当 DUT 证据**：``aclnnAbs`` 那条实测 ``global_conflict=true``，
     可两边其实都是 CANN 内置（handle 路径的那份也是沿依赖树找到的内置实现）。
  9. **stream 生命周期 + 外部 stream 注入**（D2 真机 Bug#C）：旧版每 new 一个 runner 就
     ``aclrtCreateStream`` 一条、**从不 destroy**（实测 7 条 case 落在 stream 41/40/38/46… 各不相同 →
     性能通路「按 stream 归并」失效），且无处注入外部 stream。现 ``AclnnRunner(device=..., stream=...)``
     可复用调用方的 stream（**传了就不自建、更不销毁别人的**，供 MSTX device range 与 kernel 同流采集），
     并提供**幂等** :meth:`AclnnRunner.close` + context-manager（销毁**自建** stream；仅在自建 stream
     即上下文由本 runner 建起时才 ``aclrtResetDevice``）。
 11. **严格档必须绑定「本次 DUT」而不只是「某个 custom so」**（audit#1，改动⑧/⑩之后仍留的最后一道口子）：
     旧版严格档接受 ``_custom_handles`` 里**任意**一个 custom vendor lib 定义的符号。可这些 handle 来自
     ``ASCEND_CUSTOM_OPP_PATH`` / ``ASCEND_OPP_PATH`` / ``LD_LIBRARY_PATH`` 的**环境探测**——环境里完全
     可能继承着**上一次**（甚至别的 PR 的）安装产物；本次 PR 若漏导某符号，解析会继续命中那份陈旧 so，
     还照记 ``source="custom_vendor"`` + ``defining_lib_verified=true`` → **旧产物代跑、报假 PASS**，
     而 ``verified=true`` 又让人过度相信。两个 aclnn 符号还可能分别落到**不同** vendor 库上。
     现改为：严格档**必须**由调用方显式声明本次 DUT（``dut_lib=<libcust_opapi.so 绝对路径>`` 或
     ``dut_vendor_root=<vendor 内容根>``），**未声明即 fail-closed**（不许「严格档却不知道该绑谁」）；
     严格档下**只加载、只采信该 DUT so**（环境探到的其它 custom so 只作诊断记进
     ``ignored_custom_opapi_libs``），且 ``GetWorkspaceSize`` 与执行符号**必须同出该 DUT**。
     provenance 因此拆两个字段：``defining_lib_verified``（定义方已核实属已加载的 custom vendor 集合）
     与 ``is_dut``（定义方**就是本次 DUT**）——**前者绝不可再被读成后者**。
 12. **device/context ownership 与 stream ownership 分开记**（audit#6）：旧版 ``close()`` 只看
     ``_owns_stream``，而 ``aclrtSetDevice`` 成功、``aclrtCreateStream`` 才失败时 ``_owns_stream`` 仍为
     ``False`` → **本 runner 建起来的 device 上下文没人 reset**。现 ``_owns_device`` 独立记账，
     ``_bring_up_device`` 在建 stream 失败时**完整回滚**（销毁自建 stream + reset 自己 set 的 device）。
 13. **清理 API 的返回码不再静默丢**（audit#7）：ACL 用返回码报错、不抛 Python 异常，旧版把
     ``aclrtDestroyStream`` / ``aclrtResetDevice`` / ``aclFinalize`` 的返回值整个忽略，且清理前已置
     ``_closed=True`` → 重复 close 不再尝试、**也无任何错误记录**。现逐个查返回码并记进**可审计的**
     ``teardown``（进 :meth:`AclnnRunner.runtime_provenance`）：**正常** close 把清理失败报出来，
     ``__exit__`` 带着原始异常退出时**只记账、绝不覆盖原异常**。
 14. **同一文件的判定加 (st_dev, st_ino)**（audit#9）：硬链接 / bind mount / 容器里同一 inode 的不同
     可见路径 ``realpath`` 合并不了；加载器此前经另一别名加载过同一文件时 ``dladdr`` 会返回那个别名，
     只比 realpath 会把自家 DUT 误判成外部库（**假失败**）。现 :func:`_same_file` 先比 realpath、
     再比设备号+inode；**两侧身份取不到时继续保守判「不同」**（安全方向不变）。

纯 helper（``parse_aclnn_op`` / ``contiguous_strides`` / ``_acl_dtype`` / bf16 位转换 /
``_find_custom_opapi_libs``）**无 CANN 依赖、可离线单测**；``AclnnRunner`` 的 ctypes 执行路径需 NPU。
"""

from __future__ import annotations

import ctypes
import hashlib
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


# custom 算子包 install 后对外暴露 aclnn 两段式实现的 so（CANN 固定产物名，与算子身份无关）。
_CUST_OPAPI_SO = "libcust_opapi.so"


def _env_paths(var: str) -> list[str]:
    """读一个**冒号分隔**的路径型环境变量，去空白/空项（不解析、不猜默认值）。"""
    return [p.strip() for p in (os.environ.get(var) or "").split(":") if p.strip()]


def _file_identity(path: str | None) -> tuple[int, int] | None:
    """取一个文件的 ``(st_dev, st_ino)`` 身份；取不到（不存在 / 无权限）→ ``None``。

    ``None`` **不**代表「与谁都不同」也**不**代表「与谁都相同」——由 :func:`_same_file` 保守处置。
    """
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (int(st.st_dev), int(st.st_ino))


def _same_file(a: str | None, b: str | None) -> bool:
    """两个路径是否指向**同一个文件**（audit#9：``realpath`` 之外再比设备号+inode）。

    只比 ``realpath`` 会漏判**硬链接 / bind mount / 同 inode 的不同可见路径**——它们是同一份文件，
    但 ``realpath`` 各归各。加载器若此前经另一别名加载过同一 so，``dladdr`` 就会返回那个别名，
    严格档只比 realpath 会把自家 DUT 误判成外部库（**假失败**：方向上仍安全，但白白拦掉合法验收）。
    故：realpath 相等 → 同一；否则两侧 ``(st_dev, st_ino)`` **都取得到且相等** → 同一；
    **任一侧身份取不到 → 保守判「不同」**（宁可拒，不放行没证据的来源）。
    """
    if not a or not b:
        return False
    try:
        if os.path.realpath(a) == os.path.realpath(b):
            return True
    except OSError:
        pass
    ida = _file_identity(a)
    return ida is not None and ida == _file_identity(b)


def _resolve_dut_lib(dut_lib: str | None, dut_vendor_root: str | None) -> str | None:
    """把调用方声明的 DUT 标识归一成**唯一一个 .so 绝对路径**（audit#1）；都没给 → ``None``。

    两种等价写法（都由**调用方显式**给，绝不从环境猜）：
      · ``dut_lib`` —— 本次 build install 出来的 ``libcust_opapi.so`` 绝对路径；
      · ``dut_vendor_root`` —— vendor **内容根**（与 ``$ASCEND_CUSTOM_OPP_PATH`` 各段同口径），
        DUT so = ``<root>/op_api/lib/libcust_opapi.so``。
    两个都给且**指的不是同一个文件** → fail-closed（声明自相矛盾时绝不替调用方选一个）。
    """
    from_root = (str(Path(dut_vendor_root) / "op_api" / "lib" / _CUST_OPAPI_SO)
                 if dut_vendor_root else None)
    explicit = str(dut_lib) if dut_lib else None
    if explicit and from_root:
        if os.path.abspath(explicit) != os.path.abspath(from_root) \
                and not _same_file(explicit, from_root):
            raise AclnnRunnerError(
                f"dut_lib 与 dut_vendor_root 指向不同文件——dut_lib={os.path.abspath(explicit)}，"
                f"dut_vendor_root 推出 {os.path.abspath(from_root)}；DUT 必须唯一，fail-closed")
    path = explicit or from_root
    return os.path.abspath(path) if path else None


def _find_custom_opapi_libs() -> list[str]:
    """找 build install 产物 ``libcust_opapi.so``——**三源都吃**，按可信度排优先级（Bug#B）。

    1. ``$ASCEND_CUSTOM_OPP_PATH``：install 生成的**权威** ``vendors/<v>_nn/bin/set_env.bash`` 导的就是它
       （冒号分隔的 vendor **内容根**）→ ``<root>/op_api/lib/libcust_opapi.so``。与
       :func:`aclnn_driver._header_dirs` **同口径**（旧版只读 ``ASCEND_OPP_PATH``、与 driver 两套口径 →
       按官方 env 跑时头找得到、lib 一个都找不到）。
    2. ``$ASCEND_OPP_PATH``：``vendors/*/op_api/lib/libcust_opapi.so`` glob（手工 set 该变量的老路子）。
    3. ``$LD_LIBRARY_PATH``：set_env.bash 同时导出的动态库搜索路径，逐目录兜底 ``<dir>/libcust_opapi.so``。

    顺序即**符号解析优先级**（越靠前越权威），按 realpath 去重。**custom vendor lib 可选**
    （D1 Bug#1）：一个都没有 → 返回 ``[]``、**绝不 raise**——CANN **内置** aclnn 算子（在
    ``libopapi.so`` 里）不需任何 custom vendor 即可跑。「被测物是否**必须**来自 custom vendor」
    是**调用方**的声明（``AclnnRunner(require_custom_vendor=True)`` 严格档），不在此处一刀切。
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(path) -> None:
        p = str(path)
        if not os.path.isfile(p):
            return
        real = os.path.realpath(p)
        if real in seen:
            return
        seen.add(real)
        found.append(p)

    for root in _env_paths("ASCEND_CUSTOM_OPP_PATH"):          # ① 权威（set_env.bash 导的）
        _add(Path(root) / "op_api" / "lib" / _CUST_OPAPI_SO)
    for opp in _env_paths("ASCEND_OPP_PATH"):                  # ② 老路子（vendors/* glob）
        for p in sorted(Path(opp).glob(f"vendors/*/op_api/lib/{_CUST_OPAPI_SO}")):
            _add(p)
    for d in _env_paths("LD_LIBRARY_PATH"):                    # ③ 兜底（同一份 set_env.bash 也导它）
        _add(Path(d) / _CUST_OPAPI_SO)
    return found


# ── 符号 provenance（改动⑧：证明「调的到底是哪个 .so 里的实现」）────────────────────────


class _DlInfo(ctypes.Structure):
    """glibc ``Dl_info``——``dladdr`` 据函数地址反查所属动态库（用于给全局符号定位真身）。"""

    _fields_ = [("dli_fname", ctypes.c_char_p), ("dli_fbase", ctypes.c_void_p),
                ("dli_sname", ctypes.c_char_p), ("dli_saddr", ctypes.c_void_p)]


def _func_address(fn) -> int | None:
    """取 ctypes 函数指针的数值地址；非 ctypes 对象（如单测的 mock）→ None（best-effort，不炸）。"""
    try:
        return ctypes.cast(fn, ctypes.c_void_p).value
    except Exception:
        return None


def _dladdr_lib(addr: int | None) -> str | None:
    """据地址反查所属 ``.so`` 绝对路径（best-effort；无 dladdr / 查不到 → None，绝不因此中断跑测）。"""
    if not addr:
        return None
    try:
        dladdr = ctypes.CDLL(None).dladdr
        dladdr.restype = ctypes.c_int
        dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DlInfo)]
        info = _DlInfo()
        if dladdr(ctypes.c_void_p(addr), ctypes.byref(info)) == 0 or not info.dli_fname:
            return None
        return info.dli_fname.decode("utf-8", "replace")
    except Exception:
        return None


def _lib_fingerprint(path: str | None, *, sha256: bool = False) -> dict | None:
    """一个 ``.so`` 的取证指纹：绝对路径 + size + mtime（廉价、恒记）+ **可选** sha256（大 so 才贵）。"""
    if not path:
        return None
    rec: dict = {"path": os.path.abspath(path), "size": None, "mtime": None, "sha256": None}
    try:
        st = os.stat(path)
        rec["size"], rec["mtime"] = int(st.st_size), round(st.st_mtime, 3)
    except OSError:
        pass
    if sha256:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            rec["sha256"] = h.hexdigest()
        except OSError:
            pass
    return rec


# ── ctypes 执行体 ────────────────────────────────────────────────────────────


class AclnnRunner:
    """进程内 ACL 上下文，跑 install 好的 aclnn 算子（内置或 custom），支持多输出 + 任意 dtype。

    构造参数（全 op-中立）：
      · ``device`` —— NPU device id；
      · ``stream`` —— **外部注入的 aclrtStream**（``ctypes.c_void_p`` 或整数句柄）。传了就**不自建**、
        :meth:`close` 也**绝不销毁别人的**（改动⑨：性能采集要让 MSTX device range 与 kernel 同流）；
        不传 = 自建一条并由本 runner 负责销毁；
      · ``require_custom_vendor`` —— **fail-closed 严格档**（改动⑧ + ⑩ + ⑪）：``True`` 时目标 ``aclnn*``
        符号**必须由本次 DUT 的** ``libcust_opapi.so`` **定义**——判据是 ``dladdr`` 反查出的
        ``defining_lib``，**不是** dlsym 用的 handle 路径（dlsym 会沿依赖树找到 CANN 内置的
        ``libopapi_math.so`` 等，见改动⑩），也**不是**「环境里随便哪个 custom so」（改动⑪）。
        只在全局/内置找得到、经 handle 找到但定义方 ≠ DUT、或**反查不出**定义方 → 一律 raise
        （防「验的是内置实现 / 上次遗留的陈旧安装产物、不是被测 PR 产物」的假 PASS）。
        默认 ``False`` = 允许内置算子（无 PR 的基线场景）；
        **验收路径应显式开严格档**（``aclnn_driver`` CLI 与 ``perf_msprof`` 采集 wrapper 默认即严格）。
      · ``dut_lib`` / ``dut_vendor_root`` —— **本次被测物（DUT）的显式声明**（改动⑪）：前者是
        ``libcust_opapi.so`` 绝对路径，后者是 vendor 内容根（DUT so = ``<root>/op_api/lib/…``）；
        两个都给必须指同一文件。**严格档下二者必须给其一**，否则构造即 fail-closed——
        「严格档却不知道该绑谁」等于允许环境里继承来的陈旧 so 代跑本次验收。
        宽松档下可选：给了就照样在 provenance 里标 ``is_dut``（只记录、不拦）。
      · ``hash_symbol_libs`` —— provenance 里是否附 ``.so`` 的 sha256（大 so 要整读一遍，默认关；
        路径 + size + mtime 恒记）。
    """

    def __init__(self, device: int = 0, stream=None, *,
                 require_custom_vendor: bool = False, dut_lib: str | None = None,
                 dut_vendor_root: str | None = None, hash_symbol_libs: bool = False):
        self.device = device
        self._acl = None
        self._stream = None
        self._external_stream = None if stream is None else self._as_stream(stream)
        self._owns_stream = False
        self._owns_device = False                             # 与 stream ownership **分开**记（audit#6）
        self._closed = False
        self._require_custom_vendor = bool(require_custom_vendor)
        self._hash_symbol_libs = bool(hash_symbol_libs)
        self._dut_lib = _resolve_dut_lib(dut_lib, dut_vendor_root)
        if self._require_custom_vendor and not self._dut_lib:
            raise AclnnRunnerError(
                "require_custom_vendor=True（严格档）必须同时声明本次 DUT："
                "dut_lib=<libcust_opapi.so 绝对路径> 或 dut_vendor_root=<vendor 内容根>——fail-closed。"
                "否则「符号来自某个 custom vendor so」只证明得了它来自**环境里某个** custom so"
                "（ASCEND_CUSTOM_OPP_PATH / ASCEND_OPP_PATH / LD_LIBRARY_PATH 里继承来的**上一次**"
                "安装产物同样算），证明不了它来自**本次 PR build 出来的**那个 → 本次漏导符号时"
                "旧产物会代跑并报假 PASS。严格档不许「不知道该绑谁」。")
        self._custom_handles: list[tuple[str, object]] = []   # [(so 绝对路径, CDLL handle)]，顺序=解析优先级
        self._custom_lib_snapshot: list[dict] = []            # close() 前留下的 custom lib 指纹快照（洞 3）
        self._ignored_custom_libs: list[str] = []             # 严格档下环境探到但**不采信**的 custom so（诊断）
        self._teardown_errors: list[dict] = []                # 清理 API 的非零返回码 / 异常（audit#7，可审计）
        self._sym_provenance: dict[str, dict] = {}            # symbol -> provenance 记录
        self._lib_fp_cache: dict[str, dict] = {}              # so 路径 -> 指纹（避免重复 stat/hash）

    # ── stream / 生命周期（改动⑨）────────────────────────────────────────────

    @staticmethod
    def _as_stream(stream):
        """把外部传入的 stream 归一成 ``ctypes.c_void_p``（接受 c_void_p / 整数句柄；别的 fail-closed）。"""
        if isinstance(stream, ctypes.c_void_p):
            return stream
        if isinstance(stream, int) and not isinstance(stream, bool):
            return ctypes.c_void_p(stream)
        raise AclnnRunnerError(
            f"外部 stream 只接受 ctypes.c_void_p 或整数句柄，得 {type(stream).__name__}——fail-closed")

    @property
    def stream(self):
        """当前 aclrtStream（自建或外部注入）；未 init 时为 ``None``。供性能采集与 kernel 同流用。"""
        return self._stream

    @property
    def owns_stream(self) -> bool:
        """本 runner 是否**自建**了 stream（=> close 时由它销毁）。外部注入的恒为 ``False``。"""
        return self._owns_stream

    @property
    def owns_device(self) -> bool:
        """本 runner 是否**建起了 device 上下文**（=> close 时由它 ``aclrtResetDevice``）。

        与 :attr:`owns_stream` **各记各的**（audit#6）：``aclrtSetDevice`` 成功、``aclrtCreateStream``
        才失败时 ``owns_stream`` 仍是 ``False``，而 device 上下文确实已由本 runner 建起——旧版 close
        只看 stream ownership，那条上下文就没人 reset 了。
        """
        return self._owns_device

    def _setup_stream(self, acl) -> None:
        """定 stream：外部注入的直接用（**不自建、不销毁别人的**）；否则自建一条并记 ownership。"""
        if self._external_stream is not None:
            self._stream = self._external_stream
            self._owns_stream = False
            return
        stream = ctypes.c_void_p()
        self._ck("aclrtCreateStream", acl.aclrtCreateStream(ctypes.byref(stream)))
        self._stream = stream
        self._owns_stream = True

    def _bring_up_device(self, acl) -> None:
        """``aclrtSetDevice`` → 记 **device ownership** → 定 stream；中途失败**完整回滚**（audit#6）。

        ownership 口径：外部注入 stream ⇒ device 上下文由调用方（torch_npu / 采集器）持有，我们
        **不**接管、也不 reset 别人的；否则这条上下文是本 runner 建起来的 → ``close`` 必须 reset，
        **哪怕紧接着的 ``aclrtCreateStream`` 炸了**（旧版正是这里漏 reset）。
        """
        self._ck("aclrtSetDevice", acl.aclrtSetDevice(self.device))
        self._owns_device = self._external_stream is None
        try:
            self._setup_stream(acl)
        except BaseException:
            self._rollback_bring_up(acl)             # 回滚自己建的那半截，绝不留悬空上下文
            raise

    def _rollback_bring_up(self, acl) -> None:
        """init 中途失败的回滚：销毁**自建** stream + reset **本 runner set 的** device（audit#6）。

        本身**绝不抛**——清理失败只记进 :attr:`_teardown_errors`（audit#7），免得掩盖真正的 init 异常。
        """
        if self._stream is not None and self._owns_stream:
            self._teardown_call("aclrtDestroyStream", acl.aclrtDestroyStream, self._stream)
        if self._owns_device:
            self._teardown_call("aclrtResetDevice", acl.aclrtResetDevice, self.device)
        self._stream, self._owns_stream, self._owns_device = None, False, False

    def _teardown_call(self, api: str, fn, *args) -> None:
        """调一个 ACL 清理 API 并**检查返回码**（audit#7）。失败只**记账**、绝不抛。

        ACL 用返回码报错、**不抛 Python 异常** → 旧版把 ``aclrtDestroyStream`` / ``aclrtResetDevice`` /
        ``aclFinalize`` 的返回值整个丢掉，stream 没销掉、device 没 reset 都悄无声息。现每次调用都把
        「非零状态码 / 抛出的异常 / 返回值根本不是整数」记进 :attr:`_teardown_errors`（可审计、进
        provenance 的 ``teardown``），由 :meth:`close` 在**正常**路径上统一报出来；异常在飞时只记不报。
        """
        try:
            ret = fn(*args)
        except BaseException as exc:                 # 清理路径不许再往外抛（会盖掉原始异常）
            self._teardown_errors.append({"api": api, "status": None, "error": repr(exc)})
            return
        try:
            status = int(ret)
        except (TypeError, ValueError):
            self._teardown_errors.append(
                {"api": api, "status": None, "error": f"返回值不是整数状态码: {ret!r}"})
            return
        if status != 0:
            self._teardown_errors.append({"api": api, "status": status, "error": None})

    def teardown_status(self) -> dict:
        """清理阶段的**可审计**状态（audit#7）：是否已 close + 逐条清理失败记录。

        ``errors`` 每项 ``{"api": <ACL 函数名>, "status": <非零返回码或 None>, "error": <异常/说明或 None>}``。
        非空 = 本 runner 的资源**未必**真回收干净（stream 可能没销、device 可能没 reset）——
        该条会随 :meth:`runtime_provenance` 进证据链，别当没发生过。
        """
        return {"closed": self._closed, "errors": list(self._teardown_errors)}

    def close(self, *, finalize: bool = False, raise_on_error: bool = True) -> None:
        """回收本 runner 占的 ACL 资源。**幂等**（重复 close 不炸），可当 context-manager 用。

        · **自建** stream → ``aclrtDestroyStream``；**外部注入**的 stream **绝不销毁**（不是我们的）；
        · ``aclrtResetDevice`` 只在 :attr:`owns_device` 时做（audit#6：**独立于** stream ownership）
          ——外部注入 stream 意味着 device 上下文由调用方（torch_npu / 采集器）持有，替它 reset 会拆别人的台；
        · ``aclFinalize`` 默认**不**调（进程级、会拆同进程其它 runner / torch_npu 的 ACL 上下文），
          确需时显式 ``finalize=True``。
        每个清理 API 的**返回码都查**（audit#7）：失败记进 :meth:`teardown_status`；
        ``raise_on_error=True``（默认，正常 close）时本次新增的清理失败**报出来**——清理没成功却装作成功，
        下一个 runner / 下一次采集会撞在半回收的上下文上。``raise_on_error=False`` 用于**有原始异常在飞**
        的路径（:meth:`__exit__`）：只记账、**绝不覆盖原异常**。
        close 后本 runner 不可再 run（fail-closed）。

        ⚠ 幂等的代价：第二次 close **不重试**已做过的清理（重复 destroy 一个可能已销毁的 stream 更危险），
        但第一次的失败**永久留在** :meth:`teardown_status` 里，不会「重复 close 就什么记录都没有」。

        ⚠ 洞 3：丢 handle **前**先把 custom vendor lib 的指纹**快照**下来——否则 close 之后
        :meth:`runtime_provenance` 的 ``custom_opapi_libs`` 会变空（``symbols`` 还在），
        谁在 close 后写 manifest 就会**静默丢半条证据**。
        """
        if self._closed:
            return
        self._closed = True
        acl, stream = self._acl, self._stream
        owns_stream, owns_device = self._owns_stream, self._owns_device
        self._stream, self._owns_stream, self._owns_device, self._acl = None, False, False, None
        if self._custom_handles:
            self._custom_lib_snapshot = [self._fingerprint(p) for p, _ in self._custom_handles]
        self._custom_handles = []
        if acl is None:
            return                                   # 从未 init 过 → 没有可回收的
        before = len(self._teardown_errors)          # 只对**本次** close 新增的失败负责
        if stream is not None and owns_stream:
            self._teardown_call("aclrtDestroyStream", acl.aclrtDestroyStream, stream)
        if owns_device:                              # 上下文是本 runner 建起来的才 reset
            self._teardown_call("aclrtResetDevice", acl.aclrtResetDevice, self.device)
        if finalize:
            self._teardown_call("aclFinalize", acl.aclFinalize)
        new_errors = self._teardown_errors[before:]
        if raise_on_error and new_errors:
            raise AclnnRunnerError(
                f"ACL 资源清理失败（device={self.device}）：{new_errors}——ACL 用返回码报错、不抛异常，"
                f"旧版会把这些码整个丢掉。资源未必真回收干净（stream 可能没销、device 可能没 reset）；"
                f"完整记录见 teardown_status() / runtime_provenance()['teardown']")

    def __enter__(self) -> "AclnnRunner":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # 有原始异常在飞 → 清理失败只记账（audit#7：teardown 绝不覆盖调用方的原始异常）；
        # 正常退出才把清理失败报出来。
        self.close(raise_on_error=exc_type is None)
        return False                                 # 绝不吞调用方的异常

    def _ck(self, name: str, ret: int, ok: tuple[int, ...] = (0,)) -> None:
        if ret not in ok:
            raise AclnnRunnerError(f"{name} failed with ACL status {ret}")

    def _ensure_init(self) -> None:
        if self._closed:
            raise AclnnRunnerError("AclnnRunner 已 close，不能再 run（请新建一个 runner）——fail-closed")
        if self._acl is not None:
            return
        mode = os.RTLD_GLOBAL | os.RTLD_NOW
        cann = os.environ.get("ASCEND_TOOLKIT_HOME")
        if not cann:
            raise AclnnRunnerError("ASCEND_TOOLKIT_HOME is not set; source CANN set_env.sh first")
        for lib in ("libascendcl.so", "libnnopbase.so", "libopapi.so"):
            ctypes.CDLL(os.path.join(cann, "lib64", lib), mode=mode)
        # custom vendor lib：
        #   · **严格档**（audit#1）——**只**加载调用方声明的那一个 DUT so。环境探测（ASCEND_CUSTOM_OPP_PATH /
        #     ASCEND_OPP_PATH / LD_LIBRARY_PATH）找出来的其它 custom so 可能是**上一次**（甚至别的 PR 的）
        #     安装遗留；把它们一并当合法来源 = 允许陈旧产物代跑本次验收（假 PASS）。它们只作**诊断**
        #     记进 _ignored_custom_libs（进 provenance），一律不加载、不采信。
        #   · **宽松档**（Bug#1）——维持三源搜索；一个都没有就跳过，仅用上面三个 .so 即可跑内置 aclnn 算子。
        # ⚠ 仍按 RTLD_GLOBAL 加载（与真机已验证的加载姿态一致），但**目标符号不再从全局命名空间取**
        #   ——handle 留着，run() 经 _resolve_symbol 直接 dlsym（改动⑧：全局是「先加载者赢」，内置
        #   libopapi.so 先到 → 会静默打到内置实现）。
        self._custom_handles = []
        self._ignored_custom_libs = []
        env_found = _find_custom_opapi_libs()
        if self._require_custom_vendor:
            if not os.path.isfile(self._dut_lib):
                raise AclnnRunnerError(
                    f"严格档声明的 DUT so 不存在: {self._dut_lib}——本次被测物没 build/install 出来？"
                    f"（环境探到的 custom so: {env_found or '（空）'}，严格档一律不拿它们顶替）fail-closed")
            to_load = [self._dut_lib]
            self._ignored_custom_libs = [p for p in env_found if not _same_file(p, self._dut_lib)]
        else:
            to_load = env_found
        for lib in to_load:
            try:
                handle = ctypes.CDLL(lib, mode=mode)
            except OSError as exc:                   # DUT 的 so 加载不了 = 硬故障，别静默跳过
                raise AclnnRunnerError(f"加载 custom vendor lib 失败: {lib}: {exc}") from exc
            self._custom_handles.append((os.path.abspath(lib), handle))
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
        # teardown 三件套（改动⑨：旧版一条 stream 都不销、也没 reset/finalize 入口）。
        acl.aclrtDestroyStream.restype = ctypes.c_int
        acl.aclrtDestroyStream.argtypes = [vp]
        acl.aclrtResetDevice.restype = ctypes.c_int
        acl.aclrtResetDevice.argtypes = [ctypes.c_int]
        acl.aclFinalize.restype = ctypes.c_int
        acl.aclFinalize.argtypes = []
        acl.aclrtMalloc.restype = ctypes.c_int
        acl.aclrtMalloc.argtypes = [ctypes.POINTER(vp), ctypes.c_size_t, ctypes.c_int]
        acl.aclrtMemcpy.restype = ctypes.c_int
        acl.aclrtMemcpy.argtypes = [vp, ctypes.c_size_t, vp, ctypes.c_size_t, ctypes.c_int]
        acl.aclrtFree.restype = ctypes.c_int
        acl.aclrtFree.argtypes = [vp]
        self._ck("aclInit", acl.aclInit(None), ok=(0, REPEAT_INITIALIZE))
        # SetDevice + stream 一体带回滚（audit#6）：任一步失败都不留「已 set 未 reset」的悬空上下文。
        # 成功之后才认 self._acl —— 否则半 init 的 runner 会被下次 _ensure_init 当成「已初始化」跳过。
        self._bring_up_device(acl)
        self._acl = acl

    # ── 符号解析 + provenance（改动⑧）────────────────────────────────────────

    def _fingerprint(self, path: str | None) -> dict | None:
        if not path:
            return None
        if path not in self._lib_fp_cache:
            self._lib_fp_cache[path] = _lib_fingerprint(path, sha256=self._hash_symbol_libs)
        return self._lib_fp_cache[path]

    def _global_symbol(self, sym: str):
        """全局命名空间（``CDLL(None)``）里的同名符号；没有 → None。**只作对照/兜底，不优先**。"""
        if self._acl is None:
            return None
        return getattr(self._acl, sym, None)

    def _custom_paths(self) -> list[str]:
        """当前已加载的 custom vendor lib 路径（顺序 = 解析优先级）。"""
        return [p for p, _ in self._custom_handles]

    def _is_custom_vendor_lib(self, path: str | None) -> bool:
        """``path``（dladdr 反查出的定义方 so）是否属于**已加载的 custom vendor lib 集合**。

        ⚠ 这只回答「是**某个** custom so」，**不**回答「是**本次 DUT**」（audit#1 的教训）——
        严格档的放行判据是 :meth:`_is_dut_lib`，本方法只用于**宽松档**记录与诊断。
        同一性判定走 :func:`_same_file`（realpath + 设备号/inode，audit#9）。
        """
        if not path:
            return False                             # 反查不出 → 不算「已证明属于本 vendor」
        return any(_same_file(path, p) for p in self._custom_paths())

    def _is_dut_lib(self, path: str | None) -> bool:
        """``path`` 是否**就是本次声明的 DUT so**（严格档唯一的放行判据，audit#1）。

        没声明 DUT（宽松档且调用方没给）→ 恒 ``False``：「无从判断」绝不算「是」。
        """
        return bool(self._dut_lib) and _same_file(path, self._dut_lib)

    def _dut_handles(self) -> list[tuple[str, object]]:
        """严格档下**允许 dlsym 的 handle 集合 = 只有 DUT**（audit#1）。

        正常情况下 :meth:`_ensure_init` 严格档就只加载 DUT 一个；这里再过一道，是因为 handle 也可能
        由调用方/上层注入（且 DUT 可能经软链/硬链的另一别名加载 → 按 :func:`_same_file` 认同一文件）。
        """
        return [(p, h) for p, h in self._custom_handles if self._is_dut_lib(p)]

    def _record_symbol(self, sym: str, fn, *, source: str, defining_lib: str | None,
                       resolved_via: str, defining_lib_verified: bool | None) -> dict:
        """登记一个被调符号的 provenance：**谁定义了它** + 经哪条路取到 + 地址 + 全局同名符号对照。

        字段语义（**载重的是 ``defining_lib``**，改动⑩）：
          · ``defining_lib`` —— ``dladdr`` 反查出的**定义方 so**（``lib`` / ``lib_*`` 指纹与它同源）。
            这是唯一能回答「这份实现到底出自谁」的字段。
          · ``resolved_via`` —— dlsym 实际用的 handle 路径（或 ``"global_namespace"``）。
            **它不等于来源**：POSIX ``dlsym`` 会沿该 so 的 ``DT_NEEDED`` 依赖树继续找，
            经 ``libcust_opapi.so`` 取到的符号完全可能定义在 CANN 内置的 ``libopapi_math.so`` 里。
          · ``defining_lib_verified`` —— 定义方是否**已核实**属于已加载的 custom vendor lib 集合；
            ``None`` = 反查不出（既不能证实也不能证伪）。
            ⚠ **它不等于「来自本次 DUT」**（audit#1 的原教旨误读）：环境里继承来的**陈旧** custom so
            也在那个集合里，照样能让本字段变 ``true``。要问「是不是被测 PR 产物」，看下一条。
          · ``is_dut`` —— 定义方是否**就是本次声明的 DUT so**（``dut_lib`` / ``dut_vendor_root``）。
            ``True`` 才是「验的确实是本次 PR build 的产物」；``None`` = 调用方压根没声明 DUT
            （宽松档，无从判断——**别把 None 当 True 读**）。严格档下只可能是 ``True``（否则已 raise）。
          · ``global_conflict`` —— 全局命名空间的同名符号 ≠ 实际调用的那个。
            ⚠ **不能单独当 DUT 证据**：a3 实测 ``aclnnAbs`` 这条为 ``true``，可两边其实**都是** CANN 内置
            （handle 那份也是沿依赖树找到的内置实现）。要证明验的是 DUT，看 ``defining_lib`` /
            ``defining_lib_verified``，别看 ``global_conflict``。
        """
        addr = _func_address(fn)
        gfn = self._global_symbol(sym)
        gaddr = _func_address(gfn) if gfn is not None else None
        fp = self._fingerprint(defining_lib) or {}
        rec = {
            "symbol": sym,
            # "custom_vendor"（定义方已核实属本 vendor）| "dependency_of_custom_vendor"（经 vendor handle
            # 取到、但定义方是别的 so，多半是 CANN 内置依赖）| "custom_vendor_unverified"（定义方反查不出）
            # | "global"（走全局命名空间）
            "source": source,
            "resolved_via": resolved_via,
            "defining_lib": fp.get("path") or defining_lib,
            "defining_lib_verified": defining_lib_verified,
            # 「已核实属某个 custom so」≠「就是本次 DUT」——两件事分两个字段记（audit#1）。
            "is_dut": self._is_dut_lib(defining_lib) if self._dut_lib else None,
            "lib": fp.get("path") or defining_lib,   # = defining_lib（**不是** dlsym 用的 handle 路径）
            "lib_size": fp.get("size"),
            "lib_mtime": fp.get("mtime"),
            "lib_sha256": fp.get("sha256"),
            "address": hex(addr) if addr else None,
            "global_address": hex(gaddr) if gaddr else None,
            "global_lib": _dladdr_lib(gaddr),
            "global_conflict": bool(addr and gaddr and addr != gaddr),
        }
        self._sym_provenance[sym] = rec
        return rec

    def _resolve_symbol(self, sym: str):
        """取 ``aclnn*`` 函数指针：**先逐个 custom vendor handle dlsym**，找不到再退全局（改动⑧）。

        符号名一律由调用方从 caseset 的 ``aclnn_call.symbol`` 传下来——**绝无按算子名的分支**。

        ⚠ 改动⑩：dlsym 成功**不等于**符号出自这个 so——POSIX ``dlsym(handle, …)`` 会沿 handle 的
        ``DT_NEEDED`` 依赖树继续找（a3 实测 ``libcust_opapi.so`` NEEDED 了 CANN 内置的
        ``libopapi_math.so``，后者定义了整个 elementwise/math 家族）。故每次 dlsym 命中都要用
        ``dladdr`` 反查**定义方 so** 再作判定。

        ⚠ 改动⑪（audit#1）：**严格档与宽松档的判据不同，别混**——
          · **严格档**：只在**本次 DUT** 的 handle 上 dlsym，且定义方必须 :meth:`_is_dut_lib`。
            「定义方是**某个** custom so」**不够**：环境里继承来的陈旧安装产物也是 custom so，
            本次 PR 漏导符号时正会打到它 → 旧产物代跑、报假 PASS。凡定义方 ≠ DUT（含反查不出）
            一律 raise，错误信息**点名「实际定义方 so vs 期望 DUT so」**。
          · **宽松档**：维持原判据（属于已加载 custom vendor 集合即记 ``custom_vendor``），
            其余来源如实记 ``dependency_of_custom_vendor`` / ``custom_vendor_unverified`` / ``global``。
        """
        strict = self._require_custom_vendor
        # 严格档：搜索面**收窄到 DUT 一个** handle；其余来源只允许出现在宽松档 / 诊断信息里。
        search = self._dut_handles() if strict else list(self._custom_handles)
        fallbacks: list[tuple[str, object, str | None]] = []   # [(handle 路径, fn, 定义方 so)]
        for path, handle in search:
            fn = getattr(handle, sym, None)          # CDLL 缺符号会 AttributeError → 默认 None
            if fn is None:
                continue
            defining = _dladdr_lib(_func_address(fn))
            hit = self._is_dut_lib(defining) if strict else self._is_custom_vendor_lib(defining)
            if hit:
                self._record_symbol(sym, fn, source="custom_vendor", defining_lib=defining,
                                    resolved_via=path, defining_lib_verified=True)
                return fn
            fallbacks.append((path, fn, defining))   # dlsym 命中了，但定义方不是（或证不出是）该来源
        if strict:
            gfn = self._global_symbol(sym)
            where = _dladdr_lib(_func_address(gfn)) if gfn is not None else None
            # 诊断（**只**用于错误信息，绝不参与放行）：符号是不是躺在别的已加载 custom so 里
            # ——那正是「环境里继承来的陈旧安装产物代跑」的现场。
            others = [p for p, h in self._custom_handles
                      if not self._is_dut_lib(p) and getattr(h, sym, None) is not None]
            detail = ""
            if not search:
                detail = (f"⚠ 声明的 DUT so **不在已加载的 custom vendor lib 里**"
                          f"（已加载 {self._custom_paths() or '（空）'}）——请确认 dut_lib / dut_vendor_root "
                          f"指的就是本次 build+install 出来的那个 {_CUST_OPAPI_SO}。")
            elif fallbacks:
                via, _, defining = fallbacks[0]
                detail = (f"⚠ **实际定义方 so** = {defining or '（dladdr 反查不出）'}，"
                          f"**期望 DUT so** = {self._dut_lib}——**不是同一个文件**"
                          f"（dlsym 经 {via} 命中）。POSIX dlsym 会沿该 so 的 DT_NEEDED 依赖树继续找"
                          f"（{_CUST_OPAPI_SO} 依赖 CANN 内置的 libnnopbase.so / libopapi_math.so，"
                          f"后者定义了整个 elementwise/math 家族），命中的多半是 CANN 内置实现、"
                          f"或**上一次安装遗留的陈旧 vendor so**；用它验收 = 验的不是被测 PR 产物（假 PASS）。")
            elif others:
                detail = (f"⚠ 该符号在**别的已加载 custom vendor so** 里有定义（{others}），"
                          f"但**不在本次 DUT** {self._dut_lib} 里。这些多半是 ASCEND_CUSTOM_OPP_PATH / "
                          f"ASCEND_OPP_PATH / LD_LIBRARY_PATH 里继承来的**上一次安装产物**；本次 PR 没实现 / "
                          f"没导出该符号时拿它代跑，就是**旧产物冒充被测物**（假 PASS）。")
            elif gfn is not None:
                detail = (f"⚠ 该符号在**全局命名空间**存在（{where or '所属 so 未知'}），多半是 CANN 内置实现；"
                          f"用它验收 = 验的不是被测 PR 产物（假 PASS）。")
            ignored = (f" 环境里另有 custom so {self._ignored_custom_libs} 未被采信"
                       f"（严格档只认本次 DUT，旧安装产物一律不顶替）。" if self._ignored_custom_libs else "")
            raise AclnnRunnerError(
                f"{sym} 未能证明由**本次 DUT** 的 {_CUST_OPAPI_SO} **定义**——期望 DUT so = "
                f"{self._dut_lib}（已加载 custom vendor lib: {self._custom_paths() or '（空）'}）"
                f"，而调用方声明了 require_custom_vendor=True（被测物必须来自本次 PR build 的 custom vendor）"
                f"——fail-closed。" + detail + ignored
                + f" 请确认 dut_lib / dut_vendor_root 指向本次 install 的 vendor、"
                  f"已 source 该 vendor 的 set_env.bash、且该算子确由本 vendor 实现；"
                  f"确要跑 CANN 内置算子则显式关严格档。")
        if fallbacks:                                # 宽松档：可用，但**如实**记真实来源，绝不冒充 vendor
            via, fn, defining = fallbacks[0]
            self._record_symbol(
                sym, fn,
                source="dependency_of_custom_vendor" if defining else "custom_vendor_unverified",
                defining_lib=defining, resolved_via=via,
                defining_lib_verified=False if defining else None)
            return fn
        fn = self._global_symbol(sym)
        if fn is None:
            raise AclnnRunnerError(
                f"{sym} not found in loaded ACL libs（custom vendor: "
                f"{self._custom_paths() or '（空）'}；全局命名空间也没有）")
        self._record_symbol(sym, fn, source="global", defining_lib=_dladdr_lib(_func_address(fn)),
                            resolved_via="global_namespace",
                            defining_lib_verified=False)
        return fn

    def _assert_two_stage_same_dut(self, ws_sym: str, run_sym: str) -> None:
        """严格档：两段式的**两个符号必须同出本次 DUT 那一个 so**（audit#1）。

        ``aclnn<Op>GetWorkspaceSize`` 与 ``aclnn<Op>`` 是**一对**（前者产 ``aclOpExecutor``、后者消费它），
        却是**分别**解析的——完全可能一个命中本次 DUT、另一个命中环境里遗留的陈旧 vendor so。
        跨库拼出来的 executor / workspace 语义不保证兼容，且「一半是 DUT」根本不成其为验收证据。
        逐符号判据（``is_dut``）之外再卡这条**配对**约束，宁可 fail-closed。
        """
        if not self._require_custom_vendor:
            return                                   # 宽松档只记录，不拦（跑内置算子的基线场景）
        a = (self._sym_provenance.get(ws_sym) or {}).get("defining_lib")
        b = (self._sym_provenance.get(run_sym) or {}).get("defining_lib")
        if _same_file(a, self._dut_lib) and _same_file(b, self._dut_lib):
            return
        raise AclnnRunnerError(
            f"两段式的两个符号未同出本次 DUT——期望 DUT so = {self._dut_lib}；"
            f"{ws_sym} 的实际定义方 so = {a or '（反查不出）'}；"
            f"{run_sym} 的实际定义方 so = {b or '（反查不出）'}。"
            f"GetWorkspaceSize 与执行符号分属不同库时，executor/workspace 跨库语义不保证兼容，"
            f"且「一半是 DUT」证明不了验的是被测 PR 产物（假 PASS）——fail-closed")

    def runtime_provenance(self) -> dict:
        """**证据链**：本 runner 实际调用的每个符号**由哪个 .so 定义**（+ 加载了哪些 custom vendor lib）。

        由 ``aclnn_driver`` 写进 ``out_manifest.json``——用来证明「验的确实是**本次** PR build 的产物、
        不是 CANN 内置同名算子、也不是上次安装遗留的陈旧 vendor so」。判读口径：
          · **最载重的是** ``symbols[*].is_dut``（定义方 == ``dut_lib`` 声明的那一个 so）。
            ``defining_lib_verified=true`` 只说明定义方属于「某个已加载 custom so」——**不等于 DUT**
            （audit#1：陈旧安装产物同样满足它）；``None`` = 调用方没声明 DUT，无从判断。
          · ``symbols[*].defining_lib`` = dladdr 反查的定义方 so（``lib`` 与它同源）。
            ``source="custom_vendor"`` 才是「已核实出自被测 vendor」；``dependency_of_custom_vendor``
            = 经 vendor handle 的 dlsym 沿依赖树打到了别的 so（多半 CANN 内置）；``custom_vendor_unverified``
            = 反查不出定义方；``global`` = 走全局命名空间。后三者在严格档下都不可能出现（会 raise）。
          · ``symbols[*].resolved_via`` 只是 dlsym 走的 handle 路径，**不是来源证据**。
          · ``symbols[*].global_conflict`` 同样**不能单独当 DUT 证据**——两边可能都是内置实现
            （a3 实测 ``aclnnAbs`` 即如此）。
          · ``dut_lib`` = 本次声明的被测 so 指纹（严格档必有）；``ignored_custom_opapi_libs`` =
            环境探到但**未采信**的其它 custom so（严格档的诊断线索：它们正是会造成假 PASS 的那些）。
          · ``custom_opapi_libs`` 在 :meth:`close` 后取自 close 前的**指纹快照**（洞 3：证据不随 handle 丢）。
          · ``teardown`` = 清理阶段的可审计状态（audit#7）；``errors`` 非空说明资源未必回收干净。
        """
        libs = ([self._fingerprint(p) for p in self._custom_paths()] if self._custom_handles
                else list(self._custom_lib_snapshot))
        return {
            "device": self.device,
            "strict_custom_vendor": self._require_custom_vendor,
            "dut_lib": self._fingerprint(self._dut_lib),
            "stream_owned": self._owns_stream,
            "device_owned": self._owns_device,
            "custom_opapi_libs": libs,
            "ignored_custom_opapi_libs": [self._fingerprint(p) for p in self._ignored_custom_libs],
            "teardown": self.teardown_status(),
            "symbols": [self._sym_provenance[k] for k in sorted(self._sym_provenance)],
        }

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

        ``aclnn<op_name>[GetWorkspaceSize]`` 两个符号经 :meth:`_resolve_symbol` 取——**custom vendor
        handle 优先、全局兜底**，并逐符号记 provenance（改动⑧）；严格档下 custom vendor 里没有即 raise，
        绝不拿 CANN 内置同名实现冒充 DUT。

        **argtypes 与实参严格按 slots 真实顺序拼**（不再假设「张量全在前、attr 不存在」——正是 median
        ``(self, dim, keepDim, values, indices)`` 段错误的根因）：``gws.argtypes = [每 slot 对应 ctype...]
        + [vp, vp]``（末尾 &workspaceSize / &executor）。**全程 try/finally**：tensor / scalar / device 缓冲 /
        workspace 一经登记，无论 H2D、GetWorkspaceSize、执行、同步还是 D2H 哪一步炸，都在 finally 里回收
        （audit#3；清理异常不覆盖原始异常）。
        """
        self._validate_slots_against_signature(slots, signature, op_name)
        self._ensure_init()
        acl, vp = self._acl, ctypes.c_void_p

        # 符号解析**前移到任何分配之前**（audit#3：符号找不到时不该已经占着 device 内存）；
        # 且**优先打 custom vendor handle**、严格档下非本次 DUT 定义的就 raise（改动⑧/⑪，防假 PASS）。
        gws = self._resolve_symbol(f"aclnn{op_name}GetWorkspaceSize")
        run_fn = self._resolve_symbol(f"aclnn{op_name}")
        self._assert_two_stage_same_dut(f"aclnn{op_name}GetWorkspaceSize", f"aclnn{op_name}")

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
