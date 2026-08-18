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


class BypassTest(unittest.TestCase):
    """逐条堵住「手改哪个字段能让它放行」。

    上面几条判据全靠库指纹里的两个字段，而指纹是一份可以手改的 JSON。
    """

    def test_dropping_the_sha_field_does_not_buy_a_pass(self):
        # 删掉 sha256 就没得比了，不能因此静默放行。
        found = gs.problems(provenance(), BUILTIN, dict(CANDIDATE, sha256=""),
                            ["0", "1"], "c" * 64)
        self.assertTrue(any("sha256" in item for item in found), found)

    def test_dropping_the_path_field_does_not_buy_a_pass(self):
        stripped = dict(CANDIDATE)
        del stripped["path"]
        found = gs.problems(provenance(), BUILTIN, stripped, ["0", "1"], "c" * 64)
        self.assertTrue(any("path" in item for item in found), found)

    def test_two_fingerprints_of_the_same_side_are_refused(self):
        # 把 --builtin 和 --candidate 写反，或者两次都解析了同一侧。
        found = gs.problems(provenance(), dict(CANDIDATE, side="candidate"),
                            CANDIDATE, ["0", "1"], "c" * 64)
        self.assertTrue(any("那一侧" in item for item in found), found)

    def test_fingerprint_resolved_for_another_operator_is_refused(self):
        # 库里没有本轮这个算子时 ATK 会自己去搜，搜到的很可能就是内置那份。
        found = gs.problems(provenance(), BUILTIN, dict(CANDIDATE, op="aclnnAbs"),
                            ["0", "1"], "c" * 64)
        self.assertTrue(any("aclnnAbs" in item for item in found), found)

    def test_counter_experiment_on_a_case_outside_this_run_is_refused(self):
        found = gs.problems(
            provenance(counter_experiment={"case_id": "77", "detected": True}),
            BUILTIN, CANDIDATE, ["0", "1"], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("77", found[0])

    def test_an_empty_case_set_is_refused(self):
        # 零条用例时每条判据都自动满足，「真值齐备」成了空话。
        found = gs.problems(provenance(), BUILTIN, CANDIDATE, [], "c" * 64)
        self.assertEqual(1, len(found))
        self.assertIn("一条用例都没有", found[0])

    def test_hand_edited_sha_is_caught_by_hashing_the_file_again(self):
        tmp = temp_dir(self)
        real = tmp / "libopapi_nn.so"
        real.write_bytes(b"builtin implementation")
        actual = gs.sha256_file(str(real))
        clean = {"side": "builtin", "path": str(real), "sha256": actual}
        self.assertEqual([], gs.stale_fingerprints(clean))
        edited = dict(clean, sha256="aaa")
        found = gs.stale_fingerprints(edited)
        self.assertEqual(1, len(found))
        self.assertIn(actual, found[0])

    def test_a_library_that_is_not_on_this_machine_is_not_reported_as_stale(self):
        self.assertEqual([], gs.stale_fingerprints(BUILTIN, CANDIDATE))

    def test_a_library_path_that_cannot_be_read_is_reported(self):
        # 指到一个目录上：算不出 sha256，也就核对不了，得说出来而不是当成通过。
        tmp = temp_dir(self)
        found = gs.stale_fingerprints({"side": "builtin", "path": str(tmp),
                                       "sha256": "aaa"})
        self.assertEqual(1, len(found))
        self.assertIn("读不了", found[0])


class UnreadableArtifactTest(unittest.TestCase):
    """产物读不进来时判不了，一律走 GateError，不许裸崩。"""

    def test_a_json_array_is_not_a_fingerprint(self):
        tmp = temp_dir(self)
        (tmp / "b.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(gs.GateError):
            gs.read_json_object(str(tmp / "b.json"), "库指纹")

    def test_a_case_that_is_not_an_object_is_refused(self):
        tmp = temp_dir(self)
        (tmp / "cases.json").write_text(json.dumps({"cases": ["0"]}),
                                        encoding="utf-8")
        with self.assertRaises(gs.GateError):
            gs.case_ids_of(str(tmp / "cases.json"))


class CliTest(unittest.TestCase):
    def _write(self, tmp, prov, builtin, candidate, cases):
        (tmp / "prov.json").write_text(json.dumps(prov), encoding="utf-8")
        (tmp / "b.json").write_text(json.dumps(builtin), encoding="utf-8")
        (tmp / "c.json").write_text(json.dumps(candidate), encoding="utf-8")
        (tmp / "cases.json").write_text(json.dumps({"cases": cases}),
                                        encoding="utf-8")
        (tmp / "accuracy.log").write_text(
            "[INFO] import aclnnBernoulliGetWorkspaceSize  "
            f"from {candidate.get('path')} success!\n", encoding="utf-8")

    def _run(self, tmp):
        return subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "check_golden_source.py"),
             "--provenance", str(tmp / "prov.json"),
             "--builtin", str(tmp / "b.json"),
             "--candidate", str(tmp / "c.json"),
             "--case-json", str(tmp / "cases.json"),
             "--candidate-log", str(tmp / "accuracy.log")],
            capture_output=True, text=True)

    def _prepare(self, tmp, builtin=BUILTIN, candidate=CANDIDATE, prov=None,
                 cases=None):
        """写齐四份产物，并把用例 JSON 的真实 sha256 补进取证。"""
        cases = [{"id": 0}, {"id": 1}] if cases is None else cases
        prov = provenance() if prov is None else prov
        self._write(tmp, prov, builtin, candidate, cases)
        prov["case_json_sha256"] = gs.sha256_file(str(tmp / "cases.json"))
        (tmp / "prov.json").write_text(json.dumps(prov), encoding="utf-8")

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
             "--case-json", str(tmp / "cases.json"),
             "--candidate-log", str(tmp / "accuracy.log")],
            capture_output=True, text=True)
        self.assertEqual(3, proc.returncode)

    def test_second_round_loading_the_builtin_exits_two(self):
        """第二轮忘 source env.sh，ATK 按同名搜到内置那份——两轮同一份实现。"""
        tmp = temp_dir(self)
        self._prepare(tmp)
        (tmp / "accuracy.log").write_text(
            "[INFO] import aclnnBernoulliGetWorkspaceSize  "
            f"from {BUILTIN['path']} success!\n", encoding="utf-8")
        proc = self._run(tmp)
        self.assertEqual(2, proc.returncode)
        self.assertIn("env.sh", proc.stderr)

    def test_missing_candidate_log_exits_three(self):
        tmp = temp_dir(self)
        self._prepare(tmp)
        (tmp / "accuracy.log").unlink()
        proc = self._run(tmp)
        self.assertEqual(3, proc.returncode)

    def test_library_changed_on_disk_exits_two(self):
        """跑测之后被重装、或者指纹被手改，两种都在这里现形。"""
        tmp = temp_dir(self)
        real = tmp / "libopapi_nn.so"
        real.write_bytes(b"builtin implementation")
        builtin = {"side": "builtin", "path": str(real), "sha256": "aaa"}
        self._prepare(tmp, builtin=builtin,
                      prov=provenance(builtin_library=dict(builtin)))
        proc = self._run(tmp)
        self.assertEqual(2, proc.returncode)
        self.assertIn("此刻盘上的 sha256", proc.stderr)

    def test_unreadable_json_exits_three(self):
        tmp = temp_dir(self)
        self._prepare(tmp)
        (tmp / "b.json").write_text("{ 这不是 JSON", encoding="utf-8")
        proc = self._run(tmp)
        self.assertEqual(3, proc.returncode)
        self.assertIn("读不成 JSON", proc.stderr)

    def test_case_json_in_an_unknown_shape_exits_three(self):
        tmp = temp_dir(self)
        self._prepare(tmp)
        (tmp / "cases.json").write_text(json.dumps({"case_list": []}),
                                        encoding="utf-8")
        proc = self._run(tmp)
        self.assertEqual(3, proc.returncode)
        self.assertIn("读不出用例列表", proc.stderr)

    def test_cases_without_an_id_are_numbered_by_position(self):
        """ATK 落盘的目录名就是从 0 数起的位置号。"""
        tmp = temp_dir(self)
        self._prepare(tmp, cases=[{}, {}])
        proc = self._run(tmp)
        self.assertEqual(0, proc.returncode, proc.stderr)


CANDIDATE_LOG = (
    "[INFO] import aclnnBernoulliGetWorkspaceSize  "
    "from /home/x/vendors/customize/op_api/lib/libcust_opapi.so success!\n")


class CandidateRoundTest(unittest.TestCase):
    """第二轮加载了谁：整条链上只有这里核得到。

    第二轮忘 source env.sh 时 ATK 会按算子名自己搜，社区算子与内置同名，
    搜到的很可能就是内置那份——两轮跑同一份实现，而两份指纹各自都诚实、
    反证实验也照过（改坏真值后无论第二轮加载谁，那条都会 Fail）。
    """

    def _log(self, text):
        tmp = temp_dir(self)
        path = tmp / "accuracy.log"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_matching_library_has_no_problem(self):
        self.assertEqual([], gs.candidate_round_problems(
            self._log(CANDIDATE_LOG), "aclnnBernoulli", CANDIDATE))

    def test_second_round_loading_the_builtin_is_refused(self):
        # 正是这条路要挡的那个失败：两轮跑同一份实现。
        log = self._log(
            "[INFO] import aclnnBernoulliGetWorkspaceSize  "
            "from /opp/lib64/libopapi_nn.so success!\n")
        found = gs.candidate_round_problems(log, "aclnnBernoulli", CANDIDATE)
        self.assertEqual(1, len(found))
        self.assertIn("/opp/lib64/libopapi_nn.so", found[0])
        self.assertIn("env.sh", found[0])

    def test_conflicting_libraries_in_one_log_are_refused(self):
        log = self._log(CANDIDATE_LOG + (
            "[INFO] import aclnnBernoulliGetWorkspaceSize  "
            "from /opp/lib64/libopapi_nn.so success!\n"))
        found = gs.candidate_round_problems(log, "aclnnBernoulli", CANDIDATE)
        self.assertEqual(1, len(found))
        self.assertIn("不止一份", found[0])

    def test_no_load_record_is_undecidable(self):
        with self.assertRaises(gs.GateError) as ctx:
            gs.candidate_round_problems(
                self._log("[INFO] nothing here\n"), "aclnnBernoulli", CANDIDATE)
        self.assertIn("没真正绑定过算子库", str(ctx.exception))

    def test_unreadable_log_is_undecidable(self):
        with self.assertRaises(gs.GateError) as ctx:
            gs.candidate_round_problems(
                "/nope/absent.log", "aclnnBernoulli", CANDIDATE)
        self.assertIn("--candidate-log", str(ctx.exception))

    def test_symlinked_library_still_matches(self):
        # probe_env.py 的 link_custom_opp() 造的正是软链接，两侧归一必须一致。
        tmp = temp_dir(self)
        real = tmp / "libcust_opapi.so"
        real.write_bytes(b"vendor")
        link = tmp / "linked.so"
        link.symlink_to(real)
        log = self._log(
            f"[INFO] import aclnnBernoulliGetWorkspaceSize  from {link} success!\n")
        self.assertEqual([], gs.candidate_round_problems(
            log, "aclnnBernoulli", dict(CANDIDATE, path=str(real))))


if __name__ == "__main__":
    unittest.main()
