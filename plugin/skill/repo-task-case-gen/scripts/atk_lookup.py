"""查 ATK 能力域：一条命令换一个事实，替代读源码。

零上下文 agent 遇到「这个键能填吗」「它到底怎么算」时，唯一合规的动作应该是
查一次，而不是去 grep ATK 源码。源码探索的代价在真机上反复出现：
median 约 20 次、roll 27 次，且读完的结论没人接住，下一轮从头再来。

数据来自 `references/atk-parameter-capabilities.json`，由
`probe_atk_capabilities.py` 从装机 ATK 反射与实测产出，不是手抄。

退出码：0 查到；2 查不到（会列出最接近的键）。
"""

import argparse
import json
import sys
from pathlib import Path

CAPABILITIES = Path(__file__).resolve().parents[1] / "references" / \
    "atk-parameter-capabilities.json"

# 问题 → 到哪一段找。写成「agent 会怎么问」，不是「JSON 怎么组织」。
TOPICS = {
    "backend": ("registries", "backend", "YAML/CLI 能用的执行后端"),
    "api_type": ("registries", "api_type", "YAML 的 <前缀>api_type 能填什么"),
    "comparator": ("registries", "comparator", "standard.acc 能填什么"),
    "run_mode": ("registries", "run_mode", "执行模式开关"),
    "generator": ("registries", "generator", "内置生成器注册名"),
    "parameter_type": ("registries", "parameter_type", "YAML 输入的 type"),
    "dtype": ("registries", "dtype", "dtype 令牌全集"),
    "comparator_semantics": ("semantics", "comparator", "比较器实测行为"),
    "data_generation": ("semantics", "data_generation", "取值分布实测行为"),
    "group_types": (None, "group_types", "复合容器与运行时语义的对应"),
    "group_length": (None, "group_length", "复合组的长度从哪来、每条用例怎么定"),
    "pyaclnn": (None, "pyaclnn", "pyaclnn 后端的容器与 dtype 约束"),
    "binding": (None, "binding", "inputs / method_inputs / tensor_input 各绑到哪"),
    "backends": (None, "backends", "各后端的验证状态与绑定方式"),
    "backend_selection": (None, "backend_selection", "接口模式 → 执行后端，唯一推导不是选择"),
    "data_types": (None, "data_generation", "可用的 tensor / attr dtype"),
}


def load():
    if not CAPABILITIES.exists():
        print(f"✗ 找不到能力矩阵 {CAPABILITIES}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(CAPABILITIES.read_text(encoding="utf-8"))


def resolve(data, topic):
    section, key, _ = TOPICS[topic]
    node = data.get(section, data) if section else data
    return node.get(key)


def main():
    parser = argparse.ArgumentParser(
        description="查 ATK 能力域事实，替代读 ATK 源码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="可查的主题：\n" + "\n".join(
            f"  {name:<22}{TOPICS[name][2]}" for name in sorted(TOPICS)))
    parser.add_argument("topic", nargs="?", help="主题名，省略则列出全部主题")
    parser.add_argument("-k", "--key", help="只取该主题下的某个键")
    parser.add_argument("--json", action="store_true", help="原样输出 JSON")
    args = parser.parse_args()

    data = load()
    version = data.get("capability_id") or data.get("probe", {}).get("probed_atk_version")

    if not args.topic:
        print(f"能力矩阵 {version}，可查主题：")
        for name in sorted(TOPICS):
            print(f"  {name:<22}{TOPICS[name][2]}")
        print("\n语义类主题（comparator_semantics / data_generation）记的是实测行为，"
              "不是键的清单。")
        return 0

    if args.topic not in TOPICS:
        close = [name for name in sorted(TOPICS) if args.topic in name or name in args.topic]
        print(f"✗ 没有主题 {args.topic!r}。" +
              (f"最接近的：{close}" if close else "用不带参数的调用列出全部主题。"),
              file=sys.stderr)
        return 2

    value = resolve(data, args.topic)
    if value is None:
        print(f"✗ 能力矩阵里没有 {args.topic}；"
              "先跑 probe_atk_capabilities.py 重探，再把结果并进矩阵。", file=sys.stderr)
        return 2

    if args.key is not None:
        if not isinstance(value, dict) or args.key not in value:
            keys = sorted(value) if isinstance(value, dict) else "（该主题是列表，没有键）"
            print(f"✗ {args.topic} 下没有 {args.key!r}。可用：{keys}", file=sys.stderr)
            return 2
        value = value[args.key]

    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=1))
    elif isinstance(value, list):
        print(f"{args.topic}（{version}）共 {len(value)} 项：")
        for item in value:
            print(f"  {item}")
    elif isinstance(value, dict):
        print(f"{args.topic}（{version}）：")
        for name in sorted(value):
            print(f"  {name} = {json.dumps(value[name], ensure_ascii=False)}")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
