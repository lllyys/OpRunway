#!/usr/bin/env python3
"""package_loader —— 任务包 gen_csv.py 的三道门装载器（harness-gen Step 1 草案）。

方案权威：dev-doc/harness-gen-plan.md §3（ABI 版本门 / 装载纪律 / 同源断言）与
§4（MVP 拒绝矩阵）。本模块把一个 case-gen 任务包装载成 harness 生成器的输入：

    facts, column_specs, header = load_package(package_dir)

- facts：从 gen_csv.py FACTS 区用 ast.literal_eval 提出的字典（不是 exec 命名空间里的对象）。
- column_specs：包内 `_column_specs(facts)` 的返回（每列 name/kind/source，fixed_vector
  元素列另带 index），逐项浅拷贝后返回。
- header：列名序列，已断言与 `_header_columns(facts)` 及实际 CSV 表头三方逐列一致。

装载纪律（方案 §3 门 2）：gen_csv.py 只读一次字节；AST 校验顶层形态与 FACTS 提取、
SHA-256、exec 全部基于这同一份字节。exec 发生在 ABI 版本门通过之后——不执行
不被认识的代码。

停机码（SKILL.md 停机码表，H1 三项 + H2 预筛一项）：
- UNSUPPORTED_PACKAGE_VERSION —— ABI 版本三元组未知、接口函数缺失或版本交叉不一致
  （能力边界：包可能是合法的新版 case-gen 产物，本 skill 不认识）。
- PACKAGE_MALFORMED —— 装载纪律失败：包形态不完整、标记缺失/乱序、语法或 AST 形态
  非法、FACTS 非纯字面量、包内投影执行失败（缺陷：包坏了）。
- HEADER_MISMATCH —— 同源断言失败：_column_specs / _header_columns / CSV 表头三方
  不一致或重复列名（缺陷：包内自相矛盾）。
- UNSUPPORTED_CONTRACT —— FACTS 语义超出已实现的生成能力（§4 机械可判子集：
  golden.kind 非 cblas、复数标量、nullable）。参数角色组合的封闭表判定属下游
  IR 编译（Step 2 冻结后实现），不在本模块。

衔接：header 直接喂前身 contract.py 的 compile_contract(facts, header)。
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import sys
import tokenize
from pathlib import Path

# ---------------------------------------------------------------------------
# 区域标记：逐字取自 case-gen 模板 assets/template/gen_csv.py（行 4 / 17 / 18），
# 与 assets/example/{sasum,cherk}/gen_csv.py 一致。匹配口径是整行严格相等
# （行尾 \r 容忍剥除；但 CRLF 文件会因 SHA 不匹配在版本门被拒，属预期 fail-closed）。
# ---------------------------------------------------------------------------
FACTS_BEGIN_MARKER = "# ===== FACTS 区开始 ====="
FACTS_END_MARKER = "# ===== FACTS 区结束 ====="
COMMON_BEGIN_MARKER = "# ===== 通用代码区（由 repo-task-blas-case-gen 渲染，禁止修改）====="

# ---------------------------------------------------------------------------
# ABI 允许列表（方案 §3 门 1）：(FACTS.schema_version, FACTS.generator_version,
# 通用代码区 SHA-256) 三元组。随本 skill 分发；加行要过评审。
#
# 通用区哈希算法：对 gen_csv.py 的原始字节，取 COMMON_BEGIN_MARKER 所在整行的
# 行首字节偏移起、至文件末尾（EOF）止的全部字节，hashlib.sha256(...).hexdigest()。
# 即 sha256(data[line_start_offset(COMMON_BEGIN_MARKER):])。
#
# 下面这行的哈希值于 2026-09-10 按上述算法对当前模板
# plugin/skill/repo-task-blas-case-gen/assets/template/gen_csv.py 实算得出，
# 并已核对与 assets/example/sasum、assets/example/cherk 两个包内副本逐字节一致。
# ---------------------------------------------------------------------------
KNOWN_PACKAGE_ABIS = frozenset({
    (1, 1, "8c90c6a895e9552b670e4d3d85a4c72a499a7a42ac005f80d38d5581de6088a1"),
})

# §4 预筛用的封闭词表（与模板通用区常量同名同值；此处独立声明，不从包里取，
# 因为拒绝判据必须属于本 skill 而非被装载对象）。
COMPLEX_DTYPES = frozenset({"complex64", "complex128"})
SCALAR_ROLES = frozenset({"scalar", "inout_scalar", "out_scalar"})

_REQUIRED_SPEC_KEYS = ("name", "kind", "source")


class LoaderReject(Exception):
    """装载 fail-closed 停机基类。code 属性即方案 §1 的停机码。"""

    code = "LOADER_REJECT"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"{self.code}: {reason}")


class UnsupportedPackageVersion(LoaderReject):
    """包生成器 ABI 版本、摘要或包形态不被认识。"""

    code = "UNSUPPORTED_PACKAGE_VERSION"


class PackageMalformed(LoaderReject):
    """装载纪律失败：包形态、语法、AST 形态或字面量提取非法。"""

    code = "PACKAGE_MALFORMED"


class HeaderMismatch(LoaderReject):
    """同源断言失败：三方表头不一致或重复列名。"""

    code = "HEADER_MISMATCH"


class UnsupportedContract(LoaderReject):
    """FACTS 语义超出已实现的生成能力（§4 拒绝矩阵）。"""

    code = "UNSUPPORTED_CONTRACT"


class _Markers:
    """三个标记行的 (1-based 行号, 行首字节偏移)。"""

    __slots__ = ("facts_begin", "facts_end", "common_begin")

    def __init__(self, facts_begin, facts_end, common_begin):
        self.facts_begin = facts_begin
        self.facts_end = facts_end
        self.common_begin = common_begin


def _find_markers(data: bytes) -> _Markers:
    """定位三个标记行；缺失、重复或乱序都拒绝。

    标记必须是行首 COMMENT token（tokenize 判定），docstring 或字符串字面量里的
    同文本行不算——防止把标记伪造进跨行 docstring 绕过区域结构（F4）。
    """
    wanted = {
        FACTS_BEGIN_MARKER: "facts_begin",
        FACTS_END_MARKER: "facts_end",
        COMMON_BEGIN_MARKER: "common_begin",
    }
    found: dict[str, list[int]] = {name: [] for name in wanted.values()}
    try:
        tokens = tokenize.tokenize(io.BytesIO(data).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT and tok.start[1] == 0:
                name = wanted.get(tok.string.rstrip("\r"))
                if name is not None:
                    found[name].append(tok.start[0])
    except (tokenize.TokenError, SyntaxError, IndentationError,
            UnicodeDecodeError, ValueError) as exc:
        raise PackageMalformed(f"gen_csv.py 词法解析失败：{exc}") from exc
    for name, hits in found.items():
        if len(hits) != 1:
            raise PackageMalformed(
                f"标记行 {name} 出现 {len(hits)} 次（要求恰一次）"
            )
    line_offsets = [0]
    for raw_line in data.split(b"\n")[:-1]:
        line_offsets.append(line_offsets[-1] + len(raw_line) + 1)

    def at(lineno: int) -> tuple:
        return (lineno, line_offsets[lineno - 1])

    markers = _Markers(
        at(found["facts_begin"][0]), at(found["facts_end"][0]),
        at(found["common_begin"][0]),
    )
    if not (
        markers.facts_begin[0] < markers.facts_end[0] < markers.common_begin[0]
    ):
        raise PackageMalformed("三个区域标记行乱序")
    return markers


def _common_region_sha256(data: bytes, markers: _Markers) -> str:
    """通用代码区摘要：COMMON_BEGIN_MARKER 整行行首字节偏移起至 EOF 的 SHA-256。"""
    return hashlib.sha256(data[markers.common_begin[1]:]).hexdigest()


def _extract_facts(data: bytes, gen_path: Path, markers: _Markers) -> dict:
    """AST 校验顶层形态并用 ast.literal_eval 提取 FACTS（方案 §3 门 2 的静态半程）。

    通用区语句（行号 >= COMMON_BEGIN 行）由 SHA 允许列表保真，不逐条审；
    标记行之前的自由区只允许模块 docstring 与唯一的 `FACTS = <字面量>` 赋值。
    """
    try:
        module = ast.parse(data, filename=str(gen_path))
    except (SyntaxError, ValueError) as exc:
        raise PackageMalformed(f"gen_csv.py 解析失败：{exc}") from exc
    common_lineno = markers.common_begin[0]
    facts_assign = None
    for node in module.body:
        if node.lineno >= common_lineno:
            continue
        if node.end_lineno >= common_lineno:
            raise PackageMalformed(
                f"行 {node.lineno} 起的语句跨越通用代码区标记行"
            )
        is_docstring = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.lineno < markers.facts_begin[0]
        )
        if is_docstring:
            continue
        is_facts_assign = (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "FACTS"
        )
        if is_facts_assign:
            if facts_assign is not None:
                raise PackageMalformed("FACTS 赋值出现多次")
            if not (
                markers.facts_begin[0] < node.lineno
                and node.end_lineno < markers.facts_end[0]
            ):
                raise PackageMalformed("FACTS 赋值不在 FACTS 区标记之间")
            facts_assign = node
            continue
        raise PackageMalformed(
            f"FACTS 区含不允许的顶层语句（行 {node.lineno}：{type(node).__name__}）"
        )
    if facts_assign is None:
        raise PackageMalformed("未找到 FACTS 赋值")
    try:
        facts = ast.literal_eval(facts_assign.value)
    except (ValueError, TypeError, SyntaxError, MemoryError) as exc:
        raise PackageMalformed(f"FACTS 非纯字面量：{exc}") from exc
    if not isinstance(facts, dict):
        raise PackageMalformed(
            f"FACTS 不是字典字面量（得到 {type(facts).__name__}）"
        )
    return facts


def _gate_abi_version(facts: dict, common_sha: str) -> None:
    """门 1 · ABI 版本门：三元组不在允许列表即停（§4：schema v2 由此拒绝）。"""
    for key in ("schema_version", "generator_version"):
        value = facts.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise PackageMalformed(
                f"FACTS.{key} 缺失或不是整数：{value!r}"
            )
    triple = (facts["schema_version"], facts["generator_version"], common_sha)
    if triple not in KNOWN_PACKAGE_ABIS:
        raise UnsupportedPackageVersion(
            "未知包生成器 ABI 组合：schema_version="
            f"{triple[0]}, generator_version={triple[1]}, "
            f"common_sha256={triple[2]}"
        )


def _exec_module(data: bytes, gen_path: Path) -> dict:
    """门 2 的 exec 半程：对同一份字节 exec。仅在版本门通过后调用。"""
    namespace = {
        "__name__": "oprunway_loaded_gen_csv",  # 避开模板末尾的 __main__ 守卫
        "__file__": str(gen_path),
    }
    try:
        code = compile(data, str(gen_path), "exec")
        exec(code, namespace)  # noqa: S102 —— 字节已过 SHA 允许列表
    except LoaderReject:
        raise
    except Exception as exc:  # 通用区顶层只应有定义与常量，任何异常都不该出现
        raise PackageMalformed(f"gen_csv.py exec 失败：{exc!r}") from exc
    return namespace


def _gate_abi_surface(namespace: dict, facts: dict) -> None:
    """门 1 补充断言：接口函数在场、通用区 GENERATOR_VERSION 与 FACTS 一致。

    SHA 命中允许列表后这两条理论上恒真；保留是为了抓允许列表登记错误
    （比如给某个 SHA 配错了版本号），失败同报 UNSUPPORTED_PACKAGE_VERSION。
    """
    for name in ("_column_specs", "_header_columns"):
        if not callable(namespace.get(name)):
            raise UnsupportedPackageVersion(f"包生成器缺少函数 {name}")
    generator_version = namespace.get("GENERATOR_VERSION")
    if generator_version != facts["generator_version"]:
        raise UnsupportedPackageVersion(
            f"通用区 GENERATOR_VERSION={generator_version!r} 与 "
            f"FACTS.generator_version={facts['generator_version']!r} 不一致"
        )


def _reject_duplicates(label: str, names: list) -> None:
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise HeaderMismatch(f"{label} 含重复列名：{duplicated}")


def _read_csv(csv_path: Path) -> tuple:
    """单次读取 CSV 字节；表头与摘要都从这同一份字节导出（F2 同字节纪律）。"""
    if not csv_path.is_file():
        raise PackageMalformed(f"任务包缺少 CSV：{csv_path.name}")
    try:
        raw = csv_path.read_bytes()
        header = next(csv.reader(io.StringIO(raw.decode("utf-8"))), None)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise PackageMalformed(
            f"读取 CSV 表头失败（{csv_path.name}）：{exc}"
        ) from exc
    if not header:
        raise PackageMalformed(f"CSV 无表头行：{csv_path.name}")
    return raw, header


def _gate_same_source(
    namespace: dict, facts: dict, csv_path: Path
) -> tuple:
    """门 3 · 同源断言：_column_specs 列名序列 == _header_columns == 实际 CSV 表头。

    含列序；任何一方出现重复列名即拒绝。失败即停，不生成。
    """
    try:
        raw_specs = namespace["_column_specs"](facts)
        raw_header = namespace["_header_columns"](facts)
    except LoaderReject:
        raise
    except Exception as exc:
        raise PackageMalformed(f"包内列投影执行失败：{exc!r}") from exc
    if not isinstance(raw_specs, list):
        raise PackageMalformed(
            f"_column_specs 返回形态非法：{type(raw_specs).__name__}（要求 list）"
        )
    if not isinstance(raw_header, (list, tuple)):
        raise PackageMalformed(
            f"_header_columns 返回形态非法：{type(raw_header).__name__}（要求 list）"
        )

    specs = []
    for position, spec in enumerate(raw_specs):
        if not isinstance(spec, dict) or not all(
            key in spec for key in _REQUIRED_SPEC_KEYS
        ):
            raise PackageMalformed(
                f"_column_specs 第 {position} 项缺 name/kind/source：{spec!r}"
            )
        if not isinstance(spec["name"], str) or not spec["name"]:
            raise PackageMalformed(
                f"_column_specs 第 {position} 项列名非法：{spec['name']!r}"
            )
        specs.append(dict(spec))

    spec_names = [spec["name"] for spec in specs]
    header = list(raw_header)
    csv_raw, csv_header = _read_csv(csv_path)
    _reject_duplicates("_column_specs 列名", spec_names)
    _reject_duplicates("_header_columns", header)
    _reject_duplicates(f"CSV 表头（{csv_path.name}）", csv_header)
    if not (spec_names == header == csv_header):
        raise HeaderMismatch(
            "同源断言失败：_column_specs / _header_columns / CSV 表头不一致。"
            f"specs={spec_names} header_fn={header} csv={csv_header}"
        )
    return specs, header, csv_raw


def check_contract_prescreen(facts: dict) -> None:
    """§4 拒绝矩阵的机械可判子集，命中即报 UNSUPPORTED_CONTRACT。

    覆盖三行：golden.kind 非 cblas（lapacke/loop/composed）、复数标量
    （参数 dtype 或 dtype_profiles.scalar_dtype 为 complex64/complex128）、
    nullable 参数。参数角色组合的封闭表与建设样本覆盖判定在下游 IR 编译
    （Step 2 冻结封闭角色表后实现），不在本函数；复数缓冲区（cherk 类，
    无复数标量也无 nullable 的假想包）同样落在那道下游门。
    """
    golden = facts.get("golden")
    if not isinstance(golden, dict):
        raise PackageMalformed(f"golden 缺失或不是字典：{golden!r}")
    kind = golden.get("kind")
    if kind in ("lapacke", "loop", "composed"):
        raise UnsupportedContract(
            f"golden.kind={kind!r} 超出本期生成能力（仅支持 cblas）"
        )
    if kind != "cblas":
        raise PackageMalformed(f"golden.kind 非法：{kind!r}")
    symbol = golden.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise PackageMalformed(
            "golden.kind=cblas 但 symbol 缺失（schema 要求必填）"
        )
    params = facts.get("params")
    if not isinstance(params, list) or not params:
        raise PackageMalformed("FACTS.params 缺失或为空")
    has_dynamic_scalar = False
    for param in params:
        if not isinstance(param, dict):
            raise PackageMalformed(f"params 项不是字典：{param!r}")
        name = param.get("name")
        nullable = param.get("nullable", False)
        if not isinstance(nullable, bool):
            raise PackageMalformed(f"参数 {name!r} 的 nullable 非布尔：{nullable!r}")
        if nullable:
            raise UnsupportedContract(
                f"参数 {name!r} 声明 nullable，本期不支持"
            )
        if param.get("role") in SCALAR_ROLES and param.get("dtype") in COMPLEX_DTYPES:
            raise UnsupportedContract(
                f"参数 {name!r} 是复数标量（{param['dtype']}），本期不支持"
            )
        if param.get("role") in SCALAR_ROLES and "dtype_from" in param:
            has_dynamic_scalar = True
    # 复数 profile 只有在存在动态标量（scalar 角色 + dtype_from）时才构成复数标量；
    # 只有动态复数 buffer 的包留给 H2 封闭角色表判定，不在此误拒（F3）。
    if has_dynamic_scalar:
        for profile in facts.get("dtype_profiles") or []:
            if (
                isinstance(profile, dict)
                and profile.get("scalar_dtype") in COMPLEX_DTYPES
            ):
                raise UnsupportedContract(
                    f"dtype_profile {profile.get('name')!r} 的 scalar_dtype 为复数，"
                    "本期不支持"
                )


def load_package_with_meta(package_dir) -> tuple:
    """装载一个 case-gen 任务包，过三道门与支持矩阵预筛。

    返回 (facts, column_specs, header, meta)：meta 含 gen_csv_sha256 与
    csv_sha256，均从通过门禁的同一份字节计算（F2：调用方不得重读文件另算）。
    任何拒绝都以 LoaderReject 子类抛出（.code 为停机码），不产出部分结果。
    """
    package_dir = Path(package_dir)
    gen_path = package_dir / "gen_csv.py"
    if not gen_path.is_file():
        raise PackageMalformed(
            f"任务包缺少 gen_csv.py：{package_dir}（形态 B 输入不在本方案）"
        )
    data = gen_path.read_bytes()  # 单次读取；此后 SHA / AST / exec 全用这份字节
    markers = _find_markers(data)
    common_sha = _common_region_sha256(data, markers)
    facts = _extract_facts(data, gen_path, markers)
    _gate_abi_version(facts, common_sha)  # 门 1：版本三元组
    namespace = _exec_module(data, gen_path)  # 门 2：同字节 exec（版本门之后）
    _gate_abi_surface(namespace, facts)  # 门 1 补充：函数在场、版本交叉核对
    op = facts.get("op")
    if not isinstance(op, str) or not op.isidentifier():
        raise PackageMalformed(f"FACTS.op 非法：{op!r}")
    csv_name = f"{op}_test.csv"
    column_specs, header, csv_raw = _gate_same_source(
        namespace, facts, package_dir / csv_name
    )  # 门 3：同源断言
    check_contract_prescreen(facts)  # 支持矩阵机械可判预筛
    meta = {
        "gen_csv_sha256": hashlib.sha256(data).hexdigest(),
        "csv_sha256": hashlib.sha256(csv_raw).hexdigest(),
        "csv_name": csv_name,
    }
    return facts, column_specs, header, meta


def load_package(package_dir) -> tuple[dict, list, list]:
    """load_package_with_meta 的三元组便捷形（meta 丢弃）。"""
    facts, column_specs, header, _meta = load_package_with_meta(package_dir)
    return facts, column_specs, header


def main(argv: list) -> int:
    """调试入口：python package_loader.py <package_dir>。拒绝时按 SKILL.md 约定
    打印原因、以 stderr 末行 STOP <停机码> 收尾并退出 2。"""
    if len(argv) != 2:
        print("用法：package_loader.py <任务包目录>", file=sys.stderr)
        return 2
    try:
        facts, column_specs, header = load_package(argv[1])
    except LoaderReject as exc:
        print(f"{exc.code}: {exc.reason}", file=sys.stderr)
        print(f"STOP {exc.code}", file=sys.stderr)
        return 2
    print(f"op={facts['op']} columns={len(header)}")
    print(",".join(header))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
