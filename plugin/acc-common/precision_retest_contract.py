"""CP-F 精度重测的确定性契约基础层。

本模块只负责 directive、relaxed spec、attempt 身份和原子工件；不执行 NPU、
不计算精度 metrics，也不产 pass/fail。裁决继续由 validator.py 与验收门负责。

⚠ **directive schema 是 breaking change，在途 attempt 必然失效，这是有意的 fail-closed。**
旧 `source_identity` 长这样：`{"pr_head", "build_receipt_sha256", "runner_form"}`，
`pr_head` 只被一条 `^[0-9a-f]{40,64}$` 正则校过。那条 40..64 的区间就是物理入口：
往 `pr_head` 里填一个 64 位摘要能原样通过校验，而 `_cpp_extension_base_binding` 当时
对基础收据的 `source.pr_head_sha` **一个字节都不校**——CP-F 复测链比它要复测的验收链还松。
现在来源判别一律走 `dut_source`：`pull_request` 恰 40 位 `pr_head_sha`、
`local_checkout` 恰 64 位 `local_root_digest`，`repo` 两条通路都必填。
所以旧 directive **不能**继续执行：它既没有 `repo`，也无法区分手里那串 hex 到底是线上
commit 还是本地子树摘要。重新起草 directive、重新 F2，比放行一份来源不可信的在途 attempt
便宜得多。

`repo` 的**实际校验范围**（别当成全通路都有的门）：`cpp_extension` 通路在
`materialize_attempt` 里核 `directive.source_identity.repo` 与首轮 build receipt 的
`runner_binding.base_source_repo` 逐字相等，不等即 BLOCKED；`cpp` / `aclnn_py` 通路的首轮
`execution_provenance` 里**根本没有仓名字段**，没有对照物可比，那两条通路的 `repo` 目前
只作人工可读记账。
"""

import copy
import datetime
import hashlib
import json
import math
import os
import re
import tempfile

import content_address
import dut_source
import precision_policy


SCHEMA_VERSION = 1
ATTEMPT_KINDS = frozenset({"same_policy_rerun", "relaxed_rerun", "replay_only"})
DIRECTIVE_STATUSES = frozenset({"drafted", "confirmed", "expired", "revoked"})
BASE_ARTIFACTS = (
    "spec", "caseset", "evidence", "verdict", "acceptance",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DIRECTIVE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_UTC_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_OVERRIDE_KEYS = frozenset({
    "standard", "tolerance", "error_rate", "threshold", "max_ratio", "eps",
    "rtol", "atol",
})
_OVERRIDE_KEYS_BY_STANDARD = {
    precision_policy.ASCENDOPTEST_DEFAULT:
        frozenset({"standard", "tolerance", "error_rate"}),
    precision_policy.ECOSYSTEM_MERE_MARE:
        frozenset({"standard", "threshold", "max_ratio", "eps"}),
    precision_policy.TORCH_ALLCLOSE:
        frozenset({"standard", "rtol", "atol"}),
}
# 锚字段名 → 首轮 `evidence.execution_provenance` 里承载它的**历史**键名。
# 这张表只做「改名」这一件事，**不重判来源**——来源判别只有 `dut_source` 一处。
# PR 通路刻意沿用历史键 `head_sha`：aclnn_py/cpp 两条通路的既有 evidence 都是这么写的，
# 改名会让所有历史产物一夜之间对不上，而那与本批要堵的来源伪装毫无关系。
_PROVENANCE_ANCHOR_KEY = {
    "pr_head_sha": "head_sha",
    "local_root_digest": "local_root_digest",
}
# F2 冻进 attempt 的首轮来源事实副本；名字与 fetch_source 产物一致，便于门直接消费。
_SOURCE_FACTS_NAME = "source_facts.json"
_RELAXED_SPEC_DOMAIN = "oprunway/precision-retest-relaxed-spec/v1"
_DIRECTIVE_DOMAIN = "oprunway/precision-retest-directive/v1"
_MANIFEST_DOMAIN = "oprunway/precision-retest-manifest/v1"
_RECEIPT_DOMAIN = "oprunway/precision-retest-receipt/v1"


class RetestContractError(ValueError):
    """CP-F 输入或工件不满足严格契约。"""


def _provenance_anchor_key(anchor_field, where):
    """锚字段名 → 首轮 `execution_provenance` 里承载它的键名；未登记即 fail-closed。

    ⚠ **不能写成裸下标 `_PROVENANCE_ANCHOR_KEY[anchor_field]`**。`dut_source` 是受控词表，
    哪天加进第三种来源通路（新的锚字段名）而这张表没跟着改，裸下标抛的是 `KeyError`；
    而 `cp_f_prepare_attempt.py` 只收 `(OSError, RetestContractError)`，`KeyError` 会穿过去
    变成裸 traceback——调用方拿不到约定的 `[CP-F prepare] BLOCKED: …` 单行机读契约，
    自动化那侧只看见一个非零退出码和一堆栈。转成 `RetestContractError` 才回到契约内。

    也不写 `.get(anchor_field, "head_sha")` 一类默认值：猜错键名会拿**另一条通路**的
    provenance 值去做等值校验，那正是本模块要堵的来源伪装。
    """
    try:
        return _PROVENANCE_ANCHOR_KEY[anchor_field]
    except KeyError as ex:
        raise RetestContractError(
            f"{where}: 来源锚字段 {anchor_field!r} 在 _PROVENANCE_ANCHOR_KEY 里没有登记对应的 "
            f"execution_provenance 键名（dut_source 扩了受控词表而 CP-F 未跟进）"
            f"——fail-closed，不猜键名") from ex


def _require_object(value, where):
    if not isinstance(value, dict):
        raise RetestContractError(f"{where} 须为对象，得 {type(value).__name__}")
    return value


def _require_sha256(value, where):
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RetestContractError(f"{where} 须为小写 sha256")
    return value


def _checked_number(value, where):
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise RetestContractError(f"{where} 须为有限非负数，得 {value!r}")
    return value


def sha256_file(path):
    """流式计算文件 SHA-256；拒绝非普通文件与符号链接。"""
    if os.path.islink(path) or not os.path.isfile(path):
        raise RetestContractError(f"待绑定工件须为普通文件且非符号链接: {path!r}")
    digest = hashlib.sha256()
    with open(path, "rb") as src:
        while True:
            block = src.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def fingerprint_base_artifacts(paths):
    """绑定首次验收五类基础工件，缺项或额外项均拒绝。"""
    _require_object(paths, "paths")
    expected = set(BASE_ARTIFACTS)
    actual = set(paths)
    if actual != expected:
        raise RetestContractError(
            f"基础工件键须严格等于 {sorted(expected)}，缺 {sorted(expected - actual)} "
            f"多 {sorted(actual - expected)}")
    return {name: {"path": os.path.abspath(os.fspath(paths[name])),
                   "sha256": sha256_file(paths[name])}
            for name in BASE_ARTIFACTS}


def verify_base_artifacts(directive, reports_dir):
    """复核 directive 绑定的基础工件仍位于本报告目录且字节未漂移。"""
    d = validate_directive(directive, require_confirmed=True)
    root = os.path.realpath(os.fspath(reports_dir))
    if not os.path.isdir(root) or os.path.islink(reports_dir):
        raise RetestContractError(f"reports_dir 须为真实目录且非符号链接: {reports_dir!r}")
    verified = {}
    for name in BASE_ARTIFACTS:
        recorded = d["base_artifacts"][name]
        absolute = os.path.abspath(recorded["path"])
        path = os.path.realpath(absolute)
        if path != absolute:
            raise RetestContractError(
                f"base_artifacts.{name}.path 含符号链接路径段: {absolute!r}")
        try:
            if os.path.commonpath((root, path)) != root:
                raise RetestContractError(
                    f"base_artifacts.{name}.path 逃逸 reports_dir: {path!r}")
        except RetestContractError:
            raise
        except ValueError as ex:
            raise RetestContractError(
                f"base_artifacts.{name}.path 与 reports_dir 不同卷") from ex
        actual = sha256_file(path)
        if actual != recorded["sha256"]:
            raise RetestContractError(
                f"drift_blocked:base_{name}_sha256_mismatch "
                f"recorded={recorded['sha256']} actual={actual}")
        verified[name] = {"path": path, "sha256": actual}
    return verified


def load_strict_json(path, where):
    """读取严格 JSON 对象，拒绝 NaN/Inf、坏编码和非对象顶层。"""
    try:
        with open(path, encoding="utf-8") as src:
            value = json.load(
                src,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    RetestContractError(f"{where}: 非法 JSON 常量 {token}")),
            )
    except RetestContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise RetestContractError(f"{where}: 无法读取 JSON: {ex}") from ex
    return _require_object(value, where)


def build_case_bindings(caseset, work_dir, case_ids):
    """从原 caseset 冻结 case 结构与实际输入文件字节。

    ``case_digest`` 绑定整条 case JSON；``input_sha256`` 绑定 runner 实际消费的
    ``work/<input.path>``。禁止缺 path、重复输入名、路径逃逸和符号链接。
    """
    cs = _require_object(caseset, "caseset")
    cases = cs.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RetestContractError("caseset.cases 须为非空列表")
    if (not isinstance(case_ids, list) or not case_ids
            or any(not isinstance(x, str) or not x for x in case_ids)
            or len(case_ids) != len(set(case_ids))):
        raise RetestContractError("case_ids 须为非空、无重复的非空字符串列表")
    by_id = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise RetestContractError(f"caseset.cases[{index}] 缺合法 id")
        if case["id"] in by_id:
            raise RetestContractError(f"caseset 有重复 case_id={case['id']!r}")
        by_id[case["id"]] = case
    missing = [cid for cid in case_ids if cid not in by_id]
    if missing:
        raise RetestContractError(f"directive case 不在原 caseset: {missing}")
    root = os.path.abspath(os.fspath(work_dir))
    if not os.path.isdir(root) or os.path.islink(root):
        raise RetestContractError(f"work_dir 须为真实目录且非符号链接: {work_dir!r}")
    bindings = {}
    for cid in case_ids:
        case = by_id[cid]
        inputs = case.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise RetestContractError(f"{cid}: inputs 须为非空列表")
        input_hashes = {}
        for index, inp in enumerate(inputs):
            if not isinstance(inp, dict):
                raise RetestContractError(f"{cid}: inputs[{index}] 须为对象")
            name, relative = inp.get("name"), inp.get("path")
            if not isinstance(name, str) or not name or name in input_hashes:
                raise RetestContractError(f"{cid}: input 名缺失或重复: {name!r}")
            if not isinstance(relative, str) or not relative:
                raise RetestContractError(f"{cid}.{name}: 缺 input.path")
            try:
                path = content_address.safe_path(root, relative)
            except content_address.ContentAddressError as ex:
                raise RetestContractError(f"{cid}.{name}: input.path 非法: {ex}") from ex
            input_hashes[name] = sha256_file(path)
        golden_hashes = {}
        expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
        golden_paths = []
        if isinstance(expected.get("golden_path"), str):
            golden_paths.append(expected["golden_path"])
        for output in expected.get("outputs") or []:
            if isinstance(output, dict) and isinstance(output.get("golden_path"), str):
                golden_paths.append(output["golden_path"])
        for relative in golden_paths:
            if relative in golden_hashes:
                continue
            try:
                path = content_address.safe_path(root, relative)
            except content_address.ContentAddressError as ex:
                raise RetestContractError(
                    f"{cid}: golden.path 非法: {ex}") from ex
            golden_hashes[relative] = sha256_file(path)
        if not golden_hashes:
            raise RetestContractError(f"{cid}: 缺可冻结的 golden 文件")
        bindings[cid] = {
            "case_digest": content_address.content_digest(
                "oprunway/precision-retest-case/v1", case),
            "input_sha256": input_hashes,
            "golden_sha256": golden_hashes,
        }
    return bindings


def prepare_attempt_inputs(directive, reports_dir):
    """执行 F2 的本地、无 compute 部分：基础工件复核 + case/input 冻结。"""
    d = validate_directive(directive, require_confirmed=True)
    base = verify_base_artifacts(d, reports_dir)
    caseset = load_strict_json(base["caseset"]["path"], "caseset")
    base_reports_dir = os.path.dirname(base["caseset"]["path"])
    work_dir = os.path.join(base_reports_dir, "work")
    bindings = build_case_bindings(caseset, work_dir, d["case_ids"])
    return {
        "base_artifacts": base,
        "base_reports_dir": base_reports_dir,
        "case_bindings": bindings,
    }


def _canonical_sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def publish_owner_lock(lock_path, owner):
    """完整 owner JSON 的单一原子发布；无空锁/半 JSON 可见窗口。"""
    lock = os.path.abspath(os.fspath(lock_path))
    parent = os.path.dirname(lock)
    if os.path.lexists(lock):
        raise RetestContractError("lock 已存在")
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(lock) + ".owner.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(owner, out, ensure_ascii=False, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(tmp, lock)
        except FileExistsError as ex:
            raise RetestContractError("lock 已存在") from ex
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _cpp_extension_base_binding(evidence, caseset, reports_dir, case_ids,
                                expected_kind):
    """从首次 cpp_extension 证据冻结可执行身份；不依赖已清理的远端绝对路径。

    `expected_kind` 是 directive 声明的来源通路，**不是形式主义参数**：不传它，
    「directive 说 `local_checkout`、基础收据说 `pull_request` 并填一个任意 40 位 hex」
    这条路就会走进 PR 分支，本地锚的等值校验**整条不执行**——vendor `.so` 与被测源码
    之间的机器可核对应关系就此消失。所以两边必须先确认说的是同一条通路，再按通路分支。
    """
    receipt = evidence.get("cpp_extension_receipt")
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "oprunway.cpp_extension_receipt"
            or receipt.get("schema_version") != 1
            or receipt.get("status") != "VERIFIED"):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_receipt_missing")
    work = os.path.join(os.path.realpath(os.fspath(reports_dir)), "work")
    receipt_path = os.path.join(work, "cpp_extension_receipt.json")
    plan_path = os.path.join(work, "cpp_extension_invocation_plan.json")
    manifest_path = os.path.join(work, "cpp_extension", "extension_manifest.json")
    disk_receipt = load_strict_json(receipt_path, "base cpp_extension receipt")
    plan = load_strict_json(plan_path, "base cpp_extension invocation plan")
    extension_manifest = load_strict_json(
        manifest_path, "base cpp_extension manifest")
    if disk_receipt != receipt:
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_receipt_file_mismatch")
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_bindings_missing")
    expected = {
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(extension_manifest),
        "invocation_plan_sha256": _canonical_sha(plan),
    }
    for key, value in expected.items():
        if bindings.get(key) != value:
            raise RetestContractError(
                f"drift_blocked:base_cpp_extension_{key}_mismatch")
    if plan.get("caseset_sha256") != expected["caseset_sha256"]:
        raise RetestContractError(
            "drift_blocked:base_invocation_plan_caseset_mismatch")
    rows = plan.get("cases")
    if not isinstance(rows, list):
        raise RetestContractError(
            "drift_blocked:base_invocation_plan_cases_missing")
    by_id = {}
    for row in rows:
        cid = row.get("case_id") if isinstance(row, dict) else None
        if not isinstance(cid, str) or not cid or cid in by_id:
            raise RetestContractError(
                "drift_blocked:base_invocation_plan_case_id_invalid")
        by_id[cid] = row
    missing = [cid for cid in case_ids if cid not in by_id]
    if missing:
        raise RetestContractError(
            f"drift_blocked:base_invocation_plan_missing_cases={missing}")
    vendor = receipt.get("vendor")
    build_receipt = vendor.get("build_receipt") if isinstance(vendor, dict) else None
    source = build_receipt.get("source") if isinstance(build_receipt, dict) else None
    runtime = receipt.get("runtime")
    if (not isinstance(vendor, dict) or not isinstance(build_receipt, dict)
            or not isinstance(source, dict) or not isinstance(runtime, dict)):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_provenance_incomplete")
    # 基础收据的来源锚校验**只此一处**，且必带 expected_kind（理由见函数 docstring）。
    # `repo` 的非空校验也由这里统一强制，本函数不再自己判一次。
    try:
        kind, anchor_field, anchor_value = dut_source.validate_build_receipt_source(
            source, expected_kind=expected_kind,
            where="base cpp_extension vendor build_receipt.source")
    except dut_source.DutSourceError as ex:
        raise RetestContractError(
            f"drift_blocked:base_vendor_build_source_anchor_invalid：{ex}") from ex
    build = build_receipt.get("build")
    if (not isinstance(build, dict)
            or not isinstance(build.get("argv"), list) or not build["argv"]
            or any(not isinstance(x, str) or not x for x in build["argv"])):
        raise RetestContractError(
            "drift_blocked:base_vendor_build_invocation_incomplete")
    if vendor.get("build_receipt_sha256") != _canonical_sha(build_receipt):
        raise RetestContractError(
            "drift_blocked:base_vendor_build_receipt_sha256_mismatch")
    receipt_digest = _canonical_sha(receipt)
    evidence_rows = evidence.get("evidence")
    cases = caseset.get("cases") or []
    if not isinstance(evidence_rows, list) or len(evidence_rows) != len(cases):
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_evidence_count_mismatch")
    if [row.get("case_id") if isinstance(row, dict) else None
            for row in evidence_rows] != [case.get("id") for case in cases]:
        raise RetestContractError(
            "drift_blocked:base_cpp_extension_evidence_case_sequence_mismatch")
    for row in evidence_rows:
        if (not isinstance(row, dict)
                or row.get("cpp_extension_receipt_sha256") != receipt_digest):
            raise RetestContractError(
                "drift_blocked:base_evidence_cpp_extension_receipt_mismatch")
    return {
        "schema": "oprunway.precision_retest.cpp_extension_binding",
        "schema_version": 1,
        "base_receipt_sha256": receipt_digest,
        "base_caseset_sha256": expected["caseset_sha256"],
        "base_manifest_sha256": expected["manifest_sha256"],
        "base_namespace": extension_manifest.get("namespace"),
        "base_invocation_plan_sha256": expected["invocation_plan_sha256"],
        "base_spec_sha256": bindings.get("spec_sha256"),
        # 整块记三元组，**不**另留一个「有值就用」的 base_pr_head 旧键：那等于把刚堵掉的
        # `.get(...) or .get(...)` 兜底放回来，同一段 hex 在两条通路里含义完全不同。
        "base_source_identity": {
            "dut_source": kind,
            "anchor_field": anchor_field,
            "anchor_value": anchor_value,
        },
        "base_build_receipt_sha256": vendor.get("build_receipt_sha256"),
        "base_vendor_build_argv": copy.deepcopy(
            build["argv"]),
        "base_source_repo": source.get("repo"),
        "base_vendor_elf_sha256": vendor.get("library_sha256"),
        "base_soc": runtime.get("soc"),
        "base_toolkit": runtime.get("cann_version"),
        "selected_invocations": [copy.deepcopy(by_id[cid]) for cid in case_ids],
    }


def _freeze_source_facts(base_reports_dir, kind, anchor_field, anchor_value):
    """定位首轮 `source_facts.json` 并核锚；返回 ``(doc, base_path)``，PR 缺席时 ``(None, None)``。

    **为什么非冻不可**：`validate_acceptance_state.gate_task2` →
    `_gate_build_receipt_source_binding` 在 `local_checkout` 且找不到 `source_facts.json`
    时按设计 BLOCKED，而 attempt 目录原本只复制 case 输入与 golden，**永远**不会有这份文件。
    也就是说本地通路的 CP-F 执行必 BLOCKED——不是偶发，是结构性缺口。所以 F2 就把首轮
    这份事实一并冻进 attempt，F3 再把冻结副本指给门。

    **缺席的处置按通路分**，与三级门保持同一条边界：

    | 通路 | 找不到 | 理由 |
    |---|---|---|
    | `local_checkout` | **BLOCKED** | 本地锚的全部可信度就来自它与 build receipt 的等值校验；没有对照物等于没绑定，而这是新通路、没有历史包袱 |
    | `pull_request` | 允许缺席 | 实测真机报告目录里本来就没有这份文件（取材 `--out` 与验收产物目录不同），硬要求会把现有 PR 通路整条打断 |

    读锚一律走 `dut_source.identity`：**不许**手翻 `local_checkout.root_digest`，更不能碰
    `local_checkout.git.head_sha`——后者只是「这份 checkout 当时停在哪个 commit」的信息字段，
    worktree 可能 dirty，它与被测字节没有绑定关系。
    """
    root = os.path.realpath(os.fspath(base_reports_dir))
    for candidate in (os.path.join(root, _SOURCE_FACTS_NAME),
                      os.path.join(root, "work", _SOURCE_FACTS_NAME)):
        if os.path.islink(candidate) or not os.path.isfile(candidate):
            continue
        doc = load_strict_json(candidate, "base source_facts")
        payload = doc.get("payload")
        facts = payload if isinstance(payload, dict) else doc
        try:
            identity_triple = dut_source.identity(
                facts, where="base source_facts")
        except dut_source.DutSourceError as ex:
            raise RetestContractError(
                f"drift_blocked:base_source_facts_anchor_invalid：{ex}") from ex
        if identity_triple != (kind, anchor_field, anchor_value):
            raise RetestContractError(
                f"drift_blocked:base_source_facts_anchor_mismatch "
                f"facts={identity_triple!r} "
                f"directive={(kind, anchor_field, anchor_value)!r}")
        return doc, candidate
    if kind == dut_source.LOCAL_CHECKOUT:
        raise RetestContractError(
            f"drift_blocked:base_source_facts_missing；dut_source={kind} 的 attempt 必须"
            f"冻结首轮 source_facts.json（找过 <报告目录>/ 与 <报告目录>/work/），"
            f"否则 F3 的 gate_task2 拿不到本地锚的对照物，必然 BLOCKED")
    return None, None


def materialize_attempt(directive, reports_dir, execution_identity):
    """原子创建一个准备完成的 attempt 目录，不执行 NPU。

    所有校验和工件构造先完成，最后才占用编号；占号后的各 JSON 都使用原子写。
    若写盘中断，目录因缺 ``attempt.receipt.json`` 保持为可识别的未完成现场。
    """
    d = validate_directive(directive, require_confirmed=True)
    prepared = prepare_attempt_inputs(d, reports_dir)
    spec = load_strict_json(prepared["base_artifacts"]["spec"]["path"], "base spec")
    caseset = load_strict_json(prepared["base_artifacts"]["caseset"]["path"], "caseset")
    evidence = load_strict_json(
        prepared["base_artifacts"]["evidence"]["path"], "base evidence")
    op = spec.get("op")
    if not isinstance(op, str) or not op or caseset.get("op") != op:
        raise RetestContractError(
            f"base spec/caseset op 不一致: spec={op!r}, caseset={caseset.get('op')!r}")
    runner_form = spec.get("runner_form") or "cpp"
    if runner_form != d["source_identity"]["runner_form"]:
        raise RetestContractError(
            f"drift_blocked:runner_form_mismatch directive="
            f"{d['source_identity']['runner_form']!r} spec={runner_form!r}")
    identity = copy.deepcopy(_require_object(execution_identity, "execution_identity"))
    required_identity = {
        "soc", "toolkit", "vendor_elf_sha256", "golden_source_sha256",
    }
    if set(identity) != required_identity:
        raise RetestContractError(
            f"execution_identity 键须严格等于 {sorted(required_identity)}")
    for key in ("soc", "toolkit"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise RetestContractError(f"execution_identity.{key} 须为非空字符串")
    for key in ("vendor_elf_sha256", "golden_source_sha256"):
        _require_sha256(identity.get(key), f"execution_identity.{key}")
    # directive 的来源三元组：kind 决定基础收据按哪条通路核，anchor_field 决定与首轮
    # evidence 对账时读哪个 provenance 键。判别只在这一处做，下面全部复用这三个值。
    # `validate_directive` 已经跑过同一个 helper，故这里不可能抛。
    directive_kind, directive_anchor_field, directive_anchor_value = (
        dut_source.validate_build_receipt_source(
            d["source_identity"], where="directive.source_identity"))
    anchor_provenance_key = _provenance_anchor_key(
        directive_anchor_field, "directive.source_identity")
    runner_binding = None
    base_provenance = evidence.get("execution_provenance")
    if runner_form == "cpp_extension":
        runner_binding = _cpp_extension_base_binding(
            evidence, caseset, prepared["base_reports_dir"], d["case_ids"],
            expected_kind=directive_kind)
        if runner_binding.get("base_spec_sha256") != _canonical_sha(spec):
            raise RetestContractError(
                "drift_blocked:base_cpp_extension_spec_sha256_mismatch")
        # 人工确认的仓名 ↔ 首轮 build receipt 自报的仓名，逐字对账。
        # **锚相等不蕴含仓相同**：`local_root_digest` 只覆盖 `op_subdir` 子树，fork、vendored
        # 目录、同一份代码换个仓名重开都能让两个不同的仓在该子树上字节全等；PR 通路的
        # head_sha 同样可以出现在 fork 里。所以 directive 里那句人工确认的 `repo` 必须真的
        # 参与校验，否则它只是一行没人核的自述——而模块 docstring 曾据此宣称有门。
        # ⚠ 只有 cpp_extension 有 `base_source_repo` 这个对照物；`cpp`/`aclnn_py` 的首轮
        #   `execution_provenance` 里没有仓名字段，那两条通路这里**没有**可比的东西，
        #   不许拿 `spec` 或本轮环境凑一个出来冒充首轮事实。
        directive_repo = d["source_identity"]["repo"]
        if directive_repo != runner_binding.get("base_source_repo"):
            raise RetestContractError(
                f"drift_blocked:base_source_repo_mismatch "
                f"directive={directive_repo!r} "
                f"base={runner_binding.get('base_source_repo')!r}")
        golden_source_path = os.path.join(
            os.path.dirname(prepared["base_artifacts"]["spec"]["path"]),
            "golden.py")
        golden_source_sha256 = sha256_file(golden_source_path)
        base_anchor = runner_binding["base_source_identity"]
        base_provenance = {
            # 键名按通路取（`_provenance_anchor_key`），值取基础收据自己的锚。
            # expected_kind 已保证两边同通路，故这里的键与 anchor_provenance_key 必然一致。
            # ⚠ 明知必然相等仍**独立算一次**，不复用上面那个变量：这一格的语义是「基础收据
            #   自报的锚该落在哪个键」，复用会让它悄悄改成「directive 说该落在哪个键」——
            #   将来 expected_kind 那道前置校验若被削弱，这里就成了无声的自证。
            _provenance_anchor_key(
                base_anchor["anchor_field"],
                "base cpp_extension vendor build_receipt.source"):
                base_anchor["anchor_value"],
            "soc": runner_binding["base_soc"],
            "toolkit_version": runner_binding["base_toolkit"],
            "build_receipt_sha256":
                runner_binding["base_build_receipt_sha256"],
            "vendor_elf_sha256": runner_binding["base_vendor_elf_sha256"],
            # cpp_extension 首轮 envelope 尚无该字段；改从基础 spec 同目录的
            # 授权 golden.py 实际字节取证，不接受本轮自报替代。
            "golden_source_sha256": golden_source_sha256,
        }
        runner_binding["base_golden_source_sha256"] = golden_source_sha256
    elif not isinstance(base_provenance, dict):
        raise RetestContractError(
            "drift_blocked:base_execution_provenance_missing；首次 evidence 未保存实际 "
            "来源锚/build/SoC/toolkit 身份，不能用本轮自报 identity 代替")
    elif anchor_provenance_key not in base_provenance:
        # aclnn_py / cpp 通路的 fail-closed：首轮 `execution_provenance` 里只有
        # `head_sha`——那是 aclnn_adapter 按 **PR ref** 取源核出来的线上 commit，
        # 对本地 checkout 通路毫无意义。缺本通路的锚就是没有可对账的首轮事实，
        # 必须显式 BLOCKED；**绝不**回退去读 head_sha 充数（那正是来源伪装的入口）。
        # 单靠下面的等值循环也会拒，但报出来是 `_mismatch`，读的人容易误以为
        # 「把 head_sha 抄过去就好了」——所以这里单独给出正确归因。
        raise RetestContractError(
            f"drift_blocked:base_execution_provenance_anchor_missing："
            f"dut_source={directive_kind} 需要 execution_provenance.{anchor_provenance_key}，"
            f"首次 evidence 实有键 {sorted(base_provenance)}")
    expected_provenance = {
        anchor_provenance_key: directive_anchor_value,
        "soc": identity["soc"],
        "toolkit_version": identity["toolkit"],
        "build_receipt_sha256": d["source_identity"]["build_receipt_sha256"],
    }
    for field, expected in expected_provenance.items():
        actual = base_provenance.get(field)
        if actual != expected:
            raise RetestContractError(
                f"drift_blocked:{field}_mismatch base={actual!r} expected={expected!r}")
    # vendor ELF/golden source 需要基础 evidence 提供真实 hash；旧产物缺字段时明确 BLOCKED，
    # 不把本轮 execution_identity 的值当作首次事实。
    for field in ("vendor_elf_sha256", "golden_source_sha256"):
        actual = base_provenance.get(field)
        if actual != identity[field]:
            raise RetestContractError(
                f"drift_blocked:{field}_mismatch base={actual!r} "
                f"expected={identity[field]!r}")
    source_facts_doc, source_facts_path = _freeze_source_facts(
        prepared["base_reports_dir"], directive_kind,
        directive_anchor_field, directive_anchor_value)
    manifest = build_attempt_manifest(
        d, prepared["case_bindings"], identity, runner_binding,
        os.path.realpath(os.fspath(reports_dir)),
        source_facts=(None if source_facts_doc is None else {
            "base_path": source_facts_path,
            "attempt_relpath": _SOURCE_FACTS_NAME,
            "sha256": _canonical_sha(source_facts_doc),
        }))
    relaxed = (derive_relaxed_spec(spec, d)
               if d["attempt_kind"] == "relaxed_rerun" else None)
    attempts_root = os.path.join(prepared["base_reports_dir"], "attempts")
    directive_artifact = make_directive_artifact(d)
    number, attempt_dir, reused = _allocate_idempotent_attempt(
        attempts_root, directive_artifact, manifest, relaxed,
        source_facts_doc=source_facts_doc)
    if reused:
        return {"attempt": number, "attempt_dir": attempt_dir,
                "manifest": manifest, "relaxed_spec": relaxed,
                "idempotent_reuse": True}
    return {"attempt": number, "attempt_dir": attempt_dir,
            "manifest": manifest, "relaxed_spec": relaxed}


def validate_directive(directive, *, require_confirmed=False):
    """严格校验人工 directive，并返回深拷贝。

    本函数只校结构和受控词表；case 是否属于原 caseset、基础 hash 是否匹配由准备门校。
    """
    d = copy.deepcopy(_require_object(directive, "directive"))
    allowed = {
        "schema_version", "directive_id", "directive_status", "attempt_kind",
        "case_ids", "base_artifacts", "source_identity", "human_instruction",
        "confirmed_by", "confirmed_at", "precision_override",
    }
    unknown = set(d) - allowed
    if unknown:
        raise RetestContractError(f"directive 含未知字段: {sorted(unknown)}")
    if d.get("schema_version") != SCHEMA_VERSION:
        raise RetestContractError(f"directive.schema_version 须为 {SCHEMA_VERSION}")
    did = d.get("directive_id")
    if not isinstance(did, str) or _DIRECTIVE_ID_RE.fullmatch(did) is None:
        raise RetestContractError("directive_id 须为 1..128 位小写字母数字及 ._-")
    status = d.get("directive_status")
    if status not in DIRECTIVE_STATUSES:
        raise RetestContractError(f"未知 directive_status={status!r}")
    if require_confirmed and status != "confirmed":
        raise RetestContractError("执行前 directive_status 必须为 confirmed")
    kind = d.get("attempt_kind")
    if kind not in ATTEMPT_KINDS:
        raise RetestContractError(f"未知 attempt_kind={kind!r}")
    case_ids = d.get("case_ids")
    if (not isinstance(case_ids, list) or not case_ids
            or any(not isinstance(x, str) or not x for x in case_ids)
            or len(case_ids) != len(set(case_ids))):
        raise RetestContractError("case_ids 须为非空、无重复的非空字符串列表")
    base = _require_object(d.get("base_artifacts"), "base_artifacts")
    if set(base) != set(BASE_ARTIFACTS):
        raise RetestContractError(f"base_artifacts 键须严格等于 {sorted(BASE_ARTIFACTS)}")
    for name in BASE_ARTIFACTS:
        item = _require_object(base[name], f"base_artifacts.{name}")
        if (set(item) != {"path", "sha256"}
                or not isinstance(item.get("path"), str)
                or not os.path.isabs(item["path"])):
            raise RetestContractError(f"base_artifacts.{name} 须仅含 path/sha256")
        _require_sha256(item.get("sha256"), f"base_artifacts.{name}.sha256")
    source = _require_object(d.get("source_identity"), "source_identity")
    # 来源锚的判别与长度校验**只此一处**，且只能由 `dut_source` 出：
    # PR 恰 40 位 `pr_head_sha`、本地恰 64 位 `local_root_digest`。旧的 40..64 区间正则
    # 已删——它让「叫 pr_head 却装 64 位摘要」原样通过，是本批要堵的那个洞。
    try:
        # 变量刻意叫 source_kind：本函数里 `kind` 已经是 attempt_kind，重名会把
        # 下面「只有 relaxed_rerun 可带 precision_override」那道校验悄悄改判。
        source_kind, anchor_field, _anchor_value = (
            dut_source.validate_build_receipt_source(
                source, where="directive.source_identity"))
    except dut_source.DutSourceError as ex:
        raise RetestContractError(f"source_identity 来源锚不合法：{ex}") from ex
    # `repo` 是本批新增的必填，旧 directive 因此失效（见模块 docstring）。
    # ⚠ 本函数只校它非空——**对账不在这里**：`cpp_extension` 通路由 `materialize_attempt`
    # 与 `runner_binding.base_source_repo` 逐字比；`cpp`/`aclnn_py` 没有可比的对照物。
    required_source = {"repo", "build_receipt_sha256", "runner_form", anchor_field}
    if set(source) - {"dut_source"} != required_source:
        raise RetestContractError(
            f"source_identity(dut_source={source_kind}) 键须严格等于 "
            f"{sorted(required_source)}（另可选 dut_source），实得 {sorted(source)}")
    _require_sha256(source.get("build_receipt_sha256"),
                    "source_identity.build_receipt_sha256")
    if source.get("runner_form") not in ("cpp", "aclnn_py", "cpp_extension"):
        raise RetestContractError("source_identity.runner_form 不受支持")
    if not isinstance(d.get("human_instruction"), str) or not d["human_instruction"].strip():
        raise RetestContractError("human_instruction 须为非空字符串")
    if status == "confirmed":
        if not isinstance(d.get("confirmed_by"), str) or not d["confirmed_by"].strip():
            raise RetestContractError("confirmed directive 必须记录 confirmed_by")
        if (not isinstance(d.get("confirmed_at"), str)
                or _UTC_TIME_RE.fullmatch(d["confirmed_at"]) is None):
            raise RetestContractError(
                "confirmed directive 的 confirmed_at 须为 UTC ISO 8601（末尾 Z）")
    override = d.get("precision_override")
    if kind == "relaxed_rerun":
        _validate_override(override)
    elif override is not None:
        raise RetestContractError(
            f"{kind} 不得携带 precision_override；政策变化只允许 relaxed_rerun")
    if kind == "replay_only" and status == "confirmed":
        # replay 的 policy/digest 等值由准备门对原 case 逐项校；这里确保它没有政策入口。
        if "precision_override" in d and d["precision_override"] is not None:
            raise RetestContractError("replay_only 不得改变精度政策")
    return d


def _validate_override(override):
    value = _require_object(override, "precision_override")
    unknown = set(value) - _OVERRIDE_KEYS
    if unknown:
        raise RetestContractError(f"precision_override 含越权字段: {sorted(unknown)}")
    if not value:
        raise RetestContractError("relaxed_rerun 的 precision_override 不得为空")
    standard = value.get("standard")
    if standard is None:
        raise RetestContractError(
            "relaxed_rerun 必须显式给出 precision_override.standard")
    if standard not in precision_policy.STANDARDS:
        raise RetestContractError(f"未知 precision standard={standard!r}")
    allowed = _OVERRIDE_KEYS_BY_STANDARD.get(standard)
    if allowed is None:
        raise RetestContractError(
            f"precision standard={standard!r} 没有可放宽的数值政策")
    incompatible = set(value) - allowed
    if incompatible:
        raise RetestContractError(
            f"precision standard={standard!r} 不接受字段: "
            f"{sorted(incompatible)}")
    numeric = set(value) - {"standard"}
    if not numeric:
        raise RetestContractError(
            "relaxed_rerun 必须至少给出一个实际数值字段，禁止仅改 standard/no-op")
    for key in _OVERRIDE_KEYS - {"standard"}:
        if key in value:
            _checked_number(value[key], f"precision_override.{key}")
    return value


def derive_relaxed_spec(base_spec, directive):
    """从原 spec + confirmed directive 确定性派生完整 relaxed spec。

    只允许写入 ``precision.acceptance_policy``。原平台 standard、oracle、覆盖轴及其它
    spec 字段保持不变，使 validator 仍可从 spec 复算 canonical acceptance。
    """
    base = copy.deepcopy(_require_object(base_spec, "base_spec"))
    d = validate_directive(directive, require_confirmed=True)
    if d["attempt_kind"] != "relaxed_rerun":
        raise RetestContractError("只有 relaxed_rerun 可派生 relaxed spec")
    precision = base.get("precision")
    if precision is None:
        precision = {}
        base["precision"] = precision
    if not isinstance(precision, dict):
        raise RetestContractError("base_spec.precision 须为对象")
    override = copy.deepcopy(d["precision_override"])
    base_standard = precision.get("standard")
    target_standard = override["standard"]
    if base_standard != target_standard:
        required = {
            precision_policy.ASCENDOPTEST_DEFAULT: {"tolerance", "error_rate"},
            precision_policy.ECOSYSTEM_MERE_MARE:
                {"threshold", "max_ratio", "eps"},
            precision_policy.TORCH_ALLCLOSE: {"rtol", "atol"},
        }[target_standard]
        missing = required - set(override)
        if missing:
            raise RetestContractError(
                f"跨 precision family override={target_standard!r} 必须完整给出 "
                f"{sorted(required)}，缺 {sorted(missing)}")
    precision["acceptance_policy"] = override
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "directive_id": d["directive_id"],
        "base_spec_sha256": content_address.content_digest(
            "oprunway/spec-json/v1", base_spec),
        "override_diff": {"precision.acceptance_policy": override},
    }
    base["precision_retest"] = provenance
    return content_address.make_artifact(_RELAXED_SPEC_DOMAIN, base)


def make_directive_artifact(directive):
    return content_address.make_artifact(
        _DIRECTIVE_DOMAIN, validate_directive(directive))


def allocate_attempt(attempts_root):
    """原子占用下一个四位 attempt 目录；并发者通过 mkdir 的原子性竞争。"""
    root = os.path.abspath(os.fspath(attempts_root))
    os.makedirs(root, exist_ok=True)
    for number in range(1, 10000):
        name = f"{number:04d}"
        path = os.path.join(root, name)
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        return name, path
    raise RetestContractError("attempt 编号已耗尽（0001..9999）")


def _allocate_idempotent_attempt(attempts_root, directive_artifact,
                                 manifest_artifact, relaxed_artifact,
                                 source_facts_doc=None):
    """受目录锁保护地按 directive_id 幂等分配；同 ID 异内容拒绝。

    `source_facts_doc` 与 relaxed spec 同等对待：它是准备完成的一部分，必须在占号后、
    `preparation.json` 之前一起落盘；复用时缺文件或内容不等一律按「未完成准备现场」拒。
    """
    root = os.path.abspath(os.fspath(attempts_root))
    os.makedirs(root, exist_ok=True)
    lock = os.path.join(root, ".allocation.lock")
    owner = {
        "schema_version": 1,
        "status": "running",
        "pid": os.getpid(),
        "operation": "allocate_precision_retest_attempt",
        "started_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "directive_digest": directive_artifact["digest"],
        "manifest_digest": manifest_artifact["digest"],
    }
    try:
        publish_owner_lock(lock, owner)
    except RetestContractError as ex:
        raise RetestContractError(
            "attempt allocation 正由另一 owner 执行；请稍后重试") from ex
    try:
        wanted = directive_artifact["payload"]["directive_id"]
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not re.fullmatch(r"\d{4}", name):
                continue
            if os.path.islink(path) or not os.path.isdir(path) \
                    or os.path.realpath(path) != os.path.abspath(path) \
                    or os.path.commonpath((root, os.path.realpath(path))) != root:
                raise RetestContractError(
                    f"numeric attempt entry 非受控真实目录: {name}")
            dpath = os.path.join(path, "directive.json")
            mpath = os.path.join(path, "attempt.manifest.json")
            if not os.path.isfile(dpath):
                continue
            existing_d = load_strict_json(dpath, "existing directive")
            payload = existing_d.get("payload") if isinstance(existing_d, dict) else None
            if not isinstance(payload, dict) or payload.get("directive_id") != wanted:
                continue
            if not os.path.isfile(mpath):
                raise RetestContractError(
                    f"directive_id={wanted!r} 已存在半写 attempt={name}")
            existing_m = load_strict_json(mpath, "existing manifest")
            if existing_d != directive_artifact or existing_m != manifest_artifact:
                raise RetestContractError(
                    f"directive_id={wanted!r} 已绑定不同内容，拒绝复用")
            prep = os.path.join(path, "preparation.json")
            relaxed_path = os.path.join(path, "spec.relaxed.json")
            facts_path = os.path.join(path, _SOURCE_FACTS_NAME)
            if not os.path.isfile(prep) or (
                    relaxed_artifact is not None
                    and (not os.path.isfile(relaxed_path)
                         or load_strict_json(relaxed_path, "existing relaxed spec")
                         != relaxed_artifact)) or (
                    source_facts_doc is not None
                    and (not os.path.isfile(facts_path)
                         or load_strict_json(facts_path, "existing source_facts")
                         != source_facts_doc)):
                raise RetestContractError(
                    f"directive_id={wanted!r} 命中未完成准备现场 {name}")
            return name, path, True
        name, path = allocate_attempt(root)
        content_address.atomic_write_json(
            path, "directive.json", directive_artifact)
        content_address.atomic_write_json(
            path, "attempt.manifest.json", manifest_artifact)
        if relaxed_artifact is not None:
            content_address.atomic_write_json(
                path, "spec.relaxed.json", relaxed_artifact)
        if source_facts_doc is not None:
            content_address.atomic_write_json(
                path, _SOURCE_FACTS_NAME, source_facts_doc)
        content_address.atomic_write_json(
            path, "preparation.json", {
                "schema_version": SCHEMA_VERSION,
                "attempt": name,
                "status": "prepared",
                "acceptance_verdict": None,
                "note": "仅完成 CP-F 本地准备与冻结；尚未执行 NPU、尚无重测裁决",
            })
        return name, path, False
    finally:
        if os.path.isfile(lock) and not os.path.islink(lock):
            os.unlink(lock)


def recover_stale_lock(lock_path, attempts_root, expected_digest, operation):
    """显式恢复协议：只处理已死 owner，先原子标 abandoned；绝不按 mtime 猜锁。

    调用者必须提供预期 digest/operation；execute 锁还要求 attempt 无最终 receipt。
    """
    lock = os.path.abspath(os.fspath(lock_path))
    root = os.path.realpath(os.fspath(attempts_root))
    real_parent = os.path.realpath(os.path.dirname(lock))
    if os.path.commonpath((root, real_parent)) != root:
        raise RetestContractError("lock 逃逸受控 attempts root")
    owner_path = (os.path.join(lock, "owner.json")
                  if os.path.isdir(lock) else lock)
    owner = load_strict_json(owner_path, "lock owner")
    if (owner.get("status") != "running"
            or owner.get("operation") != operation
            or expected_digest not in (
                owner.get("manifest_digest"), owner.get("directive_digest"))):
        raise RetestContractError("lock owner operation/digest 不匹配")
    pid = owner.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise RetestContractError("lock owner pid 非法")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    except PermissionError as ex:
        raise RetestContractError("无法证明 lock owner 已退出") from ex
    else:
        raise RetestContractError("lock owner 仍存活，拒绝恢复")
    if operation == "execute_precision_attempt" \
            and os.path.lexists(os.path.join(os.path.dirname(lock),
                                             "attempt.receipt.json")):
        raise RetestContractError("已有 final receipt，拒绝恢复 execute lock")
    abandoned = lock + ".abandoned." + expected_digest[:12]
    if os.path.lexists(abandoned):
        raise RetestContractError("abandoned 标记已存在，拒绝覆盖")
    os.replace(lock, abandoned)
    return abandoned


def build_attempt_manifest(directive, case_bindings, execution_identity,
                           runner_binding=None, base_artifact_scope=None,
                           source_facts=None):
    """构造冻结 manifest；不读取/猜测 case，调用方必须传入已核验绑定。

    `source_facts` 只记「从哪来 + 冻结副本叫什么 + 内容 sha256」。sha256 进了 manifest
    payload，而 manifest 自身是内容寻址 envelope，所以 F3 只需比对文件摘要即可确认这份
    事实没被换过——不必也不应在执行侧再判一次来源。
    """
    d = validate_directive(directive, require_confirmed=True)
    bindings = _require_object(case_bindings, "case_bindings")
    if set(bindings) != set(d["case_ids"]):
        raise RetestContractError(
            f"case_bindings 与 directive.case_ids 不一致：缺 "
            f"{sorted(set(d['case_ids']) - set(bindings))} 多 "
            f"{sorted(set(bindings) - set(d['case_ids']))}")
    for cid, binding in bindings.items():
        item = _require_object(binding, f"case_bindings.{cid}")
        if not isinstance(item.get("case_digest"), str):
            raise RetestContractError(f"{cid}: 缺 case_digest")
        _require_sha256(item["case_digest"], f"{cid}.case_digest")
        inputs = item.get("input_sha256")
        if not isinstance(inputs, dict) or not inputs:
            raise RetestContractError(f"{cid}: input_sha256 须为非空对象")
        for name, digest in inputs.items():
            if not isinstance(name, str) or not name:
                raise RetestContractError(f"{cid}: input 名非法")
            _require_sha256(digest, f"{cid}.input_sha256.{name}")
        goldens = item.get("golden_sha256")
        if not isinstance(goldens, dict) or not goldens:
            raise RetestContractError(f"{cid}: golden_sha256 须为非空对象")
        for path, digest in goldens.items():
            if not isinstance(path, str) or not path:
                raise RetestContractError(f"{cid}: golden 路径非法")
            _require_sha256(digest, f"{cid}.golden_sha256.{path}")
    identity = copy.deepcopy(_require_object(execution_identity, "execution_identity"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "directive_id": d["directive_id"],
        "attempt_kind": d["attempt_kind"],
        "planned_case_ids": list(d["case_ids"]),
        "base_artifacts": copy.deepcopy(d["base_artifacts"]),
        "source_identity": copy.deepcopy(d["source_identity"]),
        "case_bindings": copy.deepcopy(bindings),
        "execution_identity": identity,
    }
    if runner_binding is not None:
        payload["runner_binding"] = copy.deepcopy(
            _require_object(runner_binding, "runner_binding"))
    if base_artifact_scope is not None:
        scope = os.path.realpath(os.fspath(base_artifact_scope))
        if not os.path.isabs(scope) or not os.path.isdir(scope):
            raise RetestContractError(
                "base_artifact_scope 须为存在的绝对目录")
        payload["base_artifact_scope"] = scope
    if source_facts is not None:
        facts = _require_object(source_facts, "source_facts")
        if set(facts) != {"base_path", "attempt_relpath", "sha256"}:
            raise RetestContractError(
                "source_facts 键须严格等于 ['attempt_relpath', 'base_path', 'sha256']")
        if facts["attempt_relpath"] != _SOURCE_FACTS_NAME:
            raise RetestContractError(
                f"source_facts.attempt_relpath 须为 {_SOURCE_FACTS_NAME!r}")
        if not isinstance(facts["base_path"], str) or not os.path.isabs(facts["base_path"]):
            raise RetestContractError("source_facts.base_path 须为绝对路径")
        _require_sha256(facts["sha256"], "source_facts.sha256")
        payload["source_facts"] = copy.deepcopy(facts)
    return content_address.make_artifact(_MANIFEST_DOMAIN, payload)


def build_completion_receipt(manifest_artifact, outputs, gate):
    """构造完成 sentinel；只有 gate passed 且输出摘要齐全才允许生成。"""
    artifact = _require_object(manifest_artifact, "manifest_artifact")
    if artifact.get("domain") != _MANIFEST_DOMAIN:
        raise RetestContractError("manifest domain 不匹配")
    manifest_digest = _require_sha256(artifact.get("digest"), "manifest.digest")
    if artifact.get("schema_version") != 1 or "payload" not in artifact:
        raise RetestContractError("manifest artifact envelope 不完整")
    actual_manifest_digest = content_address.content_digest(
        _MANIFEST_DOMAIN, artifact["payload"])
    if manifest_digest != actual_manifest_digest:
        raise RetestContractError("manifest artifact 摘要不匹配")
    out = _require_object(outputs, "outputs")
    required = {"evidence_sha256", "verdict_sha256", "result_sha256"}
    if set(out) != required:
        raise RetestContractError(f"outputs 键须严格等于 {sorted(required)}")
    for key in required:
        _require_sha256(out[key], f"outputs.{key}")
    gate_obj = _require_object(gate, "gate")
    if gate_obj.get("passed") is not True or gate_obj.get("errors") not in ({}, None):
        raise RetestContractError("gate 未干净通过，不得生成完成 receipt")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": manifest_digest,
        "outputs": copy.deepcopy(out),
        "gate": {"passed": True, "errors": {}},
        "lifecycle": "completed",
    }
    return content_address.make_artifact(_RECEIPT_DOMAIN, payload)


def build_attempt_receipt(manifest_artifact, outputs, gate, completed_at):
    """构造“执行已结束”收据；gate 可通过或失败，均不等于验收 PASS。"""
    artifact = _require_object(manifest_artifact, "manifest_artifact")
    if artifact.get("domain") != _MANIFEST_DOMAIN:
        raise RetestContractError("manifest domain 不匹配")
    digest = _require_sha256(artifact.get("digest"), "manifest.digest")
    if artifact.get("schema_version") != 1 or "payload" not in artifact:
        raise RetestContractError("manifest artifact envelope 不完整")
    if digest != content_address.content_digest(
            _MANIFEST_DOMAIN, artifact["payload"]):
        raise RetestContractError("manifest artifact 摘要不匹配")
    out = _require_object(outputs, "outputs")
    required = {"evidence_sha256", "verdict_sha256", "result_sha256"}
    if set(out) != required:
        raise RetestContractError(f"outputs 键须严格等于 {sorted(required)}")
    for key in required:
        _require_sha256(out[key], f"outputs.{key}")
    gate_obj = _require_object(gate, "gate")
    if not isinstance(gate_obj.get("passed"), bool):
        raise RetestContractError("gate.passed 须为 bool")
    errors = gate_obj.get("errors")
    if not isinstance(errors, dict):
        raise RetestContractError("gate.errors 须为对象")
    if gate_obj["passed"] != (not errors):
        raise RetestContractError("gate.passed 与 gate.errors 是否为空矛盾")
    if not isinstance(completed_at, str) or _UTC_TIME_RE.fullmatch(completed_at) is None:
        raise RetestContractError("completed_at 须为 UTC ISO 8601（末尾 Z）")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": digest,
        "outputs": copy.deepcopy(out),
        "gate": copy.deepcopy(gate_obj),
        "completed_at": completed_at,
        "lifecycle": "completed",
        "acceptance_verdict": None,
    }
    return content_address.make_artifact(_RECEIPT_DOMAIN, payload)
