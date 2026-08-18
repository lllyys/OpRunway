"""align_signatures.py 的机械层回归测试。

真实事故：适配器的参数顺序、出参位置和空指针类型全靠 agent 从头文件推断，
一次写对的概率很低，错了要到部署后冒烟才报 `类型校验失败`。

这里锁住机械层的三条判定：出参不在末尾必须要求适配器、出参在末尾不要求、
以及拿不准的东西必须进 semantic_review 而不是被静默判定。

本机没有 ATK 和 torch，用桩替代——被桩的只有两张映射表和基线接口，
签名解析逻辑本身是被真实测到的。
"""

import ctypes
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
SCRIPT = SKILL_ROOT / "scripts" / "align_signatures.py"

MATMUL = ("aclnnStatus aclnnMatmulGetWorkspaceSize(const aclTensor *self, "
          "const aclTensor *mat2, aclTensor *out, int8_t cubeMathType, "
          "uint64_t *workspaceSize, aclOpExecutor **executor)")

TAIL_OUTPUT = ("aclnnStatus aclnnRollGetWorkspaceSize(const aclTensor *self, "
               "const aclIntArray *shifts, const aclIntArray *dims, aclTensor *out, "
               "uint64_t *workspaceSize, aclOpExecutor **executor)")

INPLACE = ("aclnnStatus aclnnInplaceAddGetWorkspaceSize(aclTensor *selfRef, "
           "const aclTensor *other, uint64_t *workspaceSize, aclOpExecutor **executor)")

# 桩：只提供 align_signatures 用到的两张表和一个可 inspect 的基线接口。
STUB = textwrap.dedent('''
    import ctypes, sys, types

    def _mod(name):
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            sub = ".".join(parts[:i])
            if sub not in sys.modules:
                sys.modules[sub] = types.ModuleType(sub)
        return sys.modules[name]

    class AclTensor(ctypes.Structure): pass
    class AclIntArray(ctypes.Structure): pass
    class OpExecutor(ctypes.Structure): pass

    wrapper = _mod("atk.tasks.backends.lib_interface.acl_wrapper")
    wrapper.CPP_TO_PYTHON_TYPE = {
        "aclTensor": AclTensor, "aclIntArray": AclIntArray, "aclOpExecutor": OpExecutor,
        "int8_t": ctypes.c_int8, "int64_t": ctypes.c_int64, "bool": ctypes.c_bool,
        "uint64_t": ctypes.c_uint64, "float": ctypes.c_float, "char*": ctypes.c_char_p,
    }
    backend = _mod("atk.tasks.backends.pyaclnn_backend")
    backend.PYTYPE_TO_CTYPE = {
        "int": ctypes.c_int64, "int8_t": ctypes.c_int8, "int64_t": ctypes.c_int64,
        "bool": ctypes.c_bool, "attr_bool": ctypes.c_bool, "float": ctypes.c_float,
        "string": ctypes.c_char_p,
    }

    torch = _mod("torch")
    def matmul(input, other): ...
    def roll(input, shifts, dims=None): ...
    def add(input, other): ...
    torch.matmul, torch.roll, torch.add = matmul, roll, add
    _mod("math")
''')


def run_align(baseline, signature):
    with tempfile.TemporaryDirectory() as temp_dir:
        out = Path(temp_dir) / "alignment.json"
        # 签名出处必须落在待验收算子工程目录里，CLI 现在强制核对，
        # 所以每次跑都要有一份工程树和记录了它的 env.json。
        project = Path(temp_dir) / "op_project"
        project.mkdir()
        header = project / "aclnn_op.h"
        header.write_text(signature + ";\n", encoding="utf-8")
        env = Path(temp_dir) / "env.json"
        env.write_text(json.dumps({"operator_project": {"path": str(project)}}),
                       encoding="utf-8")
        driver = Path(temp_dir) / "driver.py"
        driver.write_text(
            STUB + textwrap.dedent(f'''
                sys.path.insert(0, {str(SCRIPT.parent)!r})
                sys.argv = ["align", "--baseline", {baseline!r},
                            "--signature", {signature!r},
                            "--signature-source", {str(header)!r},
                            "--env", {str(env)!r}, "-o", {str(out)!r}]
                import align_signatures
                sys.exit(align_signatures.main())
            '''), encoding="utf-8")
        done = subprocess.run([sys.executable, str(driver)],
                              capture_output=True, text=True, timeout=60)
        report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return done, report


def run_build(baseline, signature):
    """跟 run_align 一样起子进程、一样的桩，但直接调 `build()`——
    用来喂手工构造的 baseline 字典，不依赖真实 torch.overrides 的行为。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        out = Path(temp_dir) / "report.json"
        driver = Path(temp_dir) / "driver.py"
        driver.write_text(
            STUB + textwrap.dedent(f'''
                sys.path.insert(0, {str(SCRIPT.parent)!r})
                import json as _json
                import align_signatures
                report = align_signatures.build({baseline!r}, {signature!r})
                open({str(out)!r}, "w", encoding="utf-8").write(_json.dumps(report))
            '''), encoding="utf-8")
        done = subprocess.run([sys.executable, str(driver)],
                              capture_output=True, text=True, timeout=60)
        report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return done, report


class ShimmedArityMismatchTest(unittest.TestCase):
    """真机事故（median，2026-08-16）：median 的全张量分面 aclnn 侧只有
    1 个入参，torch.overrides 替身却列了 2 个（`dim` 是 `median.dim` 重载
    混进来的，替身本身不区分重载）。旧触发条件 `len(rows) > len(base_params)`
    只在 aclnn 参数「更多」时才交叉验证，参数「更少」时直接把多出的 `dim`
    当成"基线独有"，误判 `aclnn_adapter.required=True`。改成个数不等就
    交叉验证，验证不出来就诚实报 `determinable=False`，不再猜。
    """

    MEDIAN_FULL = ("aclnnStatus aclnnMedianGetWorkspaceSize(const aclTensor *self, "
                   "aclTensor *out, uint64_t *workspaceSize, aclOpExecutor **executor)")

    def test_fewer_aclnn_params_than_shim_does_not_false_positive(self):
        baseline = {
            "api": "torch.median", "resolved": True, "source": "torch.overrides",
            "parameters": [
                {"name": "input", "kind": "POSITIONAL_OR_KEYWORD", "required": True},
                {"name": "dim", "kind": "POSITIONAL_OR_KEYWORD", "required": False},
            ],
            "overloads": ["aten::median(Tensor self) -> Tensor"], "note": None,
        }
        done, report = run_build(baseline, self.MEDIAN_FULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(report["aclnn_adapter"]["required"],
                         report["aclnn_adapter"]["reasons"])
        # 猜不出来就是猜不出来，不能落到"不需要"——那是需要人工核对官方文档
        self.assertFalse(report["aclnn_adapter"]["determinable"])


class BuiltinBaselineAlignmentTest(unittest.TestCase):
    """真机事故（bernoulli，2026-08-17）：基线是 CANN 内置实现时没有 torch 基线。

    对齐仍拿 `torch.bernoulli` 去量 aclnn 的 4 个入参，替身只列到
    `['input', 'generator']`，于是两侧适配器都判成 `determinable=False`，
    `check_adapter_binding.py` 退出码 2，S2 卡死；semantic_review 还会建议
    「6 个 aten 重载要拆成独立接口分面」——那是另一件事的建议。

    内置这条路上位置配对是恒等的：YAML 的输入名就是 aclnn 自己的形参名。
    """

    BERNOULLI = ("aclnnStatus aclnnBernoulliGetWorkspaceSize("
                 "const aclTensor* self, const aclScalar* prob, int64_t seed, "
                 "int64_t offset, aclTensor* out, uint64_t* workspaceSize, "
                 "aclOpExecutor** executor)")

    # builtin_baseline_signature() 的产物，逐字段照抄；下面有一条测试盯着它不漂。
    PROFILE = {"api": "aclnnBernoulli", "resolved": True, "parameters": None,
               "source": "cann_builtin", "kind": "cann_builtin",
               "overloads": None, "note": "..."}

    def _report(self):
        done, report = run_build(self.PROFILE, self.BERNOULLI)
        self.assertEqual(done.returncode, 0, done.stderr)
        return report

    def test_profile_matches_the_helper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "profile.json"
            driver = Path(temp_dir) / "driver.py"
            driver.write_text(
                STUB + textwrap.dedent(f'''
                    sys.path.insert(0, {str(SCRIPT.parent)!r})
                    import json as _json, align_signatures
                    open({str(out)!r}, "w", encoding="utf-8").write(_json.dumps(
                        align_signatures.builtin_baseline_signature("aclnnBernoulli")))
                '''), encoding="utf-8")
            done = subprocess.run([sys.executable, str(driver)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            actual = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual({key: value for key, value in actual.items() if key != "note"},
                         {key: value for key, value in self.PROFILE.items() if key != "note"})

    def test_yaml_keys_are_the_aclnn_parameter_names(self):
        report = self._report()
        self.assertEqual([row["yaml_key"] for row in report["aclnn"]["inputs"]],
                         ["self", "prob", "seed", "offset"])

    def test_both_adapters_are_determinable(self):
        report = self._report()
        self.assertTrue(report["aclnn_adapter"]["determinable"])
        self.assertTrue(report["baseline_adapter"]["determinable"])

    def test_cpu_golden_plugin_is_mandatory(self):
        report = self._report()
        self.assertTrue(report["baseline_adapter"]["required"])
        self.assertTrue(
            any("function_" in reason
                for reason in report["baseline_adapter"]["reasons"]),
            report["baseline_adapter"]["reasons"])

    def test_no_aclnn_adapter_when_the_output_is_at_the_tail(self):
        report = self._report()
        self.assertFalse(report["aclnn_adapter"]["required"],
                         report["aclnn_adapter"]["reasons"])

    def test_torch_only_review_items_are_gone(self):
        review = " ".join(item["issue"] for item in self._report()["semantic_review"])
        self.assertNotIn("aten 重载", review)
        self.assertNotIn("取不到基线形参名", review)
        # 可空指针那条与基线是谁无关，仍然要报
        self.assertIn("aclScalar", review)


class AlignSignaturesTest(unittest.TestCase):
    def test_output_not_at_tail_requires_adapter(self):
        done, report = run_align("torch.matmul", MATMUL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(report["aclnn_adapter"]["required"])
        self.assertTrue(any("不在末尾" in r for r in report["aclnn_adapter"]["reasons"]),
                        report["aclnn_adapter"]["reasons"])
        # 出参识别正确，且不占用基线配对位
        self.assertEqual([o["c_name"] for o in report["aclnn"]["outputs"]], ["out"])
        # 按位置配对：aclnn 的 self 对上 torch 的 input
        self.assertEqual(report["aclnn"]["inputs"][0]["baseline_name"], "input")
        self.assertEqual(report["aclnn"]["inputs"][0]["yaml_key"], "input")

    def test_output_at_tail_needs_no_adapter(self):
        done, report = run_align("torch.roll", TAIL_OUTPUT)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(report["aclnn_adapter"]["required"],
                         report["aclnn_adapter"]["reasons"])

    def test_trailing_atk_params_are_stripped(self):
        _, report = run_align("torch.roll", TAIL_OUTPUT)
        names = [p["c_name"] for p in report["aclnn"]["inputs"]]
        self.assertEqual(names, ["self", "shifts", "dims"])
        self.assertEqual(len(report["aclnn"]["trailing"]), 2)

    def test_int_array_warns_about_null_pointer(self):
        # 本轮 roll 的真实事故：None 转出 c_void_p，头文件要 aclIntArray*
        _, report = run_align("torch.roll", TAIL_OUTPUT)
        shifts = next(p for p in report["aclnn"]["inputs"] if p["c_name"] == "shifts")
        self.assertIn("c_void_p", shifts["yaml"]["note"])
        self.assertEqual(shifts["ctype"], "LP_AclIntArray")

    def test_inplace_parameter_is_not_silently_called_output(self):
        _, report = run_align("torch.add", INPLACE)
        flagged = [i["parameter"] for i in report["semantic_review"]]
        self.assertIn("selfRef", flagged, report["semantic_review"])
        # 出参不确定时不得给出入参对齐，否则 agent 会照着错的 yaml_key 写 YAML
        self.assertTrue(all(p.get("yaml_key") is None for p in report["aclnn"]["inputs"]))

    def test_name_mismatch_goes_to_semantic_review(self):
        _, report = run_align("torch.matmul", MATMUL)
        issues = {i["parameter"]: i["issue"] for i in report["semantic_review"]}
        self.assertIn("self", issues)
        self.assertIn("input", issues["self"])

    def test_aclnn_only_param_needs_baseline_adapter_not_aclnn_one(self):
        # cubeMathType 是 aclnn 独有的：aclnn 侧照常按序喂，出问题的是基线的 eval
        _, report = run_align("torch.matmul", MATMUL)
        self.assertTrue(report["baseline_adapter"]["required"])
        self.assertTrue(any("cubeMathType" in r for r in report["baseline_adapter"]["reasons"]),
                        report["baseline_adapter"]["reasons"])
        self.assertFalse(any("cubeMathType" in r for r in report["aclnn_adapter"]["reasons"]),
                         report["aclnn_adapter"]["reasons"])
        # 而且必须点名让 agent 判断取值语义，不能只报"数量对不上"
        self.assertIn("cubeMathType", [i["parameter"] for i in report["semantic_review"]])

    def test_matched_signatures_need_neither_adapter(self):
        done, report = run_align("torch.roll", TAIL_OUTPUT)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(report["aclnn_adapter"]["required"])
        self.assertFalse(report["baseline_adapter"]["required"],
                         report["baseline_adapter"]["reasons"])

    def test_suppressed_analysis_is_not_reported_as_no_adapter_needed(self):
        # 真实事故：median 的基线形参名取不全，脚本正确地拒绝分析，
        # 但汇总行照样打印"基线侧适配器：不需要"——把"没查"说成了"没问题"。
        _, report = run_align("torch.add", INPLACE)
        self.assertFalse(report["baseline_adapter"]["determinable"])
        self.assertFalse(report["baseline_adapter"]["required"])

    def test_determinable_verdict_survives_when_names_resolve(self):
        _, report = run_align("torch.roll", TAIL_OUTPUT)
        self.assertTrue(report["baseline_adapter"]["determinable"])
        self.assertTrue(report["aclnn_adapter"]["determinable"])

    def test_aten_schema_completes_names_the_shim_dropped(self):
        # 真实事故：torch.overrides 的替身给 median 只列了 ['input','dim']，漏了 keepdim。
        # 实测 torch.median(input=,dim=,keepdim=) 可调用，而 keepDim= 和 self= 都报 TypeError，
        # 所以补名必须补出 Python 形参名，不能照抄 C 的驼峰名或 aten 的 self。
        from align_signatures import aten_param_names, names_from_overloads

        schema = ("aten::median.dim(Tensor self, int dim, bool keepdim=False)"
                  " -> (Tensor values, Tensor indices)")
        self.assertEqual(aten_param_names(schema), ["input", "dim", "keepdim"])

        baseline = {"parameters": [{"name": "input"}, {"name": "dim"}],
                    "overloads": ["aten::median(Tensor self) -> Tensor", schema]}
        names, used = names_from_overloads(baseline, 3)
        self.assertEqual(names, ["input", "dim", "keepdim"])
        self.assertIs(used, schema)

    def test_conflicting_overload_is_not_trusted(self):
        # 重叠部分对不上就不能采信，否则等于拿重载名硬套
        from align_signatures import names_from_overloads

        baseline = {"parameters": [{"name": "log_probs"}],
                    "overloads": ["aten::other(Tensor self, int dim, bool k=False) -> Tensor"]}
        names, used = names_from_overloads(baseline, 3)
        self.assertIsNone(names)
        self.assertIsNone(used)

    def test_signature_without_atk_trailing_is_rejected(self):
        done, report = run_align("torch.matmul",
                                 "aclnnStatus aclnnMatmul(const aclTensor *self)")
        self.assertEqual(done.returncode, 2)
        self.assertIn("GetWorkspaceSize", done.stderr)
        self.assertIsNone(report)

    def test_unresolvable_baseline_is_rejected(self):
        done, _ = run_align("numpy.roll", TAIL_OUTPUT)
        self.assertEqual(done.returncode, 2)
        self.assertIn("torch", done.stderr)


class RejectInstalledHeaderTest(unittest.TestCase):
    """真机事故（median，2026-08-16）：`--header` 抓了 CANN 装机头文件，不是
    待验收算子自己工程目录下的头文件——两者同名但签名不同（装机版本 4 参无
    dim，待验收算子 7 参带 dim），S2 冻结了错的签名，直到 S3 构建安装完才
    发现，白跑一整轮构建。"""

    def _env(self, tmp, cann_home):
        path = tmp / "env.json"
        path.write_text(json.dumps({"cann": {"ASCEND_TOOLKIT_HOME": str(cann_home)}}),
                        encoding="utf-8")
        return path

    def test_header_under_cann_toolkit_home_is_rejected(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import align_signatures
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cann_home = tmp / "cann-9.0.0-beta.1"
            include = cann_home / "include" / "aclnnop"
            include.mkdir(parents=True)
            header = include / "aclnn_median.h"
            header.write_text("", encoding="utf-8")
            env = self._env(tmp, cann_home)
            with self.assertRaises(align_signatures.AlignError) as ctx:
                align_signatures.reject_installed_header(str(header), str(env))
            self.assertIn("装机", str(ctx.exception))

    def test_header_under_default_ascend_root_is_rejected_even_without_env(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import align_signatures
        # /usr/local/Ascend 是默认装机根，不传 --env 也要挡。
        with self.assertRaises(align_signatures.AlignError):
            align_signatures.reject_installed_header(
                "/usr/local/Ascend/cann-9.0.0-beta.1/include/aclnnop/aclnn_median.h",
                None)

    def test_header_under_operator_project_is_accepted(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import align_signatures
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cann_home = tmp / "cann-9.0.0-beta.1"
            cann_home.mkdir()
            project = tmp / "ops-nn-master-experimental-index-median"
            header_dir = project / "op_host" / "op_api"
            header_dir.mkdir(parents=True)
            header = header_dir / "aclnn_median.h"
            header.write_text("", encoding="utf-8")
            env = self._env(tmp, cann_home)
            align_signatures.reject_installed_header(str(header), str(env))  # 不应抛异常

    def test_header_outside_any_known_cann_root_is_accepted_without_env(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import align_signatures
        align_signatures.reject_installed_header("/tmp/whatever.h", None)  # 不应抛异常


class RequireProjectSourceTest(unittest.TestCase):
    """黑名单挡的是已知的 CANN 装机根，白名单正过来说：签名出处只能是
    `evidence/env.json` 的 `operator_project.path` 那棵树。

    同一台真机上还有别队的 vendor 目录、上一轮的构建产物、`find` 出来的
    同名头文件，任何一处都能给出同名不同签的声明，黑名单枚举不完。
    """

    def _env(self, tmp, project=None, cann_home=None):
        payload = {}
        if project is not None:
            payload["operator_project"] = {"path": str(project)}
        if cann_home is not None:
            payload["cann"] = {"ASCEND_TOOLKIT_HOME": str(cann_home)}
        path = tmp / "env.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def setUp(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import align_signatures
        self.mod = align_signatures

    def test_nonexistent_source_inside_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            project = tmp / "ops-nn-median"
            project.mkdir()
            env = self._env(tmp, project=project)
            with self.assertRaises(self.mod.AlignError) as ctx:
                self.mod.require_project_source(
                    str(project / "never_written.h"), str(env), "--signature-source")
            self.assertIn("不存在", str(ctx.exception))

    def test_source_inside_project_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            project = tmp / "ops-nn-median"
            (project / "op_host" / "op_api").mkdir(parents=True)
            header = project / "op_host" / "op_api" / "aclnn_median.h"
            header.write_text("", encoding="utf-8")
            env = self._env(tmp, project=project)
            self.assertEqual(
                self.mod.require_project_source(str(header), str(env), "--header"),
                str(header.resolve()))

    def test_other_vendor_dir_is_rejected_though_no_cann_root_matches(self):
        # 别队的 vendor 目录不在任何 CANN 装机根下，黑名单放行，白名单必须挡住。
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            project = tmp / "ops-nn-median"
            project.mkdir()
            stray = tmp / "other-team-vendor" / "aclnn_median.h"
            stray.parent.mkdir()
            stray.write_text("", encoding="utf-8")
            env = self._env(tmp, project=project)
            self.mod.reject_installed_header(str(stray), str(env))  # 黑名单放行
            with self.assertRaises(self.mod.AlignError) as ctx:
                self.mod.require_project_source(str(stray), str(env), "--header")
            self.assertIn("待验收算子工程目录", str(ctx.exception))

    def test_missing_operator_project_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = self._env(tmp, cann_home=tmp / "cann")
            with self.assertRaises(self.mod.AlignError) as ctx:
                self.mod.require_project_source("/tmp/x.h", str(env), "--header")
            self.assertIn("--op-repo", str(ctx.exception))

    def test_unreadable_env_is_not_a_pass(self):
        with self.assertRaises(self.mod.AlignError):
            self.mod.require_project_source("/tmp/x.h", "/tmp/no-such-env.json", "--header")


class SignatureProvenanceTest(unittest.TestCase):
    """`--signature` 是手抄的声明，以前完全绕过出处核对——把内置头文件里的
    一段粘进去，门禁一点都拦不住，`signature_source` 还只记成 `--signature`。"""

    def _run(self, extra_argv, project_exists=True):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            out = temp / "alignment.json"
            project = temp / "op_project"
            if project_exists:
                project.mkdir()
            env = temp / "env.json"
            env.write_text(json.dumps({"operator_project": {"path": str(project)}}),
                           encoding="utf-8")
            if project_exists:
                (project / "aclnn_roll.h").write_text(TAIL_OUTPUT + ";\n",
                                                      encoding="utf-8")
            argv = ["align", "--baseline", "torch.roll", "--signature", TAIL_OUTPUT,
                    "--env", str(env), "-o", str(out)]
            argv += [a.replace("<TMP>", str(temp)) for a in extra_argv]
            driver = temp / "driver.py"
            driver.write_text(
                STUB + textwrap.dedent(f'''
                    sys.path.insert(0, {str(SCRIPT.parent)!r})
                    sys.argv = {argv!r}
                    import align_signatures
                    sys.exit(align_signatures.main())
                '''), encoding="utf-8")
            done = subprocess.run([sys.executable, str(driver)],
                                  capture_output=True, text=True, timeout=60)
            report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
            return done, report

    def test_signature_without_source_is_rejected(self):
        done, report = self._run([])
        self.assertEqual(done.returncode, 2)
        self.assertIn("--signature-source", done.stderr)
        self.assertIsNone(report)

    def test_signature_source_outside_project_is_rejected(self):
        Path("/tmp/stray_aclnn.h").write_text("", encoding="utf-8")
        done, _ = self._run(["--signature-source", "/tmp/stray_aclnn.h"])
        self.assertEqual(done.returncode, 2)
        self.assertIn("待验收算子工程目录", done.stderr)

    def test_signature_source_inside_project_records_the_path(self):
        done, report = self._run(["--signature-source", "<TMP>/op_project/aclnn_roll.h"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(report["signature_source"].endswith("op_project/aclnn_roll.h"))


class AclnnNameNormalisationTest(unittest.TestCase):
    """`aclnn_name` 在两个门禁里必须是同一个语义。

    模板与 make_yaml 写的都是不带前缀的 `Roll`，op_api 绑定门禁会补成
    `aclnnRoll`；签名对齐这一侧以前直接拼 `RollGetWorkspaceSize`，
    于是同一个字段一个门禁过、另一个挂，真机上白跑一轮。
    """

    def _header(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".h", delete=False)
        handle.write(TAIL_OUTPUT + ";\n")
        handle.close()
        return handle.name

    def test_bare_name_finds_the_prefixed_symbol(self):
        from align_signatures import read_header_signature

        signature, _ = read_header_signature(self._header(), "Roll")
        self.assertIn("aclnnRollGetWorkspaceSize", signature)

    def test_prefixed_name_still_works(self):
        from align_signatures import read_header_signature

        signature, _ = read_header_signature(self._header(), "aclnnRoll")
        self.assertIn("aclnnRollGetWorkspaceSize", signature)

    def test_both_spellings_agree_with_the_binding_gate(self):
        from _opapi_binding import _symbol_prefix
        from align_signatures import read_header_signature

        header = self._header()
        for spelling in ("Roll", "aclnnRoll"):
            with self.subTest(aclnn_name=spelling):
                signature, _ = read_header_signature(header, spelling)
                self.assertIn(f"{_symbol_prefix(spelling)}GetWorkspaceSize",
                              signature)


if __name__ == "__main__":
    unittest.main()
