"""探测搭建 CANN 算子仓所需的基础环境（泛化、零硬编码）。

不假设任何固定安装布局。CANN 版本**不靠目录名猜**，而是：
  探多个 set_env.sh 候选 → 在子 shell 里 source → 读真实 `ASCEND_HOME_PATH`
  → 从其 basename 解析 CANN 版本（如 cann-9.0.0-beta.1 → 9.0.0-beta.1）。

输出机读 JSON（--json）+ 人读摘要。纯只读，不改环境、不装任何东西。

CLI:
  python3 detect_env.py [--set-env <path>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# CANN toolkit 常见安装根（按出现频率排，可被 --set-env / ASCEND_HOME_PATH 覆盖）。
# 列举的是“去哪找”，不是“一定在哪”——找不到就如实报缺，由 SKILL 询问用户。
_COMMON_ROOTS = [
    "/usr/local/Ascend",
    "/opt/Ascend",
    str(Path.home() / "Ascend"),
    "/home/HwHiAiUser/Ascend",
]

# 每个根下 set_env.sh 的常见相对位置（toolkit 布局 / 直装布局 / cann 软链布局都覆盖）。
_REL_CANDIDATES = [
    "ascend-toolkit/set_env.sh",
    "ascend-toolkit/latest/set_env.sh",
    "cann/set_env.sh",
    "set_env.sh",
    "latest/set_env.sh",
]

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)(?:[-_.]?(beta|rc|alpha|RC)\.?(\d+))?")


def set_env_candidates(explicit: str | None = None) -> list[Path]:
    """按优先级给出 set_env.sh 候选路径（去重，保持顺序）。"""
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    home_env = os.environ.get("ASCEND_HOME_PATH")
    if home_env:
        cands.append(str(Path(home_env) / "set_env.sh"))
        cands.append(str(Path(home_env).parent / "set_env.sh"))
    tk_home = os.environ.get("ASCEND_TOOLKIT_HOME")
    if tk_home:
        cands.append(str(Path(tk_home) / "set_env.sh"))
    for root in _COMMON_ROOTS:
        for rel in _REL_CANDIDATES:
            cands.append(str(Path(root) / rel))
    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(Path(c))
    return out


def find_set_env(explicit: str | None = None) -> Path | None:
    """返回第一个真实存在的 set_env.sh，找不到返回 None。"""
    for p in set_env_candidates(explicit):
        if p.is_file():
            return p
    return None


def source_ascend_home(set_env: Path) -> str | None:
    """在干净子 shell 里 source set_env.sh，回读真实 ASCEND_HOME_PATH。

    这是判定“当前激活哪个 CANN”的唯一可靠手段——不靠目录名/软链猜。
    """
    try:
        res = subprocess.run(
            ["bash", "-lc", f'source "{set_env}" >/dev/null 2>&1; printf "%s" "$ASCEND_HOME_PATH"'],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    home = res.stdout.strip()
    return home or None


def _mk_version(m: "re.Match") -> dict:
    core = m.group(1)
    pre = f"{m.group(2).lower()}.{m.group(3)}" if m.group(2) else None
    full = f"{core}-{pre}" if pre else core
    return {"full": full, "core": core, "prerelease": pre}


_NONE_VERSION = {"full": None, "core": None, "prerelease": None}


def parse_cann_version(ascend_home: str | None) -> dict:
    """从 ASCEND_HOME_PATH 解析 CANN 版本（泛化、抗 driver 版本污染）。

    优先级（authoritative 在前）：
      1. ASCEND_HOME_PATH 的 basename（及其 realpath basename），如 cann-9.0.0-beta.1
      2. 回退：**仅 home 自身**的 CANN 安装信息文件（ascend_toolkit_install.info / version.cfg）

    刻意**不读 parent 目录的 version.info**——那常是 driver/firmware 版本（如 25.5.0），
    会把 CANN 版本带偏（实测 /usr/local/Ascend/version.info=25.5.0 即 driver 版本）。
    """
    if not ascend_home:
        return dict(_NONE_VERSION)
    home = Path(ascend_home)

    # 1) basename（含解析软链后的真实 basename）—— cann-X.Y.Z[-beta.N] 是最可靠信号
    name_texts = [home.name]
    try:
        real_name = home.resolve().name
        if real_name and real_name != home.name:
            name_texts.append(real_name)
    except OSError:
        pass
    for t in name_texts:
        m = _VERSION_RE.search(t)
        if m:
            return _mk_version(m)

    # 2) 回退：只读 home 自身的 CANN 安装信息（不读 parent，避免 driver 版本）
    for vf in ("ascend_toolkit_install.info", "version.cfg"):
        f = home / vf
        if f.is_file():
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _VERSION_RE.search(txt)
            if m:
                return _mk_version(m)
    return dict(_NONE_VERSION)


def soc_name_to_build_soc(soc_name: str | None) -> str | None:
    """`acl.get_soc_name()` 的原始名 → build.sh 短 soc 串（泛化映射）。

    Ascend910_9382 / _9362 / _5512 ... → ascend910_93 / ascend910_55（取 `_` 后两位档号）
    Ascend910B3 / Ascend910ProB        → ascend910b
    Ascend910A / Ascend910PremiumA      → ascend910
    Ascend950*  → ascend950;Ascend310P* → ascend310p;Ascend310B* → ascend310b
    其它原样小写兜底（交给 build.sh 自己校验）。
    """
    if not soc_name:
        return None
    low = soc_name.strip().lower()
    m = re.match(r"ascend910_(\d{2})\d*", low)        # _93xx / _55xx 系列
    if m:
        return f"ascend910_{m.group(1)}"
    if re.match(r"ascend910(pro)?b", low):            # 910b* / 910prob*
        return "ascend910b"
    if low.startswith("ascend910"):                   # 910a / 910proa / 910premiuma
        return "ascend910"
    if low.startswith("ascend950"):
        return "ascend950"
    if low.startswith("ascend310p"):
        return "ascend310p"
    if low.startswith("ascend310b"):
        return "ascend310b"
    return low


def detect_soc(set_env: "Path | None") -> dict:
    """用 CANN 运行时 `acl.get_soc_name()` 探测精确 soc，并映射到 build.sh 短 soc 串。

    需 CANN + set_env（子 shell source 后跑 pyACL）；acl 不可用/无 NPU 权限时静默返回 None。
    返回 {raw: 'Ascend910_9382'|None, build_soc: 'ascend910_93'|None}。
    比「向用户问 SOC」更可靠——避免人工把 910_93xx 误当 ascend910b。
    """
    none = {"raw": None, "build_soc": None}
    if not set_env:
        return dict(none)
    # 用 marker 包住 soc 名：CANN 运行时会往 **stdout** 打 [Warning] 行（不是 stderr，
    # 简单取末行会取到 warning），故 grep marker 行而非末行。
    pycode = "import acl;acl.init();n=acl.get_soc_name();print('__SOC__'+str(n));acl.finalize()"
    try:
        res = subprocess.run(
            ["bash", "-lc", f'source "{set_env}" >/dev/null 2>&1; python3 -c "{pycode}"'],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return dict(none)
    raw = None
    for line in res.stdout.splitlines():
        if line.startswith("__SOC__"):
            raw = line[len("__SOC__"):].strip()
            break
    if raw and not raw.lower().startswith("ascend"):  # 排除噪声/空
        raw = None
    return {"raw": raw, "build_soc": soc_name_to_build_soc(raw)}


def _tool_version(tool: str) -> str | None:
    if shutil.which(tool) is None:
        return None
    for flag in ("--version", "-V", "version"):
        try:
            res = subprocess.run([tool, flag], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        out = (res.stdout or res.stderr).strip().splitlines()
        if out:
            return out[0].strip()
    return shutil.which(tool)


def detect_prereqs() -> dict:
    """系统构建依赖现状。required=构建必需，optional=有更好没有也能跑。"""
    required = ["cmake", "gcc", "g++", "make", "git"]
    optional = ["ccache"]
    out = {"required": {}, "optional": {}, "missing_required": [], "missing_optional": []}
    for t in required:
        v = _tool_version(t)
        out["required"][t] = v
        if v is None:
            out["missing_required"].append(t)
    for t in optional:
        v = _tool_version(t)
        out["optional"][t] = v
        if v is None:
            out["missing_optional"].append(t)
    return out


def detect_python() -> dict:
    """可见的 python 解释器（系统 + 常见版本名），供 SKILL 选/问版本用。"""
    found = {}
    for name in ("python3", "python", "python3.12", "python3.11", "python3.10", "python3.9"):
        path = shutil.which(name)
        if path and path not in found.values():
            v = _tool_version(name)
            found[name] = {"path": path, "version": v}
    return found


def detect_conda() -> dict:
    """conda 是否可用 + 已有环境列表。不存在时 available=False（SKILL 决定装 miniconda / 用 venv）。"""
    conda = shutil.which("conda")
    if not conda:
        return {"available": False, "path": None, "version": None, "envs": []}
    version = _tool_version("conda")
    envs: list[str] = []
    try:
        res = subprocess.run(["conda", "env", "list", "--json"], capture_output=True, text=True, timeout=30)
        data = json.loads(res.stdout or "{}")
        envs = [Path(p).name for p in data.get("envs", [])]
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return {"available": True, "path": conda, "version": version, "envs": envs}


def detect(explicit_set_env: str | None = None) -> dict:
    set_env = find_set_env(explicit_set_env)
    ascend_home = source_ascend_home(set_env) if set_env else None
    version = parse_cann_version(ascend_home)
    soc = detect_soc(set_env)
    return {
        "cann": {
            "set_env_sh": str(set_env) if set_env else None,
            "ascend_home_path": ascend_home,
            "version": version,
            "soc": soc,
            "ready": bool(set_env and ascend_home),
        },
        "conda": detect_conda(),
        "python": detect_python(),
        "prereqs": detect_prereqs(),
    }


def _human(d: dict) -> str:
    c = d["cann"]
    lines = ["== CANN toolkit =="]
    if c["ready"]:
        lines.append(f"  set_env.sh : {c['set_env_sh']}")
        lines.append(f"  ASCEND_HOME: {c['ascend_home_path']}")
        lines.append(f"  版本       : {c['version']['full'] or '解析失败'} (core={c['version']['core']})")
        soc = c.get("soc") or {}
        if soc.get("build_soc"):
            lines.append(f"  SOC        : {soc['build_soc']}  (acl 探测原始名 {soc.get('raw')})")
        else:
            lines.append("  SOC        : 自动探测失败（acl 不可用/无 NPU 权限）—— SKILL 改向用户询问")
    else:
        lines.append("  未发现 set_env.sh —— CANN toolkit 可能未安装，或安装在非常见路径（用 --set-env 指定）")
    co = d["conda"]
    lines.append("== conda ==")
    lines.append(f"  {'可用 ' + str(co['version']) + ' envs=' + ','.join(co['envs']) if co['available'] else '未安装（SKILL 会问：装 miniconda / 用 venv）'}")
    p = d["prereqs"]
    lines.append("== 系统构建依赖 ==")
    for t, v in p["required"].items():
        lines.append(f"  [必需] {t:6s}: {v or '缺失 ✗'}")
    for t, v in p["optional"].items():
        lines.append(f"  [可选] {t:6s}: {v or '缺失（不阻塞，装上构建更快）'}")
    lines.append("== python 可选解释器 ==")
    for name, info in d["python"].items():
        lines.append(f"  {name:10s}: {info['version']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="探测 CANN 算子仓基础环境（只读）")
    ap.add_argument("--set-env", default=None, help="显式指定 set_env.sh 路径（跳过自动探测）")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    args = ap.parse_args()
    d = detect(args.set_env)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(_human(d))
    # 退出码：CANN 未就绪 → 2（供自动化判断）
    return 0 if d["cann"]["ready"] else 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
