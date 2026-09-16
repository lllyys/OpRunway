#!/usr/bin/env python3
"""installer —— H4 安装 preflight 与 H5 安装/回滚（contract-ir.md §11 + install-preflight.md）。

全部 fail-closed，抛 InstallReject（.stop_code 为 SKILL.md 停机码）。preflight 只读校验，
install/rollback 是显式写动作，均先全量校验后再动文件（先验后改），路径一律 containment。
兼容配置 assets/compat/compat-config.json 随 skill 分发。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

_COMPAT = (Path(__file__).resolve().parent.parent / "assets" / "compat"
           / "compat-config.json")
_MANIFEST_KEYS = {"target_rel", "backup", "files"}


class InstallReject(Exception):
    def __init__(self, stop_code, reason):
        self.stop_code = stop_code
        self.reason = reason
        super().__init__(f"{stop_code}: {reason}")


def load_compat():
    return json.loads(_COMPAT.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(repo: Path) -> str:
    import subprocess
    r = subprocess.run(["git", "--no-optional-locks", "-C", str(repo),
                        "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _contained(root: Path, rel: str) -> Path:
    """把 rel 解析进 root 并断言不越界（防 ../ 逃逸与符号链接逃逸）。返回绝对路径。"""
    root = root.resolve()
    raw = root
    for part in Path(rel).parts:
        if part in ("..", "/"):
            raise InstallReject("TARGET_INCOMPATIBLE", f"路径含非法段：{rel}")
        raw = raw / part
        if raw.is_symlink():
            raise InstallReject("TARGET_INCOMPATIBLE", f"路径含符号链接：{rel}")
    dst = (root / rel)
    resolved = dst.resolve()
    if root != resolved and root not in resolved.parents:
        raise InstallReject("TARGET_INCOMPATIBLE", f"路径越界：{rel}")
    return dst


def _canonical_target(ir) -> str:
    op, family = ir["op"], ir["family"]
    if not re.match(r"^[a-z][a-z0-9_]*$", op) or not re.match(r"^[a-z][a-z0-9_]*$", family):
        raise InstallReject("TARGET_INCOMPATIBLE", f"op/family 非法：{family}/{op}")
    return f"test/{family}/{op}"


def _parse_prototype(header_text: str, symbol: str):
    """从公开头解析 symbol 的完整原型参数 ctype 序（精确匹配，非子串）。"""
    m = re.search(r"\baclblasStatus_t\s+" + re.escape(symbol) + r"\s*\(([^;{]*)\)",
                  header_text)
    if not m:
        return None
    args = m.group(1).strip()
    if not args or args == "void":
        return []
    ctypes = []
    for a in args.split(","):
        a = a.strip()
        a = re.sub(r"\s+[A-Za-z_][A-Za-z0-9_]*$", "", a)  # 去参数名
        ctypes.append(re.sub(r"\s+", " ", a).replace(" *", "*"))
    return ctypes


def preflight(ir, repo, soc, compat=None):
    """H4：fail-closed 硬门。返回 (arch_dir, target_rel)。"""
    repo = Path(repo)
    compat = compat or load_compat()
    target_rel = _canonical_target(ir)
    # 1. revision + frame 指纹
    head = _git_head(repo)
    if head != compat["revision"]:
        raise InstallReject("TARGET_INCOMPATIBLE",
                            f"revision 不符：{head or '非 git'} != {compat['revision']}")
    for rel, want in compat["frame_fingerprints"].items():
        f = _contained(repo, rel)
        if not f.is_file() or _sha(f) != want:
            raise InstallReject("TARGET_INCOMPATIBLE", f"frame 指纹不符：{rel}")
    # 2. 入口签名交叉校验（解析完整原型，逐 ctype 比对签名表 device_signature）
    entry = _contained(repo, compat["entry_header"])
    if not entry.is_file():
        raise InstallReject("TARGET_INCOMPATIBLE", f"缺入口头 {compat['entry_header']}")
    proto = _parse_prototype(entry.read_text(encoding="utf-8", errors="ignore"),
                             ir["symbol"])
    if proto is None:
        raise InstallReject("TARGET_INCOMPATIBLE", f"入口头无 {ir['symbol']} 原型")
    ir_ctypes = [p["ctype"] for p in ir["params"]]
    if proto != ir_ctypes:
        raise InstallReject("TARGET_INCOMPATIBLE",
                            f"{ir['symbol']} 原型不符：头={proto} IR={ir_ctypes}")
    # 3. soc→arch（精确键匹配，非前缀）
    arch = compat["soc_arch_map"].get(soc)
    if arch is None:
        raise InstallReject("TARGET_INCOMPATIBLE",
                            f"soc 无 arch 映射：{soc}（合法键：{sorted(compat['soc_arch_map'])}）")
    # 4. 目标目录同名歧义 + canonical 一致
    op = ir["op"]
    hits = sorted(str(p.relative_to(repo)) for p in repo.glob(f"test/*/{op}")
                  if p.is_dir())
    if (repo / "test" / op).is_dir():
        hits.append(f"test/{op}")
    if len(hits) > 1:
        raise InstallReject("TARGET_AMBIGUOUS", f"test/*/{op} 多处命中：{hits}")
    if hits and hits[0] != target_rel:
        raise InstallReject("TARGET_AMBIGUOUS",
                            f"既有目录 {hits[0]} 与 IR canonical {target_rel} 不符")
    # 5. 算子实现在场（该 arch 下）
    impl = [d for d in (list(repo.glob(f"blas/*/{op}/{arch}"))
                        + list(repo.glob(f"blas/{op}/{arch}"))) if d.is_dir()]
    if not impl:
        raise InstallReject("TARGET_INCOMPATIBLE",
                            f"{op} 在 {arch} 无 blas 实现目录")
    # 6. CMake helper 在位
    helper_file = _contained(repo, compat["cmake_helper_file"])
    if not helper_file.is_file() or compat["cmake_helper"] not in helper_file.read_text(
            encoding="utf-8", errors="ignore"):
        raise InstallReject("TARGET_INCOMPATIBLE",
                            f"缺 CMake helper {compat['cmake_helper']}")
    return arch, target_rel


def _manifest_path(repo):
    mp = Path(repo).resolve() / ".overlay-manifest.json"
    if mp.is_symlink():
        raise InstallReject("TARGET_INCOMPATIBLE", "manifest 是符号链接")
    return mp


def install(staging_root, repo, target_rel, arch, dry_run=False):
    """H5：先全量校验（含路径 containment、非空文件集、无活动 manifest、TARGET_EXISTS），
    再备份 + 写文件 + 写 manifest。任一校验失败前不动任何文件。"""
    staging_root, repo = Path(staging_root).resolve(), Path(repo).resolve()
    src_root = _contained(staging_root, target_rel)
    if not src_root.is_dir():
        raise InstallReject("TARGET_EXISTS", f"staging 缺目标树：{target_rel}")
    files = sorted(f for f in src_root.rglob("*") if f.is_file())
    if not files:
        raise InstallReject("TARGET_EXISTS", f"staging 目标树为空：{target_rel}")
    if not dry_run and _manifest_path(repo).exists():
        raise InstallReject("TARGET_EXISTS", "已有 .overlay-manifest.json（先 rollback）")
    plan = []
    for f in files:
        rel = str(f.relative_to(staging_root))
        dst = _contained(repo, rel)
        if dst.exists() and dst.read_bytes() != f.read_bytes():
            raise InstallReject("TARGET_EXISTS", f"目标已存在且不同：{rel}")
        plan.append((rel, _sha(f)))
    if dry_run:
        return {"dry_run": True, "files": plan}
    target_dir = _contained(repo, target_rel)
    backup_rel = target_rel + ".upstream-backup"
    backup = _contained(repo, backup_rel)
    manifest = {"target_rel": target_rel, "arch": arch, "backup": None,
                "backup_files": None, "files": plan}
    backed_up = False
    if target_dir.exists():
        if backup.exists():
            raise InstallReject("TARGET_EXISTS", f"备份已存在：{backup_rel}")
        manifest["backup_files"] = sorted(
            [str(x.relative_to(target_dir)),
             _sha(x) if (x.is_file() and not x.is_symlink()) else
             ("symlink:" + os.readlink(x) if x.is_symlink() else "dir")]
            for x in target_dir.rglob("*"))
        target_dir.rename(backup)
        backed_up = True
        manifest["backup"] = backup_rel
    try:
        for f in files:
            rel = str(f.relative_to(staging_root))
            dst = _contained(repo, rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(f.read_bytes())
        _manifest_path(repo).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError as exc:  # 半安装：删部分新树后恢复备份
        import shutil
        try:
            if target_dir.exists():
                shutil.rmtree(target_dir)  # 不吞错：删不掉就不能谎称已回退
            if backed_up and backup.exists() and not target_dir.exists():
                backup.rename(target_dir)
        except OSError as exc2:
            raise InstallReject("ROLLBACK_REJECTED",
                                f"安装写入失败且自动回退未完成，需人工：{exc}; {exc2}")
        raise InstallReject("TARGET_EXISTS", f"安装写入失败已回退：{exc}")
    return manifest


def rollback(repo):
    """先全量校验（manifest schema、路径 containment、安装树精确文件集、backup 全树 SHA），
    全过后再原子恢复。任何异常前不删目标。"""
    repo = Path(repo).resolve()
    mp = _manifest_path(repo)
    if not mp.is_file():
        raise InstallReject("ROLLBACK_REJECTED", "无 .overlay-manifest.json")
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallReject("ROLLBACK_REJECTED", f"manifest 非法 JSON：{exc}")
    if not _MANIFEST_KEYS <= set(manifest) or not isinstance(manifest.get("files"), list):
        raise InstallReject("ROLLBACK_REJECTED", "manifest schema 非法（缺必需键）")
    target_rel = manifest["target_rel"]
    if not re.match(r"^test/[a-z0-9_]+/[a-z0-9_]+$", target_rel):
        raise InstallReject("ROLLBACK_REJECTED", f"manifest target_rel 非法：{target_rel}")
    target_dir = _contained(repo, target_rel)
    # 安装树精确文件集 + 逐文件 SHA（多、少、改都拒）
    recorded = {rel: sha for rel, sha in manifest["files"]}
    on_disk = {str(f.relative_to(repo)) for f in target_dir.rglob("*") if f.is_file()} \
        if target_dir.is_dir() else set()
    if on_disk != set(recorded):
        raise InstallReject("ROLLBACK_REJECTED",
                            f"安装树文件集与 manifest 不符：多={on_disk-set(recorded)} "
                            f"少={set(recorded)-on_disk}")
    for rel, want in recorded.items():
        f = _contained(repo, rel)
        if not f.is_file() or _sha(f) != want:
            raise InstallReject("ROLLBACK_REJECTED", f"盘上 SHA 与记录不符：{rel}")
    backup_dir = None
    if manifest["backup"]:
        if not re.match(r"^test/[a-z0-9_]+/[a-z0-9_]+\.upstream-backup$",
                        manifest["backup"]):
            raise InstallReject("ROLLBACK_REJECTED", f"backup 路径非法：{manifest['backup']}")
        backup_dir = _contained(repo, manifest["backup"])
        if not backup_dir.is_dir():
            raise InstallReject("ROLLBACK_REJECTED", f"备份缺失：{manifest['backup']}")
        # backup 全树精确文件集 + SHA（多/少/改都拒）
        recorded_bak = {rel: sha for rel, sha in (manifest.get("backup_files") or [])}
        on_disk_bak = {str(x.relative_to(backup_dir)) for x in backup_dir.rglob("*")}
        if on_disk_bak != set(recorded_bak):
            raise InstallReject("ROLLBACK_REJECTED", "备份树条目集与 manifest 不符")
        for rel, want in recorded_bak.items():
            # rel 源自 backup_dir 自身 rglob 且 on_disk_bak==recorded 已核，路径可信；
            # 不走 _contained（它拒符号链接末端，会使含符号链接的合法备份永不可回滚）。
            if ".." in Path(rel).parts:
                raise InstallReject("ROLLBACK_REJECTED", f"备份条目路径非法：{rel}")
            bf = backup_dir / rel
            if want == "dir":
                if not bf.is_dir() or bf.is_symlink():
                    raise InstallReject("ROLLBACK_REJECTED", f"备份条目应为目录：{rel}")
            elif isinstance(want, str) and want.startswith("symlink:"):
                if not bf.is_symlink() or os.readlink(bf) != want[len("symlink:"):]:
                    raise InstallReject("ROLLBACK_REJECTED", f"备份符号链接目标不符：{rel}")
            elif not bf.is_file() or bf.is_symlink() or _sha(bf) != want:
                raise InstallReject("ROLLBACK_REJECTED", f"备份 SHA 与记录不符：{rel}")
    # 全过，move-aside 恢复（失败可复原）
    import shutil
    aside = target_dir.with_name(target_dir.name + ".rolling-out")
    if aside.exists():
        raise InstallReject("ROLLBACK_REJECTED", f"残留 {aside.name}，需人工")
    target_dir.rename(aside)
    try:
        if backup_dir:
            backup_dir.rename(target_dir)
    except OSError as exc:
        if not target_dir.exists():
            aside.rename(target_dir)  # backup 恢复失败：复原原安装树
        raise InstallReject("ROLLBACK_REJECTED", f"恢复失败已复原：{exc}")
    # 至此 target 已是 backup（或无 backup 时待删）；aside 清理失败不影响正确性
    mp.unlink()
    try:
        shutil.rmtree(aside)
    except OSError as exc:
        return {**manifest, "_warning": f"aside 残留待人工清理：{aside.name}（{exc}）"}
    return manifest
