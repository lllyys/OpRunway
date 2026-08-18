"""探测 ATK、CANN 和 NPU 环境。

输入：候选设备号。
输出：环境指纹 JSON。
退出码：0 完成。
"""

import argparse
import glob
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys

from _soc_binding import SocBindingError, infer_build_soc
import _stage_card

CANN_HINT = "source <CANN 安装路径>/set_env.sh"

# 运行副本的 skill 根目录。agent 压缩上下文后靠 find 现找，真机上跑到过
# 前一天的陈旧副本；基址是文件系统上的事实，由正在跑的这份脚本自报最准。
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def link_custom_opp(library, evidence_dir):
    """把待验收算子库软链到 opp 外面，返回软链接路径。

    ATK 的签名自检不读传进去的头文件路径，而是按 aclnn_name 在一个目录里
    `grep -r --include=*.h` 取第一个匹配。选目录时 ATK_CUSTOM_OPP_PATH →
    ASCEND_CUSTOM_OPP_PATH → ASCEND_OPP_PATH 逐个覆盖，最后那个由 CANN
    set_env.sh 设置且必然存在，目录就落到装机根，搜到的是官方同名算子的头文件。
    社区算子基本都与官方重名，所以这是必踩，不是偶发。

    库指到 opp 外面的软链接后，最后一个分支的公共路径判断不成立，目录停在
    本轮 vendor。加载走的还是同一个文件，dlopen 会解析软链接。
    详见 references/build-deploy.md#签名自检搜的是磁盘上的同名头文件。
    """
    target = os.path.abspath(os.path.expanduser(library))
    link_dir = os.path.join(evidence_dir, "atk_custom")
    os.makedirs(link_dir, exist_ok=True)
    link = os.path.join(link_dir, os.path.basename(target))
    if os.path.islink(link) or os.path.exists(link):
        os.remove(link)
    os.symlink(target, link)
    return link


def render_env_sh(env, vendor_env=None, custom_opp=None):
    """把环境事实写成一个可 source 的文件。

    每个 Bash 都是新 shell，而 ATK 命令要 CANN 环境、要 vendor 环境、
    要绝对解释器路径。没有载体时 agent 每条命令手工拼一次前缀——
    真机实测一轮拼了 29 次，其中两次漏掉 CANN 环境，同一个错误踩了两遍。

    顺序不能改：CANN 的 set_env.sh 必须在 vendor 的 set_env.bash 之前，
    后者依赖前者设好的 OPP 根目录。

    只写探到的事实。探不到就整行不写——`export ATK_CLI=` 这种空导出会让
    后面的命令拼出空串，报错落在 ATK 身上，排查方向全错。
    """
    lines = ["#!/bin/sh",
             "# 本文件由 scripts/probe_env.py 生成，不要手改。",
             "# 用法：source evidence/env.sh && cd <绝对路径> && <命令>",
             "# S3 装完本轮包后要带 --vendor-env / --custom-opp 重新生成一次。",
             ""]
    lines.append(f"export ATK_SKILL_DIR={shlex.quote(SKILL_ROOT)}")

    scripts = (env.get("cann") or {}).get("set_env") or []
    if scripts:
        lines.append(f". {shlex.quote(scripts[0])}")
    if vendor_env:
        lines.append(f". {shlex.quote(os.path.abspath(vendor_env))}")

    python = env.get("selected_python")
    if python:
        lines.append(f"export ATK_PYTHON={shlex.quote(python)}")
        # 光导出变量不够：agent 手打的命令十有八九是裸 `python scripts/xxx.py`，
        # 而 PATH 里排第一的往往是 conda base 那个没装 atk 的解释器。
        # 真机实测一轮里前 12 条命令全用了裸 python，直到 make_yaml.py 炸掉
        # 才发现跑错了解释器——报错还落在 torch_npu 的告警上，方向全错。
        # 把选中解释器的目录前置到 PATH，裸 python 与 $ATK_PYTHON 就是同一个。
        lines.append(
            f"export PATH={shlex.quote(os.path.dirname(python))}:\"$PATH\"")
    cli = env.get("atk_cli")
    if cli:
        lines.append(f"export ATK_CLI={shlex.quote(cli)}")
    device = (env.get("devices") or {}).get("selected")
    if device is not None:
        lines.append(f"export ATK_DEVICE={device}")
    if custom_opp:
        lines.append(
            f"export ATK_CUSTOM_OPP_PATH={shlex.quote(os.path.abspath(custom_opp))}")
    return "\n".join(lines) + "\n"


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as exc:
        return f"<{type(exc).__name__}>"


def under_cann(cmd, set_env):
    """把命令包进「先 source set_env.sh」的子 shell。

    torch_npu 要 CANN 的 lib64 才导得进来，而本进程未必 source 过。
    不包这一层时，探针只能报「torch_npu 导不进来」，agent 要自己想到
    「先 source 再重跑 probe」——真机上这一步花了十几次调用和一次人工介入。
    """
    if not set_env:
        return list(cmd)
    return ["sh", "-c", f". {shlex.quote(set_env)} >/dev/null 2>&1; exec \"$@\"",
            "sh", *cmd]


def candidate_interpreters():
    seen, out = set(), []
    for cand in [sys.executable, shutil.which("python3"), shutil.which("python")]:
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)
    patterns = ["~/conda/bin/python3", "~/miniconda3/bin/python3", "~/anaconda3/bin/python3",
                "~/*/bin/python3", "/opt/*/bin/python3", "/usr/local/bin/python3*"]
    for pat in patterns:
        for cand in glob.glob(os.path.expanduser(pat)):
            if cand.endswith("-config"):
                continue
            if cand not in seen and os.access(cand, os.X_OK):
                seen.add(cand)
                out.append(cand)
    return out


def inspect(interp, set_env=None):
    probe = (
        "import json,os,shutil,sys;o={'python':sys.version.split()[0]}\n"
        "try:\n import atk;o['atk']=getattr(atk,'__version__',None) or os.path.dirname(atk.__file__)\n"
        "except Exception as e: o['atk']=None\n"
        "p=os.path.join(os.path.dirname(sys.executable),'atk')\n"
        "o['atk_cli']=p if os.path.isfile(p) and os.access(p,os.X_OK) else shutil.which('atk')\n"
        "try:\n import torch;o['torch']=torch.__version__\n"
        "except Exception: o['torch']=None\n"
        "try:\n import torch_npu;o['torch_npu']=torch_npu.__version__\n"
        "except Exception as e: o['torch_npu']=None;o['torch_npu_err']=str(e).split(chr(10))[0][:120]\n"
        # 校验 ATK 自己声明的依赖约束（dist-info 的 Requires-Dist）是否被满足。
        # 不硬编码具体包：ATK 换版本时约束跟着变，硬编码必然漂移。
        # 已知影响：numpy<2.0.0 被违反时 np.Inf/np.NaN 已移除，
        # 非有限值用例会在数据集创建阶段整批失败，看起来却像算子缺陷。
        "o['dep_violations']=[]\n"
        "try:\n"
        " from importlib.metadata import distribution, version as _v\n"
        " from packaging.requirements import Requirement\n"
        " for _r in (distribution('atk').requires or []):\n"
        "  _q=Requirement(_r)\n"
        "  if not _q.specifier: continue\n"
        "  try: _got=_v(_q.name)\n"
        "  except Exception: continue\n"
        "  if not _q.specifier.contains(_got, prereleases=True):\n"
        "   o['dep_violations'].append({'name':_q.name,'required':str(_q.specifier),'installed':_got})\n"
        "except Exception as e: o['dep_violations']=None;o['dep_probe_err']=str(e)[:120]\n"
        # 缺失的依赖以前被 `except: continue` 咽掉，只查版本不查在不在。
        # 真机上 atk 缺 pytz，CLI 一跑就 0 秒退出，探针却全绿——
        # 报错落在「没找到 input.bin」上，排查方向完全错。
        "o['missing_deps']=[]\n"
        "try:\n"
        " from importlib.metadata import distribution, version as _v\n"
        " from packaging.requirements import Requirement\n"
        " for _r in (distribution('atk').requires or []):\n"
        "  _q=Requirement(_r)\n"
        "  if _q.marker and not _q.marker.evaluate(): continue\n"
        "  try: _v(_q.name)\n"
        "  except Exception: o['missing_deps'].append(_q.name)\n"
        "except Exception: o['missing_deps']=None\n"
        "print('PROBE'+json.dumps(o))"
    )
    out = run(under_cann([interp, "-c", probe], set_env))
    for line in out.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[5:])
    return {"python": None, "error": out.splitlines()[-1][:160] if out else "no output"}


def cann_set_env_scripts(preferred=None):
    """列出真实存在的 set_env.sh，指纹里给绝对路径而不是占位符。

    「先 source <CANN 安装路径>/set_env.sh」这句提示，落到零上下文 agent 手里
    还是要自己找一遍：装了几个 CANN 版本、toolkit 是不是软链、哪个才有用。
    路径是文件系统上的事实，探一次记下来就不用再找。

    第一个才是 env.sh 真正 source 的那个，所以「选哪个 CANN」必须有出口。
    真机实测：本机装了两份 CANN，倒序挑中的那份缺 libhccl.so 本体，
    agent 读了三段本脚本源码才推断出「先 source 再跑 probe」这个隐式办法。
    `preferred`（CLI 的 `--cann-root`）把它变成显式参数。
    """
    found, seen = [], set()
    roots = [os.path.abspath(os.path.expanduser(preferred)) if preferred else None,
             os.environ.get("ASCEND_TOOLKIT_HOME"),
             "/usr/local/Ascend/ascend-toolkit/latest",
             "/usr/local/Ascend/ascend-toolkit"]
    # conda 环境自带的 CANN 不在 /usr/local 下，不扫就只能靠 agent 手工先 source
    roots.extend(sorted(glob.glob(os.path.expanduser(
        "~/*/envs/*/Ascend/ascend-toolkit")), reverse=True))
    roots.extend(sorted(glob.glob("/usr/local/Ascend/cann-*"), reverse=True))
    for root in filter(None, roots):
        script = os.path.join(root, "set_env.sh")
        real = os.path.realpath(script)
        if os.path.isfile(script) and real not in seen:
            seen.add(real)
            found.append(script)
    return found


def cann_info(preferred=None):
    home = os.environ.get("ASCEND_TOOLKIT_HOME")
    scripts = cann_set_env_scripts(preferred)
    # env.sh 只 source scripts[0]。本进程当前生效的若不是它，探测结果就
    # 描述不了 env.sh 之后的世界——那正是「探针全绿、跑测全挂」的来源。
    # 所以不一致时由探针自己包一层子 shell 加载 scripts[0] 再探。
    chosen = os.path.dirname(scripts[0]) if scripts else None
    same = bool(home) and bool(chosen) and \
        os.path.realpath(home) == os.path.realpath(chosen)
    info = {"ASCEND_TOOLKIT_HOME": home, "sourced": bool(home),
            "set_env": scripts,
            "preloaded": None if same or not scripts else scripts[0]}
    if chosen and not same:
        home = chosen
    for base in filter(None, [home, "/usr/local/Ascend/ascend-toolkit/latest"]):
        for name in ("version.cfg", "version.info", "ascend_toolkit_install.info"):
            cfg = os.path.join(base, name)
            if os.path.exists(cfg):
                with open(cfg, encoding="utf-8", errors="ignore") as f:
                    info["version"] = f.read().strip().splitlines()[0][:120]
                break
        if "version" in info:
            break
    # 兜底：安装目录名通常带版本（如 cann-9.0.0-beta.1），指纹不能留空
    if "version" not in info and home:
        info["version"] = os.path.basename(os.path.realpath(home))
    return info


def project_provenance(path):
    """待验收算子工程的来源版本。

    复现包的环境指纹现在说得出 CANN 版本、ATK 版本、芯片型号，
    唯独说不出「这次测的是哪一版代码」——而那正是开发者复现时最先要问的。
    """
    if not path:
        return None
    root = os.path.abspath(os.path.expanduser(path))
    info = {"path": root}
    if not os.path.isdir(root):
        info["error"] = "目录不存在"
        return info

    def git(*args):
        out = run(["git", "-C", root, *args], timeout=30)
        return None if out.startswith("<") or "fatal" in out.lower() else out

    info["commit"] = git("rev-parse", "HEAD")
    info["describe"] = git("describe", "--always", "--dirty")
    status = git("status", "--porcelain")
    info["dirty"] = bool(status) if status is not None else None
    if info["commit"] is None:
        info["error"] = "不是 git 工作区，或本机没有可用的 git"
    return info


def device_list():
    out = run(["npu-smi", "info"], timeout=30)
    if out.startswith("<"):
        return {"npu_smi": "不可用", "healthy_candidates": []}

    health = {}
    pattern = re.compile(r"^\|\s*(\d+)\s+\S+\s*\|\s*([A-Za-z_-]+)\s*\|")
    for line in out.splitlines():
        match = pattern.match(line)
        if match:
            health.setdefault(int(match.group(1)), []).append(match.group(2))
    healthy = sorted(device for device, states in health.items()
                     if states and all(state == "OK" for state in states))
    return {
        "npu_smi": "可用" if health else "无可解析设备",
        "health": {str(device): states for device, states in sorted(health.items())},
        "healthy_candidates": healthy,
    }


def atk_cli_smoke(cli, set_env=None):
    """CLI 真跑一次。

    `import atk` 成功不代表 `atk` 命令跑得起来：真机上 atk 缺 pytz，
    包导得进来、命令一调就在 click 里抛 ModuleNotFoundError，
    任务 0 秒「正常结束」，产物为空。把这一跑放在 S0，缺依赖当场暴露。
    """
    if not cli:
        return None
    try:
        done = subprocess.run(under_cann([cli, "case", "--help"], set_env),
                              capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return {"ok": False, "error": f"<{type(exc).__name__}>"}
    if done.returncode == 0:
        return {"ok": True}
    tail = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    return {"ok": False, "returncode": done.returncode, "error": "\n".join(tail[-3:])}


def inspect_device_name(interp, device, set_env=None):
    probe = (
        "import json,torch_npu;"
        f"print('DEVICE'+json.dumps({{'name':torch_npu.npu.get_device_name({device})}}))"
    )
    out = run(under_cann([interp, "-c", probe], set_env), timeout=30)
    for line in out.splitlines():
        if line.startswith("DEVICE"):
            return json.loads(line[6:]).get("name"), None
    error = out.splitlines()[-1][:160] if out else "no output"
    return None, error


def main():
    parser = argparse.ArgumentParser(description="验收环境探测")
    parser.add_argument("--device", type=int, help="用户指定的 device；只记录，不做可用性预检")
    parser.add_argument("-o", "--output")
    parser.add_argument("--env-sh",
                        help="把环境写成可 source 的 shell 文件，如 evidence/env.sh")
    parser.add_argument("--vendor-env",
                        help="S3 安装后本轮 vendor 的 bin/set_env.bash，写进 env.sh")
    parser.add_argument("--custom-opp",
                        help="本轮待验收算子 libcust_opapi.so 的绝对路径，写进 env.sh")
    parser.add_argument("--op-repo",
                        help="待验收算子工程目录，记录其 git 版本供复现包引用")
    parser.add_argument("--cann-root",
                        help="指定本轮 CANN 安装根目录（含 set_env.sh）；"
                             "装了多份 CANN 且默认那份不可用时用它")
    args = parser.parse_args()
    _stage_card.announce(__file__)
    if args.device is not None and args.device < 0:
        parser.error("--device 必须是非负整数")

    if (args.vendor_env or args.custom_opp) and not args.env_sh:
        parser.error("--vendor-env / --custom-opp 只有写进 env.sh 才有意义，"
                     "同时给出 --env-sh")

    if args.cann_root:
        script = os.path.join(os.path.abspath(os.path.expanduser(args.cann_root)),
                              "set_env.sh")
        if not os.path.isfile(script):
            parser.error(f"--cann-root 下没有 set_env.sh：{script}")

    cann = cann_info(args.cann_root)
    preload = cann["preloaded"]
    interps = {i: inspect(i, preload) for i in candidate_interpreters()}
    usable = [i for i, v in interps.items() if v.get("atk")]
    cli_ready = [i for i in usable if interps[i].get("atk_cli")]
    with_npu = [i for i in cli_ready if interps[i].get("torch_npu")]
    selected_python = (with_npu or cli_ready or [None])[0]
    selected_atk_cli = (
        interps[selected_python].get("atk_cli") if selected_python else None
    )

    if args.device is not None:
        devices = {"npu_smi": "未检查", "healthy_candidates": []}
        selected_device = args.device
        selection_source = "user"
    else:
        devices = device_list()
        if devices["healthy_candidates"]:
            selected_device = random.SystemRandom().choice(devices["healthy_candidates"])
            selection_source = "random_healthy_candidate"
        else:
            selected_device = None
            selection_source = "none"
    devices["selected"] = selected_device
    devices["selection_source"] = selection_source
    if selected_python and selected_device is not None and selected_python in with_npu:
        selected_name, name_error = inspect_device_name(
            selected_python, selected_device, preload)
        devices["selected_name"] = selected_name
        if name_error:
            devices["selected_name_error"] = name_error
        if selected_name:
            try:
                devices["build_soc"] = infer_build_soc(selected_name)
            except SocBindingError as exc:
                devices["build_soc_error"] = str(exc)

    env = {
        "interpreters": interps,
        "atk_ready": usable,
        "atk_cli_ready": cli_ready,
        "npu_ready": with_npu,
        "selected_python": selected_python,
        "atk_cli": selected_atk_cli,
        "atk_cli_smoke": atk_cli_smoke(selected_atk_cli, preload),
        "cann": cann,
        "devices": devices,
        "skill_dir": SKILL_ROOT,
    }
    cann_loaded = bool(cann["sourced"] or cann["preloaded"])

    if not cli_ready or (env["atk_cli_smoke"] or {}).get("ok") is False:
        phase = "无"
    elif (with_npu and cann_loaded
          and selected_device is not None and devices.get("build_soc")):
        phase = "Phase A + B"
    else:
        phase = "仅 Phase A"
    env["operator_project"] = project_provenance(args.op_repo)
    # 复现包只需要这几行。整份 env.json 里绝大多数是探测过程，
    # 交付给开发者时要的是「这一轮是在什么上面跑的」。
    env["fingerprint"] = {
        "chip": devices.get("selected_name"),
        "build_soc": devices.get("build_soc"),
        "cann": env["cann"].get("version"),
        "atk": (interps.get(selected_python) or {}).get("atk"),
        "python": selected_python,
        "operator_commit": (env["operator_project"] or {}).get("commit"),
        "operator_dirty": (env["operator_project"] or {}).get("dirty"),
    }
    env["phase_supported"] = phase

    text = json.dumps(env, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    if args.env_sh:
        parent = os.path.dirname(os.path.abspath(args.env_sh))
        os.makedirs(parent, exist_ok=True)
        custom_opp = args.custom_opp
        if custom_opp:
            custom_opp = link_custom_opp(custom_opp, parent)
        with open(args.env_sh, "w", encoding="utf-8") as handle:
            handle.write(render_env_sh(env, args.vendor_env, custom_opp))
    print(text)

    print(f"\n可执行阶段：{phase}", file=sys.stderr)
    if not usable:
        print("  没有解释器装了 atk。装上后重跑，或只产出用例设计文件。", file=sys.stderr)
    elif not cli_ready:
        print("  已找到 atk 包，但没有匹配的 ATK CLI。当前不能生成或执行用例。", file=sys.stderr)
    else:
        print(f"  Python：{selected_python}", file=sys.stderr)
        print(f"  ATK CLI：{selected_atk_cli}", file=sys.stderr)
    smoke = env["atk_cli_smoke"] or {}
    if smoke.get("ok") is False:
        print("  ATK CLI 装着但跑不起来，本轮不能生成或执行用例：", file=sys.stderr)
        for line in str(smoke.get("error", "")).splitlines():
            print(f"    {line}", file=sys.stderr)
        print("  处置：按上面的报错补齐缺失依赖后重跑本脚本。", file=sys.stderr)
    selected_info = interps.get(selected_python, {}) if selected_python else {}
    for name in selected_info.get("missing_deps") or []:
        print(f"  ATK 声明的依赖 {name} 没装；CLI 会在调用时才炸，"
              "且任务日志看起来像正常结束。", file=sys.stderr)
    for item in selected_info.get("dep_violations") or []:
        print(f"  依赖不满足 ATK 声明：{item['name']} 要求 {item['required']}，"
              f"实际 {item['installed']}。", file=sys.stderr)
        if item["name"].lower() == "numpy" and item["installed"].startswith("2"):
            print("  后果：np.Inf / np.NaN 已被移除，nan / inf / -inf 用例会在"
                  "数据集创建阶段整批失败，报错落在 celery_create_dataset，"
                  "看起来像算子缺陷。", file=sys.stderr)
            print("  处置：Phase A 的 freeze_inputs.py 会在物化子进程内补回别名，"
                  "冻结之后的执行期不再生成数据，不受影响。", file=sys.stderr)
    scripts = env["cann"].get("set_env") or []
    hint = f"source {scripts[0]}" if scripts else CANN_HINT
    if env["cann"]["preloaded"]:
        print(f"  本轮 CANN：{env['cann']['preloaded']}（探测时已代为加载）",
              file=sys.stderr)
    if cli_ready and not with_npu:
        print(f"  没有解释器能导入 torch_npu；若确认本机有 NPU，先执行 {hint}", file=sys.stderr)
    elif not cann_loaded:
        print(f"  ASCEND_TOOLKIT_HOME 未设置；执行 ATK CLI 前需 {hint}", file=sys.stderr)
    if len(scripts) > 1:
        print(f"  候选 set_env.sh：{'、'.join(scripts)}", file=sys.stderr)
        print("  env.sh 只加载第一个；要换一份用 --cann-root 指定根目录后重跑。",
              file=sys.stderr)
    if selected_device is None:
        print("  没有取得 device。请让用户显式指定后再进入执行期。", file=sys.stderr)
    elif selection_source == "random_healthy_candidate":
        print(f"  用户未指定 device；本轮随机选择健康候选卡 {selected_device}。", file=sys.stderr)
    else:
        print(f"  本轮使用用户指定的 device {selected_device}。", file=sys.stderr)
    if selected_device is not None and devices.get("selected_name"):
        print(
            f"  设备型号：{devices['selected_name']}；构建 SoC：{devices['build_soc']}。",
            file=sys.stderr,
        )
    elif selected_device is not None and with_npu:
        print("  无法确定所选设备的构建 SoC；禁止进入设备执行期。", file=sys.stderr)

    if args.env_sh:
        print(f"  环境载体：{os.path.abspath(args.env_sh)}", file=sys.stderr)
        print(f"  此后每条命令以 `source {args.env_sh}` 开头，"
              "不要再手工拼环境前缀。", file=sys.stderr)


if __name__ == "__main__":
    main()
