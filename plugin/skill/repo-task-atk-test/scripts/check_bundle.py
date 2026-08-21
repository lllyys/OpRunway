"""S0 入口：核验生成侧交接包、任务书、ATK 版本与 PR 接口。

量具只在交接包副本中运行。它重算封印清单登记文件的 SHA256，核对本次任务书与
验收机环境，并在 aclnn 模式下进程内解析 PR 头文件。四项检查互不短路，结论统一
写入 ``evidence/bundle_intake.json``。

退出码：0 表示全部适用项通过；2 表示至少一项不满足；3 表示清单或必需输入不可用。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from _case_utils import file_sha256
from _signature_parse import (
    AlignError,
    parse_signature,
    read_header_signature,
    reject_installed_header,
    require_project_source,
)
import _stage_card
import _taskdoc


SUPPORTED_SCHEMA_VERSIONS = (1,)
EXPECTED_EXCLUDED = (
    "evidence/timeline.jsonl",
    "evidence/repro.sh",
    "evidence/bundle.json",
    "evidence/env.json",
    "evidence/env.sh",
)
EXPECTED_IGNORED_DIRS = ("__pycache__",)
ROOT_FROZEN_SUFFIXES = (
    "_decl.json",
    "_constraint.py",
    "_materialize.py",
    "_materialized.json",
)


class IntakeFailure(RuntimeError):
    """清单或必需输入无法可靠读取。"""


def parser():
    """构造命令行帮助。"""
    cli = argparse.ArgumentParser(
        prog="check_bundle.py",
        description=(
            "在交接包副本中重算封印摘要，核对任务书、ATK 版本，"
            "并在 aclnn 模式下核对 PR 头文件接口。"
        ),
        epilog=(
            "退出码：0 全部适用项通过；2 至少一道接收门失败；"
            "3 清单、任务书或环境输入不可用。"
        ),
    )
    cli.add_argument(
        "-C", "--dir", default=".", metavar="<目录>",
        help="交接包副本根目录，默认当前目录",
    )
    cli.add_argument("--task-doc", required=True, metavar="<md>", help="本次验收任务书")
    cli.add_argument(
        "--env", required=True, metavar="<json>",
        help="验收机刚由 probe_env.py 生成的环境指纹",
    )
    cli.add_argument(
        "--header", metavar="<路径>",
        help="aclnn 模式下待验收 PR 的头文件或头文件目录",
    )
    cli.add_argument("--aclnn-name", metavar="<名>", help="YAML 使用的 aclnn_name")
    cli.add_argument(
        "-o", "--output", default="evidence/bundle_intake.json", metavar="<json>",
        help="接收结果路径，默认 evidence/bundle_intake.json",
    )
    return cli


def read_json_object(path, label):
    """读取 JSON 对象；无法读取时转成约定的输入错误。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntakeFailure(f"读不出 {label}：{exc}") from exc
    if not isinstance(payload, dict):
        raise IntakeFailure(f"{label} 必须是 JSON 对象")
    return payload


def resolve_bundle_path(root, value):
    """相对输入优先按交接包根解析，兼容从外部目录调用。"""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    under_bundle = root / path
    if under_bundle.exists():
        return under_bundle.resolve()
    return path.resolve()


def require_manifest(root):
    """读取清单并核对当前认识的 schema 与基本字段形态。"""
    path = root / "evidence" / "bundle.json"
    manifest = read_json_object(path, "evidence/bundle.json")
    version = manifest.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        known = "、".join(str(item) for item in SUPPORTED_SCHEMA_VERSIONS)
        raise IntakeFailure(
            f"bundle.json schema_version={version!r} 不认识；当前认识的版本：{known}"
        )

    expected_shapes = {
        "files": dict,
        "task_doc": dict,
        "atk": dict,
        "interface": dict,
        "excluded": list,
        "ignored_dirs": list,
    }
    malformed = [
        name for name, kind in expected_shapes.items()
        if not isinstance(manifest.get(name), kind)
    ]
    if malformed:
        raise IntakeFailure("bundle.json 字段类型不对：" + "、".join(malformed))
    return path, manifest


def safe_manifest_path(root, relative):
    """把清单键约束为交接包根下的相对 POSIX 路径。"""
    if not isinstance(relative, str) or not relative:
        raise ValueError("文件键不是非空字符串")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ValueError("文件键越出交接包根")
    path = root.joinpath(*pure.parts)
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("文件键越出交接包根") from exc
    return path


def root_name_is_frozen(name):
    """根目录里哪些新增文件属于被冻结的 S2 产物。"""
    return (
        name.endswith(".yaml")
        or name.endswith(ROOT_FROZEN_SUFFIXES)
        or (name.startswith("must_cover") and name.endswith(".json"))
        or (name.startswith("function_") and name.endswith(".py"))
    )


def protected_extra(relative):
    """新增路径是否落在三类禁止追加的位置。"""
    parts = PurePosixPath(relative).parts
    if not parts:
        return False
    if parts[0] == "result":
        return True
    if parts[0].startswith("frozen_"):
        return True
    return len(parts) == 1 and root_name_is_frozen(parts[0])


def check_integrity(root, manifest):
    """重算 files，并只在约定的冻结位置拦截新增文件。"""
    missing = []
    mismatched = []
    unexpected = []
    manifest_problems = []
    registered = set()

    if manifest.get("excluded") != list(EXPECTED_EXCLUDED):
        manifest_problems.append("excluded 不是约定的五个固定路径")
    if manifest.get("ignored_dirs") != list(EXPECTED_IGNORED_DIRS):
        manifest_problems.append("ignored_dirs 不是 ['__pycache__']")

    for relative, expected in sorted(manifest["files"].items(), key=lambda item: str(item[0])):
        try:
            path = safe_manifest_path(root, relative)
        except ValueError as exc:
            manifest_problems.append(f"files[{relative!r}]：{exc}")
            continue
        registered.add(relative)
        if not isinstance(expected, str):
            manifest_problems.append(f"files[{relative!r}] 的摘要不是字符串")
            continue
        if path.is_symlink() or not path.is_file():
            missing.append(relative)
            continue
        try:
            actual = file_sha256(path)
        except OSError as exc:
            manifest_problems.append(f"{relative} 无法计算 SHA256：{exc}")
            continue
        if actual != expected:
            mismatched.append(relative)

    excluded = set(EXPECTED_EXCLUDED)
    ignored = set(EXPECTED_IGNORED_DIRS)
    try:
        paths = sorted(root.rglob("*"))
    except OSError as exc:
        manifest_problems.append(f"无法枚举交接包：{exc}")
        paths = []
    for path in paths:
        try:
            relative_path = path.relative_to(root)
            relative = relative_path.as_posix()
            if any(part in ignored for part in relative_path.parts):
                continue
            if relative in excluded or relative in registered:
                continue
            if not (path.is_file() or path.is_symlink()):
                continue
            if protected_extra(relative):
                unexpected.append(relative)
        except OSError as exc:
            manifest_problems.append(f"无法检查 {path}：{exc}")

    evidence = {
        "missing": missing,
        "mismatched": mismatched,
        "unexpected": unexpected,
        "manifest_problems": manifest_problems,
        "registered_files": len(manifest["files"]),
    }
    passed = not any((missing, mismatched, unexpected, manifest_problems))
    return {"passed": passed, "applicable": True, "evidence": evidence}


def check_task_doc(task_path, manifest):
    """精确比较任务书原始字节摘要。"""
    expected = manifest["task_doc"].get("sha256")
    actual = _taskdoc.sha256(task_path)
    return {
        "passed": bool(expected) and actual == expected,
        "applicable": True,
        "evidence": {
            "path": str(task_path),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "missing": [] if expected else ["bundle.task_doc.sha256"],
        },
    }


def check_atk_version(env, manifest):
    """精确比较生成侧与验收机的 ATK 版本。"""
    fingerprint = env.get("fingerprint")
    actual = fingerprint.get("atk") if isinstance(fingerprint, dict) else None
    expected = manifest["atk"].get("version")
    missing = []
    if not expected:
        missing.append("bundle.atk.version")
    if not actual:
        missing.append("env.fingerprint.atk")
    return {
        "passed": not missing and actual == expected,
        "applicable": True,
        "evidence": {
            "expected": expected,
            "actual": actual,
            "missing": missing,
        },
    }


def signature_parameters(parsed, label):
    """从解析结果取有序业务参数及包含 const 与指针层数的 C 类型。"""
    rows = parsed.get("parameters") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{label} 缺 parameters 列表")
    result = []
    names = set()
    for index, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or not row.get("c_name")
            or not row.get("c_type")
            or not isinstance(row.get("pointer_depth"), int)
            or not isinstance(row.get("is_const"), bool)
        ):
            raise ValueError(f"{label} 的 parameters[{index}] 缺完整参数类型")
        name = row["c_name"]
        if name in names:
            raise ValueError(f"{label} 的 parameters 有重复参数 {name}")
        names.add(name)
        qualifiers = "const " if row["is_const"] else ""
        pointers = " " + "*" * row["pointer_depth"] if row["pointer_depth"] else ""
        result.append((name, f"{qualifiers}{row['c_type']}{pointers}"))
    return result


def signature_differences(expected_parsed, actual_parsed):
    """按名称集合、共有项相对顺序与 C 类型生成四类差异。"""
    expected = signature_parameters(expected_parsed, "任务书签名")
    actual = signature_parameters(actual_parsed, "PR 签名")
    expected_names = [name for name, _ in expected]
    actual_names = [name for name, _ in actual]
    expected_set = set(expected_names)
    actual_set = set(actual_names)
    common = expected_set & actual_set
    expected_common = [name for name in expected_names if name in common]
    actual_common = [name for name in actual_names if name in common]
    expected_types = dict(expected)
    actual_types = dict(actual)

    reordered = []
    if expected_common != actual_common:
        reordered.append({"expected": expected_common, "actual": actual_common})
    type_mismatch = [
        {
            "name": name,
            "expected": expected_types[name],
            "actual": actual_types[name],
        }
        for name in expected_common
        if expected_types[name] != actual_types[name]
    ]
    return {
        "missing": [name for name in expected_names if name not in actual_set],
        "extra": [name for name in actual_names if name not in expected_set],
        "reordered": reordered,
        "type_mismatch": type_mismatch,
    }


def not_applicable_interface(reason, note=None):
    """构造仍保留证据键的不适用接口结论。"""
    evidence = {"reason": reason}
    if note:
        evidence["note"] = note
    return {
        "passed": None,
        "applicable": False,
        "evidence": evidence,
    }


def check_interface(root, env_path, manifest, header, aclnn_name):
    """在适用时进程内解析 PR 头文件，并与任务书声明比较。"""
    mode = manifest["interface"].get("interface_mode")
    if mode != "aclnn":
        return not_applicable_interface(f"接口模式是 {mode!r}，没有 aclnn C 头文件可比")

    baseline = manifest["interface"].get("baseline_api")
    supplied = {
        "--header": header,
        "--aclnn-name": aclnn_name,
    }
    missing_args = [name for name, value in supplied.items() if not value]
    if missing_args:
        return not_applicable_interface(
            "未给头文件参数：缺 " + "、".join(missing_args),
        )

    try:
        reject_installed_header(header, env_path)
        checked_header = require_project_source(header, env_path, "--header")
        actual_signature, source = read_header_signature(
            checked_header, aclnn_name
        )
        actual_parsed = parse_signature(actual_signature)
        output = root / "evidence" / "pr_signature.json"
        write_json_atomic(
            output,
            {
                "source": {"kind": "header", "path": source},
                "signature": actual_signature,
                "parameters": actual_parsed["parameters"],
            },
        )
        expected_report = read_json_object(
            root / "evidence" / "signature_alignment.json",
            "evidence/signature_alignment.json",
        )
        expected_signature = expected_report.get("signature")
        if not isinstance(expected_signature, str) or not expected_signature.strip():
            raise ValueError("evidence/signature_alignment.json 缺 signature 声明原文")
        expected_parsed = parse_signature(expected_signature)
        differences = signature_differences(expected_parsed, actual_parsed)
    except (AlignError, IntakeFailure, ValueError) as exc:
        return {
            "passed": False,
            "applicable": True,
            "evidence": {
                "baseline_api": baseline,
                "reason": str(exc),
                "missing": [],
                "extra": [],
                "reordered": [],
                "type_mismatch": [],
            },
        }
    evidence = {
        "baseline_api": baseline,
        **differences,
        "expected_report": "evidence/signature_alignment.json",
        "actual_report": "evidence/pr_signature.json",
    }
    return {
        "passed": not any(differences.values()),
        "applicable": True,
        "evidence": evidence,
    }


def write_json_atomic(path, payload):
    """先写同目录临时文件，再原子替换接收结果。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".bundle-intake.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise IntakeFailure(f"写不出 {path}：{exc}") from exc


def announce_at_root(root):
    """-C 指向别处时，仍把 S0 作战卡记到交接包副本。"""
    if not root.is_dir():
        return
    previous = Path.cwd()
    try:
        os.chdir(root)
        _stage_card.announce(__file__)
    finally:
        os.chdir(previous)


def print_results(report):
    """逐项打印结论；接口差异保留四类机器字段名。"""
    labels = {
        "integrity": "完整性",
        "task_doc": "任务书",
        "atk_version": "ATK 版本",
        "interface": "接口一致性",
    }
    for key, label in labels.items():
        item = report[key]
        if not item["applicable"]:
            print(f"{label}：不适用（{item['evidence']['reason']}）")
            if key == "interface" and "未给头文件" in item["evidence"]["reason"]:
                print("S0 的接口一致性门没有执行；进 S3 前必须补跑。")
            continue
        print(f"{label}：{'通过' if item['passed'] else '失败'}")

    interface = report["interface"]
    evidence = interface["evidence"]
    difference_keys = ("missing", "extra", "reordered", "type_mismatch")
    has_differences = interface["applicable"] and any(
        evidence.get(key) for key in difference_keys
    )
    if has_differences:
        print("PR 接口与任务书 §2.3 不一致")
        for key in difference_keys:
            rendered = json.dumps(evidence.get(key, []), ensure_ascii=False)
            print(f"  {key}: {rendered}")
    elif interface["applicable"] and not interface["passed"] and evidence.get("reason"):
        print(f"  原因：{evidence['reason']}")


def build_report(root, manifest_path, manifest, task_path, env_path, env, args):
    """执行四项独立检查并构造接收结果。"""
    checks = {
        "integrity": check_integrity(root, manifest),
        "task_doc": check_task_doc(task_path, manifest),
        "atk_version": check_atk_version(env, manifest),
        "interface": check_interface(
            root,
            env_path,
            manifest,
            args.header,
            args.aclnn_name,
        ),
    }
    blocked = any(
        item["applicable"] and not item["passed"] for item in checks.values()
    )
    return {
        "verdict": "blocked" if blocked else "pass",
        "bundle_sha256": file_sha256(manifest_path),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        **checks,
        "evidence": {"rewires": manifest.get("rewires", [])},
    }


def main(argv=None):
    """命令行入口。"""
    args = parser().parse_args(argv)
    root = Path(args.dir).expanduser().resolve()
    announce_at_root(root)
    try:
        if not root.is_dir():
            raise IntakeFailure(f"交接包副本目录不存在：{root}")
        manifest_path, manifest = require_manifest(root)
        task_path = resolve_bundle_path(root, args.task_doc)
        env_path = resolve_bundle_path(root, args.env)
        try:
            task_path.open("rb").close()
        except OSError as exc:
            raise IntakeFailure(f"读不出 --task-doc {task_path}：{exc}") from exc
        env = read_json_object(env_path, "--env")
        report = build_report(
            root, manifest_path, manifest, task_path, env_path, env, args
        )
        output = Path(args.output).expanduser()
        if not output.is_absolute():
            output = root / output
        write_json_atomic(output, report)
    except (IntakeFailure, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print_results(report)
    return 0 if report["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
