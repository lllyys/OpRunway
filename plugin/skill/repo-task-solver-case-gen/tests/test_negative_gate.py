# -*- coding: utf-8 -*-
"""负例门的机械断言（case-gen 侧，2026-10-09）。

装包自检此前从不执行包内 verify，sim_dut 只被复制不被执行——「verify 形同虚设」
一类缺陷（HT-24 同款）活得过装包。负例门在临时目录内用**包内成品件**跑一轮判定
链，正例必绿、负例必红。本文件把门的判定辅助与两条装包路径的真跑固化成回归：

- 门内切片选取（代价升序、info 排除、batch=1 不被排除、空池 fail-closed）→
  test_gate_pick_cases
- 轮断言的正例面（退出码、逐 ID 恰一行、多/缺行、空 cases 配伪造 summary）→
  test_gate_assert_round_positive
- 轮断言的负例面（退出码、PASS 行、证据不足行、不可裁的 error 行两型）→
  test_gate_assert_round_negative
- 批量归因解析三型（一致性层命中被扰动槽 / 命中代表槽 / 残差层单槽内容）与
  指认错位的拒绝 → test_gate_slot_attribution
- 最小非批量包真装一次，第十项打钩且证据齐全 → test_purescript_package_gate
- 最小批量包真装一次，归因指认到被扰动槽位 → test_batched_package_gate
- batch=1 的批量包也能装成（扰动槽位 0 是该内容的唯一槽位）→
  test_batched_package_gate_batch_one
- 把包内 verify 短路成恒 PASS，门必须拦住（门自身不是摆设）→
  test_gate_catches_blind_verify
"""
import json
import shutil

import pytest

import build_package as bp


def _canonical(cases, path):
    doc = {"schema": "solver-s1/canonical_cases@1", "frozen_at": "2026-10-09",
           "cases": cases}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    return path


def _purescript_cases():
    """合成 spotrf 精度用例（n 取 2/4/8/9：info 变体的 k 取 1/n//2/n 都落在域内）。"""
    return [{"case_id": f"spotrf-t{i:03d}", "op": "spotrf", "source": "cu",
             "n": n, "nrhs": None, "uplo": "L" if i % 2 else "U",
             "lda": n, "ldb": None, "seed": 4200 + i, "s1_subset": True}
            for i, n in enumerate((2, 4, 8, 9), start=1)]


def _batched_cases():
    return [{"case_id": "spotrfBatched-t001", "op": "spotrfBatched", "source": "cu",
             "n": 2, "nrhs": None, "uplo": "L", "lda": 2, "ldb": None,
             "seed": 930000777, "batch": 16, "s1_subset": True}]


def _build(tmp_path, op, cases):
    """真装一个最小包（门随装包执行）：退 0 即门的两条断言都成立。"""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "perf_baseline.json").write_text('{"note": "test stub"}\n',
                                                encoding="utf-8")
    canonical = _canonical(cases, tmp_path / "canonical.json")
    out = tmp_path / "pkg" / op
    selfcheck = tmp_path / "selfcheck.json"
    rc = bp.main(["--canonical", str(canonical), "--staging", str(staging),
                  "--out", str(out), "--op", op, "--scope", "full",
                  "--selfcheck", str(selfcheck)])
    assert rc == 0, f"{op} 装包退出码 {rc}（应 0）"
    with open(selfcheck, encoding="utf-8") as fh:
        return out, json.load(fh)


def _gate_index_doc(package, batched):
    """门消费的 index 投影（与装包内部传给 run_negative_gate 的同形）。"""
    with open(package / "cases" / "index.json", encoding="utf-8") as fh:
        index = json.load(fh)
    if batched:
        return {"ratio_basis": index.get("ratio_basis"),
                "cases": [{"case_id": e["case_id"],
                           "ratio_cpu_mean": e.get("ratio_cpu_mean")}
                          for e in index["cases"]
                          if e.get("case_purpose") != "info"]}
    return {"ratio_cpu_mean": index.get("ratio_cpu_mean"),
            "ratio_basis": index.get("ratio_basis"), "cases": []}


# ---------------------------------------------------------------------------
# 判定辅助：切片选取与三条断言
# ---------------------------------------------------------------------------

def test_gate_pick_cases():
    cases = [{"case_id": "c-big", "n": 8},
             {"case_id": "c-small", "n": 2},
             {"case_id": "a-small", "n": 2},
             {"case_id": "c-info", "n": 1, "case_purpose": "info"}]
    picked = [c["case_id"] for c in bp.gate_pick_cases(cases, False, 2)]
    assert picked == ["a-small", "c-small"]          # n² 升序，平手按 case_id
    assert len(bp.gate_pick_cases(cases, False, 9)) == 3   # info 用例不入门
    # 批量按 n²·batch 定序；batch=1 不再被排除（扰动槽位 0 恒存在）
    batched = [{"case_id": "b-wide", "n": 2, "batch": 1000},
               {"case_id": "b-tall", "n": 64, "batch": 2},
               {"case_id": "b-single", "n": 2, "batch": 1}]
    assert [c["case_id"] for c in bp.gate_pick_cases(batched, True, 2)] == \
        ["b-single", "b-wide"]
    assert [c["case_id"] for c in bp.gate_pick_cases(
        [{"case_id": "b-single", "n": 2, "batch": 1}], True, 1)] == ["b-single"]
    with pytest.raises(bp.SelfCheckError, match="没有精度 case"):
        bp.gate_pick_cases([{"case_id": "c-info", "n": 2, "batch": 4,
                             "case_purpose": "info"}], True, 1)


def _ids(count):
    return [f"c{i}" for i in range(count)]


def _report(numerics, errors=None, summary_extra=None):
    """合成判定报告：每行一个 case_id，verdict schema 取 criteria/verdict.py。"""
    errs = errors or [None] * len(numerics)
    rows = [{"case_id": cid, "status": "数值判定",
             "verdict": {"numeric": v, "error": e}}
            for cid, v, e in zip(_ids(len(numerics)), numerics, errs)]
    summary = {"total": len(rows),
               "numeric_pass": sum(1 for v in numerics if v == "PASS"),
               "numeric_fail": sum(1 for v in numerics if v == "FAIL"),
               "no_evidence": 0}
    summary.update(summary_extra or {})
    return {"cases": rows, "summary": summary}


def test_gate_assert_round_positive():
    assert bp.gate_assert_round("pos", _ids(2), 0, _report(["PASS", "PASS"])) == 2
    with pytest.raises(bp.SelfCheckError, match="退出码 1"):       # 退出码型
        bp.gate_assert_round("pos", _ids(2), 1, _report(["PASS", "PASS"]))
    with pytest.raises(bp.SelfCheckError, match="结论无效"):       # 逐行结论型
        bp.gate_assert_round("pos", _ids(2), 0, _report(["PASS", "FAIL"]))
    with pytest.raises(bp.SelfCheckError, match="缺判定行"):       # 清单上的 ID 没判
        bp.gate_assert_round("pos", _ids(3), 0, _report(["PASS", "PASS"]))
    with pytest.raises(bp.SelfCheckError, match="多出判定行"):     # 判了清单外的 ID
        bp.gate_assert_round("pos", _ids(1), 0, _report(["PASS", "PASS"]))
    with pytest.raises(bp.SelfCheckError, match="判定行重复"):
        doubled = _report(["PASS"])
        doubled["cases"].append(dict(doubled["cases"][0]))
        bp.gate_assert_round("pos", _ids(1), 0, doubled)
    # 空 cases 配一份对得上的 summary：计数由行推导，混不过去
    with pytest.raises(bp.SelfCheckError, match="缺判定行"):
        bp.gate_assert_round("pos", _ids(1), 0,
                             {"cases": [], "summary": {"total": 1,
                                                       "numeric_pass": 1}})


def test_gate_assert_round_negative():
    assert bp.gate_assert_round("neg", _ids(3), 1,
                                _report(["FAIL", "FAIL", "FAIL"])) == 3
    with pytest.raises(bp.SelfCheckError, match="退出码 0"):       # 退出码型
        bp.gate_assert_round("neg", _ids(1), 0, _report(["FAIL"]))
    with pytest.raises(bp.SelfCheckError, match="结论无效"):       # 漏检型
        bp.gate_assert_round("neg", _ids(2), 1, _report(["FAIL", "PASS"]))
    # 证据不足不算红：缺证据不是判据抓住了偏差
    noev = {"cases": [{"case_id": "c0", "status": "证据不足", "verdict": None}],
            "summary": {"total": 1, "numeric_pass": 0, "numeric_fail": 0,
                        "no_evidence": 1}}
    with pytest.raises(bp.SelfCheckError, match="结论无效"):
        bp.gate_assert_round("neg", _ids(1), 1, noev)
    # 空 cases 配一份对得上的 summary：负例方向同样混不过去
    with pytest.raises(bp.SelfCheckError, match="缺判定行"):
        bp.gate_assert_round("neg", _ids(1), 1,
                             {"cases": [], "summary": {"total": 1,
                                                       "numeric_fail": 1}})


@pytest.mark.parametrize("error", [
    # ① 判定链内部异常被捕获成错误行（verdict._error_verdict：numeric 恒 FAIL）
    "judge 内部异常: ValueError: 残差入参非有限",
    # ② 残差基线不可用（ratio_cpu_status 非 ok）
    "残差基线不可用: ratio_cpu_status='prep_failed', ratio_cpu=None",
    # ③ 缺 ratio_cpu_mean 的兼容分支（residual.pass=None + 错误说明）
    "残差证据不足: 缺 ratio_cpu_mean，第二支不可算，待含 mean 的 v3 包复判",
])
def test_gate_assert_round_rejects_unjudgeable(error):
    """numeric=="FAIL" 但 error 非空：不可裁，不是判据抓住了偏差。

    这三条都是 criteria/verdict.py 里 numeric 恒 FAIL 的「没证据」路径。只认
    numeric 的话，门会把自己的故障（基线算不出、判定链抛错）当成负例检出成功。
    """
    report = _report(["FAIL"], errors=[error])
    report["cases"][0]["verdict"]["residual"] = {"ran": False, "ratio": None,
                                                 "threshold": None, "pass": None}
    with pytest.raises(bp.SelfCheckError, match="不可裁"):
        bp.gate_assert_round("neg", _ids(1), 1, report)


def _batched_report(verdict, case_id="b1"):
    return {"cases": [{"case_id": case_id, "status": "数值判定", "verdict": verdict}],
            "summary": {"total": 1, "numeric_pass": 0, "numeric_fail": 1,
                        "no_evidence": 0}}


def _mismatch_verdict(first_fail, mismatches):
    return {"numeric": "FAIL", "fail_count": 1, "first_fail_index": first_fail,
            "worst_index": None, "diagnostics": {"a0_mismatches": mismatches}}


def test_gate_slot_attribution():
    # 型 1：被扰动槽位 1 是余槽（内容 1 的代表槽是 3），一致性层直接报它
    smap_rest = [{"content_idx": 0, "rep_slot": 0, "slots": [0, 2]},
                 {"content_idx": 1, "rep_slot": 3, "slots": [3, 1]}]
    hit_rest = _mismatch_verdict(1, [{"content_idx": 1, "slot": 1,
                                      "ref_slot": 3, "field": "out32"}])
    assert "一致性层" in bp.gate_assert_slot_attribution(
        _batched_report(hit_rest), "b1", smap_rest, slot=1)
    # 型 2：被扰动槽位 1 恰是代表槽，失配落在同内容其余槽位
    smap_rep = [{"content_idx": 0, "rep_slot": 0, "slots": [0, 2]},
                {"content_idx": 1, "rep_slot": 1, "slots": [1, 3]}]
    hit_rep = _mismatch_verdict(3, [{"content_idx": 1, "slot": 3,
                                     "ref_slot": 1, "field": "out32"}])
    assert "参照槽 1" in bp.gate_assert_slot_attribution(
        _batched_report(hit_rep), "b1", smap_rep, slot=1)
    # 型 3：该内容只有被扰动这一个槽位，无余槽可比 → 残差层指认内容下标
    smap_solo = [{"content_idx": 0, "rep_slot": 0, "slots": [0]},
                 {"content_idx": 1, "rep_slot": 1, "slots": [1]}]
    solo = {"numeric": "FAIL", "fail_count": 1, "first_fail_index": 1,
            "worst_index": 1, "diagnostics": {"a0_contents": 2}}
    assert "残差层" in bp.gate_assert_slot_attribution(_batched_report(solo),
                                                       "b1", smap_solo, slot=1)
    # 缺省槽位（GATE_SLOT=0）在 batch=1 的唯一槽位上成立：同走残差层
    smap_one = [{"content_idx": 0, "rep_slot": 0, "slots": [0]}]
    only = {"numeric": "FAIL", "fail_count": 1, "first_fail_index": 0,
            "worst_index": 0, "diagnostics": {"a0_contents": 1}}
    assert "残差层" in bp.gate_assert_slot_attribution(_batched_report(only),
                                                       "b1", smap_one)
    # 指认错位：失配报在别的槽位 → 拒绝
    astray = _mismatch_verdict(2, [{"content_idx": 0, "slot": 2,
                                    "ref_slot": 0, "field": "out32"}])
    with pytest.raises(bp.SelfCheckError, match="应指认槽位"):
        bp.gate_assert_slot_attribution(_batched_report(astray), "b1", smap_rep,
                                        slot=1)
    # 全不指认：既无一致性失配，残差层也没报到该内容 → 拒绝
    blind = {"numeric": "FAIL", "fail_count": 1, "first_fail_index": None,
             "worst_index": None, "diagnostics": {}}
    with pytest.raises(bp.SelfCheckError, match="未被指认"):
        bp.gate_assert_slot_attribution(_batched_report(blind), "b1", smap_solo,
                                        slot=1)
    # 报告里没有这条 case、或槽位不在映射里：门的前提坏了，同样 fail-closed
    with pytest.raises(bp.SelfCheckError, match="没有 b1 的判定行"):
        bp.gate_assert_slot_attribution({"cases": []}, "b1", smap_rep, slot=1)
    with pytest.raises(bp.SelfCheckError, match="没有槽位"):
        bp.gate_assert_slot_attribution(_batched_report(solo), "b1",
                                        [{"content_idx": 0, "rep_slot": 0,
                                          "slots": [0]}], slot=1)


# ---------------------------------------------------------------------------
# 两条装包路径：最小包真装一次，门随装包执行
# ---------------------------------------------------------------------------

def test_purescript_package_gate(tmp_path):
    package, checklist = _build(tmp_path, "spotrf", _purescript_cases())
    assert checklist["all_passed"] is True
    assert [i["check"] for i in checklist["items"]][-2:] == ["负例门", "指纹"]
    gate = checklist["negative_gate"]
    assert gate["positive"] == {"perturb": "none", "exit_code": 0,
                                "numeric_pass": 7}       # 精度 4 + info 契约 3
    assert gate["negative"]["exit_code"] == 1
    assert gate["negative"]["numeric_fail"] == 7
    assert gate["cases"] == [c["case_id"] for c in _purescript_cases()]
    # 预期判定清单 = 门自己造数落下的那份（精度 4 + 派生 info 3），逐 ID 核过
    assert len(gate["judged"]) == 7
    assert set(c["case_id"] for c in _purescript_cases()) <= set(gate["judged"])
    assert "最小 4 例" in gate["bound"]
    # 门的临时目录在包外：零数据与指纹不受它影响
    assert not list(package.rglob("*.npz"))
    assert not list(package.rglob("dut_*"))


def test_batched_package_gate(tmp_path):
    package, checklist = _build(tmp_path, "spotrfBatched", _batched_cases())
    assert checklist["all_passed"] is True
    gate = checklist["negative_gate"]
    assert gate["cases"] == ["spotrfBatched-t001"]
    assert gate["judged"] == ["spotrfBatched-t001"]   # --case-id 单跑，恰一行
    assert gate["negative"]["perturb"] == "scale --perturb-index 0"
    assert gate["negative"]["exit_code"] == 1
    assert "槽位" in gate["negative"]["attribution"] or \
        "内容" in gate["negative"]["attribution"]
    assert not list(package.rglob("*.npz"))


def test_batched_package_gate_batch_one(tmp_path):
    """batch=1 的合法批量包：槽位 0 是该内容的唯一槽位，门按残差层归因。"""
    cases = [dict(_batched_cases()[0], case_id="spotrfBatched-b1", batch=1)]
    _, checklist = _build(tmp_path, "spotrfBatched", cases)
    assert checklist["all_passed"] is True
    gate = checklist["negative_gate"]
    assert gate["cases"] == ["spotrfBatched-b1"]
    assert gate["negative"]["perturb"] == "scale --perturb-index 0"
    assert "残差层" in gate["negative"]["attribution"]


def test_gate_catches_blind_verify(tmp_path):
    """把包内 verify 的 judge 短路成恒 PASS（HT-24 同款），门必须拦住。"""
    package, _ = _build(tmp_path, "spotrf", _purescript_cases())
    tampered = tmp_path / "tampered"
    shutil.copytree(package, tampered)
    path = tampered / "verify_accuracy.py"
    text = path.read_text(encoding="utf-8")
    assert "v = judge(arrays, dut)" in text
    path.write_text(
        text.replace("v = judge(arrays, dut)",
                     'v = {"numeric": "PASS", "flags": [], "error": None}'),
        encoding="utf-8")
    with pytest.raises(bp.SelfCheckError, match="neg 轮断言不符"):
        bp.run_negative_gate(tampered, "spotrf",
                             _gate_index_doc(package, False), False)
