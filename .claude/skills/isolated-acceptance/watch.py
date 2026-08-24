#!/usr/bin/env python3
"""隔离验收双会话的进度视图：分侧步骤条 + 人话 + 真实命令与结果。

用法：
    python3 watch.py [jsonl路径] [选项]

选项：
    --side gen|accept  显式指定生成侧或验收侧；省略时按量具锚点自动判侧
    --brief            只看人话，不展开命令与结果
    --full             结果不截断（很长，配合 less 用）
    --width N          命令/结果每条最多显示的字符数（默认 700）

生成侧封印和验收侧 build / ATK 执行可能安静很久，属正常。

✓ 只表示模型自己在回复里勾掉了该步；▶ 是按当前动作的关键词推测，不代表真的完成。
"""
import argparse
import json
import os
import re
import shutil
import sys
import textwrap
import time


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Claude stream-json 日志路径")
    parser.add_argument("--side", choices=("gen", "accept"), help="会话侧别")
    parser.add_argument("--brief", action="store_true", help="只看人话")
    parser.add_argument("--full", action="store_true", help="结果不截断")
    parser.add_argument("--width", type=int, default=700, help="单条最大字符数")
    return parser.parse_args()


ARGS = parse_args()
PATH = ARGS.path
BRIEF = ARGS.brief
FULL = ARGS.full
WIDTH = 10**9 if FULL else ARGS.width
COLS = min(shutil.get_terminal_size((120, 40)).columns, 140)

# 两个平级 skill 各有一条五步梯子。脚本锚点按当前发布切片维护。
STEPS = {
    "gen": ["任务书解读与环境", "必测集", "物化与 YAML", "冻结", "封印"],
    "accept": ["接收与环境", "构建部署", "精度", "性能", "裁决与报告"],
}
SIDE_LABELS = {"gen": "生成（gen）", "accept": "验收（accept）"}

B, D, G, Y, R, C, M, BL, O = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m",
                              "\033[36m", "\033[35m", "\033[34m", "\033[0m")

# (正则, 步骤, 人话)。步骤为 None 表示只打印、不推进进度条。
# 首个命中即返回，所以顺序有意义：越专用的模式越靠前。
GLOSS = {
    "gen": [
        (r"seal_bundle\.py",                                      5, "★ 封印交接包"),
        (r"freeze_inputs\.py",                                    4, "冻结输入与生成物"),
        (r"make_yaml\.py|validate_cases\.py",                    3, "物化 / 校验 YAML caseset"),
        (r"align_signatures\.py|check_(?:adapter_binding|signature_contract)\.py", 3,
                                                                    "校验接口与 adapter 绑定"),
        (r"(?:^|[/\s])atk\s+case\b",                              3, "★ ATK 物化 caseset"),
        (r"make_must_cover\.py|check_coverage\.py",               2, "生成 / 校验必测集"),
        (r"derive_interface\.py",                                 2, "从任务书派生接口契约"),
        (r"probe_(?:env|atk_capabilities)\.py|atk_lookup\.py",    1, "探测生成环境与 ATK 能力"),
        (r"task_doc|taskdoc|任务书",                               1, "解读任务书"),
        (r"atk[^|;]*--version|--version[^|;]*atk",                  1, "探测 ATK 版本与绝对路径"),
    ],
    "accept": [
        (r"check_bundle\.py",                                     1, "★ 接收并核验交接包"),
        (r"probe_env\.py",                                        1, "探测验收环境"),
        (r"run_atk_task\.py[^\n]*(?:performance|perf)",           4, "★ ATK 性能执行"),
        (r"select_perf_cases\.py|op_statistic|op_summary|PROF_|msprof", 4,
                                                                    "选择性能样例 / 采集 kernel 数据"),
        (r"freeze_golden\.py",                                    3, "冻结精度真值"),
        (r"check_golden_source\.py|capture_reference\.py",       3, "核验 / 采集精度真值"),
        (r"run_atk_task\.py",                                     3, "★ ATK 精度执行"),
        (r"parse_atk_report\.py",                                 3, "解析 ATK 精度报告"),
        (r"build\.sh|\bcmake\b|\bninja\b",                       2, "★ fresh build（最长）"),
        (r"\.run\b[^|;]*--install|--install[^|;]*\.run|--quiet.*--install", 2,
                                                                    "★ 安装自定义算子包"),
        (r"\bnm\b\s+-|GetWorkspaceSize|check_(?:opapi|soc)_binding\.py", 2,
                                                                    "核对装载身份与目标 SoC 绑定"),
        (r"ops-info|ops_info|kernel.*delivery",                    2, "查目标 SoC 的算子交付"),
        (r"verdict\.py|acceptance\.(?:json|md)",                  5, "★ 生成裁决与终态文件"),
        (r"make_repro\.py|receipts?/",                            5, "闭合复现材料 / 收据"),
    ],
}

COMMON_GLOSS = [
    (r"\bcp -|\brsync\b|tar .*-C .*inputs",       None, "复制输入"),
    (r"witnesses|参考答案",                       None, "⚠ 接触 witness 夹具"),
    (r"selftest|self_test|self-test",             None, "查源码自带 self-test"),
    (r"npu-smi",                                  None, "读 NPU 全卡健康与占用（skill 外）"),
    (r"sha256sum|shasum",                         None, "算哈希（锚定 / 核验）"),
    (r"python3?\s+-c|\bimport\b",                 None, "探测 Python / 依赖"),
    (r"find .*-type f|\bls -",                    None, "清点文件"),
    (r"\bmkdir\b",                                None, "建目录"),
    (r"\bgrep\b|\brg\b",                         None, "搜内容"),
    (r"\bcat\b|sed -n|\bhead\b|\btail\b|\bwc\b", None, "读文件"),
]

state = {
    "t0": None,
    "step": 0,
    "done": set(),
    "live": False,
    "side": ARGS.side,
    "side_source": "--side" if ARGS.side else None,
}


def detect_side(text):
    """按文本里最先出现的封印/接收量具判侧。"""
    hits = []
    for side, marker in (("gen", "seal_bundle"), ("accept", "check_bundle")):
        position = text.find(marker)
        if position >= 0:
            hits.append((position, side))
    return min(hits)[1] if hits else None


def detect_side_from_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return detect_side(stream.read())
    except FileNotFoundError:
        return None


def select_side(side, source):
    if state["side"] is None:
        state["side"] = side
        state["side_source"] = source


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
    patterns = GLOSS.get(state["side"], []) + COMMON_GLOSS
    for pat, step, text in patterns:
        if re.search(pat, raw, re.I):
            return step, text, raw
    if name == "TodoWrite":
        return None, None, raw
    return None, name, raw


def stamp():
    # 事件流不带时间戳。回放历史时无从知道真实时刻，标 --:--，不编造。
    if state["t0"] is None or not state["live"]:
        return "  --:-- "
    seconds = int(time.time() - state["t0"])
    return f"  {seconds//60:02d}:{seconds%60:02d} "


def bar():
    steps = STEPS[state["side"]]
    cells = []
    for index in range(1, len(steps) + 1):
        if index in state["done"]:
            cells.append(f"{G}{index}✓{O}")
        elif index == state["step"]:
            cells.append(f"{Y}{B}{index}▶{O}")
        else:
            cells.append(f"{D}{index}·{O}")
    label = steps[state["step"] - 1] if state["step"] else "启动中"
    print(f"\n{B}┌─ {' '.join(cells)}  {D}(✓=模型已勾 ▶=当前动作推测){O}   {Y}{label}{O}")


def setstep(number, forced=False):
    steps = STEPS[state["side"]]
    if not number or not 1 <= number <= len(steps):
        return
    # 关键词推测只前进：早期文件会在后期反复读取，倒退会让进度条来回跳。
    if not forced and number < state["step"]:
        return
    if number != state["step"]:
        state["step"] = number
        bar()


def render(event):
    event_type = event.get("type")

    if event_type == "system" and event.get("subtype") == "init":
        state["t0"] = time.time()
        print(f"{B}══ 隔离验收会话 {str(event.get('session_id','?'))[:8]} ══{O}")
        print(f"{D}   cwd    {event.get('cwd')}{O}")
        print(f"{D}   侧别   {SIDE_LABELS[state['side']]}（{state['side_source']}）{O}")
        skills = [
            skill for skill in (event.get("skills") or [])
            if re.search(r"repo-task-(?:case-gen|atk-accept)", str(skill), re.I)
        ]
        print(f"{D}   plugin skills：{skills or '（无！隔离失败）'}{O}")
        return

    if event_type == "assistant":
        for item in event.get("message", {}).get("content", []):
            kind = item.get("type")
            if kind == "text" and item.get("text", "").strip():
                reply = item["text"].strip()
                for match in re.finditer(r"-\s*\[x\]\s*步骤\s*(\d+)", reply):
                    number = int(match.group(1))
                    if 1 <= number <= len(STEPS[state["side"]]):
                        state["done"].add(number)
                match = re.search(r"步骤\s*(\d+)", reply)
                if match:
                    setstep(int(match.group(1)), forced=True)
                body = re.sub(r"^\s*-\s*\[[ x]\].*$", "", reply, flags=re.M).strip()
                if body:
                    print()
                    block(body, "  │ ", C)
            elif kind == "thinking" and not BRIEF and item.get("thinking", "").strip():
                block(clip(item["thinking"], 300), "      ·思考· ", D)
            elif kind == "tool_use":
                step, text, raw = gloss(item.get("name"), item.get("input", {}))
                if text is None:
                    continue
                setstep(step)
                head = (M + B) if text.startswith("★") else ((R + B) if text.startswith("⚠") else "")
                print(f"{D}{stamp()}{O}{head}{text}{O}")
                if not BRIEF:
                    block(clip(raw), "         $ ", BL)
        return

    if event_type == "user":
        for item in event.get("message", {}).get("content", []):
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            content = item.get("content")
            if isinstance(content, list):
                content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
            content = re.sub(r"^\s*Authorized users only\..*$", "", str(content or ""), flags=re.M)
            content = clip(content)
            if item.get("is_error"):
                block(content, "         ✗ ", R)
            elif not BRIEF and content.strip():
                block(content, "         → ", D)
        return

    if event_type == "result":
        print(f"\n{B}══ 结束 ══{O}")
        bad = event.get("is_error")
        print(f"   {(R+'❌ 出错') if bad else (G+'✅ 正常收尾')}{O}   "
              f"{event.get('num_turns')} 轮   {round(event.get('duration_ms',0)/1000)} 秒   "
              f"${event.get('total_cost_usd')}")
        print()
        block(str(event.get("result", "")), "  ", G)
        print()
        if state["side"] == "gen":
            print(f"{Y}生成会话退出后仍须核验交接{O}{D}：主机只确认 evidence/bundle.json 存在，"
                  f"并记录其 SHA-256 与工作目录；缺任一项就停。{O}")
        else:
            print(f"{Y}会话结束不等于验收结束{O}{D}：还要确认目标机进程已退出、"
                  f"终态文件已生成，两者同时成立才算完。{O}")


def main():
    while not os.path.exists(PATH):
        time.sleep(1)

    if state["side"] is None:
        detected = detect_side_from_file(PATH)
        if detected:
            select_side(detected, "事件流锚点")
        else:
            print(f"{D}等待判侧：事件流命中 seal_bundle.py → gen，命中 check_bundle.py → accept；"
                  f"也可用 --side 显式指定。{O}")

    pending = []
    with open(PATH, encoding="utf-8", errors="replace") as stream:
        while True:
            line = stream.readline()
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
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if state["side"] is None:
                pending.append(event)
                detected = detect_side(line)
                if not detected:
                    continue
                select_side(detected, "事件流锚点")
                for buffered in pending:
                    render(buffered)
                pending.clear()
            else:
                render(event)
            sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{D}(停止观察，会话仍在后台运行){O}")
