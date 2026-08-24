"""检查 c_api 入口、参数可表达性与真机架构目录中的实现。"""

import argparse
import json
import re
from pathlib import Path

from _c_api_signature import (CApiSignatureError, _without_comments,
                              parse_declaration, probe_classify,
                              scan_enum_typedefs)
from _soc_binding import SocBindingError, infer_build_soc
from align_signatures import AlignError, require_project_source
import _stage_card


VERIFIED_SOC_ARCH = {
    ("ascend910b", "arch22"),
    ("ascend910_93", "arch22"),
}


def _write_report(path, report):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _implementation_file(arch_root, candidate):
    pattern = re.compile(rf"\b{re.escape(candidate)}\s*\(")
    for path in sorted(arch_root.rglob("*.cpp")):
        resolved = path.resolve()
        if not resolved.is_relative_to(arch_root.resolve()):
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(_without_comments(text)):
            return str(resolved)
    return None


def main():
    parser = argparse.ArgumentParser(description="c_api 接口适用性检查")
    parser.add_argument("--env", required=True, help="环境指纹 JSON")
    parser.add_argument("--candidate", required=True, help="待检查的公开接口名")
    parser.add_argument("--header", action="append", required=True,
                        help="公开头文件，可重复")
    parser.add_argument("--op-dir", required=True, help="算子在工程内的目录")
    parser.add_argument("--arch-dir", required=True, help="本机对应的 archXX 目录名")
    parser.add_argument("--arch-dir-basis", required=True,
                        help="工程把本机映射到该目录的书面依据")
    parser.add_argument("--allow-pending", action="store_true",
                        help="确认继续处理尚未核实的 SoC 与 archXX 组合")
    parser.add_argument("-o", "--output", required=True, help="检查报告 JSON")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    failures = []
    pending = []
    report = {
        "schema_version": 1,
        "candidate": args.candidate,
        "headers": [],
        "op_dir": None,
        "arch_dir": args.arch_dir,
        "arch_dir_basis": args.arch_dir_basis,
        "build_soc": None,
        "declaration": None,
        "parameter_probe": [],
        "matched_file": None,
        "checks": {
            "declaration": {"passed": False},
            "representability": {"passed": False},
            "arch_implementation": {"passed": False},
        },
        "pending": pending,
        "pending_allowed": bool(args.allow_pending),
        "failures": failures,
    }

    arch_blocked = False
    header_texts = []
    try:
        if not re.fullmatch(r"[A-Za-z_]\w*", args.candidate):
            raise AlignError("--candidate 必须是合法的 C 接口名")
        if not re.fullmatch(r"arch\d+", args.arch_dir):
            raise AlignError("--arch-dir 必须是 archXX 目录名，不能含路径片段")
        if not args.arch_dir_basis.strip():
            raise AlignError("--arch-dir-basis 必须给出非空书面依据")
        op_dir = Path(require_project_source(args.op_dir, args.env, "--op-dir"))
        if not op_dir.is_dir():
            raise AlignError(f"--op-dir 指向的 {op_dir} 不是目录")
        report["op_dir"] = str(op_dir)
        for header_arg in args.header:
            resolved = Path(require_project_source(header_arg, args.env, "--header"))
            if not resolved.is_file():
                raise AlignError(f"--header 指向的 {resolved} 不是文件")
            report["headers"].append(str(resolved))
            header_texts.append((resolved, resolved.read_text(
                encoding="utf-8", errors="replace")))

        declaration = None
        declaration_path = None
        parse_errors = []
        for header_path, text in header_texts:
            try:
                declaration = parse_declaration(text, args.candidate)
                declaration_path = header_path
                break
            except CApiSignatureError as exc:
                parse_errors.append(str(exc))
        if declaration is None:
            failures.append(
                f"给定头文件中找不到可解析的 {args.candidate} 声明："
                + "; ".join(parse_errors))
        else:
            report["checks"]["declaration"] = {
                "passed": True, "header": str(declaration_path),
            }
            report["declaration"] = {
                **declaration,
                "mangled": not declaration["extern_c"],
                "header": str(declaration_path),
            }
            enums = scan_enum_typedefs([text for _, text in header_texts])
            probes = probe_classify(declaration["parameters"], enum_types=enums)
            report["parameter_probe"] = probes
            unsupported = [item for item in probes if not item["representable"]]
            if unsupported:
                failures.extend(item["reason"] for item in unsupported)
            else:
                report["checks"]["representability"] = {"passed": True}

        try:
            env = json.loads(Path(args.env).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AlignError(f"读不出 {args.env}：{exc}") from exc
        device_name = (env.get("devices") or {}).get("selected_name")
        if not device_name:
            raise AlignError("环境指纹缺少 devices.selected_name")
        try:
            build_soc = infer_build_soc(device_name)
            report["build_soc"] = build_soc
            if (build_soc, args.arch_dir) not in VERIFIED_SOC_ARCH:
                pending.append(
                    f"无法判定，需用户确认：build_soc={build_soc} 与 "
                    f"arch_dir={args.arch_dir} 的对应关系；依据："
                    f"{args.arch_dir_basis}")
        except SocBindingError:
            pending.append(
                f"无法判定，需用户确认：设备型号 {device_name!r} 不能映射为"
                f"已知 build_soc；依据：{args.arch_dir_basis}")

        # 同名接口可能只在另一个架构目录实现，所以必须检查本机目录本身。
        arch_root = (op_dir / args.arch_dir).resolve()
        if not arch_root.is_relative_to(op_dir.resolve()):
            raise AlignError("--arch-dir 解析后越过了 --op-dir")
        if not arch_root.is_dir():
            failures.append(f"本机架构目录不存在：{arch_root}")
            arch_blocked = True
        else:
            matched = _implementation_file(arch_root, args.candidate)
            if matched is None:
                failures.append(
                    f"本机架构目录 {arch_root} 的 .cpp 中没有 {args.candidate} 接口")
                arch_blocked = True
            else:
                report["matched_file"] = matched
                report["checks"]["arch_implementation"] = {
                    "passed": True, "matched_file": matched,
                }
    except (AlignError, OSError) as exc:
        failures.append(str(exc))

    _write_report(args.output, report)
    for failure in failures:
        print(f"✗ {failure}")
    for item in pending:
        print(f"? {item}")
    if arch_blocked and not any(
            not message.startswith(("本机架构目录不存在：", "本机架构目录 "))
            for message in failures):
        return 3
    if failures or (pending and not args.allow_pending):
        return 2
    print(f"c_api 接口适用性检查通过 → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
