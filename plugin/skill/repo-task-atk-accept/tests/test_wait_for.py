"""等待量具：不碰真机的那几件事。

这里钉三条容易悄悄失效的判据：

- **崩了要当场判出来。** 产物与进程是与的关系，不特判「进程没了而产物不在」
  就会一路等满这一段——实测过一次第 3 秒就崩的 `NameError`，等到了上限。
- **进度行的形状与 `run_atk.py` 耦合。** 两边各改一处就对不上，等待方从此
  一个字都抓不到。
- **心跳间隔不是任务上限。** 设成小时级等于一小时不吭声，与卡死无法区分。
"""

import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import wait_for  # noqa: E402


def _args(**kw):
    class A:
        file = []
        pattern = None
        pid = None
        timeout = wait_for.HEARTBEAT_SECONDS
        progress_from = None
        progress_re = wait_for.PROGRESS_RE_DEFAULT
    a = A()
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _run(monkeypatch, args, running, sleeps=None):
    """跑一次 main()，用假的 pgrep 与假的 sleep，不真等。

    `running` 是个可调用对象，每轮问一次进程还在不在。
    """
    monkeypatch.setattr(sys, "argv", ["wait_for.py"])
    monkeypatch.setattr(wait_for.argparse.ArgumentParser, "parse_args",
                        lambda self: args)
    monkeypatch.setattr(wait_for, "_pattern_running", lambda p: running())
    monkeypatch.setattr(wait_for.time, "sleep",
                        lambda s: (sleeps.append(s) if sleeps is not None else None))
    return wait_for.main()


def test_process_gone_with_artifact_missing_is_a_crash(tmp_path, monkeypatch, capsys):
    """进程退了而产物不在 = 崩了，退 3 并说清楚别再等。"""
    missing = tmp_path / "isolate.json"
    args = _args(file=[str(missing)], pattern="run_atk.py --mode isolate")

    calls = []

    def running():
        calls.append(1)
        return len(calls) < 3   # 第三轮起进程没了

    assert _run(monkeypatch, args, running) == 3
    err = capsys.readouterr().err
    assert "崩了" in err
    assert str(missing) in err
    assert "不要再等" in err


def test_process_gone_with_artifact_present_is_success(tmp_path, monkeypatch):
    """进程退了且产物在 = 正常收工，退 0。"""
    landed = tmp_path / "isolate.json"
    landed.write_text("{}", encoding="utf-8")
    args = _args(file=[str(landed)], pattern="run_atk.py --mode isolate")
    assert _run(monkeypatch, args, lambda: False) == 0


def _heartbeat_run(monkeypatch, args, on_tick):
    """跑到心跳上限，时间由假 sleep 推进，进程一直在。`on_tick` 每轮改日志。"""
    clock = [0.0]
    monkeypatch.setattr(wait_for.time, "time", lambda: clock[0])

    def _sleep(seconds):
        clock[0] += seconds
        on_tick()

    monkeypatch.setattr(wait_for.time, "sleep", _sleep)
    monkeypatch.setattr(sys, "argv", ["wait_for.py"])
    monkeypatch.setattr(wait_for.argparse.ArgumentParser, "parse_args",
                        lambda self: args)
    monkeypatch.setattr(wait_for, "_pattern_running", lambda p: True)
    return wait_for.main()


def test_heartbeat_with_progress_moving_says_keep_waiting(tmp_path, monkeypatch,
                                                          capsys):
    """进度在长 = 还在跑，退 1 但要说「再调一次」，不能读成失败。"""
    log = tmp_path / "isolate.log"
    log.write_text("已完成    3/100\n", encoding="utf-8")
    args = _args(file=[str(tmp_path / "nope.json")],
                 pattern="run_atk.py --mode isolate",
                 timeout=30, progress_from=str(log))

    seen = [3]

    def _tick():
        seen[0] += 1
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"已完成    {seen[0]}/100\n")

    assert _heartbeat_run(monkeypatch, args, _tick) == 1
    err = capsys.readouterr().err
    assert "没等到" in err
    assert "还在动" in err
    assert "再调一次" in err


def test_heartbeat_with_progress_stuck_points_at_the_log(tmp_path, monkeypatch,
                                                         capsys):
    """进度一整段没动 = 报现象、指向日志，**不下「卡死」的结论**。"""
    log = tmp_path / "isolate.log"
    log.write_text("已完成    3/100\n", encoding="utf-8")
    args = _args(file=[str(tmp_path / "nope.json")],
                 pattern="run_atk.py --mode isolate",
                 timeout=30, progress_from=str(log))

    assert _heartbeat_run(monkeypatch, args, lambda: None) == 1
    err = capsys.readouterr().err
    assert "没动过" in err
    assert str(log) in err


def test_only_file_given_warns_about_the_blind_spot(tmp_path, monkeypatch, capsys):
    """只给产物不给进程，要当场提醒——那是崩溃判不出来的那种用法。"""
    args = _args(file=[str(tmp_path / "nope.json")], timeout=0)
    monkeypatch.setattr(sys, "argv", ["wait_for.py"])
    monkeypatch.setattr(wait_for.argparse.ArgumentParser, "parse_args",
                        lambda self: args)
    monkeypatch.setattr(wait_for.time, "sleep", lambda s: None)
    wait_for.main()
    assert "只等产物，没等进程" in capsys.readouterr().out


def test_progress_reads_only_the_log_tail(tmp_path):
    """几 MB 的日志只读尾部，且照样抓得到最后一处进度。"""
    log = tmp_path / "big.log"
    log.write_text("填充行\n" * 200000 + "已完成    77/100\n", encoding="utf-8")
    assert log.stat().st_size > wait_for.PROGRESS_TAIL_BYTES
    assert wait_for._progress(log, wait_for.PROGRESS_RE_DEFAULT) == "77/100"

    # 抓不到与读不了都返回空串，不抛——日志还没长出来是常态
    (tmp_path / "empty.log").write_text("", encoding="utf-8")
    assert wait_for._progress(tmp_path / "empty.log",
                              wait_for.PROGRESS_RE_DEFAULT) == ""
    assert wait_for._progress(tmp_path / "nope.log",
                              wait_for.PROGRESS_RE_DEFAULT) == ""


def test_default_regex_matches_the_line_run_atk_prints():
    """默认正则与 `run_atk.py` 打的 `已完成 N/M` 是同一份契约。"""
    assert re.findall(wait_for.PROGRESS_RE_DEFAULT,
                      "已完成    66/100") == [("66", "100")]


def test_heartbeat_is_not_an_hour():
    """`--timeout` 是心跳间隔：设成小时级等于一小时不吭声。"""
    assert wait_for.HEARTBEAT_SECONDS <= 300
    assert wait_for.HEARTBEAT_SECONDS > wait_for.INTERVAL_MAX


def test_pattern_match_ignores_own_process_chain(monkeypatch):
    """`pgrep -f` 会命中拉起本进程的每一层 shell——它们的命令行里就有 `--pattern`
    的值。只排自己的 pid 不够，判据会恒真：进程早退了还报「在跑」，崩溃判不出来。
    """
    me = os.getpid()
    ancestors = {me, me + 1, me + 2}
    monkeypatch.setattr(wait_for, "_own_chain", lambda: ancestors)

    class _Done:
        def __init__(self, out):
            self.stdout = out

    # 只匹配到自己这条链 = 进程已经没了
    monkeypatch.setattr(wait_for.subprocess, "run",
                        lambda *a, **k: _Done(f"{me}\n{me + 1}\n{me + 2}\n"))
    assert wait_for._pattern_running("run_atk.py --mode isolate") is False

    # 链外还有一个 = 真的在跑
    monkeypatch.setattr(wait_for.subprocess, "run",
                        lambda *a, **k: _Done(f"{me}\n{me + 1}\n999999\n"))
    assert wait_for._pattern_running("run_atk.py --mode isolate") is True


def test_own_chain_walks_up_not_down(monkeypatch):
    """排的是祖先。`probe_env.own_pids` 排后代，方向相反，别混用。"""
    me = os.getpid()
    monkeypatch.setattr(
        wait_for.subprocess, "run",
        lambda *a, **k: type("R", (), {
            "stdout": f"  PID  PPID\n{me} 4242\n4242 4200\n4200 1\n7777 {me}\n"})())
    chain = wait_for._own_chain()
    assert {me, 4242, 4200, 1} <= chain
    assert 7777 not in chain, "后代不该进来"


def test_no_wait_condition_is_a_usage_error(monkeypatch):
    args = _args()
    monkeypatch.setattr(sys, "argv", ["wait_for.py"])
    monkeypatch.setattr(wait_for.argparse.ArgumentParser, "parse_args",
                        lambda self: args)
    assert wait_for.main() == 2
