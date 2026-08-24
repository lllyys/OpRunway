"""解析 ACLNN 一段式签名，并核对签名来源属于待验收工程。"""

import json
import re

from _aclnn_names import _symbol_prefix


# aclnn 一段式签名末尾这两个参数由 ATK 在 before_call 自动补齐，
# 不出现在用例里，也不该出现在适配器构造的 input_args 中。
ATK_TRAILING = ("uint64_t *", "aclOpExecutor **")


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


def taskdoc_workspace_signature(text):
    """从任务书代码块截出第一段 GetWorkspaceSize 声明。"""
    found = re.search(
        r"\baclnnStatus\s+[A-Za-z_]\w*GetWorkspaceSize\s*\(", text)
    if found is None:
        raise AlignError("任务书 §2.3 解析不出 GetWorkspaceSize 声明")

    depth = 0
    for index in range(found.end() - 1, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[found.start():index + 1].strip()
    raise AlignError("任务书 §2.3 的 GetWorkspaceSize 声明缺少右括号")


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
