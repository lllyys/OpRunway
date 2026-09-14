"""环境校验 + 芯片型号识别。

校验项（来自用户审视后确定的官方做法）：
  1. python3 -c "import acl;print(acl.get_soc_name())"   ← CANN 安装验证 + 芯片名
  2. npu-smi info                                          ← 卡可见
  3. $ASCEND_HOME_PATH 存在

输出：JSON 到 stdout（供 skill 解析）：
  {"ok": true, "soc_raw": "Ascend910B4", "soc_version": "ascend910b",
   "ascend_home": "...", "npu_smi_lines": 12}
失败时 ok=false + reason 字段。

soc_raw → soc_version（build.sh --soc=<...> 接受的格式）的映射：
  Ascend910B*       → ascend910b
  Ascend910_93      → ascend910_93
  Ascend950         → ascend950
  Ascend310P*       → ascend310p
  其它              → lowercase + 提示用户人工确认
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys


def detect_soc() -> tuple[str, str] | tuple[None, str]:
    """返回 (soc_raw, soc_version) 或 (None, error_msg)。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import acl;print(acl.get_soc_name())"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return None, f"failed to spawn python: {e}"

    if proc.returncode != 0:
        return None, f"acl.get_soc_name() failed (exit={proc.returncode}): {proc.stderr.strip()}"

    soc_raw = proc.stdout.strip()
    if not soc_raw:
        return None, "acl.get_soc_name() returned empty string"
    return soc_raw, _normalize_soc(soc_raw)


def _normalize_soc(raw: str) -> str:
    """根据 build.sh --help 公布的合法值映射。"""
    s = raw.strip()
    # 已知精确映射
    if re.fullmatch(r"Ascend910B\d*", s):
        return "ascend910b"
    if s == "Ascend910_93":
        return "ascend910_93"
    if s == "Ascend950":
        return "ascend950"
    if re.fullmatch(r"Ascend950PR[\d_]*", s):
        return "ascend950"
    if re.fullmatch(r"Ascend310P\d*", s):
        return "ascend310p"
    if re.fullmatch(r"KirinX90\d*", s, re.IGNORECASE):
        return "kirinx90"
    if re.fullmatch(r"Kirin9030\d*", s, re.IGNORECASE):
        return "kirin9030"
    # 兜底：小写
    return s.lower()


def check_npu_smi() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["npu-smi", "info"], capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return False, "npu-smi not found in PATH"
    except Exception as e:
        return False, f"npu-smi error: {e}"

    if proc.returncode != 0:
        return False, f"npu-smi exit={proc.returncode}: {proc.stderr.strip()}"
    lines = proc.stdout.count("\n")
    return True, f"{lines} lines"


def check_ascend_home() -> tuple[bool, str]:
    p = os.environ.get("ASCEND_HOME_PATH")
    if not p:
        return False, "ASCEND_HOME_PATH not set"
    if not os.path.isdir(p):
        return False, f"ASCEND_HOME_PATH={p} not a directory"
    return True, p


def main() -> int:
    soc_result = detect_soc()
    if soc_result[0] is None:
        result = {"ok": False, "reason": "soc_detect_failed", "detail": soc_result[1]}
        print(json.dumps(result, ensure_ascii=False))
        return 1
    soc_raw, soc_version = soc_result

    npu_ok, npu_msg = check_npu_smi()
    if not npu_ok:
        result = {"ok": False, "reason": "npu_smi_failed", "detail": npu_msg,
                  "soc_raw": soc_raw, "soc_version": soc_version}
        print(json.dumps(result, ensure_ascii=False))
        return 2

    home_ok, home_msg = check_ascend_home()
    if not home_ok:
        result = {"ok": False, "reason": "ascend_home_invalid", "detail": home_msg,
                  "soc_raw": soc_raw, "soc_version": soc_version}
        print(json.dumps(result, ensure_ascii=False))
        return 3

    result = {
        "ok": True,
        "soc_raw": soc_raw,
        "soc_version": soc_version,
        "ascend_home": home_msg,
        "npu_smi": npu_msg,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
