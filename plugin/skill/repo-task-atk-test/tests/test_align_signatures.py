"""align_signatures.py 的机械层回归测试。

真实事故：适配器的参数顺序、出参位置和空指针类型全靠 agent 从头文件推断，
一次写对的概率很低，错了要到部署后冒烟才报 `类型校验失败`。

这里锁住机械层的三条判定：出参不在末尾必须要求适配器、出参在末尾不要求、
以及拿不准的东西必须进 semantic_review 而不是被静默判定。

本机没有 ATK 和 torch，用桩替代——被桩的只有两张映射表和基线接口，
签名解析逻辑本身是被真实测到的。
"""

import ctypes
import hashlib
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

MULTILINE_TAIL_OUTPUT = """aclnnStatus aclnnRollGetWorkspaceSize(
    const aclTensor   *self,
    const aclIntArray *shifts,
    const aclIntArray *dims,
    aclTensor         *out,
    uint64_t          *workspaceSize,
    aclOpExecutor    **executor)"""

ROLL_EXECUTE = """aclnnStatus aclnnRoll(
    void          *workspace,
    uint64_t       workspaceSize,
    aclOpExecutor *executor,
    aclrtStream    stream)"""

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


def _run_stubbed(temp, argv):
    """用真实 CLI 参数跑脚本，只把 torch 与 ATK 的运行时边界换成桩。"""
    out = temp / "alignment.json"
    driver = temp / "driver.py"
    command = ["align", "--baseline", "torch.roll", *argv,
               "-o", str(out)]
    driver.write_text(
        STUB + textwrap.dedent(f'''
            sys.path.insert(0, {str(SCRIPT.parent)!r})
            sys.argv = {command!r}
            import align_signatures
            sys.exit(align_signatures.main())
        '''), encoding="utf-8")
    done = subprocess.run([sys.executable, str(driver)],
                          capture_output=True, text=True, timeout=60)
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return done, report


def task_doc(signature=TAIL_OUTPUT):
    return ("# Roll 算子任务书\n\n### 2.3 接口定义\n\n```\n"
            f"{signature}\n\n{ROLL_EXECUTE}\n```\n")


def run_taskdoc(text, extra_argv=()):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        path = temp / "task.md"
        path.write_text(text, encoding="utf-8")
        return _run_stubbed(
            temp, ["--task-doc", str(path), *extra_argv])


def run_header(include_env=True):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "op_project"
        project.mkdir()
        header = project / "aclnn_roll.h"
        header.write_text(TAIL_OUTPUT + ";\n", encoding="utf-8")
        argv = ["--header", str(header), "--aclnn-name", "Roll"]
        if include_env:
            env = temp / "env.json"
            env.write_text(
                json.dumps({"operator_project": {"path": str(project)}}),
                encoding="utf-8")
            argv.extend(["--env", str(env)])
        return _run_stubbed(temp, argv)


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


class TaskDocSignatureModeTest(unittest.TestCase):
    def test_manual_and_taskdoc_routes_produce_the_same_alignment(self):
        manual_done, manual = run_align("torch.roll", TAIL_OUTPUT)
        task_done, from_task = run_taskdoc(task_doc())

        self.assertEqual(manual_done.returncode, 0, manual_done.stderr)
        self.assertEqual(task_done.returncode, 0, task_done.stderr)
        for key in ("aclnn", "aclnn_adapter", "baseline_adapter"):
            self.assertEqual(manual[key], from_task[key], key)
        without_provenance = lambda report: {
            key: value for key, value in report.items()
            if key not in {"source", "signature_source"}
        }
        self.assertEqual(without_provenance(manual),
                         without_provenance(from_task))
        self.assertNotEqual(manual["source"], from_task["source"])
        self.assertNotEqual(manual["signature_source"],
                            from_task["signature_source"])

    def test_taskdoc_route_needs_no_env_and_records_the_original_file_hash(self):
        text = task_doc()
        done, report = run_taskdoc(text)

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(report["source"]["kind"], "taskdoc")
        self.assertTrue(report["source"]["path"].endswith("task.md"))
        self.assertEqual(report["source"]["path"], report["signature_source"])
        self.assertEqual(
            report["source"]["sha256"],
            hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertNotIn("aclnnRoll(", report["signature"])

    def test_taskdoc_route_ignores_env_even_when_the_path_is_unreadable(self):
        done, report = run_taskdoc(
            task_doc(), ("--env", "/definitely/not/an/env.json"))

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(report["source"]["kind"], "taskdoc")

    def test_manual_route_still_requires_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "aclnn_roll.h"
            source.write_text(TAIL_OUTPUT + ";\n", encoding="utf-8")
            done, report = _run_stubbed(
                temp, ["--signature", TAIL_OUTPUT,
                       "--signature-source", str(source)])

        self.assertEqual(done.returncode, 2)
        self.assertRegex(done.stderr, r"env|probe_env")
        self.assertIsNone(report)

    def test_header_route_still_requires_env(self):
        done, report = run_header(include_env=False)

        self.assertEqual(done.returncode, 2)
        self.assertRegex(done.stderr, r"env|probe_env")
        self.assertIsNone(report)

    def test_header_route_records_its_source_kind_and_path(self):
        done, report = run_header()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(report["source"]["kind"], "header")
        self.assertEqual(report["source"]["path"], report["signature_source"])
        self.assertTrue(report["source"]["path"].endswith("aclnn_roll.h"))

    def test_two_signature_routes_are_rejected_before_partial_route_checks(self):
        done, report = run_taskdoc(
            task_doc(), ("--signature", TAIL_OUTPUT))

        self.assertEqual(done.returncode, 2)
        self.assertIn("只能选一条", done.stderr)
        self.assertIsNone(report)

    def test_missing_section_2_3_keeps_the_taskdoc_actionable_error(self):
        done, report = run_taskdoc(
            "# Roll 算子任务书\n\n### 2.4 参数说明\n\n正文\n")

        self.assertEqual(done.returncode, 2)
        self.assertIn("§2.3", done.stderr)
        self.assertIn("repo-task-doc-write", done.stderr)
        self.assertIsNone(report)

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

    def test_unresolvable_baseline_is_rejected(self):
        done, _ = run_align("numpy.roll", TAIL_OUTPUT)
        self.assertEqual(done.returncode, 2)
        self.assertIn("torch", done.stderr)


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
        self.assertEqual(report["source"], {
            "kind": "manual", "path": report["signature_source"]})


if __name__ == "__main__":
    unittest.main()
