#!/usr/bin/env python3
"""run_workflow 的 runner_form→mode 路由门 + `runner_form` 缺省的单一真源；不访问 NPU。"""

import ast
import contextlib
import copy
import inspect
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from unittest import mock

import cpp_extension_adapter
import cpp_extension_codegen
import gen_cases
import repo_adapter
import run_workflow as W
import _spec_fixture as SF

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 真样例 spec（**显式**声明 `runner_form: "cpp"`）；单测里 pop 掉该键来造「省略」的场景。
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")
#: 只读 golden 样例根；夹具从这里拷贝进临时 ops root（不 mock 加载函数，理由见 `_ops_root`）。
_SAMPLE_GOLDEN_ROOT = os.path.join(_HERE, "..", "samples", "golden")


def _sign_spec():
    """读样例 spec，并补上**测试侧**用例预算（`_spec_fixture`，仅当 spec 未声明时）。

    ⚠ `sign.spec.json` 已于 2026-08-06 删掉历史沿用的 `case_target: 50`（缺省值的化石、无覆盖
    矩阵依据）；本文件测的是 `runner_form → --mode` 派生，与用例数无关，预算只是让它跑得起来。
    """
    return SF.load(_SIGN_SPEC)


@contextlib.contextmanager
def _ops_root(*ops_with_golden):
    """把 `OPRUNWAY_OPS_DIR` 指到一块临时 ops root；点名的算子从只读样例拷一份 golden.py 进去。

    ⚠ 刻意**不 mock `gen_cases.load_golden`**（2026-08-05 审修门 Medium#7）：那是 gen_cases 自己的
    纯函数，替掉它等于把「真实的 `<ops_root>/<op>/golden.py` 查找 + 缺失分支」整条路径换成夹具的
    返回值——账本里 `golden_dependency` / `golden_cost_note` 两栏就再没被真跑过。
    不点名任何算子 = 一块空 ops root，真实加载逻辑会自然走到「缺 golden:」那一支。
    """
    with tempfile.TemporaryDirectory() as root:
        for op in ops_with_golden:
            os.makedirs(os.path.join(root, op))
            shutil.copyfile(
                os.path.join(_SAMPLE_GOLDEN_ROOT, op, "golden.py"),
                os.path.join(root, op, "golden.py"))
        with mock.patch.dict(os.environ, {"OPRUNWAY_OPS_DIR": root}):
            yield root


def _empty_ops_root():
    """一块**没有任何 golden.py** 的临时 ops root。"""
    return _ops_root()


def _witness_spec():
    """最小**合法**的 cpp_extension spec，且刻意**省略 `runner_form` 键**。

    用途见 `test_cpp_extension_entry_guard_no_longer_trips_on_the_omitted_key`：要证明门放行，
    只能靠**成功产出**，所以这份 spec 除了缺那个键之外必须处处合法。算子名是中立见证。
    """
    return {
        "op": "Witness",
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32"]},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": 0},
            {"name": "valuesOut", "io": "out", "dtype": ["<from_input>"]},
            {"name": "indicesOut", "io": "out", "dtype": ["int32"]},
        ],
        "call_variants": [
            {"symbol": "Witness", "active_attrs": [],
             "active_outputs": ["valuesOut"]},
            {"symbol": "WitnessDim", "active_attrs": ["dim"],
             "active_outputs": ["valuesOut", "indicesOut"]},
        ],
    }


# —— 「第二份 runner_form 缺省」的 AST 静态门 ————————————————————————————————————————
# 判据只有一条：**读 `runner_form` 时自带的那个缺省值，必须是具名常量，不能是字面量。**
# 正则版（2026-08-05 前）只认双引号、单行、少数几种排版，换行 / 单引号 / 经中间变量的副本
# 全都漏过去——扫不到的门等于没门。AST 不看排版，还能顺着赋值把中间变量认出来。

#: 唯一合法的缺省来源（`repo_adapter.DEFAULT_RUNNER_FORM` 及其别名）。
_ALLOWED_DEFAULT_NAMES = frozenset({"DEFAULT_RUNNER_FORM", "_DEFAULT_RUNNER_FORM"})
#: 受控词表本身 —— 只有这几个字符串才可能被当成 runner_form 缺省。拿它当判据，是为了不误伤
#: 「按 form 选个 gate 名字」这类同形但无关的条件表达式（preflight_aclnn 就有一处）。
_RUNNER_FORM_VALUES = frozenset(repo_adapter.SUPPORTED_NP_BY_FORM)
#: 读 `spec.runner_form` 的函数名（含 run_workflow 的同源薄壳）。
_READ_FUNCS = frozenset({"spec_runner_form", "_spec_runner_form", "resolve_runner_form"})


def _subscript_key(node):
    """取下标表达式的键节点，兼容 py3.8 的 `ast.Index` 包装。"""
    key = node.slice
    return key.value if type(key).__name__ == "Index" else key


def _is_runner_form_key(node):
    return isinstance(node, ast.Constant) and node.value == "runner_form"


def _is_form_literal(node):
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _RUNNER_FORM_VALUES)


def _is_allowed_default(node):
    if isinstance(node, ast.Name):
        return node.id in _ALLOWED_DEFAULT_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _ALLOWED_DEFAULT_NAMES
    return False


def _is_runner_form_read(node):
    """本节点是否**就是**「读 runner_form」的表达式（`.get(...)` / 下标 / 读取函数调用）。"""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr == "get" and node.args and _is_runner_form_key(node.args[0]):
                return True
            if func.attr in _READ_FUNCS:
                return True
        if isinstance(func, ast.Name) and func.id in _READ_FUNCS:
            return True
    if isinstance(node, ast.Subscript) and _is_runner_form_key(_subscript_key(node)):
        return True
    return False


def _runner_form_names(tree):
    """模块内「装着 runner_form 的变量名」集合（过近似，故意宁滥勿缺）。

    两个来源：① 被某个 runner_form 读表达式赋过值的名字（`form = spec.get("runner_form")`）；
    ② 名字里直接写着 `runner_form` 的（`runner_form` / `dry_runner_form` / 形参同名）。
    有了它，`form = form or "cpp"` 这种经中间变量的副本才抓得到。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        if any(_is_runner_form_read(sub) for sub in ast.walk(value)):
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and "runner_form" in node.id:
            names.add(node.id)
        elif isinstance(node, ast.arg) and "runner_form" in node.arg:
            names.add(node.arg)
    return names


def _mentions_runner_form(node, tainted):
    for sub in ast.walk(node):
        if _is_runner_form_read(sub):
            return True
        if isinstance(sub, ast.Name) and sub.id in tainted:
            return True
    return False


def _runner_form_default_offenders(tree):
    """返回 `[(行号, 说明), …]`；空列表 = 这棵树里没有第二份 runner_form 缺省。"""
    tainted = _runner_form_names(tree)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) >= 2
                and _is_runner_form_key(node.args[0])
                and not _is_allowed_default(node.args[1])):
            hits.append((node.lineno,
                         '`.get("runner_form", …)` 的缺省不是 DEFAULT_RUNNER_FORM'))
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if (any(_mentions_runner_form(v, tainted) for v in node.values)
                    and any(_is_form_literal(v) for v in node.values)):
                hits.append((node.lineno,
                             '`runner_form or "…"` —— `or` 把「没写」和「写坏了」混为一谈'))
        elif isinstance(node, ast.IfExp):
            if (_mentions_runner_form(node.test, tainted)
                    or _mentions_runner_form(node.body, tainted)
                    or _mentions_runner_form(node.orelse, tainted)):
                if _is_form_literal(node.body) or _is_form_literal(node.orelse):
                    hits.append((node.lineno, "条件表达式里兜了一个 runner_form 字面量缺省"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spec_args = node.args
            positional = list(getattr(spec_args, "posonlyargs", [])) + list(spec_args.args)
            defaults = list(spec_args.defaults)
            paired = list(zip(positional[len(positional) - len(defaults):], defaults))
            paired += [(a, d) for a, d in zip(spec_args.kwonlyargs, spec_args.kw_defaults)
                       if d is not None]
            for arg, default in paired:
                if "runner_form" in arg.arg and _is_form_literal(default):
                    hits.append((node.lineno,
                                 f"形参 {arg.arg} 的默认值是 runner_form 字面量"))
    return sorted(hits)


def _witness_caseset():
    """与 `_witness_spec` 配套的合法 caseset（逐 case 已解析 `aclnn_call`）。"""
    return {
        "op": "Witness",
        "cases": [
            {
                "id": "c0",
                "aclnn_call": {
                    "symbol": "Witness",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "out", "name": "valuesOut", "output_idx": 0},
                        {"role": "out_null", "name": "indicesOut"},
                    ],
                },
            },
            {
                "id": "c1",
                "aclnn_call": {
                    "symbol": "WitnessDim",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
                        {"role": "out", "name": "valuesOut", "output_idx": 0},
                        {"role": "out", "name": "indicesOut", "output_idx": 1},
                    ],
                },
            },
        ],
    }


class WorkflowModeResolutionTest(unittest.TestCase):
    def test_omitted_mode_is_derived_from_runner_form(self):
        # 通路收敛（2026-08-06）后**只剩一条派生**：cpp_extension → cpp_extension。
        # 键缺席吃缺省，缺省也是它。cpp / aclnn_py 已无映射（见 RetiredRunnerFormTest）。
        self.assertEqual(W._resolve_mode({}, None), "cpp_extension")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp_extension"}, None), "cpp_extension")
        self.assertEqual(sorted(W._RUNNER_FORM_TO_MODE), ["cpp_extension"])

    def test_explicit_real_machine_mismatch_is_rejected(self):
        # 「走错真机通路」是输入错，应当比准入门更早报出来——否则用户打错 mode 时
        # 收到的是「这条通路不准入」，看不出自己 mode 打错了。
        # ⚠ 收敛后只有准入形态谈得上「不匹配」（退役形态压根没有期望 mode，那归退役门管）。
        for bad in ("aclnn_py", "new_example"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(SystemExit, "不匹配"):
                    W._resolve_mode({"runner_form": "cpp_extension"}, bad)

    def test_explicit_non_acceptance_escape_remains_available(self):
        """⭐ mock 逃生口不受准入门影响：它物理上就不产 acceptance.json，与 runner_form 准入无关。

        ⚠ 退役形态也照走得通——**这不是给退役形态留后门**：mock 的「NPU 输出」= golden.copy()，
        那条路产不出任何验收结论，堵它只会把「本地自检用例链」一起堵死。
        """
        self.assertEqual(
            W._resolve_mode({"runner_form": "aclnn_py"}, "mock"), "mock")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp"}, "mock"), "mock")
        self.assertEqual(
            W._resolve_mode({"runner_form": "cpp_extension"}, "mock"), "mock")

    def test_unknown_runner_form_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "不受支持"):
            W._resolve_mode({"runner_form": "opaque"}, None)


class AcceptanceFormGateTest(unittest.TestCase):
    """⭐ 正式验收当前只准入 cpp_extension（唯一跑通完整 torch_parity 矩阵的通路）。

    门必须落在**两处**：入口拦正常调用路径，出口（写 acceptance.json / verdict.json 之前）
    拦绕过入口的路径。只拦入口是拦不住的——仓里已有先例：`repo_adapter.py` 的注释明写
    `repo_adapter.py cs wd acceptance.json catlass_mock` 是绕开 CLI 守卫的现成后门。
    """

    def test_entry_gate_blocks_unlisted_forms_with_actionable_message(self):
        for form in ("cpp", "aclnn_py"):
            with self.subTest(form=form):
                with self.assertRaises(SystemExit) as cm:
                    W._resolve_mode({"runner_form": form}, None)
                msg = str(cm.exception)
                self.assertIn("已停止准入", msg)
                self.assertIn("torch_parity", msg)                # 要讲清为什么堵
                # 要讲清**出路**，且出路只有「迁到 cpp_extension」这一条
                self.assertIn("cpp_extension", msg)
                self.assertIn("迁到", msg)

    def test_entry_gate_lets_cpp_extension_through(self):
        self.assertEqual(W._resolve_mode({"runner_form": "cpp_extension"}, None),
                         "cpp_extension")

    def test_exit_gate_still_blocks_a_hand_fed_retired_mode(self):
        """出口门不是冗余：绕开 `_resolve_mode` 把 mode 直接递进来，写产物前仍然被拦。"""
        with self.assertRaises(SystemExit) as cm:
            W._assert_acceptance_form_allowed({"runner_form": "aclnn_py"}, "aclnn_py")
        self.assertIn("出口门", str(cm.exception))

    def test_exit_gate_blocks_even_when_entry_was_bypassed(self):
        for form in ("cpp", "aclnn_py"):
            with self.subTest(form=form):
                with self.assertRaisesRegex(SystemExit, "拒绝为 runner_form"):
                    W._assert_acceptance_form_allowed(
                        {"runner_form": form}, _MODE_OF[form])

    def test_exit_gate_lets_cpp_extension_through(self):
        W._assert_acceptance_form_allowed({"runner_form": "cpp_extension"}, "cpp_extension")

    def test_omitted_runner_form_lands_inside_the_admission_set(self):
        """⭐ spec 漏写 `runner_form` 时**不得**撞准入门。

        缺省 `cpp` 的年代，「spec 没写 runner_form」派生出 `new_example`，一步就撞上准入门，
        编排层只能停下问用户走哪条路（Roll 就是这么卡住的）。既然只有 `cpp_extension` 准入，
        缺省就得跟着它走：**不带** `allow_experimental_form` 也应该一路通到 `cpp_extension`。
        """
        self.assertEqual(W._DEFAULT_RUNNER_FORM, "cpp_extension")
        self.assertIn(W._DEFAULT_RUNNER_FORM, W._ACCEPTANCE_RUNNER_FORMS)
        # 入口门（不给逃生阀）
        self.assertEqual(W._resolve_mode({}, None), "cpp_extension")
        # 出口门：入口放行的，出口也必须放行，否则「跑得起来、写不出裁决」
        W._assert_acceptance_form_allowed({}, "cpp_extension")
        # 显式请求同一条真机 mode 也不该被判成走错通路
        self.assertEqual(W._resolve_mode({}, "cpp_extension"), "cpp_extension")

    def test_omitted_runner_form_is_not_experimental(self):
        """三处判定必须同源：`run()` 里决定产不产验收产物的那一处也得吃同一个缺省。

        它若还按 `cpp` 读，缺省 spec 会「派生出准入 mode，却被当成实验形态只产 dev_* 产物」。
        """
        self.assertFalse(W._spec_runner_form({}) not in W._ACCEPTANCE_RUNNER_FORMS)
        self.assertEqual(W._spec_runner_form({}), "cpp_extension")

    def test_explicit_null_runner_form_stays_fail_closed(self):
        """只有**键缺席**吃缺省；显式 null / "" 是写坏的 spec，照旧报「不受支持」。

        写成 `or` 就会把它们悄悄兜成准入形态——那是拿缺省替一份坏 spec 背书。
        """
        for bad in (None, "", 0):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(SystemExit, "不受支持"):
                    W._resolve_mode({"runner_form": bad}, None)

    def test_capability_tables_are_not_touched(self):
        """能力表 ≠ 准入表：删了将来想恢复要重新考证 dtype 支持面。"""
        import repo_adapter
        for form in ("cpp", "aclnn_py"):
            self.assertIn(form, repo_adapter.SUPPORTED_NP_BY_FORM)


class RetiredRunnerFormTest(unittest.TestCase):
    """⭐ 通路收敛（2026-08-06）：`cpp` / `aclnn_py` **连真机入口都没有了**，逃生阀已删。

    上一轮的形态是「映射还在 + 白名单门 + `--allow-experimental-form`」——非准入形态跑得起来、
    只是产不出裁决。aclnnRoll 试跑实测了那条设计的代价：编排层采信一句未经验证的论断把
    `runner_form` 改成 `cpp`，之后整轮**物理上不可能产出裁决**，却跑满 1h47m 才 BLOCKED。
    结论：能跑起来的死路就会有人走进去，所以把死路封在第一步。

    本类钉三件事：**入口封死**、**错误信息给的是迁移出路而不是另一条死路**、**逃生阀不许回来**。
    """

    _RETIRED = ("cpp", "aclnn_py")

    def test_derivation_table_and_admission_set_are_the_same_set(self):
        """派生表 ≡ 准入集。两边只改一处 = 要么留下死路、要么准入了却派不出 mode。"""
        self.assertEqual(set(W._RUNNER_FORM_TO_MODE), set(W._ACCEPTANCE_RUNNER_FORMS))
        self.assertEqual(set(W._RUNNER_FORM_TO_MODE), {"cpp_extension"})

    def test_retired_forms_are_known_vocabulary_but_have_no_mode(self):
        """受控词表照旧（能力表 key），只是这些形态不再有映射——「不认识」与「已退役」要分得开。"""
        for form in self._RETIRED:
            with self.subTest(form=form):
                self.assertIn(form, W._KNOWN_RUNNER_FORMS)
                self.assertIn(form, W._RETIRED_RUNNER_FORMS)
                self.assertNotIn(form, W._RUNNER_FORM_TO_MODE)

    def test_omitted_mode_on_a_retired_form_is_refused(self):
        for form in self._RETIRED:
            with self.subTest(form=form):
                with self.assertRaisesRegex(SystemExit, "已停止准入"):
                    W._resolve_mode({"runner_form": form}, None)

    def test_explicitly_asking_for_the_old_real_machine_mode_is_also_refused(self):
        """⭐ 「换个 mode 再试」必须此路不通——否则删逃生阀只是把绕行换了个写法。"""
        for form, mode in (("cpp", "new_example"), ("aclnn_py", "aclnn_py")):
            with self.subTest(form=form, mode=mode):
                with self.assertRaisesRegex(SystemExit, "已停止准入"):
                    W._resolve_mode({"runner_form": form}, mode)
        # 交叉写法（cpp 的 spec 却点名 aclnn_py 的 mode）同样不许溜过去
        with self.assertRaisesRegex(SystemExit, "已停止准入"):
            W._resolve_mode({"runner_form": "cpp"}, "aclnn_py")

    def test_the_message_offers_migration_and_never_another_dead_end(self):
        """错误信息是**承诺**不是注释：给出路（迁 cpp_extension），并明说别去换 mode。"""
        msg = W._retired_form_message("cpp")
        self.assertIn("cpp_extension", msg)
        self.assertIn("迁到", msg)
        self.assertIn("不要改用", msg)          # 明确劝阻「换个 mode 再试」
        for dead in ("new_example", "aclnn_py"):
            self.assertIn(dead, msg, "该讲清哪些 mode 也是死路")
        # 历史不改判：不得把「不支持新建」写成「当时那次不算数」
        self.assertIn("历史", msg)
        # ⚠ 不许再指向任何逃生阀
        self.assertNotIn("--allow-experimental-form", msg)

    def test_no_stack_trace_instead_of_a_sentence(self):
        """删映射项最容易踩的坑：`.get()` 拿不到之后一路 KeyError。本条钉「报的是人话」。"""
        for form in self._RETIRED:
            with self.subTest(form=form):
                try:
                    W._resolve_mode({"runner_form": form}, None)
                except SystemExit as ex:
                    self.assertGreater(len(str(ex)), 40, "错误信息不能只有一行光秃秃的枚举")
                except KeyError as ex:                     # pragma: no cover - 回归时才会走到
                    self.fail(f"退化成 KeyError 了（不说人话）：{ex!r}")
                else:
                    self.fail(f"runner_form={form!r} 竟然被放行了")

    def test_the_escape_hatch_is_gone_from_both_the_api_and_the_cli(self):
        """⭐ 「别把 `--allow-experimental-form` 加回来」这条钉子。

        两处一起钉：`run()` 的签名（进程内调用面）与 argparse（命令行面）。
        argparse 侧走 AST 只看 `add_argument` 的实参，所以源码里那句「别加回来」的注释
        不会把本条弄成假绿。
        """
        self.assertNotIn("allow_experimental_form",
                         inspect.signature(W.run).parameters)
        added = []
        for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(W.main)))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                added.append(node.args[0].value)
        self.assertIn("--mode", added, "AST 没扫到 add_argument —— 本条会假绿，先修扫描")
        self.assertNotIn("--allow-experimental-form", added,
                         "逃生阀被加回来了：它放行的是「跑起来」，而能跑起来的死路就会有人走进去")

    def test_capability_tables_are_untouched(self):
        """能力表 ≠ 准入表：退役的是「能不能出裁决」，不是「支持哪些 dtype」那份知识。"""
        for form in self._RETIRED:
            with self.subTest(form=form):
                self.assertIn(form, repo_adapter.SUPPORTED_NP_BY_FORM)
                self.assertIn(form, repo_adapter.DEFERRED_NP_BY_FORM)

    def test_the_executor_registry_is_not_the_admission_table(self):
        """`repo_adapter.MODES` 保留全部执行器 —— 门守在 runner_form 层，不靠藏执行器。"""
        for mode in ("mock", "new_example"):
            self.assertIn(mode, repo_adapter.MODES)


class NonAcceptanceNoteTest(unittest.TestCase):
    """⭐ 非验收产物的注脚**按真实原因取串**。

    病历（2026-08-06，aclnnRoll 试跑）：一句 mock 措辞套所有非验收产物，于是
    `--allow-experimental-form` 下那一轮**真机**跑被标成「NPU 输出 = golden.copy()、
    性能是编的假数」——一句凭空的假话。读报告的人会以为这轮压根没上过真机，从而错判失败归因。

    ⚠ **措辞错方向与判定错方向一样贵**：本仓不许把假数说成真机数据，同样不许把真机数据说成假数。
    """

    #: 「数据是编的」这句话的字面标志。真机跑的产物里出现任何一个都算说假话。
    _FABRICATION_WORDS = ("mock", "golden.copy()", "假数", "构造必过")

    def test_mock_family_gets_the_fabricated_data_wording(self):
        for mode in W._MOCK_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(W._non_acceptance_note(mode, False), W._NOTE_MOCK)

    def test_real_machine_on_an_unlisted_form_is_never_called_mock(self):
        """⭐ 主门：真机数据 + 非准入 form → 只说「这条 form 不出裁决」，不许声称数据是编的。"""
        for mode in ("new_example", "aclnn_py", "cpp_extension"):
            with self.subTest(mode=mode):
                note = W._non_acceptance_note(mode, True)
                self.assertEqual(note, W._NOTE_FORM)
                for word in self._FABRICATION_WORDS:
                    self.assertNotIn(word, note.casefold())
                self.assertIn("真机", note)          # 要正面说清数据是真的

    def test_mock_wins_when_both_reasons_apply(self):
        """顺序不能反：「数据是编的」是更强、更要紧的那句话，不能被「form 不准入」盖过去。"""
        for mode in W._MOCK_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(W._non_acceptance_note(mode, True), W._NOTE_MOCK)

    def test_unregistered_modes_fall_back_to_the_neutral_wording(self):
        """漏登记一个 mock 家族的 mode，后果必须是「少说一句」，而不是凭空断言数据真假。"""
        for mode in ("catlass", "some_future_mode"):
            with self.subTest(mode=mode):
                note = W._non_acceptance_note(mode, False)
                self.assertEqual(note, W._NOTE_OTHER)
                for word in self._FABRICATION_WORDS:
                    self.assertNotIn(word, note.casefold())

    def test_stamp_dev_defaults_to_the_neutral_string(self):
        """漏传实参时的失败方向是「少说一句」。"""
        self.assertEqual(W._stamp_dev({}, False, "development")["acceptance_note"],
                         W._NOTE_OTHER)

    def test_every_note_carries_the_non_acceptance_marker(self):
        """三串都得能被「按标记词筛非验收产物」的下游认出来（`test_perf_compare` 就这么筛）。"""
        for note in (W._NOTE_MOCK, W._NOTE_FORM, W._NOTE_OTHER):
            self.assertIn("NON-ACCEPTANCE", note)
            self.assertIn("不得作为验收结论引用", note)

    def test_the_three_strings_are_actually_distinct(self):
        """把两串写成同一个值 = 分支形同虚设，而上面那些断言仍可能碰巧全绿。"""
        self.assertEqual(len({W._NOTE_MOCK, W._NOTE_FORM, W._NOTE_OTHER}), 3)


class RunnerFormDefaultSingleSourceTest(unittest.TestCase):
    """⭐ P5：`spec.runner_form` 的缺省**只有一份定义**，全仓读侧同源。

    病历：`run_workflow` 的缺省从 `cpp` 改成 `cpp_extension`，但 `gen_cases` / `repo_adapter` /
    `preflight_aclnn` / `finalize_clean_acceptance` / `precision_retest_contract` 各自还留着自己那份
    `"cpp"` 字面量 → **同一份省略了该键的 spec 在不同模块被当成两种形态**：run_workflow 派生
    `--mode cpp_extension`，gen_cases 却按 `cpp` 的窄 dtype 表规划、且判定「不需要 aclnn_call」。

    本类同时钉两件事：**值同源**（行为）与**只有一处定义**（静态）。只钉行为不够——
    行为相等可以靠两处字面量恰好写成同一个值维持，下一个人改其中一处就又漂了。
    """

    def _python_sources(self):
        """插件树下**全部**非测试 Python 源（递归）。

        ⚠ 原来只扫 `acc-common/*.py` 一层（2026-08-05 审修门 Medium#6）：`aclnn_runtime/`、
        `contract_ir/`、`catlass/` 这些子包整个在门外。扫描面窄本身就是漏网。
        """
        root = os.path.abspath(os.path.join(_HERE, ".."))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                if name.endswith(".py") and not name.startswith("test_"):
                    path = os.path.join(dirpath, name)
                    yield os.path.relpath(path, root), path

    def test_default_is_defined_exactly_once(self):
        """静态门：插件树下不得再出现第二份 `runner_form` 缺省。

        ⚠ 本门 2026-08-05 审修门 Medium#6 从正则改成 **AST**。旧正则只认双引号、单行、少数几种
        写法，以下副本全能漏过去：
        ```python
        spec.get('runner_form', 'cpp')          # 单引号
        spec.get(                               # 换行
            "runner_form",
            "cpp",
        )
        form = spec.get("runner_form"); form = form or "cpp"   # 经中间变量
        ```
        AST 不看排版、不看引号，还能顺着赋值把「先取值、再 `or` 一个字面量」这条链认出来。
        """
        offenders = []
        for name, path in self._python_sources():
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            try:
                tree = ast.parse(source, filename=name)
            except SyntaxError as ex:                # 语法都不对不该悄悄跳过
                self.fail(f"{name} 解析失败：{ex}")
            for lineno, snippet in _runner_form_default_offenders(tree):
                offenders.append(f"{name}:{lineno}: {snippet}")
        self.assertEqual(
            offenders, [],
            "runner_form 缺省出现了第二份定义 —— 必须改走 "
            "`repo_adapter.spec_runner_form(spec)` / `repo_adapter.DEFAULT_RUNNER_FORM`：\n"
            + "\n".join(offenders))

    def test_the_static_gate_actually_catches_the_shapes_it_claims_to(self):
        """⭐ 反向证明这道静态门不是假门：它自称能抓的每种写法，都得真被抓到。

        没有这一条，上面那个「offenders 为空」的断言可以靠**一个什么都不匹配的检查器**维持——
        正是旧正则的处境（换行、单引号、经中间变量的副本一个都抓不到，门却一直是绿的）。
        """
        must_catch = (
            'form = spec.get("runner_form", "cpp")',
            "form = spec.get('runner_form', 'cpp')",
            'form = spec.get(\n    "runner_form",\n    "cpp",\n)',
            'form = spec.get("runner_form") or "cpp"',
            'form = spec.get("runner_form")\nform = form or "cpp"',
            'form = spec.get("runner_form")\nform = "cpp" if form is None else form',
            'runner_form = spec["runner_form"] if k else "cpp"',
            'def f(runner_form="cpp"):\n    return runner_form',
            'form = repo_adapter.spec_runner_form(spec) or "cpp"',
        )
        for snippet in must_catch:
            with self.subTest(snippet=snippet):
                found = _runner_form_default_offenders(ast.parse(snippet))
                self.assertTrue(found, f"静态门漏掉了这种写法：\n{snippet}")
        # 反向：真源自身与合法读法不得被误报（否则门只能靠 noqa 活着）
        must_pass = (
            'return spec.get("runner_form", DEFAULT_RUNNER_FORM)',
            "return DEFAULT_RUNNER_FORM if x is UNSPECIFIED_RUNNER_FORM else x",
            "_DEFAULT_RUNNER_FORM = repo_adapter.DEFAULT_RUNNER_FORM",
            'runner_form = repo_adapter.spec_runner_form(spec)',
            'needs = runner_form in ("aclnn_py", "cpp_extension")',
            'if runner_form != "cpp_extension":\n    raise ValueError("x")',
        )
        for snippet in must_pass:
            with self.subTest(snippet=snippet):
                self.assertEqual(
                    _runner_form_default_offenders(ast.parse(snippet)), [],
                    f"静态门误报了合法写法：\n{snippet}")

    def test_run_workflow_default_is_an_alias_not_a_second_definition(self):
        self.assertEqual(W._DEFAULT_RUNNER_FORM, repo_adapter.DEFAULT_RUNNER_FORM)
        # ⚠ 值相等**不足以**证明是别名：短字符串字面量会被 interning，两处各写一份
        #   `"cpp_extension"` 照样 `is` 相等、`==` 相等。所以这里核**赋值来源**：
        #   run_workflow 必须是从 repo_adapter 取，而不是自己再写一个字面量。
        with open(os.path.abspath(W.__file__), encoding="utf-8") as fh:
            source = fh.read()
        self.assertRegex(
            source, r"_DEFAULT_RUNNER_FORM\s*=\s*repo_adapter\.DEFAULT_RUNNER_FORM",
            "run_workflow._DEFAULT_RUNNER_FORM 必须是 repo_adapter.DEFAULT_RUNNER_FORM 的别名，"
            "不得再写第二份字面量")
        # 真源自身必须落在受控词表内（否则查能力表当场 fail-closed）
        self.assertIn(repo_adapter.DEFAULT_RUNNER_FORM, repo_adapter.SUPPORTED_NP_BY_FORM)
        # 且必须落在准入集内——这条不变式的断言留在 run_workflow，这里复核它真的成立
        self.assertIn(W._DEFAULT_RUNNER_FORM, W._ACCEPTANCE_RUNNER_FORMS)

    def test_mode_derivation_and_planning_read_the_same_default(self):
        """⭐ 本任务的正题：省略 `runner_form` 时，run_workflow 派生的 mode 与 gen_cases 的
        规划口径**同源**——不再一个 cpp_extension、一个 cpp。"""
        resolved = repo_adapter.DEFAULT_RUNNER_FORM
        self.assertEqual(repo_adapter.spec_runner_form({}), resolved)
        self.assertEqual(W._spec_runner_form({}), resolved)
        # run_workflow 侧：缺省 form 派生出的真机 mode
        self.assertEqual(W._resolve_mode({}, None), W._RUNNER_FORM_TO_MODE[resolved])
        # gen_cases 侧：dtype 能力门按哪一支查表。int64 是两支的**分水岭**——
        # `cpp` 收不了（Track-C 也没挂它）、`cpp_extension` 收得了，故它能直接读出用的是哪张表。
        probe = [{"name": "self", "io": "in", "dtype": ["int64"]}]
        self.assertNotIn("int64", repo_adapter.supported_np("cpp"))
        self.assertNotIn("int64", repo_adapter.deferred_np("cpp"))
        self.assertIn("int64", repo_adapter.supported_np(resolved))
        # 退回 cpp 就会在这 raise。⚠ 传的是**从 spec 读出来的**那个值（键缺席 → 缺省），
        #   不是 `None`——P5-b 起 `None` 是非法 form，不再表示「未指定」。
        gen_cases.check_spec_capability(probe, repo_adapter.spec_runner_form({}))
        # 「调用方未提供形参」这条归一路径也必须吃同一份缺省（`gen_cases` 委托给它，不自建）。
        # 表达方式是**省略实参**，不是传 `None`。
        self.assertEqual(repo_adapter.resolve_runner_form(), resolved)
        self.assertIs(repo_adapter.supported_np(), repo_adapter.SUPPORTED_NP_BY_FORM[resolved])
        self.assertIs(repo_adapter.deferred_np(), repo_adapter.DEFERRED_NP_BY_FORM[resolved])

    def test_none_is_an_illegal_form_not_a_way_to_ask_for_the_default(self):
        """⭐ `None` **不得**再兼职表达「调用方没给形参」——那是 P5-b fail-open 的病根。

        病历：`spec_runner_form({"runner_form": None})` 按契约原样返回 `None`（写坏的 spec），
        可它一进 `resolve_runner_form(None)` 就被兜成 `cpp_extension`。于是同一份 spec 在
        `gen_cases` 里被读成两种意思：dtype 门按 cpp_extension 放行 int64，
        `needs_aclnn_call` 却比原始 `None` → 不要求 `call_variants`。
        """
        default = repo_adapter.DEFAULT_RUNNER_FORM
        # 「未提供」现在由哨兵表达，且它**不是**一种 runner_form
        self.assertIsNot(repo_adapter.UNSPECIFIED_RUNNER_FORM, None)
        self.assertNotIn(repo_adapter.UNSPECIFIED_RUNNER_FORM,
                         repo_adapter.SUPPORTED_NP_BY_FORM)
        self.assertEqual(
            repo_adapter.resolve_runner_form(repo_adapter.UNSPECIFIED_RUNNER_FORM),
            default)
        # `None` 原样穿过归一，随后在受控词表处 fail-closed
        self.assertIsNone(repo_adapter.resolve_runner_form(None))
        for bad in (None, "", 0):
            with self.subTest(bad=bad):
                for probe in (repo_adapter.supported_np, repo_adapter.deferred_np):
                    with self.assertRaisesRegex(ValueError, "未知 runner_form"):
                        probe(bad)

    def test_planning_ledger_records_the_resolved_form_not_the_raw_key(self):
        """CP-B 账本落的是**已解析**的 form：它被下游哈希绑定，记错 = 一份假记录。

        ⚠ 这里**不 mock `load_golden`**（2026-08-05 审修门 Medium#7）。原来那份 mock 掉的是
        gen_cases 自己的纯函数，等于把「dry-run 真遇到缺 golden 会怎样」整条路径替换成了
        夹具的返回值——账本里 `golden_dependency` / `golden_cost_note` 两栏于是从没被真跑过。
        改法：把 `OPRUNWAY_OPS_DIR` 指到一个空的临时 ops root，让真实加载逻辑自然走到
        「缺 golden:」那一支。
        """
        spec = copy.deepcopy(_sign_spec())
        spec.pop("runner_form")                          # 制造「spec 省略该键」
        with _empty_ops_root():
            ledger = gen_cases._build_dry_run_ledger(spec)
        self.assertEqual(ledger["planning"]["runner_form"], repo_adapter.DEFAULT_RUNNER_FORM)
        # 真实的「缺 golden」路径确实被走到了（而不是被夹具短路掉）
        self.assertEqual(ledger["golden_dependency"]["status"], "missing")
        self.assertIsNone(ledger["golden_dependency"]["bytes_sha256"])
        self.assertIn("缺 golden", ledger["planning"]["golden_cost_note"])
        self.assertEqual(ledger["planning"]["golden_out_shape"], "not_available")

    def test_explicit_null_form_fails_closed_in_gen_cases_and_dry_run(self):
        """⭐ 显式 `null` / `""` / `0` 的 spec 在**正式生成**与 **dry-run** 两条路上都 fail-closed。

        这是 P5-b 的直接回归：`spec_runner_form` 返回的坏值曾被 `resolve_runner_form(None)`
        兜成当前唯一准入形态 → 过了 cpp_extension 的 dtype 能力门，却因为 `needs_aclnn_call`
        比的是原始 `None` 而**不要求** `call_variants`，产得出没有 `aclnn_call` 的 caseset；
        dry-run 那边还会照记一本 `planning.runner_form=null` 的账。
        """
        for bad in (None, "", 0):
            with self.subTest(bad=bad):
                spec = copy.deepcopy(_sign_spec())
                spec["runner_form"] = bad
                self.assertEqual(repo_adapter.spec_runner_form(spec), bad)  # 读侧原样返回
                with _empty_ops_root():
                    # ① dry-run（CP-B 契约自检）
                    with self.assertRaisesRegex(ValueError, "未知 runner_form"):
                        gen_cases._build_dry_run_ledger(spec)
                    # ② 正式生成：能力门在 load_golden 之前，故不依赖 golden 是否存在
                    with tempfile.TemporaryDirectory() as work:
                        with self.assertRaisesRegex(ValueError, "未知 runner_form"):
                            gen_cases.gen_cases(spec, work)
                        self.assertEqual(os.listdir(work), [])   # 没产出任何半成品用例

    def test_omitted_key_still_plans_and_requires_aclnn_call(self):
        """对照组：**键缺席**照旧一路通到 cpp_extension 的规划口径（证不是把整条路堵死）。

        同时钉住 `needs_aclnn_call`：缺省解析成 cpp_extension 后，一份没有 `call_variants`
        的 spec 必须在 `gen_cases` 当场被拒——那正是 P5-b 漏掉的那一条。
        """
        spec = copy.deepcopy(_sign_spec())
        spec.pop("runner_form")
        spec.pop("call_variants", None)
        # 带上真实的 Sign golden：`gen_cases` 的 `call_variants` 检查在 `load_golden` **之后**，
        # 空 ops root 会先炸在缺 golden 上，验不到本用例要验的那条。
        with _ops_root("Sign"):
            ledger = gen_cases._build_dry_run_ledger(spec)
            self.assertEqual(ledger["planning"]["runner_form"], "cpp_extension")
            self.assertEqual(ledger["golden_dependency"]["status"], "loaded")
            with tempfile.TemporaryDirectory() as work:
                with self.assertRaisesRegex(ValueError, "未声明 call_variants"):
                    gen_cases.gen_cases(spec, work)

    def test_cpp_extension_entry_guards_stay_closed_for_declared_other_forms(self):
        """形态门改读同一份缺省 ≠ 把门放宽：**显式**声明成别的形态照旧当场拒。

        放行的只有「键缺席」这一种——而它现在全仓一致地解析为 cpp_extension，
        上游已按该形态规划完毕，门再拒就成了分裂本身。
        """
        for bad_form in ("cpp", "aclnn_py", None, ""):
            with self.subTest(form=bad_form):
                with tempfile.TemporaryDirectory() as td:
                    with self.assertRaises(
                            cpp_extension_adapter.CppExtensionAdapterError) as cm:
                        cpp_extension_adapter.prepare(
                            {"runner_form": bad_form, "params": []}, {}, td)
                self.assertIn("仅接受 runner_form=cpp_extension", str(cm.exception))

    def test_cpp_extension_entry_guard_no_longer_trips_on_the_omitted_key(self):
        """键缺席的 spec 必须**一路跑通** codegen 与 adapter，产出真的 manifest / plan。

        ⚠ 本用例 2026-08-05 审修门 High#5 重写过，重写前是一道**假门**：它 `assertRaises(Exception)`
        再断言错误消息**不含**「仅接受 runner_form=cpp_extension」。把 `cpp_extension_codegen`
        改回读原始键，codegen 会抛**另一条**错误（`spec.params 须为非空列表`），旧断言照样通过——
        也就是说把本轮 codegen 的改动整个删掉，测试也不会红。
        「抛了别的异常」永远不能用来证明门已放行；能证明的只有**成功产出**。
        """
        spec = _witness_spec()                          # 刻意**不带** runner_form 键
        self.assertNotIn("runner_form", spec)
        self.assertEqual(repo_adapter.spec_runner_form(spec), "cpp_extension")

        # ① codegen：成功产出 manifest，且落盘内容与返回值一致
        with tempfile.TemporaryDirectory() as bundle:
            manifest = cpp_extension_codegen.generate(spec, bundle)
            on_disk = json.loads(
                open(os.path.join(bundle, "extension_manifest.json"),
                     encoding="utf-8").read())
        self.assertEqual(manifest, on_disk)
        self.assertTrue(manifest["namespace"])
        self.assertEqual(len(manifest["files"]["cpp"]["sha256"]), 64)

        # ② adapter：成功产出逐 case 调用计划（合法 caseset）
        with tempfile.TemporaryDirectory() as work:
            _, plan = cpp_extension_adapter.prepare(spec, _witness_caseset(), work)
            self.assertTrue(os.path.isfile(
                os.path.join(work, cpp_extension_adapter._PLAN)))
        self.assertEqual([row["entrypoint"] for row in plan["cases"]],
                         ["invoke_v0", "invoke_v1"])
        self.assertEqual(plan["namespace"], manifest["namespace"])


#: **历史**映射（收敛前 `_RUNNER_FORM_TO_MODE` 长这样），只作测试夹具用：
#: 「出口门对着退役形态该派生出的那个 mode 也要拦」这类断言需要它。
#: ⚠ 别把它读成生产表——生产表只剩 `{"cpp_extension": "cpp_extension"}`。
_MODE_OF = {"cpp": "new_example", "aclnn_py": "aclnn_py", "cpp_extension": "cpp_extension"}


if __name__ == "__main__":
    unittest.main()
