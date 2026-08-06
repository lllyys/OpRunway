#!/usr/bin/env python3
"""spec 变更门 —— 把「跑不通就改 spec 缩范围」从**静默动作**变成**留痕动作**。

病历（2026-08-06，aclnnRoll 试跑）：`acc-spec-extractor` 写下一句未经验证的论断，orchestrator
采信后把 `runner_form` 从 `cpp_extension` 改成 `cpp`（开工 26 分钟）；此后跑不通就继续改 spec，
dtype 从任务书要求的 8 种砍到 3 种。**每一道门都工作正常**（准入门拦住了、覆盖门抓到了、
没出假 PASS），塌的是另一件事：**没有任何机制记录「spec 被改过、谁改的、为什么」**。
于是「范围一路缩小」这个最该被人看见的现象，全程没有一处产物提到过。

本门只做一件事：**验收通路开跑前后，spec 的字节必须与一份带署名、带理由的收据对得上。**

---

## 收据

落点：`<报告根>/work/spec_change_receipt.json`

```jsonc
{ "schema": "oprunway.spec_change_receipt",
  "schema_version": 1,
  "spec_sha256":          "<当前 spec 的实际 sha256>",
  "previous_spec_sha256": "<上一版；首次为 null —— 有它才判得出「发生过变更」>",
  "change_reason":        "<非空、非占位符>",
  "confirmed_by":         "<非空、非占位符，不得自动填>" }
```

生命周期：

| 动作 | 命令 | 写什么 |
|---|---|---|
| 首次建立基线 | `--init --reason … --by …` | `previous_spec_sha256 = null` |
| spec 改过之后 | `--update --reason … --by …` | 旧 `spec_sha256` → `previous_spec_sha256`，新摘要 → `spec_sha256` |
| 只想看过不过 | `--check` | 不写盘 |

`--init` 在收据已存在时**拒绝执行**：否则「改完 spec 再 init 一次」就能把
`previous_spec_sha256` 抹回 `null`，变更历史一笔勾销。

---

## 四条判据（全过才放行）

1. 收据存在（且是普通文件、合法 JSON object、schema 对得上）；
2. `spec_sha256` == **校验方当场重算**的值 —— **不读自报**；
3. `confirmed_by` 非空，且不是自动填充占位符；
4. `change_reason` 非空，且不是自动填充占位符。

任一不过 → `BLOCKED(spec 变更未确认)`。

⚠ 判据 ② 重算的是**调用方传进来的那份 spec 原件**，绝不是 `<报告根>/spec.json` 那份
staging 副本。理由是硬的：staging 副本由 `run_workflow` 自己在**本轮**（或**上一轮**）写出，
拿它当被校对象等于自己给自己作证 —— 尤其入口门跑在 staging **之前**，那时目录里躺着的是
**上一轮**的副本，校它等于「换了 spec 也照样过」。

---

## 🔴 这道门证到哪一步（别读大了）

**它证的**：

- **内容完整性** —— 收据里那串摘要与「当场重算的 spec 字节」一致，即「这份收据说的就是这份 spec」；
- **有人显式声明过** —— 收据里有一条非空、非占位符的变更理由和确认人署名。

**它不证**：

- ❌ **「用户确认过」**。收据无密钥、无签名、无第二方见证 —— **编排层自己填一个像人名的
  字符串就能过**，本门查不出来。这是已知破绽，本批不解决；
- ❌ **「这次变更是合理的」**。`change_reason` 只校非空 + 非占位词，不理解内容；
- ❌ **「spec 从没被改过」**。`previous_spec_sha256` 是**自报**字段，本门不据它判定；
  能删收据重 `--init` 的人也能把它写成任何值。

所以本门的正确说法是「**spec 内容完整，且有人显式署名声明过**」。
**不许**把它的通过写成「用户已确认」「变更已获批准」——那是本门给不出的结论，
写了就又多一道「看着有、实际拦不住」的门（正是本仓最不能容忍的那类东西）。

要真堵死，得上密钥/第二方签名，或让确认动作发生在 agent 够不着的地方；不在本批范围内。
"""
import argparse
import hashlib
import json
import os
import re
import sys

#: 收据 schema 身份。改字段结构必须同时 bump `SCHEMA_VERSION`——旧收据不该被新判据静默复用。
SCHEMA = "oprunway.spec_change_receipt"
SCHEMA_VERSION = 1
#: 收据落点（相对报告根）。⚠ 刻意放在 `work/` 下、且**不在** `run_workflow._RESULT_FILES` 里：
#: 它是本轮的**输入侧凭证**，不是结论产物。跟着结论一起被作废的话，每次复跑都得重 `--init`，
#: 而 `previous_spec_sha256` 记的变更历史会在第一次复跑时全部蒸发——那正好毁掉本门唯一的价值。
RECEIPT_PARTS = ("work", "spec_change_receipt.json")
#: 拒绝时统一打这个标签（编排层与人读报告按它检索）。
BLOCKED_LABEL = "BLOCKED(spec 变更未确认)"

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
#: 归一化时剥掉的首尾符号（中英标点 + 常见分隔符）。剥完为空 = 这一栏什么都没说。
_STRIP_CHARS = " \t\r\n　.,;:!?、，。；：！？\"'`()[]{}<>《》「」【】-_*~/\\|+=#"

#: **自动填充占位符词表**：填了它们等于没填。
#: ⚠ 这是**启发式**，天生不可能穷举——它拦的是「随手敷衍」，拦不住「认真编一个人名」。
#:   失败方向选在少拦（漏一个占位词 = 一条本该被追问的记录混过去），而不是误伤真实署名。
#:   所以**不要**往里加任何可能是真人名/真团队名的词。
_PLACEHOLDER_TOKENS = frozenset({
    # 空/符号类（多数已被 `_STRIP_CHARS` 剥成空串，留着是为了让判据自解释）
    "-", "--", "---", "?", "??", "???", "x", "xx", "xxx", "n/a", "na", "n.a.",
    # 英文占位
    "none", "null", "nil", "nan", "empty", "unknown", "unspecified", "unset",
    "tbd", "tba", "todo", "fixme", "pending", "default", "placeholder",
    "example", "sample", "test", "testing", "dummy", "foo", "bar", "baz",
    # ⚠ 连字符/下划线已由 `_normalize` 归一成空格，所以这里只收「空格版」一份：
    #   `auto-fill` / `AUTO_FILL` / `autofill` 都会落到下面这几项上。
    "auto", "autofill", "auto fill", "auto filled", "automatic", "automated",
    "anonymous", "someone", "somebody", "anybody", "user", "the user",
    "operator", "owner", "maintainer", "system", "workflow", "pipeline",
    "script", "tool", "runner", "orchestrator", "agent", "subagent",
    "bot", "robot", "ai", "assistant", "llm", "model",
    "claude", "codex", "gpt", "copilot", "gemini", "cursor",
    # 中文占位
    "无", "没有", "未知", "未填", "未填写", "待填", "待填写", "待定", "待补",
    "自动", "自动填充", "占位", "占位符", "默认", "缺省", "同上", "略",
    "系统", "用户", "使用者", "某人", "本人", "机器", "脚本", "工具",
    "编排", "编排层", "流水线", "智能体", "代理", "助手", "测试", "示例",
})


# ── 基础工具 ────────────────────────────────────────────────────────────────
def receipt_path(out_dir):
    """收据的绝对/相对落点（跟随传进来的 `out_dir` 形态，不做 abspath）。"""
    return os.path.join(out_dir, *RECEIPT_PARTS)


def spec_digest(spec_path):
    """**当场重算** spec 文件字节的 sha256（判据 ② 的唯一取数口径）。

    ⚠ 调用点必须传**原件**路径。绝不能改成读 `<报告根>/spec.json`——那是 `run_workflow`
    自己 staging 出来的副本，拿它当被校对象就是自己给自己作证（详见模块文档）。
    """
    digest = hashlib.sha256()
    with open(spec_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(value):
    """署名/理由的归一化：连字符/下划线视同空格 → 折叠空白 → 剥首尾标点 → casefold。

    剥完为空 = 这一栏什么都没说。
    ⚠ 连字符/下划线要归一化：否则词表得同时收 `auto`、`autofill`、`auto fill`、`auto-fill`、
      `AUTO_FILL`……——那是靠穷举排版硬撑，一定漏。归一化只影响**占位符查表**，
      真实署名（`li-ming`）归一成 `li ming` 后照样不在词表里。
    """
    flattened = value.replace("-", " ").replace("_", " ")
    return " ".join(flattened.split()).strip(_STRIP_CHARS).casefold()


def _declaration_problems(number, field, value):
    """判据 ③/④ 共用的「非空且非占位符」校验。返回问题清单（空 = 过）。"""
    if not isinstance(value, str):
        return [f"{number} {field} 不是字符串（实际 {type(value).__name__}）"]
    norm = _normalize(value)
    if not norm:
        return [f"{number} {field} 为空（或只有空白/标点）：{value!r}"]
    if norm in _PLACEHOLDER_TOKENS:
        return [f"{number} {field}={value!r} 是自动填充占位符，不算「有人显式声明过」"]
    return []


def _load_receipt(path):
    """读收据。返回 `(payload|None, problems)`；payload 为 None 时 problems 必非空。"""
    if os.path.islink(path):
        # 与 staging 侧同一条口径：不跟随软链。收据是判定依据，跟着链走等于让门去读别处的文件。
        return None, [f"① 收据是符号链接（拒绝跟随）：{path}"]
    if not os.path.isfile(path):
        return None, [f"① 收据不存在：{path}"]
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as ex:
        return None, [f"① 收据读不出或不是合法 JSON：{type(ex).__name__}: {ex}"]
    if not isinstance(payload, dict):
        return None, ["① 收据不是 JSON object"]
    return payload, []


# ── 判据 ────────────────────────────────────────────────────────────────────
def check(spec_path, out_dir):
    """跑四条判据，返回**问题清单**（空列表 = 放行）。纯只读、零副作用。

    ⚠ 判据 ② 的被校对象是 `spec_path` 指的那份原件（见 `spec_digest` 的 ⚠）。
    """
    path = receipt_path(out_dir)
    receipt, problems = _load_receipt(path)
    if receipt is None:
        return problems

    # ① 结构：schema 身份对得上，`previous_spec_sha256` 这个键**必须显式在场**
    #    （首次写 null）。缺键与「写了 null」是两件事：前者是收据没按契约产，
    #    后者是「这是第一版」这句明确的话。
    if receipt.get("schema") != SCHEMA:
        problems.append(f"① 收据 schema={receipt.get('schema')!r}，应为 {SCHEMA!r}")
    if receipt.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"① 收据 schema_version={receipt.get('schema_version')!r}，"
            f"应为 {SCHEMA_VERSION!r}")
    if "previous_spec_sha256" not in receipt:
        problems.append("① 收据缺 previous_spec_sha256 键（首次也要显式写 null）")
    else:
        previous = receipt["previous_spec_sha256"]
        if previous is not None and not (isinstance(previous, str) and _HEX64.match(previous)):
            problems.append(
                f"① previous_spec_sha256 既不是 null 也不是 64 位小写 sha256：{previous!r}")

    # ② 摘要：**当场重算**，不读自报
    claimed = receipt.get("spec_sha256")
    if not (isinstance(claimed, str) and _HEX64.match(claimed)):
        problems.append(f"② spec_sha256 不是 64 位小写 sha256：{claimed!r}")
        claimed = None
    try:
        actual = spec_digest(spec_path)
    except OSError as ex:
        problems.append(f"② 读不到 spec、无从重算摘要：{spec_path!r}（{ex}）")
        actual = None
    if claimed is not None and actual is not None and claimed != actual:
        problems.append(
            f"② spec 已变更但收据未更新：\n"
            f"     收据记      {claimed}\n"
            f"     当场重算得  {actual}\n"
            f"     （校的是 --spec 指的**原件** {spec_path!r}，"
            f"不是报告目录里那份 staging 副本）")

    # ③/④ 有人显式声明过
    problems += _declaration_problems("③", "confirmed_by", receipt.get("confirmed_by"))
    problems += _declaration_problems("④", "change_reason", receipt.get("change_reason"))
    return problems


def blocked_message(spec_path, out_dir, stage, problems):
    """拒绝时的人读消息：说清哪一条没过、下一步敲什么、以及**这道门证不到什么**。"""
    lines = [f"{BLOCKED_LABEL} · 门位置：{stage}",
             f"  spec：{spec_path}",
             f"  收据：{receipt_path(out_dir)}",
             "  未过的判据："]
    lines += [f"    · {p}" for p in problems]
    lines += [
        "  怎么办（`spec_change_gate.py` 在本目录）：",
        "    · 首次建立基线：",
        "        python3 spec_change_gate.py --spec <spec.json> --out <报告根> --init \\",
        "            --reason \"<为什么是这份 spec>\" --by \"<确认人>\"",
        "    · spec 确实改过了：",
        "        python3 spec_change_gate.py --spec <spec.json> --out <报告根> --update \\",
        "            --reason \"<改了什么、为什么改>\" --by \"<确认人>\"",
        "  ⚠ 这道门要挡的是「跑不通就改 spec 缩范围」：改窄 dtype、砍用例数、换 runner_form"
        " 都在它的射程内。",
        "  ⚠ 它只证「spec 内容完整 + 有人显式署名声明过」，**不证**「用户确认过」"
        "（收据无密钥）——别把放行写成「变更已获批准」。",
    ]
    return "\n".join(lines)


def assert_confirmed(spec_path, out_dir, stage):
    """四条判据全过才返回；否则 `SystemExit(BLOCKED(spec 变更未确认) …)`。

    `stage` 只进人读消息（"进 Task1 之前" / "写验收产物之前"），不参与判定。
    """
    problems = check(spec_path, out_dir)
    if problems:
        raise SystemExit(blocked_message(spec_path, out_dir, stage, problems))


# ── 收据生命周期 ────────────────────────────────────────────────────────────
def _reject_bad_declaration(reason, by):
    """写盘前先按判据 ③/④ 校一遍：不让本工具产出一份自己都过不了门的收据。"""
    problems = _declaration_problems("③", "confirmed_by", by)
    problems += _declaration_problems("④", "change_reason", reason)
    if problems:
        raise SystemExit(
            "拒绝写收据——变更声明不合格：\n"
            + "\n".join(f"  · {p}" for p in problems)
            + "\n  · `--by` 要写**具体是谁**拍的板，`--reason` 要写改了什么、为什么改。\n"
              "  ⚠ 本工具不校验你写的是不是真人；它只拒绝明显的敷衍占位。")


def _write_receipt(out_dir, payload):
    """落盘（`O_NOFOLLOW`：落点是软链就拒绝跟随，绝不把收据写出报告目录）。"""
    path = receipt_path(out_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as ex:
        raise SystemExit(f"收据落盘失败（落点是软链或不可写？）：{path}（{ex}）")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def init_receipt(spec_path, out_dir, reason, by):
    """首次建立基线：`previous_spec_sha256 = null`。

    ⚠ 收据已存在时**拒绝**：否则「改完 spec 再 init 一次」= 把 `previous_spec_sha256`
    抹回 null，变更历史一笔勾销。要记一次变更请用 `--update`。
    """
    path = receipt_path(out_dir)
    if os.path.lexists(path):
        raise SystemExit(
            f"收据已存在：{path}\n"
            "  --init 只用于首次建立基线；它会把 previous_spec_sha256 写成 null，\n"
            "  对一份已有收据这么做等于抹掉变更历史。\n"
            "  · spec 改过了 → 改用 --update --reason … --by …；\n"
            "  · 真要另起一轮 → 换一个 --out 报告根。")
    _reject_bad_declaration(reason, by)
    payload = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
               "spec_sha256": spec_digest(spec_path),
               "previous_spec_sha256": None,
               "change_reason": reason, "confirmed_by": by}
    _write_receipt(out_dir, payload)
    return payload


def update_receipt(spec_path, out_dir, reason, by):
    """记一次变更：旧 `spec_sha256` → `previous_spec_sha256`，当场重算的新摘要 → `spec_sha256`。

    ⚠ 旧收据读不出/结构坏掉时**拒绝**：把一份坏收据静默改写成合格收据，等于用一次 update
    洗掉了「这里出过异常」这件事。
    """
    path = receipt_path(out_dir)
    old, problems = _load_receipt(path)
    if old is None:
        raise SystemExit(
            "拒绝 --update：\n" + "\n".join(f"  · {p}" for p in problems)
            + "\n  · 还没有基线 → 先 --init；\n"
              "  · 收据坏了 → 由人核实现场后处理，不由本工具静默改写。")
    previous = old.get("spec_sha256")
    if not (isinstance(previous, str) and _HEX64.match(previous)):
        raise SystemExit(
            f"拒绝 --update：旧收据的 spec_sha256 不是 64 位小写 sha256（{previous!r}），"
            "无从记录「上一版是什么」。")
    _reject_bad_declaration(reason, by)
    current = spec_digest(spec_path)
    payload = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
               "spec_sha256": current, "previous_spec_sha256": previous,
               "change_reason": reason, "confirmed_by": by}
    _write_receipt(out_dir, payload)
    if current == previous:
        # 如实说出来：这一次 update 记的是「重新声明」，不是「spec 变了」。
        print("[spec 变更门] ⚠ spec 字节未变（新旧摘要相同）——本次记录的是一次**重新声明**，"
              "不是一次变更。")
    return payload


# ── CLI ─────────────────────────────────────────────────────────────────────
#: `--check` 未过时的退出码。照 `fetch_source.py` 的 blocked 口径用 3，与 argparse 的用法错误
#: （2）和一般异常（1）分开——编排层要能把「门没过」与「命令敲错了」区分开。
EXIT_BLOCKED = 3


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="spec 变更门：验收通路上，spec 的字节必须与一份带署名、带理由的收据对得上。"
                    "⚠ 本门只证「内容完整 + 有人显式署名声明过」，**不证**「用户确认过」"
                    "（收据无密钥，编排层自己填就能过）——别把放行描述成变更已获批准。")
    parser.add_argument("--spec", required=True, help="spec.json **原件**路径（不是报告目录里的副本）")
    parser.add_argument("--out", required=True, metavar="DIR",
                        help=f"报告根；收据落 {os.path.join(*RECEIPT_PARTS)}")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--init", action="store_true", help="首次建立基线（previous_spec_sha256=null）")
    action.add_argument("--update", action="store_true", help="记一次 spec 变更")
    action.add_argument("--check", action="store_true", help="只校验、不写盘")
    parser.add_argument("--reason", default=None, help="变更理由（--init/--update 必给，非空非占位符）")
    parser.add_argument("--by", default=None, help="确认人（--init/--update 必给，非空非占位符）")
    args = parser.parse_args(argv)

    if args.init or args.update:
        # ⚠ 用 argparse 的 required 表达不了「随 action 变化的必填」，只能在这里校。
        #   缺席一律拒——不给缺省、更不自动填：自动填出来的署名正是本门要拦的东西。
        missing = [f for f, v in (("--reason", args.reason), ("--by", args.by)) if v is None]
        if missing:
            parser.error(f"{'--init' if args.init else '--update'} 必须同时给 "
                         f"{' 与 '.join(missing)}（不得自动填）")
        payload = (init_receipt if args.init else update_receipt)(
            args.spec, args.out, args.reason, args.by)
        print(f"[spec 变更门] 已写 {receipt_path(args.out)}")
        print(f"  spec_sha256          = {payload['spec_sha256']}")
        print(f"  previous_spec_sha256 = {payload['previous_spec_sha256']}")
        print(f"  confirmed_by         = {payload['confirmed_by']}")
        print("  ⚠ 本收据证明「内容完整 + 有人显式署名声明过」，不证明「用户确认过」。")
        return 0

    problems = check(args.spec, args.out)
    if problems:
        print(blocked_message(args.spec, args.out, "--check", problems), file=sys.stderr)
        return EXIT_BLOCKED
    print(f"[spec 变更门] PASSED · {receipt_path(args.out)} 与 {args.spec} 对得上"
          "（内容完整 + 有人显式署名声明过；**不**代表用户确认过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
