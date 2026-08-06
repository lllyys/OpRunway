"""测试夹具：给**已删掉历史沿用 `case_target`** 的样例/夹具 spec 补一个测试自己的用例预算。

为什么存在
----------
`precision.case_target` 于 2026-08-06 删掉缺省值（原 `gen_cases._DEFAULT_CASE_TARGET = 50`），
读取点 `gen_cases._require_case_target` 改成**缺席即 fail-fast**，目的是让「没人定过这个算子该造
多少条用例」显性化。删缺省的当天，为了不让既有样例 spec 立刻炸，曾把 `case_target: 50` 硬填回
`samples/specs/{sign,neg,equal,isclose,im2col}.spec.json`、`test_fixtures/*.spec.json`、
`testdata/gpu_demo.spec.json`。用户判定：**那等于把问题重新藏起来，抵消了删缺省的意义**——
那些 50 是缺省值的化石，不是按覆盖矩阵算出来的依据。于是它们被删干净，各 spec 只留
`_case_target_note` 墓碑说明为什么不许回填。

后果是那些 spec 对 `gen_cases` **不可跑**。这是有意的、正确的状态：用例数该多少，等推算规则
定出来再由人写进 spec 并附 `case_target_source`。

可测试仍然要跑。那个数属于**测试**，不属于 spec —— 这个模块就是它唯一的落点。

`FIXTURE_CASE_TARGET` 为什么是 50
----------------------------------
⚠ **它不是建议值、不是默认值、不是任何算子的用例数依据。** 取 50 只有一个理由：
`test_gen_cases_dtype_attr.ExistingOpsByteIdenticalTest` 的 sha256 基线（`_U3_CASESET_BASELINES`，
每个 numpy 随机流各一份）是在预算 50 下实测取的。那道 pin 守的是
**「gen_cases 的逻辑改动不得改变现有算子的 caseset 字节」**，与「用例数该定多少」是两个问题；
换个数就要把两组基线全部重取，pin 会白白失去跨版本可比性。

所以这里的 50 是**基线参数**，不是**规范建议**。判据很简单：spec 里的 50 会被下一个人当成
「这个算子就该造 50 条」；测试里的 50 只会被当成「取基线时用的那个预算」。

⚠ 别把这个常量搬回 `gen_cases`、也别搬回任何 tracked `*.spec.json`：那等于把删掉的缺省换个入口
加回来。`test_gen_cases_dtype_attr.CaseTargetHasNoDefaultTest.test_module_exposes_no_default_constant`
会红。

用法
----
    import _spec_fixture as SF

    spec = SF.load(path)                       # 就地补预算，返回 dict
    spec = SF.load(path, case_target=3)        # 本用例自己要小预算时显式覆盖
    p = SF.materialize(path, tmpdir)           # 要把**文件路径**交给 CLI/subprocess 时

`load` / `materialize` **只在 spec 未声明 `case_target` 时**才注入：`median.spec.json`（1344 =
torch_parity 完整矩阵，机器可复算）、`gaussian_blur.spec.json`（169 = 任务书用例条数）这类
**有真实依据**的值原样保留，不被夹具值盖掉。判据是字段在不在，**不看算子名**（律令 #0）。
"""
import copy
import json
import os

#: 夹具用例预算。见模块 docstring —— **不是**建议值，改它要重取 `_U3_CASESET_BASELINES` 全部基线。
FIXTURE_CASE_TARGET = 50


def inject(spec, case_target=FIXTURE_CASE_TARGET):
    """在 `spec`（dict，原地改）缺 `precision.case_target` 时补上夹具预算；已声明则原样不动。

    返回同一个 dict，方便链式写法。`precision` 整节缺失时一并建出来——那种 spec 本来也跑不了，
    但夹具不该在这里以 KeyError 的形式炸，真正的报错该由 gen_cases 的契约校验给出。
    """
    precision = spec.setdefault("precision", {})
    if "case_target" not in precision:
        precision["case_target"] = case_target
    return spec


def load(path, case_target=FIXTURE_CASE_TARGET):
    """读一份 spec 文件 → dict，并按 `inject` 的规则补夹具预算。"""
    with open(path, encoding="utf-8") as fh:
        return inject(json.load(fh), case_target)


def materialize(path, dest, name=None, case_target=FIXTURE_CASE_TARGET, **overrides):
    """把补好预算的 spec 写成一份**新文件**并返回其路径 —— 给要吃 spec 路径的 CLI / subprocess 用。

    `dest` 是目标目录（须已存在）；`name` 缺省沿用源文件名。`overrides` 是顶层键的覆盖
    （例如 `runner_form="cpp"`），方便原本就要改一两个字段再落盘的用例合并成一步。

    ⚠ 刻意**不**回写源文件：tracked spec 必须保持「不可跑」，那是这轮改动要立住的事实。
    """
    spec = load(path, case_target)
    spec.update(copy.deepcopy(overrides))
    out = os.path.join(dest, name or os.path.basename(path))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False)
    return out
