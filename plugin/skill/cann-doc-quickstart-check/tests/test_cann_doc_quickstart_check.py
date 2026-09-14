"""cann-doc-quickstart-check 脚本单测(纯逻辑,无 NPU)。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import _state          # noqa: E402
import find_docs       # noqa: E402
import run_step        # noqa: E402
import render_report   # noqa: E402


def _redirect(monkeypatch, tmp_path):
    """把产物根重定向到 tmp(_state 的路径函数读模块全局,monkeypatch 即生效)。"""
    monkeypatch.setattr(_state, "DOCCHECK_ROOT", tmp_path / "doccheck")


# ---- find_docs:文件名命中 + 标题命中 + 跳过噪声目录 ----

def test_find_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "QUICKSTART.md").write_text("# Quick Start\n步骤", encoding="utf-8")
    (tmp_path / "docs" / "zh").mkdir()
    (tmp_path / "docs" / "zh" / "intro.md").write_text("## 快速入门\n内容", encoding="utf-8")  # heading
    (tmp_path / "README.md").write_text("# Project\n无关", encoding="utf-8")                    # 不命中
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "QUICKSTART.md").write_text("# Quick Start", encoding="utf-8")        # 噪声目录,跳过

    docs = find_docs.find_docs(str(tmp_path))
    paths = [d["path"] for d in docs]
    assert "docs/QUICKSTART.md" in paths            # 文件名命中
    assert "docs/zh/intro.md" in paths              # 标题命中
    assert "README.md" not in paths                 # 无关键字,不收
    assert all("build/" not in p for p in paths)    # 噪声目录跳过
    assert docs[0]["match"] == "filename"           # 文件名命中排前


# ---- run_step 执行:忠实记录退出码 + 落盘日志,verdict 待判 ----

def test_run_step_execute(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    rec = run_step.execute("ops-x", 1, str(work), "echo hello-quickstart")
    assert rec["exit_code"] == 0
    assert "hello-quickstart" in rec["stdout_excerpt"]
    assert rec["verdict"] == "UNJUDGED"             # 执行不判定
    assert os.path.isfile(rec["log_path"])          # 真实日志落盘
    steps = _state.load_steps("ops-x")
    assert len(steps) == 1 and steps[0]["idx"] == 1


def test_run_step_failure_recorded(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    rec = run_step.execute("ops-x", 2, str(work), "exit 3")
    assert rec["exit_code"] == 3                    # 失败如实记录,不重试


def test_run_step_no_injection(tmp_path, monkeypatch):
    """纪律核验:execute 不注入文档外 env —— 文档没让 export 的变量就不该存在。"""
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    rec = run_step.execute("ops-x", 1, str(work), 'echo "VAR=[${QUICKSTART_INJECT:-unset}]"')
    assert "VAR=[unset]" in rec["stdout_excerpt"]


# ---- 判定 + 卡住即停语义 ----

def test_set_verdict_and_blocker(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-x", 1, str(work), "echo ok")
    run_step.execute("ops-x", 2, str(work), "exit 1")
    assert _state.set_verdict("ops-x", 1, "OK")
    assert _state.set_verdict("ops-x", 2, "FAIL", defect="文档未说明需先 source 环境", fix="文档应在第 2 步前加 `source set_env.sh`")
    assert not _state.set_verdict("ops-x", 99, "OK")        # 不存在的步
    assert "FAIL" in _state.BLOCKER_VERDICTS                 # FAIL 是卡点


# ---- 报告:有 blocker → 卡在第 N 步;全 OK → 通过 ----

def test_render_blocked(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    _state.save_meta("ops-x", {"doc": "docs/QUICKSTART.md", "declared_prerequisites": ["CANN 已装"]})
    run_step.execute("ops-x", 1, str(work), "echo ok")
    run_step.execute("ops-x", 2, str(work), "exit 1")
    _state.set_verdict("ops-x", 1, "OK")
    _state.set_verdict("ops-x", 2, "FAIL", defect="缺 source 步骤", fix="补 source")
    md = render_report.render("ops-x")
    assert "卡在第 2 步" in md
    assert "缺 source 步骤" in md and "补 source" in md       # 缺陷 + 修订建议都进报告
    assert "docs/QUICKSTART.md" in md


def test_render_passed(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-y", 1, str(work), "echo a")
    run_step.execute("ops-y", 2, str(work), "echo b")
    _state.set_verdict("ops-y", 1, "OK")
    _state.set_verdict("ops-y", 2, "OK")
    md = render_report.render("ops-y")
    assert "能纯按文档跑通" in md
    assert "未发现文档缺陷" in md


# ---- 两趟:injected_fix 字段 + 探索趟报告 ----

def test_injected_fix_stored(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-nn/explored", 1, str(work), "echo built")
    # 探索趟:注入修复 + 判定
    assert _state.set_verdict("ops-nn/explored", 1, "OK",
                              injected_fix="source set_env.sh; --soc ascend910b→ascend910_93")
    s = _state.load_steps("ops-nn/explored")[0]
    assert s["injected_fix"].startswith("source set_env.sh")
    assert s["verdict"] == "OK"


def test_explored_report_has_injected_column(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("r/explored", 1, str(work), "echo build")
    run_step.execute("r/explored", 2, str(work), "exit 1")
    _state.set_verdict("r/explored", 1, "OK", injected_fix="source CANN + 代理")
    _state.set_verdict("r/explored", 2, "FAIL", defect="非文档缺陷:NPU 运行时错", injected_fix="source CANN")
    md = render_report.render("r/explored", kind="explored")
    assert "探索趟" in md and "注入修复" in md          # 探索趟标题 + 台账列
    assert "source CANN + 代理" in md                   # injected_fix 进了报告
    assert "本趟实际注入" in md                          # 缺陷段展示注入
    assert "止于第 2 步" in md or "终止详情" in md        # 探索趟用「止于/终止」措辞
    # 忠实趟默认无注入列
    md_f = render_report.render("r/explored", kind="faithful")
    assert "注入修复" not in md_f


# ---- judge_prev:判上一步 + 跑下一步合并成一次调用(省往返),且卡点不放行 ----

def test_judge_prev_ok_lets_next_step_run(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-x", 1, str(work), "echo a")
    ok, proceed, note = run_step.judge_prev("ops-x", 1, 2, "OK", None, None, None)
    assert (ok, proceed) == (True, True)
    assert _state.load_steps("ops-x")[0]["verdict"] == "OK"
    assert "#1" in note


def test_judge_prev_blocker_stops_next_step(tmp_path, monkeypatch):
    """上一步判成卡点 → 判定落盘,但**不放行**下一步(卡住即停,脚本级强制)。"""
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-x", 1, str(work), "exit 1")
    ok, proceed, note = run_step.judge_prev("ops-x", 1, 2, "FAIL", "缺 source", "补 source", None)
    assert ok is True and proceed is False
    s = _state.load_steps("ops-x")[0]
    assert s["verdict"] == "FAIL" and s["defect"] == "缺 source"   # 判定与缺陷都已落盘
    assert "STOP" in note


def test_judge_prev_blocker_allows_same_step_rerun(tmp_path, monkeypatch):
    """探索趟:注入修复后重跑**同一步**是正当续跑,不受卡点闸门拦截。"""
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("r/explored", 3, str(work), "exit 1")
    ok, proceed, _ = run_step.judge_prev("r/explored", 3, 3, "FAIL", "缺 source", "补 source", None)
    assert (ok, proceed) == (True, True)


def test_judge_prev_missing_step_fails_loudly(tmp_path, monkeypatch):
    """指向不存在的步 → 失败,调用方必须硬失败而不是照跑下一步(台账不能缺条)。"""
    _redirect(monkeypatch, tmp_path)
    ok, proceed, note = run_step.judge_prev("ops-x", 7, 8, "OK", None, None, None)
    assert (ok, proceed) == (False, False)
    assert "ERROR" in note


def test_judge_prev_records_injected_fix(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("r/explored", 1, str(work), "echo a")
    run_step.judge_prev("r/explored", 1, 2, "OK", None, None, "source set_env.sh")
    assert _state.load_steps("r/explored")[0]["injected_fix"] == "source set_env.sh"


def test_cli_prev_verdict_end_to_end(tmp_path, monkeypatch, capsys):
    """CLI 一次调用完成「判第 1 步 + 跑第 2 步」。"""
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-x", 1, str(work), "echo a")
    monkeypatch.setattr(sys, "argv", [
        "run_step", "--repo", "ops-x", "--idx", "2", "--cwd", str(work),
        "--cmd", "echo second", "--prev-verdict", "OK"])
    assert run_step.main() == 0
    steps = _state.load_steps("ops-x")
    assert steps[0]["verdict"] == "OK"                       # 上一步判定已落
    assert steps[1]["idx"] == 2 and "second" in steps[1]["stdout_excerpt"]   # 本步已执行


def test_cli_prev_verdict_blocker_does_not_execute(tmp_path, monkeypatch):
    """CLI 层:上一步是卡点时,下一步**不得被执行**(台账里不该出现该步)。"""
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    run_step.execute("ops-x", 1, str(work), "exit 1")
    monkeypatch.setattr(sys, "argv", [
        "run_step", "--repo", "ops-x", "--idx", "2", "--cwd", str(work),
        "--cmd", "echo must-not-run", "--prev-verdict", "FAIL", "--prev-defect", "缺步骤"])
    assert run_step.main() == 0
    idxs = [s["idx"] for s in _state.load_steps("ops-x")]
    assert idxs == [1]                                        # 第 2 步没执行


def test_cli_prev_verdict_missing_step_returns_1(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    work = tmp_path / "w"; work.mkdir()
    monkeypatch.setattr(sys, "argv", [
        "run_step", "--repo", "ops-x", "--idx", "5", "--cwd", str(work),
        "--cmd", "echo x", "--prev-verdict", "OK"])
    assert run_step.main() == 1
    assert _state.load_steps("ops-x") == []                   # 没落任何步骤
