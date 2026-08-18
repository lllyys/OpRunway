"""对齐基线接口签名与 aclnn C 签名，产出参数对齐表。

一份用例要同时喂两个接口：基线节点按**参数名**绑定（`eval(name)(**kwargs)`），
待验收算子节点按**声明顺序**绑定（逐个 convert_input_data）。两边对不上的部分就是适配器。

本脚本只负责机械层——参数顺序、ctype、出参位置、ATK 自动补齐项。
这些由两份签名唯一确定，推断只会引入错误。

语义层不在这里判：参数在两边表达方式不同（转置写成 attr 还是写成已转置的
tensor）、dtype 要不要提升、in-place 参数怎么处理，都写进 semantic_review
交给使用者推断。本脚本对拿不准的一律降低 confidence 并列进去，不猜。

退出码：0 完成；2 签名无法解析或基线接口无法解析。
"""

import argparse
import inspect
import json
import re
import sys

from _opapi_binding import _symbol_prefix
from _runtime_guard import runtime_imports
import _stage_card

# aclnn 一段式签名末尾这两个参数由 ATK 在 before_call 自动补齐，
# 不出现在用例里，也不该出现在适配器构造的 input_args 中。
ATK_TRAILING = ("uint64_t *", "aclOpExecutor **")

# 非 const 的张量指针默认判为出参；名字带这些词的提高置信度。
OUTPUT_NAME_HINT = re.compile(r"(?i)(^out|out$|output|result)")
INPLACE_NAME_HINT = re.compile(r"(?i)ref$")

TENSOR_LIKE = {"aclTensor", "aclTensorList"}


class AlignError(ValueError):
    """签名无法可靠解析。"""


def split_params(text):
    """取签名括号内的参数，按顶层逗号切分。"""
    start = text.find("(")
    end = text.rfind(")")
    if start == -1 or end == -1 or end < start:
        raise AlignError("签名里找不到成对的参数括号")
    return [p.strip() for p in text[start + 1:end].split(",") if p.strip()]


def parse_param(raw, position):
    """把 `const aclTensor *self` 拆成类型、指针层数和形参名。"""
    text = raw.strip()
    name = None
    m = re.search(r"([A-Za-z_]\w*)\s*$", text)
    if m and not re.fullmatch(r"(const|unsigned|long|short|int|char|void|bool)", m.group(1)):
        name = m.group(1)
        text = text[:m.start()].strip()
    pointer_depth = text.count("*")
    base = text.replace("*", "").strip()
    is_const = "const" in base.split()
    base_type = " ".join(w for w in base.split() if w != "const")
    if not base_type:
        raise AlignError(f"第 {position + 1} 个参数解析不出类型：{raw!r}")
    return {
        "position": position,
        "c_declaration": raw.strip(),
        "c_name": name,
        "c_type": base_type,
        "pointer_depth": pointer_depth,
        "is_const": is_const,
    }


def is_atk_trailing(param):
    joined = f"{param['c_type']} {'*' * param['pointer_depth']}"
    return joined in ATK_TRAILING


def classify(param):
    """判定参数角色。拿不准时降低 confidence，由 semantic_review 兜住。"""
    if param["c_type"] in TENSOR_LIKE and param["pointer_depth"] >= 1:
        if param["is_const"]:
            return "input", "high", None
        name = param["c_name"] or ""
        if INPLACE_NAME_HINT.search(name):
            return "inplace", "low", "非 const 张量指针且名字以 Ref 结尾，可能是原地写回而非出参"
        if OUTPUT_NAME_HINT.search(name):
            return "output", "high", None
        return "output", "low", "非 const 张量指针但名字看不出是出参，需确认不是原地写回"
    return "input", "high", None


def resolve_ctype(param):
    """用 ATK 自己的映射表解析 C 类型，避免与运行期校验产生分歧。"""
    from atk.tasks.backends.lib_interface.acl_wrapper import CPP_TO_PYTHON_TYPE

    base = CPP_TO_PYTHON_TYPE.get(param["c_type"])
    if base is None and param["pointer_depth"]:
        base = CPP_TO_PYTHON_TYPE.get(param["c_type"] + "*")
        if base is not None:
            return getattr(base, "__name__", str(base))
    if base is None:
        return None
    import ctypes

    for _ in range(param["pointer_depth"]):
        base = ctypes.POINTER(base)
    return getattr(base, "__name__", str(base))


def suggest_yaml(param):
    """给出该参数在 YAML 里的写法建议，使 convert_input_data 转出匹配的 ctype。"""
    from atk.tasks.backends.pyaclnn_backend import PYTYPE_TO_CTYPE
    from atk.tasks.backends.lib_interface.acl_wrapper import CPP_TO_PYTHON_TYPE

    c_type = param["c_type"]
    if c_type == "aclTensor":
        return {"type": "tensor", "dtype": None, "note": None}
    if c_type == "aclScalar":
        return {"type": "scalar", "dtype": None,
                "note": "type 里不能含 attr，否则会走 PYTYPE_TO_CTYPE 转成裸 C 类型"}
    if c_type in ("aclIntArray", "aclFloatArray", "aclBoolArray",
                  "aclTensorList", "aclScalarList"):
        return {"type": "attr", "dtype": None,
                "note": f"值必须是非空 list，create_x_list 按元素类型决定生成 {c_type}；"
                        "传 None 会得到 c_void_p 而不是该类型的空指针"}
    expected = CPP_TO_PYTHON_TYPE.get(c_type)
    if expected is None:
        return {"type": None, "dtype": None, "note": f"CPP_TO_PYTHON_TYPE 没有 {c_type}"}
    matches = [k for k, v in PYTYPE_TO_CTYPE.items() if v is expected]
    dtype = c_type if c_type in matches else (matches[0] if matches else None)
    note = None
    if expected.__name__ == "c_bool":
        dtype = "attr_bool"
        note = "取值必须是 Python bool 或字符串，写 0/1 会被 ATK 拒绝"
    if not matches:
        note = f"PYTYPE_TO_CTYPE 里没有能转出 {expected.__name__} 的 dtype"
    return {"type": "attr", "dtype": dtype, "note": note}


def parse_signature(text):
    params = [parse_param(raw, i) for i, raw in enumerate(split_params(text))]
    trailing = []
    while params and is_atk_trailing(params[-1]):
        trailing.insert(0, params.pop())
    if len(trailing) != 2:
        raise AlignError(
            "签名末尾不是 `uint64_t *workspaceSize, aclOpExecutor **executor`。"
            "确认取的是一段式 GetWorkspaceSize 的声明。")
    m = re.search(r"([A-Za-z_]\w*)\s*\(", text)
    return {
        "symbol": m.group(1) if m else None,
        "parameters": params,
        "trailing": [p["c_declaration"] for p in trailing],
    }


def read_header_signature(header, aclnn_name):
    """按 ATK 运行期同样的方式从头文件抓一段式声明。

    接口名的归一化必须与 `_opapi_binding` 同一份：YAML 的 `aclnn_name`
    按模板写法是不带前缀的 `Roll`，op_api 绑定门禁会补成 `aclnnRoll`，
    这里以前直接拼 `RollGetWorkspaceSize`，同一个字段两处语义不同——
    真机上 `--aclnn-name Roll` 过了绑定门禁、挂在签名对齐上。
    """
    import os

    symbol = f"{_symbol_prefix(aclnn_name)}GetWorkspaceSize"
    pattern = re.compile(rf"aclnnStatus\s+{re.escape(symbol)}\s*\((.*?)\)", re.S)
    roots = [header] if os.path.isfile(header) else []
    if not roots:
        for base, _, files in os.walk(header):
            roots.extend(os.path.join(base, f) for f in files if f.endswith((".h", ".hpp")))
    hits = []
    for path in roots:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for m in pattern.finditer(text):
            hits.append((path, f"aclnnStatus {symbol}({m.group(1)})"))
    if not hits:
        raise AlignError(f"{header} 下找不到 {symbol} 的声明")
    unique = {sig for _, sig in hits}
    if len(unique) > 1:
        raise AlignError(f"{symbol} 有多份不一致的声明：{[p for p, _ in hits]}")
    return hits[0][1], hits[0][0]


def reject_installed_header(header, env_path):
    """`--header` 不能落在 CANN 装机目录下，只能是待验收算子自己工程目录里的头文件。

    真机事故（median，2026-08-16）：装机的 CANN 恰好有同名接口的官方已发布
    实现，S2 顺手从 `/usr/local/Ascend/.../include` 抓了头文件，冻结的是
    官方的签名（4 参、无 dim）。待验收算子自己的头文件当时已经在工程源码树
    里、构建前就能读到（7 参、带 dim），两者同名不同签，签名对齐当场就该
    发现——真正发现却晚到 S3 构建安装完之后，白跑了一整轮构建。
    官方装机版本和待验收算子是两份独立代码，同名不代表同签名。
    """
    import os

    cann_home = None
    if env_path:
        try:
            env = json.loads(open(env_path, encoding="utf-8").read())
            cann_home = (env.get("cann") or {}).get("ASCEND_TOOLKIT_HOME")
        except (OSError, ValueError):
            pass

    resolved = os.path.realpath(os.path.expanduser(header))
    # /usr/local/Ascend 是默认装机根，不管有没有传 --env 都要挡；
    # env.json 记录的 ASCEND_TOOLKIT_HOME 用于装了多份 CANN、装到别处的情形。
    for root in filter(None, [cann_home, "/usr/local/Ascend"]):
        root_resolved = os.path.realpath(os.path.expanduser(root))
        if resolved == root_resolved or resolved.startswith(root_resolved + os.sep):
            raise AlignError(
                f"--header 指向了 CANN 装机头文件（{root_resolved} 下）：{resolved}。"
                "签名对齐只能用待验收算子自己工程目录下的头文件（构建前就在源码树"
                "里，见 evidence/env.json 的 operator_project.path）——装机头文件是"
                "官方已发布版本，可能同名不同签名。")


def require_project_source(path, env_path, flag):
    """签名只能从待验收算子工程目录里的文件读——白名单，不是黑名单。

    黑名单（`reject_installed_header`）只挡得住已知的 CANN 装机根。真机上
    同一台机器还会有别队的 vendor 目录、上一轮的构建产物、`find` 出来的
    同名头文件，任何一处都能给出一份同名不同签的声明，黑名单枚举不完。

    白名单把规则正过来说：能读签名的只有 `evidence/env.json` 的
    `operator_project.path` 这一棵树。CANN 内置实现是**另一份代码**，
    任何情况下都不是本轮的签名来源；工程目录之外的任何路径同理。

    env.json 里没有 operator_project 时不放行：拿不到工程根就无从判断，
    「判不了」不等于「通过」。
    """
    import os

    try:
        env = json.loads(open(env_path, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise AlignError(
            f"读不出 {env_path}：{exc}。签名对齐必须核对签名是从哪个文件读的，先跑 probe_env.py") from exc

    project = (env.get("operator_project") or {}).get("path")
    if not project:
        raise AlignError(
            f"{env_path} 里没有 operator_project.path，无法核对 {flag} 指的文件在不在工程目录里。"
            "重跑 probe_env.py 时带上 --op-repo <待验收算子工程目录>——"
            "签名只能来自这棵树，判不了就不能放行。")

    resolved = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(os.path.expanduser(project))
    if resolved == root or resolved.startswith(root + os.sep):
        # 那个文件得真的存在：指不出具体哪一行代码，就不算说清了签名从哪来。
        if not os.path.exists(resolved):
            raise AlignError(f"{flag} 指向的 {resolved} 不存在")
        return resolved

    raise AlignError(
        f"{flag} 指向的 {resolved} 不在待验收算子工程目录（{root}）下。"
        "签名对齐只认这棵树里的声明：CANN 装机目录、别队 vendor 目录、上一轮"
        "构建产物里都可能有同名接口，它们是另一份代码，同名不代表同签名。"
        "工程里确实没有这个接口的声明时，停下来向用户确认工程路径或接口名，"
        "不要换一个目录去找。")


def baseline_signature(api):
    """按 function_api.py 的作用域解析基线接口，取形参名。"""
    scope = {}
    import math

    with runtime_imports("torch"):
        import torch

    scope.update({"math": math, "torch": torch})
    try:
        import torch.distributed as dist

        scope["dist"] = dist
    except Exception:
        pass
    try:
        import torch_npu

        scope["torch_npu"] = torch_npu
    except Exception:
        pass
    try:
        func = eval(api, scope)  # noqa: S307 — 与 FunctionApi 的绑定方式保持一致
    except Exception as exc:
        raise AlignError(
            f"基线接口 {api!r} 在 function_api.py 的作用域里解析不了：{exc}。"
            "只有 torch / torch_npu / math / dist 可用。") from exc

    # torch 的算子多数是 C 实现，inspect 直接取不到形参名。
    # torch.overrides 的测试替身是纯 Python 的，形参名与 Python 调用约定一致
    # （aten schema 用 self，Python 用 input，而基线走的是 Python 调用）。
    sig, source = None, None
    try:
        sig, source = inspect.signature(func), "inspect"
    except (TypeError, ValueError):
        try:
            from torch.overrides import get_testing_overrides

            shim = get_testing_overrides().get(func)
            if shim is not None:
                sig, source = inspect.signature(shim), "torch.overrides"
        except Exception:
            pass

    overloads = aten_overloads(api)
    if sig is None:
        return {"api": api, "resolved": True, "parameters": None, "source": None,
                "overloads": overloads,
                "note": "inspect 和 torch.overrides 都取不到形参名，需从官方文档确认"}
    params = [
        {"name": p.name, "kind": str(p.kind),
         "required": p.default is inspect.Parameter.empty}
        for p in sig.parameters.values()
        # out= 是 torch 的原地出参写法，不是用例输入
        if p.name != "out"
    ]
    return {"api": api, "resolved": True, "parameters": params,
            "source": source, "overloads": overloads, "note": None}


def builtin_baseline_signature(api):
    """`baseline_kind` 是 `cann_builtin` 时的基线画像。

    这条路上根本没有 torch 基线：真值来自先跑一轮 CANN 内置实现存盘、再由
    `node -b cpu --task accuracy_load` 读回来（references/builtin-baseline.md）。
    基线接口名是一个 C 接口名（`aclnnBernoulli`），`eval` 不了，也没有形参名可反射。

    所以不去解析它，直接声明「没有 Python 形参名」，让 build() 走内置分支：
    YAML 的输入名取 aclnn 自己的形参名，位置配对是恒等的，不存在不可信的问题。
    """
    return {"api": api, "resolved": True, "parameters": None,
            "source": "cann_builtin", "kind": "cann_builtin",
            "overloads": None,
            "note": "CANN 内置实现是 C 接口，没有可反射的 Python 形参名；"
                    "真值走 accuracy_load，不由基线节点算出"}


def aten_overloads(api):
    """列出对应的 aten 重载。多个重载通常意味着要拆接口分面。"""
    import torch

    name = api.split(".")[-1]
    try:
        schemas = torch._C._jit_get_schemas_for_operator(f"aten::{name}")
    except Exception:
        return None
    return [str(s) for s in schemas] or None


def aten_param_names(schema):
    """从 aten schema 取形参名。

    aten 的第一个张量形参叫 self，Python functional API 暴露成 input，
    这是 torch 的固定约定；其余形参名两边一致。实测：
    `torch.median(input=, dim=, keepdim=)` 可调用，
    `torch.median(self=, ...)` 和 `torch.median(keepDim=)` 都报 TypeError。
    """
    start, end = schema.find("("), schema.find(")")
    if start == -1 or end == -1:
        return []
    names = []
    for arg in schema[start + 1:end].split(","):
        arg = arg.strip()
        if not arg or arg == "*":
            continue
        arg = arg.split("=")[0].strip()
        parts = arg.split()
        if len(parts) < 2:
            continue
        names.append(parts[-1])
    return ["input" if i == 0 and n == "self" else n for i, n in enumerate(names)]


def names_from_overloads(baseline, arity):
    """用形参个数匹配的 aten 重载补全形参名，并与已知来源交叉验证。

    只有当两个来源在重叠部分完全一致时才采信，避免拿重载名硬套。
    """
    known = [p["name"] for p in baseline.get("parameters") or []]
    for schema in baseline.get("overloads") or []:
        names = aten_param_names(schema)
        if len(names) != arity:
            continue
        if known and names[:len(known)] != known:
            continue
        return names, schema
    return None, None


def build(baseline, signature):
    parsed = parse_signature(signature)
    rows, outputs = [], []
    for param in parsed["parameters"]:
        role, confidence, why = classify(param)
        entry = dict(param, role=role, confidence=confidence,
                     ctype=resolve_ctype(param), yaml=suggest_yaml(param))
        if why:
            entry["uncertain"] = why
        (outputs if role in ("output", "inplace") else rows).append(entry)

    review = [{"parameter": e["c_name"], "issue": e["uncertain"]}
              for e in outputs + rows if e.get("uncertain")]

    # 出参判定不可靠时，入参的位置配对就是错的——宁可不给，也不给错的对齐。
    reliable = all(e["confidence"] == "high" for e in outputs)
    if not reliable:
        review.append({
            "parameter": None,
            "issue": "出参角色未能确定，下面的入参位置配对不可信；"
                     "先确认出参再重跑，不要照着当前 yaml_key 写 YAML",
        })

    # 内置真值这条路没有 torch 基线，下面所有「拿基线形参名去配对」的判定都不适用。
    builtin = baseline.get("kind") == "cann_builtin"
    base_params = baseline.get("parameters") if reliable else None
    variadic = []
    if builtin and reliable:
        # YAML 的输入名就是 aclnn 自己的形参名，位置配对是恒等的。
        for row in rows:
            row["baseline_name"] = None
            row["yaml_key"] = row["c_name"]
    if base_params is not None:
        variadic = [p["name"] for p in base_params if p["kind"].endswith(("VAR_POSITIONAL",
                                                                         "VAR_KEYWORD"))]
        base_params = [p for p in base_params if p["name"] not in variadic]
        # aclnn 侧按顺序绑定，所以按位置配对；名字对不上是常态（input vs self）。
        for i, row in enumerate(rows):
            row["baseline_name"] = base_params[i]["name"] if i < len(base_params) else None
            row["yaml_key"] = row["baseline_name"] or row["c_name"]

    # 两边总得有一边补差量。分开判定，因为它们的成因不同。
    aclnn_reasons, baseline_reasons = [], []
    tail = len(parsed["parameters"]) - len(outputs)
    if outputs and any(o["position"] < tail for o in outputs):
        aclnn_reasons.append(
            f"出参在第 {[o['position'] for o in outputs]} 位，不在末尾；"
            "默认实现只会 extend 到末尾，必须在适配器里 insert 到正确位置")
    for row in rows:
        if row["yaml"].get("type") is None:
            aclnn_reasons.append(
                f"{row['c_name']} 的 C 类型 {row['c_type']} 没有对应的 YAML 写法，"
                "只能在适配器里手工构造")

    # 是否会有用例把可空指针置空，取决于用例设计，签名里看不出来，所以只能问。
    for row in rows:
        if row["c_type"].startswith("acl") and row["c_type"] != "aclTensor":
            review.append({
                "parameter": row["c_name"],
                "issue": f"{row['c_type']} 是可空指针。若有用例把它置空，"
                         f"默认路径会绑成 c_void_p 而不是 {row['ctype']}，"
                         "届时需要适配器构造 typed 空指针；全部用例都给值则不需要",
            })

    if builtin:
        # CPU 节点在两轮里都要真跑一次（第一轮供出参形状与 dtype，第二轮 ATK
        # 建 aclnn 任务时仍要靠它拿形状），而 aclnn 的入参名喂给任何 torch
        # 函数都是 unexpected keyword argument。所以这个插件是必需品，不是可选项。
        baseline_reasons.append(
            "基线是 CANN 内置实现，没有可调用的 torch 函数；CPU 节点只负责供出参的"
            "形状与 dtype，必须写 function_<op>.py 接住 aclnn 的入参名并返回等形状、"
            "等 dtype 的张量（数值不参与比对，见 references/builtin-baseline.md#两步跑测）")

    if not builtin and baseline.get("overloads") and len(baseline["overloads"]) > 1:
        review.append({
            "parameter": None,
            "issue": f"基线接口有 {len(baseline['overloads'])} 个 aten 重载，"
                     "返回结构不同的重载要拆成独立接口分面：" + "；".join(baseline["overloads"]),
        })

    # torch.overrides 的测试替身既可能少列可选形参（torch.median.dim 少了
    # keepdim），也可能多列出别的重载混进来的形参（torch.median 全张量分面的
    # aclnn 侧只有 1 个入参，替身却列了 2 个，多出来的 dim 其实是 median.dim
    # 重载的）。两种情形都会让下面的差量判定假阳性，所以个数对不上就不下结论，
    # 一律先按 aten 重载交叉验证。
    shimmed = baseline.get("source") == "torch.overrides"
    if base_params is not None and shimmed and len(rows) != len(base_params):
        names, schema = names_from_overloads(baseline, len(rows))
        if names:
            base_params = [{"name": n, "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                           for n in names]
            baseline["source"] = "torch.overrides + aten schema"
            baseline["schema_used"] = schema
            for i, row in enumerate(rows):
                row["baseline_name"] = names[i]
                row["yaml_key"] = names[i]
        else:
            review.append({
                "parameter": None,
                "issue": f"基线形参名取自 torch.overrides 的测试替身，只列到 "
                         f"{[p['name'] for p in base_params]}，而 aclnn 有 {len(rows)} 个入参，"
                         "也没有形参个数匹配的 aten 重载可以交叉验证。"
                         "多出来的部分需对照官方文档确认后再补 yaml_key",
            })
            base_params = None
            for row in rows:
                row.pop("baseline_name", None)
                row.pop("yaml_key", None)

    if base_params is not None:
        # aclnn 独有：YAML 必须带上它（aclnn 要），但 eval(基线)(**kwargs) 不认这个键。
        for row in rows[len(base_params):]:
            baseline_reasons.append(
                f"{row['c_name']} 是 aclnn 独有的参数，基线接口没有这个形参，"
                "默认基线路径会报 unexpected keyword argument")
            review.append({
                "parameter": row["c_name"],
                "issue": f"aclnn 独有（C 类型 {row['c_type']}），基线没有对应形参；"
                         "确认它的取值语义，以及基线侧如何等价表达",
            })
        # 基线独有：YAML 必须带上它（基线要），但喂给 aclnn 前要丢掉。
        for extra in base_params[len(rows):]:
            aclnn_reasons.append(
                f"{extra['name']} 是基线独有的参数，aclnn 签名里没有，"
                "喂给 aclnn 前必须从 kwargs 里 pop 掉")
            review.append({
                "parameter": extra["name"],
                "issue": "基线独有，aclnn 签名里没有；确认它的语义在 aclnn 侧由什么表达"
                         "（例如 transposeA 对应的是「传进来的已经是转置后的 tensor」）",
            })
        for row in rows:
            if row["baseline_name"] and row["baseline_name"] != row["c_name"]:
                review.append({
                    "parameter": row["c_name"],
                    "issue": f"按位置配到基线的 {row['baseline_name']}。名字不同是常态，"
                             "但要确认两边的形参顺序真的一致，否则位置配对就是错的",
                })
        if variadic:
            review.append({"parameter": None,
                           "issue": f"基线签名含变长参数 {variadic}，位置配对不可靠，需人工核对"})
    elif not builtin and baseline.get("parameters") is None:
        review.append({"parameter": None, "issue": "取不到基线形参名，需人工从文档确认"})

    # 差量判定依赖基线形参名。取不到时"没找到理由"不等于"不需要适配器"，
    # 只有 required=true 在任何情况下都成立，required=false 必须有 determinable 背书。
    # 内置真值这条路例外：它的位置配对是恒等的（YAML 输入名就是 aclnn 形参名），
    # 判得了，不是判不了。
    determinable = base_params is not None or bool(builtin and reliable)
    return {
        "baseline": baseline,
        "aclnn": {"symbol": parsed["symbol"], "trailing": parsed["trailing"],
                  "inputs": rows, "outputs": outputs},
        "aclnn_adapter": {"required": bool(aclnn_reasons), "reasons": aclnn_reasons,
                          "determinable": determinable or bool(aclnn_reasons)},
        "baseline_adapter": {"required": bool(baseline_reasons), "reasons": baseline_reasons,
                             "determinable": determinable},
        "semantic_review": review,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, help="YAML 的 name 字段，如 torch.median")
    ap.add_argument("--signature", help="一段式 GetWorkspaceSize 的完整 C 声明；"
                                        "必须与 --signature-source 一起给")
    ap.add_argument("--signature-source",
                    help="--signature 抄自哪个文件；必须是待验收算子工程目录下的路径")
    ap.add_argument("--header", help="待验收算子工程目录下的头文件路径或目录，与 --aclnn-name 配合")
    ap.add_argument("--aclnn-name", help="YAML 的 aclnn_name 字段")
    ap.add_argument("--env", required=True,
                    help="evidence/env.json 路径；据此核对签名是从待验收算子工程目录里读的")
    ap.add_argument("--interface",
                    help="derive_interface.py 的产物；读 baseline_kind。"
                         "cann_builtin 时基线是 C 接口名、没有可反射的形参名，"
                         "对齐改走内置分支（YAML 输入名取 aclnn 自己的形参名）")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    _stage_card.announce(__file__)

    baseline_kind = "torch"
    if args.interface:
        try:
            with open(args.interface, encoding="utf-8") as handle:
                baseline_kind = json.load(handle).get("baseline_kind", "torch")
        except (OSError, ValueError) as exc:
            print(f"读不出 --interface：{exc}", file=sys.stderr)
            return 3

    try:
        signature = args.signature
        if signature:
            # 手抄的声明也要说得出抄自哪一行代码：核对不了从哪抄的，就等于没核对。
            if not args.signature_source:
                raise AlignError(
                    "--signature 必须同时给 --signature-source（抄自待验收算子工程里的哪个文件）")
            source = require_project_source(
                args.signature_source, args.env, "--signature-source")
        else:
            if not (args.header and args.aclnn_name):
                raise AlignError("需要 --signature 加 --signature-source，或 --header 加 --aclnn-name")
            reject_installed_header(args.header, args.env)
            require_project_source(args.header, args.env, "--header")
            signature, source = read_header_signature(args.header, args.aclnn_name)
        profile = (builtin_baseline_signature(args.baseline)
                   if baseline_kind == "cann_builtin"
                   else baseline_signature(args.baseline))
        report = build(profile, signature)
    except AlignError as exc:
        print(f"对齐失败：{exc}", file=sys.stderr)
        return 2

    report["signature_source"] = source
    report["signature"] = signature
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"基线 {args.baseline} / aclnn {report['aclnn']['symbol']}")
    print(f"入参 {len(report['aclnn']['inputs'])} 个，出参 {len(report['aclnn']['outputs'])} 个")
    for key, label, default in (
            ("aclnn_adapter", "aclnn 侧适配器", "不需要，默认 aclnn_function 即可"),
            ("baseline_adapter", "基线侧适配器", "不需要，默认 function 即可")):
        block = report[key]
        if block["required"]:
            verdict = "需要"
        elif block["determinable"]:
            verdict = default
        else:
            verdict = "无法判定——基线形参名没取全，见 semantic_review，不要当作不需要"
        print(f"{label}：{verdict}")
        for reason in block["reasons"]:
            print(f"  - {reason}")
    if report["semantic_review"]:
        print("需要语义判断：")
        for item in report["semantic_review"]:
            print(f"  - {item['parameter']}：{item['issue']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
