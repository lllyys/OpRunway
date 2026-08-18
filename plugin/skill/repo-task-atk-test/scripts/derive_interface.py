"""从 S1 的接口确认结果派生执行后端，一次定死，后面只读。

以前这件事要 agent 在三处独立声明：S2 的能力检查 --backend、S3 的 manifest
--execution-backend、S4 的 verdict 比对。三处任一处不一致就炸，而 skill 写着
「aclnn → pyaclnn 或 aclnn」，agent 没有依据知道该填哪个——roll 那轮就卡死在这。

它本来就不是选择题：AclnnBackend 要 aclnnTest C++ 扩展并逐算子绑定
（atk/tasks/backends/aclnn_backend.py:71-86），社区算子验收没有这层工程；
ATK 自带的两份 aclnn 精度节点配置写的也都是 pyaclnn + cpu。

interface.json 是每个算子 S1 都必须产出、后面全部只读的唯一文件，
所以随机算子精度策略的确认也钉在这里：--task-doc 命中信号词时，
不给 --random-strategy 就退出码 2，逼着 S1 当场去问用户，而不是靠 agent
自己写探针脚本实证 CPU/NPU 同 seed 是否一致——那是可以预先写进事实的
行业通识（bernoulli 验收那轮探测了两次 ctypes 脚本才拿到的结论），
不需要每个随机算子的验收都重新测一遍。

精度基线不是一个字符串，是三件独立的事：基线函数名是什么、它属于哪一类
（torch 表达式还是 CANN 内置的 C 接口）、它跑在哪个后端。以前后两件被硬编码成
「torch 表达式 + cpu」，用户提出的其它约束——基线跑 npu、基线用内置实现对比——
哪怕落在 ATK 能力域内也没有声明出口，只能被挡住或被 agent 手写探针绕过去。
这里把三者都摊开成可声明字段，默认值保持旧行为，存量算子零影响。

退出码：0 完成；2 输入不成立（模式未开放、待验收算子与基线填成了同一个接口名、缺依据、
随机算子未给出具体精度策略、基线三维度组合不成立）。
"""

import argparse
import json
import re
import sys
from pathlib import Path

from _policy import load_policy
import _stage_card

# 接口模式 → 执行后端。真源是 references/acceptance-policy.json，
# 这里的常量由 tests/test_derive_interface.py 钉住与它一致。
BACKEND_BY_MODE = {
    "aclnn": "pyaclnn",
    "pytorch": "npu",
    "kernel": "kernel",
}

# 基线跑在哪。ATK 的基线节点只是「主节点配置复制一份、改掉 backend 字段」
# （atk/configs/nodes_config.py 的 NodesConfig.init），没有任何地方规定它必须是
# cpu；atk node -b 的 --is_compare 帮助文本原话就有「npu vs npu」的场景。
BASELINE_DEVICES = ("cpu", "npu")
DEFAULT_BASELINE_DEVICE = "cpu"

# 基线是什么。torch 走 eval 作用域；cann_builtin 是 CANN 内置的 C 接口，
# 输出经 pyaclnn 后端的库路径解析拿到（acl_wrapper.get_opp_lib_path 按
# ATK_CUSTOM_OPP_PATH 等环境变量逐级回退，不 source 待验收算子 vendor 就落到内置库）。
BASELINE_KINDS = ("torch", "cann_builtin")
DEFAULT_BASELINE_KIND = "torch"

# CPU 基线节点执行 eval(baseline_api)，只有这些顶层名字可解析——
# atk/tasks/api_execute/function_api.py:17-22 只 import 了它们。
BASELINE_SCOPE_ROOTS = ("torch", "torch_npu", "math", "dist")

# 随机数生成类算子的精度判据。自由文本判不了真假——真机上收到过「按任务书的
# 合理固定种子策略」这种回答，它读起来像个判据，但没有任何一步能照它执行。
# 每一档后面那句是它的成立前提，前提不成立就不许选这一档。
RANDOM_STRATEGIES = {
    "equal_vs_builtin_pinned_seed":
        "与 CANN 内置实现逐位比对。前提：种子是显式入参且在用例数据里钉死",
    "deterministic_boundary_only":
        "只测确定性边界值（如概率取 0 或 1）。前提：任务书认这样的覆盖够用",
    "distribution_test":
        "统计分布检验。前提：判定公式与阈值已经写明",
    "self_consistency":
        "同一台 device 同种子两次跑测自洽。前提：种子是显式入参",
}

# 这两档要求两次跑测拿到同一条随机数流，只有种子是显式入参才做得到。
SEED_REQUIRED_STRATEGIES = ("equal_vs_builtin_pinned_seed", "self_consistency")

RANDOM_SIGNALS_PATH = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "random-operator-signals.json"
)


def load_random_operator_signals(path=None):
    signals_path = Path(path) if path else RANDOM_SIGNALS_PATH
    with signals_path.open(encoding="utf-8") as stream:
        return json.load(stream)


def detect_random_operator(task_doc_text, signals):
    """在任务书正文里找随机数信号词，大小写不敏感。"""
    text_lower = (task_doc_text or "").lower()
    return [kw for kw in signals["signal_keywords"] if kw.lower() in text_lower]


def _sentences(text):
    # 任务书条目常带编号前缀（"4. xxx"），转述判断要按内容比较，
    # 前缀不该让同一句话被误判成"不同句"从而放过原样转述。
    raw = [s.strip() for s in re.split(r"[。！\n]", text or "") if s.strip()]
    return [re.sub(r"^[0-9]+[.、)]\s*", "", s) for s in raw]


def check_random_strategy(matched_keywords, strategy, strategy_source,
                          task_doc_text, ask_template, seed_parameters=(),
                          baseline_kind=DEFAULT_BASELINE_KIND):
    """随机算子必须有受控取值的精度判据，且该取值的成立前提要真的成立。

    早先这里判的是「不许原样转述任务书」，靠字符串比对。那挡不住换个说法的
    转述，也说不出该填什么。改成受控词表：填不进词表的直接把菜单列出来。
    """
    if not matched_keywords:
        return None
    if not (strategy or "").strip():
        return (
            f"任务书命中随机数信号词 {matched_keywords}，但未给出精度对比策略。\n"
            f"  → {ask_template}\n"
            "  → 不要自己写探针脚本实证 CPU/NPU 同 seed 是否一致，先问用户。")
    if not (strategy_source or "").strip():
        return (
            "随机算子精度策略缺依据，必须写明谁确认的、何时确认的"
            "（如「S1 用户裁决 2026-08-16」），否则结论不可追溯。")
    if strategy not in RANDOM_STRATEGIES:
        menu = "\n".join(f"    {name}：{why}"
                         for name, why in RANDOM_STRATEGIES.items())
        return (
            f"随机判据 {strategy!r} 不是受控取值。可选：\n{menu}\n"
            "  → 「按任务书的合理固定种子策略」这类转述判不了真假，选一个具体取值。")
    if strategy == "equal_vs_builtin_pinned_seed" and baseline_kind != "cann_builtin":
        return (
            "随机判据选了 equal_vs_builtin_pinned_seed，基线类别却是 "
            f"{baseline_kind!r}。\n"
            "  → 这一档就是「与 CANN 内置实现逐位比对」，必须同时写 "
            "--baseline-kind cann_builtin。\n"
            "     不写的话比较器判据、冻结冲突判据、裁决前的取证核对全都不触发，\n"
            "     最后盖的结论是「精度达标」，而实际比的是内置——那是假结论。")
    if strategy in SEED_REQUIRED_STRATEGIES and not seed_parameters:
        return (
            f"{strategy} 要两次跑测拿到同一条随机数流，前提是种子是显式入参，\n"
            "    但 --seed-parameters 是空的。\n"
            "  → 读待验收算子工程目录里的头文件，把种子参数名列出来，逗号分隔；\n"
            "     接口里根本没有种子参数时改选 distribution_test 或 "
            "deterministic_boundary_only。")
    return None


def check_baseline_api(baseline, candidate, mode, baseline_kind=DEFAULT_BASELINE_KIND):
    """核对精度基线接口是不是一个真的标杆，不是待验收算子自己要提交的那个接口。

    任务书常同时写「对标接口 aclnnXxx」和「等价于 torch.xxx」，前者说的是
    待验收算子要提交的接口名，后者才是基线。把 aclnnXxx 填成基线有两种下场：
    轻则基线节点在 eval 上 NameError，重则两侧都指向待验收算子实现，跑出一份
    100% 通过的自比报告。

    pytorch 模式下待验收算子与基线本来就是同一个 torch 函数——两侧差在设备
    （npu 与 cpu），不在函数名，这里不能误判成自比。

    cann_builtin 模式下两条检查都不适用，且不适用的理由不同：待验收算子与内置本就是
    同一个公开接口的新旧两版实现（差在装进哪个 vendor 路径，不在接口名），
    这是该模式的前提而非错误；C 接口也不可 eval，拿作用域根名去量它没有意义。
    这条路径由 check_baseline_shape 的 --baseline-source 必填来把关。
    """
    if not (baseline or "").strip():
        return "精度基线接口为空。"
    if baseline_kind == "cann_builtin":
        return None
    if mode != "pytorch" and baseline == candidate:
        return (
            f"精度基线接口 {baseline!r} 与待验收算子要提交的接口名相同。\n"
            "  → 这是拿待验收算子实现给自己当标杆，报告必然全过而毫无意义。"
            "基线要填任务书里的框架接口，如 torch.xxx。")
    root = re.split(r"[.\[(\s]", baseline, maxsplit=1)[0]
    if root not in BASELINE_SCOPE_ROOTS:
        return (
            f"精度基线接口 {baseline!r} 的根名 {root!r} 不在基线执行作用域里。\n"
            f"  → function_api.py 只 import 了 {'、'.join(BASELINE_SCOPE_ROOTS)}，"
            f"基线节点执行 eval({baseline!r}) 会直接 NameError。\n"
            "  → 任务书里的 aclnnXxx 是待验收算子要提交的接口名，填进 --candidate；"
            "基线取任务书同时给出的 torch.xxx。\n"
            "  → 确实要拿 CANN 内置实现当标杆时，显式写 "
            "--baseline-kind cann_builtin 并给出 --baseline-source。")
    return None


def check_baseline_shape(baseline_kind, baseline_device, baseline_source):
    """核对基线三维度的组合成不成立。

    内置实现当标杆是一条要显式声明才走得到的路径：它拿的是「任务书正要修的
    那份实现」当真值，证据强度天然弱于框架基线，所以不做默认、不做兜底，
    必须写明依据（谁要求的、写在哪）才放行。
    """
    if baseline_kind not in BASELINE_KINDS:
        return f"基线类别 {baseline_kind!r} 不在 {BASELINE_KINDS} 里。"
    if baseline_device not in BASELINE_DEVICES:
        return f"基线后端 {baseline_device!r} 不在 {BASELINE_DEVICES} 里。"
    if baseline_kind != "cann_builtin":
        return None
    if not (baseline_source or "").strip():
        return (
            "基线类别为 cann_builtin 却没写 --baseline-source。\n"
            "  → 拿 CANN 内置实现当标杆，等于用任务书正要修的那份代码做真值，"
            "证据强度弱于框架基线。\n"
            "  → 只有任务书或用户明确要求这么比时才用它，并把依据写进 "
            "--baseline-source，否则回到 --baseline-kind torch。")
    if baseline_device != "cpu":
        return (
            f"基线类别 cann_builtin 与基线节点后端 {baseline_device!r} 不相容。\n"
            "  → 别把「内置实现跑在哪」和「判定那轮谁当基线节点」混为一谈。\n"
            "     内置实现确实跑在 npu 上，但那是第一轮、作为 pyaclnn 主节点跑的；\n"
            "     判定发生在第二轮，那一轮的基线节点是从磁盘读真值的 "
            "`node -b cpu --task accuracy_load`。\n"
            "     verdict.py 核的是判定那一轮报告里有没有这个后端的结果，"
            "所以这里只能是 cpu。\n"
            "     两轮拓扑见 references/builtin-baseline.md#两步跑测。")
    return None


def derive(mode, candidate, baseline, mode_source, policy, task_doc_text,
          random_strategy=None, random_strategy_source=None,
          random_signals=None, baseline_kind=DEFAULT_BASELINE_KIND,
          baseline_device=DEFAULT_BASELINE_DEVICE, baseline_source=None,
          seed_parameters=()):
    block = (policy.get("interface_modes") or {}).get(mode)
    if block is None:
        raise ValueError(f"接口模式 {mode!r} 不在验收政策里")
    if not block.get("acceptance_enabled"):
        raise ValueError(f"接口模式 {mode!r} 暂停验收，不出结论")
    problem = check_baseline_shape(baseline_kind, baseline_device, baseline_source)
    if problem:
        raise ValueError(problem)
    problem = check_baseline_api(baseline, candidate, mode, baseline_kind)
    if problem:
        raise ValueError(problem)
    if not (mode_source or "").strip():
        raise ValueError("必须写明接口模式的依据，否则结论不可追溯")

    signals = random_signals if random_signals is not None \
        else load_random_operator_signals()
    matched = detect_random_operator(task_doc_text, signals)
    problem = check_random_strategy(matched, random_strategy,
                                    random_strategy_source, task_doc_text,
                                    signals["ask_template"], seed_parameters,
                                    baseline_kind)
    if problem:
        raise ValueError(problem)

    return {
        "schema_version": 1,
        "interface_mode": mode,
        "candidate_symbol": candidate,
        "baseline_api": baseline,
        "baseline_kind": baseline_kind,
        "baseline_source": (baseline_source or "").strip() or None,
        "execution_backend": BACKEND_BY_MODE[mode],
        "baseline_backend": baseline_device,
        "mode_source": mode_source.strip(),
        "random_operator": {
            "detected": bool(matched),
            "matched_keywords": matched,
            "strategy": random_strategy.strip() if matched else None,
            "strategy_source": random_strategy_source.strip() if matched else None,
        },
        # 种子参数名要落盘：validate_cases.py 的 C7 拿它补词表判不出来的名字
        # （philoxState 之类），核对用例数据里这些参数是不是钉成了同一个常量。
        "seed_parameters": list(seed_parameters or ()),
        # 内置实现自己跑在 npu 上（第一轮，pyaclnn 主节点）。它与
        # baseline_backend 不是一回事：后者说的是判定那一轮的基线节点。
        "builtin_runs_on": "npu" if baseline_kind == "cann_builtin" else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="从 S1 的接口确认结果派生执行后端，产出 evidence/interface.json")
    parser.add_argument("--mode", required=True,
                        help="任务书说待验收算子是什么接口：aclnn / pytorch / kernel")
    parser.add_argument("--seed-parameters",
                        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                        default=[],
                        help="接口声明里的种子参数名，逗号分隔；读工程目录里的头文件得到")
    parser.add_argument("--candidate", required=True, help="待验收算子的接口名，如 aclnnRoll")
    parser.add_argument("--baseline", required=True, help="基线函数名，如 torch.roll")
    parser.add_argument("--baseline-kind", choices=BASELINE_KINDS,
                        default=DEFAULT_BASELINE_KIND,
                        help="基线是什么：torch 表达式，还是 CANN 内置的 C 接口"
                             "（cann_builtin 必须同时给 --baseline-source）")
    # 参数名说的是「判定那一轮谁当基线节点」，不是「基线跑在哪个设备」。
    # 后者读起来会让 cann_builtin 只能填 cpu 变成「内置算子跑在 CPU 上」，
    # 而内置实现确实跑在 NPU 上——那是第一轮 pyaclnn 节点的事。
    # 产物字段一直叫 baseline_backend，这里让参数名向它看齐；
    # 旧名保留为别名，已写好的 repro.sh 与既有证据链不受影响。
    parser.add_argument("--baseline-node-backend", "--baseline-device",
                        dest="baseline_device", choices=BASELINE_DEVICES,
                        default=DEFAULT_BASELINE_DEVICE,
                        help="判定那一轮谁当基线节点的后端名，不是「基线跑在哪个设备」。"
                             "cann_builtin 只能是 cpu：内置实现本身跑在 npu 上"
                             "（第一轮的 pyaclnn 节点），判定发生在第二轮，"
                             "那一轮的基线节点是从磁盘读真值的 "
                             "node -b cpu --task accuracy_load，它不计算任何东西。"
                             "见 builtin-baseline.md#两步跑测")
    parser.add_argument("--baseline-source", default="",
                        help="基线选型的依据，如「任务书 §3 要求与内置实现对比」"
                             "（--baseline-kind cann_builtin 时必填）")
    parser.add_argument("--mode-source", required=True,
                        help="接口模式的依据，如「任务书 §2 + S1 用户确认」")
    parser.add_argument("--task-doc", required=True,
                        help="任务书文件路径，用于机械识别随机数信号词")
    parser.add_argument("--random-strategy", default="",
                        help="随机算子的精度判据，信号词命中时必填。"
                             "取值见 references/random-operator-signals.json "
                             "的 strategies")
    parser.add_argument("--random-strategy-source", default="",
                        help="随机算子精度策略的依据，如「S1 用户裁决 2026-08-16」")
    parser.add_argument("-o", "--output", default="evidence/interface.json")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    try:
        task_doc_text = Path(args.task_doc).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"接口事实不成立：任务书读取失败：{exc}", file=sys.stderr)
        return 2

    try:
        payload = derive(args.mode, args.candidate, args.baseline,
                         args.mode_source, load_policy(), task_doc_text,
                         args.random_strategy, args.random_strategy_source,
                         baseline_kind=args.baseline_kind,
                         baseline_device=args.baseline_device,
                         baseline_source=args.baseline_source,
                         seed_parameters=args.seed_parameters)
    except ValueError as exc:
        print(f"接口事实不成立：{exc}", file=sys.stderr)
        return 2

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"接口模式 {payload['interface_mode']} → 执行后端 "
          f"{payload['execution_backend']}，基线节点 {payload['baseline_backend']}"
          f"（{payload['baseline_kind']}）")
    print(f"写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
