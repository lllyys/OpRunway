#!/usr/bin/env python3
"""隔离验收会话的进度视图：步骤条 + 人话 + 真实命令与结果。

用法：
    python3 watch.py [jsonl路径] [选项]

选项：
    --brief         只看人话，不展开命令与结果
    --full          结果不截断（很长，配合 less 用）
    --width N       命令/结果每条最多显示的字符数（默认 700）

    最长的两段是 S3 的构建与 S4 的 ATK 跑测，那里会安静很久，属正常。

    历史事件显示 --:--；实时计时从观察器追到文件末尾、开始跟随新事件时算起。

✓ 表示已越过的阶段；▶ 表示当前阶段。
"""
import json, sys, time, os, re, textwrap, shutil

argv = sys.argv[1:]
BRIEF = "--brief" in argv
FULL = "--full" in argv
WIDTH = 10**9 if FULL else 700
if "--width" in argv:
    WIDTH = int(argv[argv.index("--width") + 1])
paths = [a for a in argv if not a.startswith("--") and not a.isdigit()]
if not paths:
    print(__doc__)
    sys.exit(2)
PATH = paths[0]

COLS = min(shutil.get_terminal_size((120, 40)).columns, 140)

# 与 plugin/skill/repo-task-atk-test/SKILL.md 的阶段表一一对应。那边改了这里就要跟。
STEPS = ["任务书解读", "用例生成", "编译安装部署", "精度性能测试", "输出测试结果"]

B, D, G, Y, R, C, M, BL, O = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m",
                              "\033[36m", "\033[35m", "\033[34m", "\033[0m")

# (正则, 步骤, 人话)。步骤为 None 表示只打印、不推进进度条。
# 首个命中即返回，所以顺序有意义：越专用的模式越靠前。
GLOSS = [
    (r"probe_env\.py",                               1, "探测环境指纹"),
    (r"derive_interface\.py",                        1, "派生接口事实"),
    (r"check_c_api_applicability\.py",               1, "c_api 适用性三查"),
    (r"task_doc|任务书",                              1, "读任务书"),
    (r"interface\.json|constraints\.md",             1, "写接口事实与约束表"),
    (r"aclnn_\w+\.h|cann_ops_\w+\.h",                1, "读公开头文件"),
    (r"_def\.cpp|op_def",                            1, "读公开 op_def"),
    (r"examples?/.*\.(cpp|py)",                      1, "读公开 example"),
    (r"make_must_cover\.py",                         2, "生成必测集"),
    (r"materialize",                                 2, "物化组合表"),
    (r"make_yaml\.py",                               2, "推导 YAML"),
    (r"(?:^|[/\s])atk\s+case\b",                     2, "★ ATK 生成用例"),
    (r"freeze_inputs\.py",                           2, "★ 冻结输入"),
    (r"validate_cases\.py",                          2, "校验用例集"),
    (r"check_signature_contract\.py",                2, "核对出参顺序"),
    (r"align_signatures\.py",                        2, "签名对齐"),
    (r"check_adapter_binding\.py",                   2, "适配器判定"),
    (r"gate_lookup\.py",                             2, "查门禁"),
    (r"rewire_adapter\.py",                          2, "受控接线改写"),
    (r"constraint\.py",                              2, "处理生成器插件"),
    (r"build\.sh|\bcmake\b|\bninja\b",               3, "★ 构建（最长）"),
    (r"\.run\b.*--install",                          3, "★ 安装算子包"),
    (r"check_soc_binding\.py",                       3, "SoC 绑定门"),
    (r"check_opapi_binding\.py",                     3, "op_api 绑定门"),
    (r"make_c_api_build\.py",                        3, "渲染 c_api 构建"),
    (r"check_c_api_binding\.py",                     3, "c_api 导出函数名与路径门"),
    (r"\bnm\b\s+-",                                  3, "查导出函数名"),
    (r"ATK_C_API_",                                  3, "设置 c_api 执行器环境"),
    (r"performance_device|performance",              4, "★ 性能执行"),
    (r"run_atk_task\.py",                            4, "★ ATK 跑测"),
    (r"accuracy",                                    4, "★ 精度执行"),
    (r"verdict\.py",                                 4, "裁决"),
    (r"save_failed_cases\.py",                       4, "留存失败用例"),
    (r"op_statistic|op_summary|PROF_|msprof",        4, "采集 profiler"),
    (r"probe_progress\.py",                       None, "从产物反推进度"),
    (r"报告|report",                                  5, "写报告"),
    (r"repro\.sh",                                   5, "复现包"),
    (r"npu-smi",                                 None, "读 NPU 全卡健康与占用（skill 外）"),
    (r"sha256sum|shasum",                        None, "算哈希（核验）"),
    (r"python3?\s+-c|\bimport\b",                None, "探测 Python / 依赖"),
    (r"find .*-type f|\bls -",                   None, "清点文件"),
    (r"\bmkdir\b",                               None, "建目录"),
    (r"\bgrep\b|\brg\b",                         None, "搜内容"),
    (r"\bcat\b|sed -n|\bhead\b|\btail\b|\bwc\b", None, "读文件"),
]

state = {"t0": None, "step": 0, "done": set(), "live": False}


def clip(s, n=None):
    n = WIDTH if n is None else n
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + f" …(+{len(s)-n} 字符)"


def block(text, prefix, color):
    for line in textwrap.wrap(text, max(40, COLS - len(prefix) - 2)) or [""]:
        print(f"{color}{prefix}{line}{O}")


def raw_of(name, inp):
    if name in ("Read", "Write", "Edit"):
        return f"{name} {inp.get('file_path','')}"
    if name in ("Glob", "Grep"):
        return f"{name} {inp.get('pattern','')} {inp.get('path','')}"
    if name == "Skill":
        return f"Skill {inp.get('skill','')}"
    return inp.get("command") or json.dumps(inp, ensure_ascii=False)


def gloss(name, inp):
    raw = raw_of(name, inp)
    for pat, step, text in GLOSS:
        if re.search(pat, raw, re.I):
            return step, text, raw
    if name == "TodoWrite":
        return None, None, raw
    return None, name, raw


def stamp():
    # 事件流不带时间戳。回放历史时无从知道真实时刻，标 --:--，不编造。
    if state["t0"] is None or not state["live"]:
        return "  --:-- "
    s = int(time.time() - state["t0"])
    return f"  {s//60:02d}:{s%60:02d} "


def bar():
    cells = []
    for i in range(1, len(STEPS) + 1):
        if i in state["done"]:
            cells.append(f"{G}{i}✓{O}")
        elif i == state["step"]:
            cells.append(f"{Y}{B}{i}▶{O}")
        else:
            cells.append(f"{D}{i}·{O}")
    label = STEPS[state["step"] - 1] if state["step"] else "启动中"
    print(f"\n{B}┌─ {' '.join(cells)}  {D}(✓=已越过 ▶=当前阶段){O}   {Y}{label}{O}")


def setstep(n, forced=False):
    # 关键词推测只前进：读 op_def 之类的动作会在后期反复出现，倒退会让进度条来回跳。
    if not n or not 1 <= n <= len(STEPS):
        return
    if not forced and n < state["step"]:
        return
    done = set(range(1, n))
    changed = done != state["done"]
    if forced:
        state["done"] = done
    if n != state["step"]:
        state["step"] = n
        bar()
    elif changed and forced:
        bar()


def render(ev):
    t = ev.get("type")

    if t == "system" and ev.get("subtype") == "init":
        state["t0"] = time.time()
        print(f"{B}══ 隔离验收会话 {str(ev.get('session_id','?'))[:8]} ══{O}")
        print(f"{D}   cwd    {ev.get('cwd')}{O}")
        sk = [s for s in (ev.get("skills") or [])
              if any(x in str(s).lower() for x in ("accept", "atk"))]
        print(f"{D}   plugin skill：{sk or '（无！隔离失败）'}{O}")
        return

    if t == "assistant":
        for b in ev.get("message", {}).get("content", []):
            k = b.get("type")
            if k == "text" and b.get("text", "").strip():
                txt = b["text"].strip()
                print()
                for line in txt.splitlines():
                    blocked = re.search(r"阻塞·未验收\s*@S(\d)", line)
                    stage = re.match(
                        r"\s*S(\d)\s+(?:" + "|".join(map(re.escape, STEPS)) + r")\b", line)
                    if blocked:
                        setstep(int(blocked.group(1)), forced=True)
                        block(line, "  │ ", R + B)
                    else:
                        if stage:
                            setstep(int(stage.group(1)), forced=True)
                        block(line, "  │ ", C)
            elif k == "thinking" and not BRIEF and b.get("thinking", "").strip():
                block(clip(b["thinking"], 300), "      ·思考· ", D)
            elif k == "tool_use":
                raw = raw_of(b.get("name"), b.get("input", {}))
                mark = re.search(r"mark_step\.py\s+(\d)\b", raw, re.I)
                if mark and 1 <= int(mark.group(1)) <= len(STEPS):
                    step = int(mark.group(1))
                    text = f"进入 S{step} {STEPS[step - 1]}"
                    setstep(step, forced=True)
                else:
                    step, text, raw = gloss(b.get("name"), b.get("input", {}))
                if text is None:
                    continue
                setstep(step)
                head = (M + B) if text.startswith("★") else ((R + B) if text.startswith("⚠") else "")
                print(f"{D}{stamp()}{O}{head}{text}{O}")
                if not BRIEF:
                    block(clip(raw), "         $ ", BL)
        return

    if t == "user":
        for b in ev.get("message", {}).get("content", []):
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            c = b.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            c = re.sub(r"^\s*Authorized users only\..*$", "", str(c or ""), flags=re.M)
            c = clip(c)
            if b.get("is_error"):
                block(c, "         ✗ ", R)
            elif not BRIEF and c.strip():
                block(c, "         → ", D)
        return

    if t == "result":
        print(f"\n{B}══ 结束 ══{O}")
        bad = ev.get("is_error")
        print(f"   {(R+'❌ 出错') if bad else (G+'✅ 正常收尾')}{O}   "
              f"{ev.get('num_turns')} 轮   {round(ev.get('duration_ms',0)/1000)} 秒   "
              f"${ev.get('total_cost_usd')}")
        print()
        block(str(ev.get("result", "")), "  ", G)
        print()
        print(f"{Y}会话结束不等于验收结束{O}{D}：还要确认目标机进程已退出、"
              f"终态文件已生成，两者同时成立才算完。{O}")


def main():
    while not os.path.exists(PATH):
        time.sleep(1)
    with open(PATH, encoding="utf-8", errors="replace") as f:
        while True:
            line = f.readline()
            if not line:
                if not state["live"]:
                    state["live"] = True
                    if state["t0"]:
                        state["t0"] = time.time()
                    print(f"{D}  ── 以上为已有内容；以下实时，计时从此刻起 ──{O}")
                    sys.stdout.flush()
                time.sleep(0.4)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                render(json.loads(line))
            except json.JSONDecodeError:
                pass
            sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{D}(停止观察，会话仍在后台运行){O}")
