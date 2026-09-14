"""quickstart_probe: 解析目标仓 docs/QUICKSTART.md 得到 build/run_example 命令模板。
每个用例独立造一份 QUICKSTART.md，不依赖任何真实仓；解析不到的字段必须是 None，
不能凭空编出一个模板。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from pathlib import Path
import quickstart_probe as qp

# 取自真实 ops-nn 仓 docs/QUICKSTART.md「一、编译运行」章节的例子（脱敏后原样保留结构）
REAL_QUICKSTART = """
## 一、编译运行

### 2. 编译AddExample算子

```bash
bash build.sh --pkg --soc=${soc_version} --ops=add_example -j16
```

### 3. 安装AddExample算子包

```bash
./build_out/cann-ops-nn-*linux*.run
```

### 5. 快速验证：运行算子样例

```bash
bash build.sh --run_example add_example eager cust --vendor_name=custom
```
"""


def _write_quickstart(repo_path: Path, content: str) -> None:
    docs = repo_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "QUICKSTART.md").write_text(content, encoding="utf-8")


def test_discover_success(tmp_path: Path) -> None:
    _write_quickstart(tmp_path, REAL_QUICKSTART)

    templates = qp.discover_command_templates(tmp_path)

    assert templates["build"] == "bash build.sh --pkg --soc={soc} --ops={op} -j{jobs}"
    assert templates["run"] == "bash build.sh --run_example {op} eager cust --vendor_name=custom"
    assert templates["source"].endswith("docs/QUICKSTART.md")

    # 模板要能正常代入真实值，不留没替换掉的占位符
    build_cmd = templates["build"].format(soc="ascend910b", op="my_op", jobs=8)
    assert build_cmd == "bash build.sh --pkg --soc=ascend910b --ops=my_op -j8"
    run_cmd = templates["run"].format(op="my_op")
    assert run_cmd == "bash build.sh --run_example my_op eager cust --vendor_name=custom"


def test_discover_missing_doc(tmp_path: Path) -> None:
    # 不写任何 QUICKSTART.md
    templates = qp.discover_command_templates(tmp_path)

    assert templates["build"] is None
    assert templates["run"] is None


def test_discover_partial_missing_run_section(tmp_path: Path) -> None:
    only_build = """
```bash
bash build.sh --pkg --soc=${soc_version} --ops=add_example -j16
```
"""
    _write_quickstart(tmp_path, only_build)

    templates = qp.discover_command_templates(tmp_path)

    assert templates["build"] is not None
    assert templates["run"] is None


def test_discover_unrecognized_content(tmp_path: Path) -> None:
    # 有 fenced bash 块，但既不是 build 也不是 run_example 命令
    _write_quickstart(tmp_path, "```bash\necho hello\n```\n")

    templates = qp.discover_command_templates(tmp_path)

    assert templates["build"] is None
    assert templates["run"] is None
