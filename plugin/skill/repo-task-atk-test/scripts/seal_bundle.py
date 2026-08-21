"""S2 出口：核对生成侧产物并封印交接包。

量具先复用进度探测器核对 S1、S2 骨架，再按用例集的
SHA256 将覆盖、冻结、校验和适配器报告配到每个 YAML 分面。
只有全部出口门通过时才写入 ``evidence/bundle.json``。

退出码：0 封印成功；2 产物不齐、配对冲突或门禁未过；
3 工作目录、核心 JSON 或写入结构不可用。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from _case_utils import file_sha256
from _contracts import ContractError, artifacts_of, load
import _stage_card
from probe_progress import survey


SCHEMA_VERSION = 1
EXCLUDED = (
    "evidence/timeline.jsonl",
    "evidence/repro.sh",
    "evidence/bundle.json",
    "evidence/env.json",
    "evidence/env.sh",
)
IGNORED_DIRS = ("__pycache__",)
REPORT_LABELS = {
    "coverage": "覆盖",
    "freeze": "冻结",
    "validate": "用例校验",
    "adapter": "适配器",
}
INTERFACE_FIELDS = (
    "interface_mode",
    "candidate_symbol",
    "baseline_api",
    "baseline_kind",
)


class SealFailure(RuntimeError):
    """封印失败，带约定的退出码。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parser():
    """构造命令行帮助。"""
    return argparse.ArgumentParser(
        prog="seal_bundle.py",
        description=(
            "核对 S1、S2 产物与出口门，写出 evidence/bundle.json。"
            "分面报告按 case_file_sha256 或 case_json_sha256 "
            "与各 YAML 的用例 JSON 配对。"
        ),
        epilog=(
            "退出码：0 封印成功；2 产物不齐、配对冲突或门禁未过；"
            "3 目录结构或核心 JSON 不可用。"
        ),
    )


def relative(root, path):
    """转成以交接包为根、使用正斜杠的相对路径。"""
    return Path(path).relative_to(root).as_posix()


def read_json_object(path, label, code):
    """读 JSON 对象，失败时使用调用方指定的退出码。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SealFailure(code, f"读不出 {label}：{exc}") from exc
    if not isinstance(payload, dict):
        raise SealFailure(code, f"{label} 必须是 JSON 对象")
    return payload


def condition_source(spec):
    """给条件产物找到决定它的已登记证据。"""
    condition = spec.get("condition") or ""
    if "baseline_adapter.required" in condition:
        return "evidence/signature_alignment.json"
    if "cann_builtin" in condition:
        return "evidence/interface.json"
    return "条件判据文件"


def check_skeleton(root):
    """复用 probe_progress.survey 核对 S1、S2，但排除清单自身。"""
    try:
        data = load()
    except ContractError as exc:
        raise SealFailure(3, f"骨架不可用：{exc}") from exc

    table = survey(root, data)
    problems = []
    for stage in ("S1", "S2"):
        _, missing, undecided, _ = table[stage]
        for name in missing:
            if name == "evidence/bundle.json":
                continue
            problems.append(f"{stage} 缺少产物：{name}")
        specs = artifacts_of(data, stage)
        for name in undecided:
            source = condition_source(specs[name])
            problems.append(f"{stage} 条件未定：{name}；判据 {source} 还没落盘或读不出")
    if problems:
        body = "\n".join(f"  - {item}" for item in problems)
        raise SealFailure(2, f"封印前产物不齐：\n{body}")


def require_metadata(interface, env):
    """取封印清单的唯一字段来源。"""
    task_doc = interface.get("task_doc")
    if not isinstance(task_doc, dict) or not task_doc.get("name") or not task_doc.get("sha256"):
        raise SealFailure(
            2,
            "evidence/interface.json 缺 task_doc.name 或 task_doc.sha256；"
            "请带 --task-doc 重跑 derive_interface.py",
        )

    missing_interface = [key for key in INTERFACE_FIELDS if interface.get(key) is None]
    if missing_interface:
        raise SealFailure(
            2,
            "evidence/interface.json 缺字段 "
            + "、".join(missing_interface)
            + "；请重跑 derive_interface.py",
        )

    fingerprint = env.get("fingerprint")
    missing_env = []
    if not isinstance(fingerprint, dict) or fingerprint.get("atk") is None:
        missing_env.append("fingerprint.atk")
    for key in ("selected_python", "phase_supported"):
        if env.get(key) is None:
            missing_env.append(key)
    if missing_env:
        raise SealFailure(
            2,
            "evidence/env.json 缺字段 " + "、".join(missing_env) + "；请重跑 probe_env.py",
        )
    return task_doc, fingerprint


def discover_facets(root):
    """根据根目录 YAML 找到每个分面的固定用例路径。"""
    facets = []
    problems = []
    for yaml_path in sorted(root.glob("*.yaml")):
        if not yaml_path.is_file():
            continue
        name = yaml_path.stem
        case_path = root / "result" / name / "json" / f"all_{name}.json"
        if not case_path.is_file():
            problems.append(f"分面 {name} 缺用例 JSON：{relative(root, case_path)}")
            continue
        try:
            case_digest = file_sha256(case_path)
        except OSError as exc:
            problems.append(f"分面 {name} 的用例 JSON 读不出：{exc}")
            continue
        facets.append(
            {
                "name": name,
                "yaml": yaml_path,
                "case_json": case_path,
                "case_digest": case_digest,
                "reports": {kind: [] for kind in REPORT_LABELS},
            }
        )
    if problems:
        body = "\n".join(f"  - {item}" for item in problems)
        raise SealFailure(2, f"分面发现未通过：\n{body}")
    return facets


def candidate_json_paths(root):
    """按约定遍历 evidence 树与交接包根的 JSON。"""
    paths = set(root.glob("*.json"))
    paths.update((root / "evidence").rglob("*.json"))
    bundle = root / "evidence" / "bundle.json"
    return [path for path in sorted(paths) if path.is_file() and path != bundle]


def load_candidate_json(root):
    """读取可能的报告；非对象 JSON 仍可作为摘要候选。"""
    loaded = []
    problems = []
    for path in candidate_json_paths(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{relative(root, path)} 读不出：{exc}")
            continue
        if isinstance(payload, dict):
            loaded.append((path, payload))
    return loaded, problems


def root_json_digests(root):
    """为根目录 JSON 建摘要到路径的反向索引。"""
    by_digest = {}
    problems = []
    for path in sorted(root.glob("*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            digest = file_sha256(path)
        except OSError as exc:
            problems.append(f"{relative(root, path)} 无法计算 SHA256：{exc}")
            continue
        by_digest.setdefault(digest, []).append(path)
    return by_digest, problems


def report_kinds(payload):
    """按报告内的识别键分类，不依赖文件名。"""
    keys = set(payload)
    kinds = []
    if {"case_file_sha256", "must_cover_sha256"} <= keys:
        kinds.append(("coverage", "case_file_sha256"))
    if {"case_json_sha256", "frozen_dir", "inputs"} <= keys:
        kinds.append(("freeze", "case_json_sha256"))
    if {"case_file_sha256", "failures"} <= keys:
        kinds.append(("validate", "case_file_sha256"))
    if {"case_file_sha256", "problems"} <= keys:
        kinds.append(("adapter", "case_file_sha256"))
    return kinds


def normalize_frozen_dir(root, value):
    """把冻结报告里的绝对或相对目录收敛到交接包内。"""
    if not isinstance(value, str) or not value:
        raise ValueError("frozen_dir 不是非空路径")
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"frozen_dir 不在交接包内：{value}") from exc
    if not resolved.is_dir():
        raise ValueError(f"frozen_dir 不存在：{relative(root, resolved)}")
    return resolved


def pair_reports(root, facets):
    """把所有可识别报告按用例集摘要配到分面。"""
    loaded, problems = load_candidate_json(root)
    must_cover_by_digest, digest_problems = root_json_digests(root)
    problems.extend(digest_problems)

    facets_by_digest = {}
    for facet in facets:
        facets_by_digest.setdefault(facet["case_digest"], []).append(facet)

    for path, payload in loaded:
        for kind, digest_key in report_kinds(payload):
            matches = facets_by_digest.get(payload.get(digest_key), [])
            report_name = relative(root, path)
            if not matches:
                problems.append(
                    f"{REPORT_LABELS[kind]}报告 {report_name} 的 {digest_key} "
                    "未匹配任何分面用例 JSON"
                )
                continue
            if len(matches) > 1:
                names = "、".join(item["name"] for item in matches)
                problems.append(
                    f"{REPORT_LABELS[kind]}报告 {report_name} 的配对键同时命中分面 {names}"
                )
                continue

            item = {"path": path, "payload": payload}
            if kind == "coverage":
                must_paths = must_cover_by_digest.get(payload.get("must_cover_sha256"), [])
                if len(must_paths) != 1:
                    state = "没有" if not must_paths else "多份"
                    shown = "、".join(relative(root, value) for value in must_paths)
                    problems.append(
                        f"覆盖报告 {report_name} 的 must_cover_sha256 {state}匹配文件"
                        + (f"：{shown}" if shown else "")
                    )
                else:
                    item["must_cover"] = must_paths[0]
            elif kind == "freeze":
                try:
                    item["frozen_dir"] = normalize_frozen_dir(
                        root, payload.get("frozen_dir")
                    )
                except ValueError as exc:
                    problems.append(f"冻结报告 {report_name}：{exc}")
            matches[0]["reports"][kind].append(item)

    for facet in facets:
        for kind, label in REPORT_LABELS.items():
            reports = facet["reports"][kind]
            if not reports:
                problems.append(f"分面 {facet['name']} 没有配到{label}报告")
            elif len(reports) > 1:
                names = "、".join(relative(root, item["path"]) for item in reports)
                problems.append(f"分面 {facet['name']} 配到多份{label}报告：{names}")

    if problems:
        body = "\n".join(f"  - {item}" for item in problems)
        raise SealFailure(2, f"报告发现与配对未通过：\n{body}")


def check_report_gates(root, facets):
    """按各报告的真实输出字段核对出口结论。"""
    problems = []
    for facet in facets:
        name = facet["name"]
        coverage = facet["reports"]["coverage"][0]
        coverage_data = coverage["payload"]
        if (
            coverage_data.get("missing") != []
            or coverage_data.get("must_cover_hit")
            != coverage_data.get("must_cover_total")
        ):
            problems.append(
                f"分面 {name} 的覆盖报告 {relative(root, coverage['path'])} 未通过"
            )

        freeze = facet["reports"]["freeze"][0]
        freeze_data = freeze["payload"]
        constant_cases = freeze_data.get("constant_input_cases")
        constant_allowed = (
            constant_cases in ({}, [])
            or freeze_data.get("constant_input_check") == "not_applicable"
        )
        if (
            freeze_data.get("unmaterialized_ids") != []
            or freeze_data.get("baseline_failed_cases") != []
            or not constant_allowed
        ):
            problems.append(
                f"分面 {name} 的冻结报告 {relative(root, freeze['path'])} 未通过"
            )

        validate = facet["reports"]["validate"][0]
        if validate["payload"].get("failures") != []:
            problems.append(
                f"分面 {name} 的用例校验报告 {relative(root, validate['path'])} 未通过"
            )

        adapter = facet["reports"]["adapter"][0]
        if adapter["payload"].get("problems") != []:
            problems.append(
                f"分面 {name} 的适配器报告 {relative(root, adapter['path'])} 未通过"
            )

    signature_path = root / "evidence" / "signature_contract.json"
    try:
        signature = read_json_object(signature_path, relative(root, signature_path), 2)
    except SealFailure as exc:
        problems.append(str(exc))
    else:
        if signature.get("verdict") != "pass" or signature.get("problems") != []:
            problems.append("evidence/signature_contract.json 签名契约结论未通过")

    if problems:
        body = "\n".join(f"  - {item}" for item in problems)
        raise SealFailure(2, f"S2 出口门未通过：\n{body}")


def manifest_facets(root, facets):
    """把已配对的内部记录转为清单的稳定相对路径。"""
    rendered = []
    for facet in sorted(facets, key=lambda item: item["name"]):
        coverage = facet["reports"]["coverage"][0]
        freeze = facet["reports"]["freeze"][0]
        validate = facet["reports"]["validate"][0]
        adapter = facet["reports"]["adapter"][0]
        rendered.append(
            {
                "name": facet["name"],
                "yaml": relative(root, facet["yaml"]),
                "case_json": relative(root, facet["case_json"]),
                "must_cover": relative(root, coverage["must_cover"]),
                "frozen_dir": relative(root, freeze["frozen_dir"]),
                "coverage_report": relative(root, coverage["path"]),
                "freeze_report": relative(root, freeze["path"]),
                "validate_report": relative(root, validate["path"]),
                "adapter_report": relative(root, adapter["path"]),
            }
        )
    return rendered


def hash_files(root):
    """计算排除文件与忽略目录之外的全部普通文件。"""
    files = {}
    for path in sorted(root.rglob("*")):
        try:
            rel_path = path.relative_to(root)
            rel = rel_path.as_posix()
            if any(part in IGNORED_DIRS for part in rel_path.parts):
                continue
            if rel in EXCLUDED or path.is_symlink() or not path.is_file():
                continue
            files[rel] = file_sha256(path)
        except OSError as exc:
            raise SealFailure(3, f"无法计算 {path} 的 SHA256：{exc}") from exc
    return dict(sorted(files.items()))


def write_manifest(path, payload):
    """在同一目录先写临时文件，成功后原子替换旧清单。"""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".bundle.",
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
        raise SealFailure(3, f"写不出 {path}：{exc}") from exc


def build_manifest(root, interface, env, task_doc, fingerprint, facets):
    """在所有门禁通过后构造封印清单。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "operator": interface["candidate_symbol"],
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "task_doc": {"name": task_doc["name"], "sha256": task_doc["sha256"]},
        "generator": {
            "skill": "repo-task-case-gen",
            "phase_supported": env["phase_supported"],
        },
        "atk": {
            "version": fingerprint["atk"],
            "python": env["selected_python"],
        },
        "interface": {key: interface[key] for key in INTERFACE_FIELDS},
        "facets": manifest_facets(root, facets),
        "files": hash_files(root),
        "excluded": list(EXCLUDED),
        "ignored_dirs": list(IGNORED_DIRS),
    }


def announce_at_root(root):
    """-C 指向别处时，仍把作战卡记到该交接包。"""
    if not root.is_dir():
        return
    previous = Path.cwd()
    try:
        os.chdir(root)
        _stage_card.announce(__file__)
    finally:
        os.chdir(previous)


def seal(root):
    """完成检查、配对、门禁和封印。"""
    if not root.is_dir():
        raise SealFailure(3, f"工作目录不存在：{root}")
    evidence = root / "evidence"
    if not evidence.is_dir():
        raise SealFailure(3, f"工作目录缺 evidence/ 目录：{root}")

    check_skeleton(root)
    interface = read_json_object(
        evidence / "interface.json", "evidence/interface.json", 3
    )
    env = read_json_object(evidence / "env.json", "evidence/env.json", 3)
    task_doc, fingerprint = require_metadata(interface, env)
    facets = discover_facets(root)
    pair_reports(root, facets)
    check_report_gates(root, facets)
    payload = build_manifest(root, interface, env, task_doc, fingerprint, facets)
    write_manifest(evidence / "bundle.json", payload)
    return payload


def main(argv=None):
    """命令行入口。"""
    cli = parser()
    cli.add_argument("-C", "--dir", default=".", metavar="<目录>", help="交接包根目录，默认当前目录")
    args = cli.parse_args(argv)
    root = Path(args.dir).expanduser().resolve()
    announce_at_root(root)
    try:
        payload = seal(root)
    except SealFailure as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    print(
        f"已封印 {len(payload['facets'])} 个分面、{len(payload['files'])} 个文件 "
        "→ evidence/bundle.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
