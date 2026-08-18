"""内置那一轮的取证与搬运。

日志里那一行是 info 级、默认级别就打得出来
（atk/tasks/backends/pyaclnn_backend.py:246），它是「这轮到底加载了哪份库」
的唯一机械依据。搬运则要把目录名改成 load 节点认得的形状
（atk/common/utils.py:259）。
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

import capture_reference as cr  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "capture_reference.py"


def temp_dir(case):
    """用例结束时自动清理的临时目录。不用 enterContext：真机解释器可能是 3.9。"""
    holder = tempfile.TemporaryDirectory()
    case.addCleanup(holder.cleanup)
    return Path(holder.name)


def real_library(directory, name="libopapi_nn.so", content=b"builtin-bytes"):
    """在盘上真建一份库文件，连同它此刻的 sha256。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()

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

    def test_the_same_library_repeated_is_still_one_answer(self):
        # 每条用例都会打一行，都指同一份库，不算矛盾。
        self.assertEqual(
            "/usr/local/Ascend/latest/opp/../aarch64-linux/lib64/libopapi_nn.so",
            cr.loaded_library(LOG + LOG + LOG, "aclnnBernoulli"))

    def test_two_different_libraries_in_one_log_are_refused(self):
        # 同一个算子绑到了两份库：日志串了，或者跑测中途重绑过。
        text = LOG + ("[INFO] import aclnnBernoulliGetWorkspaceSize  "
                      "from /vendor/libcust_opapi.so success!\n")
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.loaded_library(text, "aclnnBernoulli")
        message = str(ctx.exception)
        self.assertIn("/vendor/libcust_opapi.so", message)
        self.assertIn("libopapi_nn.so", message)

    def test_path_with_spaces_still_matches(self):
        text = ("[INFO] import aclnnBernoulliGetWorkspaceSize  from "
                "/opt/my libs/lib64/libopapi_nn.so success!\n")
        self.assertEqual("/opt/my libs/lib64/libopapi_nn.so",
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
        cases = cr.stage_golden(str(self.run_output), str(staged))
        self.assertEqual(["0", "1"], cases)
        self.assertEqual(
            b"golden0",
            (staged / "cpu_0" / "bernoulli_cases" / "0" / "output_0.pt").read_bytes())

    def test_staged_tree_is_exactly_the_aclnn_side(self):
        # CPU 侧是 torch 基线的输出，出参精度被上调过，搬它复跑会被算子拒绝。
        # 「搬过去的文件恰好是这些」才既挡住漏搬，也挡住两个目录都搬。
        staged = self.tmp / "golden_builtin"
        cr.stage_golden(str(self.run_output), str(staged))
        moved = {str(path.relative_to(staged))
                 for path in staged.rglob("*") if path.is_file()}
        self.assertEqual(
            {"cpu_0/bernoulli_cases/0/output_0.pt",
             "cpu_0/bernoulli_cases/1/output_0.pt"},
            moved)

    def test_a_symlink_sitting_on_the_target_is_refused(self):
        staged = self.tmp / "golden_builtin"
        staged.mkdir()
        elsewhere = self.tmp / "somewhere_else"
        elsewhere.mkdir()
        (staged / "cpu_0").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.stage_golden(str(self.run_output), str(staged))
        self.assertIn("软链接", str(ctx.exception))
        # 没有顺手删掉别人的东西。
        self.assertTrue(elsewhere.is_dir())

    def test_a_plain_file_sitting_on_the_target_is_refused(self):
        staged = self.tmp / "golden_builtin"
        staged.mkdir()
        (staged / "cpu_0").write_text("not a directory", encoding="utf-8")
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.stage_golden(str(self.run_output), str(staged))
        self.assertIn("普通文件", str(ctx.exception))

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
    def setUp(self):
        self.tmp = temp_dir(self)
        self.lib, self.sha = real_library(self.tmp / "opp" / "lib64")
        self.case_json = self.tmp / "bernoulli_cases.json"
        self.case_json.write_text('{"cases": []}', encoding="utf-8")

    def build(self, **overrides):
        kwargs = dict(
            op="aclnnBernoulli",
            library=str(self.lib),
            fingerprint={"side": "builtin", "path": str(self.lib),
                         "sha256": self.sha, "op": "aclnnBernoulli"},
            case_json=str(self.case_json),
            input_data="frozen_main",
            staged_dir=str(self.tmp / "golden_builtin"),
            cases=["0", "1"])
        kwargs.update(overrides)
        return cr.build_provenance(**kwargs)

    def test_provenance_carries_every_evidence_item(self):
        got = self.build()
        self.assertEqual(str(self.lib), got["builtin_library"]["path"])
        self.assertEqual(self.sha, got["builtin_library"]["sha256"])
        self.assertEqual(["0", "1"], got["cases"])
        self.assertEqual("frozen_main", got["input_data"])
        self.assertIsNone(got["counter_experiment"])
        self.assertEqual(64, len(got["case_json_sha256"]))

    def test_library_mismatch_between_log_and_fingerprint_is_refused(self):
        other, other_sha = real_library(self.tmp / "vendors" / "lib",
                                        "libcust_opapi.so", b"vendor-bytes")
        with self.assertRaises(cr.CaptureError) as ctx:
            self.build(library=str(other))
        self.assertIn("日志里加载的是", str(ctx.exception))
        self.assertNotEqual(self.sha, other_sha)

    # --- 哪一侧 --------------------------------------------------------

    def test_a_candidate_side_fingerprint_is_refused(self):
        """两轮跑同一份 vendor 库就是自己跟自己比，这是这条路要挡的失败。"""
        with self.assertRaises(cr.CaptureError) as ctx:
            self.build(fingerprint={"side": "candidate", "path": str(self.lib),
                                    "sha256": self.sha, "op": "aclnnBernoulli"})
        message = str(ctx.exception)
        self.assertIn("candidate", message)
        self.assertIn("--side builtin", message)

    def test_a_fingerprint_without_a_side_is_refused(self):
        with self.assertRaises(cr.CaptureError):
            self.build(fingerprint={"path": str(self.lib), "sha256": self.sha})

    def test_a_fingerprint_for_another_operator_is_refused(self):
        with self.assertRaises(cr.CaptureError) as ctx:
            self.build(fingerprint={"side": "builtin", "path": str(self.lib),
                                    "sha256": self.sha, "op": "aclnnMedian"})
        self.assertIn("aclnnMedian", str(ctx.exception))

    def test_a_fingerprint_without_an_op_key_still_passes(self):
        # op 这个键是 resolve_opp_library.py 的 CLI 才加的，缺了不算证据矛盾。
        got = self.build(fingerprint={"side": "builtin", "path": str(self.lib),
                                      "sha256": self.sha})
        self.assertEqual("aclnnBernoulli", got["op"])

    # --- 盘上这一刻的文件 -----------------------------------------------

    def test_the_library_content_is_rechecked_against_the_fingerprint(self):
        """路径没变、文件被重装覆盖过：只比路径的话察觉不到。"""
        self.lib.write_bytes(b"reinstalled-bytes")
        with self.assertRaises(cr.CaptureError) as ctx:
            self.build()
        message = str(ctx.exception)
        self.assertIn("sha256", message)
        self.assertIn(self.sha, message)

    def test_a_library_that_is_gone_is_refused_not_crashed(self):
        self.lib.unlink()
        with self.assertRaises(cr.CaptureError) as ctx:
            self.build()
        self.assertIn(str(self.lib), str(ctx.exception))

    def test_a_symlinked_path_is_the_same_library(self):
        """CANN 装机路径里的 latest 就是软链接，同一份库两种写法要认得出。"""
        link = self.tmp / "latest_lib64"
        link.symlink_to(self.lib.parent, target_is_directory=True)
        got = self.build(library=str(link / self.lib.name))
        self.assertEqual(self.sha, got["builtin_library"]["sha256"])

    def test_a_dot_dot_path_is_the_same_library(self):
        # 日志里的路径长成 /opp/../aarch64-linux/lib64/xxx.so 这样。
        detoured = self.tmp / "opp" / "lib64" / ".." / "lib64" / self.lib.name
        got = self.build(library=str(detoured))
        self.assertEqual(self.sha, got["builtin_library"]["sha256"])


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


class TamperValidationTest(unittest.TestCase):
    """tamper() 的两条前置校验分支不需要 torch 就能走到——

    import torch 已经挪到 torch.load() 前面，晚于这两处检查，
    所以本机没装 torch 也能真验证它们抛的是 CaptureError 而不是
    ModuleNotFoundError。
    """

    def test_missing_node_root_is_refused(self):
        tmp = temp_dir(self)
        staged = tmp / "does_not_have_cpu_0"
        staged.mkdir()
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.tamper(str(staged), "3")
        self.assertIn(str(staged / "cpu_0"), str(ctx.exception))

    def test_unknown_case_id_is_refused_without_torch(self):
        tmp = temp_dir(self)
        node_root = tmp / "golden" / "cpu_0" / "cases" / "0"
        node_root.mkdir(parents=True)
        (node_root / "output_0.pt").write_bytes(b"golden0")
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.tamper(str(tmp / "golden"), "99")
        message = str(ctx.exception)
        self.assertIn("99", message)
        self.assertIn("0", message)


class RestoreTest(unittest.TestCase):
    """restore() 不需要 torch 就能验证：直接建 .orig 文件核对还原。"""

    def test_orig_backup_is_moved_back_into_place(self):
        tmp = temp_dir(self)
        case_dir = tmp / "golden" / "cpu_0" / "cases" / "3"
        case_dir.mkdir(parents=True)
        target = case_dir / "output_0.pt"
        backup = case_dir / "output_0.pt.orig"
        target.write_bytes(b"tampered")
        backup.write_bytes(b"original")

        restored = cr.restore(str(tmp / "golden"))

        self.assertEqual([str(target)], restored)
        self.assertEqual(b"original", target.read_bytes())
        self.assertFalse(backup.exists())

    def test_no_backups_present_is_a_noop(self):
        tmp = temp_dir(self)
        (tmp / "golden").mkdir()
        self.assertEqual([], cr.restore(str(tmp / "golden")))


class CliTest(unittest.TestCase):
    """真跑子进程，检查退出码只落在 0/2/3 上、取证 JSON 真写出来。"""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.lib, self.sha = real_library(self.tmp / "opp" / "lib64")

    def make_run(self, case_ids=("0",)):
        run_output = self.tmp / "output"
        for case_id in case_ids:
            case_dir = run_output / "pyaclnn_0" / "cases" / case_id
            case_dir.mkdir(parents=True)
            (case_dir / "output_0.pt").write_bytes(b"g" + case_id.encode())
        return run_output

    def make_log(self, library=None):
        log = self.tmp / "builtin.log"
        log.write_text(
            "[INFO] import aclnnBernoulliGetWorkspaceSize  from "
            f"{library or self.lib} success!\n", encoding="utf-8")
        return log

    def make_fingerprint(self, **overrides):
        payload = {"side": "builtin", "path": str(self.lib),
                   "sha256": self.sha, "op": "aclnnBernoulli"}
        payload.update(overrides)
        path = self.tmp / "fp.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def make_case_json(self, body='{"cases": [{"id": 0}]}'):
        path = self.tmp / "cases.json"
        path.write_text(body, encoding="utf-8")
        return path

    def run_cli(self, run_output, log, fingerprint, case_json, output=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT),
             "--op", "aclnnBernoulli", "--from-run", str(run_output),
             "--log", str(log), "--case-json", str(case_json),
             "--input-data", "frozen_main",
             "--library-fingerprint", str(fingerprint),
             "--staged", str(self.tmp / "golden_builtin"),
             "-o", str(output or self.tmp / "prov.json")],
            capture_output=True, text=True)

    def test_end_to_end_writes_provenance(self):
        out = self.tmp / "prov.json"
        proc = self.run_cli(self.make_run(), self.make_log(),
                            self.make_fingerprint(), self.make_case_json(), out)
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(["0"], payload["cases"])
        self.assertEqual(self.sha, payload["builtin_library"]["sha256"])

    def test_output_directory_is_created_when_missing(self):
        # 默认值是 evidence/golden_provenance.json，evidence/ 还没建时就中。
        out = self.tmp / "evidence" / "golden_provenance.json"
        proc = self.run_cli(self.make_run(), self.make_log(),
                            self.make_fingerprint(), self.make_case_json(), out)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(out.is_file())

    def test_a_bare_list_case_json_is_also_understood(self):
        proc = self.run_cli(self.make_run(), self.make_log(),
                            self.make_fingerprint(),
                            self.make_case_json('[{"id": 0}]'))
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_a_half_finished_run_exits_two_and_names_the_missing_cases(self):
        """141 条只落了 3 条也照搬照写的话，取证 JSON 上看不出这轮是残缺的。"""
        cases = json.dumps({"cases": [{"id": index} for index in range(4)]})
        proc = self.run_cli(self.make_run(("0",)), self.make_log(),
                            self.make_fingerprint(), self.make_case_json(cases))
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("4", proc.stderr)
        self.assertIn("1、2、3", proc.stderr)

    def test_a_short_run_with_unnumbered_cases_still_exits_two(self):
        # 目录名不是纯数字时按号对不上，只报条数，不列一串猜出来的号。
        cases = json.dumps({"cases": [{"id": index} for index in range(3)]})
        proc = self.run_cli(self.make_run(("case_a",)), self.make_log(),
                            self.make_fingerprint(), self.make_case_json(cases))
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("3", proc.stderr)
        self.assertNotIn("缺的用例号", proc.stderr)

    def test_a_candidate_side_fingerprint_exits_two(self):
        """同一份 vendor 库解析成 candidate 再喂给第一轮：两轮就是同一份实现。"""
        proc = self.run_cli(self.make_run(), self.make_log(),
                            self.make_fingerprint(side="candidate"),
                            self.make_case_json())
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("--side builtin", proc.stderr)

    def test_a_library_overwritten_after_resolving_exits_two(self):
        fingerprint = self.make_fingerprint()
        log = self.make_log()
        self.lib.write_bytes(b"reinstalled-bytes")
        proc = self.run_cli(self.make_run(), log, fingerprint,
                            self.make_case_json())
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("sha256", proc.stderr)

    def test_two_libraries_in_one_log_exits_two(self):
        log = self.tmp / "mixed.log"
        log.write_text(
            f"[INFO] import aclnnBernoulliGetWorkspaceSize  from {self.lib} success!\n"
            "[INFO] import aclnnBernoulliGetWorkspaceSize  from "
            "/opt/other/libopapi_nn.so success!\n", encoding="utf-8")
        proc = self.run_cli(self.make_run(), log, self.make_fingerprint(),
                            self.make_case_json())
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("/opt/other/libopapi_nn.so", proc.stderr)

    def test_a_symlink_on_the_staged_target_exits_two(self):
        staged = self.tmp / "golden_builtin"
        staged.mkdir()
        (staged / "cpu_0").symlink_to(self.tmp / "opp", target_is_directory=True)
        proc = self.run_cli(self.make_run(), self.make_log(),
                            self.make_fingerprint(), self.make_case_json())
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("软链接", proc.stderr)
        self.assertTrue(self.lib.is_file(), "不该顺手删掉软链接指向的东西")

    def test_an_unreadable_case_json_exits_three(self):
        broken = self.tmp / "cases.json"
        broken.write_text("{ not json", encoding="utf-8")
        proc = self.run_cli(self.make_run(), self.make_log(),
                            self.make_fingerprint(), broken)
        self.assertEqual(3, proc.returncode, proc.stdout)

    def test_vendor_library_in_the_builtin_run_exits_two(self):
        vendor, vendor_sha = real_library(
            self.tmp / "home" / "vendors" / "customize" / "op_api" / "lib",
            "libcust_opapi.so", b"vendor-bytes")
        proc = self.run_cli(self.make_run(), self.make_log(vendor),
                            self.make_fingerprint(path=str(vendor),
                                                  sha256=vendor_sha),
                            self.make_case_json())
        self.assertEqual(2, proc.returncode)
        self.assertIn("vendors", proc.stderr)

    # --- 反证实验的两个新分支 --------------------------------------------

    def run_conclude(self, provenance, *extra_args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--op", "aclnnBernoulli",
             "--conclude-tamper", "0",
             "--staged", str(self.tmp / "golden_builtin"),
             "-o", str(provenance), *extra_args],
            capture_output=True, text=True)

    def _report(self, passed):
        path = self.tmp / "acc.json"
        path.write_text(json.dumps({"cases": [{"id": 0, "passed": passed}]}),
                        encoding="utf-8")
        return str(path)

    def test_conclude_tamper_needs_a_report(self):
        # 结论不接受手工声明：没有报告就判不了。
        prov = self.tmp / "prov.json"
        prov.write_text(json.dumps({"cases": ["0"], "counter_experiment": None}),
                        encoding="utf-8")
        proc = self.run_conclude(prov)
        self.assertEqual(3, proc.returncode, proc.stdout)
        self.assertIn("--report", proc.stderr)

    def test_case_still_passing_after_tamper_exits_two(self):
        # 反证实验的重点：改坏了真值却没变 Fail，说明比对拓扑没判别力，
        # 这条路必须拒绝裁决，不能悄悄放行。
        prov = self.tmp / "prov.json"
        prov.write_text(json.dumps({"cases": ["0"], "counter_experiment": None}),
                        encoding="utf-8")
        proc = self.run_conclude(prov, "--report", self._report(True))
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("没通过", proc.stderr)

    def test_case_failing_after_tamper_exits_zero(self):
        prov = self.tmp / "prov.json"
        prov.write_text(json.dumps({"cases": ["0"], "counter_experiment": None}),
                        encoding="utf-8")
        proc = self.run_conclude(prov, "--report", self._report(False))
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_conclude_tamper_with_corrupted_provenance_exits_three(self):
        prov = self.tmp / "prov.json"
        prov.write_text("{ not json", encoding="utf-8")
        proc = self.run_conclude(prov)
        self.assertEqual(3, proc.returncode, proc.stdout)
        self.assertIn("读不成 JSON", proc.stderr)

    @unittest.skipIf(torch is not None,
                     "本机装了 torch，--tamper 会真的往下改张量，行为不同，"
                     "这条只锁定「没装 torch 时退出码是 3」这一点")
    def test_tamper_without_torch_exits_three(self):
        staged = self.tmp / "golden_builtin"
        case_dir = staged / "cpu_0" / "cases" / "0"
        case_dir.mkdir(parents=True)
        (case_dir / "output_0.pt").write_bytes(b"golden0")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--op", "aclnnBernoulli",
             "--tamper", "0", "--staged", str(staged)],
            capture_output=True, text=True)
        self.assertEqual(3, proc.returncode, proc.stdout)
        self.assertIn("torch", proc.stderr)


class DetectedFromReportTest(unittest.TestCase):
    """反证结论从报告推导，不接受手工声明（红线 4）。

    裸 --detected 的问题：想要绿灯的人只需 --tamper 0 之后立刻
    --conclude-tamper 0 --detected，一轮都不用重跑，而反证实验恰恰是
    这条路唯一有判别力的证据。
    """

    def _report(self, cases):
        tmp = temp_dir(self)
        path = tmp / "acc.json"
        path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
        return str(path)

    def test_failed_case_means_the_experiment_worked(self):
        self.assertTrue(cr.detected_from_report(
            self._report([{"id": 3, "passed": False}, {"id": 4, "passed": True}]),
            "3"))

    def test_passing_case_means_the_topology_did_not_bite(self):
        self.assertFalse(cr.detected_from_report(
            self._report([{"id": 3, "passed": True}]), "3"))

    def test_case_absent_from_the_report_is_refused(self):
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.detected_from_report(
                self._report([{"id": 4, "passed": False}]), "3")
        self.assertIn("改坏真值之后", str(ctx.exception))

    def test_unreadable_report_is_refused(self):
        with self.assertRaises(cr.CaptureError):
            cr.detected_from_report("/nope/absent.json", "3")


class TamperedCaseBookkeepingTest(unittest.TestCase):
    """改坏的那条与记结论的那条必须是同一条。"""

    def _prov(self, extra=None):
        tmp = temp_dir(self)
        path = tmp / "prov.json"
        payload = {"cases": ["3", "4"], "counter_experiment": None}
        payload.update(extra or {})
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_mismatched_case_is_refused(self):
        prov = self._prov({"tampered_case": {"case_id": "3"}})
        with self.assertRaises(cr.CaptureError) as ctx:
            cr.record_counter_experiment(prov, "4", True)
        self.assertIn("同一条", str(ctx.exception))

    def test_matching_case_passes_and_clears_the_marker(self):
        prov = self._prov({"tampered_case": {"case_id": "3"}})
        got = cr.record_counter_experiment(prov, "3", True)
        self.assertEqual({"case_id": "3", "detected": True},
                         got["counter_experiment"])
        self.assertNotIn("tampered_case", got)


if __name__ == "__main__":
    unittest.main()
