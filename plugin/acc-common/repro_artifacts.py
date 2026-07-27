#!/usr/bin/env python3
"""生成验收后人工复现制品；不参与或改写验收裁决。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile


_CASE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class ReproArtifactError(RuntimeError):
    pass


def _json_compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _write(path, text, executable=False):
    with open(path, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
    if executable:
        os.chmod(path, 0o755)


def _case_script(case_id):
    return f"""#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
report_root="$(cd "$script_dir/../.." && pwd)"
if [[ -n "${{OPRUNWAY_REPRO_ENV_FILE:-}}" ]]; then
  # 环境文件由人工显式指定；生成器不猜私有机器路径。
  if [[ ! -f "$OPRUNWAY_REPRO_ENV_FILE" ]]; then
    echo "OPRUNWAY_REPRO_ENV_FILE 不存在: $OPRUNWAY_REPRO_ENV_FILE" >&2
    exit 2
  fi
  source "$OPRUNWAY_REPRO_ENV_FILE"
  if [[ -n "${{OPRUNWAY_SETENV:-}}" ]]; then
    if [[ ! -f "$OPRUNWAY_SETENV" ]]; then
      echo "OPRUNWAY_SETENV 不存在: $OPRUNWAY_SETENV" >&2
      exit 2
    fi
    source "$OPRUNWAY_SETENV"
  fi
fi
plugin_root="${{OPRUNWAY_PLUGIN_ROOT:-${{CLAUDE_PLUGIN_ROOT:-}}}}"
if [[ -z "$plugin_root" ]]; then
  # 报告仍位于 OpRunway 工作树中时自动向上寻找插件根，审核员无需先 export。
  probe="$report_root"
  while [[ "$probe" != "/" ]]; do
    if [[ -f "$probe/plugin/acc-common/cpp_extension_repro.py" ]]; then
      plugin_root="$probe/plugin"
      break
    fi
    probe="$(dirname "$probe")"
  done
fi
if [[ -z "$plugin_root" ]]; then
  echo "无法自动定位 plugin；报告若已移出 OpRunway 仓，请设置 OPRUNWAY_PLUGIN_ROOT（或 CLAUDE_PLUGIN_ROOT）。" >&2
  exit 2
fi
if [[ ! -f "$plugin_root/acc-common/cpp_extension_repro.py" ]]; then
  echo "插件根无效，缺 $plugin_root/acc-common/cpp_extension_repro.py" >&2
  exit 2
fi
if [[ "${{1:-}}" == "--describe" ]]; then
  exec python3 "$plugin_root/acc-common/repro_case_inspect.py" \\
    --report-root "$report_root" --case-id {case_id}
fi
exec python3 "$plugin_root/acc-common/cpp_extension_repro.py" \\
  --report-root "$report_root" --case-id {case_id} "$@"
"""


def _run_case_script():
    return """#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "用法: $0 <case_id> [cpp_extension_repro.py 追加参数]" >&2
  exit 2
fi
case_id="$1"
shift
if [[ ! "$case_id" =~ ^[A-Za-z0-9_.-]+$ || "$case_id" == -* ]]; then
  echo "非法 case_id: $case_id" >&2
  exit 2
fi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case_script="$script_dir/cases/$case_id.sh"
if [[ ! -x "$case_script" ]]; then
  echo "case 不存在或脚本不可执行: $case_id" >&2
  exit 2
fi
exec "$case_script" "$@"
"""


def _show_case_script():
    return """#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "用法: $0 <case_id>" >&2
  exit 2
fi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$script_dir/run_case.sh" "$1" --describe
"""


def _review_script():
    return """#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
failed="$script_dir/failed.tsv"

usage() {
  cat <<'EOF'
审核员快捷入口：
  ./review.sh list              列出失败 case（带编号）
  ./review.sh show <编号|case>  查看输入、golden、policy 和原 metrics（不跑 NPU）
  ./review.sh run  <编号|case>  重放一个 case，并按原验收结果解释是否稳定复现
  ./review.sh all               查看全部 case 索引
EOF
}

resolve_case() {
  local token="$1"
  if [[ "$token" =~ ^[0-9]+$ ]]; then
    awk -F '\t' -v n="$token" 'NR>1 && $1==n {print $2; exit}' "$failed"
  else
    printf '%s\n' "$token"
  fi
}

cmd="${1:-list}"
case "$cmd" in
  list)
    column -t -s $'\t' "$failed" 2>/dev/null || cat "$failed"
    usage
    ;;
  all)
    column -t -s $'\t' "$script_dir/index.tsv" 2>/dev/null || cat "$script_dir/index.tsv"
    ;;
  show)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    case_id="$(resolve_case "$2")"
    [[ -n "$case_id" ]] || { echo "找不到失败编号: $2" >&2; exit 2; }
    exec "$script_dir/show_case.sh" "$case_id"
    ;;
  run)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    case_id="$(resolve_case "$2")"
    [[ -n "$case_id" ]] || { echo "找不到失败编号: $2" >&2; exit 2; }
    original="$(awk -F '\t' -v id="$case_id" 'NR>1 && $1==id {print $5; exit}' "$script_dir/index.tsv")"
    set +e
    "$script_dir/run_case.sh" "$case_id"
    rc=$?
    set -e
    if [[ "$original" == "fail" && $rc -eq 1 ]]; then
      echo "复核结果：原 FAIL 已稳定复现（底层重放退出码 1）。"
      exit 0
    fi
    if [[ "$original" == "pass" && $rc -eq 0 ]]; then
      echo "复核结果：原 PASS 已稳定复现。"
      exit 0
    fi
    if [[ $rc -ne 0 && $rc -ne 1 ]]; then
      echo "复核未执行完成：启动或环境错误（replay_rc=$rc）；未与原验收结果比较，请先处理上方错误。" >&2
      echo "若目标环境需初始化，请执行：OPRUNWAY_REPRO_ENV_FILE=<runtime-env绝对路径> ./repro/review.sh run $2" >&2
      exit "$rc"
    fi
    echo "复核结果与原验收不一致：original=$original replay_rc=$rc，请人工调查。" >&2
    exit 1
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
"""


def generate_cpp_extension(report_root, caseset, verdict):
    """为完整 caseset 生成逐 case 启动脚本和可审计索引。"""
    report_root = os.path.realpath(report_root)
    if not os.path.isdir(os.path.join(report_root, "work", "cpp_extension")):
        raise ReproArtifactError("报告缺 work/cpp_extension，无法生成重放入口")
    verdict_by_id = {
        row.get("case_id"): row for row in (verdict.get("per_case") or [])
        if isinstance(row, dict)
    }
    cases = caseset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ReproArtifactError("caseset.cases 缺失或为空")

    parent = report_root
    stage = tempfile.mkdtemp(prefix=".repro-stage-", dir=parent)
    target = os.path.join(report_root, "repro")
    try:
        case_dir = os.path.join(stage, "cases")
        os.makedirs(case_dir)
        rows, failed_rows, manifest_cases = [], [], []
        for case in cases:
            case_id = case.get("id")
            if (not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id)
                    or case_id.startswith("-") or case_id in (".", "..")):
                raise ReproArtifactError(f"非法 case_id={case_id!r}")
            inputs = case.get("inputs") or []
            dtype = inputs[0].get("dtype") if inputs else None
            shapes = [item.get("shape") for item in inputs]
            precision = (verdict_by_id.get(case_id) or {}).get("精度", "not_judged")
            rel = f"cases/{case_id}.sh"
            _write(os.path.join(stage, rel), _case_script(case_id), executable=True)
            item = {
                "case_id": case_id, "dtype": dtype, "input_shapes": shapes,
                "attrs": case.get("attrs") or {}, "dims": case.get("dims") or [],
                "precision_result": precision, "script": rel,
            }
            manifest_cases.append(item)
            rows.append("\t".join([
                case_id, str(dtype or ""), _json_compact(shapes),
                _json_compact(item["attrs"]), precision, rel,
            ]))
            if precision != "pass":
                failed_rows.append("\t".join([
                    str(len(failed_rows) + 1), case_id, str(dtype or ""),
                    _json_compact(shapes), precision,
                ]))

        manifest = {
            "schema": "oprunway.repro_manifest",
            "schema_version": 1,
            "backend": "cpp_extension",
            "case_count": len(manifest_cases),
            "cases": manifest_cases,
            "acceptance_verdict": None,
            "note": "人工复现启动入口；不生成或改写验收裁决",
        }
        _write(os.path.join(stage, "manifest.json"),
               json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        _write(os.path.join(stage, "index.tsv"),
               "case_id\tdtype\tinput_shapes\tattrs\tprecision_result\tscript\n"
               + "\n".join(rows) + "\n")
        _write(os.path.join(stage, "failed.tsv"),
               "no\tcase_id\tdtype\tinput_shapes\tprecision_result\n"
               + "\n".join(failed_rows) + "\n")
        _write(os.path.join(stage, "run_case.sh"), _run_case_script(), executable=True)
        _write(os.path.join(stage, "show_case.sh"), _show_case_script(), executable=True)
        _write(os.path.join(stage, "review.sh"), _review_script(), executable=True)
        _write(os.path.join(stage, "README.md"), """# 人工复现入口

本目录由验收 workflow 生成，不参与验收裁决。

- `index.tsv`：全部 case、dtype、shape、属性、原精度结果与脚本路径；
- `failed.tsv`：带短编号的失败 case 清单；
- `review.sh list/show/run`：审核员快捷入口，负责解释重放结果；
- `run_case.sh <case_id>`：统一入口；
- `show_case.sh <case_id>`：展示 case 定义、输入/golden 摘要、调用槽、policy 与原结果；
- `cases/<case_id>.sh`：逐 case 可执行入口。

报告位于 OpRunway 工作树内时会自动向上定位 `plugin/`，无需预先设置根变量。
报告移出工作树后须设置 `OPRUNWAY_PLUGIN_ROOT`（或 `CLAUDE_PLUGIN_ROOT`）。
如需加载目标机环境，可显式设置 `OPRUNWAY_REPRO_ENV_FILE`；脚本会继续加载其中声明的
`OPRUNWAY_SETENV`。生成器不会把私有机器路径写入制品。

```bash
OPRUNWAY_REPRO_ENV_FILE=<runtime-env绝对路径> ./repro/review.sh run 1
```
复现命令返回 1 表示该 case 的失败得到复现，返回 0 表示本次未复现失败。
每个逐 case 脚本也支持 `--describe`，只读展示、不执行 NPU。
""")
        if os.path.lexists(target):
            if os.path.islink(target):
                raise ReproArtifactError("既有 repro 不得为软链")
            shutil.rmtree(target)
        os.replace(stage, target)
        stage = None
        return manifest
    finally:
        if stage and os.path.isdir(stage):
            shutil.rmtree(stage)
