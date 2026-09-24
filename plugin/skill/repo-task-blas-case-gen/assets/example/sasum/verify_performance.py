#!/usr/bin/env python3
"""用 msprof op（msopprof）kernel 耗时验收 CSV 驱动的性能用例。"""

# ===== 渲染常量区开始 =====
OP = "sasum"
FAMILY = "asum"
CSV_NAME = "sasum_test.csv"
PACKAGE_CSV_SHA256 = "781669a4ce9062ee8d2eca3733ce7717a4948ed2b0cf3a8f517ca99a86a3d559"
GENERATOR_VERSION = int("1")
PERF_KEY = ["n"]
PROFILE_ASSIGNS_JSON = '''{}'''
PERF_THRESHOLD = float("0.8")
PERF_FILTER = "*TC_PF_*"
# ===== 渲染常量区结束 =====

import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time


ENVIRONMENT_EXIT = 3

IDLE_GATE_EXIT = 4

HARNESS_PROFILE = "blas"
# 本 profile 的构建/绑卡惯例，渲染时自 registry 注入（唯一权威在 registry）：
# build_device_flag（build.sh 是否吃 --device）、visible_devices_env（运行时绑卡
# 变量，None 即编译期定卡）、runtime_library_dirs（跑测所需库路径，相对工程根）。
BUILD_CONVENTION = json.loads("""{"build_device_flag": true, "runtime_library_dirs": [], "visible_devices_env": null}""")


# 物理卡取值域。这是假设，不是探测结果：默认目标机有 0-7 共 8 张卡
# （与 `--device-pool` 的默认池同源，改一处要同时改那里）。映射串共
# device_compiled+1 位，每位都要一个互不相同、且在该机器上真实存在的物理卡号——
# 占位位取 0..N-1（目标卡落在其中时顺延到 N）。由此 device_compiled 最大 7，
# 与 accept 侧换卡门的上界一致。卡数不足 N+1 的机器上换卡不成立，改用同卡复测；
# 量具不探测卡数，也不为此加探测。
DEVICE_CARDS = tuple(range(8))
MAX_COMPILED_DEVICE = len(DEVICE_CARDS) - 1


def _visible_devices_map(target, compiled_device):
    """构造 ASCEND_RT_VISIBLE_DEVICES 串：逗号分隔的物理卡号列表，第 i 项映射为
    进程内逻辑卡 i（A3 机实测，2026-09-18）。

    二进制的 TEST_DEVICE_ID 编译期固定在逻辑卡 `compiled_device` 上，所以把目标
    物理卡 `target` 放到第 `compiled_device` 位，它就落到这张卡：
    compiled_device=0 时串就是 target 本身；为 N 时前 N 位填占位真实卡号
    （`DEVICE_CARDS` 里不等于 target 的最小 N 个，升序），第 N 位填 target。
    占位位只为把 target 顶到第 N 位，二进制不会用到它们，但它们必须在该机器上真实
    存在——这一点由 `DEVICE_CARDS` 的默认 8 卡假设兜着，量具不探测卡数。
    例：N=3、target=6 → "0,1,2,6"；N=3、target=1 → "0,2,3,1"。"""
    placeholders = [card for card in DEVICE_CARDS if card != target][:compiled_device]
    if len(placeholders) < compiled_device:
        # 参数面已把 compiled_device 限在 0-7，走到这里即调用约定被破坏；
        # 与其发一串长度不足的映射串让二进制落到错卡，不如就地炸掉。
        raise ValueError(
            f"device_compiled={compiled_device} 需要 {compiled_device} 张占位卡，"
            f"按默认 {len(DEVICE_CARDS)} 卡假设"
            f"（{DEVICE_CARDS[0]}-{MAX_COMPILED_DEVICE}）去掉目标卡 {target} 后"
            f"只剩 {len(placeholders)} 张"
        )
    return ",".join(str(card) for card in [*placeholders, target])


def _run_environment(repo, device, auto=False, compiled_device=0):
    """gtest/采集子进程环境：按惯例注入绑卡变量与运行库路径。
    auto 模式统一用 ASCEND_RT_VISIBLE_DEVICES 把选中的物理卡映射到二进制的编译
    逻辑卡位（全局协议，非域特判；编译期定卡域的二进制在 auto 下按逻辑 0 构建，
    故 compiled_device 默认 0，映射串退化成该物理卡号本身）。换卡复测由启动方
    传入非 0 的 compiled_device，串的构造见 `_visible_devices_map`。"""
    env = dict(os.environ)
    visible = BUILD_CONVENTION["visible_devices_env"]
    if auto:
        env["ASCEND_RT_VISIBLE_DEVICES"] = _visible_devices_map(device, compiled_device)
    elif visible:
        env[visible] = str(device)
    lib_dirs = [
        str(Path(repo) / item) for item in BUILD_CONVENTION["runtime_library_dirs"]
    ]
    if lib_dirs:
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            lib_dirs + ([current] if current else [])
        )
    return env



def _npu_idle_gate(device, timeout=20):
    """起跑前现场核查目标卡空闲（用户裁定，fail-closed 三分支）。

    返回 (ok, payload)。判定写死：输出含 "Process id:" → BUSY；
    含 "No process in device" → IDLE；其余（含命令失败/超时/输出无法判读）
    一律 QUERY_FAILED。BUSY 与 QUERY_FAILED 都阻塞，退出码 IDLE_GATE_EXIT。"""
    command = ["npu-smi", "info", "-t", "proc-mem", "-i", str(device)]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        partial = getattr(exc, "stdout", None)
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return False, {
            "status": "QUERY_FAILED",
            "command": " ".join(command),
            "detail": str(exc),
            "output": partial,
        }
    output = result.stdout or ""
    payload = {
        "status": None,
        "command": " ".join(command),
        "detail": None,
        # 全量原始输出（proc-mem 输出量小，不截断）。
        "output": output,
    }
    if result.returncode != 0:
        payload.update(status="QUERY_FAILED", detail=f"npu-smi 退出码 {result.returncode}")
        return False, payload
    if "Process id:" in output:
        payload.update(status="BUSY", detail="目标卡存在进程，拒绝起跑")
        return False, payload
    if "No process in device" in output:
        payload.update(status="IDLE", detail="目标卡空闲")
        return True, payload
    payload.update(status="QUERY_FAILED", detail="输出无法判读，按查询失败阻塞")
    return False, payload


def _parse_device_request(raw_device, raw_pool):
    """解析 --device/--device-pool（术语：物理卡=npu-smi 编号的真实设备；
    逻辑卡=进程内 ACL 编号，受 ASCEND_RT_VISIBLE_DEVICES 映射）。

    显式整数：严格单卡（既有裁定，行为不变），此时给 --device-pool 属参数错误。
    "auto"：起跑时按 pool 序逐卡过空闲门，首张 IDLE 即选中并运行时映射为逻辑 0。
    返回 (requested, pool)；requested 为 int 或 "auto"。"""
    if raw_device == "auto":
        pool = []
        for item in (raw_pool or "0,1,2,3,4,5,6,7").split(","):
            item = item.strip()
            if not item:
                continue
            if not item.isdigit():
                raise SystemExit(f"--device-pool 含非法项 {item!r}（须为非负整数）")
            value = int(item)
            if value not in pool:
                pool.append(value)
        if not pool:
            raise SystemExit("--device-pool 解析后为空")
        return "auto", pool
    if raw_pool is not None:
        raise SystemExit("--device-pool 仅在 --device auto 时有效（显式卡号是严格单卡契约）")
    try:
        return int(raw_device), None
    except ValueError:
        raise SystemExit(f"--device 只接受非负整数或 auto，得到 {raw_device!r}")


def _resolve_device(requested, pool):
    """起跑时定卡：构建后单次有序遍历（评审裁定，无预扫描、无重试上限）。

    显式整数：对该卡过一次空闲门，BUSY/QUERY_FAILED 即阻塞（行为与历史一致）。
    auto：按 pool 序逐卡过同一空闲门；候选 BUSY 或 QUERY_FAILED 都跳过该卡
    （该卡绝不被选中，fail-closed 逐卡成立），首张 IDLE 即选中；池尽无 IDLE
    则整体阻塞。返回 (resolved 或 None, npu_gate payload)。"""
    attempts = []
    candidates = pool if requested == "auto" else [requested]
    resolved = None
    for candidate in candidates:
        ok, probe = _npu_idle_gate(candidate)
        probe = dict(probe)
        probe["device"] = candidate
        attempts.append(probe)
        if ok:
            resolved = candidate
            break
    gate = {
        "requested": requested,
        "pool": pool,
        "attempts": attempts,
        "final": attempts[-1] if attempts else None,
        "resolved": resolved,
    }
    return resolved, gate

TEST_FAILURE_EXIT = 1
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")




FAILURE_EXIT = 1
INSUFFICIENT_EXIT = 2
# 默认单次采集:每次采样都是独立带 profiling 的进程,5 次带来 ~5x 时长,
# 而中位数收益有限(历史实测样本间 spread 仅 1.3–2.4%);需要多样本时用 --repeats 覆盖。
REPEATS = 1
# 单例耗时预估(秒):单 launch 用例实测 4.6-6.2s(A3,CANN 9.0.1)。多 launch 算子随
# 实际 launch 数上升,这个数只用于开跑前的总时长提示,不参与任何判定。
PER_CASE_ESTIMATE_S = 6

# msopprof 产物。布局取决于**实际采到的 launch 数**,不取决于 --launch-count 取值:
#   采到 1 个   → OPPROF_<时间戳>_<随机串>/OpBasicInfo.csv
#   采到多个   → OPPROF_*/<kernel 符号名>/<序号>/OpBasicInfo_<时间戳>.csv
# 所以 glob 必须递归覆盖两种。只写扁平那条会在多 launch 算子上扫空,表现为每例
# NO_KERNEL,与「算子没跑起来」无法区分(A3 实测,2026-09-23)。
OP_BASIC_INFO_GLOB = "OPPROF_*/**/OpBasicInfo*.csv"
# OpBasicInfo.csv 九列,**没有 Task Type 列**,所以旧的 kernel task 类型过滤整体作废。
OP_BASIC_INFO_COLUMNS = {
    "duration_us": "Task Duration(us)",
}
# --launch-count 默认值。工具允许 1-5000;超额指定安全、不增加耗时与体积。
# 取 512 是基于已知反例留的余量,不是「必然够」的保证——保证来自 _parse_truncated。
# 反例(ops-blas 源码):ssymm/arch22 在 m=1280 n=128 下 105 个 launch;
# sgemm_strided_batched/arch35 由 batchCount 驱动,参数校验只判 >= 0,无上界。
DEFAULT_LAUNCH_COUNT = 512
# 工具自报采集/解析失败的日志表述。命中即判环境失败并整轮中止。
#
# **这不是 WARN 黑名单**,只收工具明确说「这次采集或解析没成功」的那几句。工具退 0
# 不代表采到了:磁盘满时它只刷 Copy failed;部分 kernel 解析失败时它打
# 「N success, M failed」后照常返回(msopprof 源码 op_prof_data_parse.cpp:114)。
# 不认这些串,少算的耗时会让 ratio 虚高——这是会把 FAIL 写成 PASS 的路径之一。
COLLECTION_FAILURE_MARKERS = (
    "Copy failed",                    # 写盘失败(磁盘满实测)
    "Failed to save",                 # 同上,另一种表述
    "No space left",                  # 同上
    "Get profiling data failed",      # 采集阶段失败
    "Profiling data parse failed",    # 解析阶段失败
    "No profiling data dumped",       # 落盘为空
)
# 「N success, M failed」只在 M 非零时算失败:M 为 0 是正常完成的汇总行。
PARTIAL_FAILURE_RE = re.compile(
    r"Profiling kernels result is:\s*\d+\s*success,\s*(\d+)\s*failed"
)
INTEGER_RE = re.compile(r"^[+-]?\d+$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PERF_KEY = [key for key in PERF_KEY if key]
PROFILE_ASSIGNS = json.loads(PROFILE_ASSIGNS_JSON)


class ProfileParseError(ValueError):
    """表示 msopprof 的 OpBasicInfo.csv 无法按当前表驱动协议解析。"""


class EnvironmentAbort(RuntimeError):
    """采集环境出了问题,整轮中止而不是逐例记状态。

    磁盘满、工具自身失败这类条件不是单例属性:对着满盘继续跑二百例只会产出
    二百个假 NO_KERNEL,把环境问题说成算子问题。走 _fail 的环境失败出口,
    退 ENVIRONMENT_EXIT,不动 PASS/FAIL/NO_KERNEL/CRASH/TIMEOUT/MISSING 这个
    封闭状态词表——往词表里加一个值是对外契约扩张,折叠核会把不认识的状态判无效轮。"""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
        self.message = message


class ProfileTruncatedError(ProfileParseError):
    """采到的 launch 数撞上 --launch-count 上限,完整性未证实。

    单列一类是因为去向不同:普通解析失败是产物坏了,截断是「这个用例的 launch
    规模超出当前采集口径」——措辞不能说成算子失败。"""


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _arch_for_soc(soc):
    value = soc.lower()
    if value.startswith("ascend910b") or value.startswith("ascend910_93"):
        return "arch22"
    if value.startswith("ascend950"):
        return "arch35"
    if value.startswith("ascend310p"):
        return "arch20"
    return None


def _unique_existing(candidates):
    result = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.is_file():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _find_source_csv(repo, arch):
    test_root = repo / "test"
    candidates = [test_root / OP / arch / CSV_NAME]
    candidates.extend(sorted(test_root.glob(f"*/{OP}/{arch}/{CSV_NAME}")))
    if len(OP) > 1:
        candidates.append(test_root / OP[1:] / arch / CSV_NAME)
    matches = _unique_existing(candidates)
    return matches[0] if matches else None


def _find_binary(repo):
    """候选收集 + 唯一裁决：build/test 下递归找 <op>_test；0/多命中 fail-closed 报候选。"""
    test_root = repo / "build" / "test"
    name = f"{OP}_test"
    matches = _unique_existing(sorted(test_root.glob(f"**/{name}")))
    if len(matches) == 1:
        return matches[0], None, None
    if not matches:
        return None, f"build/test 下未找到 {name}", "BINARY_NOT_FOUND"
    listed = "、".join(str(path) for path in matches)
    return None, f"测试二进制命中多个候选：{listed}", "BINARY_AMBIGUOUS"


def _read_csv_rows(path, prefix):
    header = None
    rows = []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            values = next(csv.reader([raw]))
            if header is None:
                header = values
                continue
            if len(values) != len(header):
                raise ValueError("CSV 行列数与表头不一致")
            row = dict(zip(header, values))
            if row.get("case_name", "").startswith(prefix):
                rows.append(row)
    return rows


def _selected_rows(csv_path, args):
    rows = _read_csv_rows(csv_path, "TC_PF_")
    if args.case:
        requested = set(args.case)
        rows = [row for row in rows if row["case_name"] in requested]
    if args.filter:
        rows = [row for row in rows if args.filter in row["case_name"]]
    return rows


def _comparable_rows(rows, references):
    """只保留能在 gpu_baseline.csv 里配到非空 gpu_ms 的行；返回 (可比行, 被忽略的用例名)。"""
    comparable = []
    ignored = []
    for row in rows:
        try:
            reference = references.get(_key_for_row(row))
        except ValueError:
            reference = None
        if reference is None:
            ignored.append(row["case_name"])
        else:
            comparable.append(row)
    return comparable, ignored


def _commit_result(out_path, payload):
    """no-clobber 结果提交：目标结果 JSON 已存在即拒绝写入并返回 False。

    证据保护(原始证据一经写出不可变)的防御性双保险——主检查在起跑前。
    机制:先写同目录唯一临时文件,再 os.link 提交——目标已存在时内核抛
    FileExistsError,检查与提交是同一个原子操作,无 TOCTOU 窗口,不引入
    锁协议。写入成功返回 True。"""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    try:
        os.link(temporary, target)
    except FileExistsError:
        print(
            f"RUN_ID_EXISTS: 结果 JSON 在运行期间被外部创建,不写:{target}",
            file=sys.stderr,
        )
        return False
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return True


def _retest_footprint(run_dir, reason, message):
    """复测轮环境失败的中断轮足迹:确保阶段目录存在并把原因追记进 fail.log,
    使该轮占号、可发现、原因可读。追记不覆盖——目录可能属既占轮号的先前
    尝试,不动其既有文件。复测模式下所有不写结果 JSON 的失败退出都要经过
    这里(_fail 与各内联失败分支统一调用)。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "fail.log").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{_timestamp()} {reason}: {message}\n")


RETEST_RUN_ID_RE = re.compile(r"^(?P<base>.+)-retest-(?P<round>[1-9]\d*)$")


def _parse_retest_mode(run_id):
    """入口一次解析复测模式,此后执行链保持单一,不在各分支重复判断后缀。

    run-id 以最右 `-retest-<k>`(k 正整数,无前导零)结尾即复测轮,返回
    {"base_run_id", "round"};否则返回 None(首轮)。贪婪匹配保证嵌套后缀
    取最右一段(如 a-retest-2-retest-3 → base=a-retest-2, round=3)。"""
    match = RETEST_RUN_ID_RE.fullmatch(run_id)
    if match is None:
        return None
    return {"base_run_id": match.group("base"), "round": int(match.group("round"))}


def _base_result(args, arch, started):
    result = {
        "run_id": args.run_id,
        "op": OP,
        "family": FAMILY,
        "soc": args.soc,
        "arch": arch,
        "device": _parse_device_request(args.device, args.device_pool)[0],
        "device_pool": _parse_device_request(args.device, args.device_pool)[1],
        "repo": str(args.repo.resolve()),
        "binary": None,
        "binary_sha256": None,
        "csv_path": None,
        "csv_sha256": None,
        "package_csv_sha256": PACKAGE_CSV_SHA256,
        "calls_per_case": 1,
        "ignored_no_ref": [],
        "gpu_baseline_path": None,
        "baseline_warnings": [],
        "msprof": None,
        "gtest_filter": None,
        "cases": [],
        "summary": {},
        "exit_code": None,
        "started": started,
        "finished": None,
    }
    return result


def _environment_error(payload, out_path, reason, message, allow_error_json=True):
    """环境失败退出。目标结果 JSON 已存在时任何路径都不写(证据保护)。

    allow_error_json=False 即复测模式:环境失败一律不写 JSON,只留目录与日志,
    该轮成为中断轮(弃号换下一号);首轮保持默认 True,目标不存在时照常写出
    错误 JSON。"""
    payload["summary"] = {
        "expected": len(payload["cases"]),
        "status": "证据不足",
        "timing_scope": "unknown",
        "threshold": PERF_THRESHOLD,
        "scope_caveat": True,
        "reason": reason,
        "message": message,
    }
    payload["exit_code"] = ENVIRONMENT_EXIT
    payload["finished"] = _timestamp()
    written = allow_error_json and _commit_result(out_path, payload)
    print(f"{reason}: {message}", file=sys.stderr)
    if written:
        print(f"result: {out_path}")
    return ENVIRONMENT_EXIT


def _run_build(args, log_path):
    command = [
        "bash",
        "build.sh",
        f"--soc={args.soc}",
        f"--ops={OP}",
    ]
    if BUILD_CONVENTION["build_device_flag"]:
        # 运行时映射(auto 或 --map-device)按编译逻辑卡号构建:auto 定卡协议的
        # --compiled-device 默认 0,换卡复测由启动方传入非 0;否则按显式卡号构建。
        mapped = args.device == "auto" or args.map_device
        command.append(f"--device={args.compiled_device if mapped else args.device}")
    try:
        result = subprocess.run(
            command,
            cwd=str(args.repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=args.build_timeout,
            check=False,
        )
        output = result.stdout
        return_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output += "\nBUILD TIMEOUT\n"
        return_code = -1
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(output)
    return return_code


def _read_nonempty_lines(path):
    try:
        return [line.strip() for line in Path(path).read_text().splitlines() if line.strip()]
    except OSError:
        return []


def _check_build_lists(repo):
    test_build = repo / "build" / "test"
    built = _read_nonempty_lines(test_build / "built_tests.list")
    skipped = _read_nonempty_lines(test_build / "skipped_tests.list")
    if any(line.split("|", 1)[0] == OP for line in skipped):
        return "OP_SKIPPED", "skipped_tests.list 标记该算子为跳过"
    # 清单存在才核验；不存在（有的仓不产 built_tests.list）由二进制寻址器裁决。
    if built and OP not in built and f"{OP}_test" not in built:
        return "BUILD_FAILED", "built_tests.list 不含目标算子"
    return None, None


def _list_tests(binary, timeout, expected, env=None):
    try:
        result = subprocess.run(
            [str(binary), "--gtest_list_tests"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"无法列出 GTest：{exc}", "LIST_FAILED"
    if result.returncode != 0:
        message = result.stderr.strip() or f"退出码 {result.returncode}"
        return None, message, "LIST_FAILED"
    mapping = {}
    suite = None
    for raw in result.stdout.splitlines():
        content = raw.split("#", 1)[0].rstrip()
        stripped = content.strip()
        if not stripped:
            continue
        if not content[:1].isspace() and stripped.endswith("."):
            suite = stripped
            continue
        if suite is None or not content[:1].isspace():
            continue
        full_name = suite + stripped
        case_name = full_name.rsplit("/", 1)[-1]
        # 按期望集精确匹配，不筛命名前缀（与精度侧同一口径）。
        if case_name not in expected:
            continue
        if case_name in mapping:
            return None, f"case_name {case_name!r} 映射重复", "DUPLICATE_CASE"
        mapping[case_name] = full_name
    return mapping, None, None


def _normalize_key_value(value):
    text = str(value).strip()
    return int(text) if INTEGER_RE.fullmatch(text) else text


def _key_for_row(row):
    missing = [key for key in PERF_KEY if key != "profile" and key not in row]
    if missing:
        raise ValueError("缺少性能键：" + ", ".join(missing))
    values = []
    for key in PERF_KEY:
        if key != "profile" or key in row:
            values.append(_normalize_key_value(row[key]))
            continue
        matches = [
            name
            for name, assign in PROFILE_ASSIGNS.items()
            if all(row.get(enum_name) == enum_value for enum_name, enum_value in assign.items())
        ]
        if len(matches) != 1:
            raise ValueError(f"性能行无法唯一映射 profile：{matches}")
        values.append(matches[0])
    return tuple(values)


def _load_gpu_baseline(path):
    metadata = {}
    data_lines = []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                key, separator, value = stripped[1:].strip().partition("=")
                if separator:
                    metadata[key.strip()] = value.strip()
                continue
            data_lines.append(raw)
    if not data_lines:
        raise ValueError("gpu_baseline.csv 缺表头")
    rows = list(csv.DictReader(data_lines))
    expected = ["id", *PERF_KEY, "gpu_ms"]
    if rows and list(rows[0]) != expected:
        raise ValueError("gpu_baseline.csv 表头与 PERF_KEY 不一致")
    if not rows and next(csv.reader([data_lines[0]])) != expected:
        raise ValueError("gpu_baseline.csv 表头与 PERF_KEY 不一致")
    references = {}
    warnings = []
    for index, row in enumerate(rows, 2):
        key = _key_for_row(row)
        # 逐行既有校验(非数值/≤0)对所有行照常执行,重复键行也不例外。
        raw_gpu_ms = row.get("gpu_ms", "").strip()
        if raw_gpu_ms:
            try:
                gpu_ms = float(raw_gpu_ms)
            except ValueError as exc:
                raise ValueError(f"gpu_baseline.csv 第 {index} 行 gpu_ms 非数值") from exc
            if gpu_ms <= 0:
                raise ValueError(f"gpu_baseline.csv 第 {index} 行 gpu_ms 必须大于 0")
        else:
            gpu_ms = None
        # 重复键不再报错:按规范化键首行生效——首行 gpu_ms 为空即 NO_REF,
        # 后续重复行不覆盖,只记 warning(所有基线读取点统一此选择规则)。
        if key in references:
            warnings.append(f"gpu_baseline.csv 第 {index} 行性能键重复,首行生效")
            continue
        references[key] = gpu_ms
    return metadata, references, warnings


def _launch_dirs_without_csv(root, seen):
    """嵌套布局下,建了采集目录却没落 CSV 的那些 launch。返回相对路径列表。

    工具为每次 launch 建一个 <kernel>/<序号>/ 目录。目录在而 CSV 不在,说明那一次
    的耗时丢了;只数读到的行数发现不了这种缺口。"""
    missing = []
    for prof in sorted(Path(root).glob("OPPROF_*")):
        for index_dir in sorted(prof.glob("*/[0-9]*")):
            if not index_dir.is_dir():
                continue
            if not any(f.resolve() in seen for f in index_dir.glob("OpBasicInfo*.csv")):
                missing.append(str(index_dir.relative_to(root)))
    return missing


def _collection_failure_marker(output):
    """采集日志里有没有工具自报的失败。返回命中的那一条,没有则 None。

    工具退 0 不代表采到了。不认这些串,失败会落到「无数据行」那一档判 NO_KERNEL,
    把环境问题说成算子没起 kernel;部分失败更糟——剩下的行照常求和,少算的耗时
    直接抬高 ratio。"""
    text = output or ""
    for marker in COLLECTION_FAILURE_MARKERS:
        if marker in text:
            return marker
    match = PARTIAL_FAILURE_RE.search(text)
    if match and match.group(1) != "0":
        return f"{match.group(1)} 个 kernel 采集失败"
    return None


def _check_free_space(output_dir, launch_count):
    """采样前按**本次采集上限**估所需空间。够则 None,不够返回说明。

    按上限算而不按上一例的实际占用算:上一例可能只有一个 launch,下一例可能有
    一百个,用上一例外推必然漏算。上限是唯一在跑之前可知的量。
    峰值系数 >1 是因为工具先复制目录再删原目录,有两份数据同时在盘的阶段。"""
    per_launch_bytes = 2.2 * 1024 * 1024        # A3 实测,约 2.2 MB/launch
    peak_factor = 1.5
    need = int(per_launch_bytes * launch_count * peak_factor)
    target = Path(output_dir)
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return f"{probe} 可用空间查不到：{exc}"
    if usage.free < need:
        return (
            f"{probe} 可用 {usage.free // (1024 * 1024)} MB，"
            f"按 --launch-count={launch_count} 需要约 {need // (1024 * 1024)} MB；"
            "调小 --launch-count 或换一块盘"
        )
    return None


def _resolve_msprof(override):
    """定位 msopprof。参数名沿用 --msprof 不改,只换查找目标与帮助文本。

    可执行不等于可用:旧 CANN 上可能有同名文件却不支持本协议,所以命中后还要
    _msopprof_usable 探一次。探不过按环境问题报,不要让它表现成每一例都失败。"""
    if override is not None:
        candidate = shutil.which(str(override))
        if candidate:
            return Path(candidate).resolve()
        path = Path(override).expanduser()
        return path.resolve() if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which("msopprof")
    if found:
        return Path(found).resolve()
    toolkit = os.environ.get("ASCEND_TOOLKIT_HOME")
    candidates = []
    if toolkit:
        candidates.append(Path(toolkit) / "tools" / "msopprof" / "bin" / "msopprof")
        candidates.append(Path(toolkit) / "bin" / "msopprof")
    candidates.append(Path("/usr/local/Ascend/ascend-toolkit/latest/bin/msopprof"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _msopprof_usable(binary):
    """跑一次 --help 确认是支持本协议的 msopprof。返回 (可用?, 说明)。"""
    try:
        result = subprocess.run(
            [str(binary), "--help"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{binary} --help 起不来：{exc}"
    if result.returncode != 0:
        return False, f"{binary} --help 退出码 {result.returncode}"
    text = (result.stdout or b"").decode("utf-8", "replace")
    if "--launch-count" not in text:
        return False, f"{binary} 不认 --launch-count，版本过旧"
    return True, ""


def parse_op_summary(output_dir, launch_limit=None):
    """汇总一次采样的 kernel 耗时。返回 (duration 之和, launch 数)。

    读数是递归 glob 命中的**全部文件、全部数据行**的 Task Duration(us) 之和。
    多份多行是多 launch 算子的正常形态,不是异常——cherk 每次 API 调用发 6 个
    kernel(1 反交织 + 4 GEMM + 1 合并)。行数即该次进程实际发生的 launch 数。

    launch_limit 给出本次的 --launch-count。行数等于它时抛 ProfileTruncatedError:
    工具撞上限后静默停止给后续 kernel 分配采集路径、只记 debug 日志,这时无法区分
    「恰好这么多」与「被截断」,而截断的后果是求和少算、ratio 虚高、假 PASS。
    这是本协议唯一会把 FAIL 写成 PASS 的路径,必须 fail-closed。"""
    root = Path(output_dir)
    files = sorted(root.glob(OP_BASIC_INFO_GLOB)) + sorted(
        root.glob("OPPROF_*/OpBasicInfo*.csv")
    )
    seen = set()
    kernel_us = 0.0
    launches = 0
    for path in files:
        resolved = path.resolve()
        if resolved in seen:      # 两个 glob 在扁平布局上会重叠
            continue
        seen.add(resolved)
        rows_here = 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or [])
            required = set(OP_BASIC_INFO_COLUMNS.values())
            if not required.issubset(columns):
                missing = ", ".join(sorted(required - columns))
                raise ProfileParseError(f"{path.name} 缺列：{missing}")
            for row_index, row in enumerate(reader, 2):
                raw_duration = (row.get(OP_BASIC_INFO_COLUMNS["duration_us"]) or "").strip()
                try:
                    duration = float(raw_duration)
                except ValueError as exc:
                    raise ProfileParseError(
                        f"{path.name} 第 {row_index} 行 duration 非数值"
                    ) from exc
                if not math.isfinite(duration) or duration <= 0:
                    raise ProfileParseError(
                        f"{path.name} 第 {row_index} 行 duration 须为有限正数"
                    )
                kernel_us += duration
                launches += 1
                rows_here += 1
        if rows_here == 0:
            # 只有表头的 CSV 意味着那一次 launch 的耗时没落盘。放它过去,剩下的行
            # 照常求和,少算的部分直接抬高 ratio——按产物异常拒绝,不当零行忽略。
            raise ProfileParseError(f"{path.name} 只有表头,没有数据行")
    missing = _launch_dirs_without_csv(root, seen)
    if missing:
        raise ProfileParseError(
            "以下采集目录没有 OpBasicInfo CSV，该次 launch 的耗时缺失："
            + "、".join(missing[:5]) + (" 等" if len(missing) > 5 else "")
        )
    if launch_limit is not None and launches >= launch_limit:
        raise ProfileTruncatedError(
            f"采到 {launches} 个 launch，等于 --launch-count 上限 {launch_limit}，"
            "无法确认是否被截断；当前采集口径不支持该用例的 launch 规模"
        )
    return kernel_us, launches


def _gtest_evidence(path, gtest_name):
    """校验一次采样的执行成功证据 r<N>.gtest.json(gtest --gtest_output 写出)。

    合格是正向条件:文件可读、可解析为 JSON 对象、目标完整 gtest 名的记录存在、
    状态为已执行完成(非 SKIPPED/NOTRUN)、无 failure 记录。读取、解析、结构校验的
    任何失败一律不合格,不对缺失字段按成功默认。返回 (合格?, 不合格原因)。
    结构解析与精度侧 _gtest_records 同构(testsuites[].testsuite[],
    全名 = "<suite名>.<test名>")。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, f"证据文件不可读或非 JSON({exc})"
    if not isinstance(payload, dict):
        return False, "证据文件顶层不是 JSON 对象"
    suites = payload.get("testsuites")
    if not isinstance(suites, list):
        return False, "证据缺少 testsuites 列表"
    target = None
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        suite_name = suite.get("name", "")
        tests = suite.get("testsuite")
        if not isinstance(tests, list):
            continue
        for test in tests:
            if not isinstance(test, dict):
                continue
            test_name = test.get("name", "")
            full_name = f"{suite_name}.{test_name}" if suite_name else test_name
            if full_name == gtest_name:
                target = test
                break
        if target is not None:
            break
    if target is None:
        return False, f"证据中无目标用例 {gtest_name!r} 的记录"
    result = str(target.get("result", "")).upper()
    status = str(target.get("status", "")).upper()
    if result == "SKIPPED" or status in {"SKIPPED", "NOTRUN"}:
        return False, "目标用例未执行(SKIPPED/NOTRUN)"
    # 完成标记两字段同时要求(A3 机真机 JSON 直证并存);缺失或未知值一律不合格,
    # 不按成功默认——旧版 gtest 若无 result 字段会在此显式 CRASH,fail-closed 方向。
    if status != "RUN" or result != "COMPLETED":
        return False, f"目标用例无已执行完成标记(status={status or '缺失'}, result={result or '缺失'})"
    # gtest 成功记录正常省略 failures;字段存在时必须是数组,畸形值即结构失败。
    failures = target.get("failures", [])
    if not isinstance(failures, list):
        return False, f"证据 failures 字段畸形(类型 {type(failures).__name__})"
    if failures:
        return False, f"目标用例含 {len(failures)} 条 failure 记录"
    return True, None


def _run_process(command, timeout, cwd=None, env=None):
    try:
        result = subprocess.run(
            command,
            cwd=None if cwd is None else str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout, None
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return None, output, "TIMEOUT"
    except OSError as exc:
        # 进程起不来是环境问题,不是被测对象崩了。调用方据此走 EnvironmentAbort。
        return None, str(exc), "LAUNCH_FAILED"


def _write_log(path, content):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _progress(message):
    """进度与 warning 通道:逐条打到 stderr 并立即 flush,便于实时观测;
    判定面仍只看结果 JSON 与 stdout 表格。"""
    print(message, file=sys.stderr, flush=True)


def _case_record(row, gtest_name):
    record = {
        "name": row["case_name"],
        "gtest_name": gtest_name,
        "status": None,
        "kernel_us": None,
        "samples": [],
        "launches": [],
        "gpu_ms": None,
        "ratio": None,
        "spread": None,
        "verdict": None,
        "message": "",
        # 逐例诊断 warning(与顶层 baseline_warnings 分开):判定触发时记录,
        # 文本含 repeat 序号、采集工具退出码、执行成功证据文件路径。
        "warnings": [],
    }
    return record


def _with_scope_caveat(verdict, scope_caveat):
    return verdict + " (scope caveat)" if scope_caveat else verdict


def _measure_case(args, binary, msprof, row, gtest_name, references, profile_root,
                  run_env=None):
    record = _case_record(row, gtest_name)
    if run_env is None:
        run_env = dict(os.environ)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", row["case_name"])
    case_dir = profile_root / safe_name
    case_dir.mkdir(parents=True, exist_ok=True)
    case_dir = case_dir.resolve()
    for repeat in range(1, args.repeats + 1):
        output_dir = case_dir / f"r{repeat}"
        gtest_json = case_dir / f"r{repeat}.gtest.json"
        # 每 case 恰一次采集。新语法是 msopprof [options] <app> [app args]:
        # --output 及其余选项必须排在被测二进制之前,其后一律视为被测程序的参数
        # (放错位置会报 "output dir is not writable",错误信息有误导性)。
        # --application= 已废弃;--ai-core/--task-time 在新命令下是硬错误(退 255)。
        # r<N>.gtest.json 仍是本次采样的「执行成功证据」,由 gtest 在 RUN_ALL_TESTS
        # 返回时写出,证据链与旧后端一致。
        space_problem = _check_free_space(output_dir, args.launch_count)
        if space_problem:
            raise EnvironmentAbort("DISK_SPACE", space_problem)
        command = [
            str(msprof),
            # **必须用 = 形式**:空格分隔会报 "argument --output miss value"
            # (A3 实测,CANN 9.0.1)。错误信息不提示形式问题,容易误判成路径不可写。
            f"--output={output_dir}",
            "--aic-metrics=BasicInfo",
            f"--launch-count={args.launch_count}",
            str(binary),
            f"--gtest_filter={gtest_name}",
            f"--gtest_output=json:{gtest_json}",
        ]
        code, output, problem = _run_process(command, args.timeout, env=run_env)
        _write_log(case_dir / f"r{repeat}.log", output)
        # 判定顺序固定,先到先定,**环境问题必须排在算子证据之前**:
        # 1 超时 → TIMEOUT;2 工具自身非零退出或日志显示写盘失败 → 整轮中止
        # (EnvironmentAbort,退 ENVIRONMENT_EXIT);3 gtest 证据不合格 → CRASH;
        # 4 无文件/无行/缺列/值非有限正数 → NO_KERNEL;5 截断 → NO_KERNEL;6 计分。
        # 第 2 条不恢复旧的「非零 → NO_KERNEL」:工具失败是环境问题,记成算子问题
        # 会让排错方向完全相反。磁盘满可能先让 gtest JSON 写不出而撞第 3 条,
        # 所以日志检查必须排在证据检查之前。
        if problem == "LAUNCH_FAILED":
            raise EnvironmentAbort(
                "PROFILER_FAILED",
                f"{row['case_name']} 第 {repeat} 次采集进程起不来：{output}",
            )
        if problem:
            record["status"] = problem
            record["verdict"] = problem
            record["message"] = f"第 {repeat} 次采集未完成"
            return record
        failure_hit = _collection_failure_marker(output)
        if code != 0 or failure_hit:
            reason = "COLLECTION_FAILED" if failure_hit else "PROFILER_FAILED"
            detail = (
                f"{row['case_name']} 第 {repeat} 次采集："
                + (f"日志出现「{failure_hit}」" if failure_hit
                   else f"采集工具退出码 {code}")
                + f"；日志 {case_dir / f'r{repeat}.log'}"
            )
            raise EnvironmentAbort(reason, detail)
        evidence_ok, evidence_detail = _gtest_evidence(gtest_json, gtest_name)
        if not evidence_ok:
            record["warnings"].append(
                f"r{repeat} 执行成功证据缺失/不合格({evidence_detail});"
                f"采集工具退出码 {code};证据文件 {gtest_json}"
            )
            record["status"] = "CRASH"
            record["verdict"] = "CRASH"
            record["message"] = (
                f"第 {repeat} 次执行成功证据缺失/不合格:{evidence_detail}"
            )
            return record
        try:
            kernel_us, launches = parse_op_summary(output_dir, args.launch_count)
        except ProfileTruncatedError as exc:
            # 截断是单例属性(大规模用例撞上限而别的用例不会),所以逐例记而不是
            # 整轮中止。状态用词表里的 NO_KERNEL,措辞必须落在采集口径上——
            # 这不是算子没起 kernel,是这一例的 launch 规模超出当前采集能力。
            record["warnings"].append(
                f"r{repeat} {exc};提高 --launch-count 后重采可解，"
                f"但产物体积随 launch 数线性增长;证据文件 {gtest_json}"
            )
            record["status"] = "NO_KERNEL"
            record["verdict"] = "NO_KERNEL"
            record["message"] = f"第 {repeat} 次{exc}"
            return record
        except (OSError, UnicodeError, csv.Error, ProfileParseError) as exc:
            record["warnings"].append(
                f"r{repeat} OpBasicInfo 解析失败;采集工具退出码 {code};"
                f"证据文件 {gtest_json}"
            )
            record["status"] = "NO_KERNEL"
            record["verdict"] = "NO_KERNEL"
            record["message"] = f"第 {repeat} 次解析失败：{exc}"
            return record
        if launches == 0:
            record["warnings"].append(
                f"r{repeat} OpBasicInfo 缺失或无数据行;采集工具退出码 {code};"
                f"证据文件 {gtest_json}"
            )
            record["status"] = "NO_KERNEL"
            record["verdict"] = "NO_KERNEL"
            record["message"] = f"第 {repeat} 次采样没有 kernel 行"
            return record
        # 读数即该次进程全部 kernel launch 的 duration 之和,不再按 calls_per_case
        # 归一——msopprof 采的就是这次调用实际发生的 launch,没有重复计入。
        record["samples"].append(kernel_us)
        record["launches"].append(launches)

    median_us = float(statistics.median(record["samples"]))
    if median_us <= 0:
        record["status"] = "NO_KERNEL"
        record["verdict"] = "NO_KERNEL"
        record["message"] = "kernel duration 中位数不大于 0"
        return record
    record["kernel_us"] = median_us
    # repeats=1 时 median=该值、spread=0:spread=0 表示无样本间差异可算,
    # 不是稳定性证明;字段结构不随 repeats 变。
    record["spread"] = (
        max(record["samples"]) - min(record["samples"])
    ) / median_us
    reference = references.get(_key_for_row(row))
    if reference is None:
        record["status"] = "NO_REF"
        record["verdict"] = "NO_REF"
        return record
    record["gpu_ms"] = reference
    record["ratio"] = reference / (median_us / 1000.0)
    if record["ratio"] >= PERF_THRESHOLD:
        record["status"] = "PASS"
        record["verdict"] = "PASS"
    else:
        record["status"] = "FAIL"
        record["verdict"] = "FAIL"
    return record


def _summarize(cases, timing_scope):
    counts = {
        name: sum(case["status"] == name for case in cases)
        for name in ("PASS", "FAIL", "NO_REF", "NO_KERNEL", "CRASH", "TIMEOUT", "MISSING")
    }
    insufficient = counts["NO_KERNEL"] + counts["CRASH"]
    insufficient += counts["TIMEOUT"] + counts["MISSING"]
    if insufficient:
        status = "证据不足"
        exit_code = INSUFFICIENT_EXIT
    elif counts["FAIL"]:
        status = "不通过"
        exit_code = FAILURE_EXIT
    elif not (counts["PASS"] or counts["FAIL"]):
        # 没有一条可比较的用例：有 PF 却无基线，或全被 --case/--filter 收窄掉。
        status = "NO_REF"
        exit_code = 0
    else:
        status = "通过"
        exit_code = 0
    return {
        "expected": len(cases),
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "no_ref": counts["NO_REF"],
        "no_kernel": counts["NO_KERNEL"],
        "crash": counts["CRASH"],
        "timeout": counts["TIMEOUT"],
        "missing": counts["MISSING"],
        "status": status,
        "timing_scope": timing_scope,
        "threshold": PERF_THRESHOLD,
        "scope_caveat": timing_scope != "kernel",
    }, exit_code


def _print_cases(cases, summary):
    print("case_name status kernel_us gpu_ms ratio spread verdict")
    for case in cases:
        values = [
            case["name"],
            case["status"],
            case["kernel_us"],
            case["gpu_ms"],
            case["ratio"],
            case["spread"],
            case["verdict"],
        ]
        print(" ".join("-" if value is None else str(value) for value in values))
    print(
        f"summary: {summary['status']}，PASS={summary['pass']}，FAIL={summary['fail']}，"
        f"NO_REF={summary['no_ref']}，证据不足="
        f"{summary['no_kernel'] + summary['crash'] + summary['timeout'] + summary['missing']}"
    )


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="ops-blas 仓库根目录")
    parser.add_argument("--soc", required=True, help="目标 SoC，例如 ascend910b3")
    parser.add_argument(
        "--device", default="0",
        help="目标物理卡号，或 auto（起跑时从 --device-pool 逐卡选首张空闲卡）",
    )
    parser.add_argument(
        "--device-pool", default=None,
        help="auto 的候选物理卡池，逗号分隔（默认 0-7）；显式卡号时给出即报错",
    )
    parser.add_argument(
        "--map-device", action="store_true",
        help="仅复测模式:对显式 --device K 走 auto 同款运行时映射(K 落到逻辑卡 "
             "--compiled-device 位,构建号取 --compiled-device)。首轮为 auto 的复测由"
             "启动方按首轮绑卡方式传入;首轮传入即参数错误",
    )
    parser.add_argument(
        "--compiled-device", type=int, default=0,
        help=f"被测二进制编译期固定的进程内逻辑卡号(0-{MAX_COMPILED_DEVICE},默认 0),"
             "非 0 时须同时置位 --map-device。由启动方按 harness profile 推导后传入,"
             "量具只按此值构造映射串与构建号,不读历史、不判断是否换卡;"
             "首轮传非 0 即参数错误",
    )
    parser.add_argument("--skip-build", action="store_true", help="复用上次编译产物")
    parser.add_argument("--build-timeout", type=int, default=1800, help="编译超时秒数")
    parser.add_argument("--timeout", type=int, default=3600, help="每个进程的超时秒数")
    parser.add_argument(
        "--repeats", type=int, default=REPEATS,
        help="每例采样次数(默认 1,单次采集;>1 时取中位数)",
    )
    parser.add_argument(
        "--calls-per-case",
        type=int,
        default=1,
        help="一条 gtest 用例调用被测接口的次数；当前只接受 1（读数不再按它归一）",
    )
    parser.add_argument(
        "--launch-count",
        type=int,
        default=DEFAULT_LAUNCH_COUNT,
        help=f"单次采集的 kernel launch 上限(1-5000,默认 {DEFAULT_LAUNCH_COUNT})；"
             "采到的行数等于它即判截断、不计分",
    )
    parser.add_argument("--msprof", help="覆盖 msopprof 可执行文件路径")
    parser.add_argument(
        "--keep-prof",
        action="store_true",
        default=True,
        help="保留采集原始目录；当前默认保留",
    )
    parser.add_argument("--case", action="append", help="精确复跑 case_name，可重复")
    parser.add_argument("--filter", help="按 case_name 子串收窄性能集")
    parser.add_argument(
        "--run-id",
        default=datetime.now().strftime("%Y%m%dT%H%M%S"),
        help="结果运行标识",
    )
    parser.add_argument("--out", type=Path, help="结果 JSON 路径")
    return parser


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(raw_argv)
    requested_device, device_pool = _parse_device_request(args.device, args.device_pool)
    if (
        requested_device == "auto"
        and args.skip_build
        and BUILD_CONVENTION["build_device_flag"]
    ):
        # 守法（评审裁定）：编译期定卡域的 auto 依赖二进制按逻辑卡 0 构建，
        # skip-build 无法证明这一点，直接拒绝，本次量具必须重建。
        raise SystemExit(
            "--device auto 在编译期定卡域不允许 --skip-build（须以 --device=0 重建）"
        )
    if args.build_timeout <= 0 or args.timeout <= 0 or args.repeats <= 0:
        parser.error("timeout 与 repeats 必须为正整数")
    if args.calls_per_case != 1:
        # 冻结为 1:读数已是该次进程全部 launch 之和,再归一就是二次归一。
        # 字段保留是因为它同时是复测绑定锚、evidence_id 哈希输入与 check.json
        # 契约比对项,删字段会让已在盘的全部 check.json 自检失败。
        parser.error("--calls-per-case 当前只接受 1（读数不再按它归一）")
    if not 1 <= args.launch_count <= 5000:
        parser.error("--launch-count 合法范围 1-5000")
    if not 0 <= args.compiled_device <= MAX_COMPILED_DEVICE:
        parser.error(
            f"--compiled-device 合法范围 0-{MAX_COMPILED_DEVICE}:映射串共 N+1 位,"
            f"每位都要一个互不相同的真实卡号,超出默认 {len(DEVICE_CARDS)} 卡假设;"
            "该机器卡数不足时换卡不成立,改用同卡复测"
        )
    results = Path(__file__).resolve().parent / "results"
    if not RUN_ID_RE.fullmatch(args.run_id):
        print("RUN_ID_INVALID: run-id 只能含字母、数字、点、下划线和连字符", file=sys.stderr)
        return ENVIRONMENT_EXIT
    # 复测模式入口一次解析;retest 为 None 即首轮。
    retest = _parse_retest_mode(args.run_id)
    if retest is not None:
        if args.out is not None:
            parser.error("复测模式不接受 --out(结果固定落 results/performance_<run-id>.json)")
        if requested_device == "auto":
            parser.error("复测模式不接受 --device auto(须显式指定物理卡)")
        if args.filter is not None:
            parser.error("复测模式不接受 --filter(逐例点名用 --case)")
        if not args.case:
            parser.error("复测模式必须用 --case 点名,不可为空")
        if len(args.case) != len(set(args.case)):
            parser.error("复测模式 --case 点名重复")
        if args.compiled_device and not args.map_device:
            # 依附关系(同 --device-pool 之于 --device auto):编译逻辑卡位只在
            # 重映射下才用得上——不置位 --map-device 时它既不进映射串也不进构建号,
            # 留着就是个静默失效的参数,不如当场报错。
            parser.error("--compiled-device 非 0 时必须同时置位 --map-device")
    elif args.map_device or args.compiled_device:
        parser.error(
            "--map-device 与非 0 的 --compiled-device 仅复测模式有效"
            "(由启动方按首轮绑卡方式传入)"
        )
    out_path = args.out or results / f"performance_{args.run_id}.json"
    # 证据保护(先决):目标结果 JSON 已存在即拒绝起跑——不写任何文件、不建目录、
    # 不编译、不探卡。既有结果一经写出不可变,重复 run-id 不覆盖。
    if out_path.exists():
        print(
            f"RUN_ID_EXISTS: 结果 JSON 已存在,不写任何文件:{out_path}",
            file=sys.stderr,
        )
        return ENVIRONMENT_EXIT
    arch = _arch_for_soc(args.soc)
    payload = _base_result(args, arch, _timestamp())
    run_dir = results / args.run_id / "performance"

    def _fail(reason, message):
        # 环境失败统一出口(新增失败路径一律走这里)。首轮保持现行错误 JSON
        # 行为;复测轮不写结果 JSON,改留中断轮足迹。
        if retest is not None:
            _retest_footprint(run_dir, reason, message)
        return _environment_error(
            payload, out_path, reason, message, allow_error_json=retest is None
        )

    if retest is not None:
        # 复测 preflight:建目录/编译/探卡之前,从完整 CSV+基线算性能期望集
        # (不受 --case 收窄),点名必须全部落在期望集内。
        # 兜 AttributeError:短行(缺列)数据经 DictReader 出 None,加载器对它调
        # 字符串方法会崩;preflight 发生在建目录前,不兜就是零足迹裸 traceback。
        # 加载器本体不改——首轮同输入的现行行为保持原样(byte-compat)。
        package_dir = Path(__file__).resolve().parent
        try:
            all_rows = _read_csv_rows(package_dir / CSV_NAME, "TC_PF_")
        except (OSError, UnicodeError, csv.Error, ValueError, AttributeError) as exc:
            return _fail("CSV_INVALID", str(exc))
        try:
            _, preflight_refs, _ = _load_gpu_baseline(package_dir / "gpu_baseline.csv")
        except (OSError, UnicodeError, csv.Error, ValueError, AttributeError) as exc:
            return _fail("BASELINE_INVALID", str(exc))
        expected_names = {
            row["case_name"] for row in _comparable_rows(all_rows, preflight_refs)[0]
        }
        unknown = [name for name in args.case if name not in expected_names]
        if unknown:
            # 参数类错误(spec:未知点名属参数错误):退 2,零副作用,不占轮号。
            parser.error("UNKNOWN_CASE: 点名不在性能期望集:" + ", ".join(unknown))
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return _fail("RUN_ID_EXISTS", f"运行目录已存在：{run_dir}")
    if arch is None:
        return _fail("CSV_NOT_DEPLOYED", "SoC 无 arch 映射")
    csv_path = _find_source_csv(args.repo, arch)
    if csv_path is None:
        return _fail("CSV_NOT_DEPLOYED", "找不到部署 CSV")
    payload["csv_path"] = str(csv_path)
    payload["csv_sha256"] = _sha256(csv_path)
    payload["calls_per_case"] = args.calls_per_case
    device_explicit = any(
        item == "--device" or item.startswith("--device=") for item in raw_argv
    )
    if args.skip_build:
        if device_explicit and BUILD_CONVENTION["build_device_flag"]:
            # 仅编译期定卡的域需要此提醒；运行时绑卡的域（visible_devices_env）不受影响。
            print("设备号在编译期固定（-DTEST_DEVICE_ID），跳过编译时以上次编译为准")
    else:
        build_log = run_dir / "build.log"
        if _run_build(args, build_log) != 0:
            return _fail("BUILD_FAILED", f"见 {build_log}")
        reason, message = _check_build_lists(args.repo)
        if reason:
            return _fail(reason, message)
    binary, binary_message, binary_reason = _find_binary(args.repo)
    if binary is None:
        return _fail(binary_reason, binary_message)
    payload["binary"] = str(binary)
    payload["binary_sha256"] = _sha256(binary)
    resolved_device, gate_payload = _resolve_device(requested_device, device_pool)
    payload["npu_gate"] = gate_payload
    payload["device_resolved"] = resolved_device
    if resolved_device is None:
        payload["exit_code"] = IDLE_GATE_EXIT
        final = gate_payload.get("final") or {}
        gate_reason = f"NPU_GATE_{final.get('status', 'BLOCKED')}"
        gate_message = (
            f"{final.get('detail', '无可用空闲卡')}（候选 {len(gate_payload['attempts'])} 张）"
        )
        # 与环境失败同口径:首轮照写(目标已存在即不写,证据保护);复测轮不写
        # JSON,留中断轮足迹(原因即空闲门 BUSY/QUERY_FAILED 判定)。
        if retest is None:
            _commit_result(out_path, payload)
        else:
            _retest_footprint(run_dir, gate_reason, gate_message)
        print(f"{gate_reason}: {gate_message}", file=sys.stderr)
        return IDLE_GATE_EXIT
    # 先读任务包 TC_PF_ 行：映射面用全集精确匹配（不筛命名前缀）。
    try:
        package_rows = _selected_rows(Path(__file__).resolve().parent / CSV_NAME, args)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _fail("CSV_INVALID", str(exc))
    run_env = _run_environment(
        args.repo,
        resolved_device,
        # --map-device:显式卡号走 auto 同款重映射(复测首轮为 auto 时由启动方
        # 传入,否则编译逻辑 0 的二进制会落在卡 0 而记录却是显式卡号)。换卡轮
        # --compiled-device 非 0 时目标卡被顶到第 N 位,二进制在逻辑 N 上落到它。
        auto=(requested_device == "auto" or args.map_device),
        compiled_device=args.compiled_device,
    )
    mapping, message, reason = _list_tests(
        binary,
        args.timeout,
        {row["case_name"] for row in package_rows},
        run_env,
    )
    if reason:
        return _fail(reason, message)
    baseline_path = Path(__file__).resolve().parent / "gpu_baseline.csv"
    payload["gpu_baseline_path"] = str(baseline_path)
    try:
        metadata, references, baseline_warnings = _load_gpu_baseline(baseline_path)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _fail("BASELINE_INVALID", str(exc))
    # 锚字段:对实际读取的运行时规范化基线实算哈希(不从 manifest 抄值),
    # 只随完整结果写出,供复测轮绑定校验取锚。
    baseline_sha256 = _sha256(baseline_path)
    payload["baseline_warnings"] = baseline_warnings
    for warning in baseline_warnings:
        _progress(f"BASELINE_WARNING: {warning}")
    # 期望集 = 任务包 CSV 里有可比 GPU 基线的 TC_PF_ 行；没有基线的行不跑，只计数。
    try:
        expected_rows, ignored = _comparable_rows(package_rows, references)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _fail("CSV_INVALID", str(exc))
    payload["ignored_no_ref"] = ignored
    payload["gtest_filter"] = ":".join(
        mapping[row["case_name"]]
        for row in expected_rows
        if row["case_name"] in mapping
    )
    timing_scope = metadata.get("timing_scope", "unspecified")
    mapped_rows = [row for row in expected_rows if row["case_name"] in mapping]
    msprof = _resolve_msprof(args.msprof) if mapped_rows else None
    if mapped_rows and msprof is None:
        return _fail("MSPROF_NOT_FOUND", "找不到可执行的 msopprof")
    if msprof is not None and args.msprof is None:
        # 只在自动发现时探。显式传 --msprof 是使用者的覆盖,不二次猜疑——
        # 这道探测防的是「旧 CANN 上自动找到了不支持本协议的同名文件」,
        # 不是防使用者给错路径。
        usable, why = _msopprof_usable(msprof)
        if not usable:
            # 可执行不等于可用:旧 CANN 上有同名文件却不支持本协议时,不报成
            # 「找不到」——那会让人去查 PATH,而真正的原因是版本。
            return _fail("MSPROF_UNUSABLE", why)
    payload["msprof"] = None if msprof is None else str(msprof)
    profile_root = run_dir / "prof"
    cases = []
    scope_caveat = timing_scope != "kernel"
    total = len(expected_rows)
    # 预估只是开跑提示:单例预估 × 每例采样次数;不参与判定。
    estimate_min = (total * PER_CASE_ESTIMATE_S * args.repeats + 59) // 60
    _progress(f"性能采样:{total} 例,预估 ~{estimate_min} min")
    loop_started = time.monotonic()
    done = 0
    for row in expected_rows:
        case_started = time.monotonic()
        name = row["case_name"]
        gtest_name = mapping.get(name)
        if gtest_name is None:
            record = _case_record(row, None)
            record["status"] = "MISSING"
            record["verdict"] = "MISSING"
            record["message"] = "部署 CSV 用例未出现在 --gtest_list_tests"
        else:
            try:
                record = _measure_case(
                    args, binary, msprof, row, gtest_name, references, profile_root,
                    run_env=run_env,
                )
            except EnvironmentAbort as abort:
                # 采集环境坏了就地中止:继续跑只会把同一个环境问题记成一串
                # 算子失败。已完成的例保留在现场目录里,重跑时不用从头来。
                _progress(f"采集环境失败，中止本轮：{abort.message}")
                return _fail(abort.reason, abort.message)
        record["verdict"] = _with_scope_caveat(record["verdict"], scope_caveat)
        cases.append(record)
        done += 1
        case_elapsed = time.monotonic() - case_started
        cumulative = time.monotonic() - loop_started
        # ETA 按已完成例的平均耗时外推;进度行在每例完成后打印,此时至少有
        # 一例完成,「待估」只是无完成样本时的守护分支。
        if done:
            eta_text = f"ETA{(cumulative / done) * (total - done) / 60:.1f}m"
        else:
            eta_text = "ETA待估"
        _progress(
            f"[{done}/{total}] {name} {record['status']} "
            f"{case_elapsed:.1f}s 累计{cumulative / 60:.1f}m {eta_text}"
        )
    _progress(
        f"性能采样结束:{total} 例,累计{(time.monotonic() - loop_started) / 60:.1f}m"
    )
    payload["cases"] = cases
    summary, exit_code = _summarize(cases, timing_scope)
    payload["summary"] = summary
    payload["exit_code"] = exit_code
    # 锚字段(复测绑定锚点,顶层加法扩展):规范化基线与量具脚本自身的 SHA-256。
    payload["normalized_baseline_sha256"] = baseline_sha256
    payload["verifier_sha256"] = _sha256(Path(__file__).resolve())
    if retest is not None:
        # 复测轮记录(schema v1):身份与轮号、点名清单、设备字段、绑定锚里
        # 首轮顶层缺席的 threshold、存证 argv(仅参考,不作机械判定输入)。
        # 其余绑定字段(binary/csv/normalized_baseline/verifier 四哈希与
        # calls_per_case)已在顶层。
        payload["schema_version"] = 1
        payload["base_run_id"] = retest["base_run_id"]
        payload["round"] = retest["round"]
        payload["kind"] = "measure"
        payload["requested_cases"] = list(args.case)
        # device_requested 记 --device(目标物理卡),device_resolved 记实际执行卡
        # (已在空闲门处落盘,显式卡号下两者相等)。
        payload["device_requested"] = requested_device
        # device_compiled 记本轮映射用的编译逻辑卡位,只在 --map-device 置位时出现
        # ——没有重映射就没有这个位,写个默认 0 会谎报编译期 TEST_DEVICE_ID(首轮显式
        # 卡 N 的同卡复测里它其实是 N),accept 的轮次有效性会照此判该轮无效。
        # 值是启动方按 harness profile 推导后声明的,量具原样记录,不反推也不与首轮
        # 核对(核对在 accept 一侧)。
        if args.map_device:
            payload["device_compiled"] = args.compiled_device
        payload["threshold"] = PERF_THRESHOLD
        payload["argv"] = [sys.argv[0], *raw_argv]
    payload["finished"] = _timestamp()
    if not _commit_result(out_path, payload):
        # 起跑后目标被外部创建:结果未落盘,按环境失败退出,不打成功表格;
        # 复测轮留中断轮足迹(竞态措辞与 _commit_result 的 stderr 一致)。
        if retest is not None:
            _retest_footprint(
                run_dir, "RUN_ID_EXISTS",
                f"结果 JSON 在运行期间被外部创建,不写:{out_path}",
            )
        return ENVIRONMENT_EXIT
    _print_cases(cases, summary)
    print(f"result: {out_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
