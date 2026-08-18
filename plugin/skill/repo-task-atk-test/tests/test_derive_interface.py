"""接口事实由 S1 派生一次，后面只读不再声明。

roll 那轮在 S4 卡死：manifest 声明 execution_backend=aclnn，报告实测 pyaclnn。
成因不是填错，是 skill 把一个可推导的事实做成了选择题——
acceptance-policy 里 aclnn 的 backends 写着 ["pyaclnn", "aclnn"]，
agent 没有任何依据知道该填哪个。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import derive_interface  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "derive_interface.py"


class BaselineScopeTest(unittest.TestCase):
    """基线接口必须是 CPU 基线节点执行期真能 eval 出来的符号。

    作用域不是约定而是硬事实：atk/tasks/api_execute/function_api.py
    模块级只 import 了 math、torch、torch.distributed as dist 和
    可选的 torch_npu，基线节点执行的正是该模块作用域里的 eval(baseline_api)。
    """

    def test_scope_roots_match_function_api_imports(self):
        self.assertEqual(set(derive_interface.BASELINE_SCOPE_ROOTS),
                         {"torch", "torch_npu", "math", "dist"})

    def test_every_scope_root_is_accepted(self):
        for api in ("torch.roll", "torch.nn.functional.relu",
                    "torch_npu.npu_rotary_mul", "math.fsum", "dist.all_reduce"):
            self.assertIsNone(
                derive_interface.check_baseline_api(api, "aclnnRoll", "aclnn"), api)

    def test_out_of_scope_root_is_rejected(self):
        for api in ("np.roll", "numpy.roll", "custom.roll"):
            self.assertIsNotNone(
                derive_interface.check_baseline_api(api, "aclnnRoll", "aclnn"), api)

    def test_dotted_path_root_is_parsed_not_the_whole_string(self):
        self.assertIsNone(
            derive_interface.check_baseline_api("torch.Tensor.roll", "aclnnRoll", "aclnn"))

    def test_empty_baseline_rejected(self):
        for api in ("", "   "):
            self.assertIsNotNone(
                derive_interface.check_baseline_api(api, "aclnnRoll", "aclnn"))

    def test_self_comparison_rejected_in_aclnn_mode(self):
        warn = derive_interface.check_baseline_api("Roll", "Roll", "aclnn")
        self.assertIsNotNone(warn)
        self.assertIn("自己当标杆", warn)

    def test_same_name_on_both_sides_is_normal_in_pytorch_mode(self):
        # pytorch 模式下两侧差在设备（npu / cpu）不在符号。
        self.assertIsNone(
            derive_interface.check_baseline_api("torch.roll", "torch.roll", "pytorch"))


class BaselineShapeTest(unittest.TestCase):
    """基线是三件独立的事：符号是什么、属于哪一类、跑在哪个后端。

    前两件以前被硬编码成「torch 表达式 + cpu」，用户提出的其它约束
    （基线跑 npu、基线用 CANN 内置实现对比）落在 ATK 能力域内却没有声明出口。
    """

    def test_defaults_keep_the_old_behaviour(self):
        self.assertEqual(derive_interface.DEFAULT_BASELINE_KIND, "torch")
        self.assertEqual(derive_interface.DEFAULT_BASELINE_DEVICE, "cpu")

    def test_torch_kind_accepts_both_devices(self):
        # 基线节点在 ATK 里只是「复制主节点、改 backend 字段」，
        # 没有任何地方规定它必须是 cpu。
        for device in ("cpu", "npu"):
            self.assertIsNone(
                derive_interface.check_baseline_shape("torch", device, ""), device)

    def test_unknown_kind_or_device_rejected(self):
        self.assertIsNotNone(
            derive_interface.check_baseline_shape("numpy", "cpu", ""))
        self.assertIsNotNone(
            derive_interface.check_baseline_shape("torch", "gpu", ""))

    def test_cann_builtin_requires_a_source(self):
        # 唯一的显式开关：没人明确要求，就永远走不到链接内置实现这条路。
        warn = derive_interface.check_baseline_shape("cann_builtin", "npu", "")
        self.assertIsNotNone(warn)
        self.assertIn("--baseline-source", warn)

    def test_cann_builtin_with_source_passes(self):
        self.assertIsNone(derive_interface.check_baseline_shape(
            "cann_builtin", "cpu", "任务书 §3 要求与内置实现对比"))

    def test_cann_builtin_baseline_node_is_the_cpu_load_node(self):
        # 判定发生在第二轮，那一轮的基线节点是 `-b cpu --task accuracy_load`。
        # 内置实现跑在 npu 上是第一轮的事，两者不是一回事——写成 npu 的话
        # verdict.py 会在报告里找不到 npu 节点，100% 拒绝出结论。
        self.assertIsNone(derive_interface.check_baseline_shape(
            "cann_builtin", "cpu", "用户 2026-08-16 指定"))

    def test_cann_builtin_rejects_npu_as_the_baseline_node(self):
        warn = derive_interface.check_baseline_shape(
            "cann_builtin", "npu", "用户 2026-08-16 指定")
        self.assertIsNotNone(warn)
        self.assertIn("accuracy_load", warn)

    def test_cann_builtin_skips_scope_and_self_comparison_checks(self):
        # C 符号不可 eval，拿作用域根名量它没意义；待验收算子与内置本就是
        # 同一个公开符号的新旧两版实现，不是自比。
        self.assertIsNone(derive_interface.check_baseline_api(
            "aclnnBernoulli", "aclnnBernoulli", "aclnn", "cann_builtin"))

    def test_torch_kind_still_enforces_both_checks(self):
        self.assertIsNotNone(derive_interface.check_baseline_api(
            "aclnnBernoulli", "aclnnBernoulli", "aclnn", "torch"))

    def test_out_of_scope_hint_points_at_the_builtin_switch(self):
        # 报错要告诉 agent 出口在哪，否则它只会退回去改 --baseline 猜一个 torch 名字。
        warn = derive_interface.check_baseline_api(
            "aclnnBernoulli", "aclnnFoo", "aclnn", "torch")
        self.assertIn("--baseline-kind cann_builtin", warn)


class BackendDerivationTest(unittest.TestCase):
    def test_aclnn_interface_always_runs_on_pyaclnn(self):
        # AclnnBackend 要 aclnnTest C++ 扩展并逐算子绑定，社区算子验收用不了。
        self.assertEqual(derive_interface.BACKEND_BY_MODE["aclnn"], "pyaclnn")

    def test_every_open_mode_maps_to_exactly_one_backend(self):
        for mode, backend in derive_interface.BACKEND_BY_MODE.items():
            with self.subTest(mode=mode):
                self.assertIsInstance(backend, str)
                self.assertTrue(backend)

    def test_policy_no_longer_offers_a_choice(self):
        policy = json.loads(
            (SKILL_ROOT / "references" / "acceptance-policy.json")
            .read_text(encoding="utf-8"))
        for mode, block in policy["interface_modes"].items():
            if not block.get("acceptance_enabled"):
                continue
            with self.subTest(mode=mode):
                self.assertEqual(len(block["backends"]), 1,
                                 f"{mode} 仍是选择题，agent 无从判断该填哪个")

    def test_policy_and_script_agree(self):
        policy = json.loads(
            (SKILL_ROOT / "references" / "acceptance-policy.json")
            .read_text(encoding="utf-8"))
        for mode, block in policy["interface_modes"].items():
            if not block.get("acceptance_enabled"):
                continue
            with self.subTest(mode=mode):
                self.assertEqual(derive_interface.BACKEND_BY_MODE[mode],
                                 block["backends"][0])


class DeriveCliTest(unittest.TestCase):
    NON_RANDOM_TASK_DOC = "roll 算子开发任务书：把张量沿指定轴滚动。"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.task_doc = Path(self._tmp.name) / "task_doc.md"
        self.task_doc.write_text(self.NON_RANDOM_TASK_DOC, encoding="utf-8")

    def _write_task_doc(self, text):
        self.task_doc.write_text(text, encoding="utf-8")

    def _run(self, *args, task_doc=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "interface.json"
            full_args = list(args)
            if "--task-doc" not in full_args:
                full_args += ["--task-doc", str(task_doc or self.task_doc)]
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *full_args, "-o", str(out)],
                capture_output=True, text=True)
            payload = json.loads(out.read_text(encoding="utf-8")) \
                if out.exists() else None
            return result, payload

    def test_derives_backend_without_asking(self):
        result, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["execution_backend"], "pyaclnn")
        self.assertEqual(payload["baseline_backend"], "cpu")
        self.assertEqual(payload["interface_mode"], "aclnn")
        self.assertFalse(payload["random_operator"]["detected"])

    def test_candidate_and_baseline_must_differ(self):
        # 任务书常同时写「对标 aclnnXxx」和「等价于 torch.xxx」，把 aclnnXxx
        # 填成基线会拿待验收算子给自己当标杆，报告必然全过而毫无意义。
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "torch.roll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("与待验收算子要提交的接口名相同", result.stderr)

    def test_baseline_outside_execution_scope_is_refused(self):
        # 基线节点执行 eval(baseline_api)，只认得 function_api.py 实际 import
        # 的那几个顶层名字；填一个它解析不了的根名会在执行期直接 NameError，
        # 而不是在 S1 当场拦下——这个检查就是把那次 NameError 提前。
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "numpy.roll", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("不在基线执行作用域里", result.stderr)

    def test_closed_mode_is_refused(self):
        result, _ = self._run(
            "--mode", "triton", "--candidate", "x",
            "--baseline", "torch.x", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("暂停验收", result.stderr)

    def test_mode_source_is_required(self):
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "")
        self.assertEqual(result.returncode, 2)

    def test_task_doc_missing_is_refused(self):
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2",
            task_doc="/nonexistent/task_doc.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("任务书读取失败", result.stderr)

    def test_baseline_defaults_are_recorded_without_being_asked_for(self):
        # 不传新参数的存量算子，产物必须和以前一致。
        _, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2")
        self.assertEqual(payload["baseline_kind"], "torch")
        self.assertEqual(payload["baseline_backend"], "cpu")
        self.assertIsNone(payload["baseline_source"])

    def test_torch_baseline_can_run_on_npu(self):
        result, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2",
            "--baseline-device", "npu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["baseline_backend"], "npu")
        self.assertEqual(payload["baseline_kind"], "torch")

    def test_cann_builtin_baseline_passes_with_source(self):
        result, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnBernoulli",
            "--baseline", "aclnnBernoulli", "--mode-source", "任务书 §2",
            "--baseline-kind", "cann_builtin", "--baseline-device", "cpu",
            "--baseline-source", "用户 2026-08-16 指定与内置实现对比")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["baseline_kind"], "cann_builtin")
        # 判定那一轮的基线节点是读盘的 cpu 节点，不是跑内置的 npu
        self.assertEqual(payload["baseline_backend"], "cpu")
        self.assertEqual(payload["builtin_runs_on"], "npu")
        self.assertIn("用户", payload["baseline_source"])

    def test_cann_builtin_without_source_is_refused(self):
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "aclnnBernoulli",
            "--baseline", "aclnnBernoulli", "--mode-source", "任务书 §2",
            "--baseline-kind", "cann_builtin", "--baseline-device", "cpu")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--baseline-source", result.stderr)


class BaselineNodeBackendNamingTest(DeriveCliTest):
    """参数名要说的是「判定那轮谁当基线节点」，不是「基线跑在哪个设备」。

    `--baseline-device` 字面读是后者，于是 `cann_builtin` 只能填 `cpu` 这件事
    读起来像「内置算子跑在 CPU 上」——而内置实现确实跑在 NPU 上（第一轮的
    pyaclnn 节点）。填 cpu 说的是判定那一轮的基线节点是从磁盘读真值的
    `node -b cpu --task accuracy_load`，它不计算任何东西。

    产物里的字段早就叫 `baseline_backend`，只有 CLI 参数名还留着 device，
    两边说的是同一件事却用了两个词。这里让参数名向字段看齐。
    """

    def test_backend_flag_is_accepted(self):
        result, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2",
            "--baseline-node-backend", "npu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["baseline_backend"], "npu")

    def test_old_device_flag_still_works(self):
        # 别名不是改名：改名会让写好的 repro.sh 与既有证据链一起挂。
        result, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2",
            "--baseline-device", "npu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["baseline_backend"], "npu")

    def test_help_explains_that_builtin_itself_runs_on_npu(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, timeout=30)
        self.assertIn("--baseline-node-backend", result.stdout)
        # 只写「只能是 cpu」会被读成「内置跑在 CPU 上」，必须同时说清
        # 内置实现本身跑在哪。
        self.assertIn("npu", result.stdout)


class RandomOperatorGateTest(unittest.TestCase):
    """随机算子（bernoulli 那轮）：不许现场探测证明/证伪 CPU-NPU 同 seed 可比性，
    命中信号词就必须已经拿到用户给的具体策略，否则退出码 2 逼回 S1 去问。
    """

    BERNOULLI_TASK_DOC = (
        "aclnnBernoulli 算子开发任务书。\n"
        "特别注意事项：\n"
        "4. 随机采样算子精度比对需采用合理的统计/固定种子策略。\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.task_doc = Path(self._tmp.name) / "task_doc.md"

    def _run(self, extra_args, task_doc_text):
        self.task_doc.write_text(task_doc_text, encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "interface.json"
            args = [
                "--mode", "aclnn", "--candidate", "aclnnBernoulli",
                "--baseline", "torch.bernoulli", "--mode-source", "任务书",
                "--task-doc", str(self.task_doc),
                *extra_args,
            ]
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *args, "-o", str(out)],
                capture_output=True, text=True)
            payload = json.loads(out.read_text(encoding="utf-8")) \
                if out.exists() else None
            return result, payload

    def test_signal_word_detected_from_task_doc(self):
        signals = json.loads(
            (SKILL_ROOT / "references" / "random-operator-signals.json")
            .read_text(encoding="utf-8"))
        matched = derive_interface.detect_random_operator(
            self.BERNOULLI_TASK_DOC, signals)
        self.assertIn("bernoulli", matched)

    def test_bernoulli_keyword_is_registered(self):
        # 这条算子名信号必须留在词表里，不能在维护时被误删——
        # roll 那轮的表漂移就是靠字段级测试钉住的，这里用同样的方式钉。
        signals = json.loads(
            (SKILL_ROOT / "references" / "random-operator-signals.json")
            .read_text(encoding="utf-8"))
        self.assertIn("bernoulli", signals["signal_keywords"])

    def test_random_operator_without_strategy_is_refused(self):
        result, _ = self._run([], self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 2)
        self.assertIn("未给出精度对比策略", result.stderr)
        self.assertIn("不要自己写探针脚本", result.stderr)

    def test_strategy_without_source_is_refused(self):
        result, _ = self._run(
            ["--random-strategy",
             "只测 prob=0/1 确定性边界，equal 逐元素比对"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 2)
        self.assertIn("缺依据", result.stderr)

    def test_free_text_strategy_is_refused_with_the_menu(self):
        # 任务书那句「合理的统计/固定种子策略」读起来像个判据，但没有一步
        # 能照它执行。早先靠字符串比对挡原句转述，换个说法就绕过去了；
        # 现在只认受控取值，填不进词表就把菜单列出来。
        result, _ = self._run(
            ["--random-strategy",
             "随机采样算子精度比对需采用合理的统计/固定种子策略",
             "--random-strategy-source", "任务书原文"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 2)
        self.assertIn("不是受控取值", result.stderr)
        self.assertIn("equal_vs_builtin_pinned_seed", result.stderr)

    def test_controlled_strategy_passes_and_is_recorded(self):
        result, payload = self._run(
            ["--random-strategy", "deterministic_boundary_only",
             "--random-strategy-source", "S1 用户裁决 2026-08-16"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = payload["random_operator"]
        self.assertTrue(block["detected"])
        self.assertIn("bernoulli", block["matched_keywords"])
        self.assertEqual("deterministic_boundary_only", block["strategy"])
        self.assertEqual(block["strategy_source"], "S1 用户裁决 2026-08-16")

    def test_pinned_seed_strategy_needs_seed_parameters(self):
        # 逐位比对的前提是两轮拿到同一条随机数流，接口里没有种子入参就做不到。
        result, _ = self._run(
            ["--random-strategy", "equal_vs_builtin_pinned_seed",
             "--random-strategy-source", "用户 2026-08-17 指定",
             "--baseline-kind", "cann_builtin",
             "--baseline-source", "用户要求与内置对比",
             "--baseline", "aclnnBernoulli"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--seed-parameters", result.stderr)

    def test_pinned_seed_strategy_requires_the_builtin_baseline(self):
        # 忘了 --baseline-kind cann_builtin 的话，四处判据全不触发，
        # 最后盖的结论是「精度达标」，而实际比的是内置——假结论。
        result, _ = self._run(
            ["--random-strategy", "equal_vs_builtin_pinned_seed",
             "--random-strategy-source", "用户 2026-08-17 指定",
             "--seed-parameters", "seed,offset"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--baseline-kind cann_builtin", result.stderr)

    def test_pinned_seed_strategy_passes_with_seed_parameters(self):
        result, payload = self._run(
            ["--random-strategy", "equal_vs_builtin_pinned_seed",
             "--random-strategy-source", "用户 2026-08-17 指定",
             "--seed-parameters", "seed,offset",
             "--baseline-kind", "cann_builtin",
             "--baseline-source", "用户要求与内置对比",
             "--baseline", "aclnnBernoulli"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(["seed", "offset"], payload["seed_parameters"])

    def test_distribution_test_does_not_need_seed_parameters(self):
        result, payload = self._run(
            ["--random-strategy", "distribution_test",
             "--random-strategy-source", "任务书 §3 给了判定公式"],
            self.BERNOULLI_TASK_DOC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([], payload["seed_parameters"])

    def test_non_random_operator_is_not_gated(self):
        result, payload = self._run([], "roll 算子开发任务书：沿轴滚动张量。")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(payload["random_operator"]["detected"])
        self.assertIsNone(payload["random_operator"]["strategy"])


if __name__ == "__main__":
    unittest.main()
