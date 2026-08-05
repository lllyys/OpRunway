#!/usr/bin/env python3
"""校验非真机准备工件能否安全复用。

本脚本只复核 CP-A/CP-B 的来源、对应关系、spec、golden 与 dry-run 计划绑定。
任务书授权锚还须在 source facts、spec golden、GOLDEN_CONTRACT 与 golden 生效目录
四处摘要一致；生效目录快照必须是普通非符号链接文件。
它不读取 caseset/evidence/verdict，不运行 golden，不产生任何验收 PASS。
"""

import argparse
import ast
import hashlib
import json
import os
import sys

import content_address
import dut_source
import fetch_source            # 摘要策略与 warnings 受控词表的**唯一真源**（不在此另抄一份）


_DUT_SOURCE_PR = dut_source.PULL_REQUEST
_DUT_SOURCE_LOCAL = dut_source.LOCAL_CHECKOUT
_DUT_SOURCES = dut_source.ALL
_SOURCE_DOMAIN = "oprunway/source-facts/v1"
_RECEIPT_DOMAIN = "oprunway/preparation-receipt/v1"
_LEDGER_SCHEMA = "oprunway.gen_cases.dry_run_ledger"
_CASE_PLAN_DOMAIN = "oprunway/case-plan/v1"
_PLANNER_DEPENDENCIES = (
    "gen_cases.py",
    "repo_adapter.py",
    "precision_policy.py",
)


def _is_sha(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _validate_source_payload(source):
    """校验 source facts 的载重字段；摘要自洽不等于 producer 输出完整。

    按 `dut_source` 判别式分支（缺省 `pull_request`，老收据不带这个键仍走 PR 分支）：

      · `pull_request`  ：`pr` 必填集**一行不改**，关键文件锚 = `pr.head_sha`；
      · `local_checkout`：`local_checkout.root_digest` 必填（64 位 sha）、`op_subdir` 非空 str，
        关键文件锚 = `root_digest`；**不接受 `pr` 键**（本地事实不许伪装成 PR 事实）。
    """
    if not isinstance(source, dict):
        raise content_address.ContentAddressError(
            "source_facts payload 须为 JSON object")
    try:
        # 判别式 + 「两条通路的事实键互斥」一并在这里收（未知取值、混装收据都当场拒）。
        kind = dut_source.assert_facts_key_exclusive(source, where="source_facts")
    except dut_source.DutSourceError as exc:
        raise content_address.ContentAddressError(str(exc)) from exc
    is_local = kind == _DUT_SOURCE_LOCAL
    required = {
        "contract_version", "taskdoc", "changed_files", "key_files",
        "derived", "completeness", "producer",
    } | {dut_source.FACTS_KEY[kind]}
    missing = sorted(required.difference(source))
    if missing:
        raise content_address.ContentAddressError(
            f"source_facts 缺必要字段: {missing}")
    if source["contract_version"] != 1:
        raise content_address.ContentAddressError(
            "source_facts.contract_version 不受支持")
    taskdoc = source["taskdoc"]
    if not isinstance(taskdoc, dict):
        raise content_address.ContentAddressError(
            "source_facts.taskdoc 须为 JSON object")
    if (not _is_sha(taskdoc.get("bytes_sha256"))
            or taskdoc.get("snapshot_sha256") != taskdoc.get("bytes_sha256")
            or not isinstance(taskdoc.get("size"), int)
            or isinstance(taskdoc.get("size"), bool)
            or taskdoc["size"] < 0
            or not isinstance(taskdoc.get("source_locator"), str)):
        raise content_address.ContentAddressError(
            "source_facts.taskdoc 的 bytes/snapshot/size/source_locator 契约不完整")
    if is_local:
        local = source["local_checkout"]
        if not isinstance(local, dict):
            raise content_address.ContentAddressError(
                "source_facts.local_checkout 须为 JSON object")
        if (not _is_sha(local.get("root_digest"))
                or not isinstance(local.get("op_subdir"), str)
                or not local.get("op_subdir")):
            raise content_address.ContentAddressError(
                "source_facts.local_checkout 的 root_digest/op_subdir 契约不完整"
                "（root_digest 须 64 位小写 sha）")
        # ⚠ 摘要策略必须**逐字**等于本工具当前支持的那一份，不能「是个非空列表就收下」：
        # 一份声明了弱化/伪造排除规则的本地摘要，外表与正常摘要毫无区别，却覆盖不到它
        # 声称覆盖的字节。未知算法/版本/规则一律 fail-closed。
        if local.get("digest_policy") != fetch_source.digest_policy():
            raise content_address.ContentAddressError(
                f"source_facts.local_checkout.digest_policy 不是本工具支持的策略：\n"
                f"  收据里：{local.get('digest_policy')!r}\n"
                f"  支持的：{fetch_source.digest_policy()!r}\n"
                f"  → 排除规则不同则 root_digest 不可比，而外表看不出来；fail-closed。")
        # 「非 git 仓」的唯一合法表示是**整键缺席**（`fetch_source` 就是这么写的）。
        # ⚠ `git: null` 不算：`is not None` 会让它与缺席同义，于是一份被裁剪/写坏的收据
        # 只要把 git 置空就能免掉下面全部 dirty 一致性校验，还顺带免掉 warnings 反向核对。
        if "git" in local:
            git = local["git"]
            if (not isinstance(git, dict)
                    or not isinstance(git.get("dirty"), bool)
                    or not isinstance(git.get("dirty_files"), list)
                    or any(not isinstance(p, str) or not p
                           for p in git["dirty_files"])):
                raise content_address.ContentAddressError(
                    "source_facts.local_checkout.git 契约不完整"
                    "（dirty 须 bool、dirty_files 须非空字符串数组；"
                    "非 git 仓请让 git 整键缺席，不要写 null）")
            # ⚠ `dirty` 与清单必须互相蕴含：`dirty=false` 配非空清单会让降级 warning
            # 整条消失（`warnings` 是按 `git.dirty` 派生的），`dirty=true` 配空清单则是
            # 「说脏却举不出一份脏文件」——两种都是自相矛盾的收据，不猜哪半是真的。
            if bool(git["dirty"]) != bool(git["dirty_files"]):
                raise content_address.ContentAddressError(
                    f"source_facts.local_checkout.git 自相矛盾："
                    f"dirty={git['dirty']!r} 但 dirty_files 有 {len(git['dirty_files'])} 项")
            in_op = git.get("dirty_files_in_op_subdir")
            if in_op is not None and (
                    not isinstance(in_op, list)
                    or not set(in_op).issubset(set(git["dirty_files"]))):
                raise content_address.ContentAddressError(
                    "source_facts.local_checkout.git.dirty_files_in_op_subdir "
                    "须为 dirty_files 的子集")
        anchor = local["root_digest"]
        anchor_desc = "本地子树摘要 root_digest"
    else:
        pr = source["pr"]
        if not isinstance(pr, dict):
            raise content_address.ContentAddressError(
                "source_facts.pr 须为 JSON object")
        if (not isinstance(pr.get("canonical_url"), str)
                or not isinstance(pr.get("source_repo"), str)
                or not isinstance(pr.get("number"), int)
                or isinstance(pr.get("number"), bool)
                or pr["number"] <= 0
                or not _is_sha(pr.get("head_sha"), length=40)
                or not isinstance(pr.get("head_repo"), str)
                or not isinstance(pr.get("is_fork"), bool)
                or not isinstance(pr.get("state"), str)):
            raise content_address.ContentAddressError(
                "source_facts.pr 的 URL/repo/number/head/fork/state 契约不完整")
        anchor = pr["head_sha"]
        anchor_desc = "PR head"
    changed_files = source["changed_files"]
    # ⚠ 本地通路允许 `"unavailable"`（没给 --base-ref 时算不出改动清单）——它与「确实没改」
    # 是两回事，绝不能退化成空数组。PR 通路仍必须是非空数组。
    if is_local and changed_files == "unavailable":
        pass
    elif (not isinstance(changed_files, list) or not changed_files
            or any(not isinstance(path, str) or not path
                   for path in changed_files)):
        raise content_address.ContentAddressError(
            "source_facts.changed_files 须为非空字符串数组"
            + ("（本地通路亦可为 'unavailable'）" if is_local else ""))
    key_files = source["key_files"]
    if not isinstance(key_files, list) or not key_files:
        raise content_address.ContentAddressError(
            "source_facts.key_files 须为非空数组")
    for index, item in enumerate(key_files):
        if (not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or item.get("ref") != anchor
                or not _is_sha(item.get("bytes_sha256"))
                or not isinstance(item.get("size"), int)
                or isinstance(item.get("size"), bool)
                or item["size"] < 0):
            raise content_address.ContentAddressError(
                f"source_facts.key_files[{index}] 契约不完整或未绑定{anchor_desc}")
    derived = source["derived"]
    if (not isinstance(derived, dict)
            or not isinstance(derived.get("op"), str)
            or not isinstance(derived.get("target_dir"), str)
            or not isinstance(derived.get("aclnn_headers"), list)
            or any(not isinstance(path, str)
                   for path in derived.get("aclnn_headers", []))):
        raise content_address.ContentAddressError(
            "source_facts.derived 的 op/target_dir/aclnn_headers 契约不完整")
    completeness = source["completeness"]
    if (not isinstance(completeness, dict)
            or completeness.get("status") != "complete"
            or completeness.get("reasons") != []):
        raise content_address.ContentAddressError(
            "source_facts.completeness 必须是 complete 且 reasons 为空")
    # `warnings` 是非阻塞留痕（如本地通路的 changed_files_unavailable），**仅在非空时出现**。
    # ⚠ 必须是**受控词表**且与载重事实**对得上**，不能「是字符串就收下」：否则把任意阻塞原因
    # 写成 warning、再配 `status=complete, reasons=[]`，就能让降级的来源以干净 pass 过门。
    if "warnings" in completeness:
        warnings = completeness["warnings"]
        if (not isinstance(warnings, list) or not warnings
                or any(not isinstance(w, str) or not w for w in warnings)):
            raise content_address.ContentAddressError(
                "source_facts.completeness.warnings 若出现则须为非空字符串数组"
                "（空数组请整键省略——恒为空的键会改动 PR 通路的业务字段）")
        unknown = sorted(set(warnings) - set(fetch_source.SOURCE_WARNINGS))
        if unknown:
            raise content_address.ContentAddressError(
                f"source_facts.completeness.warnings 含词表外取值 {unknown}"
                f"（受控词表 {list(fetch_source.SOURCE_WARNINGS)}）——"
                f"任意字符串都能进 warnings 就等于给阻塞原因开了后门")
        # 逐条核「这条 warning 对应的降级事实是否真的存在」——反向的多写同样要拒。
        expected = set()
        if is_local:
            if source["changed_files"] == "unavailable":
                expected.add(fetch_source.WARN_CHANGED_FILES_UNAVAILABLE)
            git = source["local_checkout"].get("git")
            if isinstance(git, dict) and git.get("dirty"):
                expected.add(fetch_source.WARN_DIRTY_WORKTREE_ALLOWED)
        if set(warnings) != expected:
            raise content_address.ContentAddressError(
                f"source_facts.completeness.warnings={sorted(set(warnings))} 与载重事实派生的 "
                f"{sorted(expected)} 不一致（少写 = 降级没留痕；多写 = 编造降级）")
    elif is_local:
        # 反向：降级发生了却整键缺席，同样是「没留痕」。
        missing = set()
        if source["changed_files"] == "unavailable":
            missing.add(fetch_source.WARN_CHANGED_FILES_UNAVAILABLE)
        git = source["local_checkout"].get("git")
        if isinstance(git, dict) and git.get("dirty"):
            missing.add(fetch_source.WARN_DIRTY_WORKTREE_ALLOWED)
        if missing:
            raise content_address.ContentAddressError(
                f"source_facts 发生了来源降级但 completeness.warnings 整键缺席：应含 "
                f"{sorted(missing)}（降级必须留痕，否则报告里看不出 provenance 弱在哪）")
    producer = source["producer"]
    if (not isinstance(producer, dict)
            or producer.get("tool") != "fetch_source.py"
            or not _is_sha(producer.get("logic_sha256"))):
        raise content_address.ContentAddressError(
            "source_facts.producer 契约不完整")


def _validate_plan_payload(plan):
    """校验 dry-run 账本载重；防止只复制几项摘要的空壳命中。"""
    if not isinstance(plan, dict):
        raise content_address.ContentAddressError(
            "case plan 须为 JSON object")
    required = {
        "schema", "schema_version", "spec_binding", "preparation_inputs",
        "planner_binding", "planning", "golden_dependency", "summary",
        "coverage", "determinism", "ledger_digest",
    }
    missing = sorted(required.difference(plan))
    if missing:
        raise content_address.ContentAddressError(
            f"case plan 缺必要字段: {missing}")
    if (plan.get("schema") != _LEDGER_SCHEMA
            or plan.get("schema_version") != 1):
        raise content_address.ContentAddressError(
            "case plan schema/schema_version 不受支持")
    planning = plan["planning"]
    if (not isinstance(planning, dict)
            or not isinstance(planning.get("case_target"), int)
            or isinstance(planning.get("case_target"), bool)
            or planning["case_target"] < 1
            or not isinstance(planning.get("runner_form"), str)):
        raise content_address.ContentAddressError(
            "case plan planning.case_target/runner_form 契约不完整")
    summary = plan["summary"]
    count_keys = ("emitted", "pool_max", "forced_total", "forced_special")
    if (not isinstance(summary, dict)
            or any(not isinstance(summary.get(key), int)
                   or isinstance(summary.get(key), bool)
                   or summary[key] < 0 for key in count_keys)
            or not isinstance(summary.get("by_dtype"), dict)
            or not isinstance(summary.get("shapes"), list)
            or not isinstance(summary.get("id_kinds"), dict)):
        raise content_address.ContentAddressError(
            "case plan summary 载重字段不完整")
    coverage = plan["coverage"]
    if (not isinstance(coverage, dict)
            or not isinstance(coverage.get("strength"), str)
            or not isinstance(coverage.get("golden_cost"), dict)
            or not isinstance(coverage.get("dropped_combo_classes"), list)
            or not isinstance(coverage.get("unpaired_combo_classes"), dict)):
        raise content_address.ContentAddressError(
            "case plan coverage 载重字段不完整")
    determinism = plan["determinism"]
    if (determinism is not None
            and (not isinstance(determinism, dict)
                 or not isinstance(determinism.get("case_id"), str)
                 or not isinstance(determinism.get("equal"), bool))):
        raise content_address.ContentAddressError(
            "case plan determinism 契约不完整")


def _strict_json(path):
    try:
        with open(path, "r", encoding="utf-8") as src:
            value = json.load(
                src,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    content_address.ContentAddressError(
                        f"非法 JSON 常量: {token}")),
            )
    except content_address.ContentAddressError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise content_address.ContentAddressError(
            f"无法读取 JSON {path!r}: {ex}") from ex
    content_address.canonical_json_bytes(value)
    return value


def _file_sha256(path):
    if os.path.islink(path):
        raise content_address.ContentAddressError(f"依赖文件不得是符号链接: {path!r}")
    try:
        with open(path, "rb") as src:
            return hashlib.sha256(src.read()).hexdigest()
    except OSError as ex:
        raise content_address.ContentAddressError(
            f"无法读取依赖文件 {path!r}: {ex}") from ex


def _golden_contract(path):
    """静态读取 golden.py 的 GOLDEN_CONTRACT；不 import、不执行被验收代码。"""
    if os.path.islink(path):
        raise content_address.ContentAddressError(
            f"golden.py 不得是符号链接: {path!r}")
    try:
        with open(path, "r", encoding="utf-8") as src:
            tree = ast.parse(src.read(), filename=path)
    except (OSError, UnicodeError, SyntaxError) as ex:
        raise content_address.ContentAddressError(
            f"无法静态读取 golden.py {path!r}: {ex}") from ex
    values = []
    for node in tree.body:
        if (isinstance(node, (ast.Assign, ast.AnnAssign))
                and ((isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name)
                              and target.id == "GOLDEN_CONTRACT"
                              for target in node.targets))
                     or (isinstance(node, ast.AnnAssign)
                         and isinstance(node.target, ast.Name)
                         and node.target.id == "GOLDEN_CONTRACT"))):
            try:
                values.append(ast.literal_eval(node.value))
            except (ValueError, TypeError) as ex:
                raise content_address.ContentAddressError(
                    "GOLDEN_CONTRACT 必须是可静态读取的字面量") from ex
    if len(values) != 1 or not isinstance(values[0], dict):
        raise content_address.ContentAddressError(
            "golden.py 必须且只能定义一个 object 型 GOLDEN_CONTRACT")
    return values[0]


def _check(checks, name, status, reason):
    checks.append({"name": name, "status": status, "reason": reason})


def evaluate(root, spec_rel, case_plan_rel, golden_path=None,
             source_rel="source_facts.json",
             correspondence_rel="correspondence.json",
             taskdoc_snapshot_rel=None):
    """返回 `REUSABLE | MISS | BLOCKED` 的准备阶段收据 payload。

    `MISS` 表示输入或逻辑正常漂移，应重做非真机准备；`BLOCKED` 表示工件损坏、
    schema 异常或不可信，不能静默重做来掩盖。
    """
    root = os.path.abspath(root)
    checks, bindings = [], {}
    if taskdoc_snapshot_rel is None:
        taskdoc_snapshot_rel = os.path.join(
            os.path.dirname(os.fspath(source_rel)), "task_doc.snapshot.md")
    snapshot_path = None

    try:
        snapshot_path = content_address.safe_path(root, taskdoc_snapshot_rel)
        source_path = content_address.safe_path(root, source_rel)
        if not os.path.exists(source_path):
            source = None
            source_digest = None
            _check(checks, "source_facts", "MISS", "source_facts.json 不存在")
        else:
            source = content_address.read_artifact(
                root, source_rel, _SOURCE_DOMAIN)
            _validate_source_payload(source)
            source_digest = content_address.content_digest(
                _SOURCE_DOMAIN, source)
            bindings["source_facts_digest"] = source_digest
            completeness = source.get("completeness")
            if not isinstance(completeness, dict):
                raise content_address.ContentAddressError(
                    "source_facts.completeness 须为 JSON object")
            complete = completeness.get("status")
            if complete == "complete":
                _check(checks, "source_facts", "PASS",
                       "内容摘要与完整性均有效")
            else:
                _check(checks, "source_facts", "MISS",
                       "source_facts completeness 不是 complete")
            fetch_source_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "fetch_source.py")
            producer_sha = _file_sha256(fetch_source_path)
            bindings["source_producer_sha256"] = producer_sha
            producer = source.get("producer")
            if not isinstance(producer, dict):
                raise content_address.ContentAddressError(
                    "source_facts.producer 须为 JSON object")
            recorded_producer = producer.get("logic_sha256")
            if recorded_producer != producer_sha:
                _check(checks, "source_producer", "MISS",
                       "fetch_source.py 事实生成逻辑已变化")
            else:
                _check(checks, "source_producer", "PASS",
                       "事实生成逻辑摘要一致")
            source_taskdoc = source.get("taskdoc")
            if not isinstance(source_taskdoc, dict):
                raise content_address.ContentAddressError(
                    "source_facts.taskdoc 须为 JSON object")
            recorded_snapshot_sha = source_taskdoc.get("snapshot_sha256")
            if not (isinstance(recorded_snapshot_sha, str)
                    and len(recorded_snapshot_sha) == 64
                    and all(c in "0123456789abcdef"
                            for c in recorded_snapshot_sha)):
                _check(checks, "taskdoc_snapshot", "MISS",
                       "source_facts 未绑定合法 taskdoc snapshot sha256")
            elif not os.path.exists(snapshot_path):
                _check(checks, "taskdoc_snapshot", "MISS",
                       f"{taskdoc_snapshot_rel} 不存在")
            else:
                current_snapshot_sha = _file_sha256(snapshot_path)
                bindings["taskdoc_snapshot_sha256"] = current_snapshot_sha
                if current_snapshot_sha != recorded_snapshot_sha:
                    _check(checks, "taskdoc_snapshot", "MISS",
                           "任务书快照内容已变化")
                else:
                    _check(checks, "taskdoc_snapshot", "PASS",
                           "任务书快照与 source_facts 摘要一致")
    except content_address.ContentAddressError as ex:
        source = None
        source_digest = None
        _check(checks, "source_facts", "BLOCKED", str(ex))

    correspondence_sha = None
    try:
        correspondence_path = content_address.safe_path(root, correspondence_rel)
        if not os.path.exists(correspondence_path):
            _check(checks, "correspondence", "MISS",
                   "correspondence.json 不存在")
        else:
            correspondence = _strict_json(correspondence_path)
            correspondence_sha = hashlib.sha256(
                content_address.canonical_json_bytes(correspondence)).hexdigest()
            bindings["correspondence_sha256"] = correspondence_sha
            if not isinstance(correspondence, dict):
                raise content_address.ContentAddressError(
                    "correspondence 须为 JSON object")
            if correspondence.get("status") != "confirmed":
                _check(checks, "correspondence", "MISS",
                       f"status={correspondence.get('status')!r}，不是 confirmed")
            elif not source_digest:
                _check(checks, "correspondence", "MISS",
                       "source_facts 尚不可绑定")
            elif correspondence.get("source_facts_digest") != source_digest:
                _check(checks, "correspondence", "MISS",
                       "缺少或不匹配 source_facts_digest，须重新确认对应关系")
            else:
                _check(checks, "correspondence", "PASS",
                       "confirmed 且绑定当前 source_facts")
    except content_address.ContentAddressError as ex:
        _check(checks, "correspondence", "BLOCKED", str(ex))

    try:
        spec_path = content_address.safe_path(root, spec_rel)
        if not os.path.exists(spec_path):
            spec = None
            spec_sha = None
            _check(checks, "spec", "MISS", f"{spec_rel} 不存在")
        else:
            spec = _strict_json(spec_path)
            if not isinstance(spec, dict):
                raise content_address.ContentAddressError(
                    "spec 须为 JSON object")
            spec_sha = hashlib.sha256(
                content_address.canonical_json_bytes(spec)).hexdigest()
            bindings["spec_sha256"] = spec_sha
            spec_golden = spec.get("golden")
            if spec_golden is not None and not isinstance(spec_golden, dict):
                raise content_address.ContentAddressError(
                    "spec.golden 须为 JSON object")
            authorization = (spec_golden or {}).get("authorization")
            if authorization is not None and not isinstance(
                    authorization, dict):
                raise content_address.ContentAddressError(
                    "spec.golden.authorization 须为 JSON object")
            authorization_kind = (authorization or {}).get("kind")
            if authorization_kind in {"oracle_method", "formula"}:
                spec_snapshot = (spec_golden or {}).get("taskdoc_snapshot")
                if not isinstance(spec_snapshot, dict):
                    raise content_address.ContentAddressError(
                        "spec.golden.taskdoc_snapshot 须为 JSON object")
                spec_snapshot_sha = spec_snapshot.get("sha256")
                source_snapshot_sha = (
                    ((source or {}).get("taskdoc") or {})
                    .get("snapshot_sha256"))
                if (not source_snapshot_sha
                        or spec_snapshot_sha != source_snapshot_sha):
                    _check(checks, "spec_taskdoc_anchor", "BLOCKED",
                           "spec golden 的任务书锚未绑定当前 source_facts 快照")
                else:
                    _check(checks, "spec_taskdoc_anchor", "PASS",
                           "spec golden 绑定当前任务书快照")
    except content_address.ContentAddressError as ex:
        spec = None
        spec_sha = None
        _check(checks, "spec", "BLOCKED", str(ex))

    try:
        plan_path = content_address.safe_path(root, case_plan_rel)
        if not os.path.exists(plan_path):
            _check(checks, "case_plan", "MISS", f"{case_plan_rel} 不存在")
            plan = None
        else:
            plan = _strict_json(plan_path)
        if plan is not None:
            _validate_plan_payload(plan)
            recorded_plan_digest = plan.get("ledger_digest")
            plan_payload = dict(plan)
            plan_payload.pop("ledger_digest", None)
            actual_plan_digest = content_address.content_digest(
                _CASE_PLAN_DOMAIN, plan_payload)
            if recorded_plan_digest != actual_plan_digest:
                raise content_address.ContentAddressError(
                    "case plan ledger_digest 缺失或不匹配")
            preparation_inputs = plan.get("preparation_inputs")
            if not isinstance(preparation_inputs, dict):
                _check(checks, "case_plan_inputs", "MISS",
                       "case plan 未绑定 source_facts/correspondence")
            elif (not source_digest or not correspondence_sha
                  or preparation_inputs.get("source_facts_digest")
                  != source_digest
                  or preparation_inputs.get("correspondence_sha256")
                  != correspondence_sha):
                _check(checks, "case_plan_inputs", "MISS",
                       "case plan 的来源事实或用户确认已变化")
            else:
                _check(checks, "case_plan_inputs", "PASS",
                       "case plan 绑定当前来源事实与用户确认")
            plan_spec_binding = plan.get("spec_binding")
            if not isinstance(plan_spec_binding, dict):
                raise content_address.ContentAddressError(
                    "case plan spec_binding 须为 JSON object")
            plan_spec_sha = plan_spec_binding.get("sha256")
            if not spec_sha:
                _check(checks, "case_plan_spec", "MISS", "spec 尚不可绑定")
            elif plan_spec_sha != spec_sha:
                _check(checks, "case_plan_spec", "MISS", "spec 内容已变化")
            else:
                _check(checks, "case_plan_spec", "PASS",
                       "case plan 绑定当前 spec")

            logic_root = os.path.dirname(os.path.abspath(__file__))
            current_logic_files = {
                filename: _file_sha256(os.path.join(logic_root, filename))
                for filename in _PLANNER_DEPENDENCIES
            }
            planner_sha = current_logic_files["gen_cases.py"]
            bindings["planner_sha256"] = planner_sha
            planner_binding = plan.get("planner_binding")
            if not isinstance(planner_binding, dict):
                raise content_address.ContentAddressError(
                    "case plan planner_binding 须为 JSON object")
            recorded_planner = planner_binding.get("gen_cases_py_sha256")
            recorded_logic_files = planner_binding.get("logic_files")
            if (recorded_planner != planner_sha
                    or recorded_logic_files != current_logic_files):
                _check(checks, "case_planner", "MISS",
                       "gen_cases.py 或其规划依赖逻辑已变化")
            else:
                _check(checks, "case_planner", "PASS", "规划逻辑摘要一致")

            golden = plan.get("golden_dependency")
            if not isinstance(golden, dict) or golden.get("status") != "loaded":
                _check(checks, "golden", "MISS",
                       "dry-run 未成功绑定 golden.py")
            elif not golden_path:
                _check(checks, "golden", "MISS", "未提供 --golden 复核路径")
            elif not os.path.exists(os.path.abspath(golden_path)):
                _check(checks, "golden", "MISS", "golden.py 不存在")
            else:
                absolute_golden = os.path.abspath(golden_path)
                current_golden_sha = _file_sha256(absolute_golden)
                bindings["golden_sha256"] = current_golden_sha
                if current_golden_sha != golden.get("bytes_sha256"):
                    _check(checks, "golden", "MISS", "golden.py 内容已变化")
                else:
                    _check(checks, "golden", "PASS", "golden.py 摘要一致")
                contract = _golden_contract(absolute_golden)
                contract_sha = hashlib.sha256(
                    content_address.canonical_json_bytes(contract)).hexdigest()
                bindings["golden_contract_sha256"] = contract_sha
                if contract_sha != golden.get("contract_sha256"):
                    _check(checks, "golden_contract", "BLOCKED",
                           "GOLDEN_CONTRACT 与 case plan 记录摘要冲突")
                else:
                    _check(checks, "golden_contract", "PASS",
                           "GOLDEN_CONTRACT 摘要与 case plan 一致")

                contract_snapshot = contract.get("taskdoc_snapshot")
                contract_snapshot_sha = (
                    contract_snapshot.get("sha256")
                    if isinstance(contract_snapshot, dict) else None)
                bindings["golden_contract_taskdoc_snapshot_sha256"] = (
                    contract_snapshot_sha)
                ops_snapshot = os.path.join(
                    os.path.dirname(absolute_golden), "task_doc.snapshot.md")
                bindings["source_taskdoc_snapshot_path"] = snapshot_path
                bindings["ops_taskdoc_snapshot_path"] = ops_snapshot
                if not os.path.lexists(ops_snapshot):
                    _check(checks, "ops_taskdoc_snapshot", "MISS",
                           "golden 生效目录缺 task_doc.snapshot.md，须重做 gen_golden")
                elif os.path.islink(ops_snapshot):
                    _check(checks, "ops_taskdoc_snapshot", "BLOCKED",
                           "golden 生效目录 task_doc.snapshot.md 不得是符号链接")
                elif not os.path.isfile(ops_snapshot):
                    _check(checks, "ops_taskdoc_snapshot", "BLOCKED",
                           "golden 生效目录 task_doc.snapshot.md 须为普通文件")
                else:
                    ops_snapshot_sha = _file_sha256(ops_snapshot)
                    bindings["ops_taskdoc_snapshot_sha256"] = ops_snapshot_sha
                    source_snapshot_sha = (
                        ((source or {}).get("taskdoc") or {})
                        .get("snapshot_sha256"))
                    spec_snapshot_sha = None
                    spec_golden = (
                        spec.get("golden") if isinstance(spec, dict) else None)
                    if isinstance(spec_golden, dict):
                        spec_snapshot = spec_golden.get("taskdoc_snapshot")
                        if isinstance(spec_snapshot, dict):
                            spec_snapshot_sha = spec_snapshot.get("sha256")
                    bindings["source_taskdoc_snapshot_sha256"] = (
                        source_snapshot_sha)
                    bindings["spec_taskdoc_snapshot_sha256"] = (
                        spec_snapshot_sha)
                    anchors = {
                        "source_facts": source_snapshot_sha,
                        "spec.golden": spec_snapshot_sha,
                        "GOLDEN_CONTRACT": contract_snapshot_sha,
                        "ops_snapshot": ops_snapshot_sha,
                    }
                    if (not all(_is_sha(value) for value in anchors.values())
                            or len(set(anchors.values())) != 1):
                        _check(
                            checks, "ops_taskdoc_snapshot", "BLOCKED",
                            f"任务书授权锚四方摘要缺失或冲突: {anchors}")
                    else:
                        _check(
                            checks, "ops_taskdoc_snapshot", "PASS",
                            "source/spec/GOLDEN_CONTRACT/生效目录快照四方摘要一致")
            bindings["case_plan_sha256"] = hashlib.sha256(
                content_address.canonical_json_bytes(plan)).hexdigest()
    except content_address.ContentAddressError as ex:
        _check(checks, "case_plan", "BLOCKED", str(ex))

    statuses = {item["status"] for item in checks}
    status = ("BLOCKED" if "BLOCKED" in statuses
              else "MISS" if "MISS" in statuses else "REUSABLE")
    return {
        "schema": "oprunway.preparation_receipt",
        "schema_version": 1,
        "status": status,
        "reusable": status == "REUSABLE",
        "scope": "non-real-machine-preparation-only",
        "acceptance_verdict": None,
        "checks": checks,
        "bindings": bindings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="复核非真机准备工件绑定；不产生验收裁决")
    ap.add_argument("--root", required=True, help="准备工件根目录")
    ap.add_argument("--spec", required=True, help="root 内 spec 相对路径")
    ap.add_argument("--case-plan", required=True, help="root 内 case plan 相对路径")
    ap.add_argument("--golden", default=None, help="golden.py 绝对或当前目录相对路径")
    ap.add_argument("--source", default="source_facts.json",
                    help="root 内 source facts 相对路径")
    ap.add_argument("--correspondence", default="correspondence.json",
                    help="root 内 correspondence 相对路径")
    ap.add_argument(
        "--taskdoc-snapshot", default=None,
        help=("root 内 CP-A 任务书快照相对路径；省略时自动取 --source 同目录下"
              "的 task_doc.snapshot.md"))
    ap.add_argument("--out", default=None, help="root 内收据相对路径（可选）")
    args = ap.parse_args(argv)
    receipt = evaluate(
        args.root, args.spec, args.case_plan, golden_path=args.golden,
        source_rel=args.source, correspondence_rel=args.correspondence,
        taskdoc_snapshot_rel=args.taskdoc_snapshot)
    if args.out:
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _RECEIPT_DOMAIN, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["reusable"] else 2


if __name__ == "__main__":
    sys.exit(main())
