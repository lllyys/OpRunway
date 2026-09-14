"""布局分组与超时推算：不碰真机的那几件事。

**复算必须与 ATK 用同一个公式**，否则报告里的分组是编出来的——看报告的人
会把它当实测。这里把公式逐字钉住，ATK 那边改了公式这条测试不会响，
但至少本仓两处（run_atk 与 verdict）不会各算各的。
"""

import json
import random
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_atk  # noqa: E402
import verdict  # noqa: E402
import wait_for  # noqa: E402


def _case(cid, **attrs):
    return {"id": cid, "inputs": [{"name": k, "range_values": [v]}
                                  for k, v in attrs.items()]}


def _write_cases(tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def test_layout_of_matches_atk_formula():
    # atk/tasks/backends/backend.py:151-157
    for ratio in (0.1, 0.4, 0.75):
        for cid in range(200):
            expected = random.Random(cid).random() < ratio
            assert run_atk._layout_of(cid, ratio) is expected


def test_ratio_bounds_are_all_or_nothing():
    assert run_atk._layout_of(7, 0.0) is False
    assert run_atk._layout_of(7, 1.0) is True


def test_split_by_ratio_covers_every_case_exactly_once(tmp_path):
    cases = [_case(i) for i in range(500)]
    contig, noncontig = run_atk._layout_split(_write_cases(tmp_path, cases), 0.4)
    assert not (contig & noncontig)
    assert contig | noncontig == set(range(500))
    # 0.4 在 500 条上落在这个区间；只钉数量级，不钉具体条数
    assert 170 < len(noncontig) < 230


def test_split_by_attr_ignores_ratio(tmp_path):
    """npu 剖面：布局写在用例属性里，ATK 的 ratio 不参与。"""
    cases = [_case(i, input_mode=i % 4) for i in range(40)]
    path = _write_cases(tmp_path, cases)
    contig, noncontig = run_atk._layout_split(path, 0.9, "input_mode", {3})
    assert noncontig == {i for i in range(40) if i % 4 == 3}
    assert len(noncontig) == 10


def test_slice_ratio_reads_facts(tmp_path):
    facts = tmp_path / "facts.json"

    facts.write_text(json.dumps({"non_contiguous": {"required": False}}), encoding="utf-8")
    assert run_atk._slice_ratio(facts) == 0.0

    facts.write_text(json.dumps({"non_contiguous": {"required": True}}), encoding="utf-8")
    assert run_atk._slice_ratio(facts) == run_atk.DEFAULT_SLICE_RATIO

    facts.write_text(json.dumps({"non_contiguous": {"required": True, "ratio": 0.6}}),
                     encoding="utf-8")
    assert run_atk._slice_ratio(facts) == 0.6

    # 越界的比例返回 -1 让调用方退 3，**不静默按缺省跑**：
    # 那会让报告写着「按 0.4 混」而实际不是。
    for bad in (0, 1.5, -0.2, "x"):
        facts.write_text(json.dumps({"non_contiguous": {"required": True, "ratio": bad}}),
                         encoding="utf-8")
        assert run_atk._slice_ratio(facts) == -1.0

    assert run_atk._slice_ratio(tmp_path / "nope.json") == 0.0


def test_round_timeout_scales_with_case_count():
    assert run_atk._round_timeout(10) == run_atk.TIMEOUT_FLOOR
    # 1000 条按 3 s/条加固定开销，必须高于原先写死的 1800
    assert run_atk._round_timeout(1000) > run_atk.TIMEOUT_FLOOR
    assert run_atk._round_timeout(1000) >= 1000 * run_atk.SECONDS_PER_CASE
    assert run_atk._round_timeout(5000) > run_atk._round_timeout(1000)


def test_case_count_reads_both_case_file_shapes(tmp_path):
    """兜底按条数推，而条数只能从**文件**里数——调用方手上没有列表。

    数不出来返回 0 而不是抛：读不出条数就把整轮拦掉，比按下限跑一轮糟得多。
    """
    array = tmp_path / "array.json"
    array.write_text(json.dumps([{"id": i} for i in range(1000)]), encoding="utf-8")
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"cases": [{"id": i} for i in range(7)]}),
                       encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")

    assert run_atk._case_count(array) == 1000
    assert run_atk._case_count(wrapped) == 7
    assert run_atk._case_count(broken) == 0
    assert run_atk._case_count(tmp_path / "nope.json") == 0
    # 1000 条那轮实测约 47 分钟，兜底必须高过写死的下限，否则跑得完的轮次被杀
    assert run_atk._round_timeout(run_atk._case_count(array)) > run_atk.TIMEOUT_FLOOR


def test_solo_recheck_prints_progress_wait_for_can_parse(tmp_path, capsys, monkeypatch):
    """隔离复验每条完成打一行 `已完成 N/M`，形状必须是 `wait_for` 认的那个。

    **这是等待方的唯一心跳。** 调起等待的工具不向用户流式输出，进度只有在
    `wait_for` 退出时才到得了终端；不打这一行，100 条十来分钟里终端与卡死无法区分。
    两个脚本各改一处就对不上，所以在这里把耦合钉住。
    """
    cases = _write_cases(tmp_path, [_case(i) for i in range(5)])

    def _fake_subset(ids, *args, **kwargs):
        # 3 号单独跑仍算错，其余单独跑都通过（= 批内污染）
        wrong = [cid for cid in ids if cid == 3]
        return {"failed_ids": [], "accuracy_false_ids": wrong}, list(ids), ""

    monkeypatch.setattr(run_atk, "_run_subset", _fake_subset)
    still, batch_only, wrong = run_atk._solo_recheck(
        [0, 1, 2, 3, 4], cases, tmp_path / "golden", ["0", "1"], None, 0.0, None)

    assert still == []
    assert wrong == [3]
    assert sorted(batch_only) == [0, 1, 2, 4]

    lines = [ln for ln in capsys.readouterr().out.splitlines() if "已完成" in ln]
    assert len(lines) == 5, "每条完成打一行，不是一次跳满"
    hits = [re.findall(wait_for.PROGRESS_RE_DEFAULT, ln) for ln in lines]
    assert all(hit for hit in hits), "wait_for 的默认正则抓不到这些行"
    assert [hit[-1] for hit in hits] == [("1", "5"), ("2", "5"), ("3", "5"),
                                         ("4", "5"), ("5", "5")]


def test_wait_for_timeout_is_a_heartbeat_not_a_task_ceiling():
    """默认 `--timeout` 是「多久回吐一次消息」，设成小时级等于一小时不吭声。"""
    assert wait_for.HEARTBEAT_SECONDS <= 600
    parser_default = wait_for.HEARTBEAT_SECONDS
    assert parser_default < wait_for.INTERVAL_MAX * 60


def test_verdict_groups_pass_rate_by_layout():
    accuracy = {"layout": {"slice_ratio": 0.4,
                           "contiguous_ids": [1, 2, 3, 4],
                           "noncontiguous_ids": [5, 6]}}
    stats = verdict._layout_stats(accuracy, failed_ids=[5], acc_false_ids=[2])
    assert stats["contiguous"] == {
        "total": 4, "passed": 3, "exec_failed_ids": [], "accuracy_false_ids": [2],
        "pass_rate": 75.0}
    assert stats["noncontiguous"]["total"] == 2
    assert stats["noncontiguous"]["passed"] == 1
    assert stats["noncontiguous"]["pass_rate"] == 50.0


def test_verdict_refuses_to_group_without_layout():
    """没有 layout 字段就不分组——猜一个划分比不分组更糟。"""
    assert verdict._layout_stats({}, [], []) == {}
    assert verdict._layout_stats({"layout": {"contiguous_ids": [1]}}, [], []) == {}


def test_noncontig_note_never_speaks_for_the_taskdoc():
    """**报告不许从 facts 的声明推出「任务书没要求」**——两个真机会话各错一次，
    两次的任务书参数表里「非连续Tensor」列都写着支持。"""
    required = verdict._noncontig_note({"non_contiguous": {"required": True}}, {})
    assert "任务书要求支持非连续输入" in required[0]

    # 原来的错误文案是「任务书没要求支持非连续输入，……」，那是替任务书说话。
    absent = verdict._noncontig_note({"non_contiguous": {"required": False}}, {})
    assert "任务书没要求支持非连续输入" not in absent[0]
    assert "facts.non_contiguous.required" in absent[0]
    assert "这不等于任务书没要求" in absent[0]

    missing = verdict._noncontig_note({}, {})
    assert "任务书没要求支持非连续输入" not in missing[0]
