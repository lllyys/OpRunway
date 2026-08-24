"""裁决前核对真值确实来自 CANN 内置实现。

输入：内置真值取证、两侧的库指纹、本轮用例 JSON。
输出：核对结论打印到标准输出。
退出码：0 通过；2 不通过；3 无法判定。

挡的是这条路唯一的致命失败：两轮跑了同一份实现，
报告 100% 通过而什么都没验。见 references/builtin-baseline.md。
"""

import argparse
import hashlib
import json
import os
import sys
import _stage_card

VENDOR_MARK = os.sep + "vendors" + os.sep

# 库指纹里 side 字段的两种取值，以及说人话的叫法。
SIDE_NAMES = {"builtin": "CANN 内置", "candidate": "本轮 vendor 的待验收算子"}


class GateError(Exception):
    """产物读不进来，判不了。"""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def side_name(fingerprint):
    """这份指纹自称是哪一侧的库。"""
    side = fingerprint.get("side")
    return SIDE_NAMES.get(side, f"{side!r}")


def incomplete(fingerprint, expected_side):
    """指纹缺字段、或者自称的那一侧不对。

    下面的比对全靠 path 与 sha256 两个字段。缺一个就没得比，
    这时候必须报出来，不能悄悄跳过——跳过就等于删掉 JSON 里一行
    就能让「两轮是同一份库」这条判据放行。
    """
    found = []
    missing = [key for key in ("path", "sha256") if not fingerprint.get(key)]
    if missing:
        found.append(
            f"{SIDE_NAMES[expected_side]}那一侧的库指纹缺 {'、'.join(missing)} 字段，"
            "没有这两项就核对不了两轮跑的是不是同一份库。\n"
            "  → 库指纹由 resolve_opp_library.py 生成，别手写、别手改。")
    if fingerprint.get("side") != expected_side:
        found.append(
            f"给 --{expected_side} 的这份库指纹记的是"
            f"「{side_name(fingerprint)}」那一侧的库。\n"
            f"  → 两个参数写反了，或者解析时 --side 给错了。用 "
            f"resolve_opp_library.py --side {expected_side} 重新解析一份，"
            "跑测前 export 的 ATK_CUSTOM_OPP_PATH 也要跟着对上。")
    return found


def candidate_round_problems(log_path, op, candidate):
    """第二轮跑测实际加载的，必须是钉死的那份待验收算子库。

    这是整条链上唯一核**第二轮**加载了谁的地方。前面几道闸都够不到它：
    取证只看第一轮的日志；反证实验验的是 load 节点通不通，改坏真值之后
    无论第二轮加载谁，那条用例都会 Fail；两份指纹各自都可以是诚实的。

    所以第二轮忘了 source evidence/env.sh 时，ATK 会自己按算子名搜到内置
    那一份，于是两轮跑的是同一份实现——报告 100% 通过，而什么都没验。

    读不到加载记录时抛 GateError（判不了），其余情况返回问题清单。
    """
    from capture_reference import CaptureError, loaded_library

    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise GateError(
            f"{log_path} 读不了：{exc}\n"
            "  → --candidate-log 要给第二轮（跑待验收算子那一轮）的跑测日志。")

    try:
        library = loaded_library(text, op)
    except CaptureError as exc:
        return [str(exc)]

    if not library:
        raise GateError(
            f"{log_path} 里没有 {op}GetWorkspaceSize 的加载记录。\n"
            "  → 这一轮没真正绑定过算子库，可能建任务就失败了。\n"
            "     先看这份日志本身，确认第二轮真的跑起来了，再回来核。")

    if os.path.realpath(library) != os.path.realpath(candidate.get("path") or ""):
        return [
            f"第二轮实际加载的是 {library}，\n"
            f"    而钉死的待验收算子库是 {candidate.get('path')}。\n"
            "  → 最常见的原因是第二轮忘了 source evidence/env.sh，或者没有 "
            "export ATK_CUSTOM_OPP_PATH。\n"
            "     那样 ATK 会按算子名自己去搜，社区算子与内置同名，搜到的很可能"
            "就是内置那一份——\n"
            "     两轮跑同一份实现，报告照样 100% 通过。重跑第二轮。"]
    return []


def problems(provenance, builtin, candidate, case_ids, case_json_sha):
    """全部判据都从产物推导，一条都不接受口头断言。"""
    found = []
    found.extend(incomplete(builtin, "builtin"))
    found.extend(incomplete(candidate, "candidate"))

    recorded = provenance.get("builtin_library")
    if not isinstance(recorded, dict):
        recorded = {}
    if (os.path.realpath(recorded.get("path") or "")
            != os.path.realpath(builtin.get("path") or "")
            or recorded.get("sha256") != builtin.get("sha256")):
        found.append(
            f"取证记的库是 {recorded.get('path')}（sha256 {recorded.get('sha256')}），"
            f"库指纹记的是 {builtin.get('path')}（sha256 {builtin.get('sha256')}）。\n"
            "  → 两者必须是同一份。中间换过库就重跑内置那一轮。")

    op = provenance.get("op")
    for fingerprint in (builtin, candidate):
        declared_op = fingerprint.get("op")
        if op and declared_op and declared_op != op:
            found.append(
                f"{side_name(fingerprint)}那一侧的库指纹是按算子 {declared_op} 解析的，"
                f"这一轮测的是 {op}。\n"
                f"  → 不同算子不一定落在同一个 .so 里；指错库时 ATK 会自己去搜，"
                f"搜到的很可能就是内置那一份。用 resolve_opp_library.py --op {op} "
                "重新解析这一侧的库。")

    builtin_path = builtin.get("path")
    candidate_path = candidate.get("path")
    if (builtin_path and candidate_path
            and os.path.realpath(builtin_path) == os.path.realpath(candidate_path)):
        found.append(
            f"两轮加载的是同一份库 {builtin_path}。\n"
            "  → 这是自己跟自己比，报告作废。跑测前分别 export "
            "ATK_CUSTOM_OPP_PATH 到两份不同的库。")
    elif builtin.get("sha256") and builtin.get("sha256") == candidate.get("sha256"):
        found.append(
            f"两轮的库路径不同但 sha256 相同（{builtin.get('sha256')}）。\n"
            "  → 同一份文件的软链接或拷贝，仍然是自己跟自己比。")

    if builtin_path and VENDOR_MARK in os.path.realpath(builtin_path) + os.sep:
        found.append(
            f"内置那一侧的库 {builtin_path} 在 vendors 目录下。\n"
            "  → 这是某个 vendor 的库，不是内置。用 resolve_opp_library.py "
            "--side builtin 重新解析。")

    if provenance.get("case_json_sha256") != case_json_sha:
        found.append(
            "两轮的用例 JSON 不是同一份"
            f"（内置那轮 {provenance.get('case_json_sha256')}，"
            f"本轮 {case_json_sha}）。\n"
            "  → 比的不是同一批用例，重跑内置那一轮。")

    listed = provenance.get("cases")
    golden = [str(case_id) for case_id in listed] if isinstance(listed, list) else []
    if not case_ids:
        # 一条用例都没有时下面每条判据都自动满足，"真值齐备"就成了空话。
        found.append(
            "本轮用例 JSON 里一条用例都没有。\n"
            "  → 没有用例就没有可裁决的东西。给 --case-json 这一轮真跑的那份。")
    missing = [str(cid) for cid in case_ids if str(cid) not in set(golden)]
    if missing:
        found.append(
            f"有 {len(missing)} 条用例没有内置真值：{missing[:12]}。\n"
            "  → 内置那一轮没跑完，或者搬运时漏了。重跑并重新取证。")

    counter = provenance.get("counter_experiment")
    if not counter or not isinstance(counter, dict):
        found.append(
            "没做反证实验。\n"
            "  → 全绿既可能是真的一致，也可能是 load 节点没生效。\n"
            "     跑 capture_reference.py --tamper <用例号>，重跑后 "
            "--conclude-tamper 记结论。")
    else:
        if str(counter.get("case_id")) not in set(golden):
            found.append(
                f"反证实验记的是用例 {counter.get('case_id')}，这一轮的真值里没有这条"
                f"（有的是 {golden[:12]}）。\n"
                "  → 改坏的不是这一轮的真值，这次反证实验证明不了这一轮的比对成立。"
                "重做一次反证实验。")
        if not counter.get("detected"):
            found.append(
                f"反证实验没通过：改坏了用例 {counter.get('case_id')} 的真值，"
                "它却没变 Fail。\n"
                "  → 比对拓扑没生效，这一轮的结论不成立。")

    return found


def stale_fingerprints(*fingerprints):
    """指纹里记的 sha256 与此刻盘上那份文件对不上就报。

    上面几条判据全建立在 sha256 上，而 sha256 是 JSON 里的一行字，
    手改一个字符就能让「两轮是同一份库」放行一次。这里当场把文件重算一遍，
    改 JSON 就没有用了。

    文件不在盘上（比如解析和裁决不在同一台机器）时判不了，跳过。
    """
    found = []
    for fingerprint in fingerprints:
        path = fingerprint.get("path")
        if not path or not os.path.exists(path):
            continue
        try:
            actual = sha256_file(path)
        except OSError as exc:
            found.append(
                f"{path} 此刻读不了：{exc}\n"
                "  → 核对不了它是不是指纹里记的那份库。确认这份 .so 还在原地、"
                "当前用户读得了，再重跑本门禁。")
            continue
        if actual != fingerprint.get("sha256"):
            found.append(
                f"{side_name(fingerprint)}那一侧的库 {path} 此刻盘上的 sha256 是 "
                f"{actual}，指纹里记的却是 {fingerprint.get('sha256')}。\n"
                "  → 要么这份库在跑测之后被重装或覆盖过，要么这份指纹被手改过。"
                "两种都让上面的比对失去意义：用 resolve_opp_library.py 重新解析，"
                "并把对应那一轮重跑。")
    return found


def read_json_object(path, what):
    """读一份 JSON 对象；读不了就是判不了，不要裸崩。"""
    if not os.path.exists(path):
        raise GateError(
            f"{path} 不存在，无法判定。\n"
            "  → 这条路的产物由 resolve_opp_library.py 与 "
            "capture_reference.py 产出，见 references/builtin-baseline.md。")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise GateError(
            f"{path} 读不成 JSON：{exc}\n"
            f"  → {what}要由脚本生成，别手写。")
    if not isinstance(payload, dict):
        raise GateError(
            f"{path} 里不是一个 JSON 对象。\n"
            f"  → {what}要由脚本生成，别手写。")
    return payload


def case_ids_of(path):
    """本轮用例 JSON 里的用例号。

    形态两种：make_yaml.py 落的是 {"cases": [...]}，手工裁剪过的可能是裸 list。
    用例没写 id 时按位置从 0 数起，与 ATK 落盘的目录名一致。
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise GateError(
            f"{path} 读不成 JSON：{exc}\n"
            "  → --case-json 要给这一轮真跑的那份用例 JSON。")
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise GateError(
            f"{path} 里读不出用例列表，认得的形态是 "
            '{"cases": [...]} 或者裸的用例列表。\n'
            "  → --case-json 要给这一轮真跑的那份用例 JSON。")
    ids = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise GateError(
                f"{path} 的第 {index} 条用例不是一个 JSON 对象。\n"
                "  → --case-json 要给这一轮真跑的那份用例 JSON。")
        case_id = case.get("id")
        ids.append(str(index if case_id is None else case_id))
    return ids


def gather(args):
    """把四份产物读进来，读不了就抛 GateError。"""
    provenance = read_json_object(args.provenance, "内置真值取证")
    builtin = read_json_object(args.builtin, "库指纹")
    candidate = read_json_object(args.candidate, "库指纹")
    case_ids = case_ids_of(args.case_json)
    try:
        case_sha = sha256_file(args.case_json)
    except OSError as exc:
        raise GateError(
            f"{args.case_json} 读不了：{exc}\n"
            "  → 确认这份用例 JSON 还在原地、当前用户读得了，再重跑本门禁。")
    op = provenance.get("op") or candidate.get("op")
    if not op:
        raise GateError(
            "取证与待验收算子库指纹里都没有算子名（op），核不了第二轮加载了谁。\n"
            "  → 两份产物都由本条路的脚本产出，缺 op 说明它们不是这一轮的。重跑。")
    candidate_found = candidate_round_problems(args.candidate_log, op, candidate)
    return provenance, builtin, candidate, case_ids, case_sha, candidate_found


def main():
    parser = argparse.ArgumentParser(description="核对真值确实来自 CANN 内置实现")
    parser.add_argument("--provenance", default="evidence/golden_provenance.json")
    parser.add_argument("--builtin", default="evidence/opp_library_builtin.json")
    parser.add_argument("--candidate", default="evidence/opp_library_candidate.json")
    parser.add_argument("--case-json", required=True)
    parser.add_argument("-o", "--output", default="evidence/golden_source.json",
                        help="判定结论落盘路径。verdict.py 在这条路上强制读它——"
                             "光有门禁没用，得有东西证明它真的跑过")
    parser.add_argument("--candidate-log", required=True,
                        help="第二轮（跑待验收算子那一轮）的跑测日志；"
                             "核这一轮实际加载的是不是钉死的那份库")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    try:
        (provenance, builtin, candidate, case_ids, case_sha,
         candidate_found) = gather(args)
    except GateError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    found = problems(provenance, builtin, candidate, case_ids, case_sha)
    found.extend(stale_fingerprints(builtin, candidate))
    found.extend(candidate_found)
    if found:
        print("", file=sys.stderr)
        for item in found:
            print(f"✗ {item}", file=sys.stderr)
        print("\n真值来历不成立，不要裁决。", file=sys.stderr)
        return 2

    # 落一份判定结论：光有这个脚本没用，得有东西证明它真的跑过。
    # verdict.py 在 baseline_kind 为 cann_builtin 时强制读它，
    # 否则漏跑这一步照样能拿到裁决——那正是这条路要挡的失败。
    conclusion = {
        "op": provenance.get("op"),
        "verdict": "ok",
        "builtin_library": builtin,
        "candidate_library": candidate,
        "case_json_sha256": provenance.get("case_json_sha256"),
        "input_data": provenance.get("input_data"),
        "counter_experiment": provenance.get("counter_experiment"),
        "cases": len(case_ids),
    }
    try:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as sink:
            json.dump(conclusion, sink, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"判定通过了，但结论写不进 {args.output}：{exc}\n"
              "  → verdict.py 要读这份文件才肯裁决，先解决写盘再重跑。",
              file=sys.stderr)
        return 3

    print(f"真值来自内置库 {builtin['path']}")
    print(f"  待验收算子库 {candidate['path']}")
    print(f"  {len(case_ids)} 条用例的真值齐备，反证实验通过。")
    print(f"  判定结论写入 {args.output}（verdict.py 会读它）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
