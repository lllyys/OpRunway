import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock

import cpp_extension_adapter as A
import cpp_extension_codegen as C
import cpp_extension_driver as D


def _plan():
    return {"cases": [{"case_id": "bad"}, {"case_id": "good"}]}


def _status(stage1=0, null=False, called=True, stage2=0):
    return {"schema": "oprunway.cpp_extension_call_status", "schema_version": 1,
            "stage1_ret": stage1, "workspace_size": 8,
            "executor_null": null, "stage2_called": called, "stage2_ret": stage2}


def _isolation():
    return {"schema": A.EXECUTION_ISOLATION_SCHEMA, "schema_version": 1,
            "mode": "subprocess_per_case_v1", "records": [
                {"case_id": "bad", "launch_id": "a", "isolation_mode": "subprocess_per_case_v1",
                 "termination_kind": "normal", "returncode": 1,
                 "parent_pid": 101, "child_pid": None,
                 "outcome": "failed", "call_status": _status(7, False, False, None)},
                {"case_id": "good", "launch_id": "b",
                 "isolation_mode": "subprocess_per_case_v1", "termination_kind": "normal",
                 "returncode": 0, "parent_pid": 102, "child_pid": 102,
                 "outcome": "produced", "call_status": _status()},
            ]}


class IsolationContractTest(unittest.TestCase):
    def test_driver_forks_before_importing_torch_runtime(self):
        source = Path(__file__).with_name("cpp_extension_driver.py").read_text()
        start = source.index("def _invoke_all(")
        worker = source.index("_launch_case_worker(", start)
        runtime_import = source.index("import torch\n", start)
        self.assertLess(worker, runtime_import)

    def test_first_failure_does_not_hide_fresh_success(self):
        self.assertEqual(A.validate_execution_isolation(_plan(), _isolation())["records"][1]["outcome"], "produced")

    def test_stage1_or_null_executor_may_not_call_stage2(self):
        for mutate in (
                lambda x: x["records"][0]["call_status"].update(stage2_called=True, stage2_ret=0),
                lambda x: x["records"][0]["call_status"].update(stage1_ret=0, executor_null=True,
                                                                  stage2_called=True, stage2_ret=0)):
            bad = copy.deepcopy(_isolation()); mutate(bad)
            with self.assertRaises(A.CppExtensionAdapterError):
                A.validate_execution_isolation(_plan(), bad)

    def test_stage2_failure_signal_and_record_mutations_fail_closed(self):
        for mutate in (
                lambda x: x["records"][1]["call_status"].update(stage2_ret=9),
                lambda x: x["records"][1].update(launch_id="a"),
                lambda x: x["records"][1].update(child_pid=999)):
            bad = copy.deepcopy(_isolation()); mutate(bad)
            with self.assertRaises(A.CppExtensionAdapterError):
                A.validate_execution_isolation(_plan(), bad)

    def test_signal_and_timeout_are_failed_records_not_reusable_successes(self):
        for termination, returncode in (("signal", -11), ("timeout", None)):
            value = _isolation()
            value["records"][0].update(termination_kind=termination,
                                       returncode=returncode, child_pid=None,
                                       call_status=None, outcome="failed")
            self.assertEqual(
                A.validate_execution_isolation(_plan(), value)["records"][1]["outcome"],
                "produced")

    def test_validator_is_total_and_rejects_non_plain_or_inexact_values(self):
        mutations = (
            lambda x: x["records"].__setitem__(0, []),
            lambda x: x["records"][0].update(extra=True),
            lambda x: x["records"][1].update(returncode=True),
            lambda x: x["records"][1].update(parent_pid=True),
            lambda x: x["records"][1]["call_status"].update(stage1_ret=True),
            lambda x: x["records"][1]["call_status"].update(workspace_size=-1),
            lambda x: x["records"][1]["call_status"].update(executor_null=0),
            lambda x: x["records"][1]["call_status"].update(stage2_called=1),
        )
        for mutate in mutations:
            bad = copy.deepcopy(_isolation()); mutate(bad)
            with self.assertRaises(A.CppExtensionAdapterError):
                A.validate_execution_isolation(_plan(), bad)
        with self.assertRaises(A.CppExtensionAdapterError):
            A.validate_execution_isolation(None, None)

    def test_outcome_is_produced_iff_process_pid_and_call_are_all_successful(self):
        for mutate in (
                lambda x: x["records"][1].update(outcome="failed"),
                lambda x: x["records"][0].update(outcome="produced"),
                lambda x: x["records"][1].update(parent_pid=101, child_pid=101),
                lambda x: x["records"][1].update(termination_kind="missing_result",
                                                   child_pid=None)):
            bad = copy.deepcopy(_isolation()); mutate(bad)
            with self.assertRaises(A.CppExtensionAdapterError):
                A.validate_execution_isolation(_plan(), bad)

    def test_real_subprocess_parent_pid_and_termination_classification(self):
        with tempfile.TemporaryDirectory() as root:
            result = os.path.join(root, "result.json")
            code = ("import json,os,sys; "
                    "open(sys.argv[1],'w').write(json.dumps({'child_pid':os.getpid()}))")
            observed = D._launch_case_worker([sys.executable, "-c", code, result],
                                             dict(os.environ), result, timeout=2)
            child = json.loads(Path(result).read_text())
            self.assertEqual(observed["termination_kind"], "normal")
            self.assertEqual(observed["parent_pid"], child["child_pid"])
            os.unlink(result)
            missing = D._launch_case_worker([sys.executable, "-c", "pass"],
                                            dict(os.environ), result, timeout=2)
            self.assertEqual(missing["termination_kind"], "missing_result")
            signalled = D._launch_case_worker(
                [sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"],
                dict(os.environ), result, timeout=2)
            self.assertEqual(signalled["termination_kind"], "signal")
            self.assertEqual(signalled["returncode"], -signal.SIGTERM)
            timed = D._launch_case_worker([sys.executable, "-c", "import time; time.sleep(5)"],
                                          dict(os.environ), result, timeout=.05)
            self.assertEqual(timed["termination_kind"], "timeout")

    def test_run_rejects_externally_injected_worker_marker(self):
        with mock.patch.dict(os.environ, {"OPRUNWAY_CPP_EXTENSION_CASE_WORKER": "1"}):
            with self.assertRaises(D.DriverError):
                D.run("/does/not/matter", "/does/not/matter")


class GeneratedBridgeTest(unittest.TestCase):
    def test_bridge_records_and_stops_before_stage2(self):
        body = C._render_generated_two_stage_body(
            {"symbol": "Witness", "stage2_call_arity": 4}, "x, y", 2, "x",
            standard_abi=True)
        self.assertIn("OpRunwayWriteCallStatus(workspace_status, workspace_size", body)
        guard = body.index("if (workspace_status != 0 || executor == nullptr)")
        stage2 = body.index("auto exec_params")
        self.assertLess(guard, stage2)
        self.assertIn("false, true, api_ret", body)


if __name__ == "__main__":
    unittest.main()
