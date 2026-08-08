#!/usr/bin/env python3
"""正式验收产物与未完成 attempt 的最小确定性边界。

本模块只回答「这份由既有确定性链算出的候选状态能否使用正式文件名」，不计算精度、
性能或证据完整性结论。既有 canonical state 映射也提升到这里成为唯一共享实现，
``run_workflow`` 只保留对象别名；renderer 不重抄任何状态关系。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from contextlib import contextmanager

import fcntl
import artifact_path_guard


ATTEMPT_RECORD_FILE = "attempt_record.json"
FORMAL_ACCEPTANCE_FILE = "acceptance.json"
ATTEMPT_RECORD_SCHEMA = "oprunway.workflow_attempt_record"
ATTEMPT_RECORD_SCHEMA_VERSION = 2
PRE_EXECUTION_TERMINAL_FILE = "pre_execution_terminal.json"
PRE_EXECUTION_RESERVED_FILES = (
    PRE_EXECUTION_TERMINAL_FILE,
    "vendor_build_attempt.json",
    "前置执行失败明细.md",
)
ARTIFACT_LOCK_FILE = ".oprunway-artifacts.lock"
ARTIFACT_TEMP_PREFIX = ".oprunway-artifact-tmp-"
LEGACY_ARTIFACT_TEMP_FILES = (
    "验收报告.md.tmp",
    "历史验收报告（只读）.md.tmp",
    "精度失败明细.md.tmp",
    "性能失败明细.md.tmp",
)

# 既有 run_workflow 人读 overall → canonical state 实现的唯一真源。公开常量供 workflow
# 保留兼容别名；正式发布门与 workflow 由此使用同一张关系表，而不是各维护一份白名单。
MEASURED_ONLY_OVERALL = "PASS(性能仅实测未裁决)"
MEASURED_ONLY_STATE = "PASSED_PRECISION_PERF_MEASURED_ONLY"
MEASURE_INCOMPLETE_OVERALL = "BLOCKED(measure_only 性能实测未完成)"
MEASURE_INCOMPLETE_STATE = "BLOCKED_PERF_MEASUREMENT_INCOMPLETE"
BLOCKED_WAIT_REAL_BASELINE_STATUS = "blocked_wait_real_baseline"
BLOCKED_WAIT_REAL_BASELINE_STATE = "BLOCKED_WAIT_REAL_BASELINE"

CANONICAL_STATE_BY_OVERALL = {
    "PASS": "PASSED",
    "PASS(无性能要求)": "PASSED",
    MEASURED_ONLY_OVERALL: MEASURED_ONLY_STATE,
    MEASURE_INCOMPLETE_OVERALL: MEASURE_INCOMPLETE_STATE,
    "FAIL(精度)": "FAILED_PRECISION",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "PASSED_WITH_RISK": "PASSED_WITH_RISK",
    "PASSED_WITH_GAPS": "PASSED_WITH_GAPS",
    "BLOCKED_GOLDEN_UNAUTHORIZED": "BLOCKED_GOLDEN_UNAUTHORIZED",
    "BLOCKED_GOLDEN_UNAVAILABLE": "BLOCKED_GOLDEN_UNAVAILABLE",
    "BLOCKED_WAIT_GPU_BENCHMARK": "BLOCKED_WAIT_GPU_BENCHMARK",
    BLOCKED_WAIT_REAL_BASELINE_STATE: BLOCKED_WAIT_REAL_BASELINE_STATE,
    "BLOCKED_INCOMPARABLE_TIMING_SCOPE": "BLOCKED_INCOMPARABLE_TIMING_SCOPE",
    "BLOCKED_GPU_BASELINE_INVALID": "BLOCKED_GPU_BASELINE_INVALID",
}


class FormalAcceptanceError(RuntimeError):
    """候选状态不允许使用正式验收产物名。"""


class ArtifactNameConflictError(FormalAcceptanceError):
    """正式总结与 attempt 总结本应互斥，但目标目录已有另一种文件名。"""


def _assert_execution_identity(acceptance):
    identity = acceptance.get("execution_identity") if isinstance(acceptance, dict) else None
    binding = identity.get("source_binding") if isinstance(identity, dict) else None
    candidate = binding.get("candidate") if isinstance(binding, dict) else None
    public_op = identity.get("public_op") if isinstance(identity, dict) else None
    kernel_op = identity.get("kernel_op_type") if isinstance(identity, dict) else None
    if (public_op != acceptance.get("op")
            or not isinstance(kernel_op, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", kernel_op) is None
            or identity.get("resolution") not in {
                "derived_exact_source_candidate", "explicit_spec_binding"}
            or not isinstance(binding, dict)
            or binding.get("schema") != "oprunway.kernel_identity_spec_binding"
            or binding.get("schema_version") != 1
            or re.fullmatch(r"[0-9a-f]{64}", binding.get("identity_sha256") or "") is None
            or not isinstance(candidate, dict)
            or candidate.get("kernel_op_type") != kernel_op
            or not isinstance(candidate.get("source_path"), str)
            or re.fullmatch(r"[0-9a-f]{64}",
                            candidate.get("source_sha256") or "") is None
            or not isinstance(candidate.get("line"), int)
            or isinstance(candidate.get("line"), bool)
            or candidate.get("line", 0) < 1):
        raise FormalAcceptanceError(
            "正式 acceptance.execution_identity 缺失或未绑定 public op、source fact 与 kernel op type")


def _is_incomplete_label(value):
    """只识别状态机的未完成标签形态，不枚举可发布终态。"""
    return (not isinstance(value, str) or not value
            or value == "NEEDS_REVIEW" or value.startswith("BLOCKED"))


def canonical_state(overall, perf_summary):
    """既有人读 overall → canonical state 的唯一实现。

    ``perf_summary`` 沿用 workflow 的 summary object 接口；artifact renderer 的候选只有
    ``perf_status`` 时传 ``{"status": ...}``。未知 overall 保持既有 fail-closed 语义，
    落 ``NEEDS_REVIEW``，因此不能进入正式发布。
    """
    if isinstance(overall, str) and overall in CANONICAL_STATE_BY_OVERALL:
        return CANONICAL_STATE_BY_OVERALL[overall]
    status = perf_summary.get("status") if isinstance(perf_summary, dict) else None
    if status == "blocked_incomparable_timing_scope":
        return "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
    if status == "blocked_gpu_baseline_invalid":
        return "BLOCKED_GPU_BASELINE_INVALID"
    if status == "blocked_wait_gpu_benchmark":
        return "BLOCKED_WAIT_GPU_BENCHMARK"
    if status == BLOCKED_WAIT_REAL_BASELINE_STATUS:
        return BLOCKED_WAIT_REAL_BASELINE_STATE
    if isinstance(overall, str) and overall.startswith("性能未达成"):
        return "FAILED_PERFORMANCE"
    if isinstance(overall, str) and overall.startswith("BLOCKED"):
        return "BLOCKED_EVIDENCE_INCOMPLETE"
    return "NEEDS_REVIEW"


def formal_acceptance_allowed(acceptance):
    """仅完整确定性门已过、且非阻塞/待复核状态时允许正式发布。

    合法的确定性 FAIL 与 ``PASSED_WITH_RISK/GAPS`` 都不是这里要改判的对象；只要它们
    已通过 gate 链，就仍是正式终态。布尔值用 ``is True``，拒绝 ``1`` 等宽松 JSON 形态。
    """
    if not isinstance(acceptance, dict):
        return False
    gate = acceptance.get("gate")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        return False
    # ``passed`` 与非空/坏形态 errors 同时出现只能说明输入自相矛盾。主链不会造出它，
    # 但 renderer 是公开入口，必须对手写/历史文件 fail-closed。
    errors = gate.get("errors")
    if not isinstance(errors, dict) or errors:
        return False
    state = acceptance.get("state")
    overall = acceptance.get("overall")
    expected_state = canonical_state(
        overall, {"status": acceptance.get("perf_status")})
    # overall 是正式报告直接展示的人读结论；它与 state 必须由同一 canonical helper
    # 得出精确关系。未知 overall 会映射 NEEDS_REVIEW，仍由负向门拒绝。
    return (state == expected_state
            and not _is_incomplete_label(state)
            and not _is_incomplete_label(overall))


def assert_formal_acceptance_allowed(acceptance):
    """拒绝把未过门、阻塞或待复核候选写成/渲染成正式验收产物。"""
    if formal_acceptance_allowed(acceptance):
        return
    gate = acceptance.get("gate") if isinstance(acceptance, dict) else None
    state = acceptance.get("state") if isinstance(acceptance, dict) else None
    overall = acceptance.get("overall") if isinstance(acceptance, dict) else None
    raise FormalAcceptanceError(
        "正式验收产物发布门未通过："
        f"gate.passed={gate.get('passed') if isinstance(gate, dict) else None!r}, "
        f"gate.errors={gate.get('errors') if isinstance(gate, dict) else None!r}, "
        f"state={state!r}, overall={overall!r}；"
        "本轮只能保留非正式 attempt 诊断工件。")


def _artifact_exists(root, filename):
    if isinstance(root, ArtifactTransaction):
        return root.lexists(filename)
    return os.path.lexists(os.path.join(root, filename))


def _assert_opposite_summary_absent(root, filename):
    """公共 writer 也维护两种总结名互斥；不暗中删除调用方已有工件。"""
    opposite = (ATTEMPT_RECORD_FILE
                if filename == FORMAL_ACCEPTANCE_FILE else FORMAL_ACCEPTANCE_FILE)
    if _artifact_exists(root, opposite):
        raise ArtifactNameConflictError(
            f"总结工件名必须互斥：写 {filename!r} 前发现已有 {opposite!r}；"
            "请由 workflow 的统一失效步骤先处理上一轮总结。")


def assert_no_pre_execution_artifacts(root):
    """marker 或任一 pre-execution payload 均代表终态/半提交，正式路径不得忽略。"""
    present = [name for name in PRE_EXECUTION_RESERVED_FILES
               if _artifact_exists(root, name)]
    if present:
        raise ArtifactNameConflictError(
            "报告根存在 pre-execution terminal/incomplete 工件："
            + ", ".join(present)
            + "；只有一次新的完整 workflow 能在同一工件锁内显式失效后重跑")


class ArtifactTransaction:
    """持有报告根 inode 与锁的 dirfd-relative 事务。"""

    def __init__(self, path, dir_fd):
        self.path = path
        self.dir_fd = dir_fd
        root_stat = os.fstat(dir_fd)
        self._identity = (root_stat.st_dev, root_stat.st_ino)
        self._owned_temps = {}
        self._round = secrets.token_hex(12)

    @staticmethod
    def _name(filename):
        if (not isinstance(filename, str) or not filename
                or filename in {".", ".."}
                or os.path.basename(filename) != filename):
            raise ArtifactNameConflictError("事务工件名必须是报告根下的单个文件名")
        return filename

    def assert_path_stable(self):
        try:
            current = os.stat(self.path, follow_symlinks=False)
        except OSError as ex:
            raise ArtifactNameConflictError("报告根事务期间被替换或移除") from ex
        if (not stat.S_ISDIR(current.st_mode)
                or (current.st_dev, current.st_ino) != self._identity):
            raise ArtifactNameConflictError("报告根事务期间被替换")

    def lexists(self, filename):
        name = self._name(filename)
        try:
            os.stat(name, dir_fd=self.dir_fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def unlink(self, filename):
        name = self._name(filename)
        self.assert_path_stable()
        try:
            os.unlink(name, dir_fd=self.dir_fd)
        except FileNotFoundError:
            return False
        self.assert_path_stable()
        return True

    def sha256(self, filename):
        import hashlib
        name = self._name(filename)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, dir_fd=self.dir_fd)
        try:
            value = hashlib.sha256()
            for chunk in iter(lambda: os.read(fd, 1024 * 1024), b""):
                value.update(chunk)
            return value.hexdigest()
        finally:
            os.close(fd)

    def read_bytes(self, relative_path):
        """从固定报告根 dirfd 逐段 no-follow 读取正式输入。"""
        if (not isinstance(relative_path, str) or not relative_path
                or os.path.isabs(relative_path)
                or os.path.normpath(relative_path) != relative_path):
            raise ArtifactNameConflictError("事务读取路径须为报告根内规范相对路径")
        parts = relative_path.split(os.path.sep)
        if any(part in {"", ".", ".."} for part in parts):
            raise ArtifactNameConflictError("事务读取路径不得逃逸报告根")
        directory_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                           | getattr(os, "O_NOFOLLOW", 0))
        directory_fd = os.dup(self.dir_fd)
        try:
            for part in parts[:-1]:
                child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child_fd
            fd = os.open(
                parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd)
            try:
                current = os.fstat(fd)
                if not stat.S_ISREG(current.st_mode):
                    raise ArtifactNameConflictError("事务正式输入不是普通文件")
                chunks = []
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        return b"".join(chunks)
                    chunks.append(chunk)
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)

    def read_json(self, relative_path):
        try:
            return json.loads(self.read_bytes(relative_path))
        except (UnicodeError, json.JSONDecodeError) as ex:
            raise ArtifactNameConflictError(
                f"事务正式 JSON 输入不可解析：{relative_path!r}") from ex

    def atomic_write_bytes(self, filename, raw):
        name = self._name(filename)
        if not isinstance(raw, bytes):
            raise TypeError("atomic payload 须为 bytes")
        temp_name = (
            f"{ARTIFACT_TEMP_PREFIX}{self._round}.{secrets.token_hex(8)}.tmp")
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0))
        fd = os.open(temp_name, flags, 0o600, dir_fd=self.dir_fd)
        temp_stat = os.fstat(fd)
        self._owned_temps[temp_name] = (temp_stat.st_dev, temp_stat.st_ino)
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.assert_path_stable()
        current = os.stat(temp_name, dir_fd=self.dir_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != self._owned_temps[temp_name]:
            raise ArtifactNameConflictError("本轮原子写临时文件在提交前被换绑")
        os.replace(
            temp_name, name, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd)
        self._owned_temps.pop(temp_name, None)
        os.fsync(self.dir_fd)
        self.assert_path_stable()
        return os.path.join(self.path, name)

    def atomic_write_json(self, filename, payload):
        raw = json.dumps(
            payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.atomic_write_bytes(filename, raw)

    def cleanup_own_temps(self):
        for name, identity in tuple(self._owned_temps.items()):
            try:
                current = os.stat(
                    name, dir_fd=self.dir_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(name, dir_fd=self.dir_fd)
            except FileNotFoundError:
                pass
            finally:
                self._owned_temps.pop(name, None)


def _atomic_write_json(root, filename, payload):
    """在已持锁事务内原子写 JSON；兼容入口会自行取得同一把锁。"""
    if isinstance(root, ArtifactTransaction):
        return root.atomic_write_json(filename, payload)
    guard = artifact_path_guard.prepare_existing_directory(root)
    with artifact_transaction(guard["path"], guard=guard) as transaction:
        return transaction.atomic_write_json(filename, payload)


@contextmanager
def artifact_transaction(out_dir, *, guard=None):
    """序列化同一报告根的正式/attempt 终态切换。

    lock 文件只负责并发互斥，不承载状态；崩溃后的 durable 状态由正式总结或
    ``pre_execution_terminal.json`` 表达。调用方必须先创建报告根。
    """
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        dir_fd = os.open(out_dir, flags)
    except OSError as ex:
        raise ArtifactNameConflictError("报告根无法以 no-follow dirfd 打开") from ex
    transaction = ArtifactTransaction(os.path.abspath(out_dir), dir_fd)
    lock_fd = None
    try:
        if guard is not None:
            expected = guard["identities"][-1][1:]
            if transaction._identity != expected:
                raise ArtifactNameConflictError("报告根取得事务前已被替换")
        transaction.assert_path_stable()
        lock_flags = (os.O_RDWR | os.O_CREAT
                      | getattr(os, "O_NOFOLLOW", 0))
        lock_fd = os.open(
            ARTIFACT_LOCK_FILE, lock_flags, 0o600, dir_fd=dir_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        orphans = sorted(
            name for name in os.listdir(dir_fd)
            if (name.startswith(ARTIFACT_TEMP_PREFIX)
                or name in LEGACY_ARTIFACT_TEMP_FILES))
        if orphans:
            raise ArtifactNameConflictError(
                "报告根存在上一轮 orphan 原子写临时文件：" + ", ".join(orphans))
        transaction.assert_path_stable()
        yield transaction
        transaction.assert_path_stable()
    finally:
        transaction.cleanup_own_temps()
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(dir_fd)


def publish_acceptance_json_locked(out_dir, acceptance, *, transaction):
    """调用方已持有报告根锁时发布正式 acceptance。"""
    assert_formal_acceptance_allowed(acceptance)
    _assert_execution_identity(acceptance)
    _assert_opposite_summary_absent(transaction, FORMAL_ACCEPTANCE_FILE)
    assert_no_pre_execution_artifacts(transaction)
    return _atomic_write_json(transaction, FORMAL_ACCEPTANCE_FILE, acceptance)


def publish_acceptance_json(out_dir, acceptance):
    """正式 ``acceptance.json`` 的唯一写出原语。"""
    assert_formal_acceptance_allowed(acceptance)
    _assert_execution_identity(acceptance)
    try:
        guard = artifact_path_guard.prepare_existing_directory(out_dir)
    except artifact_path_guard.ArtifactPathError as ex:
        raise ArtifactNameConflictError(f"正式报告根不可信：{ex}") from ex
    with artifact_transaction(guard["path"], guard=guard) as transaction:
        return publish_acceptance_json_locked(
            guard["path"], acceptance, transaction=transaction)


def build_attempt_record(acceptance):
    """把不可正式发布的候选投影成无裁决语义的 attempt record。"""
    if formal_acceptance_allowed(acceptance):
        raise ValueError("可正式发布的确定性终态不得降名为 attempt_record.json")
    if not isinstance(acceptance, dict):
        raise TypeError("attempt 候选须为 JSON object")
    return {
        "schema": ATTEMPT_RECORD_SCHEMA,
        "schema_version": ATTEMPT_RECORD_SCHEMA_VERSION,
        "status": "not_publishable",
        "formal_eligible": False,
        "acceptance_verdict": None,
        "op": acceptance.get("op"),
        "execution_identity": acceptance.get("execution_identity"),
        "repo_mode": acceptance.get("repo_mode"),
        "pipeline_result": acceptance.get("overall"),
        "pipeline_state": acceptance.get("state"),
        "exit_code": acceptance.get("exit_code"),
        "requires_human_cp": acceptance.get("requires_human_cp", False),
        "gate": acceptance.get("gate"),
        "diagnostic_sources": acceptance.get("diagnostic_sources") or {
            "precision": "verdict.json",
            "performance": "perf_report.json",
            "evidence": "evidence.json",
        },
        "pre_execution_failure": acceptance.get("pre_execution_failure"),
        "note": (
            "本工件只记录未完成/阻塞的 workflow attempt；acceptance_verdict 恒为 null，"
            "不得命名、引用或渲染为正式验收裁决。"),
    }


def write_attempt_record(out_dir, acceptance):
    """原子写未完成 attempt；正式终态会被 ``build_attempt_record`` 拒绝。"""
    record = build_attempt_record(acceptance)
    try:
        guard = artifact_path_guard.prepare_existing_directory(out_dir)
    except artifact_path_guard.ArtifactPathError as ex:
        raise ArtifactNameConflictError(f"attempt 报告根不可信：{ex}") from ex
    with artifact_transaction(guard["path"], guard=guard) as transaction:
        _assert_opposite_summary_absent(transaction, ATTEMPT_RECORD_FILE)
        assert_no_pre_execution_artifacts(transaction)
        return _atomic_write_json(transaction, ATTEMPT_RECORD_FILE, record)
