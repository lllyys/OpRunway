# 内置 aclnn 当精度真值 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让零上下文 agent 能用本 skill 完成「待验收 aclnn 算子 vs CANN 内置同名 aclnn 算子、逐位比对」的验收，且拿错库、自己跟自己比、种子没钉死这三种假绿都被门禁挡住。

**Architecture:** 两轮跑测 + `accuracy_load`：第一轮把 `ATK_CUSTOM_OPP_PATH` 钉在内置 `.so` 上跑出真值存盘，搬成 load 节点认得的目录；第二轮钉在本轮 vendor 的 `.so` 上跑待验收算子，把第一轮的输出当标杆读入。两轮加载了哪份库从 ATK 日志抓取并落盘，裁决前由门禁核对。所有新脚本只读 ATK 的产物与日志，不改 `atk/`（红线 2）。

**Tech Stack:** Python 3 标准库 + unittest；ctypes（判断某个 `.so` 里有没有某个函数）；torch 仅用于反证实验的改数，无 torch 时该测试 skip。

## Global Constraints

- 不修改 `atk/` 下任何文件（CLAUDE.md 红线 2）。
- 新知识先进 `skill/repo-task-atk-test/references/`，不写在脚本注释里（CLAUDE.md §5）。
- 新产物必须登记进 `references/artifact-contracts.json`，否则 `tests/test_contracts.py` 的结构不变量会红。
- 运行期文档与脚本报错里禁用这些词：`出处`、`来源文件`、`候选符号`、`符号`、`投影`、`薄壳`（`tests/test_plain_language.py`）。
- `SKILL.md` 与 `references/*.md` 的正文行：单行 ≤100 字符，且最多一个 `。`（`tests/test_document_style.py`）。
- `SKILL.md` 总行数 ≤360。
- 门禁脚本退出码约定：`0` 通过 / `2` 判定不通过 / `3` 无法判定。
- 所有路径相对 `skill/repo-task-atk-test/`，命令都在该目录下跑。
- 本机没有装 torch，`tests/test_make_yaml.py` 等 20 条会失败，那是既有状态，不是本计划引入的。判断「有没有跑挂」看的是失败数不超过 20 且失败文件名不变。

## 已经完成、不要重做

- `references/builtin-baseline.md`：这条路的全部规范（四级查找、真值目录规则、两步跑测、三件取证、反证实验、种子、比较器、结论写法、四种错写法）。
- `references/case-design.md#种子类参数` + `scripts/validate_cases.py` 的 C7 门禁（种子必须钉成常量）。
- `references/atk-cli.md` 里两条错命令已删，改成指向 `builtin-baseline.md`。
- `references/intake.md`、`SKILL.md`、`references/glossary.md` 已登记这条路。
- `scripts/derive_interface.py` 已支持 `--baseline-kind cann_builtin --baseline-source`。
- `scripts/probe_env.py` 的 `link_custom_opp()` 与 `--custom-opp` 已把待验收算子那一侧钉死。

---

### Task 1: 骨架登记三个新产物

**Files:**
- Modify: `references/artifact-contracts.json`
- Modify: `references/decision-points.md`（由脚本重新生成，不手改）
- Test: `tests/test_builtin_baseline.py`

**Interfaces:**
- Consumes: 无
- Produces: 骨架里三个新条目，键名固定为 `内置库指纹`、`内置真值`、`内置真值取证`，后续任务的脚本名与它们的 `producer` 字段必须一致。

- [ ] **Step 1: 写失败测试**

在 `tests/test_builtin_baseline.py` 末尾、`if __name__` 之前追加：

```python
class SpineRegistrationTest(unittest.TestCase):
    """新产物必须进骨架，否则完备性不可判定（CLAUDE.md §3.1）。"""

    def setUp(self):
        self.data = json.loads(
            (SKILL_ROOT / "references" / "artifact-contracts.json")
            .read_text(encoding="utf-8"))["artifacts"]

    def test_three_builtin_artifacts_are_registered(self):
        for name, producer, stage in (
                ("内置库指纹", "resolve_opp_library.py", "S3"),
                ("内置真值", "capture_reference.py", "S3"),
                ("内置真值取证", "capture_reference.py", "S3")):
            with self.subTest(artifact=name):
                self.assertIn(name, self.data)
                self.assertEqual(producer, self.data[name]["producer"])
                self.assertEqual(stage, self.data[name]["stage"])

    def test_they_are_conditional_on_the_baseline_kind(self):
        for name in ("内置库指纹", "内置真值", "内置真值取证"):
            with self.subTest(artifact=name):
                self.assertIn("cann_builtin", self.data[name]["condition"])

    def test_they_point_at_the_new_reference(self):
        for name in ("内置库指纹", "内置真值", "内置真值取证"):
            with self.subTest(artifact=name):
                self.assertTrue(
                    self.data[name]["spec"].startswith("references/builtin-baseline.md"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_builtin_baseline.py::SpineRegistrationTest -q`
Expected: FAIL，`KeyError` 或 `AssertionError: '内置库指纹' not found`

- [ ] **Step 3: 写进骨架**

在 `references/artifact-contracts.json` 的 `artifacts` 对象里追加三条（注意 JSON 不能有尾逗号）：

```json
"evidence/opp_library_<side>.json": {
  "stage": "S3",
  "owner": "script",
  "condition": "interface.json 的 baseline_kind 为 cann_builtin；其余情况不产出",
  "spec": "references/builtin-baseline.md#同名算子不要让-atk-去搜",
  "template": null,
  "producer": "resolve_opp_library.py",
  "consumed_by": ["capture_reference.py", "check_golden_source.py"],
  "risk": "社区算子与内置同名，ATK 按算子名逐级搜库；搜错就是拿同一份实现自己跟自己比，报告 100% 通过而什么都没验",
  "fields": {
    "path": {
      "owner": "script",
      "source": "按 ATK 的候选清单解析出的 .so 完整路径",
      "consumer": "两轮跑测的 ATK_CUSTOM_OPP_PATH",
      "failure": "指错库时绑定阶段报 AttributeError: 库文件 ... 中无法找到函数"
    },
    "sha256": {
      "owner": "script",
      "source": "该 .so 文件的 SHA256",
      "consumer": "check_golden_source.py、报告",
      "failure": "两轮 sha256 相同即两轮跑的是同一份实现，结论作废"
    },
    "side": {
      "owner": "script",
      "source": "builtin 或 candidate",
      "consumer": "check_golden_source.py",
      "failure": "两侧标错，门禁比对的方向反了"
    }
  }
},
"evidence/golden_builtin/": {
  "stage": "S3",
  "owner": "script",
  "condition": "interface.json 的 baseline_kind 为 cann_builtin；其余情况真值在跑测当场产生",
  "spec": "references/builtin-baseline.md#真值目录必须长成什么样",
  "template": null,
  "producer": "capture_reference.py",
  "consumed_by": ["atk node --task accuracy_load --output_path"],
  "risk": "目录名与用例 JSON 文件名任一处对不上，ATK 读到空目录，表现是文件数不符而不是路径错",
  "fields_not_applicable": "落盘的输出张量，结构由 ATK 的 accuracy_load 规则担保"
},
"evidence/golden_provenance.json": {
  "stage": "S3",
  "owner": "script",
  "condition": "interface.json 的 baseline_kind 为 cann_builtin；其余情况不产出",
  "spec": "references/builtin-baseline.md#三件必须取证的事",
  "template": null,
  "producer": "capture_reference.py",
  "consumed_by": ["check_golden_source.py", "verdict.py", "报告"],
  "risk": "缺任何一项，结论都退化成不可核验的口头断言",
  "fields": {
    "builtin_library": {
      "owner": "script",
      "source": "第一轮 ATK 日志里 import ... from ... success! 那一行抓到的路径",
      "consumer": "check_golden_source.py",
      "failure": "抓不到就说明这轮没真正加载过算子库，真值来历不明"
    },
    "case_json_sha256": {
      "owner": "script",
      "source": "第一轮用的用例 JSON 的 SHA256",
      "consumer": "check_golden_source.py",
      "failure": "与第二轮不一致时两轮比的不是同一批用例"
    },
    "input_data": {
      "owner": "script",
      "source": "第一轮消费的冻结输入目录",
      "consumer": "check_golden_source.py",
      "failure": "两轮输入不同则逐位比对无意义"
    },
    "staged_dir": {
      "owner": "script",
      "source": "搬运后的真值目录",
      "consumer": "第二轮的 --output_path",
      "failure": "写错则 load 节点读到空目录"
    },
    "counter_experiment": {
      "owner": "script",
      "source": "反证实验的记录：改了哪条用例、重跑后是否变 Fail",
      "consumer": "check_golden_source.py",
      "failure": "没做反证实验时，全绿既可能是真的一致，也可能是自己跟自己比"
    }
  }
}
```

- [ ] **Step 4: 跑测试确认通过并重新生成派生视图**

```bash
python3 -m pytest tests/test_builtin_baseline.py -q
python3 scripts/render_views.py --write
python3 -m pytest tests/test_contracts.py -q
```
Expected: 两次 pytest 都 PASS；`render_views.py` 打印「已生成 .../decision-points.md」

- [ ] **Step 5: 提交**

```bash
git add references/artifact-contracts.json references/decision-points.md tests/test_builtin_baseline.py
git commit -m "feat: 骨架登记内置真值这条路的三个产物"
```

---

### Task 2: 解析并钉死每一侧的算子库

**Files:**
- Create: `scripts/_opp_library.py`
- Create: `scripts/resolve_opp_library.py`
- Test: `tests/test_opp_library.py`

**Interfaces:**
- Consumes: Task 1 登记的 `evidence/opp_library_<side>.json` 字段名 `path` / `sha256` / `side`
- Produces:
  - `_opp_library.builtin_candidates(opp_path) -> list[str]`
  - `_opp_library.has_function(so_path, func_name) -> bool`
  - `_opp_library.resolve_builtin(func_name, opp_path, probe=has_function) -> str`（找不到抛 `LibraryNotFound`）
  - `_opp_library.fingerprint(path, side) -> dict`，键 `side` / `path` / `sha256`
  - CLI：`resolve_opp_library.py --op <aclnn 名> --side builtin|candidate [--library <path>] [--opp-path <path>] -o <json>`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_opp_library.py`：

```python
"""解析每一侧的算子库。

社区算子与 CANN 内置基本都同名，ATK 按算子名逐级搜库
（atk/tasks/backends/lib_interface/acl_wrapper.py:524-575）。
搜错一次两轮就是同一份实现，报告 100% 通过而什么都没验。
这里把「搜」换成「解析好再钉死」。
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _opp_library  # noqa: E402


def temp_dir(case):
    """用例结束时自动清理的临时目录。不用 enterContext：真机解释器可能是 3.9。"""
    holder = tempfile.TemporaryDirectory()
    case.addCleanup(holder.cleanup)
    return Path(holder.name)


class BuiltinCandidatesTest(unittest.TestCase):
    def test_candidate_list_matches_atk(self):
        # 名单与顺序都照抄 acl_wrapper.py:565-573，错一个就可能挑到别的库。
        got = _opp_library.builtin_candidates("/opp")
        self.assertEqual([
            "/opp/../aarch64-linux/lib64/libopapi_math.so",
            "/opp/../aarch64-linux/lib64/libopapi_nn.so",
            "/opp/../aarch64-linux/lib64/libopapi_cv.so",
            "/opp/../aarch64-linux/lib64/libopapi_transformer.so",
            "/opp/../aarch64-linux/lib64/libopapi.so",
            "/opp/../x86_64-linux/lib64/libopapi_math.so",
            "/opp/../x86_64-linux/lib64/libopapi_nn.so",
            "/opp/../x86_64-linux/lib64/libopapi_cv.so",
            "/opp/../x86_64-linux/lib64/libopapi_transformer.so",
            "/opp/../x86_64-linux/lib64/libopapi.so",
            "/opp/lib64/libopapi_math.so",
            "/opp/lib64/libopapi_nn.so",
            "/opp/lib64/libopapi_cv.so",
            "/opp/lib64/libopapi_transformer.so",
            "/opp/lib64/libopapi.so",
        ], got)


class ResolveBuiltinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        for name in ("libopapi_math.so", "libopapi_nn.so"):
            path = self.tmp / "lib64" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake")

    def test_picks_the_library_that_has_the_function(self):
        def probe(path, func):
            return path.endswith("libopapi_nn.so")
        got = _opp_library.resolve_builtin("aclnnBernoulliGetWorkspaceSize",
                                           str(self.tmp), probe=probe)
        self.assertTrue(got.endswith("lib64/libopapi_nn.so"))

    def test_skips_files_that_do_not_exist(self):
        def probe(path, func):
            return True
        got = _opp_library.resolve_builtin("aclnnFooGetWorkspaceSize",
                                           str(self.tmp), probe=probe)
        # 名单里 math 排在 nn 前面，两个都存在时取 math。
        self.assertTrue(got.endswith("lib64/libopapi_math.so"))

    def test_no_library_carries_the_function(self):
        with self.assertRaises(_opp_library.LibraryNotFound) as ctx:
            _opp_library.resolve_builtin("aclnnFooGetWorkspaceSize",
                                         str(self.tmp), probe=lambda p, f: False)
        self.assertIn("aclnnFooGetWorkspaceSize", str(ctx.exception))


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_carries_path_sha_and_side(self):
        tmp = temp_dir(self)
        lib = tmp / "libcust_opapi.so"
        lib.write_bytes(b"vendor")
        got = _opp_library.fingerprint(str(lib), "candidate")
        self.assertEqual("candidate", got["side"])
        self.assertEqual(str(lib), got["path"])
        self.assertEqual(hashlib.sha256(b"vendor").hexdigest(), got["sha256"])


class CliTest(unittest.TestCase):
    def test_candidate_side_writes_the_fingerprint(self):
        tmp = temp_dir(self)
        lib = tmp / "libcust_opapi.so"
        lib.write_bytes(b"vendor")
        out = tmp / "fp.json"
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "resolve_opp_library.py"),
             "--op", "aclnnBernoulli", "--side", "candidate",
             "--library", str(lib), "-o", str(out)],
            capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("candidate", payload["side"])
        self.assertEqual("aclnnBernoulli", payload["op"])

    def test_candidate_side_without_library_is_refused(self):
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "resolve_opp_library.py"),
             "--op", "aclnnBernoulli", "--side", "candidate", "-o", "/dev/null"],
            capture_output=True, text=True)
        self.assertEqual(3, proc.returncode)
        self.assertIn("--library", proc.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_opp_library.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_opp_library'`

- [ ] **Step 3: 写实现**

新建 `scripts/_opp_library.py`：

```python
"""解析某个算子在某一侧对应的算子库文件。

ATK 自己是按算子名逐级搜的（acl_wrapper.py:524-575），而第一级
ATK_CUSTOM_OPP_PATH 路径存在就直接用、不校验里面有没有这个算子
（acl_wrapper.py:536-538）。社区算子与 CANN 内置基本都同名，
让它去搜就有搜错的机会，搜错就是两轮跑同一份实现。

所以这里把候选清单照抄过来自己解析，解析结果钉进 ATK_CUSTOM_OPP_PATH，
搜索路径不再参与决策。
"""

import ctypes
import hashlib
import os

# 照抄 acl_wrapper.py:565-573 的两张清单，顺序也照抄。
BUILTIN_SUBDIRS = ("../aarch64-linux/", "../x86_64-linux/", "")
BUILTIN_SO_NAMES = ("libopapi_math.so", "libopapi_nn.so", "libopapi_cv.so",
                    "libopapi_transformer.so", "libopapi.so")


class LibraryNotFound(Exception):
    """候选清单里没有一个库带这个算子。"""


def builtin_candidates(opp_path):
    """内置库的候选清单，顺序与 ATK 一致。"""
    return [os.path.join(opp_path, f"{sub}lib64/{name}")
            for sub in BUILTIN_SUBDIRS
            for name in BUILTIN_SO_NAMES]


def has_function(so_path, func_name):
    """库里有没有这个函数。与 ATK 的 check_interface_exists 同法。"""
    try:
        lib = ctypes.CDLL(so_path)
        getattr(lib, func_name)
        return True
    except (OSError, AttributeError):
        return False


def resolve_builtin(func_name, opp_path, probe=has_function):
    """在内置候选清单里挑出真正带这个算子的那一个。"""
    tried = []
    for path in builtin_candidates(opp_path):
        if not os.path.exists(path):
            continue
        tried.append(path)
        if probe(path, func_name):
            return path
    raise LibraryNotFound(
        f"{opp_path} 下没有一个库带 {func_name}。\n"
        f"  已试过 {tried or '（候选清单里的文件一个都不存在）'}\n"
        "  → 确认 ASCEND_OPP_PATH 指对了，且这个算子确实是 CANN 内置算子。")


def fingerprint(path, side):
    """把一份库记成可核对的三元组。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {"side": side, "path": os.path.abspath(path),
            "sha256": digest.hexdigest()}
```

新建 `scripts/resolve_opp_library.py`：

```python
"""解析并记录某一侧的算子库文件。

输入：算子名、哪一侧、内置时的 opp 根目录或待验收侧的库文件。
输出：库指纹 JSON。
退出码：0 完成；3 无法判定。

两轮跑测各跑一次，把 path 钉进 ATK_CUSTOM_OPP_PATH，
再由 check_golden_source.py 核对两轮不是同一份。
"""

import argparse
import json
import os
import sys

from _opp_library import LibraryNotFound, fingerprint, resolve_builtin


def main():
    parser = argparse.ArgumentParser(description="解析并记录某一侧的算子库")
    parser.add_argument("--op", required=True, help="aclnn 接口名，如 aclnnBernoulli")
    parser.add_argument("--side", required=True, choices=("builtin", "candidate"))
    parser.add_argument("--library", help="--side candidate 时本轮 vendor 的 .so")
    parser.add_argument("--opp-path", default=os.environ.get("ASCEND_OPP_PATH"),
                        help="--side builtin 时的 ASCEND_OPP_PATH，默认读环境变量")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    if args.side == "candidate":
        if not args.library:
            print("--side candidate 必须给 --library：待验收算子那一侧的库路径"
                  "是 S3 装包的产物，猜不出来。", file=sys.stderr)
            return 3
        if not os.path.exists(args.library):
            print(f"{args.library} 不存在。", file=sys.stderr)
            return 3
        payload = fingerprint(args.library, "candidate")
    else:
        if not args.opp_path:
            print("--side builtin 需要 ASCEND_OPP_PATH：先 source CANN 的 "
                  "set_env.sh，或显式给 --opp-path。", file=sys.stderr)
            return 3
        try:
            path = resolve_builtin(f"{args.op}GetWorkspaceSize", args.opp_path)
        except LibraryNotFound as exc:
            print(str(exc), file=sys.stderr)
            return 3
        payload = fingerprint(path, "builtin")

    payload["op"] = args.op
    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(payload, sink, ensure_ascii=False, indent=2)
    print(f"{payload['side']} 侧算子库 {payload['path']}")
    print(f"  sha256 {payload['sha256']}")
    print(f"  跑测前 export ATK_CUSTOM_OPP_PATH={payload['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_opp_library.py -q`
Expected: PASS，8 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/_opp_library.py scripts/resolve_opp_library.py tests/test_opp_library.py
git commit -m "feat: 解析并钉死每一侧的算子库，不让 ATK 按同名去搜"
```

---

### Task 3: 从跑测日志取证 + 把内置输出搬成真值目录

**Files:**
- Create: `scripts/capture_reference.py`
- Test: `tests/test_capture_reference.py`

**Interfaces:**
- Consumes: `_opp_library.fingerprint` 产出的字典（键 `side` / `path` / `sha256`）
- Produces:
  - `capture_reference.loaded_library(log_text, op) -> str | None`
  - `capture_reference.stage_golden(run_output, staged_dir, node_dir="pyaclnn_0", as_name="cpu_0") -> int`（返回搬了几条用例）
  - `capture_reference.build_provenance(...) -> dict`，键 `builtin_library` / `case_json` / `case_json_sha256` / `input_data` / `staged_dir` / `cases` / `counter_experiment`
  - CLI：`capture_reference.py --op <名> --from-run <atk_output/xxx/output> --log <日志> --case-json <path> --input-data <dir> --library-fingerprint <json> --staged evidence/golden_builtin -o evidence/golden_provenance.json`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_capture_reference.py`：

```python
"""内置那一轮的取证与搬运。

日志里那一行是 info 级、默认级别就打得出来
（atk/tasks/backends/pyaclnn_backend.py:246），它是「这轮到底加载了哪份库」
的唯一机械依据。搬运则要把目录名改成 load 节点认得的形状
（atk/common/utils.py:259）。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import capture_reference as cr  # noqa: E402


def temp_dir(case):
    """用例结束时自动清理的临时目录。不用 enterContext：真机解释器可能是 3.9。"""
    holder = tempfile.TemporaryDirectory()
    case.addCleanup(holder.cleanup)
    return Path(holder.name)

LOG = """\
2026-08-17 10:00:01 [INFO] start task
2026-08-17 10:00:02 [INFO] import aclnnBernoulliGetWorkspaceSize  from /usr/local/Ascend/latest/opp/../aarch64-linux/lib64/libopapi_nn.so success!
2026-08-17 10:00:03 [INFO] done
"""


class LoadedLibraryTest(unittest.TestCase):
    def test_reads_the_path_from_the_info_line(self):
        self.assertEqual(
            "/usr/local/Ascend/latest/opp/../aarch64-linux/lib64/libopapi_nn.so",
            cr.loaded_library(LOG, "aclnnBernoulli"))

    def test_other_operator_does_not_match(self):
        self.assertIsNone(cr.loaded_library(LOG, "aclnnMedian"))

    def test_absent_line_returns_none(self):
        self.assertIsNone(cr.loaded_library("nothing here\n", "aclnnBernoulli"))

    def test_last_line_wins_when_repeated(self):
        # 每条用例都会打一行；取最后一行，前面可能是别的算子的绑定。
        text = LOG + ("[INFO] import aclnnBernoulliGetWorkspaceSize  "
                      "from /vendor/libcust_opapi.so success!\n")
        self.assertEqual("/vendor/libcust_opapi.so",
                         cr.loaded_library(text, "aclnnBernoulli"))


class StageGoldenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.run_output = self.tmp / "atk_output" / "task_x" / "output"
        for case_id in ("0", "1"):
            case_dir = self.run_output / "pyaclnn_0" / "bernoulli_cases" / case_id
            case_dir.mkdir(parents=True)
            (case_dir / "output_0.pt").write_bytes(b"golden" + case_id.encode())
        cpu_dir = self.run_output / "cpu_0" / "bernoulli_cases" / "0"
        cpu_dir.mkdir(parents=True)
        (cpu_dir / "output_0.pt").write_bytes(b"torch")

    def test_moves_the_aclnn_directory_under_the_load_node_name(self):
        staged = self.tmp / "golden_builtin"
        count = cr.stage_golden(str(self.run_output), str(staged))
        self.assertEqual(2, count)
        self.assertEqual(
            b"golden0",
            (staged / "cpu_0" / "bernoulli_cases" / "0" / "output_0.pt").read_bytes())

    def test_does_not_take_the_cpu_directory(self):
        # CPU 侧是 torch 基线的输出，出参精度被上调过，搬它复跑会被算子拒绝。
        staged = self.tmp / "golden_builtin"
        cr.stage_golden(str(self.run_output), str(staged))
        self.assertNotEqual(
            b"torch",
            (staged / "cpu_0" / "bernoulli_cases" / "0" / "output_0.pt").read_bytes())

    def test_missing_aclnn_directory_is_refused(self):
        staged = self.tmp / "golden_builtin"
        empty = self.tmp / "empty" / "output"
        (empty / "cpu_0").mkdir(parents=True)
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.stage_golden(str(empty), str(staged))
        self.assertIn("pyaclnn_0", str(ctx.exception))

    def test_restaging_replaces_the_previous_content(self):
        staged = self.tmp / "golden_builtin"
        (staged / "cpu_0" / "stale").mkdir(parents=True)
        cr.stage_golden(str(self.run_output), str(staged))
        self.assertFalse((staged / "cpu_0" / "stale").exists())


class ProvenanceTest(unittest.TestCase):
    def test_provenance_carries_every_evidence_item(self):
        tmp = temp_dir(self)
        case_json = tmp / "bernoulli_cases.json"
        case_json.write_text('{"cases": []}', encoding="utf-8")
        got = cr.build_provenance(
            op="aclnnBernoulli",
            library="/opp/lib64/libopapi_nn.so",
            fingerprint={"side": "builtin", "path": "/opp/lib64/libopapi_nn.so",
                         "sha256": "abc"},
            case_json=str(case_json),
            input_data="frozen_main",
            staged_dir=str(tmp / "golden_builtin"),
            cases=["0", "1"])
        self.assertEqual("/opp/lib64/libopapi_nn.so", got["builtin_library"]["path"])
        self.assertEqual("abc", got["builtin_library"]["sha256"])
        self.assertEqual(["0", "1"], got["cases"])
        self.assertEqual("frozen_main", got["input_data"])
        self.assertIsNone(got["counter_experiment"])
        self.assertEqual(64, len(got["case_json_sha256"]))

    def test_library_mismatch_between_log_and_fingerprint_is_refused(self):
        tmp = temp_dir(self)
        case_json = tmp / "c.json"
        case_json.write_text("{}", encoding="utf-8")
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.build_provenance(
                op="aclnnBernoulli",
                library="/vendor/libcust_opapi.so",
                fingerprint={"side": "builtin", "path": "/opp/lib64/libopapi_nn.so",
                             "sha256": "abc"},
                case_json=str(case_json), input_data="frozen_main",
                staged_dir=str(tmp), cases=["0"])
        self.assertIn("日志里加载的是", str(ctx.exception))


class CliTest(unittest.TestCase):
    def test_end_to_end_writes_provenance(self):
        tmp = temp_dir(self)
        run_output = tmp / "output"
        case_dir = run_output / "pyaclnn_0" / "cases" / "0"
        case_dir.mkdir(parents=True)
        (case_dir / "output_0.pt").write_bytes(b"g")
        log = tmp / "builtin.log"
        log.write_text(
            "[INFO] import aclnnBernoulliGetWorkspaceSize  from /opp/lib64/a.so success!\n",
            encoding="utf-8")
        lib = tmp / "a.so"
        lib.write_bytes(b"x")
        fp = tmp / "fp.json"
        fp.write_text(json.dumps({"side": "builtin", "path": "/opp/lib64/a.so",
                                  "sha256": "z", "op": "aclnnBernoulli"}),
                      encoding="utf-8")
        case_json = tmp / "cases.json"
        case_json.write_text('{"cases": [{"id": 0}]}', encoding="utf-8")
        out = tmp / "prov.json"
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "capture_reference.py"),
             "--op", "aclnnBernoulli", "--from-run", str(run_output),
             "--log", str(log), "--case-json", str(case_json),
             "--input-data", "frozen_main", "--library-fingerprint", str(fp),
             "--staged", str(tmp / "golden_builtin"), "-o", str(out)],
            capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(["0"], payload["cases"])

    def test_vendor_library_in_the_builtin_run_exits_two(self):
        tmp = temp_dir(self)
        run_output = tmp / "output"
        (run_output / "pyaclnn_0" / "cases" / "0").mkdir(parents=True)
        (run_output / "pyaclnn_0" / "cases" / "0" / "output_0.pt").write_bytes(b"g")
        log = tmp / "builtin.log"
        log.write_text(
            "[INFO] import aclnnBernoulliGetWorkspaceSize  "
            "from /home/x/vendors/customize/op_api/lib/libcust_opapi.so success!\n",
            encoding="utf-8")
        fp = tmp / "fp.json"
        fp.write_text(json.dumps(
            {"side": "builtin",
             "path": "/home/x/vendors/customize/op_api/lib/libcust_opapi.so",
             "sha256": "z", "op": "aclnnBernoulli"}), encoding="utf-8")
        case_json = tmp / "cases.json"
        case_json.write_text('{"cases": [{"id": 0}]}', encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "capture_reference.py"),
             "--op", "aclnnBernoulli", "--from-run", str(run_output),
             "--log", str(log), "--case-json", str(case_json),
             "--input-data", "frozen_main", "--library-fingerprint", str(fp),
             "--staged", str(tmp / "golden_builtin"), "-o", str(tmp / "p.json")],
            capture_output=True, text=True)
        self.assertEqual(2, proc.returncode)
        self.assertIn("vendors", proc.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_capture_reference.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'capture_reference'`

- [ ] **Step 3: 写实现**

新建 `scripts/capture_reference.py`：

```python
"""内置那一轮的取证与搬运。

输入：内置那一轮的 output 目录、跑测日志、用例 JSON、库指纹。
输出：搬好的真值目录 + 取证 JSON。
退出码：0 完成；2 取证不通过（加载的不是内置）；3 无法判定。

跑测本身由 run_atk_task.py 拉起，本脚本只消费它的产物，
所以不需要 NPU 也能自检。命令见 references/builtin-baseline.md#两步跑测。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys

VENDOR_MARK = os.sep + "vendors" + os.sep


class CaptureError(Exception):
    """取证或搬运不成立。"""


def loaded_library(log_text, op):
    """从日志里读这一轮实际加载了哪份库。

    ATK 在 pyaclnn_backend.py:246 打这一行，info 级，默认日志级别就有。
    每条用例都会打一次，取最后一行。
    """
    pattern = re.compile(
        rf"import\s+{re.escape(op)}GetWorkspaceSize\s+from\s+(\S+)\s+success!")
    found = pattern.findall(log_text)
    return found[-1] if found else None


def stage_golden(run_output, staged_dir, node_dir="pyaclnn_0", as_name="cpu_0"):
    """把内置那一轮的输出搬成 load 节点认得的目录。

    要搬的是 aclnn 那一侧：CPU 侧是 torch 基线的输出，出参精度被上调过，
    拿它复跑 fp16/bf16 会在 GetWorkspaceSize 阶段被算子拒绝。
    """
    source = os.path.join(run_output, node_dir)
    if not os.path.isdir(source):
        raise CaptureError(
            f"{run_output} 下没有 {node_dir} 目录，现有 "
            f"{sorted(os.listdir(run_output)) if os.path.isdir(run_output) else '（目录不存在）'}。\n"
            "  → 这一轮不是「aclnn 主节点 + CPU 节点」的拓扑，或者没带 "
            "--save_data output。见 references/builtin-baseline.md#两步跑测。")
    target = os.path.join(staged_dir, as_name)
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(staged_dir, exist_ok=True)
    shutil.copytree(source, target)
    return len(collect_case_ids(target))


def collect_case_ids(node_root):
    """真值目录里有哪些用例号。"""
    ids = []
    for save_name in sorted(os.listdir(node_root)):
        save_dir = os.path.join(node_root, save_name)
        if not os.path.isdir(save_dir):
            continue
        for case_id in sorted(os.listdir(save_dir), key=lambda x: (len(x), x)):
            if os.path.isdir(os.path.join(save_dir, case_id)):
                ids.append(case_id)
    return ids


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_provenance(op, library, fingerprint, case_json, input_data,
                     staged_dir, cases):
    """把这一轮的全部依据记成一份可核对的 JSON。"""
    if os.path.abspath(library) != os.path.abspath(fingerprint["path"]):
        raise CaptureError(
            f"日志里加载的是 {library}，钉死的却是 {fingerprint['path']}。\n"
            "  → 跑测前没有 export ATK_CUSTOM_OPP_PATH，或者导出的不是这一份。"
            "这一轮的真值来历不明，重跑。")
    return {
        "op": op,
        "builtin_library": dict(fingerprint),
        "case_json": os.path.abspath(case_json),
        "case_json_sha256": sha256_file(case_json),
        "input_data": input_data,
        "staged_dir": os.path.abspath(staged_dir),
        "cases": cases,
        "counter_experiment": None,
    }


def main():
    parser = argparse.ArgumentParser(description="内置那一轮的取证与搬运")
    parser.add_argument("--op", required=True, help="aclnn 接口名")
    parser.add_argument("--from-run", required=True,
                        help="内置那一轮的 atk_output/<任务>/output 目录")
    parser.add_argument("--log", required=True, help="内置那一轮的跑测日志")
    parser.add_argument("--case-json", required=True)
    parser.add_argument("--input-data", required=True, help="冻结输入目录")
    parser.add_argument("--library-fingerprint", required=True,
                        help="resolve_opp_library.py --side builtin 的产物")
    parser.add_argument("--staged", default="evidence/golden_builtin")
    parser.add_argument("-o", "--output", default="evidence/golden_provenance.json")
    args = parser.parse_args()

    for path in (args.from_run, args.log, args.case_json, args.library_fingerprint):
        if not os.path.exists(path):
            print(f"{path} 不存在。", file=sys.stderr)
            return 3

    with open(args.library_fingerprint, encoding="utf-8") as handle:
        fingerprint = json.load(handle)

    with open(args.log, encoding="utf-8", errors="replace") as handle:
        library = loaded_library(handle.read(), args.op)
    if not library:
        print(f"{args.log} 里没有 {args.op}GetWorkspaceSize 的加载记录。\n"
              "  → 这一轮没真正绑定过算子库，可能建任务就失败了。先看日志本身。",
              file=sys.stderr)
        return 3

    if VENDOR_MARK in os.path.abspath(library) + os.sep:
        print(f"内置那一轮加载的是 {library}，路径里有 vendors。\n"
              "  → 这是某个 vendor 的库，不是内置。跑测前把 ATK_CUSTOM_OPP_PATH "
              "export 成 resolve_opp_library.py --side builtin 解析出来的那份，重跑。",
              file=sys.stderr)
        return 2

    try:
        stage_golden(args.from_run, args.staged)
        cases = collect_case_ids(os.path.join(args.staged, "cpu_0"))
        payload = build_provenance(
            args.op, library, fingerprint, args.case_json, args.input_data,
            args.staged, cases)
    except CaptureError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(payload, sink, ensure_ascii=False, indent=2)
    print(f"内置真值 {len(cases)} 条 → {payload['staged_dir']}")
    print(f"  库 {library}")
    print(f"  取证写入 {args.output}")
    print("  下一步：做反证实验（capture_reference.py --tamper），没做过不许裁决。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_capture_reference.py -q`
Expected: PASS，11 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/capture_reference.py tests/test_capture_reference.py
git commit -m "feat: 内置那一轮的日志取证与真值搬运"
```

---

### Task 4: 反证实验

**Files:**
- Modify: `scripts/capture_reference.py`
- Test: `tests/test_capture_reference.py`

**Interfaces:**
- Consumes: Task 3 的 `build_provenance` 产出的 `counter_experiment` 键（此前恒为 `None`）
- Produces:
  - `capture_reference.tamper(staged_dir, case_id) -> dict`，键 `case_id` / `file` / `backup`
  - `capture_reference.record_counter_experiment(provenance_path, case_id, detected) -> dict`
  - CLI 两个新模式：`--tamper <用例号>` 与 `--conclude-tamper <用例号> --detected|--not-detected`

- [ ] **Step 1: 写失败测试**

在 `tests/test_capture_reference.py` 的 `CliTest` 之前插入：

```python
try:
    import torch
except ImportError:  # pragma: no cover - 无 torch 的环境跳过
    torch = None


@unittest.skipIf(torch is None, "需要 torch")
class TamperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.case_dir = self.tmp / "golden" / "cpu_0" / "cases" / "3"
        self.case_dir.mkdir(parents=True)
        torch.save(torch.zeros(8, dtype=torch.float32),
                   self.case_dir / "output_0.pt")

    def test_tamper_changes_exactly_one_element(self):
        info = cr.tamper(str(self.tmp / "golden"), "3")
        after = torch.load(self.case_dir / "output_0.pt")
        self.assertEqual(1, int((after != 0).sum()))
        self.assertTrue(Path(info["backup"]).exists())

    def test_unknown_case_id_is_refused(self):
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.tamper(str(self.tmp / "golden"), "99")
        self.assertIn("99", str(ctx.exception))


class CounterExperimentRecordTest(unittest.TestCase):
    def test_detected_result_is_written_back(self):
        tmp = temp_dir(self)
        prov = tmp / "prov.json"
        prov.write_text(json.dumps({"cases": ["3"], "counter_experiment": None}),
                        encoding="utf-8")
        got = cr.record_counter_experiment(str(prov), "3", True)
        self.assertEqual({"case_id": "3", "detected": True}, got["counter_experiment"])
        self.assertEqual(
            True,
            json.loads(prov.read_text(encoding="utf-8"))["counter_experiment"]["detected"])

    def test_not_detected_is_recorded_too(self):
        # 记下来才能让门禁拒绝，抹掉等于把失败藏起来。
        tmp = temp_dir(self)
        prov = tmp / "prov.json"
        prov.write_text(json.dumps({"cases": ["3"], "counter_experiment": None}),
                        encoding="utf-8")
        got = cr.record_counter_experiment(str(prov), "3", False)
        self.assertFalse(got["counter_experiment"]["detected"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_capture_reference.py -q`
Expected: FAIL，`AttributeError: module 'capture_reference' has no attribute 'tamper'`（无 torch 时 `TamperTest` skip，`CounterExperimentRecordTest` 仍失败）

- [ ] **Step 3: 写实现**

在 `scripts/capture_reference.py` 的 `build_provenance` 之后插入：

```python
def tamper(staged_dir, case_id):
    """把某条用例的真值改掉一个元素，用来验证比对拓扑真有判别力。

    前面全绿说明不了任何事：load 节点没生效、或者待验收算子在跟自己比，
    结果同样是 100% 通过。改坏一条再重跑，那条必须变 Fail。
    """
    import torch

    node_root = os.path.join(staged_dir, "cpu_0")
    target = None
    for save_name in sorted(os.listdir(node_root)):
        probe = os.path.join(node_root, save_name, str(case_id), "output_0.pt")
        if os.path.exists(probe):
            target = probe
            break
    if target is None:
        raise CaptureError(
            f"真值目录里没有用例 {case_id} 的 output_0.pt。\n"
            f"  → 现有用例号 {collect_case_ids(node_root)[:12]}")

    backup = target + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(target, backup)
    data = torch.load(target)
    flat = data.reshape(-1).clone()
    if flat.numel() == 0:
        raise CaptureError(f"用例 {case_id} 的输出是空张量，改不动，换一条。")
    if flat.dtype == torch.bool:
        flat[0] = ~flat[0]
    elif flat.is_floating_point():
        flat[0] = flat[0] + 1.0
    else:
        flat[0] = flat[0] + 1
    torch.save(flat.reshape(data.shape), target)
    return {"case_id": str(case_id), "file": target, "backup": backup}


def restore(staged_dir):
    """把改坏的真值全部还原。"""
    restored = []
    for root, _dirs, files in os.walk(staged_dir):
        for name in files:
            if not name.endswith(".orig"):
                continue
            backup = os.path.join(root, name)
            shutil.move(backup, backup[:-len(".orig")])
            restored.append(backup[:-len(".orig")])
    return restored


def record_counter_experiment(provenance_path, case_id, detected):
    """把反证实验的结论写回取证 JSON。"""
    with open(provenance_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["counter_experiment"] = {"case_id": str(case_id),
                                     "detected": bool(detected)}
    with open(provenance_path, "w", encoding="utf-8") as sink:
        json.dump(payload, sink, ensure_ascii=False, indent=2)
    return payload
```

把 `main()` 里 `args = parser.parse_args()` 之前的参数声明补上三个：

```python
    parser.add_argument("--tamper", help="反证实验：改坏这条用例的真值")
    parser.add_argument("--conclude-tamper",
                        help="反证实验：记录这条用例重跑后的结论并还原真值")
    parser.add_argument("--detected", action="store_true",
                        help="--conclude-tamper 时：重跑后那条确实变 Fail")
```

紧接 `args = parser.parse_args()` 之后插入两个分支：

```python
    if args.tamper:
        try:
            info = tamper(args.staged, args.tamper)
        except CaptureError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"已改坏用例 {info['case_id']} 的真值：{info['file']}")
        print(f"  原件备份在 {info['backup']}")
        print("  现在重跑第三步。那条用例必须变 Fail，其余保持原状态。")
        print(f"  跑完执行：capture_reference.py --conclude-tamper {info['case_id']} "
              "--detected（或不带 --detected）")
        return 0

    if args.conclude_tamper:
        if not os.path.exists(args.output):
            print(f"{args.output} 不存在，先跑一次取证。", file=sys.stderr)
            return 3
        payload = record_counter_experiment(args.output, args.conclude_tamper,
                                            args.detected)
        restored = restore(args.staged)
        print(f"已还原 {len(restored)} 份真值")
        if not args.detected:
            print("反证实验没通过：改坏了真值，那条用例却没变 Fail。\n"
                  "  → 说明 load 节点没生效，或者两轮跑的是同一份实现。\n"
                  "     核对第三步的 --output_path 与目录名，见 "
                  "references/builtin-baseline.md#反证实验。", file=sys.stderr)
            return 2
        print(f"反证实验通过，已记进 {args.output}")
        return 0
```

同时把 `--library-fingerprint`、`--from-run`、`--log`、`--case-json`、`--input-data`
五个参数的 `required=True` 去掉，改成在取证分支里判：

```python
    missing = [flag for flag, value in (
        ("--from-run", args.from_run), ("--log", args.log),
        ("--case-json", args.case_json), ("--input-data", args.input_data),
        ("--library-fingerprint", args.library_fingerprint)) if not value]
    if missing:
        print(f"取证模式需要 {' '.join(missing)}。", file=sys.stderr)
        return 3
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_capture_reference.py -q`
Expected: PASS（无 torch 时 2 skipped）

- [ ] **Step 5: 提交**

```bash
git add scripts/capture_reference.py tests/test_capture_reference.py
git commit -m "feat: 反证实验，证明比对拓扑真有判别力"
```

---

### Task 5: 裁决前的假绿门禁

**Files:**
- Create: `scripts/check_golden_source.py`
- Test: `tests/test_golden_source.py`

**Interfaces:**
- Consumes: `evidence/golden_provenance.json`（Task 3/4）、两份 `内置库指纹` JSON（Task 2）
- Produces: `check_golden_source.problems(provenance, builtin_fp, candidate_fp, case_ids) -> list[str]`；CLI `check_golden_source.py --provenance ... --builtin ... --candidate ... --case-json ...`，退出码 0/2/3

- [ ] **Step 1: 写失败测试**

新建 `tests/test_golden_source.py`：

```python
"""裁决前挡住假绿。

这条路最危险的失败不是精度不达标，是两轮跑了同一份实现——
报告 100% 通过而什么都没验。判据全部从产物推导，不接受口头断言。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import check_golden_source as gs  # noqa: E402


def temp_dir(case):
    """用例结束时自动清理的临时目录。不用 enterContext：真机解释器可能是 3.9。"""
    holder = tempfile.TemporaryDirectory()
    case.addCleanup(holder.cleanup)
    return Path(holder.name)

BUILTIN = {"side": "builtin", "path": "/opp/lib64/libopapi_nn.so", "sha256": "aaa"}
CANDIDATE = {"side": "candidate",
             "path": "/home/x/vendors/customize/op_api/lib/libcust_opapi.so",
             "sha256": "bbb"}


def provenance(**overrides):
    base = {
        "op": "aclnnBernoulli",
        "builtin_library": dict(BUILTIN),
        "case_json": "/w/cases.json",
        "case_json_sha256": "c" * 64,
        "input_data": "frozen_main",
        "staged_dir": "/w/evidence/golden_builtin",
        "cases": ["0", "1"],
        "counter_experiment": {"case_id": "0", "detected": True},
    }
    base.update(overrides)
    return base


class ProblemsTest(unittest.TestCase):
    def test_clean_run_has_no_problem(self):
        self.assertEqual([], gs.problems(provenance(), BUILTIN, CANDIDATE,
                                         ["0", "1"], "c" * 64))

    def test_same_library_on_both_sides_is_refused(self):
        found = gs.problems(provenance(), BUILTIN, dict(BUILTIN, side="candidate"),
                            ["0", "1"], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("同一份", found[0])

    def test_same_sha_with_different_paths_is_refused(self):
        # 软链接、复制过去的同一份库，路径不同但内容相同。
        twin = dict(CANDIDATE, sha256="aaa")
        found = gs.problems(provenance(), BUILTIN, twin, ["0", "1"], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("sha256", found[0])

    def test_builtin_under_a_vendor_directory_is_refused(self):
        bad = dict(BUILTIN, path="/home/x/vendors/customize/op_api/lib/libcust_opapi.so")
        found = gs.problems(provenance(builtin_library=bad), bad, CANDIDATE,
                            ["0", "1"], "c" * 64)
        self.assertTrue(any("vendors" in item for item in found))

    def test_case_json_changed_between_the_two_runs_is_refused(self):
        found = gs.problems(provenance(), BUILTIN, CANDIDATE, ["0", "1"], "d" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("用例 JSON", found[0])

    def test_missing_golden_for_some_cases_is_refused(self):
        found = gs.problems(provenance(), BUILTIN, CANDIDATE, ["0", "1", "2"],
                            "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("['2']", found[0])

    def test_counter_experiment_not_done_is_refused(self):
        found = gs.problems(provenance(counter_experiment=None), BUILTIN,
                            CANDIDATE, ["0", "1"], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("反证实验", found[0])

    def test_counter_experiment_failed_is_refused(self):
        found = gs.problems(
            provenance(counter_experiment={"case_id": "0", "detected": False}),
            BUILTIN, CANDIDATE, ["0", "1"], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("没变 Fail", found[0])

    def test_provenance_library_disagrees_with_the_fingerprint(self):
        found = gs.problems(
            provenance(builtin_library=dict(BUILTIN, sha256="zzz")),
            BUILTIN, CANDIDATE, ["0", "1"], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("取证记的库", found[0])


class CliTest(unittest.TestCase):
    def _write(self, tmp, prov, builtin, candidate, cases):
        (tmp / "prov.json").write_text(json.dumps(prov), encoding="utf-8")
        (tmp / "b.json").write_text(json.dumps(builtin), encoding="utf-8")
        (tmp / "c.json").write_text(json.dumps(candidate), encoding="utf-8")
        (tmp / "cases.json").write_text(json.dumps({"cases": cases}),
                                        encoding="utf-8")

    def _run(self, tmp):
        return subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "check_golden_source.py"),
             "--provenance", str(tmp / "prov.json"),
             "--builtin", str(tmp / "b.json"),
             "--candidate", str(tmp / "c.json"),
             "--case-json", str(tmp / "cases.json")],
            capture_output=True, text=True)

    def test_clean_run_exits_zero(self):
        tmp = temp_dir(self)
        cases = [{"id": 0}, {"id": 1}]
        prov = provenance()
        self._write(tmp, prov, BUILTIN, CANDIDATE, cases)
        prov["case_json_sha256"] = gs.sha256_file(str(tmp / "cases.json"))
        (tmp / "prov.json").write_text(json.dumps(prov), encoding="utf-8")
        proc = self._run(tmp)
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_same_library_exits_two(self):
        tmp = temp_dir(self)
        cases = [{"id": 0}, {"id": 1}]
        prov = provenance()
        self._write(tmp, prov, BUILTIN, dict(BUILTIN, side="candidate"), cases)
        prov["case_json_sha256"] = gs.sha256_file(str(tmp / "cases.json"))
        (tmp / "prov.json").write_text(json.dumps(prov), encoding="utf-8")
        proc = self._run(tmp)
        self.assertEqual(2, proc.returncode)

    def test_missing_provenance_exits_three(self):
        tmp = temp_dir(self)
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "check_golden_source.py"),
             "--provenance", str(tmp / "nope.json"),
             "--builtin", str(tmp / "b.json"),
             "--candidate", str(tmp / "c.json"),
             "--case-json", str(tmp / "cases.json")],
            capture_output=True, text=True)
        self.assertEqual(3, proc.returncode)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_golden_source.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'check_golden_source'`

- [ ] **Step 3: 写实现**

新建 `scripts/check_golden_source.py`：

```python
"""裁决前核对真值确实来自 CANN 内置实现。

输入：内置真值取证、两侧的库指纹、本轮用例 JSON。
输出：核对结论打印到标准输出。
退出码：0 通过；2 不通过；3 无法判定。

挡的是这条路唯一的致命失败：两轮跑了同一份实现，
报告 100% 通过而什么都没验。见 references/builtin-baseline.md。
"""

import argparse
import hashlib
import json
import os
import sys

VENDOR_MARK = os.sep + "vendors" + os.sep


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def problems(provenance, builtin, candidate, case_ids, case_json_sha):
    """全部判据都从产物推导，一条都不接受口头断言。"""
    found = []

    recorded = provenance.get("builtin_library") or {}
    if (recorded.get("path") != builtin.get("path")
            or recorded.get("sha256") != builtin.get("sha256")):
        found.append(
            f"取证记的库是 {recorded.get('path')}（sha256 {recorded.get('sha256')}），"
            f"库指纹记的是 {builtin.get('path')}（sha256 {builtin.get('sha256')}）。\n"
            "  → 两者必须是同一份。中间换过库就重跑内置那一轮。")

    if os.path.abspath(builtin.get("path", "")) == os.path.abspath(
            candidate.get("path", "")):
        found.append(
            f"两轮加载的是同一份库 {builtin.get('path')}。\n"
            "  → 这是自己跟自己比，报告作废。跑测前分别 export "
            "ATK_CUSTOM_OPP_PATH 到两份不同的库。")
    elif builtin.get("sha256") == candidate.get("sha256"):
        found.append(
            f"两轮的库路径不同但 sha256 相同（{builtin.get('sha256')}）。\n"
            "  → 同一份文件的软链接或拷贝，仍然是自己跟自己比。")

    if VENDOR_MARK in os.path.abspath(builtin.get("path", "")) + os.sep:
        found.append(
            f"内置那一侧的库 {builtin.get('path')} 在 vendors 目录下。\n"
            "  → 这是某个 vendor 的库，不是内置。用 resolve_opp_library.py "
            "--side builtin 重新解析。")

    if provenance.get("case_json_sha256") != case_json_sha:
        found.append(
            "两轮的用例 JSON 不是同一份"
            f"（内置那轮 {provenance.get('case_json_sha256')}，"
            f"本轮 {case_json_sha}）。\n"
            "  → 比的不是同一批用例，重跑内置那一轮。")

    missing = [str(cid) for cid in case_ids
               if str(cid) not in set(provenance.get("cases") or [])]
    if missing:
        found.append(
            f"有 {len(missing)} 条用例没有内置真值：{missing[:12]}。\n"
            "  → 内置那一轮没跑完，或者搬运时漏了。重跑并重新取证。")

    counter = provenance.get("counter_experiment")
    if not counter:
        found.append(
            "没做反证实验。\n"
            "  → 全绿既可能是真的一致，也可能是 load 节点没生效。\n"
            "     跑 capture_reference.py --tamper <用例号>，重跑后 "
            "--conclude-tamper 记结论。")
    elif not counter.get("detected"):
        found.append(
            f"反证实验没通过：改坏了用例 {counter.get('case_id')} 的真值，"
            "它却没变 Fail。\n"
            "  → 比对拓扑没生效，这一轮的结论不成立。")

    return found


def main():
    parser = argparse.ArgumentParser(description="核对真值确实来自 CANN 内置实现")
    parser.add_argument("--provenance", default="evidence/golden_provenance.json")
    parser.add_argument("--builtin", default="evidence/opp_library_builtin.json")
    parser.add_argument("--candidate", default="evidence/opp_library_candidate.json")
    parser.add_argument("--case-json", required=True)
    args = parser.parse_args()

    payloads = {}
    for flag, path in (("provenance", args.provenance), ("builtin", args.builtin),
                       ("candidate", args.candidate)):
        if not os.path.exists(path):
            print(f"{path} 不存在，无法判定。\n"
                  "  → 这条路的产物由 resolve_opp_library.py 与 "
                  "capture_reference.py 产出，见 references/builtin-baseline.md。",
                  file=sys.stderr)
            return 3
        with open(path, encoding="utf-8") as handle:
            payloads[flag] = json.load(handle)

    if not os.path.exists(args.case_json):
        print(f"{args.case_json} 不存在。", file=sys.stderr)
        return 3
    with open(args.case_json, encoding="utf-8") as handle:
        data = json.load(handle)
    cases = data.get("cases", data) if isinstance(data, dict) else data
    case_ids = [case.get("id") for case in cases]

    found = problems(payloads["provenance"], payloads["builtin"],
                     payloads["candidate"], case_ids,
                     sha256_file(args.case_json))
    if found:
        print("", file=sys.stderr)
        for item in found:
            print(f"✗ {item}", file=sys.stderr)
        print("\n真值来历不成立，不要裁决。", file=sys.stderr)
        return 2

    print(f"真值来自内置库 {payloads['builtin']['path']}")
    print(f"  待验收算子库 {payloads['candidate']['path']}")
    print(f"  {len(case_ids)} 条用例的真值齐备，反证实验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_golden_source.py -q`
Expected: PASS，12 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/check_golden_source.py tests/test_golden_source.py
git commit -m "feat: 裁决前核对真值确实来自内置实现"
```

---

### Task 6: 比较器判据按比对双方分叉，补随机生成类

**Files:**
- Modify: `scripts/_coverage_strategy.py:188-207`（`OPERATOR_CLASSES`）、`:433-445`（`comparator_failures`）、`:718-725`（`equal` + 浮点那条）
- Modify: `scripts/make_must_cover.py:186-196`（新增 `--interface`）、`:245-252`（写 `baseline_kind`）
- Modify: `references/case-design.md`（算子类别表）、`references/experimental_standard.md`（选哪个比较器）
- Test: `tests/test_coverage_strategy.py`、`tests/test_reference_facts.py`

**Interfaces:**
- Consumes: `evidence/interface.json` 的 `baseline_kind`
- Produces: `must_cover.json` 新增顶层键 `baseline_kind`；`_coverage_strategy.expected_comparator(must_cover, profile) -> str`

- [ ] **Step 1: 写失败测试**

在 `tests/test_coverage_strategy.py` 末尾追加：

```python
class BuiltinComparatorTest(unittest.TestCase):
    """两侧都在 NPU 上跑同一个 aclnn 接口时，浮点的位级相等成立。

    「浮点位级相等在 NPU 上不成立」那条判据说的是跨后端比框架基线，
    这里两侧都是 aclnn，任何一位不同都说明改动改变了输出。
    """

    def test_cann_builtin_expects_equal_whatever_the_class_says(self):
        must_cover = {"operator_class": "elementwise", "comparator": "equal",
                      "baseline_kind": "cann_builtin"}
        profile = _coverage_strategy.class_profile("elementwise", None)
        self.assertEqual("equal",
                         _coverage_strategy.expected_comparator(must_cover, profile))
        self.assertEqual([], _coverage_strategy.comparator_failures(must_cover, profile))

    def test_cann_builtin_refuses_mixed_tolerance(self):
        must_cover = {"operator_class": "elementwise",
                      "comparator": "mixed_tolerance_bm",
                      "baseline_kind": "cann_builtin"}
        profile = _coverage_strategy.class_profile("elementwise", None)
        found = _coverage_strategy.comparator_failures(must_cover, profile)
        self.assertEqual(1, len(found))
        self.assertIn("回归比对", found[0])

    def test_torch_baseline_keeps_the_class_comparator(self):
        must_cover = {"operator_class": "elementwise", "comparator": "equal",
                      "baseline_kind": "torch"}
        profile = _coverage_strategy.class_profile("elementwise", None)
        self.assertEqual(1,
                         len(_coverage_strategy.comparator_failures(must_cover, profile)))

    def test_generation_class_exists_and_uses_equal(self):
        profile = _coverage_strategy.class_profile("generation", None)
        self.assertEqual("equal", profile["comparator"])
        self.assertFalse(profile["arithmetic"])
        self.assertIn("dtype", profile["axes"])
```

在 `tests/test_coverage_strategy.py` 里再加一条守 `equal` + 浮点分叉的：

```python
class FloatEqualWaiverTest(unittest.TestCase):
    def _report(self, baseline_kind):
        dims = {"dtype": ["fp16", "fp32"], "size_class": ["small", "large"]}
        combos = [{"dtype": d, "size_class": s, "coverage_tags": []}
                  for d in dims["dtype"] for s in dims["size_class"]]
        must_cover = {"dims": dims, "combos": combos, "comparator": "equal",
                      "operator_class": "elementwise",
                      "dtype_binding": {"source": "README.md",
                                        "declared": ["fp16", "fp32"]},
                      "baseline_kind": baseline_kind}
        failures, _report = _coverage_strategy.audit_coverage(must_cover)
        return failures

    def test_torch_baseline_still_refuses_equal_with_floats(self):
        self.assertTrue(any("位级相等" in item for item in self._report("torch")))

    def test_cann_builtin_waives_it(self):
        self.assertFalse(any("位级相等" in item for item in self._report("cann_builtin")))
```

`audit_coverage(must_cover)` 返回 `(failures, report)` 两元组（`scripts/_coverage_strategy.py:588,786`），上面只取第一个。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_coverage_strategy.py -q`
Expected: FAIL，`AttributeError: module '_coverage_strategy' has no attribute 'expected_comparator'`

- [ ] **Step 3: 写实现**

在 `scripts/_coverage_strategy.py` 的 `OPERATOR_CLASSES` 里追加一类：

```python
    "generation": {
        "arithmetic": False,
        "comparator": "equal",
        "axes": ("dtype", "size_class", "shape_form"),
        "group": ("dtype", "size_class", "shape_form"),
    },
```

在 `comparator_failures` 之前插入：

```python
def expected_comparator(must_cover, profile):
    """这份用例集该用哪个比较器。

    常规验收由算子类别定死。真值来自 CANN 内置实现时不一样：两侧都在 NPU 上
    跑同一个 aclnn 接口，任何一位不同都说明改动改变了输出，只能是 equal。
    「浮点的位级相等在 NPU 上不成立」那条讲的是跨后端比框架基线。
    """
    if must_cover.get("baseline_kind") == "cann_builtin":
        return "equal"
    return profile.get("comparator")
```

把 `comparator_failures` 里 `expected = profile.get("comparator")` 改成
`expected = expected_comparator(must_cover, profile)`，并在 `hint` 的分支里
把 `cann_builtin` 单独说清：

```python
    if must_cover.get("baseline_kind") == "cann_builtin":
        hint = ("    真值来自 CANN 内置实现，这是回归比对：两侧都在 NPU 上跑同一个\n"
                "    aclnn 接口，任何一位不同都说明改动改变了输出，只能用 equal。\n"
                "    见 references/builtin-baseline.md#比较器。")
        return [f"真值来自内置实现时比较器只能是 equal，声明的却是 {declared}。\n" + hint]
```

把 `equal` + 浮点那条判据加上前置条件（`scripts/_coverage_strategy.py:720`）：

```python
                if (comparator == "equal" and axis_floats
                        and must_cover.get("baseline_kind") != "cann_builtin"):
```

在 `scripts/make_must_cover.py` 的参数里加：

```python
    parser.add_argument("--interface", default="evidence/interface.json",
                        help="derive_interface.py 的产物；读 baseline_kind")
```

在写 `must_cover` 字典之后、`must_cover["combos"] = ...` 之前插入：

```python
    if os.path.exists(args.interface):
        with open(args.interface, encoding="utf-8") as handle:
            must_cover["baseline_kind"] = json.load(handle).get(
                "baseline_kind", "torch")
    else:
        must_cover["baseline_kind"] = "torch"
```

（`make_must_cover.py` 顶部已 `import json`；确认 `import os` 在场，不在就补。）

在 `references/case-design.md` 的算子类别表里追加一行：

```markdown
| generation | 随机生成类：输出不由输入算出，由随机数流决定 | equal | dtype、size_class、shape_form |
```

在 `references/experimental_standard.md` 的「选哪个比较器」判定表末尾追加一行：

```markdown
| 真值来自 CANN 内置实现（`baseline_kind` 为 `cann_builtin`） | `equal`，浮点也一样 | 是 |
```

并在该表下方补一句：

```markdown
最后一行是唯一一处浮点也用 `equal` 的场景：两侧都在 NPU 上跑同一个 aclnn 接口，
比的是「改动有没有改变输出」，不是「算得对不对」。见 builtin-baseline.md#比较器。
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python3 -m pytest tests/test_coverage_strategy.py tests/test_reference_facts.py \
                  tests/test_make_must_cover.py tests/test_document_style.py -q
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/_coverage_strategy.py scripts/make_must_cover.py \
        references/case-design.md references/experimental_standard.md \
        tests/test_coverage_strategy.py
git commit -m "feat: 比较器判据按比对双方分叉，补随机生成类"
```

---

### Task 7: 随机策略收敛成受控取值，种子参数名落盘

**Files:**
- Modify: `scripts/derive_interface.py`
- Modify: `scripts/validate_cases.py`（C7 消费 interface.json 里的种子参数名）
- Modify: `references/random-operator-signals.json`、`references/intake.md`
- Test: `tests/test_derive_interface.py`、`tests/test_builtin_baseline.py`

**Interfaces:**
- Consumes: Task 6 的 `baseline_kind` 语义
- Produces: `interface.json` 新增 `random_strategy`（受控取值）与 `seed_parameters`（列表）；`validate_cases.check_seed_is_pinned(cases, failures, notes, extra_names=())`

- [ ] **Step 1: 写失败测试**

在 `tests/test_derive_interface.py` 末尾追加：

```python
class RandomStrategyVocabularyTest(unittest.TestCase):
    """自由文本的策略描述判不了真假，收敛成受控取值。"""

    def test_known_strategies_are_accepted(self):
        for name in ("equal_vs_builtin_pinned_seed", "deterministic_boundary_only",
                     "distribution_test", "self_consistency"):
            with self.subTest(strategy=name):
                self.assertIsNone(derive_interface.check_random_strategy(
                    name, "用户 2026-08-17 指定", ["seed", "offset"]))

    def test_unknown_strategy_is_refused_with_the_menu(self):
        warn = derive_interface.check_random_strategy(
            "合理的固定种子策略", "任务书 §2", ["seed"])
        self.assertIn("equal_vs_builtin_pinned_seed", warn)

    def test_pinned_seed_without_seed_parameters_is_refused(self):
        warn = derive_interface.check_random_strategy(
            "equal_vs_builtin_pinned_seed", "用户 2026-08-17 指定", [])
        self.assertIn("种子", warn)

    def test_other_strategies_do_not_need_seed_parameters(self):
        self.assertIsNone(derive_interface.check_random_strategy(
            "distribution_test", "任务书 §2", []))
```

在 `tests/test_builtin_baseline.py` 的 `SeedPinningTest` 里追加：

```python
    def test_extra_names_from_the_interface_are_checked_too(self):
        # 词表判不出的种子参数名（如 philoxState），由 interface.json 补名单。
        cases = [self._case(0, 1, name="philoxState"),
                 self._case(1, 2, name="philoxState")]
        failures, _ = self._run_with_extra(cases, ["philoxState"])
        self.assertEqual(1, len(failures))
        self.assertIn("philoxState", failures[0])
```

并在该类里加一个辅助：

```python
    def _run_with_extra(self, cases, extra):
        failures, notes = [], []
        validate_cases.check_seed_is_pinned(cases, failures, notes,
                                            extra_names=extra)
        return failures, notes
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_derive_interface.py::RandomStrategyVocabularyTest \
                  tests/test_builtin_baseline.py -q
```
Expected: FAIL，`AttributeError: ... has no attribute 'check_random_strategy'`

- [ ] **Step 3: 写实现**

在 `scripts/derive_interface.py` 的 `BASELINE_KINDS` 附近加：

```python
# 随机数生成类算子的精度判据。自由文本判不了真假，收敛成受控取值。
# 每一档后面那句是它的成立前提，前提不成立就不许选这一档。
RANDOM_STRATEGIES = {
    "equal_vs_builtin_pinned_seed":
        "与 CANN 内置实现逐位比对。前提：种子是显式入参且在用例数据里钉死",
    "deterministic_boundary_only":
        "只测确定性边界值（如概率取 0 或 1）。前提：任务书认这样的覆盖够用",
    "distribution_test":
        "统计分布检验。前提：判定公式与阈值已经写明",
    "self_consistency":
        "同一台 device 同种子两次跑测自洽。前提：种子是显式入参",
}
SEED_REQUIRED_STRATEGIES = ("equal_vs_builtin_pinned_seed", "self_consistency")


def check_random_strategy(strategy, source, seed_parameters):
    """随机判据必须是受控取值，且它的成立前提要真的成立。"""
    if strategy not in RANDOM_STRATEGIES:
        menu = "\n".join(f"    {name}：{why}"
                         for name, why in RANDOM_STRATEGIES.items())
        return (f"随机判据 {strategy!r} 不是受控取值。可选：\n{menu}\n"
                "  → 「合理的固定种子策略」这类转述判不了真假，选一个具体取值。")
    if not source:
        return f"随机判据 {strategy!r} 没写依据（谁要求的、写在哪）。"
    if strategy in SEED_REQUIRED_STRATEGIES and not seed_parameters:
        return (f"{strategy} 要求种子是显式入参，但 --seed-parameters 是空的。\n"
                "  → 读待验收算子工程目录里的头文件，把种子参数名列出来；\n"
                "     接口里根本没有种子参数时改选 distribution_test 或 "
                "deterministic_boundary_only。")
    return None
```

把 CLI 的 `--random-strategy` 改成 `choices=tuple(RANDOM_STRATEGIES)`，新增：

```python
    parser.add_argument("--seed-parameters",
                        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                        default=[],
                        help="接口声明里的种子参数名，逗号分隔；"
                             "读工程目录里的头文件得到")
```

在 `derive()` 里，随机信号命中的那段之后调用 `check_random_strategy`，
并把两个新字段写进 payload：

```python
        "random_strategy": random_strategy,
        "seed_parameters": seed_parameters,
```

在 `scripts/validate_cases.py` 里把 `check_seed_is_pinned` 与 `seed_parameter_names`
补上 `extra_names`：

```python
def seed_parameter_names(cases, extra_names=()):
    """用例里出现的种子类参数名。

    词表判不出的名字（如 philoxState）由 interface.json 的 seed_parameters 补进来。
    """
    names = set(extra_names)
    for case in iter_cases(cases):
        specs = list(iter_input_specs(case))
        primary = {spec.get("name") for spec in specs
                   if _name_tokens(spec.get("name")) & set(SEED_WORDS)}
        names |= {n for n in primary if n}
        if not primary:
            continue
        for spec in specs:
            if _name_tokens(spec.get("name")) & set(SEED_COMPANION_WORDS):
                names.add(spec.get("name"))
    return {n for n in names if n}
```

只改两处：函数签名多一个 `extra_names=()`，函数体第一行由 `names = set()`
变成 `names = set(extra_names)`，其余原样。

`check_seed_is_pinned` 也只改两行：

```python
def check_seed_is_pinned(cases, failures, notes, extra_names=()):
    ...
    all_cases = list(iter_cases(cases))
    names = seed_parameter_names(all_cases, extra_names)
```

签名多一个 `extra_names=()`，调用 `seed_parameter_names` 时把它传下去，
函数体其余部分（区间判定、多取值判定、notes）一行不动。

在 `main()` 里加 `--interface` 并把名单传进去：

```python
    parser.add_argument("--interface", default="evidence/interface.json",
                        help="derive_interface.py 的产物；读 seed_parameters")
```

```python
    extra_seed_names = []
    if os.path.exists(args.interface):
        with open(args.interface, encoding="utf-8") as handle:
            extra_seed_names = json.load(handle).get("seed_parameters") or []
    check_seed_is_pinned(all_cases, failures, notes, extra_names=extra_seed_names)
```

在 `references/random-operator-signals.json` 里把 `ask_template` 改写成
列这四个受控取值（保留原有说明文字，只把「例如」后面那串换成四个取值名与前提），
并新增一个键：

```json
"strategies": {
  "equal_vs_builtin_pinned_seed": "与 CANN 内置实现逐位比对。前提：种子是显式入参且在用例数据里钉死",
  "deterministic_boundary_only": "只测确定性边界值（如概率取 0 或 1）。前提：任务书认这样的覆盖够用",
  "distribution_test": "统计分布检验。前提：判定公式与阈值已经写明",
  "self_consistency": "同一台 device 同种子两次跑测自洽。前提：种子是显式入参"
}
```

在 `references/intake.md` 的随机算子那一节，把「要具体到判据本身」改写成
「从 `random-operator-signals.json` 的 `strategies` 里选一个取值，并给出依据；
选 `equal_vs_builtin_pinned_seed` 或 `self_consistency` 时还要用 `--seed-parameters`
列出接口声明里的种子参数名」。

- [ ] **Step 4: 跑测试确认通过**

```bash
python3 -m pytest tests/test_derive_interface.py tests/test_builtin_baseline.py \
                  tests/test_reference_facts.py -q
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/derive_interface.py scripts/validate_cases.py \
        references/random-operator-signals.json references/intake.md \
        tests/test_derive_interface.py tests/test_builtin_baseline.py
git commit -m "feat: 随机判据收敛成受控取值，种子参数名落盘并进 C7"
```

---

### Task 8: 冻结与裁决认这条路

**Files:**
- Modify: `scripts/freeze_inputs.py`（`freeze_golden` 入口）
- Modify: `scripts/verdict.py`
- Modify: `references/reporting.md`
- Test: `tests/test_freeze_diagnosis.py`、`tests/test_verdict.py`

**Interfaces:**
- Consumes: `interface.json` 的 `baseline_kind`、`evidence/golden_provenance.json`
- Produces: `verdict.json` 新增键 `conclusion_kind`，取值 `accuracy_vs_framework` 或 `regression_vs_builtin`

- [ ] **Step 1: 写失败测试**

在 `tests/test_freeze_diagnosis.py` 末尾追加：

```python
class BuiltinGoldenIsNotFrozenHereTest(unittest.TestCase):
    """真值来自内置时不走 freeze_inputs --golden，走 capture_reference.py。

    两条路的产物形状不同，混用会冻出一份没人消费的摘要，
    然后 agent 拿它当真值来历去写报告。
    """

    def test_cann_builtin_refuses_and_points_at_the_right_script(self):
        problem = freeze_inputs.golden_path_conflict("cann_builtin")
        self.assertIn("capture_reference.py", problem)

    def test_torch_baseline_is_unaffected(self):
        self.assertIsNone(freeze_inputs.golden_path_conflict("torch"))
```

在 `tests/test_verdict.py` 末尾追加：

```python
class ConclusionKindTest(unittest.TestCase):
    def test_torch_baseline_is_accuracy(self):
        self.assertEqual("accuracy_vs_framework",
                         verdict.conclusion_kind({"baseline_kind": "torch"}))

    def test_builtin_baseline_is_regression(self):
        self.assertEqual("regression_vs_builtin",
                         verdict.conclusion_kind({"baseline_kind": "cann_builtin"}))

    def test_builtin_without_provenance_blocks_the_verdict(self):
        found = verdict.builtin_evidence_problems(
            {"baseline_kind": "cann_builtin"}, provenance=None)
        self.assertEqual(1, len(found))
        self.assertIn("golden_provenance.json", found[0])

    def test_builtin_with_failed_counter_experiment_blocks_the_verdict(self):
        found = verdict.builtin_evidence_problems(
            {"baseline_kind": "cann_builtin"},
            provenance={"counter_experiment": {"case_id": "0", "detected": False}})
        self.assertEqual(1, len(found))
        self.assertIn("反证实验", found[0])

    def test_builtin_with_full_evidence_passes(self):
        self.assertEqual([], verdict.builtin_evidence_problems(
            {"baseline_kind": "cann_builtin"},
            provenance={"counter_experiment": {"case_id": "0", "detected": True},
                        "builtin_library": {"path": "/opp/lib64/a.so",
                                            "sha256": "x"}}))
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_freeze_diagnosis.py tests/test_verdict.py -q
```
Expected: FAIL，`AttributeError: ... has no attribute 'golden_path_conflict'`

- [ ] **Step 3: 写实现**

在 `scripts/freeze_inputs.py` 里加：

```python
def golden_path_conflict(baseline_kind):
    """真值来自内置实现时，golden 不在这里冻。"""
    if baseline_kind != "cann_builtin":
        return None
    return ("baseline_kind 是 cann_builtin，真值不由这一步产生。\n"
            "  → 走 capture_reference.py：内置那一轮跑完之后取证并搬运，\n"
            "     见 references/builtin-baseline.md#两步跑测。")
```

在 `main()` 的 `if args.golden:` 分支最前面插入（`args.env` 已是必填，
`env.json` 里没有 `baseline_kind` 时读 `evidence/interface.json`）：

```python
    if args.golden:
        interface_path = os.path.join(os.path.dirname(args.env), "interface.json")
        baseline_kind = "torch"
        if os.path.exists(interface_path):
            with open(interface_path, encoding="utf-8") as handle:
                baseline_kind = json.load(handle).get("baseline_kind", "torch")
        conflict = golden_path_conflict(baseline_kind)
        if conflict:
            fail(conflict, 2)
```

在 `scripts/verdict.py` 里加：

```python
def conclusion_kind(interface):
    """这轮能下哪一种结论。

    真值来自内置实现时只能说「与内置逐位一致」，不能说「精度达标」——
    内置本身没有被这轮验收检验过，它只是对照物。
    """
    if interface.get("baseline_kind") == "cann_builtin":
        return "regression_vs_builtin"
    return "accuracy_vs_framework"


def builtin_evidence_problems(interface, provenance):
    """真值来自内置时，取证不齐不许裁决。"""
    if interface.get("baseline_kind") != "cann_builtin":
        return []
    if not provenance:
        return ["[BUILTIN_GOLDEN] 缺 evidence/golden_provenance.json，"
                "真值来历不可核验，不裁决。\n"
                "  → 跑 capture_reference.py，见 references/builtin-baseline.md。"]
    counter = provenance.get("counter_experiment")
    if not counter or not counter.get("detected"):
        return ["[BUILTIN_GOLDEN] 反证实验没做或没通过，比对拓扑未经验证，不裁决。\n"
                "  → capture_reference.py --tamper，见 "
                "references/builtin-baseline.md#反证实验。"]
    return []
```

在 `verdict.py` 组装结论的位置调用这两个函数：把 `conclusion_kind(interface)`
写进输出 JSON 的 `conclusion_kind` 键，把 `builtin_evidence_problems(...)` 的结果
并进已有的阻塞项列表（与 `[BASELINE_BACKEND]` 那条同一个列表）。
`provenance` 从 `evidence/golden_provenance.json` 读，不存在传 `None`。

在 `references/reporting.md` 里新增一节：

```markdown
## 真值来自 CANN 内置实现时

`verdict.json` 的 `conclusion_kind` 是 `regression_vs_builtin` 时，
结论只能写「待验收算子的输出与 CANN 内置实现逐位一致（或第 N 条不一致）」。

不能写「精度达标」：内置实现本身没有被这轮验收检验过，它只是对照物。

报告必须引用三样东西，缺一项结论不成立：两侧算子库的完整路径与 SHA256、
两轮的用例 JSON 与冻结输入的 SHA256、反证实验的结论。

它们都在 `evidence/golden_provenance.json` 与两份
`evidence/opp_library_*.json` 里，不要另写一遍。
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python3 -m pytest tests/test_freeze_diagnosis.py tests/test_verdict.py \
                  tests/test_document_style.py -q
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/freeze_inputs.py scripts/verdict.py references/reporting.md \
        tests/test_freeze_diagnosis.py tests/test_verdict.py
git commit -m "feat: 冻结与裁决认内置真值这条路，结论分级"
```

---

### Task 9: 作战卡与全量验收

**Files:**
- Modify: `references/builtin-baseline.md`（补脚本调用）
- Modify: `SKILL.md`（S3 作战卡）
- Modify: `CLAUDE.md`（架构演进与测试数）
- Test: 全量

**Interfaces:**
- Consumes: 前八个任务的全部脚本名与参数
- Produces: 无新接口

- [ ] **Step 1: 写失败测试**

在 `tests/test_builtin_baseline.py` 的 `BuiltinBaselineKnowledgeTest` 里追加：

```python
    def test_every_script_on_this_path_is_named_in_the_doc(self):
        # 文档只讲原理不给命令时，agent 会自己拼参数，真机上拼错三次以上。
        for script in ("resolve_opp_library.py", "capture_reference.py",
                       "check_golden_source.py"):
            self.assertIn(script, self.text, f"{script} 没写进文档")

    def test_the_two_runs_export_the_pinned_library(self):
        self.assertIn("export ATK_CUSTOM_OPP_PATH", self.text)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_builtin_baseline.py -q`
Expected: FAIL，`AssertionError: resolve_opp_library.py 没写进文档`

- [ ] **Step 3: 补文档**

在 `references/builtin-baseline.md` 的「两步跑测」之前插入一节：

```markdown
## 这条路要跑的脚本

按顺序，四个：

```bash
# 1. 解析两侧的算子库，各自钉死
<python> scripts/resolve_opp_library.py --op <aclnn 名> --side builtin \
  -o evidence/opp_library_builtin.json
<python> scripts/resolve_opp_library.py --op <aclnn 名> --side candidate \
  --library <本轮 vendor 的 .so> -o evidence/opp_library_candidate.json

# 2. 内置那一轮跑完之后取证并搬运
<python> scripts/capture_reference.py --op <aclnn 名> \
  --from-run <atk_output/<任务>/output> --log evidence/builtin_run.log \
  --case-json <case-json> --input-data <frozen> \
  --library-fingerprint evidence/opp_library_builtin.json \
  --staged evidence/golden_builtin -o evidence/golden_provenance.json

# 3. 反证实验：改坏一条，重跑第三步，记结论
<python> scripts/capture_reference.py --tamper <用例号> \
  --staged evidence/golden_builtin -o evidence/golden_provenance.json
<python> scripts/capture_reference.py --conclude-tamper <用例号> --detected \
  --staged evidence/golden_builtin -o evidence/golden_provenance.json

# 4. 裁决前核对（--candidate-log 是第二轮的跑测日志，必填）
<python> scripts/check_golden_source.py --case-json <case-json> \
  --candidate-log evidence/accuracy.log
```

每一步的 `export ATK_CUSTOM_OPP_PATH=<对应那一侧的 path>` 由 
`resolve_opp_library.py` 打印出来，照抄即可。
```

把「两步跑测」里两段跑测命令的 `export ATK_CUSTOM_OPP_PATH=<内置 .so 的完整路径>`
改成 `export ATK_CUSTOM_OPP_PATH=$(python3 -c "import json;print(json.load(open('evidence/opp_library_builtin.json'))['path'])")`。

把 `SKILL.md` 的 S3 作战卡整段换成骨架渲染出来的版本：

```bash
python3 scripts/render_views.py --cards
```

从输出里取 `### S3 编译安装部署` 到下一个 `###` 之前的整段，**逐字**替换
`SKILL.md` 里的同名小节。`tests/test_contracts.py::CardCoverageTest` 判的是
逐字相等，手写一行改一行必定对不上。

替换后 `SKILL.md` 会长 9 行左右，仍在 ≤360 行的预算内；超了就报 DONE_WITH_CONCERNS，
不要自己删别处的正文腾地方。

在 `CLAUDE.md` §8 的演进清单里把「2026-08-17 内置实现当真值」那条补完：

```markdown
- **2026-08-17 内置实现当真值:** `references/builtin-baseline.md` 落盘全部规范 + 两侧算子库自行解析并钉死 + 参考跑取证与搬运 + 反证实验 + 裁决前假绿门禁 + 种子必须钉死（C7），+N 测试
```

（`N` 填实际新增测试数，跑完全量后按 `passed` 的增量填。）

- [ ] **Step 4: 跑全量并核对**

```bash
python3 scripts/render_views.py --check
python3 -m pytest tests/ -q 2>&1 | tail -5
```
Expected: `render_views.py --check` 打印「派生视图与骨架一致」；pytest 的失败数不超过 20 且失败文件全部是 `tests/test_make_yaml.py` 与 `tests/test_assets_example.py`（本机无 torch 的既有状态）。

把 `CLAUDE.md` 的「当前状态」行改成实际的 `passed` 数。

- [ ] **Step 5: 提交**

```bash
git add references/builtin-baseline.md SKILL.md CLAUDE.md tests/test_builtin_baseline.py
git commit -m "docs: 内置真值这条路的脚本调用与作战卡"
```

---

## 真机验收（本计划之外，需要 1.2 真机）

九个任务跑完之后，按 `docs/development/2026-08-17-builtin-baseline-dryrun.md`
在真机上跑一遍伯努利。要带回来的五项在那份文件里，其中前两项会反过来改文档：

1. 日志里 `import ... success!` 那一行的实际格式——`loaded_library()` 的正则按它校准
2. 最终生效的节点拓扑与目录名——`stage_golden()` 的 `node_dir` / `as_name` 默认值按它校准

真机跑通前不要宣称这条路可用；跑通后把两项校准结果写进 `references/builtin-baseline.md`
并补对应的防漂移测试。
