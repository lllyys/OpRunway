"""接线字段的受控改写：用例语义未变，只换执行器。

「一次 atk case 生成后冻结」是硬纪律，但运行期类型校验这类问题确实可能
只在 S3 才暴露。规则冲突没有出口时 agent 会自己发明一条——真机实测里
它手工自证了十次「用例语义没变」。

本脚本把那十次变成一条命令，并且把判据钉死：
patch YAML 接线字段 → 重跑 atk case → 逐条比对新旧用例 →
除接线键外必须逐字段相同 → 重新绑定冻结输入 → 写留痕。
若工作目录已有封印清单，核对通过后还会同步被改文件摘要与接线改写记录。

不满足即退出码 2，视为需要回 S2 重做，没有第二条通道。

注意一种真实的失败：ATK 的用例生成不是处处确定的（复合组长度是随机抽的，
见 L0 group_length）。这种算子重跑会得到不同的用例，本脚本会如实报告
差异并拒绝——那不是脚本过严，是「只换接线」这件事在该算子上不成立。

退出码：0 只有接线变了；2 用例语义变了或核对不过；3 输入/命令本身出错。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from _case_utils import file_sha256, iter_cases, load_json

# 只有这两个键是「接线」：换执行器不改变用例要测什么。
# generate 不在其中——换生成器就是换用例本身，那是重新设计，不是改接线。
WIRING_KEYS = frozenset({"api_type", "aclnn_api_type"})

SAVED_CASE = re.compile(r"save case json file:\s*(\S+)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class BundleUpdateError(ValueError):
    """封印清单无法可靠读取、更新或写回。"""


def parse_set(items):
    """把 `--set k=v` 解析成字典，非接线键当场拒绝。"""
    patch = {}
    for item in items or []:
        key, sep, value = str(item).partition("=")
        if not sep or not value:
            raise ValueError(f"--set 要写成 k=v，收到 {item!r}")
        if key not in WIRING_KEYS:
            raise ValueError(
                f"{key!r} 不是接线字段。只有 {sorted(WIRING_KEYS)} 可以受控改写；"
                "改别的字段等于改用例本身，回 S2 重做")
        patch[key] = value
    if not patch:
        raise ValueError("没有给出任何 --set")
    return patch


def strip_wiring(case):
    """去掉接线键的用例副本，比对用。"""
    return {key: value for key, value in case.items() if key not in WIRING_KEYS}


def diff_cases(old, new):
    """返回非接线差异的人话描述，空列表表示只有接线变了。"""
    if len(old) != len(new):
        return [f"用例条数从 {len(old)} 变成 {len(new)}；"
                "这不是接线改写，用例集本身变了"]
    old_ids = [case.get("id") for case in old]
    new_ids = [case.get("id") for case in new]
    if old_ids != new_ids:
        return [f"用例号变了：{old_ids[:8]} → {new_ids[:8]}"]
    problems = []
    for before, after in zip(old, new):
        if strip_wiring(before) != strip_wiring(after):
            problems.append(
                f"用例 {before.get('id')} 除接线字段外还有变化；"
                "用例语义已变，冻结纪律要求回 S2 重做")
    return problems


def patch_yaml(path, patch):
    """就地改 YAML 的接线字段，原文件备份成 <path>.pre_rewire。"""
    import yaml

    with open(path, encoding="utf-8") as handle:
        design = yaml.safe_load(handle)
    before = {key: design.get(key) for key in patch}
    design.update(patch)
    shutil.copyfile(path, f"{path}.pre_rewire")
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(design, handle, allow_unicode=True, sort_keys=False,
                       width=100)
    return before


def _bundle_relative(root, path, label):
    """把路径约束到交接包根内并转成正斜杠相对路径。"""
    root = Path(root).resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise BundleUpdateError(f"{label} 越出交接包根：{candidate}") from exc


def update_bundle(bundle_path, root, touched, record):
    """重算改写文件摘要，追加接线记录，再原子写回封印清单。"""
    bundle_path = Path(bundle_path)
    root = Path(root).resolve()
    try:
        manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleUpdateError(f"读不出封印清单 {bundle_path}：{exc}") from exc
    if not isinstance(manifest, dict):
        raise BundleUpdateError("封印清单必须是 JSON 对象")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise BundleUpdateError("封印清单缺 files 对象")
    rewires = manifest.get("rewires")
    if rewires is None:
        rewires = []
        manifest["rewires"] = rewires
    elif not isinstance(rewires, list):
        raise BundleUpdateError("封印清单的 rewires 必须是列表")

    paths = {}
    for item in touched:
        path = Path(item)
        if path.is_symlink() or not path.is_file():
            raise BundleUpdateError(f"改写文件不是普通文件：{path}")
        relative = _bundle_relative(root, path, "改写文件")
        paths[relative] = path

    changes = []
    for relative in sorted(paths):
        try:
            after = file_sha256(paths[relative])
        except OSError as exc:
            raise BundleUpdateError(
                f"无法计算 {relative} 的 SHA256：{exc}"
            ) from exc
        before = files.get(relative)
        if before == after:
            continue
        files[relative] = after
        changes.append({"file": relative, "before": before, "after": after})

    patched = record.get("patched")
    if not isinstance(patched, dict) or not patched:
        raise BundleUpdateError("接线留痕缺 patched 对象")
    try:
        yaml_path = record["yaml"]
        record_path = record["record"]
        backup_path = record["backup"]
    except KeyError as exc:
        raise BundleUpdateError(f"接线留痕缺字段 {exc.args[0]}") from exc
    at = record.get("at") or datetime.now(timezone.utc).isoformat()
    try:
        parsed_at = datetime.fromisoformat(at)
    except (TypeError, ValueError) as exc:
        raise BundleUpdateError("接线留痕 at 不是 ISO 8601 时间") from exc
    if parsed_at.utcoffset() is None:
        raise BundleUpdateError("接线留痕 at 必须带时区")

    entry = {
        "at": at,
        "yaml": _bundle_relative(root, yaml_path, "YAML"),
        "fields": list(patched),
        "record": _bundle_relative(root, record_path, "留痕"),
        "backup": _bundle_relative(root, backup_path, "备份"),
        "changes": changes,
    }
    rewires.append(entry)

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=bundle_path.parent,
            prefix=".bundle-rewire.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, bundle_path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise BundleUpdateError(f"写不回封印清单 {bundle_path}：{exc}") from exc
    return entry


def collect_touched(root, yaml_path, frozen_path=None):
    """列出接线改写会改变的全部已封印普通文件。"""
    root = Path(root).resolve()
    yaml_path = Path(yaml_path).expanduser().resolve()
    touched = [yaml_path]
    result_dir = root / "result" / yaml_path.stem
    if result_dir.is_dir():
        for path in sorted(result_dir.rglob("*")):
            relative = path.relative_to(result_dir)
            if "__pycache__" in relative.parts:
                continue
            if path.is_file() and not path.is_symlink():
                touched.append(path)
    if frozen_path:
        touched.append(Path(frozen_path).expanduser().resolve())
    return touched


def regenerate(atk_cli, yaml_path, plugin, timeout):
    """重跑 atk case，返回新用例 JSON 的路径。"""
    command = [atk_cli, "case", "-f", os.path.abspath(yaml_path)]
    if plugin:
        command += ["-p", os.path.abspath(plugin)]
    print("重新生成：" + " ".join(command))
    done = subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout)
    log = ANSI.sub("", done.stdout + done.stderr)
    found = SAVED_CASE.findall(log)
    if not found:
        raise RuntimeError(
            f"日志里没有 save case json file:（退出码 {done.returncode}）；"
            "产物路径只能从日志取，不要自己拼")
    return found[-1], log


def main():
    parser = argparse.ArgumentParser(
        description="接线字段的受控改写：用例语义未变才放行")
    parser.add_argument("-f", "--yaml", required=True, help="本分面的设计 YAML")
    parser.add_argument("-j", "--case-json", required=True,
                        help="已冻结的用例 JSON，比对基准")
    parser.add_argument("--atk-cli", required=True,
                        help="probe_env.py 探出的绝对路径")
    parser.add_argument("--set", action="append", dest="sets",
                        help=f"接线字段改写，k=v，只接受 {sorted(WIRING_KEYS)}")
    parser.add_argument("-p", "--plugin", help="生成器插件路径，与首次生成保持一致")
    parser.add_argument("--frozen",
                        help="evidence/frozen_inputs.json；给了就重新绑定到新用例集")
    parser.add_argument(
        "--bundle", default="evidence/bundle.json",
        help=("封印清单路径，默认 evidence/bundle.json；存在时同步摘要与改写记录，"
              "不存在时按未封印流程处理"),
    )
    parser.add_argument("-o", "--output", required=True,
                        help="留痕落盘路径，如 evidence/rewire.json")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    try:
        patch = parse_set(args.sets)
    except ValueError as exc:
        print(f"参数不合法：{exc}", file=sys.stderr)
        return 3
    if not os.path.exists(args.yaml) or not os.path.exists(args.case_json):
        print(f"找不到 {args.yaml} 或 {args.case_json}", file=sys.stderr)
        return 3

    bundle_path = Path(args.bundle).expanduser()
    if not bundle_path.is_absolute():
        bundle_path = Path.cwd() / bundle_path
    bundle_path = bundle_path.resolve()
    bundle_exists = bundle_path.exists()
    if not bundle_exists:
        print(f"未找到封印清单，按未封印流程处理：{bundle_path}")

    old_cases = list(iter_cases(load_json(args.case_json)))
    old_sha = file_sha256(args.case_json)

    try:
        before = patch_yaml(args.yaml, patch)
        new_path, _ = regenerate(args.atk_cli, args.yaml, args.plugin,
                                 args.timeout)
    except Exception as exc:  # noqa: BLE001 命令与解析失败都归 3
        print(f"重新生成失败：{exc}", file=sys.stderr)
        print(f"  → YAML 原文已备份在 {args.yaml}.pre_rewire", file=sys.stderr)
        return 3

    new_cases = list(iter_cases(load_json(new_path)))
    problems = diff_cases(old_cases, new_cases)

    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "yaml": os.path.abspath(args.yaml),
        "patched": {key: {"from": before.get(key), "to": value}
                    for key, value in patch.items()},
        "record": os.path.abspath(args.output),
        "backup": os.path.abspath(f"{args.yaml}.pre_rewire"),
        "old_case_json": os.path.abspath(args.case_json),
        "old_case_json_sha256": old_sha,
        "new_case_json": os.path.abspath(new_path),
        "new_case_json_sha256": file_sha256(new_path),
        "total_cases": len(new_cases),
        "problems": problems,
    }

    if not problems and args.frozen:
        # 冻结摘要用 case_json_sha256 绑死用例集。用例语义未变时把绑定
        # 挪到新文件上，并留下旧摘要——冻结的实质是「输入没重算」，
        # 而输入本来就还是那一份。
        frozen = load_json(args.frozen)
        frozen["previous_case_json_sha256"] = frozen.get("case_json_sha256")
        frozen["case_json"] = os.path.abspath(new_path)
        frozen["case_json_sha256"] = record["new_case_json_sha256"]
        frozen["rewired_at"] = record["at"]
        with open(args.frozen, "w", encoding="utf-8") as sink:
            json.dump(frozen, sink, ensure_ascii=False, indent=2)
        record["frozen_rebound"] = os.path.abspath(args.frozen)

    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(record, sink, ensure_ascii=False, indent=2)

    if not problems and bundle_exists:
        root = bundle_path.parent.parent.resolve()
        try:
            touched = collect_touched(root, args.yaml, args.frozen)
            update_bundle(bundle_path, root, touched, record)
        except BundleUpdateError as exc:
            print(f"封印清单同步失败：{exc}", file=sys.stderr)
            print(f"  → YAML 原文已备份在 {args.yaml}.pre_rewire", file=sys.stderr)
            print("  → 文件已改写但清单未同步，交接包此刻不再满足封印一致性；"
                  "先恢复或修复清单，不能继续验收。", file=sys.stderr)
            return 3

    for key, change in record["patched"].items():
        print(f"{key}：{change['from']} → {change['to']}")
    print(f"新用例集：{new_path}（{len(new_cases)} 条）")
    print(f"留痕写入 {args.output}")

    if not problems:
        print("核对通过：除接线字段外，逐条用例逐字段相同。")
        return 0

    print(f"\n✗ {len(problems)} 处非接线差异：", file=sys.stderr)
    for index, message in enumerate(problems, 1):
        print(f"  {index}. {message}", file=sys.stderr)
    print("  → 用例语义变了就不属于接线改写，回 S2 重做整轮，"
          "不要拿这份新用例集继续跑。", file=sys.stderr)
    print(f"  → YAML 原文备份在 {args.yaml}.pre_rewire。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
