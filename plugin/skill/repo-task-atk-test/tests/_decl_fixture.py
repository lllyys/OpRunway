"""make_must_cover 的 CLI 现在要 dtype 出处，出处要落在待验收算子工程里。

真机上这两样都是 S1 的现成产物（`evidence/env.json` 加工程 README），
测试里没有工程树，就地造一棵最小的：一份 env.json 指向它，一份声明了
数据类型的 README。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _axis_binding import DTYPE_SOURCE_ALIASES  # noqa: E402

# 与 assets/example/decl.json 的 dtype 轴逐一对应的 C 侧名字。
# 出处与 dims 是双向核对的：这里多一个名字，那边就得多声明一个 dtype。
EXAMPLE_DTYPES = "FLOAT16、BFLOAT16、FLOAT、INT32"


def project_for(tmp, dims):
    """按 dims 的 dtype 轴反查出一份匹配的出处。

    出处校验不是这些测试的被测对象——它们查的是 attr dtype、取值域这些别的
    判据。让出处随 dims 走，测试就只在自己关心的那件事上红。
    """
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
