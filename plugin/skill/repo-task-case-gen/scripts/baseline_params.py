#!/usr/bin/env python3
"""查 CPU 执行器有没有把 aclnn 的入参悄悄丢掉。

**只在写了执行器时才有意义。** 没写执行器时 ATK 直接
`eval(基线名)(*用例里的输入)` 按序透传（`cpu_backend.py`），结构上漏不掉参数，
这时本模块什么都不查。

写了执行器就不一样：传什么由那段手写代码定。一个 aclnn 会读的入参在执行器里被
解包出来又扔掉，golden 就与 NPU 侧算的不是同一件事，而 S1–S4 四道出口判据
（字段齐备 / 能不能生成 / 够不够 100 条 / 冻成功几条）没有一道会发现——
这是拿真机事故换来的：`UpsampleNearestExact1d/2d` 的执行器丢了 `scales`，
120 条用例里 100 条（2d 是 117 条）的 golden 与 NPU 实测对不上，全程零告警。

**丢参数不一定是错的。** aclnn 有而 torch 没有的参数、torch 自己分配的输出张量，
本来就该丢。所以判据不是「不许丢」，是「丢了要写明理由」：理由写进 `facts.json`
的 `baseline_params`，跟着用例包走，跑测侧精度大面积失败时第一眼就能看到
是基线丢了参数，而不是算子有问题。

```json
"baseline_params": {
  "scales": "torch 侧 size 与 scale_factor 二选一，给了 size 就传不了它"
}
```

看不懂执行器的写法时报「待定」并要求人工确认，**不假装通过**。
"""

import ast
import json
import sys
from pathlib import Path


class Undecidable(Exception):
    """执行器的写法超出静态分析能力，交人工判。"""


def api_type_of(yaml_path):
    """从 YAML 读 api_type。读不出来返回 None。"""
    try:
        import yaml  # noqa: PLC0415  atk 自带，装不上时走下面的兜底
    except ImportError:
        yaml = None
    text = Path(yaml_path).read_text(encoding="utf-8")
    if yaml is not None:
        try:
            loaded = yaml.safe_load(text)
        except Exception:  # noqa: BLE001  YAML 本身的错留给 atk case 去报
            return None
        if isinstance(loaded, dict):
            value = loaded.get("api_type")
            return str(value) if value is not None else None
        return None
    for line in text.splitlines():
        if line.startswith("api_type:"):
            return line.split(":", 1)[1].split("#", 1)[0].strip().strip("'\"") or None
    return None


def find_executor(api_type, root="."):
    """在 function_*.py 里找注册名等于 api_type 的那个文件。

    按注册名找而不是按文件名找：一个工作目录下可能同时有 CPU 与 NPU 两个执行器，
    只有 api_type 指的那个管 CPU 标杆。
    """
    for path in sorted(Path(root).glob("function_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                if _register_name(decorator) == api_type:
                    return path, node
    return None, None


def _register_name(decorator):
    """`@register("function_x_cpu")` -> "function_x_cpu"，别的形状返回 None。"""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "register" or not decorator.args:
        return None
    first = decorator.args[0]
    return first.value if isinstance(first, ast.Constant) else None


def _call_body(class_node):
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__call__":
            return item
    raise Undecidable("执行器类里没有 __call__")


def _is_input_args(node):
    """认 `input_data.args`，不认别的取法。"""
    return (isinstance(node, ast.Attribute) and node.attr == "args"
            and isinstance(node.value, ast.Name) and node.value.id == "input_data")


def _slot_map(call_node):
    """位置 -> 变量名。看不懂就抛 Undecidable。

    认两种写法，`assets/function_*.py` 用的都是第一种：

        self_t, dim, index, value = input_data.args[:4]   # 元组解包
        self_t = input_data.args[0]                       # 逐个取
    """
    slots = {}
    seen_args = False
    for node in ast.walk(call_node):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        value, target = node.value, node.targets[0]

        if _is_input_args(value):
            base = 0
        elif isinstance(value, ast.Subscript) and _is_input_args(value.value):
            index = value.slice
            if isinstance(index, ast.Slice):
                if index.lower is None:
                    base = 0
                elif isinstance(index.lower, ast.Constant):
                    base = index.lower.value
                else:
                    raise Undecidable("input_data.args 的切片下界不是常量")
            elif isinstance(index, ast.Constant) and isinstance(index.value, int):
                if not isinstance(target, ast.Name):
                    raise Undecidable("input_data.args[i] 赋给了非变量")
                seen_args = True
                slots[index.value] = target.id
                continue
            else:
                raise Undecidable("input_data.args 的下标不是常量")
        else:
            continue

        seen_args = True
        if not isinstance(target, ast.Tuple):
            raise Undecidable("input_data.args 整体赋给了一个变量，逐参数追不下去")
        for offset, element in enumerate(target.elts):
            if isinstance(element, ast.Starred):
                raise Undecidable("元组解包里有 *rest，位置对不上参数")
            if not isinstance(element, ast.Name):
                raise Undecidable("元组解包的目标不是简单变量")
            slots[base + offset] = element.id

    if not seen_args:
        raise Undecidable("执行器里没有 input_data.args，取输入的方式不认识")
    return slots


def _loaded_names(call_node):
    """__call__ 里被读过的变量名。解包目标是 Store，不算在内——这正是判据。"""
    return {node.id for node in ast.walk(call_node)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def audit(facts, yaml_path, root="."):
    """返回 (状态, 明细)。

    状态取值：
      `不查`   —— 没写执行器，ATK 按序透传
      `通过`   —— 每个入参都被执行器用上了
      `待丢弃` —— 有入参没用上，明细是 [(参数名, 变量名, 原因)]
      `待定`   —— 执行器的写法静态分析看不懂，明细是一句话
    """
    if (facts.get("accuracy") or {}).get("kind") == "builtin":
        # 内置基线时那个 cpu 节点只为给出输出的 shape 与 dtype，值不参与比对
        # （SKILL.md S4）。`assets/function_bernoulli.py` 就是这个形态：四个入参
        # 只用了 self，是设计如此，不是丢参数。
        return "不查", "builtin"

    api_type = api_type_of(yaml_path)
    if api_type in (None, "function"):
        return "不查", api_type

    path, class_node = find_executor(api_type, root)
    if path is None:
        return "待定", (f"YAML 的 api_type 是 {api_type!r}，"
                        f"但 {root} 下没有哪个 function_*.py 注册了这个名字")

    try:
        call_node = _call_body(class_node)
        slots = _slot_map(call_node)
    except Undecidable as exc:
        return "待定", f"{path.name}：{exc}"

    used = _loaded_names(call_node)
    inputs = [p for p in facts.get("params", []) if p.get("role") == "input"]

    dropped = []
    for index, param in enumerate(inputs):
        name = param.get("name", f"#{index}")
        variable = slots.get(index)
        if variable is None:
            dropped.append((name, "—", "根本没从 input_data.args 里解包出来"))
        elif variable not in used:
            dropped.append((name, variable, "解包了但整个 __call__ 里没再读过"))
    return ("待丢弃" if dropped else "通过"), (dropped or path.name)


def report(facts, yaml_path, root=".", stream=sys.stdout):
    """打印结论。返回 True 表示可以往下走。"""
    state, detail = audit(facts, yaml_path, root)

    if state == "不查":
        why = ("accuracy.kind=builtin，cpu 节点只给形状，值不参与比对"
               if detail == "builtin" else "无 CPU 执行器，ATK 按序透传")
        print(f"基线适配  {why}，不查入参", file=stream)
        return True

    if state == "通过":
        print(f"基线适配  {detail}：aclnn 入参都被用上了", file=stream)
        return True

    if state == "待定":
        print(f"\n基线适配静态检查没结论：{detail}", file=stream)
        print("对着 aclnn 签名逐个参数看一遍执行器，确认没有哪个被丢掉；"
              "确实要丢的写进 facts.json 的 baseline_params。", file=stream)
        print("看完把结论写进报告，本条不拦。", file=stream)
        return True

    declared = facts.get("baseline_params") or {}
    missing = [item for item in detail if not str(declared.get(item[0], "")).strip()]
    for name, variable, why in detail:
        mark = "已声明" if not any(name == m[0] for m in missing) else "**没声明**"
        print(f"基线适配  aclnn 入参 {name}（{variable}）没被基线消费——{why} [{mark}]",
              file=stream)
    if not missing:
        return True

    names = "、".join(item[0] for item in missing)
    print(f"\naclnn 会读 {names}，执行器没读，且 facts.json 的 baseline_params "
          f"里没写理由。两条路选一条：", file=stream)
    print("  1. 这个参数本来就该传 —— 改执行器传上，别丢。golden 与 NPU 侧算的"
          "不是同一件事时，跑测侧看到的是精度大面积失败，查不到基线头上。",
          file=stream)
    print("  2. 确实传不了（torch 侧没有对应参数、或与别的参数互斥）——"
          "在 facts.json 里写明理由：", file=stream)
    print(f'       "baseline_params": {{"{missing[0][0]}": "为什么传不了"}}',
          file=stream)
    print("     理由跟着用例包交给跑测侧，精度失败时第一眼能定位到基线。",
          file=stream)
    return False


def main():
    import argparse  # noqa: PLC0415  只有直接跑本文件调试时才用得上

    parser = argparse.ArgumentParser(description="单独跑一次基线入参消费检查")
    parser.add_argument("yaml")
    parser.add_argument("--facts", default="facts.json")
    args = parser.parse_args()
    with open(args.facts, encoding="utf-8") as handle:
        facts = json.load(handle)
    return 0 if report(facts, args.yaml) else 2


if __name__ == "__main__":
    sys.exit(main())
