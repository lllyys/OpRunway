"""T1–T4 后处理改进单测（纯逻辑，无 NPU）。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import utils
import postrun


# ---- T4：SOC 名 → build.sh 短 soc 映射 ----

def test_soc_mapping():
    f = utils.soc_name_to_build_soc
    assert f("Ascend910_9382") == "ascend910_93"
    assert f("Ascend910_9362") == "ascend910_93"
    assert f("Ascend910_5512") == "ascend910_55"
    assert f("Ascend910B3") == "ascend910b"
    assert f("Ascend910ProB") == "ascend910b"
    assert f("Ascend910A") == "ascend910"
    assert f("Ascend950") == "ascend950"
    assert f("Ascend310P3") == "ascend310p"
    assert f("Ascend310B") == "ascend310b"
    assert f(None) is None


# ---- T2：空退判定 + 集中状态判定 ----

def test_is_empty_run():
    assert utils.is_empty_run("", "", 0.2) is True
    assert utils.is_empty_run("  \n\n  ", "", 0.2) is True        # 全空白行
    assert utils.is_empty_run("result[0] is: 1", "", 0.2) is False  # 有有效输出
    assert utils.is_empty_run("", "", 5.0) is False               # 慢，不当空退（避免误伤）


def test_classify_run_status():
    f = utils.classify_run_status
    assert f("", "", 0, 0.2, False)[0] == "SKIPPED_NO_RUN_ARTIFACT"   # 空退（T2）
    assert f("result[0] is: 42", "", 0, 1.0, False)[0] == "PASS"      # 强成功
    assert f("boom", "", 1, 1.0, False)[0] == "RUN_EXIT_FAIL"         # exit≠0
    assert f("Segmentation fault", "", 0, 1.0, False)[0] == "RUN_PATTERN_FAIL"  # 强失败模式
    assert f("", "", 124, 300.0, True)[0] == "TIMEOUT"               # 超时
    # exit0 + 有实质但不结论 + 不快 → 仍 UNCERTAIN（不被空退误归）
    assert f("ambiguous long output without verdict word", "", 0, 5.0, False)[0] == "UNCERTAIN"


# ---- T1/T3：postrun 待办队列 + 整轮完成判定 ----

def _fake_state(monkeypatch, statuses):
    repos = {"ops-x": {"ops": {op: {"phase1": {"status": s}} for op, s in statuses.items()}}}
    monkeypatch.setattr(postrun.state, "load", lambda: {"repos": repos})


def test_build_postrun_actions(monkeypatch):
    _fake_state(monkeypatch, {"a": "PASS", "b": "BUILD_FAIL", "c": "UNCERTAIN",
                              "d": "RUN_EXIT_FAIL", "e": "SKIPPED_NO_RUN_ARTIFACT"})
    act = postrun.build_postrun_actions()
    assert {x["op"] for x in act["failed_ops"]} == {"b", "d"}      # 失败入队
    assert {x["op"] for x in act["uncertain_reviews"]} == {"c"}   # UNCERTAIN 入队
    assert act["incomplete_ops"] == []                            # 全已结算，无未跑完
    # PASS / SKIPPED_NO_RUN_ARTIFACT 不入队
    assert postrun.run_completion(act) == "ACTION_REQUIRED"


def test_run_completion_complete(monkeypatch):
    _fake_state(monkeypatch, {"a": "PASS", "b": "SKIPPED_NO_RUN_ARTIFACT", "c": "SKIPPED_USER"})
    act = postrun.build_postrun_actions()
    assert act["failed_ops"] == [] and act["uncertain_reviews"] == []
    assert act["incomplete_ops"] == []
    assert postrun.run_completion(act) == "COMPLETE"


def test_incomplete_blocks_completion(monkeypatch):
    # 修复 #2/#4：PENDING / RUNNING（worker 崩了 / 被中断）必须算待办，不得静默 COMPLETE
    _fake_state(monkeypatch, {"a": "PASS", "b": "PENDING", "c": "RUNNING"})
    act = postrun.build_postrun_actions()
    assert {x["op"] for x in act["incomplete_ops"]} == {"b", "c"}
    assert act["failed_ops"] == [] and act["uncertain_reviews"] == []
    assert postrun.run_completion(act) == "ACTION_REQUIRED"


def test_fail_statuses_single_source(monkeypatch):
    # 修复 #3：postrun 不再自定义失败集合，与 state 同一对象（杜绝漂移）
    import state as _state
    assert postrun._FAIL_STATUSES is _state.FAIL_STATUSES


def test_exit_codes_not_reuse_2():
    # T1 关键：ACTION_REQUIRED 退出码必须 ≠ 2（2 已被 OpsResolutionError / argparse usage 占用）
    assert postrun.EXIT_ACTION_REQUIRED == 3
    assert postrun.EXIT_COMPLETE == 0
