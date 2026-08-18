"""内置那一轮的取证与搬运。

输入：内置那一轮的 output 目录、跑测日志、用例 JSON、库指纹。
输出：搬好的真值目录 + 取证 JSON。
退出码：0 完成；2 取证不通过（加载的不是内置那一份，或者这一轮没跑完）；
3 无法判定。

跑测本身由 run_atk_task.py 拉起，本脚本只消费它的产物，
所以不需要 NPU 也能自检。命令见 references/builtin-baseline.md#两步跑测。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import _stage_card

VENDOR_MARK = os.sep + "vendors" + os.sep


class CaptureError(Exception):
    """取证或搬运不成立。"""


def loaded_library(log_text, op):
    """从日志里读这一轮实际加载了哪份库。

    ATK 在 pyaclnn_backend.py:246 打这一行，info 级，默认日志级别就有。
    每条用例都会打一次，正则已经按算子名过滤，别的算子的行匹配不上，
    所以正常情况下这些行指的是同一份库。真出现两份不同的库路径，
    说明「这一轮加载了哪份」这件事本身是矛盾的，不能随手挑一条接着跑。
    """
    pattern = re.compile(
        rf"import\s+{re.escape(op)}GetWorkspaceSize\s+from\s+(.+?)\s+success!")
    found = []
    for path in pattern.findall(log_text):
        if path not in found:
            found.append(path)
    if not found:
        return None
    if len(found) > 1:
        listed = "\n".join(f"    {path}" for path in found)
        raise CaptureError(
            f"算子 {op} 在这份日志里加载了不止一份库：\n{listed}\n"
            "  → 两轮的日志八成重定向进了同一个文件，或者跑测中途换过 "
            "ATK_CUSTOM_OPP_PATH 重新绑定过。\n"
            "  两轮各自写一份日志，把这一轮单独重跑一次再取证。")
    return found[0]


def stage_golden(run_output, staged_dir, node_dir="pyaclnn_0", as_name="cpu_0"):
    """把内置那一轮的输出搬成 load 节点认得的目录。

    要搬的是 aclnn 那一侧：CPU 侧是 torch 基线的输出，出参精度被上调过，
    拿它复跑 fp16/bf16 会在 GetWorkspaceSize 阶段被算子拒绝。

    返回搬过去的用例号列表。
    """
    source = os.path.join(run_output, node_dir)
    if not os.path.isdir(source):
        raise CaptureError(
            f"{run_output} 下没有 {node_dir} 目录，现有 "
            f"{sorted(os.listdir(run_output)) if os.path.isdir(run_output) else '（目录不存在）'}。\n"
            "  → 这一轮不是「aclnn 主节点 + CPU 节点」的拓扑，或者没带 "
            "--save_data output。见 references/builtin-baseline.md#两步跑测。")
    target = os.path.join(staged_dir, as_name)
    if os.path.islink(target) or os.path.isfile(target):
        kind = "软链接" if os.path.islink(target) else "普通文件"
        raise CaptureError(
            f"{target} 已经占着了，而且它是一个{kind}，不是本脚本上一轮搬出来的目录。\n"
            "  → 本脚本只覆盖自己搬出来的目录，不会替你删别的东西。"
            "确认这个路径可以丢了就手工删掉它，然后重跑；"
            "如果它有用，换一个 --staged 目录。")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return collect_case_ids(target)


def collect_case_ids(node_root):
    """真值目录里有哪些用例号。"""
    ids = []
    for save_name in sorted(os.listdir(node_root)):
        save_dir = os.path.join(node_root, save_name)
        if not os.path.isdir(save_dir):
            continue
        for case_id in sorted(os.listdir(save_dir), key=lambda x: (len(x), x)):
            if os.path.isdir(os.path.join(save_dir, case_id)):
                ids.append(case_id)
    return ids


def declared_case_count(case_json):
    """用例 JSON 里声明了几条。

    形态两种：make_yaml.py 落的是 {"cases": [...]}，手工裁剪过的可能是裸 list。
    认不出来的形态返回 None，由调用方判成「判不了」。
    """
    with open(case_json, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        return len(payload["cases"])
    return None


def missing_cases(declared, staged):
    """声明了 declared 条，实际搬到 staged 这些，差的是哪几条用例号。

    用例号就是 ATK 落盘的目录名，从 0 数起。目录名不是纯数字时按号对不上，
    返回空表，只报条数对不上——列一串猜出来的号会把人带偏。
    """
    if any(not case_id.isdigit() for case_id in staged):
        return []
    have = set(staged)
    return [str(index) for index in range(declared) if str(index) not in have]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_or_refuse(path, what):
    """算文件的 sha256；读不了就当成取证不成立，不要裸崩。"""
    try:
        return sha256_file(path)
    except OSError as exc:
        raise CaptureError(
            f"{what}读不了：{path}（{exc}）。\n"
            "  → 这份文件此刻不在盘上或者没有读权限，取证核对不了。"
            "确认它还在原地再重跑。")


def build_provenance(op, library, fingerprint, case_json, input_data,
                     staged_dir, cases):
    """把这一轮的全部依据记成一份可核对的 JSON。

    第一轮跑的必须是 CANN 内置那一份实现。社区算子和内置基本都同名，
    要是这里放过一份 candidate 侧的指纹，两轮就是同一份实现自己跟自己比，
    报告会显示 100% 通过而什么都没验到——所以哪一侧、哪个算子、
    盘上这一刻的文件内容，三样都要当场核。
    """
    side = fingerprint.get("side")
    if side != "builtin":
        raise CaptureError(
            f"这份库指纹记的是 {side!r} 那一侧的库，第一轮真值要的是 builtin 那一侧"
            "（CANN 内置的那份实现）。\n"
            "  → 用 resolve_opp_library.py --side builtin 重新解析一份指纹，"
            "跑测前 export 的 ATK_CUSTOM_OPP_PATH 也要跟着换成它，再重跑这一轮。\n"
            "  两轮都用同一份库的话，第二轮就是自己跟自己比，什么都验不到。")

    declared_op = fingerprint.get("op")
    if declared_op is not None and declared_op != op:
        raise CaptureError(
            f"这份库指纹是按算子 {declared_op} 解析出来的，这一轮测的是 {op}。\n"
            f"  → 不同算子不一定落在同一个 .so 里。用 "
            f"resolve_opp_library.py --op {op} --side builtin 重新解析一份指纹。")

    declared_path = fingerprint.get("path")
    if not declared_path:
        raise CaptureError(
            "这份库指纹里没有 path 字段，核对不了这一轮加载的是不是它。\n"
            "  → 指纹要由 resolve_opp_library.py 生成，别手写。")
    if os.path.realpath(library) != os.path.realpath(declared_path):
        raise CaptureError(
            f"日志里加载的是 {library}，钉死的却是 {declared_path}。\n"
            "  → 跑测前没有 export ATK_CUSTOM_OPP_PATH，或者导出的不是这一份。"
            "这一轮的真值来历不明，重跑。")

    actual = digest_or_refuse(declared_path, "指纹钉死的那份算子库")
    if actual != fingerprint.get("sha256"):
        raise CaptureError(
            f"{declared_path} 现在的 sha256 是 {actual}，指纹里记的却是 "
            f"{fingerprint.get('sha256')}。\n"
            "  → 路径没变，文件内容变了：解析指纹之后这份库被重装或覆盖过。"
            "先 resolve_opp_library.py --side builtin 重新解析一份指纹，再重跑这一轮。")

    return {
        "op": op,
        "builtin_library": dict(fingerprint),
        "case_json": os.path.abspath(case_json),
        "case_json_sha256": digest_or_refuse(case_json, "用例 JSON"),
        "input_data": input_data,
        "staged_dir": os.path.abspath(staged_dir),
        "cases": cases,
        "counter_experiment": None,
    }


def tamper(staged_dir, case_id):
    """把某条用例的真值改掉一个元素，用来验证比对拓扑真有判别力。

    前面全绿说明不了任何事：load 节点没生效、或者待验收算子在跟自己比，
    结果同样是 100% 通过。改坏一条再重跑，那条必须变 Fail。
    """
    node_root = os.path.join(staged_dir, "cpu_0")
    if not os.path.isdir(node_root):
        raise CaptureError(
            f"{node_root} 不存在。\n"
            "  → 这一轮还没跑 capture_reference.py 把内置真值搬过来，"
            "先跑一次取证再做反证实验。")
    target = None
    for save_name in sorted(os.listdir(node_root)):
        probe = os.path.join(node_root, save_name, str(case_id), "output_0.pt")
        if os.path.exists(probe):
            target = probe
            break
    if target is None:
        raise CaptureError(
            f"真值目录里没有用例 {case_id} 的 output_0.pt。\n"
            f"  → 现有用例号 {collect_case_ids(node_root)[:12]}")

    backup = target + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(target, backup)

    import torch

    # 读写都借 ATK 自己的两个函数，不要用裸 torch.load / torch.save：
    # 裸 load 在 torch>=2.6 默认 weights_only=True，读 ATK 存的 .pt 直接
    # UnpicklingError；裸 save 会丢掉 sanitize_data 与 pickle_protocol=4，
    # uint 类张量存回去 ATK 读不出来。真机上实测过这两条。
    # 依据 atk/common/utils.py:466-476。
    try:
        from atk.common.utils import torch_load_safe, torch_save_safe
    except ImportError as exc:
        raise CaptureError(
            f"改真值要借 ATK 自己的读写函数，但 import atk 失败：{exc}\n"
            "  → 换用装了 ATK 的解释器（probe_env.py 探出的那个）重跑。\n"
            "     不要绕过去用裸 torch.load：存回去的文件 ATK 读不出来，"
            "反证实验会变成一场误报。")

    data = torch_load_safe(target)
    flat = data.reshape(-1).clone()
    if flat.numel() == 0:
        raise CaptureError(f"用例 {case_id} 的输出是空张量，改不动，换一条。")
    if flat.dtype == torch.bool:
        flat[0] = ~flat[0]
    elif flat.is_floating_point():
        flat[0] = flat[0] + 1.0
    else:
        flat[0] = flat[0] + 1
    torch_save_safe(flat.reshape(data.shape), target)

    # 回读确认真的改动了。bf16 只有 8 位尾数，值 ≥256 时 x+1.0 舍回 x；
    # fp16 在 ≥2048 时同理；inf/nan 加一还是自己。改了等于没改的话，
    # 第二轮那条不会 Fail，反证实验会判成「拓扑没生效」——把唯一有判别力
    # 的实验变成一次假红，而假红最省事的出口恰好是随手声明 --detected。
    if torch.equal(torch_load_safe(target), data):
        shutil.move(backup, target)
        raise CaptureError(
            f"用例 {case_id} 的输出改不动：写回去和原值逐位相同。\n"
            f"    dtype {data.dtype}，改的那个元素原值 {data.reshape(-1)[0]}。\n"
            "  → 低精度浮点在大值上加一会被舍掉。真值已还原，换一条用例重试；\n"
            "     挑输出里有小值或整型的那条。")
    return {"case_id": str(case_id), "file": target, "backup": backup}


def note_tampered_case(provenance_path, case_id):
    """把「这一轮改坏的是哪条」记进取证，供 --conclude-tamper 对账。"""
    if not os.path.exists(provenance_path):
        return
    with open(provenance_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["tampered_case"] = {"case_id": str(case_id)}
    with open(provenance_path, "w", encoding="utf-8") as sink:
        json.dump(payload, sink, ensure_ascii=False, indent=2)


def restore(staged_dir):
    """把改坏的真值全部还原。"""
    restored = []
    for root, _dirs, files in os.walk(staged_dir):
        for name in files:
            if not name.endswith(".orig"):
                continue
            backup = os.path.join(root, name)
            shutil.move(backup, backup[:-len(".orig")])
            restored.append(backup[:-len(".orig")])
    return restored


def detected_from_report(report_path, case_id):
    """从重跑后的报告里读那条用例判没判失败。

    红线 4：判据从数据推导。裸 `--detected` 是 agent 举手声明——想要绿灯
    的人只需 `--tamper 0` 之后立刻 `--conclude-tamper 0 --detected`，
    一轮都不用重跑，而反证实验恰恰是这条路唯一有判别力的证据。
    """
    try:
        with open(report_path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CaptureError(
            f"{report_path} 读不成 JSON：{exc}\n"
            "  → --report 要给 parse_atk_report.py 产出的 "
            "conclusion/accuracy_results.json。")

    rows = doc.get("cases") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise CaptureError(
            f"{report_path} 里没有 cases 列表，这不像是精度结果。\n"
            "  → 用 parse_atk_report.py 解析那一轮的报告，再把产物给 --report。")

    for row in rows:
        if str(row.get("id")) == str(case_id):
            return not row.get("passed", True)
    raise CaptureError(
        f"{report_path} 里没有用例 {case_id} 的判定结果。\n"
        f"    这份结果里的用例号：{[str(r.get('id')) for r in rows][:12]}\n"
        "  → 确认这份是**改坏真值之后**那一轮跑出来的，不是之前那轮。")


def record_counter_experiment(provenance_path, case_id, detected):
    """把反证实验的结论写回取证 JSON。"""
    with open(provenance_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    tampered = (payload.get("tampered_case") or {}).get("case_id")
    if tampered is not None and str(tampered) != str(case_id):
        raise CaptureError(
            f"改坏的是用例 {tampered}，结论却记在用例 {case_id} 上。\n"
            "  → 两者必须是同一条，否则这次反证实验证明不了任何事。")
    payload["counter_experiment"] = {"case_id": str(case_id),
                                     "detected": bool(detected)}
    payload.pop("tampered_case", None)
    with open(provenance_path, "w", encoding="utf-8") as sink:
        json.dump(payload, sink, ensure_ascii=False, indent=2)
    return payload


def main():
    parser = argparse.ArgumentParser(description="内置那一轮的取证与搬运")
    parser.add_argument("--op", required=True, help="aclnn 接口名")
    parser.add_argument("--from-run",
                        help="内置那一轮的 atk_output/<任务>/output 目录")
    parser.add_argument("--log", help="内置那一轮的跑测日志")
    parser.add_argument("--case-json")
    parser.add_argument("--input-data", help="冻结输入目录")
    parser.add_argument("--library-fingerprint",
                        help="resolve_opp_library.py --side builtin 的产物")
    parser.add_argument("--staged", default="evidence/golden_builtin")
    parser.add_argument("-o", "--output", default="evidence/golden_provenance.json")
    parser.add_argument("--tamper", help="反证实验：改坏这条用例的真值")
    parser.add_argument("--conclude-tamper",
                        help="反证实验：记录这条用例重跑后的结论并还原真值")
    parser.add_argument("--report",
                        help="--conclude-tamper 时：改坏真值后那一轮的精度结果"
                             "（parse_atk_report.py 的产物）。结论从它读出来，"
                             "不接受手工声明")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    if args.tamper:
        try:
            info = tamper(args.staged, args.tamper)
        except CaptureError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ImportError as exc:
            print(f"改真值需要 torch，这台机器上导入不了：{exc}\n"
                  "  → 装上 torch 再跑这一步；反证实验要改的就是 torch 存的真值文件。",
                  file=sys.stderr)
            return 3
        except OSError as exc:
            print(f"改真值时出错：{exc}\n"
                  "  → 这是文件系统层面的问题（权限不足、磁盘满、只读挂载等），"
                  f"不是取证逻辑的事。确认 {args.staged} 读写正常（磁盘还有空间、"
                  "不是只读挂载、当前用户有写权限），再重跑这一步。",
                  file=sys.stderr)
            return 3
        try:
            note_tampered_case(args.output, info["case_id"])
        except (OSError, ValueError) as exc:
            print(f"改坏了真值，但记不进 {args.output}：{exc}\n"
                  f"  → 先手工从 {info['backup']} 还原，解决写盘问题再重来。",
                  file=sys.stderr)
            return 3
        print(f"已改坏用例 {info['case_id']} 的真值：{info['file']}")
        print(f"  原件备份在 {info['backup']}")
        print("  现在重跑第三步，**日志和报告都换个名字**，别覆盖正式那一轮的：")
        print("    run_atk_task.py -o evidence/tamper_run.log -- ...")
        print("  跑完解析报告，再执行：")
        print(f"    capture_reference.py --conclude-tamper {info['case_id']} \\")
        print("      --report conclusion/tamper_results.json")
        print("  结论从报告里读，不是自己填。")
        return 0

    if args.conclude_tamper:
        if not os.path.exists(args.output):
            print(f"{args.output} 不存在，先跑一次取证。", file=sys.stderr)
            return 3
        try:
            with open(args.output, encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"{args.output} 读不成 JSON：{exc}\n"
                  "  → 这份取证 JSON 要由 capture_reference.py 生成，别手改。",
                  file=sys.stderr)
            return 3
        if not args.report:
            print("--conclude-tamper 要给 --report：反证实验的结论从重跑后的\n"
                  "  精度结果里读，不接受手工声明（红线 4）。\n"
                  "  → 用 parse_atk_report.py 解析那一轮的报告，把产物给 --report。",
                  file=sys.stderr)
            return 3
        try:
            detected = detected_from_report(args.report, args.conclude_tamper)
        except CaptureError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        try:
            record_counter_experiment(args.output, args.conclude_tamper, detected)
            restored = restore(args.staged)
        except CaptureError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"还原真值时出错：{exc}\n"
                  f"  → 确认 {args.output} 与 {args.staged} 都写得进去，再重跑。",
                  file=sys.stderr)
            return 3
        print(f"已还原 {len(restored)} 份真值")
        if not detected:
            print("反证实验没通过：改坏了真值，那条用例却没变 Fail。\n"
                  "  → 说明 load 节点没生效，或者两轮跑的是同一份实现。\n"
                  "     核对第三步的 --output_path 与目录名，见 "
                  "references/builtin-baseline.md#反证实验。", file=sys.stderr)
            return 2
        print(f"反证实验通过，已记进 {args.output}")
        return 0

    missing_flags = [flag for flag, value in (
        ("--from-run", args.from_run), ("--log", args.log),
        ("--case-json", args.case_json), ("--input-data", args.input_data),
        ("--library-fingerprint", args.library_fingerprint)) if not value]
    if missing_flags:
        print(f"取证模式需要 {' '.join(missing_flags)}。", file=sys.stderr)
        return 3

    for path in (args.from_run, args.log, args.case_json, args.library_fingerprint):
        if not os.path.exists(path):
            print(f"{path} 不存在。", file=sys.stderr)
            return 3

    try:
        with open(args.library_fingerprint, encoding="utf-8") as handle:
            fingerprint = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"{args.library_fingerprint} 读不成 JSON：{exc}\n"
              "  → 这份库指纹要由 resolve_opp_library.py 生成，别手写。",
              file=sys.stderr)
        return 3
    if not isinstance(fingerprint, dict):
        print(f"{args.library_fingerprint} 里不是一个 JSON 对象。\n"
              "  → 这份库指纹要由 resolve_opp_library.py 生成，别手写。",
              file=sys.stderr)
        return 3

    try:
        declared = declared_case_count(args.case_json)
    except (OSError, ValueError) as exc:
        print(f"{args.case_json} 读不成 JSON：{exc}\n"
              "  → 给 --case-json 的要是这一轮真跑的那份用例 JSON。", file=sys.stderr)
        return 3
    if declared is None:
        print(f"{args.case_json} 里读不出用例条数，认得的形态是 "
              '{"cases": [...]} 或者裸的用例列表。\n'
              "  → 给 --case-json 的要是这一轮真跑的那份用例 JSON。", file=sys.stderr)
        return 3

    try:
        with open(args.log, encoding="utf-8", errors="replace") as handle:
            library = loaded_library(handle.read(), args.op)
    except CaptureError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{args.log} 读不了：{exc}\n  → 给 --log 的要是一份可读的跑测日志。",
              file=sys.stderr)
        return 3
    if not library:
        print(f"{args.log} 里没有 {args.op}GetWorkspaceSize 的加载记录。\n"
              "  → 这一轮没真正绑定过算子库，可能建任务就失败了。先看日志本身。",
              file=sys.stderr)
        return 3

    if VENDOR_MARK in os.path.realpath(library) + os.sep:
        print(f"内置那一轮加载的是 {library}，路径里有 vendors。\n"
              "  → 这是某个 vendor 的库，不是内置。跑测前把 ATK_CUSTOM_OPP_PATH "
              "export 成 resolve_opp_library.py --side builtin 解析出来的那份，重跑。",
              file=sys.stderr)
        return 2

    try:
        cases = stage_golden(args.from_run, args.staged)
        if len(cases) != declared:
            missing = missing_cases(declared, cases)
            listed = "、".join(missing[:12]) + ("…" if len(missing) > 12 else "")
            raise CaptureError(
                f"{args.case_json} 里有 {declared} 条用例，搬到的真值只有 "
                f"{len(cases)} 条" + (f"，缺的用例号 {listed}" if missing else "") + "。\n"
                "  → 这一轮没跑完（中途崩了或者被打断），残缺的真值不能当第二轮的标杆。"
                "先看跑测日志断在哪一条，修掉再把这一轮整个重跑。")
        payload = build_provenance(
            args.op, library, fingerprint, args.case_json, args.input_data,
            args.staged, cases)
    except CaptureError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"搬运真值目录时出错：{exc}\n"
              f"  → 确认 {args.from_run} 读得了、{args.staged} 写得进去，再重跑。",
              file=sys.stderr)
        return 3

    try:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as sink:
            json.dump(payload, sink, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"取证 JSON 写不进 {args.output}：{exc}\n"
              "  → 换一个写得进去的 -o 路径再重跑。", file=sys.stderr)
        return 3
    print(f"内置真值 {len(cases)} 条 → {payload['staged_dir']}")
    print(f"  库 {library}")
    print(f"  取证写入 {args.output}")
    print("  下一步：做反证实验（capture_reference.py --tamper），没做过不许裁决。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
