"""Verify that the candidate operator covers the SoC of the acceptance machine.

两道检查，前一道便宜，后一道彻底：

`--build-log` 在构建刚结束时扫日志，判断算子本身是否声明了真机的构建族。
不声明就是结构性阻塞——换个 SoC 重编虽然能编过，但编出来的包在真机上
必然加载失败，那不是修复，是把验收前提换掉了。所以它单独占一个退出码。

`--vendor-root` 在安装之后检查包里是否真有该构建族的 config 与 kernel 产物。
"""

import argparse
import json
from pathlib import Path

from _soc_binding import (SocBindingError, SocUnsupportedError, infer_build_soc,
                          inspect_package, scan_build_log)
import _stage_card


def main():
    parser = argparse.ArgumentParser(description="待验收算子与真机 SoC 一致性门禁")
    parser.add_argument("--env", required=True, help="probe_env.py 生成的环境指纹")
    parser.add_argument("--build-log",
                        help="本轮构建日志；扫描算子是否声明真机构建族，构建后立即跑")
    parser.add_argument("--vendor-root", help="本轮安装的 vendor 根目录；安装后跑")
    parser.add_argument("-o", "--output", default="soc_binding.json")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    if not args.build_log and not args.vendor_root:
        parser.error("--build-log 与 --vendor-root 至少给一个")

    failures = []
    report = {"schema_version": 1, "build_soc": None, "unsupported_hits": None,
              "binding": None, "failures": failures}
    unsupported = None
    try:
        env = json.loads(Path(args.env).read_text(encoding="utf-8"))
        device_name = env.get("devices", {}).get("selected_name")
        if not device_name:
            raise SocBindingError("环境指纹缺少所选设备的 selected_name")
        build_soc = infer_build_soc(device_name)
        report["build_soc"] = build_soc

        if args.build_log:
            hits = scan_build_log(args.build_log, build_soc)
            report["unsupported_hits"] = hits
            if hits:
                unsupported = SocUnsupportedError(
                    f"算子未声明真机构建族 {build_soc}，构建日志已给出终局结论："
                    f"{hits[0]!r}。不得改用其他 SoC 重建、安装或冒烟。")
                raise unsupported

        if args.vendor_root:
            report["binding"] = inspect_package(args.vendor_root, device_name)
    except (OSError, json.JSONDecodeError, SocBindingError) as exc:
        failures.append(str(exc))

    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if unsupported is not None:
        print(f"✗ {unsupported}")
        print("S3 结构性阻塞：出具「阻塞·未验收 @S3」，解除条件是算子侧补该 SoC 声明。")
        return 3
    if failures:
        for failure in failures:
            print(f"✗ {failure}")
        return 2
    print(f"待验收算子包 SoC 门禁通过 → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
