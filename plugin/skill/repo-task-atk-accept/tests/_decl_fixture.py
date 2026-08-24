"""make_must_cover 的 CLI 现在要 dtype 出处，出处要落在待验收算子工程里。

真机上这两样都是 S1 的现成产物（`evidence/env.json` 加工程 README），
测试里没有工程树，就地造一棵最小的：一份 env.json 指向它，一份声明了
数据类型的 README。
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from _paths import SCRIPTS

sys.path.insert(0, str(SCRIPTS))

from _case_utils import file_sha256  # noqa: E402
import _handoff_contract  # noqa: E402

# 与 assets/example/decl.json 的 dtype 轴逐一对应的 C 侧名字。
# 出处与 dims 是双向核对的：这里多一个名字，那边就得多声明一个 dtype。
EXAMPLE_DTYPES = "FLOAT16、BFLOAT16、FLOAT、INT32"


def seal_bundle(work):
    """用真实生成侧量具封印验收侧测试夹具。"""
    script = SCRIPTS / "seal_bundle.py"
    if not script.is_file():
        # 独立验收侧产物不携带生成侧量具。这里仅构造测试输入清单，
        # 不实现或替代 seal_bundle.py 的任何门禁；嵌套源仍走真实量具。
        _write_bundle_fixture(work)
        return subprocess.CompletedProcess(
            [sys.executable, str(script), "-C", str(work)],
            0,
            stdout="已构造验收侧测试清单\n",
            stderr="",
        )
    sealed = subprocess.run(
        [sys.executable, str(script), "-C", str(work)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return sealed


def _write_bundle_fixture(work):
    """为不含生成侧量具的展开产物写最小已封印输入。"""
    work = Path(work)
    evidence = work / "evidence"
    interface = read_json(evidence / "interface.json")
    env = read_json(evidence / "env.json")
    excluded = (
        "evidence/timeline.jsonl",
        "evidence/repro.sh",
        "evidence/bundle.json",
        "evidence/env.json",
        "evidence/env.sh",
    )
    files = {}
    for path in sorted(work.rglob("*")):
        relative = path.relative_to(work)
        name = relative.as_posix()
        if (not path.is_file() or path.is_symlink()
                or "__pycache__" in relative.parts or name in excluded):
            continue
        files[name] = file_sha256(path)
    write_json(
        evidence / "bundle.json",
        {
            "schema_version": 1,
            "operator": interface["candidate_symbol"],
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "task_doc": interface["task_doc"],
            "generator": {
                "skill": "repo-task-case-gen",
                "phase_supported": env["phase_supported"],
            },
            "atk": {
                "version": env["fingerprint"]["atk"],
                "python": env["selected_python"],
            },
            "interface": {
                key: interface[key]
                for key in _handoff_contract.interface_fields()
            },
            "facets": [{
                "name": "med",
                "yaml": "med.yaml",
                "case_json": "result/med/json/all_med.json",
                "must_cover": "must_cover.json",
                "frozen_dir": "frozen_med",
                "coverage_report": "evidence/coverage.json",
                "freeze_report": "evidence/frozen_inputs.json",
                "validate_report": "evidence/validate.json",
                "adapter_report": "evidence/adapter_binding.json",
            }],
            "files": files,
            "excluded": list(excluded),
            "ignored_dirs": ["__pycache__"],
        },
    )


def project_for(tmp, dims):
    """按 dims 的 dtype 轴反查出一份匹配的出处。

    出处校验不是这些测试的被测对象——它们查的是 attr dtype、取值域这些别的
    判据。让出处随 dims 走，测试就只在自己关心的那件事上红。
    """
    # 验收侧展开产物也会载入这个共用夹具，但没有生成侧的
    # _axis_binding.py；只在生成侧用到这项能力时再导入。
    from _axis_binding import DTYPE_SOURCE_ALIASES

    names = "、".join(DTYPE_SOURCE_ALIASES[str(dtype)][0]
                      for dtype in (dims or {}).get("dtype", ()))
    return fake_project(tmp, names)


def project_from(tmp, readme):
    """把一份真实的 README 摆进工程树里当出处。

    样例的 README 就是它自己的 dtype 出处，让测试照真实用法走一遍：
    出处里混着 attr 的 C++ 类型名，正好证明 dtype_source_excludes 管用。
    """
    project = Path(tmp) / "op_project"
    project.mkdir(parents=True, exist_ok=True)
    source = project / "README.md"
    source.write_text(readme.read_text(encoding="utf-8"), encoding="utf-8")
    return _args(tmp, project, source)


def fake_project(tmp, dtypes=EXAMPLE_DTYPES):
    """造一棵最小工程树，返回 make_must_cover 要的两个参数。"""
    project = Path(tmp) / "op_project"
    project.mkdir(parents=True, exist_ok=True)
    source = project / "README.md"
    source.write_text(f"| 数据类型 | {dtypes} |", encoding="utf-8")
    return _args(tmp, project, source)


def _args(tmp, project, source, baseline_kind="torch"):
    env = Path(tmp) / "env.json"
    env.write_text(json.dumps({"operator_project": {"path": str(project)}}),
                   encoding="utf-8")
    # interface.json 是 S1 的必产物，make_must_cover 缺它会判不了退 3：
    # 比较器判据要按 baseline_kind 分叉，静默按 torch 走会把人推向错的那条路。
    interface = Path(tmp) / "interface.json"
    interface.write_text(json.dumps({"baseline_kind": baseline_kind}),
                         encoding="utf-8")
    return ["--dtype-source", str(source), "--env", str(env),
            "--interface", str(interface)]


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_bundle(work):
    """造一份最小但完整的 S1 + S2 交接包。"""
    work = Path(work)
    evidence = work / "evidence"
    evidence.mkdir(parents=True)

    (evidence / "constraints.md").write_text("# 约束\n", encoding="utf-8")
    write_json(
        evidence / "interface.json",
        {
            "interface_mode": "aclnn",
            "candidate_symbol": "aclnnMedian",
            "baseline_api": "torch.median",
            "baseline_kind": "torch",
            "execution_backend": "npu",
            "baseline_backend": "cpu",
            "mode_source": "任务书 §2.3",
            "task_doc": {"name": "median.md", "sha256": "d" * 64},
        },
    )
    write_json(
        evidence / "env.json",
        {
            "fingerprint": {"atk": "7.3.0"},
            "selected_python": "/opt/python/bin/python3",
            "phase_supported": "仅 Phase A",
        },
    )
    (evidence / "env.sh").write_text("export DEVICE_LIST=''\n", encoding="utf-8")
    write_json(
        evidence / "signature_alignment.json",
        {
            "baseline_adapter": {"required": False},
            "aclnn_adapter": {"required": False},
        },
    )
    write_json(
        evidence / "signature_contract.json",
        {"verdict": "pass", "problems": []},
    )
    (evidence / "timeline.jsonl").write_text("", encoding="utf-8")

    write_json(work / "med_decl.json", {"parameters": {"input": {}}})
    (work / "med_materialize.py").write_text("# materialize\n", encoding="utf-8")
    write_json(work / "must_cover.json", {"combos": [{"dtype": "fp32"}]})
    write_json(
        work / "med_materialized.json",
        {"combos": [{"dtype": "fp32", "shape": [2]}]},
    )
    (work / "med.yaml").write_text("name: torch.median\n", encoding="utf-8")
    (work / "med_constraint.py").write_text("# constraint\n", encoding="utf-8")

    case_json = work / "result" / "med" / "json" / "all_med.json"
    write_json(
        case_json,
        {
            "cases": [
                {
                    "id": "0",
                    "inputs": [
                        {
                            "name": "input",
                            "type": "tensor",
                            "dtype": "fp32",
                            "shape": [2],
                            "range_values": [-1, 1],
                        }
                    ],
                }
            ]
        },
    )
    frozen_input = work / "frozen_med" / "0" / "input.bin"
    frozen_input.parent.mkdir(parents=True)
    frozen_input.write_bytes(b"frozen input")

    pyc = work / "__pycache__" / "x.pyc"
    pyc.parent.mkdir()
    pyc.write_bytes(b"timestamped bytecode")

    case_digest = file_sha256(case_json)
    must_cover_digest = file_sha256(work / "must_cover.json")
    write_json(
        evidence / "coverage.json",
        {
            "case_file_sha256": case_digest,
            "must_cover_sha256": must_cover_digest,
            "must_cover_total": 1,
            "must_cover_hit": 1,
            "missing": [],
        },
    )
    write_json(
        evidence / "validate.json",
        {"case_file_sha256": case_digest, "failures": []},
    )
    write_json(
        evidence / "adapter_binding.json",
        {
            "case_file_sha256": case_digest,
            "verdicts": {},
            "problems": [],
        },
    )
    write_json(
        evidence / "frozen_inputs.json",
        {
            "case_json_sha256": case_digest,
            "frozen_dir": str(work / "frozen_med"),
            "inputs": {"0": {"sha256": file_sha256(frozen_input)}},
            "unmaterialized_ids": [],
            "constant_input_cases": {},
            "constant_input_check": "enforced",
            "baseline_failed_cases": [],
        },
    )
