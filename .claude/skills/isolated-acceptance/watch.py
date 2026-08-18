#!/usr/bin/env python3
"""隔离验收会话的进度视图：步骤条 + 人话 + 真实命令与结果。

用法：
    python3 watch.py [jsonl路径] [选项]

选项：
    --brief         只看人话，不展开命令与结果
    --full          结果不截断（很长，配合 less 用）
    --width N       命令/结果每条最多显示的字符数（默认 700）

最长的两段是步骤 3 的 fresh build 与步骤 5/6 的 ATK 执行，那里会安静很久，属正常。

✓ 只表示模型自己在回复里勾掉了该步；▶ 是按当前动作的关键词推测，不代表真的完成。
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

# 与 plugin/skills/acceptance-workflow/SKILL.md 的七步一一对应。那边改了这里就要跟。
STEPS = ["锚定输入与环境准入", "冻结 spec 与 design", "编译安装与装载身份",
         "生成 ATK case", "精度测试", "性能测试", "证据闭合与报告"]

B, D, G, Y, R, C, M, BL, O = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m",
                              "\033[36m", "\033[35m", "\033[34m", "\033[0m")

# (正则, 步骤, 人话)。步骤为 None 表示只打印、不推进进度条。
# 首个命中即返回，所以顺序有意义：越专用的模式越靠前。
GLOSS = [
    (r"\bcp -|\brsync\b|tar .*-C .*inputs",         1, "把调用方输入复制进 session"),
    (r"atk[^|;]*--version|--version[^|;]*atk",      1, "探测 ATK 版本与绝对路径"),
    (r"witnesses|参考答案",                       None, "⚠ 接触 witness 夹具"),
    (r"selftest|self_test|self-test",             None, "查源码自带 self-test"),
    (r"(?:^|[/\s])atk\s+case\b",                    4, "★ ATK 生成 caseset"),
    (r"task_doc|taskdoc|任务书",                    2, "读任务书（语义/硬件/阈值权威）"),
    (r"_def\.cpp|op_def",                           2, "读 op_def（dtype 与 SoC 能力交叉验证）"),
    (r"aclnn_\w+\.h\b",                             2, "读 header（ABI 事实）"),
    (r"examples?/.*\.(cpp|py)",                     2, "读 example（调用形态）"),
    (r"docs?/.*\.md",                               2, "读接口文档"),
    (r"spec\.json",                                 2, "写 / 校验 op.spec.json"),
    (r"design.*\.(yaml|yml|csv)|\bdesign\b",        2, "写 / 校验 ATK design"),
    (r"generator|execution_plugin|_plugin\.py",     2, "处理 generator / execution plugin"),
    (r"staging",                                    3, "建 clean staging"),
    (r"build\.sh|\bcmake\b|\bninja\b",              3, "★ fresh build（最长）"),
    (r"\.run\b[^|;]*--install|--install[^|;]*\.run|--quiet.*--install", 3, "★ 安装自定义算子包"),
    (r"\bnm\b\s+-|GetWorkspaceSize",                3, "★ vendor ELF 双符号与装载身份"),
    (r"ops-info|ops_info|kernel.*delivery",         3, "查该 SoC 的算子交付"),
    (r"\batk\b[^|;]*\baclnn\b[^|;]*performance",    6, "★ ATK 性能执行"),
    (r"op_statistic|op_summary|PROF_|msprof",       6, "采集 profiler kernel 数据"),
    (r"\batk\b[^|;]*\baclnn\b",                     5, "★ ATK 精度执行"),
    (r"acceptance\.(json|md)",                      7, "终态文件"),
    (r"receipts?/",                                 7, "读 / 写收据"),
    (r"npu-smi",                                 None, "读 NPU 全卡健康与占用（skill 外）"),
    (r"sha256sum|shasum",                        None, "算哈希（锚定 / 核验）"),
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
    print(f"\n{B}┌─ {' '.join(cells)}  {D}(✓=模型已勾 ▶=当前动作推测){O}   {Y}{label}{O}")


def setstep(n, forced=False):
    # 关键词推测只前进：读 op_def 之类的动作会在后期反复出现，倒退会让进度条来回跳。
    if n and not forced and n < state["step"]:
        return
    if n and n != state["step"]:
        state["step"] = n
        bar()


def render(ev):
    t = ev.get("type")

    if t == "system" and ev.get("subtype") == "init":
        state["t0"] = time.time()
        print(f"{B}══ 隔离验收会话 {str(ev.get('session_id','?'))[:8]} ══{O}")
        print(f"{D}   cwd    {ev.get('cwd')}{O}")
        sk = [s for s in (ev.get("skills") or []) if "accept" in str(s).lower()]
        print(f"{D}   plugin skill：{sk or '（无！隔离失败）'}{O}")
        return

    if t == "assistant":
        for b in ev.get("message", {}).get("content", []):
            k = b.get("type")
            if k == "text" and b.get("text", "").strip():
                txt = b["text"].strip()
                for m in re.finditer(r"-\s*\[x\]\s*步骤\s*(\d+)", txt):
                    state["done"].add(int(m.group(1)))
                m = re.search(r"步骤\s*(\d+)", txt)
                if m:
                    setstep(int(m.group(1)), forced=True)
                body = re.sub(r"^\s*-\s*\[[ x]\].*$", "", txt, flags=re.M).strip()
                if body:
                    print()
                    block(body, "  │ ", C)
            elif k == "thinking" and not BRIEF and b.get("thinking", "").strip():
                block(clip(b["thinking"], 300), "      ·思考· ", D)
            elif k == "tool_use":
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
