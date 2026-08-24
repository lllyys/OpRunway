"""S2 出口：参数契约必须与 aclnn 签名逐位对得上。

`align_signatures.py` 早就把 aclnn 的入参名、顺序和 ctype 算清楚了，但那份
报告一直只有人眼消费——agent 读完再手抄进 `decl.json` 的 `parameters`。
抄错、抄漏、抄乱顺序，全仓没有一处会说话：

- `make_yaml.py` 的 `check_baseline_binding` 只查**基线侧**，而且是子集判定
  （「每个 YAML 输入都得是基线形参名」），少写一个入参它一句都不说
- `check_adapter_binding.py` 只判适配器该不该写，不看参数集合

于是 median 那轮：签名少了 `dim`（读错了文件），契约跟着少一个入参，基线侧
子集判定合法放行，一路过完 S2 三道门禁、冻结、构建，直到 S3 按声明顺序绑
pyaclnn 才炸。中间每一次「改用例数据再试」都是在错的入参集合上打转。

判据全部从两份数据推导，不问 agent：

- 缺参数：aclnn 声明里有、契约里没有 → 调用少喂，必挂
- 乱顺序：pyaclnn 按**声明顺序**逐个 convert（L0 `binding.pyaclnn_native_conversion`
  = inputs only），相对顺序错了就是静默错绑
- 多参数：契约里有、aclnn 声明里没有，只有 `aclnn_adapter.required` 为 true
  时才合法（适配器会在 init_by_input_data 里丢掉它），否则原样喂给 aclnn

`omitted: true` 的占位参数两边都不进调用（L0 `non_param_dtype_behavior`
= removed_before_call），不参与对齐；`method_inputs` / `tensor_input` 通道
只服务基线对象构造，pyaclnn 不转换，同样不参与。

退出码：0 判定通过；2 对不上或判不了；3 输入读不出来。
"""

import argparse
import json
import sys

from _case_utils import load_json
import _stage_card

# pyaclnn 只转换 inputs 通道（L0 `binding.pyaclnn_native_conversion`）。
# method_inputs / tensor_input 是基线侧对象构造，不进 aclnn 调用。
ACLNN_CHANNEL = "inputs"


class ContractMismatch(ValueError):
    """契约与签名对不上。"""


def contract_input_names(parameters):
    """契约里真正会喂给 aclnn 的输入名，按声明顺序。

    键可以写成 `method_inputs.<name>` 限定通道（`references/yaml-schema.md`），
    没限定就是 inputs。JSON 对象的键序即声明序，`make_yaml.py` 也是照这个序
    往 YAML 的 inputs 里排的。
    """
    names = []
    for key, contract in (parameters or {}).items():
        channel, _, bare = key.rpartition(".")
        channel = channel or (contract or {}).get("channel") or "inputs"
        if channel != ACLNN_CHANNEL:
            continue
        if (contract or {}).get("omitted"):
            continue
        names.append(bare)
    return names


def aclnn_input_names(alignment):
    """aclnn 声明里的入参，按声明顺序，用它期望的 YAML 键名表示。

    `yaml_key` 由 `align_signatures.py` 按位置配对算出（基线形参名优先，
    aclnn 独有的参数用 C 形参名）。它算不出来时这里不放行——位置配对不可信
    的时候，「没发现差异」不等于「没有差异」。
    """
    rows = ((alignment or {}).get("aclnn") or {}).get("inputs")
    if rows is None:
        raise ContractMismatch("签名对齐报告里没有 aclnn.inputs，先重跑 align_signatures.py")
    missing_key = [row.get("c_name") for row in rows if not row.get("yaml_key")]
    if missing_key:
        raise ContractMismatch(
            f"签名对齐没能给出 {missing_key} 的 yaml_key，位置配对不可信。"
            "先按 signature_alignment.json 的 semantic_review 补齐基线形参名或确认出参角色，"
            "不要照当前对齐结果写契约")
    return [row["yaml_key"] for row in rows]


def judge(parameters, alignment):
    """返回 (报告, 问题列表)。问题列表非空即判不过。"""
    expected = aclnn_input_names(alignment)
    actual = contract_input_names(parameters)
    adapter = (alignment.get("aclnn_adapter") or {})

    problems = []
    missing = [name for name in expected if name not in actual]
    if missing:
        problems.append(
            f"契约缺少 aclnn 声明里的入参 {missing}（aclnn 入参：{expected}；契约：{actual}）。"
            "pyaclnn 按声明顺序逐个绑定，少喂一个就是调用不成立——"
            "先确认签名是从待验收算子工程目录里读的，再补契约")

    extra = [name for name in actual if name not in expected]
    if extra and not adapter.get("required"):
        problems.append(
            f"契约多出 aclnn 声明里没有的入参 {extra}，而 aclnn_adapter.required 是 false。"
            "它们会被原样喂给 aclnn；基线独有的参数必须由 aclnn 侧适配器在 "
            "init_by_input_data 里丢掉，否则把它们移出 inputs 通道")

    # 顺序只在两边都有的那些参数之间比，多出来的由适配器丢掉，不占位置。
    paired = [name for name in actual if name in expected]
    ordered = [name for name in expected if name in actual]
    if paired != ordered:
        problems.append(
            f"入参顺序与 aclnn 声明不一致：契约 {paired}，aclnn {ordered}。"
            "pyaclnn 不按名字绑定，顺序错了是静默错绑，跑得出数但比对必错")

    report = {
        "aclnn_inputs": expected,
        "contract_inputs": actual,
        "missing": missing,
        "extra": extra,
        "order_ok": paired == ordered,
        "aclnn_adapter_required": bool(adapter.get("required")),
        "signature_source": alignment.get("signature_source"),
    }
    return report, problems


def main():
    parser = argparse.ArgumentParser(description="参数契约与 aclnn 签名一致性门禁")
    parser.add_argument("-d", "--decl", required=True,
                        help="<op>_decl.json 或 must_cover.json，取其 parameters")
    parser.add_argument("-a", "--alignment", required=True,
                        help="align_signatures.py 产出的 signature_alignment.json")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    _stage_card.announce(__file__)

    try:
        decl = load_json(args.decl)
        alignment = load_json(args.alignment)
    except (OSError, ValueError) as exc:
        print(f"读不出输入：{exc}", file=sys.stderr)
        return 3

    parameters = decl.get("parameters")
    if not parameters:
        print(f"{args.decl} 里没有 parameters", file=sys.stderr)
        return 3

    try:
        report, problems = judge(parameters, alignment)
    except ContractMismatch as exc:
        print(f"判不了：{exc}", file=sys.stderr)
        return 2

    report["verdict"] = "pass" if not problems else "fail"
    report["problems"] = problems
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    if problems:
        for index, text in enumerate(problems, 1):
            print(f"{index}. {text}", file=sys.stderr)
        return 2

    print(f"参数契约与 aclnn 签名一致（{len(report['aclnn_inputs'])} 个入参）"
          f" → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
