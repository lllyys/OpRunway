#!/usr/bin/env python3
"""spec 变更门：验收通路上，**跑的这份 spec 必须有一份「谁、为什么」的收据**。

病历（2026-08-06，aclnnRoll 试跑）：跑不通就改 spec——`runner_form` 从 `cpp_extension`
改成 `cpp`（结果整轮物理上产不出裁决）、dtype 从任务书要求的 8 种砍到 3 种。三次字段收缩
全程没人记过一句「为什么改」，事后只能靠会话时间戳反推。**门本身当时都工作正常**
（准入门拦住了、覆盖门抓到了、没出假 PASS），缺的是「改 spec 这件事本身要留痕并被核对」。

---

## 这道门证到哪一步（⚠ 别读大了）

**证得到**：

1. 报告目录里确实有一份 spec 变更收据，且它**结构完整**——四个业务字段齐、类型对、
   内容寻址 envelope 的 domain 与摘要自洽（能挡住半份写入、手滑改坏、拿别的 JSON 冒充）；
2. 收据认领的 `spec_sha256` 与**校验方当场重算**的 spec 原件字节**一致**。
   也就是「本轮跑的这份 spec，就是收据认领的那份」。改一个字节而不更新收据 → 当场 BLOCKED；
3. 有人**显式声明**过变更理由与确认人姓名（两者都非空、都不是自动填充占位符）。

**证不到**（逐条写清，别把它描述成「用户已确认」的证明）：

- ⚠ **确认人身份**。收据没有密钥、没有签名，编排层自己填一句 `--by lys` 就能过。
  本门只能说「**有人在收据里写下了这个名字**」，**不能**说「这个人真的看过并同意了」。
  治理批判定里这条记作「**人确认身份不可证**」，本批**不解决**它。
- ⚠ **理由是否属实**。`change_reason` 只被校「非空且非占位符」，内容真假无从核验。
- ⚠ **删档重来**。把收据删掉再 `--init`，`previous_spec_sha256` 就回到 `null`，
  「发生过变更」这件事在文件系统上没了痕迹。本门拦得住「改 spec 不更新收据」，
  **拦不住**「改 spec + 重建收据」——后者至少需要一次显式的、留在命令历史里的动作，
  这就是本门全部的威慑力所在，不要夸大成防篡改。

所以本门的正确读法是：**它把「悄悄改 spec」变成「必须显式声明一次」**，不是身份认证。

---

## 收据

落点 `<报告根>/work/spec_change_receipt.json`，内容寻址 envelope（domain 见 `DOMAIN`），payload：

```jsonc
{ "schema": "oprunway.spec_change_receipt", "schema_version": 1,
  "spec_sha256":          "<当前 spec 原件的实际 sha256>",
  "previous_spec_sha256": "<上一版；首次为 null —— 有它才判得出「发生过变更」>",
  "change_reason":        "<非空、非占位符>",
  "confirmed_by":         "<非空、非占位符；不得自动填>" }
```

用法：

```bash
# 首轮（previous_spec_sha256 = null）
python3 spec_change_gate.py --out <报告根> --spec <spec.json> \
        --init --reason "首轮：按任务书抽出的初始 spec" --by "<确认人>"
# 改过 spec 之后（把旧 spec_sha256 写进 previous_spec_sha256）
python3 spec_change_gate.py --out <报告根> --spec <spec.json> \
        --update --reason "<为什么改>" --by "<确认人>"
# 只查（run_workflow 内嵌同一份判定，这里是给人和 CI 用的独立入口）
python3 spec_change_gate.py --out <报告根> --spec <spec.json> --check
```

退出码：`0` 通过 / `1` BLOCKED(spec 变更未确认) / `2` 用法错或写入被拒。

---

## 判据（四条全过才放行）

| # | 判据 | 不过时的失败方向 |
|---:|---|---|
| ① | 收据存在且 envelope 可信 | BLOCKED |
| ② | `spec_sha256` == **校验方当场重算**的值（**不读自报**） | BLOCKED |
| ③ | `confirmed_by` 非空且非自动填充占位符 | BLOCKED |
| ④ | `change_reason` 非空且非自动填充占位符 | BLOCKED |

⚠ 判据 ④ 是**初稿声明了却没校**的那一条（2026-08-06 codex 审出）——「声明了一道门」和
「那道门真的在跑」是两件事，本模块的 `SpecChangeReceiptTest` 逐条钉住，删掉任一 `raise` 必红。

⚠ 判据 ② 校的是 **spec 原件**（`run_workflow` 的位置实参），**不是** `--out` 里 CP-E staging
出来的那份副本。副本是本进程自己刚写的，拿它当被校对象等于**自己给自己作证**。

⚠ 占位符黑名单只是**最低门槛**，不是身份证明：它挡的是「`--by auto` / `--reason TODO`」
这种一望即知的自动填充，挡不住随手写个人名。见上面「证不到」那节。
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import content_address  # noqa: E402

#: 内容寻址 domain（域分离：别的工件的摘要拿来当本收据用会被 `read_artifact` 当场拒）。
DOMAIN = "oprunway/spec-change/v1"
SCHEMA = "oprunway.spec_change_receipt"
SCHEMA_VERSION = 1
#: 相对**报告根**的收据落点。与 `run_workflow` 的 `work = <out>/work` 同一层。
RECEIPT_REL = os.path.join("work", "spec_change_receipt.json")
#: 人读裁决串。`run_workflow` 拒跑时逐字引用它，别在两处各写一份。
BLOCKED_LABEL = "BLOCKED(spec 变更未确认)"

#: payload 字段**严格相等**（不是「至少包含」）：多字段一律拒。
#: 理由同 `content_address._ARTIFACT_KEYS`——留一条「多写的键被忽略」的缝，就等于留一条
#: 「在收据里夹带私货、而校验方视而不见」的路。
_PAYLOAD_KEYS = frozenset({
    "schema", "schema_version", "spec_sha256", "previous_spec_sha256",
    "change_reason", "confirmed_by"})

#: 「一望即知是自动填充」的确认人。归一化（折叠空白 + casefold）后**整串相等**才算命中，
#: 所以 `auto` 被拒、`autodesk 张三` 不会被误伤。
#: ⚠ 这张表**不是**身份校验，只是最低门槛；它拦不住随手编一个人名（见模块 docstring）。
_PLACEHOLDER_CONFIRMERS = frozenset({
    "n/a", "na", "none", "null", "nil", "nan", "unknown", "unspecified", "anonymous",
    "tbd", "todo", "fixme", "xxx", "x", "test", "example", "sample", "placeholder",
    "auto", "automatic", "autofill", "default", "system", "bot", "ci", "cli",
    "agent", "orchestrator", "assistant", "ai", "llm", "model", "robot",
    "claude", "codex", "gpt", "copilot",
    "自动", "自动填充", "系统", "机器", "占位", "占位符", "待定", "未知", "无", "匿名",
    "用户", "确认人", "某人",
})
#: 同上，用于变更理由。刻意比确认人那张表**短**——理由是自由文本，误伤成本更高。
_PLACEHOLDER_REASONS = frozenset({
    "n/a", "na", "none", "null", "nil", "unknown", "unspecified",
    "tbd", "todo", "fixme", "xxx", "x", "test", "placeholder", "reason", "change",
    "auto", "automatic", "default", "no reason", "noreason",
    "自动", "占位", "占位符", "待定", "未知", "无", "无理由", "理由", "变更", "修改", "调整",
})
#: 纯标点/空白串（`---`、`…`、`/`）一律按占位符处理：它们「非空」但什么都没说。
_PUNCT_ONLY = frozenset(" \t\r\n-_.,;:!?/\\|*+=~`'\"<>()[]{}^&%$#@"
                        "（）【】《》〈〉、，。；：！？—…·「」『』")


class SpecChangeError(RuntimeError):
    """收据写入被拒（用法错、旧收据不可用、声明字段是占位符）。CLI 退出码 2。"""


class SpecChangeBlocked(SpecChangeError):
    """门判定不通过 → `BLOCKED(spec 变更未确认)`。CLI 退出码 1。

    ⚠ 是 `SpecChangeError` 的子类：调用方（`run_workflow`）捕 **基类** 即可 fail-closed，
    不会因为漏捕某个子类而把一次「门自己出错」读成「门通过了」。
    """


def _hex64(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _normalize(text):
    """折叠空白后的显示串；非字符串返回 None（交由调用方按「缺失」处理）。"""
    if not isinstance(text, str):
        return None
    return " ".join(text.split())


def _is_placeholder(text, vocabulary):
    """空 / 纯标点 / 命中黑名单 → True。**类型不对也算 True**（fail-closed）。"""
    normalized = _normalize(text)
    if not normalized:
        return True
    if all(ch in _PUNCT_ONLY for ch in normalized):
        return True
    return normalized.casefold() in vocabulary


def spec_sha256(spec_path):
    """**当场重算** spec 原件字节的 sha256。判据 ② 的「不读自报」就落在这一行。"""
    if not os.path.isfile(spec_path):
        raise SpecChangeBlocked(
            f"spec 不存在或不是普通文件，无法重算摘要：{spec_path!r}")
    try:
        with open(spec_path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError as ex:
        raise SpecChangeBlocked(f"读取 spec 失败：{spec_path!r}：{ex}") from ex


def receipt_path(out_dir):
    return os.path.join(out_dir, RECEIPT_REL)


def _missing_message(out_dir):
    return (
        f"报告根 {out_dir!r} 下没有 spec 变更收据（{RECEIPT_REL}）。\n"
        f"  → 本轮跑的这份 spec 没有任何人认领过「为什么是它」。先落一份收据：\n"
        f"     python3 spec_change_gate.py --out {out_dir!r} --spec <spec.json> \\\n"
        f"             --init --reason \"<为什么是这份 spec>\" --by \"<确认人>\"\n"
        f"  → 若 spec 是在上一轮基础上**改过**的，用 --update（它会把旧 spec_sha256 记进\n"
        f"     previous_spec_sha256，「发生过变更」这件事才留得下痕迹）。\n"
        f"  → 只想本地自检用例链（非验收）→ 显式加 --mode mock：本门只约束验收通路。")


def read_receipt(out_dir):
    """读出并复核收据 envelope；任何读不出/不自洽都是 BLOCKED，绝不返回半份 payload。"""
    path = receipt_path(out_dir)
    if not os.path.isfile(path):
        raise SpecChangeBlocked(_missing_message(out_dir))
    try:
        return content_address.read_artifact(out_dir, RECEIPT_REL, DOMAIN)
    except (content_address.ContentAddressError, OSError, ValueError) as ex:
        raise SpecChangeBlocked(
            f"spec 变更收据不可信（envelope / domain / 摘要不自洽）：{path!r}：{ex}\n"
            f"  → 收据坏了不等于「没改过 spec」，一律拒；要重建请显式 --init/--update。") from ex


def check_payload(payload, actual_spec_sha256):
    """按四条判据核 payload（判据 ② 的对照值由调用方**当场重算**后传进来）。

    通过则原样返回 payload；否则 `SpecChangeBlocked`。判定顺序刻意与文档的 ①②③④ 一致，
    错误信息说清是哪一条不过。
    """
    # —— 结构（判据 ① 的后半：envelope 之外，payload 自身也要成形）——————————————
    if not isinstance(payload, dict):
        raise SpecChangeBlocked(
            f"收据 payload 必须是 JSON object，实得 {type(payload).__name__}")
    if frozenset(payload) != _PAYLOAD_KEYS:
        raise SpecChangeBlocked(
            f"收据字段必须严格等于 {sorted(_PAYLOAD_KEYS)}，实得 {sorted(payload)}")
    version = payload["schema_version"]
    # ⚠ `isinstance(True, int)` 为真且 `True == 1`——不显式排 bool 的话，
    #   一份 `"schema_version": true` 的收据能当 v1 混过去。
    if (payload["schema"] != SCHEMA or isinstance(version, bool)
            or not isinstance(version, int) or version != SCHEMA_VERSION):
        raise SpecChangeBlocked(
            f"收据 schema 不认：期望 {SCHEMA!r}/v{SCHEMA_VERSION}，"
            f"实得 {payload['schema']!r}/v{version!r}")
    # —— 判据 ②：spec_sha256 == 当场重算 ————————————————————————————————————
    recorded = payload["spec_sha256"]
    if not _hex64(recorded):
        raise SpecChangeBlocked(f"收据 spec_sha256 不是小写 sha256：{recorded!r}")
    if recorded != actual_spec_sha256:
        raise SpecChangeBlocked(
            f"spec 与收据不符（收据认领 {recorded}，当场重算 {actual_spec_sha256}）。\n"
            f"  → spec 变了却没更新收据。这正是本门要拦的那件事：先说清为什么改。\n"
            f"     python3 spec_change_gate.py --out <报告根> --spec <spec.json> \\\n"
            f"             --update --reason \"<为什么改>\" --by \"<确认人>\"")
    previous = payload["previous_spec_sha256"]
    if previous is not None and not _hex64(previous):
        raise SpecChangeBlocked(
            f"previous_spec_sha256 必须是 null 或小写 sha256：{previous!r}")
    if previous == recorded:
        # 「上一版 == 这一版」= 一份自称记录了变更、其实什么都没变的收据。
        raise SpecChangeBlocked(
            "previous_spec_sha256 与 spec_sha256 相同 —— 这份收据自称记了一次变更，"
            "实际前后同一份 spec，判为写坏。")
    # —— 判据 ③：confirmed_by ——————————————————————————————————————————
    if _is_placeholder(payload["confirmed_by"], _PLACEHOLDER_CONFIRMERS):
        raise SpecChangeBlocked(
            f"confirmed_by 为空或是自动填充占位符：{payload['confirmed_by']!r}\n"
            f"  → 必须由人显式声明。⚠ 本门只能核「有人写下了这个名字」，"
            f"核不了「这个人真的确认过」（模块 docstring 已如实记账）。")
    # —— 判据 ④：change_reason（**初稿声明了却没校**的那一条）————————————————
    if _is_placeholder(payload["change_reason"], _PLACEHOLDER_REASONS):
        raise SpecChangeBlocked(
            f"change_reason 为空或是自动填充占位符：{payload['change_reason']!r}\n"
            f"  → 「为什么改」是这道门的全部意义所在；写不出理由就不该改。")
    return payload


def validate(out_dir, spec_path):
    """门的唯一入口：读收据 → 当场重算 spec 摘要 → 四条判据。通过返回 payload。"""
    return check_payload(read_receipt(out_dir), spec_sha256(spec_path))


def _assert_declared(reason, by):
    """写入侧同样拒占位符——**两端一致**。只在读侧拦的话，一份坏收据能一路落到盘上，
    等到跑验收才炸；那时人已经跑完一轮真机了。"""
    if _is_placeholder(by, _PLACEHOLDER_CONFIRMERS):
        raise SpecChangeError(
            f"--by 为空或是自动填充占位符：{by!r} —— 确认人必须显式写清（不得自动填）。")
    if _is_placeholder(reason, _PLACEHOLDER_REASONS):
        raise SpecChangeError(
            f"--reason 为空或是自动填充占位符：{reason!r} —— 必须写清为什么是这份 spec。")


def _write(out_dir, sha, previous, reason, by):
    _assert_declared(reason, by)
    payload = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "spec_sha256": sha, "previous_spec_sha256": previous,
        "change_reason": reason.strip(), "confirmed_by": by.strip(),
    }
    try:
        os.makedirs(out_dir, exist_ok=True)
        return content_address.write_artifact(out_dir, RECEIPT_REL, DOMAIN, payload)
    except (content_address.ContentAddressError, OSError) as ex:
        # 落盘失败（报告根是软链、目录不可写……）必须收敛成本模块的错误类型，
        # 否则 CLI 直接 traceback、退出码也不再是约定的 0/1/2。
        raise SpecChangeError(f"写 {RECEIPT_REL} 失败：{out_dir!r}：{ex}") from ex


def init_receipt(out_dir, spec_path, reason, by):
    """首轮收据（`previous_spec_sha256 = null`）。

    ⚠ 收据已存在时**拒绝**：`--init` 会把 `previous_spec_sha256` 抹回 `null`，
    等于清掉「发生过变更」的痕迹。要换版本走 `--update`。
    （这拦不住「先删文件再 --init」——那一格本门证不到，模块 docstring 已如实记账。）
    """
    if os.path.lexists(receipt_path(out_dir)):
        raise SpecChangeError(
            f"{RECEIPT_REL} 已存在 → 用 --update，不要 --init。\n"
            f"  → --init 会把 previous_spec_sha256 抹回 null，"
            f"「这份 spec 是改过的」这件事就没痕迹了。")
    return _write(out_dir, spec_sha256(spec_path), None, reason, by)


def update_receipt(out_dir, spec_path, reason, by):
    """spec 改过之后换版本：旧 `spec_sha256` → 新收据的 `previous_spec_sha256`。"""
    old = read_receipt(out_dir)          # 坏收据不能靠 --update 洗白：读不通就停在这
    old_sha = old.get("spec_sha256") if isinstance(old, dict) else None
    if not _hex64(old_sha):
        raise SpecChangeError(
            f"旧收据的 spec_sha256 不是合法 sha256（{old_sha!r}），拒绝在其上叠加新版本。")
    current = spec_sha256(spec_path)
    if current == old_sha:
        raise SpecChangeError(
            "spec 与现有收据一致（没有发生变更），无需 --update。\n"
            "  → 想只做一次确认自检，用 --check。")
    return _write(out_dir, current, old_sha, reason, by)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="spec 变更门：验收通路上，跑的这份 spec 必须有一份「谁、为什么」的收据。"
                    "⚠ 本门证的是「内容完整 + 有人显式声明过」，**不是**「用户已确认」"
                    "（收据无密钥，身份不可证；详见模块 docstring）。")
    ap.add_argument("--out", required=True, metavar="DIR",
                    help="报告根（= run_workflow 的 --out）；收据落 <DIR>/" + RECEIPT_REL)
    ap.add_argument("--spec", required=True, metavar="PATH",
                    help="spec 原件路径（**不是** --out 里 staging 出来的副本）")
    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--init", action="store_true",
                        help="首轮落收据（previous_spec_sha256 = null）")
    action.add_argument("--update", action="store_true",
                        help="spec 改过之后换版本（旧 spec_sha256 → previous_spec_sha256）")
    action.add_argument("--check", action="store_true", help="只查，不写")
    ap.add_argument("--reason", default=None, metavar="TEXT", help="为什么是/为什么改这份 spec")
    ap.add_argument("--by", default=None, metavar="NAME", help="确认人（不得自动填）")
    args = ap.parse_args(argv)
    try:
        if args.check:
            payload = validate(args.out, args.spec)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            print("STATUS: CONFIRMED")
            return 0
        if args.reason is None or args.by is None:
            ap.error("--init / --update 必须同时给 --reason 与 --by")
        path = (init_receipt if args.init else update_receipt)(
            args.out, args.spec, args.reason, args.by)
        print(f"已写 {path}")
        print("STATUS: CONFIRMED")
        return 0
    except SpecChangeBlocked as ex:      # ⚠ 必须排在基类前面，否则 BLOCKED 会被吞成退出码 2
        print(f"  ✗ {ex}")
        print(f"STATUS: {BLOCKED_LABEL}")
        return 1
    except SpecChangeError as ex:
        print(f"  ✗ {ex}")
        print("STATUS: REFUSED")
        return 2


if __name__ == "__main__":
    sys.exit(main())
