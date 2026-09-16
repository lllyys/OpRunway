#!/usr/bin/env python3
"""harness —— 本 skill 的阶段化 CLI 入口（SKILL.md 主流程 H1–H5）。

Step 1 交付 load（H1 装包）；compile/render 随 Step 2/3 交付，preflight/install/
rollback 随 Step 3 交付。未交付的子命令如实报错退出 3，不做占位实现。

退出码约定（SKILL.md）：0 成功；2 停机（stderr 末行 STOP <停机码>）；3 输入路径或
环境错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_loader  # noqa: E402
import contract  # noqa: E402
import renderer  # noqa: E402
import installer  # noqa: E402
import json as _json


def _cmd_load(args) -> int:
    package_dir = Path(args.package)
    if not package_dir.is_dir():
        print(f"任务包目录不存在：{package_dir}", file=sys.stderr)
        return 3
    csvs = sorted(package_dir.glob("*_test.csv"))
    if len(csvs) != 1:
        print(f"任务包须恰好一个 *_test.csv，实得 {len(csvs)}：{[c.name for c in csvs]}",
              file=sys.stderr)
        return 3
    try:
        facts, column_specs, header, meta = (
            package_loader.load_package_with_meta(package_dir)
        )
    except package_loader.LoaderReject as exc:
        print(f"{exc.code}: {exc.reason}", file=sys.stderr)
        print(f"STOP {exc.code}", file=sys.stderr)
        return 2
    # 快照是 H1→H2 的完整阶段工件：H2 只消费它，不重读活任务包（F1）。
    # 摘要取自装载器验证过的同一份字节，本函数不重读文件另算（F2）。
    snapshot = {
        "snapshot_version": 1,
        "op": facts["op"],
        "package_dir": str(package_dir.resolve()),
        "facts": facts,
        "header": header,
        "column_specs": column_specs,
        "gen_csv_sha256": meta["gen_csv_sha256"],
        "csv_sha256": meta["csv_sha256"],
        "csv_name": meta["csv_name"],
        "schema_version": facts["schema_version"],
        "generator_version": facts["generator_version"],
    }
    out = Path("package.json")
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    tmp.replace(out)  # 原子落位（R3）
    print(f"op={facts['op']} columns={len(header)} snapshot={out.resolve()}")
    return 0


def _cmd_compile(_args) -> int:
    snap = Path("package.json")
    if not snap.is_file():
        print("缺 package.json：先跑 load（H1）", file=sys.stderr)
        return 3
    snapshot = _json.loads(snap.read_text(encoding="utf-8"))
    try:
        ir = contract.compile_contract(snapshot)
    except contract.iv.ContractReject as exc:
        print(f"{exc.payload['code']}: {exc.payload}", file=sys.stderr)
        print(f"STOP {exc.payload['code']}", file=sys.stderr)
        return 2
    Path("ir.json").write_text(
        _json.dumps(ir, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"op={ir['op']} ir.json written ({len(ir['params'])} params)")
    return 0


def _cmd_render(args) -> int:
    ir_path, snap_path = Path("ir.json"), Path("package.json")
    if not ir_path.is_file() or not snap_path.is_file():
        print("缺 ir.json 或 package.json：先跑 load、compile", file=sys.stderr)
        return 3
    import hashlib
    import shutil
    ir = _json.loads(ir_path.read_text(encoding="utf-8"))
    try:
        contract.iv.validate_ir(ir)  # 边界重验：ir.json 落盘后可能被改
    except contract.iv.ContractReject as exc:
        print(f"{exc.payload['code']}: {exc.payload}", file=sys.stderr)
        print(f"STOP {exc.payload['code']}", file=sys.stderr)
        return 2
    compat = installer.load_compat()
    if args.arch not in set(compat["soc_arch_map"].values()):
        print(f"未知 arch：{args.arch}（合法：{sorted(set(compat['soc_arch_map'].values()))}）",
              file=sys.stderr)
        return 3
    ir["_arch_dir"] = args.arch
    snapshot = _json.loads(snap_path.read_text(encoding="utf-8"))
    csv_path = Path(snapshot["package_dir"]) / snapshot["csv_name"]
    csv_bytes = csv_path.read_bytes()
    if hashlib.sha256(csv_bytes).hexdigest() != snapshot["csv_sha256"]:
        print("CSV 与 H1 快照摘要不符（TOCTOU）", file=sys.stderr)
        return 3
    staging = Path("staging")
    if staging.exists():
        shutil.rmtree(staging)  # render 前清旧树，防渲染失败留残树被后续 install 消费
    try:
        out1 = renderer.render_overlay(ir, csv_bytes)
        out2 = renderer.render_overlay(ir, csv_bytes)
    except renderer.iv.ContractReject as exc:
        print(f"{exc.payload['code']}: {exc.payload}", file=sys.stderr)
        print(f"STOP {exc.payload['code']}", file=sys.stderr)
        return 2
    if out1 != out2:
        print("RENDER_MISMATCH: 双渲不一致（非确定性）", file=sys.stderr)
        print("STOP RENDER_MISMATCH", file=sys.stderr)
        return 2
    file_shas = {}
    for rel, data in out1.items():
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        file_shas[rel] = hashlib.sha256(data).hexdigest()
    prov = {"op": ir["op"], "family": ir["family"], "arch": args.arch,
            "generator_version": snapshot["generator_version"],
            "contract_version": ir["version"],
            "compat_config_version": compat["config_version"],
            "gen_csv_sha256": snapshot["gen_csv_sha256"],
            "csv_sha256": snapshot["csv_sha256"],
            "file_sha256": file_shas}
    (staging / "provenance.json").write_text(
        _json.dumps(prov, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"staging: {len(out1)} files arch={args.arch} -> {staging.resolve()}")
    return 0


def _load_ir_for_install():
    ir_path = Path("ir.json")
    if not ir_path.is_file():
        return None
    return _json.loads(ir_path.read_text(encoding="utf-8"))


def _revalidate_ir(ir):
    contract.iv.validate_ir(ir)


def _check_staging_chain(ir):
    """校验 staging 与 provenance 一致（M-05 工件链）：精确文件集 + 逐文件 SHA +
    op/family/arch 与 ir 一致。路径经 containment。返回 prov 或抛。"""
    import hashlib
    staging = Path("staging").resolve()
    prov_path = staging / "provenance.json"
    if not prov_path.is_file():
        raise installer.InstallReject("TARGET_INCOMPATIBLE", "staging 缺 provenance.json")
    prov = _json.loads(prov_path.read_text(encoding="utf-8"))
    if (prov.get("op"), prov.get("family")) != (ir["op"], ir["family"]):
        raise installer.InstallReject("TARGET_INCOMPATIBLE",
                                      "provenance op/family 与 ir 不符")
    recorded = prov.get("file_sha256", {})
    on_disk = {str(f.relative_to(staging)) for f in staging.rglob("*")
               if f.is_file() and str(f.relative_to(staging)) != "provenance.json"}
    if on_disk != set(recorded):
        raise installer.InstallReject("TARGET_INCOMPATIBLE",
                                      f"staging 文件集与 provenance 不符："
                                      f"多={on_disk-set(recorded)} 少={set(recorded)-on_disk}")
    for rel, want in recorded.items():
        f = installer._contained(staging, rel)
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != want:
            raise installer.InstallReject("TARGET_INCOMPATIBLE",
                                          f"staging 文件与 provenance 不符：{rel}")
    return prov


def _cmd_preflight(args) -> int:
    ir = _load_ir_for_install()
    if ir is None:
        print("缺 ir.json：先跑 compile（H2）", file=sys.stderr)
        return 3
    try:
        _revalidate_ir(ir)
        arch, target = installer.preflight(ir, args.repo, args.soc)
    except (installer.InstallReject, contract.iv.ContractReject) as exc:
        code = getattr(exc, "stop_code", None) or exc.payload["code"]
        print(f"{code}: {getattr(exc, 'reason', exc)}", file=sys.stderr)
        print(f"STOP {code}", file=sys.stderr)
        return 2
    print(f"preflight OK: arch={arch} target={target}")
    return 0


def _cmd_install(args) -> int:
    ir = _load_ir_for_install()
    if ir is None or not Path("staging").is_dir():
        print("缺 ir.json 或 staging/：先跑 compile、render", file=sys.stderr)
        return 3
    try:
        _revalidate_ir(ir)
        prov = _check_staging_chain(ir)
        arch, target = installer.preflight(ir, args.repo, args.soc)
        if prov["arch"] != arch:
            raise installer.InstallReject("TARGET_INCOMPATIBLE",
                                          f"staging arch={prov['arch']} 与 soc→arch={arch} 不符")
        result = installer.install("staging", args.repo, target, arch,
                                   dry_run=args.dry_run)
    except (installer.InstallReject, contract.iv.ContractReject) as exc:
        code = getattr(exc, "stop_code", None) or exc.payload["code"]
        print(f"{code}: {getattr(exc, 'reason', exc)}", file=sys.stderr)
        print(f"STOP {code}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"dry-run: 将写 {len(result['files'])} 个文件到 {target}")
    else:
        print(f"installed {len(result['files'])} files；backup={result['backup']}；"
              f"rollback: harness.py rollback --repo {args.repo}")
    return 0


def _cmd_rollback(args) -> int:
    try:
        m = installer.rollback(args.repo)
    except installer.InstallReject as exc:
        print(f"{exc.stop_code}: {exc.reason}", file=sys.stderr)
        print(f"STOP {exc.stop_code}", file=sys.stderr)
        return 2
    if m.get("_warning"):
        print(f"rolled back {m['target_rel']}（警告：{m['_warning']}）")
    else:
        print(f"rolled back {m['target_rel']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harness.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_load = sub.add_parser("load", help="H1 装包：三道门 + 支持矩阵预筛，写 package.json 快照")
    p_load.add_argument("--package", required=True, help="任务包目录（绝对路径）")
    p_load.set_defaults(func=_cmd_load)
    p_compile = sub.add_parser("compile", help="H2 契约编译：快照 → ir.json")
    p_compile.set_defaults(func=_cmd_compile)
    p_render = sub.add_parser("render", help="H3 渲染：ir.json + CSV → staging 树（双渲）")
    p_render.add_argument("--arch", default="arch22", help="目标 arch 目录（默认 arch22）")
    p_render.set_defaults(func=_cmd_render)
    p_pf = sub.add_parser("preflight", help="H4 安装 preflight：七项 fail-closed 硬门")
    p_pf.add_argument("--repo", required=True)
    p_pf.add_argument("--soc", required=True)
    p_pf.set_defaults(func=_cmd_preflight)
    p_inst = sub.add_parser("install", help="H5 安装：备份+manifest，可回滚")
    p_inst.add_argument("--repo", required=True)
    p_inst.add_argument("--soc", required=True)
    p_inst.add_argument("--dry-run", action="store_true")
    p_inst.set_defaults(func=_cmd_install)
    p_rb = sub.add_parser("rollback", help="按 manifest 整体回滚")
    p_rb.add_argument("--repo", required=True)
    p_rb.set_defaults(func=_cmd_rollback)
    args = parser.parse_args(argv)  # 未知参数明确拒绝（R2，fail-closed）
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
