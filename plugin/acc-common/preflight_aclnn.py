#!/usr/bin/env python3
"""ACLNN ABI 的纯静态 CP-C0 预检（aclnn_py / cpp_extension 共用）。

只用 CP-A/B 已取到的接口头与 spec 做签名/slots 对账；不 clone、不 build、不加载
`.so`、不访问 NPU。成功状态只能是 ``READY_WAIT_NPU_TRUST_GATE``，不能替代后续真机
build、DUT 定义方核验或 harness trust gate。

来源身份（声明形态 × 实得形态、provenance 锚、降级挂账）一律由
:mod:`source_provenance` 一处解释，本模块不自建「kind → 锚字段名」的映射，也不重复
做锚等值对账（`source_provenance.bind` 已做且更严）。

签名解析统一走 :func:`aclnn_runner.parse_aclnn_signature`，因此本门是 **stage2 感知**的
（runner 改动⑮）。产物里逐签名/逐变体记两组来源，让两件本来不可见的事在产物里可审：

1. ``stage2_dispatch_form`` / ``stage2_call_arity`` —— 执行段 ``aclnn<Op>`` 若是
   「框架三参 + stage1 实参原样重复 + stream」形态（实测 ``aclnnGaussianBlur`` 为 10 参），
   ``slot_contract`` 里的 N 项与真机 native 调用的实参个数**本来就不相等**。不记这两项，
   读产物的人只能默认它是标准 4 参调用；
2. 逐形参 ``direction_source`` —— ``dst`` 这类 stage1 写 ``const aclTensor *``、stage2 写
   ``aclTensor *`` 的 DUT 上，「它是输出」这个结论**完全来自 stage2**。不落来源，产物里
   就只剩一个凭空的 ``role="out"``（AGENTS.md 5.8：推断项须显式标注出处）。

两组都只是**记录**：对账强度仍由 :meth:`AclnnRunner._validate_slots_against_signature`
一处解释，本门不因 stage2 形态放宽任何一条逐项校验。
"""

import argparse
import hashlib
import json
import os
import sys

import content_address
import gen_cases
import precision_policy
import repo_adapter
import source_provenance
from aclnn_runtime.aclnn_runner import (
    STAGE2_EXTENDED,
    STAGE2_STANDARD,
    AclnnRunner,
    AclnnRunnerError,
    parse_aclnn_signature,
)


_SOURCE_DOMAIN = "oprunway/source-facts/v1"
_PREFLIGHT_DOMAIN = "oprunway/aclnn-preflight/v1"


def _strict_json(path):
    with open(path, "r", encoding="utf-8") as src:
        value = json.load(
            src,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非法 JSON 常量: {token}")),
        )
    content_address.canonical_json_bytes(value)
    return value


def _sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _self_sha256():
    with open(__file__, "rb") as src:
        return hashlib.sha256(src.read()).hexdigest()


def _stage2_record(signature):
    """签名的 stage2 派发形态 + **真机 native 实参个数**（只记录，不参与任何判定）。

    ``stage2_form`` 照抄解析结果（``absent`` = header 里没有执行段声明，``None`` = 调用方没声明）；
    ``stage2_dispatch_form`` 是 :meth:`AclnnRunner.run` 实际会走的分支——**不可派发的形态记 ``None``**，
    绝不写成 ``STAGE2_STANDARD``（那等于在收据里替 runner 编了一个它根本不会走的分支）；
    ``stage2_call_arity`` = 那次 native 调用的实参个数：standard 恒 4；extended 是
    「框架三参 + stage1 实参原样重复 + stream」= ``3 + len(params) + 1``；不可派发时为 ``None``。
    """
    form = getattr(signature, "stage2_form", None)
    params = list(getattr(signature, "params", None) or ())
    dispatch = form if form in (STAGE2_STANDARD, STAGE2_EXTENDED) else None
    if dispatch == STAGE2_EXTENDED:
        arity = 3 + len(params) + 1
    elif dispatch == STAGE2_STANDARD:
        arity = 4
    else:
        arity = None
    return {
        "stage2_form": form,
        "stage2_dispatch_form": dispatch,
        "stage2_call_arity": arity,
    }


def _param_records(signature):
    """签名形参表 → 产物记录，逐项补 ``direction_source``（in/out 这个结论**是从哪来的**）。

    - ``stage2_param_qualifier``：extended 形态下，方向取自 stage2 那份重复实参的 const 限定符；
    - ``stage1_const_heuristic``：stage2 缺席或为 standard，方向取自 stage1 的 ``const`` 启发式；
    - ``not_applicable``：非张量形参（属性）没有方向可言。

    按 ``ctype`` 分（通用类型判据），**绝不按算子身份**。
    """
    from_stage2 = getattr(signature, "stage2_form", None) == STAGE2_EXTENDED
    records = []
    for param in getattr(signature, "params", None) or ():
        record = dict(param)
        record["direction_source"] = (
            ("stage2_param_qualifier" if from_stage2 else "stage1_const_heuristic")
            if param.get("ctype") == "tensor" else "not_applicable")
        records.append(record)
    return records


def _abstract_slots(spec, variant):
    active_attrs = set(variant["active_attrs"])
    active_outputs = set(variant["active_outputs"])
    slots = []
    params = spec.get("params")
    if not isinstance(params, list):
        raise ValueError("spec.params 须为 JSON array")
    for index, param in enumerate(params):
        if not isinstance(param, dict):
            raise ValueError(
                f"spec.params[{index}] 须为 JSON object，得 {type(param).__name__}")
        role, name = param.get("io"), param.get("name")
        if role == "in":
            slots.append({"kind": "in", "name": name})
        elif role == "attr":
            if name in active_attrs:
                slots.append({
                    "kind": "attr",
                    "name": name,
                    "ctype": gen_cases._attr_ctype(param),
                })
        elif role == "out":
            slots.append({
                "kind": "out" if name in active_outputs else "out_null",
                "name": name,
            })
        else:
            raise ValueError(
                f"spec param {name!r} 的 io={role!r} 非 in/attr/out")
    return slots


def evaluate(root, spec_rel, pr_facts_rel="pr_facts.json",
             source_rel="source_facts.json"):
    """返回静态预检 payload；异常全部收敛成机读 ``BLOCKED``。"""
    root = os.path.abspath(root)
    result = {
        "schema": "oprunway.aclnn_preflight",
        "schema_version": 1,
        "status": "BLOCKED",
        "scope": "static-only",
        "acceptance_verdict": None,
        "required_next_gate": "NPU_BUILD_AND_HARNESS_TRUST_GATE",
        "bindings": {},
        # 源 provenance 的机读**降级**挂账。只装「本该做到却没做到」的事（如声明要测 PR
        # 却只拿到一份本地快照）；正常通路恒为空表。空表与「工具没记」是两回事，故恒存在。
        "provenance_degradations": [],
        # 源 provenance 的机读**中性形态事实**。如「本地源码没有上游 commit」——它不是降级，
        # 是这条输入形态本来的样子。与上一项分开记，报告才不会把正常的本地源码验收读成异常。
        "provenance_form_facts": [],
        "signatures": [],
        "variants": [],
        "blocked_reasons": [],
        "producer": {
            "tool": "preflight_aclnn.py",
            "logic_sha256": _self_sha256(),
        },
    }
    try:
        source = content_address.read_artifact(
            root, source_rel, _SOURCE_DOMAIN)
        if not isinstance(source, dict):
            raise ValueError("source_facts payload 须为 JSON object")
        source_digest = content_address.content_digest(_SOURCE_DOMAIN, source)
        result["bindings"]["source_facts_digest"] = source_digest

        spec = _strict_json(content_address.safe_path(root, spec_rel))
        pr_facts = _strict_json(content_address.safe_path(root, pr_facts_rel))
        if not isinstance(spec, dict) or not isinstance(pr_facts, dict):
            raise ValueError("spec/pr_facts 须为 JSON object")
        result["bindings"]["spec_sha256"] = _sha(spec)
        # 源身份绑定（含档位）统一由 source_provenance 一处解释：判据是「**实得形态是否与
        # 声明的输入形态一致**」——`git_pr`/`local_source` 两条声明如愿实得时都无条件放行；
        # 只有「声明要测 PR 却只拿到本地快照」才要编排层显式授权，并把降级机读挂账进收据。
        # ⚠ 这段不能挪到下面 runner_form 早退之后：`cpp` 的 NOT_APPLICABLE payload 历来就带
        #   来源绑定键，挪走会让它凭空少一个键。
        # ⚠ 锚只能经 `source_provenance.bind` 取，不许自己按字段名去翻：本地事实里合法地
        #   存在「这份快照来自哪个 commit」一类的信息字段，它**不是锚**；任何「哪个字段有值
        #   用哪个」的兜底都会把它当成 PR provenance 用。同理 64 位 merkle 绝不能写进
        #   `pr_head_sha`——下游是按键名认通路的。
        provenance_bindings, degradations = source_provenance.bind(source, pr_facts)
        result["bindings"].update(provenance_bindings)
        result["provenance_degradations"] = degradations
        result["provenance_form_facts"] = source_provenance.form_facts(
            provenance_bindings)

        # 缺省口径经全仓唯一真源（P5）；本模块曾写死 `"cpp"` → spec 省略该键时这里判 NOT_APPLICABLE、
        # run_workflow 却按 cpp_extension 去跑，等于**静默跳过**了本应做的 ABI 预检。
        runner_form = repo_adapter.spec_runner_form(spec)
        # ⚠ 早退**只认精确的 `"cpp"`**，不许写成「不是那两支就早退」（2026-08-05 审修门 High#2）。
        #   原写法是 `if runner_form not in ("aclnn_py", "cpp_extension"): NOT_APPLICABLE`——
        #   `null` / `""` / `0` / `"opaque"` 这些**写坏的 spec** 会一并落进 NOT_APPLICABLE，
        #   而 CLI 对该状态返回 0：一份根本没声明合法形态的 spec，收到的是「这道门不适用」。
        #   门看着有、其实拦不住，正是本仓最贵的那类缺陷。
        #   NOT_APPLICABLE 的语义必须窄到「**已知**不需要 ACLNN ABI 预检的那一支」：只有 `cpp`
        #   （per-op C++ runner，不走标准 aclnn 两段式）符合。
        if runner_form == "cpp":
            result["status"] = "NOT_APPLICABLE"
            result["blocked_reasons"] = []
            return result
        if runner_form not in ("aclnn_py", "cpp_extension"):
            # 词表外一律 BLOCKED（本 except 收敛 ValueError → status 保持 BLOCKED、CLI 退 2）。
            # 受控词表以 `repo_adapter` 的能力表 key 为准，本模块不自建第二份。
            raise ValueError(
                f"spec.runner_form={runner_form!r} 不在受控词表 "
                f"{sorted(repo_adapter.SUPPORTED_NP_BY_FORM)} 内——"
                f"写坏的 spec 不得被判成「不需要本预检」，fail-closed")
        result["bindings"]["runner_form"] = runner_form
        result["required_next_gate"] = (
            "CPP_EXTENSION_BUILD_LOAD_AND_HARNESS_TRUST_GATE"
            if runner_form == "cpp_extension"
            else "NPU_BUILD_AND_HARNESS_TRUST_GATE")

        # ⚠ 以下 key_files / aclnn_headers 对账**与来源通路无关**：它只消费
        #   `pr_facts.key_files` 的正文与 `source_facts.key_files[].bytes_sha256`，
        #   两条通路同形。别顺手把 `recorded["ref"]` 拉进来对账——PR 通路的 ref 是
        #   head_sha、本地快照通路是 merkle，一加进来这段就按通路分叉了。
        # ⚠ 这里**不再**自己做「先核 kind 一致、再核锚相等」的两步对账：
        #   `source_provenance.bind` 上面已经做了同形且更严的版本（缺席与显式 null 分开、
        #   local 档强制两侧 head 显式 null、snapshot_scope 必须相等）。留第二份判据
        #   只会让两处慢慢漂移。
        key_files = pr_facts.get("key_files")
        if not isinstance(key_files, dict):
            raise ValueError("pr_facts.key_files 缺失或非 object")
        source_keys = {
            item["path"]: item for item in source.get("key_files") or []
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        source_derived = source.get("derived")
        if not isinstance(source_derived, dict):
            raise ValueError("source_facts.derived 须为 JSON object")
        header_paths = source_derived.get("aclnn_headers") or []
        if not header_paths:
            raise ValueError("source_facts 未声明 aclnn_headers")

        signatures = {}
        for path in sorted(header_paths):
            text = key_files.get(path)
            if not isinstance(text, str):
                raise ValueError(f"PR head 关键文件中缺接口头正文: {path}")
            raw = text.encode("utf-8")
            recorded = source_keys.get(path)
            if not recorded:
                raise ValueError(f"source_facts.key_files 未绑定接口头: {path}")
            if hashlib.sha256(raw).hexdigest() != recorded.get("bytes_sha256"):
                raise ValueError(f"接口头正文与 source_facts 摘要不一致: {path}")
            signature = parse_aclnn_signature(text)
            if signature.op_name in signatures:
                raise ValueError(
                    f"aclnn{signature.op_name} 签名在多份 header 重复，无法唯一绑定")
            signatures[signature.op_name] = (path, signature)
            result["signatures"].append({
                "symbol": signature.op_name,
                "header": path,
                "params": _param_records(signature),
                "bytes_sha256": recorded["bytes_sha256"],
                **_stage2_record(signature),
            })

        variants = precision_policy.call_variants(spec)
        if not variants:
            raise ValueError(
                f"runner_form={runner_form} 但 spec.call_variants 缺失")
        for index, variant in enumerate(variants):
            symbol = variant["symbol"]
            if symbol not in signatures:
                raise ValueError(
                    f"call_variants[{index}] symbol={symbol!r} 在 PR head header 中无唯一签名")
            slots = _abstract_slots(spec, variant)
            _, signature = signatures[symbol]
            AclnnRunner._validate_slots_against_signature(
                slots, signature, symbol)
            result["variants"].append({
                "index": index,
                "symbol": symbol,
                "when": variant["when"],
                "slot_contract": [{
                    "name": slot["name"],
                    "role": ("out" if slot["kind"] == "out_null"
                             else slot["kind"]),
                    "nullable": slot["kind"] == "out_null",
                    **({"ctype": slot["ctype"]}
                       if slot["kind"] == "attr" else {}),
                } for slot in slots],
                # slot_contract 是 stage1 的 N 项；extended 形态下真机 stage2 还要把这 N 项
                # 原样重复一遍（3 + N + 1 个实参）——记下来，别让读产物的人默认成 4 参。
                **_stage2_record(signature),
                "status": "STATIC_SIGNATURE_MATCH",
            })
        result["status"] = "READY_WAIT_NPU_TRUST_GATE"
        return result
    except (AclnnRunnerError, content_address.ContentAddressError,
            KeyError, OSError, TypeError, ValueError,
            json.JSONDecodeError, UnicodeError) as ex:
        result["blocked_reasons"].append(str(ex))
        return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ACLNN ABI 静态签名预检；成功后仍必须过对应真机 trust gate")
    ap.add_argument("--root", required=True, help="CP-A/B 工件根目录")
    ap.add_argument("--spec", required=True, help="root 内 spec 相对路径")
    ap.add_argument("--pr-facts", default="pr_facts.json",
                    help="root 内 pr_facts 相对路径")
    ap.add_argument("--source", default="source_facts.json",
                    help="root 内 source_facts 相对路径")
    ap.add_argument("--out", default=None, help="root 内输出相对路径（可选）")
    args = ap.parse_args(argv)
    result = evaluate(
        args.root, args.spec, pr_facts_rel=args.pr_facts,
        source_rel=args.source)
    if args.out:
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _PREFLIGHT_DOMAIN, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {
        "READY_WAIT_NPU_TRUST_GATE", "NOT_APPLICABLE"} else 2


if __name__ == "__main__":
    sys.exit(main())
