#!/usr/bin/env python3
"""隔离验收会话的进度视图：步骤条 + 人话 + 真实命令与结果。

用法：
    python3 watch.py [jsonl路径] [选项]

选项：
    --brief         只看人话，不展开命令与结果
    --full          结果不截断（很长，配合 less 用）
    --width N       命令/结果每条最多显示的字符数（默认 700）
    --replay        从头快速回放已有内容后再跟随（默认就是这样）

步骤 8 是最长的一段（输入锚定 → staging → 用例生成 → build → 安装 → 双符号 → 执行 → 裁决），
进入该步后会另出一条子进度条。

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

STEPS = ["确认适用", "生成 op.spec.json", "生成 ATK design", "绑定官方 bundle",
         "选择能力落点", "环境前置检查", "选定并锁定物理卡", "在锁内执行验收",
         "核对终态判据", "产出收据与终态"]

# 步骤 8 内部的八个环节
SUB = ["输入锚定", "staging", "用例生成", "build", "安装", "双符号", "ATK 执行", "裁决"]

B, D, G, Y, R, C, M, BL, O = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m",
                              "\033[36m", "\033[35m", "\033[34m", "\033[0m")

# (正则, 主步骤, 子环节序号, 人话)
GLOSS = [
    (r"task_doc|taskdoc|任务书",                    1, None, "读任务书（语义/硬件/阈值权威）"),
    (r"_def\.cpp|op_def",                           1, None, "读 op_def（dtype 与 SoC 能力交叉验证）"),
    (r"aclnn_\w+\.h\b",                             1, None, "读 header（ABI 事实）"),
    (r"examples?/.*\.(cpp|py)",                     1, None, "读 example（调用形态）"),
    (r"docs?/.*\.md",                               1, None, "读接口文档"),
    (r"witnesses|参考答案",                          4, None, "⚠ 接触 witness 夹具"),
    (r"selftest|self_test|self-test",               4, None, "查源码自带 self-test"),
    (r"spec\.json",                                 2, None, "写 / 校验 op.spec.json"),
    (r"design.*\.(yaml|yml|csv)|\bdesign\b",        3, None, "写 / 校验 ATK design"),
    (r"generator|execution_plugin|_plugin\.py",     5, None, "处理 generator / execution plugin"),
    (r"atk[^|;]*--version|--version[^|;]*atk",      6, None, "探测 ATK 版本"),
    (r"npu-smi",                                    7, None, "读 NPU 全卡健康与占用"),
    (r"\bflock\b",                                  7, None, "对物理卡加互斥锁"),
    (r"\bcp -|\brsync\b|tar .*-C .*inputs",         8, 1,    "把调用方输入复制进 session"),
    (r"staging",                                    8, 2,    "建 clean staging"),
    (r"\batk\b[^|;]*\bcase\b",                      8, 3,    "★ ATK 生成 caseset"),
    (r"build\.sh|\bcmake\b|\bninja\b",              8, 4,    "★ fresh build（最长）"),
    (r"\.run\b[^|;]*--install|--install[^|;]*\.run|--quiet.*--install", 8, 5, "★ 安装自定义算子包"),
    (r"\bnm\b\s+-|GetWorkspaceSize",                8, 6,    "★ vendor ELF 双符号验证"),
    (r"\batk\b[^|;]*\baclnn\b[^|;]*performance",    8, 7,    "★ ATK 性能执行"),
    (r"\batk\b[^|;]*\baclnn\b",                     8, 7,    "★ ATK 精度执行"),
    (r"op_statistic|op_summary|PROF_|msprof",       8, 7,    "采集 profiler kernel 数据"),
    (r"acceptance\.(json|md)",                      9, 8,    "终态文件"),
    (r"receipts?/",                                10, None, "读 / 写收据"),
    (r"sha256sum|shasum",                        None, None, "算哈希（锚定 / 核验）"),
    (r"python3?\s+-c|\bimport\b",                None, None, "探测 Python / 依赖"),
    (r"find .*-type f|\bls -",                   None, None, "清点文件"),
    (r"\bmkdir\b",                               None, None, "建目录"),
    (r"\bgrep\b|\brg\b",                         None, None, "搜内容"),
    (r"\bcat\b|sed -n|\bhead\b|\btail\b|\bwc\b", None, None, "读文件"),
]

state = {"t0": None, "step": 0, "sub": 0, "done": set(), "subdone": set(), "live": False}


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
    for pat, step, sub, text in GLOSS:
        if re.search(pat, raw, re.I):
            return step, sub, text, raw
    if name == "TodoWrite":
        return None, None, None, raw
    return None, None, name, raw


def stamp():
    # 事件流不带时间戳。回放历史时无从知道真实时刻，标 --:--，不编造。
    if state["t0"] is None or not state["live"]:
        return "  --:-- "
    s = int(time.time() - state["t0"])
    return f"  {s//60:02d}:{s%60:02d} "


def bar():
    cells = []
    for i in range(1, 11):
        if i in state["done"]:
            cells.append(f"{G}{i}✓{O}")
        elif i == state["step"]:
            cells.append(f"{Y}{B}{i}▶{O}")
        else:
            cells.append(f"{D}{i}·{O}")
    label = STEPS[state["step"] - 1] if state["step"] else "启动中"
    print(f"\n{B}┌─ {' '.join(cells)}  {D}(✓=模型已勾 ▶=当前动作推测){O}   {Y}{label}{O}")


def subbar():
    cells = []
    for i, nm in enumerate(SUB, 1):
        if i in state["subdone"]:
            cells.append(f"{G}{nm}{O}")
        elif i == state["sub"]:
            cells.append(f"{Y}{B}{nm}{O}")
        else:
            cells.append(f"{D}{nm}{O}")
    print(f"{B}└─ 步骤8: {D}·{O} ".rstrip() + f" {D}·{O} ".join(cells))


def setstep(n, sub=None, forced=False):
    changed = False
    # 关键词推测只前进：读 op_def 之类的动作会在后期反复出现，倒退会让进度条来回跳。
    if n and not forced and n < state["step"]:
        n = None
    if n and n != state["step"]:
        state["step"] = n
        state["sub"] = 0
        changed = True
        bar()
    if sub and sub != state["sub"]:
        if state["sub"]:
            state["subdone"].add(state["sub"])
        state["sub"] = sub
        subbar()
        changed = True
    return changed


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
                step, sub, text, raw = gloss(b.get("name"), b.get("input", {}))
                if text is None:
                    continue
                setstep(step, sub)
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
        print(f"{Y}会话结束不等于验收结束{O}{D}：还要确认目标机进程已退出、设备锁已释放、"
              f"终态文件已生成，三者同时成立才算完。{O}")


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
