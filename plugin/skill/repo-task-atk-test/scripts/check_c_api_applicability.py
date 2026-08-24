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

VERIFIED_SOC_NPU_ARCH = {
    ("ascend910b", "dav-2201"),
    ("ascend910_93", "dav-2201"),
}

# 适用性门与 S3 构建量具隔着阶段边界，不能跨侧导入；这里故意重复一条小正则。
NPU_ARCH_FLAG = re.compile(r"--npu-arch=([A-Za-z0-9_-]+)")

GAUGE_DEFECT_PROTOCOL = (
    "若你判断是门禁自身误判：按 SKILL.md 的量具规则停止本轮并记录，"
    "不要修改量具续跑。"
)


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


def _npu_arch_from_cmake(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    # CMake 的 # 注释不能给出构建事实；逐行去掉后再提取。
    uncommented = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    values = sorted(set(NPU_ARCH_FLAG.findall(uncommented)))
    if not values:
        raise AlignError(f"{path} 中没有 --npu-arch=<值> 参数")
    if len(values) > 1:
        raise AlignError(f"{path} 中有互相冲突的 --npu-arch 参数：{values}")
    return values[0]


def main():
    parser = argparse.ArgumentParser(description="c_api 接口适用性检查")
    parser.add_argument("--env", required=True, help="环境指纹 JSON")
    parser.add_argument("--candidate", required=True, help="待检查的公开接口名")
    parser.add_argument("--header", action="append", required=True,
                        help="公开头文件，可重复")
    parser.add_argument("--op-dir", required=True, help="算子在工程内的目录")
    parser.add_argument("--layout", choices=("arch_dirs", "experimental"),
                        required=True, help="算子工程目录布局")
    parser.add_argument("--arch-dir", help="本机对应的 archXX 目录名")
    parser.add_argument("--arch-dir-basis",
                        help="工程把本机映射到该目录的书面依据")
    parser.add_argument("--build-cmake",
                        help="experimental 算子的 test/CMakeLists.txt")
    parser.add_argument("--allow-pending", action="store_true",
                        help="确认继续处理尚未核实的 SoC 与架构组合")
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
        "layout": args.layout,
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
    if args.layout == "experimental":
        report.update({"build_cmake": None, "npu_arch": None})

    arch_blocked = False
    structural_failures = []
    header_texts = []
    npu_arch = None
    try:
        if not re.fullmatch(r"[A-Za-z_]\w*", args.candidate):
            raise AlignError("--candidate 必须是合法的 C 接口名")
        if args.layout == "arch_dirs":
            if not args.arch_dir:
                raise AlignError("--layout arch_dirs 必须给出 --arch-dir")
            if not re.fullmatch(r"arch\d+", args.arch_dir):
                raise AlignError("--arch-dir 必须是 archXX 目录名，不能含路径片段")
            if not (args.arch_dir_basis or "").strip():
                raise AlignError("--arch-dir-basis 必须给出非空书面依据")
            if args.build_cmake:
                raise AlignError("--layout arch_dirs 不接受 --build-cmake")
        else:
            if args.arch_dir is not None:
                raise AlignError("--layout experimental 不得给出 --arch-dir")
            if not args.build_cmake:
                raise AlignError("--layout experimental 必须给出 --build-cmake")
        op_dir = Path(require_project_source(args.op_dir, args.env, "--op-dir"))
        if not op_dir.is_dir():
            raise AlignError(f"--op-dir 指向的 {op_dir} 不是目录")
        report["op_dir"] = str(op_dir)
        if args.layout == "experimental":
            build_cmake = Path(require_project_source(
                args.build_cmake, args.env, "--build-cmake"))
            expected_cmake = (op_dir / "test" / "CMakeLists.txt").resolve()
            if build_cmake != expected_cmake:
                raise AlignError(
                    f"--build-cmake 必须是算子的 {expected_cmake}，实际为 {build_cmake}")
            if not build_cmake.is_file():
                raise AlignError(f"--build-cmake 指向的 {build_cmake} 不是文件")
            report["build_cmake"] = str(build_cmake)
            try:
                npu_arch = _npu_arch_from_cmake(build_cmake)
                report["npu_arch"] = npu_arch
            except AlignError as exc:
                failures.append(str(exc))
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
            if (args.layout == "arch_dirs"
                    and (build_soc, args.arch_dir) not in VERIFIED_SOC_ARCH):
                pending.append(
                    f"无法判定，需用户确认：build_soc={build_soc} 与 "
                    f"arch_dir={args.arch_dir} 的对应关系；依据："
                    f"{args.arch_dir_basis}")
            elif (args.layout == "experimental" and npu_arch is not None
                  and (build_soc, npu_arch) not in VERIFIED_SOC_NPU_ARCH):
                pending.append(
                    f"无法判定，需用户确认：build_soc={build_soc} 与 "
                    f"npu_arch={npu_arch} 的对应关系；依据：{args.build_cmake}")
        except SocBindingError:
            basis = (args.arch_dir_basis if args.layout == "arch_dirs"
                     else args.build_cmake)
            pending.append(
                f"无法判定，需用户确认：设备型号 {device_name!r} 不能映射为"
                f"已知 build_soc；依据：{basis}")

        if args.layout == "experimental":
            op_host = (op_dir / "op_host").resolve()
            op_kernel = (op_dir / "op_kernel").resolve()
            for required_dir in (op_host, op_kernel):
                if not required_dir.is_dir():
                    message = f"experimental 实现目录不存在：{required_dir}"
                    failures.append(message)
                    structural_failures.append(message)
                    arch_blocked = True
            if op_host.is_dir():
                matched = _implementation_file(op_host, args.candidate)
                if matched is None:
                    message = (
                        f"experimental 实现目录 {op_host} 的 .cpp 中没有 "
                        f"{args.candidate} 接口")
                    failures.append(message)
                    structural_failures.append(message)
                    arch_blocked = True
                else:
                    report["matched_file"] = matched
                    if op_kernel.is_dir():
                        report["checks"]["arch_implementation"] = {
                            "passed": True, "matched_file": matched,
                        }
        else:
            # 同名接口可能只在另一个架构目录实现，所以必须检查本机目录本身。
            arch_root = (op_dir / args.arch_dir).resolve()
            if not arch_root.is_relative_to(op_dir.resolve()):
                raise AlignError("--arch-dir 解析后越过了 --op-dir")
            if not arch_root.is_dir():
                message = f"本机架构目录不存在：{arch_root}"
                failures.append(message)
                structural_failures.append(message)
                arch_blocked = True
            else:
                matched = _implementation_file(arch_root, args.candidate)
                if matched is None:
                    message = (
                        f"本机架构目录 {arch_root} 的 .cpp 中没有 "
                        f"{args.candidate} 接口")
                    failures.append(message)
                    structural_failures.append(message)
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
    if arch_blocked and failures and all(
            message in structural_failures for message in failures):
        print(GAUGE_DEFECT_PROTOCOL)
        return 3
    if failures or (pending and not args.allow_pending):
        print(GAUGE_DEFECT_PROTOCOL)
        return 2
    print(f"c_api 接口适用性检查通过 → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
